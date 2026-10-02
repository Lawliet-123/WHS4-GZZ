"""Sysmon Event ID 10 (ProcessAccess) parsing and bounded polling."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import ntpath
import time
from typing import Any, Iterable, Mapping
import xml.etree.ElementTree as ET

from .config import AllowlistSettings
from .models import EvidenceEvent
from .policy import (
    FileFingerprintCache,
    PROCESS_CREATE_THREAD,
    PROCESS_DUP_HANDLE,
    PROCESS_VM_OPERATION,
    PROCESS_VM_READ,
    PROCESS_VM_WRITE,
    access_labels,
    assess_process_access,
)

try:  # Importing the parser remains possible on hosts without pywin32/Sysmon.
    import win32evtlog as _win32evtlog
except ImportError:  # pragma: no cover - exercised on non-Windows CI hosts
    _win32evtlog = None


SYSMON_CHANNEL = "Microsoft-Windows-Sysmon/Operational"
SYSMON_PROCESS_ACCESS_EVENT_ID = 10
DEFAULT_GAME_EXECUTABLE = "PenguinHotel-Win64-Shipping.exe"

ACCESS_BITS: tuple[tuple[int, str], ...] = (
    (PROCESS_VM_READ, "VM_READ"),
    (PROCESS_VM_WRITE, "VM_WRITE"),
    (PROCESS_VM_OPERATION, "VM_OPERATION"),
    (PROCESS_CREATE_THREAD, "CREATE_THREAD"),
    (PROCESS_DUP_HANDLE, "DUP_HANDLE"),
)

_CHANNEL_NOT_FOUND_CODES = {2, 3, 15007}
_ACCESS_DENIED = 5


@dataclass(frozen=True, slots=True)
class SysmonProcessAccess:
    record_id: int | None
    event_id: int
    timestamp: float | None
    computer: str
    source_process_id: int | None
    source_thread_id: int | None
    source_image: str
    source_user: str
    target_process_id: int | None
    target_image: str
    target_user: str
    granted_access: int | None
    granted_access_raw: str
    call_trace: str
    rule_name: str
    data: Mapping[str, str]

    @property
    def access_labels(self) -> tuple[str, ...]:
        return decode_granted_access(self.granted_access)

    @property
    def targets_game(self) -> bool:
        return is_game_target(self.target_image)


@dataclass(frozen=True, slots=True)
class SysmonStatus:
    installed: bool
    enabled: bool
    available: bool
    code: str
    message: str
    error_code: int | None = None

    def to_dict(self) -> dict[str, bool | str | int | None]:
        return {
            "installed": self.installed,
            "enabled": self.enabled,
            "available": self.available,
            "code": self.code,
            "message": self.message,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class SysmonPollResult:
    status: SysmonStatus
    events: tuple[SysmonProcessAccess, ...] = ()
    scanned_count: int = 0
    truncated: bool = False


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_element(root: ET.Element, name: str) -> ET.Element | None:
    return next((node for node in root.iter() if _local_name(node.tag) == name), None)


def _parse_int(value: str | int | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        try:
            return int(text, 16)
        except ValueError:
            return None


def _parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def parse_process_access_xml(xml_text: str) -> SysmonProcessAccess:
    """Parse one Sysmon ProcessAccess XML record without using Windows APIs.

    A ``ValueError`` is raised for malformed XML or a non-Event-10 record so a
    caller cannot accidentally treat another Sysmon schema as process access.
    """

    if not isinstance(xml_text, str) or not xml_text.strip():
        raise ValueError("event XML must be a non-empty string")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("malformed Sysmon event XML") from exc

    event_id_node = _first_element(root, "EventID")
    event_id = _parse_int(event_id_node.text if event_id_node is not None else None)
    if event_id != SYSMON_PROCESS_ACCESS_EVENT_ID:
        raise ValueError(f"expected Sysmon Event ID 10, got {event_id!r}")

    record_node = _first_element(root, "EventRecordID")
    computer_node = _first_element(root, "Computer")
    time_node = _first_element(root, "TimeCreated")
    system_time = time_node.attrib.get("SystemTime") if time_node is not None else None

    fields: dict[str, str] = {}
    event_data = _first_element(root, "EventData")
    if event_data is not None:
        for node in event_data:
            if _local_name(node.tag) != "Data":
                continue
            name = node.attrib.get("Name", "").strip()
            if name:
                fields[name] = node.text or ""

    timestamp = _parse_timestamp(system_time)
    if timestamp is None:
        timestamp = _parse_timestamp(fields.get("UtcTime"))

    granted_raw = fields.get("GrantedAccess", "")
    return SysmonProcessAccess(
        record_id=_parse_int(record_node.text if record_node is not None else None),
        event_id=event_id,
        timestamp=timestamp,
        computer=(computer_node.text or "") if computer_node is not None else "",
        source_process_id=_parse_int(fields.get("SourceProcessId")),
        source_thread_id=_parse_int(fields.get("SourceThreadId")),
        source_image=fields.get("SourceImage", ""),
        source_user=fields.get("SourceUser", ""),
        target_process_id=_parse_int(fields.get("TargetProcessId")),
        target_image=fields.get("TargetImage", ""),
        target_user=fields.get("TargetUser", ""),
        granted_access=_parse_int(granted_raw),
        granted_access_raw=granted_raw,
        call_trace=fields.get("CallTrace", ""),
        rule_name=fields.get("RuleName", ""),
        data=dict(fields),
    )


def decode_granted_access(mask: int | str | None) -> tuple[str, ...]:
    """Decode only the process rights relevant to this monitor."""

    parsed = _parse_int(mask)
    if parsed is None:
        return ()
    # Keep this helper's public order stable and reuse policy labels when they
    # match.  The explicit fallback also documents every relevant bit here.
    decoded = access_labels(parsed)
    if decoded:
        return decoded
    return tuple(label for bit, label in ACCESS_BITS if parsed & bit)


def has_relevant_access(mask: int | str | None) -> bool:
    return bool(decode_granted_access(mask))


def is_game_target(
    target_image: str, executable_name: str = DEFAULT_GAME_EXECUTABLE
) -> bool:
    """Match a Sysmon target path by basename, case-insensitively."""

    return (
        bool(target_image)
        and ntpath.basename(str(target_image).replace("/", "\\")).casefold()
        == ntpath.basename(executable_name).casefold()
    )


def evidence_from_process_access(
    event: SysmonProcessAccess,
    *,
    allowlist: AllowlistSettings | None = None,
    fingerprints: FileFingerprintCache | None = None,
    game_executable: str = DEFAULT_GAME_EXECUTABLE,
    session_id: str = "default",
) -> EvidenceEvent | None:
    """Turn one relevant, untrusted Event 10 record into scored evidence."""

    if not is_game_target(event.target_image, game_executable):
        return None
    if event.granted_access is None:
        return None
    if (
        event.source_process_id is not None
        and event.source_process_id == event.target_process_id
    ):
        return None

    assessment = assess_process_access(
        source_path=event.source_image,
        access_mask=event.granted_access,
        allowlist=allowlist or AllowlistSettings(),
        fingerprints=fingerprints or FileFingerprintCache(),
    )
    if assessment is None or assessment.trusted:
        return None

    # Record IDs are unique transport identities and are retained in details.
    # Scoring needs a stable actor/action identity so repeated OpenProcess calls
    # from the same source are subject to the policy's deduplication window.
    source_identity = (
        str(event.source_process_id)
        if event.source_process_id is not None
        else event.source_image.casefold()
    )
    target_identity = (
        str(event.target_process_id)
        if event.target_process_id is not None
        else event.target_image.casefold()
    )
    return EvidenceEvent(
        category=assessment.category,
        strength=assessment.strength,
        reliability=assessment.reliability,
        source="sysmon:event-10",
        timestamp=event.timestamp if event.timestamp is not None else time.time(),
        reason=assessment.reason,
        details={
            "record_id": event.record_id,
            "source_pid": event.source_process_id,
            "source_image": event.source_image,
            "source_user": event.source_user,
            "source_sha256": assessment.source_sha256,
            "target_pid": event.target_process_id,
            "target_image": event.target_image,
            "granted_access": event.granted_access,
            "granted_access_hex": f"0x{event.granted_access:08X}",
            "access_labels": list(assessment.access_labels),
        },
        dedup_key=(
            f"sysmon:{assessment.category}:{source_identity}:{target_identity}"
        ),
        session_id=session_id,
        subject_id=(
            str(event.target_process_id) if event.target_process_id is not None else None
        ),
    )


def _winerror(exc: BaseException) -> int | None:
    candidate = getattr(exc, "winerror", None)
    if isinstance(candidate, int):
        return candidate
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _close_handle(handle: Any) -> None:
    if handle is None:
        return
    close = getattr(handle, "close", None) or getattr(handle, "Close", None)
    if close is not None:
        try:
            close()
        except Exception:
            pass


def get_sysmon_status(evt_module: Any = None) -> SysmonStatus:
    """Report whether the Sysmon Operational channel exists and is enabled."""

    evt = _win32evtlog if evt_module is None else evt_module
    if evt is None:
        return SysmonStatus(
            installed=False,
            enabled=False,
            available=False,
            code="pywin32_missing",
            message="pywin32 event-log APIs are unavailable",
        )

    channel_handle = None
    try:
        channel_handle = evt.EvtOpenChannelConfig(SYSMON_CHANNEL)
        raw_enabled = evt.EvtGetChannelConfigProperty(
            channel_handle, evt.EvtChannelConfigEnabled
        )
        enabled_value = raw_enabled[0] if isinstance(raw_enabled, tuple) else raw_enabled
        enabled = bool(enabled_value)
        if not enabled:
            return SysmonStatus(
                installed=True,
                enabled=False,
                available=False,
                code="disabled",
                message="Sysmon is installed but its Operational channel is disabled",
            )
        return SysmonStatus(
            installed=True,
            enabled=True,
            available=True,
            code="ready",
            message="Sysmon Operational channel is available",
        )
    except Exception as exc:
        error_code = _winerror(exc)
        if error_code in _CHANNEL_NOT_FOUND_CODES:
            return SysmonStatus(
                installed=False,
                enabled=False,
                available=False,
                code="not_installed",
                message="Sysmon Operational channel was not found",
                error_code=error_code,
            )
        if error_code == _ACCESS_DENIED:
            return SysmonStatus(
                installed=True,
                enabled=False,
                available=False,
                code="access_denied",
                message="access to the Sysmon Operational channel was denied",
                error_code=error_code,
            )
        return SysmonStatus(
            installed=False,
            enabled=False,
            available=False,
            code="error",
            message=f"unable to inspect Sysmon channel: {exc}",
            error_code=error_code,
        )
    finally:
        _close_handle(channel_handle)


def deduplicate_records(
    events: Iterable[SysmonProcessAccess],
    seen_record_ids: Iterable[int] = (),
) -> tuple[SysmonProcessAccess, ...]:
    """Return ascending records with each non-null RecordID at most once."""

    seen = set(seen_record_ids)
    accepted: list[SysmonProcessAccess] = []
    for event in events:
        if event.record_id is not None:
            if event.record_id in seen:
                continue
            seen.add(event.record_id)
        accepted.append(event)
    return tuple(
        sorted(
            accepted,
            key=lambda item: (
                item.record_id is None,
                item.record_id if item.record_id is not None else 0,
            ),
        )
    )


class SysmonPoller:
    """Poll a bounded recent window of Event 10 records without duplicates."""

    def __init__(
        self,
        *,
        channel: str = SYSMON_CHANNEL,
        batch_size: int = 64,
        max_events_per_poll: int = 512,
        seen_record_limit: int = 4096,
        include_existing: bool = True,
        evt_module: Any = None,
    ) -> None:
        if batch_size <= 0 or max_events_per_poll <= 0 or seen_record_limit <= 0:
            raise ValueError("polling limits must be positive")
        self.channel = channel
        self.batch_size = int(batch_size)
        self.max_events_per_poll = int(max_events_per_poll)
        self.seen_record_limit = int(seen_record_limit)
        self.include_existing = bool(include_existing)
        self._evt = _win32evtlog if evt_module is None else evt_module
        self._seen_order: deque[int] = deque()
        self._seen_ids: set[int] = set()
        self._primed = False

    @property
    def last_record_id(self) -> int | None:
        return max(self._seen_ids) if self._seen_ids else None

    def _remember(self, record_id: int | None) -> None:
        if record_id is None or record_id in self._seen_ids:
            return
        while len(self._seen_order) >= self.seen_record_limit:
            removed = self._seen_order.popleft()
            self._seen_ids.discard(removed)
        self._seen_order.append(record_id)
        self._seen_ids.add(record_id)

    def poll(self) -> SysmonPollResult:
        status = get_sysmon_status(self._evt)
        if not status.available or self._evt is None:
            return SysmonPollResult(status=status)

        query_handle = None
        scanned = 0
        truncated = False
        parsed_events: list[SysmonProcessAccess] = []
        encountered_seen = False
        try:
            flags = self._evt.EvtQueryChannelPath | self._evt.EvtQueryReverseDirection
            query_handle = self._evt.EvtQuery(
                self.channel,
                flags,
                "*[System[(EventID=10)]]",
            )

            while scanned < self.max_events_per_poll and not encountered_seen:
                request_count = min(
                    self.batch_size, self.max_events_per_poll - scanned
                )
                native_events = self._evt.EvtNext(query_handle, request_count)
                if not native_events:
                    break
                scanned += len(native_events)
                for native_event in native_events:
                    try:
                        xml_text = self._evt.EvtRender(
                            native_event, self._evt.EvtRenderEventXml
                        )
                        event = parse_process_access_xml(xml_text)
                    except (ValueError, TypeError):
                        continue
                    finally:
                        _close_handle(native_event)

                    if event.record_id is not None and event.record_id in self._seen_ids:
                        encountered_seen = True
                        continue
                    parsed_events.append(event)

            if scanned >= self.max_events_per_poll and not encountered_seen:
                truncated = True
        except Exception as exc:
            error_code = _winerror(exc)
            failed = SysmonStatus(
                installed=True,
                enabled=True,
                available=False,
                code="query_error",
                message=f"failed to query Sysmon Event ID 10: {exc}",
                error_code=error_code,
            )
            return SysmonPollResult(
                status=failed,
                scanned_count=scanned,
                truncated=truncated,
            )
        finally:
            _close_handle(query_handle)

        ordered = deduplicate_records(parsed_events, self._seen_ids)
        for event in ordered:
            self._remember(event.record_id)

        should_emit = self.include_existing or self._primed
        self._primed = True
        return SysmonPollResult(
            status=status,
            events=ordered if should_emit else (),
            scanned_count=scanned,
            truncated=truncated,
        )


# Descriptive alias for orchestration code that treats sensors as event sources.
SysmonEventSource = SysmonPoller


__all__ = [
    "ACCESS_BITS",
    "DEFAULT_GAME_EXECUTABLE",
    "SYSMON_CHANNEL",
    "SYSMON_PROCESS_ACCESS_EVENT_ID",
    "SysmonPollResult",
    "SysmonPoller",
    "SysmonEventSource",
    "SysmonProcessAccess",
    "SysmonStatus",
    "decode_granted_access",
    "deduplicate_records",
    "evidence_from_process_access",
    "get_sysmon_status",
    "has_relevant_access",
    "is_game_target",
    "parse_process_access_xml",
]
