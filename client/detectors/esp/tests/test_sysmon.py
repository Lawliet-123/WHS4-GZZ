from __future__ import annotations

from dataclasses import replace
import unittest

from anti_esp.sysmon import (
    SysmonPoller,
    decode_granted_access,
    deduplicate_records,
    evidence_from_process_access,
    get_sysmon_status,
    is_game_target,
    parse_process_access_xml,
)


def event_xml(
    record_id: int,
    *,
    event_id: int = 10,
    source_pid: int = 4242,
    target_pid: int = 9001,
    source_image: str = r"C:\Python311\python.exe",
    target_image: str = r"C:\Games\PenguinHotel-Win64-Shipping.exe",
    access: str = "0x0010",
) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <Provider Name="Microsoft-Windows-Sysmon" />
    <EventID>{event_id}</EventID>
    <TimeCreated SystemTime="2026-09-18T01:02:03.5000000Z" />
    <EventRecordID>{record_id}</EventRecordID>
    <Computer>TEST-PC</Computer>
  </System>
  <EventData>
    <Data Name="RuleName">AntiESP</Data>
    <Data Name="UtcTime">2026-09-18 01:02:03.500</Data>
    <Data Name="SourceProcessId">{source_pid}</Data>
    <Data Name="SourceThreadId">101</Data>
    <Data Name="SourceImage">{source_image}</Data>
    <Data Name="SourceUser">TEST-PC\\player</Data>
    <Data Name="TargetProcessId">{target_pid}</Data>
    <Data Name="TargetImage">{target_image}</Data>
    <Data Name="TargetUser">TEST-PC\\player</Data>
    <Data Name="GrantedAccess">{access}</Data>
    <Data Name="CallTrace">C:\\Windows\\SYSTEM32\\ntdll.dll+123</Data>
  </EventData>
