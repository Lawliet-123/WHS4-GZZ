import unittest

from anti_esp.core.context import ProcessTarget, SensorContext
from anti_esp.sensors.process_access import SysmonProcessAccessSensor
from anti_esp.sysmon import SysmonPollResult, SysmonProcessAccess, SysmonStatus


class FakePoller:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def poll(self):
        if self.error:
            raise self.error
        return self.result


def record(**changes):
    values = dict(
        record_id=42,
        event_id=10,
        timestamp=101.0,
        computer="TEST",
        source_process_id=22,
        source_thread_id=23,
        source_image=r"C:\Tools\reader.exe",
        source_user=r"TEST\user",
        target_process_id=77,
        target_image=r"C:\Game\game.exe",
        target_user=r"TEST\user",
        granted_access=0x30,
        granted_access_raw="0x30",
        call_trace="frame-a;frame-b",
        rule_name="",
        data={"SourceProcessGUID": "source-guid"},
    )
    values.update(changes)
    return SysmonProcessAccess(**values)


def ready(*events):
    return SysmonPollResult(
        SysmonStatus(True, True, True, "ready", "ready"),
        tuple(events),
        scanned_count=len(events),
    )


class ProcessAccessSensorTests(unittest.TestCase):
    def setUp(self):
        self.context = SensorContext(
            "esp_001",
            (ProcessTarget(77, r"C:\Game\game.exe", created_at=100.0),),
            session_started_at=90.0,
            observed_at=102.0,
        )

    def test_emits_factual_event_without_score_or_verdict(self):
        sensor = SysmonProcessAccessSensor(
            poller=FakePoller(ready(record())), self_pid=999, clock=lambda: 102.0
        )
        batch = sensor.poll(self.context)
        self.assertEqual(batch.status, "online")
        self.assertEqual(len(batch.events), 1)
        event = batch.events[0]
        self.assertEqual(event.event_type, "process_access")
        self.assertEqual(event.subject_id, "game-process:77:100000")
        self.assertEqual(event.payload["access_labels"], ["VM_READ", "VM_WRITE"])
        self.assertNotIn("score", event.payload)
        self.assertNotIn("suspicious", event.payload)

    def test_wrong_pid_and_stale_pid_reuse_event_are_ignored(self):
        sensor = SysmonProcessAccessSensor(
            poller=FakePoller(
                ready(
                    record(record_id=1, target_process_id=88),
                    record(record_id=2, timestamp=90.0),
                )
            ),
            self_pid=999,
        )
        self.assertEqual(sensor.poll(self.context).events, ())

    def test_pre_session_record_is_ignored_even_when_game_already_existed(self):
        context = SensorContext(
            "esp_001",
            (ProcessTarget(77, r"C:\Game\game.exe", created_at=100.0),),
            session_started_at=200.0,
            observed_at=201.0,
        )
        sensor = SysmonProcessAccessSensor(
            poller=FakePoller(ready(record(timestamp=150.0))),
            self_pid=999,
            stale_tolerance_seconds=2.0,
        )
        self.assertEqual(sensor.poll(context).events, ())

    def test_session_boundary_allows_only_explicit_clock_skew_tolerance(self):
        context = SensorContext(
            "esp_001",
            (ProcessTarget(77, r"C:\Game\game.exe", created_at=100.0),),
            session_started_at=200.0,
            observed_at=201.0,
        )
        sensor = SysmonProcessAccessSensor(
            poller=FakePoller(
                ready(
                    record(record_id=1, timestamp=198.0),
                    record(record_id=2, timestamp=197.999),
                )
            ),
            self_pid=999,
            stale_tolerance_seconds=2.0,
        )
        events = sensor.poll(context).events
        self.assertEqual([event.sequence for event in events], [1])

    def test_sensor_itself_and_game_self_access_are_ignored(self):
        sensor = SysmonProcessAccessSensor(
            poller=FakePoller(
                ready(
                    record(record_id=1, source_process_id=999),
                    record(record_id=2, source_process_id=77),
                )
            ),
            self_pid=999,
        )
        self.assertEqual(sensor.poll(self.context).events, ())

    def test_unavailable_and_error_are_not_healthy_empty(self):
        unavailable = SysmonPollResult(
            SysmonStatus(False, False, False, "not_installed", "missing")
        )
        self.assertEqual(
            SysmonProcessAccessSensor(poller=FakePoller(unavailable)).poll(self.context).status,
            "unavailable",
        )
        self.assertEqual(
            SysmonProcessAccessSensor(
                poller=FakePoller(error=OSError("denied"))
            ).poll(self.context).status,
            "error",
        )

    def test_no_target_waits_without_consuming_poller(self):
        sensor = SysmonProcessAccessSensor(poller=FakePoller(error=AssertionError()))
        context = SensorContext("esp_001", observed_at=1.0)
        self.assertEqual(sensor.poll(context).status, "waiting")


if __name__ == "__main__":
    unittest.main()