</Event>"""


class ProcessAccessParserTests(unittest.TestCase):
    def test_parses_namespaced_event_10_xml(self) -> None:
        event = parse_process_access_xml(event_xml(123, access="0x007A"))

        self.assertEqual(event.record_id, 123)
        self.assertEqual(event.event_id, 10)
        self.assertEqual(event.source_process_id, 4242)
        self.assertEqual(event.target_process_id, 9001)
        self.assertEqual(event.granted_access, 0x7A)
        self.assertEqual(event.computer, "TEST-PC")
        self.assertEqual(event.rule_name, "AntiESP")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(
            set(event.access_labels),
            {"VM_READ", "VM_WRITE", "VM_OPERATION", "CREATE_THREAD", "DUP_HANDLE"},
        )

    def test_rejects_non_process_access_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "Event ID 10"):
            parse_process_access_xml(event_xml(1, event_id=1))

    def test_target_match_uses_windows_basename_and_casefold(self) -> None:
        self.assertTrue(
            is_game_target(r"D:\steam\PENGUINHOTEL-WIN64-SHIPPING.EXE")
        )
        self.assertTrue(is_game_target("D:/steam/PenguinHotel-Win64-Shipping.exe"))
        self.assertFalse(is_game_target(r"D:\steam\OtherGame.exe"))

    def test_decodes_only_relevant_process_rights(self) -> None:
        self.assertEqual(set(decode_granted_access("0x005A")), {
            "CREATE_THREAD", "VM_OPERATION", "VM_READ", "DUP_HANDLE"
        })
        self.assertEqual(decode_granted_access("not-a-mask"), ())
        self.assertEqual(decode_granted_access(0x1000), ())

    def test_converts_untrusted_game_memory_read_to_evidence(self) -> None:
        record = parse_process_access_xml(event_xml(77, access="0x10"))
        evidence = evidence_from_process_access(record, session_id="round-1")

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.category, "memory_read")
        self.assertEqual(evidence.session_id, "round-1")
        self.assertEqual(evidence.details["record_id"], 77)
        self.assertEqual(evidence.details["access_labels"], ["VM_READ"])

        repeated = evidence_from_process_access(
            parse_process_access_xml(event_xml(78, access="0x10")),
            session_id="round-1",
        )
        self.assertIsNotNone(repeated)
        self.assertEqual(evidence.dedup_key, repeated.dedup_key)
        self.assertNotEqual(evidence.details["record_id"], repeated.details["record_id"])

    def test_ignores_self_access_and_non_game_target(self) -> None:
        record = parse_process_access_xml(event_xml(77, source_pid=9001))
        self.assertIsNone(evidence_from_process_access(record))
        other = replace(record, source_process_id=5, target_image=r"C:\Other.exe")
        self.assertIsNone(evidence_from_process_access(other))

    def test_deduplicates_record_ids_and_sorts_ascending(self) -> None:
        first = parse_process_access_xml(event_xml(10))
        second = parse_process_access_xml(event_xml(11))
        duplicate = parse_process_access_xml(event_xml(10, source_pid=999))

        result = deduplicate_records([second, first, duplicate], seen_record_ids={9})
        self.assertEqual([event.record_id for event in result], [10, 11])
        result = deduplicate_records([first, second], seen_record_ids={10})
        self.assertEqual([event.record_id for event in result], [11])


class _FakeHandle:
    def __init__(self, payload: str | None = None) -> None:
        self.payload = payload
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeEventLog:
    EvtChannelConfigEnabled = 0
    EvtQueryChannelPath = 1
    EvtQueryReverseDirection = 512
    EvtRenderEventXml = 1

    def __init__(self, snapshots: list[list[str]], *, enabled: bool = True) -> None:
        self.snapshots = snapshots
        self.enabled = enabled
        self.query_index = 0
        self.active: list[_FakeHandle] = []

    def EvtOpenChannelConfig(self, _channel: str) -> _FakeHandle:
        return _FakeHandle()

    def EvtGetChannelConfigProperty(self, _handle: _FakeHandle, _prop: int):
        return (self.enabled, 13)

    def EvtQuery(self, _channel: str, _flags: int, _query: str) -> _FakeHandle:
        snapshot = self.snapshots[self.query_index]
        self.query_index += 1
        self.active = [_FakeHandle(xml) for xml in snapshot]
        return _FakeHandle()

    def EvtNext(self, _query: _FakeHandle, count: int):
        result, self.active = self.active[:count], self.active[count:]
        return tuple(result)

    def EvtRender(self, event: _FakeHandle, _flags: int) -> str:
        assert event.payload is not None
        return event.payload


class _MissingEventLog(_FakeEventLog):
    def EvtOpenChannelConfig(self, _channel: str) -> _FakeHandle:
        error = OSError("channel missing")
        error.winerror = 15007  # type: ignore[attr-defined]
        raise error


class SysmonPollingTests(unittest.TestCase):
    def test_evt_next_exhaustion_is_available_and_retains_preceding_records(self) -> None:
        class Exhausted(_FakeEventLog):
            def EvtNext(self, query, count):
                result = super().EvtNext(query, count)
                if not result:
                    error = OSError("synthetic normal enumeration exhaustion")
                    error.winerror = 259
                    raise error
                return result
        poller = SysmonPoller(evt_module=Exhausted([[event_xml(2), event_xml(1)]]))
        result = poller.poll()
        self.assertTrue(result.status.available)
        self.assertEqual(result.status.code, "ready")
        self.assertEqual([event.record_id for event in result.events], [1, 2])
        self.assertEqual(result.scanned_count, 2)
        self.assertFalse(result.truncated)
        self.assertEqual(poller.last_record_id, 2)

    def test_empty_evt_next_exhaustion_is_successful_empty_observation(self) -> None:
        class Empty(_FakeEventLog):
            def EvtNext(self, query, count):
                error = OSError(259, "synthetic enumeration exhausted")
                raise error
        result = SysmonPoller(evt_module=Empty([[]])).poll()
        self.assertTrue(result.status.available)
        self.assertEqual(result.events, ())
        self.assertEqual(result.scanned_count, 0)
        self.assertFalse(result.truncated)

    def test_other_evt_next_errors_and_259_from_other_apis_remain_unavailable(self) -> None:
        for operation, code in (("next", 5), ("next", 1460), ("query", 259), ("render", 259), ("channel", 259)):
            class Failed(_FakeEventLog):
                def fail(self):
                    error = OSError("synthetic genuine API failure")
                    error.winerror = code
                    raise error
                def EvtOpenChannelConfig(self, channel):
                    if operation == "channel": self.fail()
                    return super().EvtOpenChannelConfig(channel)
                def EvtQuery(self, *values):
                    if operation == "query": self.fail()
                    return super().EvtQuery(*values)
                def EvtNext(self, *values):
                    if operation == "next": self.fail()
                    return super().EvtNext(*values)
                def EvtRender(self, *values):
                    if operation == "render": self.fail()
                    return super().EvtRender(*values)
            with self.subTest(operation=operation, code=code):
                result = SysmonPoller(evt_module=Failed([[event_xml(1)]])).poll()
                self.assertFalse(result.status.available)
                self.assertEqual(result.status.error_code, code)
                self.assertEqual(result.events, ())

    def test_scan_cap_stays_truncated_even_if_next_call_would_be_exhausted(self) -> None:
        result = SysmonPoller(evt_module=_FakeEventLog([[event_xml(3), event_xml(2), event_xml(1)]]),
                              max_events_per_poll=2).poll()
        self.assertTrue(result.status.available)
        self.assertTrue(result.truncated)
        self.assertEqual(result.scanned_count, 2)
        self.assertEqual([event.record_id for event in result.events], [2, 3])

    def test_reports_disabled_and_missing_channel_explicitly(self) -> None:
        disabled = get_sysmon_status(_FakeEventLog([], enabled=False))
        self.assertEqual(disabled.code, "disabled")
        self.assertTrue(disabled.installed)
        self.assertFalse(disabled.available)

        missing = get_sysmon_status(_MissingEventLog([]))
        self.assertEqual(missing.code, "not_installed")
        self.assertFalse(missing.installed)

    def test_poller_never_reemits_record_id(self) -> None:
        fake = _FakeEventLog(
            snapshots=[
                [event_xml(2), event_xml(1)],
                [event_xml(3), event_xml(2), event_xml(1)],
            ]
        )
        poller = SysmonPoller(evt_module=fake, batch_size=10)

        first = poller.poll()
        second = poller.poll()

        self.assertEqual([event.record_id for event in first.events], [1, 2])
        self.assertEqual([event.record_id for event in second.events], [3])
        self.assertEqual(poller.last_record_id, 3)


if __name__ == "__main__":
    unittest.main()
