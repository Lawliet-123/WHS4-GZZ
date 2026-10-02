"""Pure interpretation of normalized observations relevant to external ESP.

This module intentionally performs no acquisition.  It does not import
Sysmon, ctypes, pywin32, Toolhelp, or any other operating-system adapter.  Its
only input is :class:`anti_esp.core.events.SensorEvent`, which keeps recorded
sessions replayable and makes detector policy independently testable.

The output is existing :class:`anti_esp.models.EvidenceEvent` data accepted by
``SuspicionEngine``.  Module changes and signature failures are deliberately
mapped to the low-cap ``behavioral_signal`` category.  A new or unsigned DLL is
therefore corroborating context, not proof of injection and not a decisive
finding on its own.
"""

from __future__ import annotations

import math
import ntpath
from typing import Any, Iterable, Mapping

from anti_esp.core.events import SensorEvent
from anti_esp.core.wintrust_codes import classify_winverifytrust_code
from anti_esp.models import EvidenceEvent


ESP_DETECTOR_ID = "local_guard.esp.v1"
SUPPORTED_EVENT_TYPES = frozenset(
    {
        "process_access",
        "window_overlap",
        "module_added",
        "module_changed",
        "module_trust",
    }
)

_ACCESS_LABELS = frozenset(
    {"CREATE_THREAD", "VM_OPERATION", "VM_READ", "VM_WRITE", "DUP_HANDLE"}
)
_TAMPER_LABELS = frozenset({"CREATE_THREAD", "VM_OPERATION", "VM_WRITE"})
_ACCESS_BITS = (
    (0x0002, "CREATE_THREAD"),
    (0x0008, "VM_OPERATION"),
    (0x0010, "VM_READ"),
    (0x0020, "VM_WRITE"),
    (0x0040, "DUP_HANDLE"),
)
_OVERLAY_STYLE_LABELS = frozenset({"LAYERED", "TRANSPARENT", "TOPMOST"})


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except (TypeError, ValueError):
            return None
    return None


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _first_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _non_empty_text(payload.get(key))
        if value is not None:
            return value
    return None


def _normalized_labels(value: Any, allowed: frozenset[str]) -> frozenset[str]:
    if isinstance(value, str):
        candidates: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        return frozenset()

    labels: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        label = candidate.strip().upper()
        if label.startswith("PROCESS_"):
            label = label[len("PROCESS_") :]
        if label in allowed:
            labels.add(label)
    return frozenset(labels)


def _access_labels(payload: Mapping[str, Any]) -> frozenset[str]:
    for key in ("access_labels", "requested_rights", "rights"):
        if key in payload:
            labels = _normalized_labels(payload.get(key), _ACCESS_LABELS)
            if labels:
                return labels

    mask = _integer(payload.get("granted_access"))
    if mask is None:
        mask = _integer(payload.get("access_mask"))
    if mask is None or mask < 0:
        return frozenset()
    return frozenset(label for bit, label in _ACCESS_BITS if mask & bit)


def _event_details(
    event: SensorEvent,
    *,
    interpretation: str,
    requires_corroboration: bool = False,
) -> dict[str, Any]:
    # SensorEvent has already validated and defensively copied this JSON object.
    # Detector-owned identity fields are written last so a payload cannot spoof
    # the link back to the original observation.
    details = dict(event.payload)
    details.update(
        {
            "sensor_event_id": event.event_id,
            "sensor_event_type": event.event_type,
            "sensor_id": event.sensor_id,
            "source_module": event.source_module,
            "detector_id": ESP_DETECTOR_ID,
            "interpretation": interpretation,
            "requires_corroboration": requires_corroboration,
        }
    )
    return details


def _evidence(
    event: SensorEvent,
    *,
    category: str,
    strength: float,
    reliability: float,
    reason: str,
    dedup_key: str,
    interpretation: str,
    requires_corroboration: bool = False,
) -> EvidenceEvent:
    return EvidenceEvent(
        category=category,
        strength=strength,
        reliability=reliability,
        source=f"detector:{ESP_DETECTOR_ID}/{event.sensor_id}",
        timestamp=event.timestamp_ms / 1000.0,
        reason=reason,
        details=_event_details(
            event,
            interpretation=interpretation,
            requires_corroboration=requires_corroboration,
        ),
        dedup_key=dedup_key,
        # A detector run over the same captured event should yield the same
        # evidence identity, which makes replay tests and storage idempotent.
        event_id=f"{event.event_id}:{category}",
        session_id=event.session_id,
        subject_id=event.subject_id,
    )


def _process_access(event: SensorEvent) -> tuple[EvidenceEvent, ...]:
    payload = event.payload
    if any(payload.get(key) is True for key in ("trusted", "source_trusted", "allowlisted")):
        return ()

    source_pid = _integer(payload.get("source_pid"))
    if source_pid is None:
        source_pid = _integer(payload.get("source_process_id"))
    target_pid = _integer(payload.get("target_pid"))
    if target_pid is None:
        target_pid = _integer(payload.get("target_process_id"))
    source_image = _first_text(payload, "source_image", "source_path", "process_path")

    # An actor identity is required both for auditability and stable dedup.
    if source_pid is None and source_image is None:
        return ()
    if source_pid is not None and target_pid is not None and source_pid == target_pid:
        return ()

    labels = _access_labels(payload)
    if not labels:
        return ()

    if labels & _TAMPER_LABELS:
        category = "process_tamper"
        strength = 1.0
        reliability = 0.97
        reason = "external process requested game-memory modification or remote-thread rights"
        interpretation = "direct dangerous process-access rights were observed"
    elif "VM_READ" in labels:
        category = "memory_read"
        strength = 0.88
        reliability = 0.95
        reason = "external process requested permission to read game memory"
        interpretation = "direct game-memory read access was observed"
    elif "DUP_HANDLE" in labels:
        category = "handle_duplicate"
        strength = 0.62
        reliability = 0.85
        reason = "external process requested handle-duplication access"
        interpretation = "handle duplication can transfer access but is not proof by itself"
    else:
        return ()

    source_identity = (
        str(source_pid)
        if source_pid is not None
        else str(source_image).replace("/", "\\").casefold()
    )
    return (
        _evidence(
            event,
            category=category,
            strength=strength,
            reliability=reliability,
            reason=reason,
            dedup_key=(
                f"process-access:{category}:{source_identity}:{event.subject_id}"
            ),
            interpretation=interpretation,
        ),
    )


def _window_overlap(
    event: SensorEvent,
    *,
    minimum_overlap_ratio: float,
) -> tuple[EvidenceEvent, ...]:
    payload = event.payload
    ratio = None
    for key in ("game_overlap_ratio", "overlap_ratio"):
        if key in payload:
            ratio = _finite_number(payload.get(key))
            break
    if ratio is None or not 0.0 <= ratio <= 1.0 or ratio < minimum_overlap_ratio:
        return ()

    styles = frozenset()
    for key in ("style_labels", "extended_style_labels"):
        if key in payload:
            styles = _normalized_labels(payload.get(key), _OVERLAY_STYLE_LABELS)
            if styles:
                break
    if len(styles) < 2:
        return ()

    window_pid = _integer(payload.get("window_pid"))
    if window_pid is None:
        window_pid = _integer(payload.get("candidate_pid"))
    game_pid = _integer(payload.get("game_pid"))
    if game_pid is None:
        game_pid = _integer(payload.get("target_pid"))
    if window_pid is None or window_pid in (0, 4):
        return ()
    if game_pid is not None and game_pid == window_pid:
        return ()

    style_strength = (
        (0.30 if "LAYERED" in styles else 0.0)
        + (0.30 if "TRANSPARENT" in styles else 0.0)
        + (0.20 if "TOPMOST" in styles else 0.0)
    )
    strength = min(1.0, style_strength + min(0.20, ratio * 0.20))
    reliability = min(0.94, 0.68 + 0.07 * len(styles) + 0.05 * ratio)
    hwnd = _integer(payload.get("hwnd"))
    window_identity = str(hwnd) if hwnd is not None else event.event_id
    return (
        _evidence(
            event,
            category="overlay",
            strength=strength,
            reliability=reliability,
            reason="external window overlaps the game and has multiple overlay-like styles",
            dedup_key=(
                f"window-overlap:{event.subject_id}:{window_pid}:{window_identity}"
            ),
            interpretation=(
                "window geometry and styles match an overlay shape; benign overlays "
                "remain possible"
            ),
            requires_corroboration=True,
        ),
    )


def _module_container(payload: Mapping[str, Any], *preferred: str) -> Mapping[str, Any]:
    for key in preferred:
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    candidate = payload.get("module")
    return candidate if isinstance(candidate, Mapping) else payload


def _module_path(payload: Mapping[str, Any], *preferred: str) -> str | None:
    container = _module_container(payload, *preferred)
    return _first_text(container, "path", "module_path", "image_path")


def _module_signal(
    event: SensorEvent,
    *,
    change_kind: str,
    strength: float,
    reliability: float,
    reason: str,
    preferred_container: tuple[str, ...] = (),
) -> tuple[EvidenceEvent, ...]:
    path = _module_path(event.payload, *preferred_container)
    if path is None:
        return ()
    identity = path.replace("/", "\\").casefold()
    return (
        _evidence(
            event,
            category="behavioral_signal",
            strength=strength,
            reliability=reliability,
            reason=reason,
            dedup_key=f"module:{change_kind}:{event.subject_id}:{identity}",
            interpretation=(
                f"module {change_kind} is a correlation signal, not proof of DLL injection"
            ),
            requires_corroboration=True,
        ),
    )


def _module_added(event: SensorEvent) -> tuple[EvidenceEvent, ...]:
    if event.payload.get("baseline_created") is True:
        return ()
    return _module_signal(
        event,
        change_kind="added",
        strength=0.28,
        reliability=0.70,
        reason="a module path appeared after the process baseline",
    )


def _module_changed(event: SensorEvent) -> tuple[EvidenceEvent, ...]:
    return _module_signal(
        event,
        change_kind="changed",
        strength=0.18,
        reliability=0.65,
        reason="a known module path reported changed load metadata",
        preferred_container=("after",),
    )


def _trust_state(payload: Mapping[str, Any]) -> str | None:
    if payload.get("hash_blacklisted") is True or payload.get("known_bad_hash") is True:
        return "blacklisted"

    for key in ("signature_status", "verification_status", "trust_status", "status"):
        raw = payload.get(key)
        if not isinstance(raw, str):
            continue
        state = raw.strip().casefold().replace("-", "_").replace(" ", "_")
        if state in {"valid", "verified", "trusted", "signed", "trust_ok"}:
            return "trusted"
        if state in {"unsigned", "not_signed", "no_signature", "missing_signature"}:
            return "unsigned"
        if state in {
            "invalid",
            "untrusted",
            "revoked",
            "bad_signature",
            "rejected",
            "policy_rejected",
        }:
            return "invalid"
        if state in {"unknown", "error", "unavailable", "not_checked"}:
            return None

    if payload.get("trusted") is True or payload.get("signature_valid") is True:
        return "trusted"
    if payload.get("signed") is False:
        return "unsigned"
    if payload.get("signature_valid") is False:
        return "invalid"

    # Accept the canonical FileIdentityEnricher key and legacy/native aliases.
    # This fallback is reached only when there was no explicit factual status;
    # an explicit backend ``error``/``unavailable`` therefore stays
    # indeterminate even if an unrelated numeric field is present.
    native_status = None
    for key in ("signature_native_code", "winverifytrust_status", "native_code"):
        native_status = _integer(payload.get(key))
        if native_status is not None:
            break
    if native_status is not None:
        outcome = classify_winverifytrust_code(native_status)
        if outcome == "trusted":
            return "trusted"
        if outcome == "unsigned":
            return "unsigned"
        if outcome == "rejected":
            return "invalid"
    return None


def _module_trust(event: SensorEvent) -> tuple[EvidenceEvent, ...]:
    state = _trust_state(event.payload)
    if state in (None, "trusted"):
        return ()

    is_baseline = (
        event.payload.get("baseline_created") is True
        or str(event.payload.get("observation_phase", "")).casefold()
        == "baseline"
    )
    # The first snapshot describes what was already present when LocalGuard
    # started. Unsigned/rejected status is retained in raw telemetry but is not
    # enough to claim a new runtime anomaly. An explicit known-bad hash remains
    # actionable because it is an independent exact-match fact.
    if is_baseline and state != "blacklisted":
        return ()

    if state == "blacklisted":
        strength, reliability = 0.95, 0.98
        reason = "module hash matched the configured known-bad list"
    elif state == "invalid":
        strength, reliability = 0.40, 0.76
        reason = "module signature or trust verification failed"
    else:
        strength, reliability = 0.30, 0.72
        reason = "module has no verifiable digital signature"

    return _module_signal(
        event,
        change_kind=f"trust-{state}",
        strength=strength,
        reliability=reliability,
        reason=reason,
    )


class EspEventDetector:
    """Convert normalized ESP-related sensor events into score evidence."""

    detector_id = ESP_DETECTOR_ID
    accepted_event_types = SUPPORTED_EVENT_TYPES

    def __init__(
        self,
        *,
        minimum_overlay_overlap_ratio: float = 0.55,
        allowlisted_paths: Iterable[str] = (),
        allowlisted_sha256: Iterable[str] = (),
    ) -> None:
        if (
            isinstance(minimum_overlay_overlap_ratio, bool)
            or not isinstance(minimum_overlay_overlap_ratio, (int, float))
            or not math.isfinite(float(minimum_overlay_overlap_ratio))
            or not 0.0 <= float(minimum_overlay_overlap_ratio) <= 1.0
        ):
            raise ValueError("minimum_overlay_overlap_ratio must be between 0 and 1")
        self.minimum_overlay_overlap_ratio = float(minimum_overlay_overlap_ratio)
        self.allowlisted_paths = frozenset(
            ntpath.normcase(ntpath.normpath(str(path).replace("/", "\\")))
            for path in allowlisted_paths
            if str(path).strip()
        )
        self.allowlisted_sha256 = frozenset(
            str(digest).strip().casefold()
            for digest in allowlisted_sha256
            if len(str(digest).strip()) == 64
        )

    def _is_allowlisted(self, event: SensorEvent) -> bool:
        payload = event.payload
        if any(
            payload.get(key) is True
            for key in ("trusted", "source_trusted", "allowlisted")
        ):
            return True
        path = _first_text(
            payload,
            "source_image",
            "source_path",
            "process_path",
            "module_path",
            "path",
        )
        if path is not None:
            normalized = ntpath.normcase(ntpath.normpath(path.replace("/", "\\")))
            if normalized in self.allowlisted_paths:
                return True
        digest = _first_text(payload, "source_sha256", "sha256")
        return digest is not None and digest.casefold() in self.allowlisted_sha256

    def detect(self, event: SensorEvent) -> tuple[EvidenceEvent, ...]:
        """Interpret one event, returning no evidence for unsupported/bad input.

        A malformed sensor payload is rejected at this trust boundary rather
        than terminating the LocalGuard polling loop.
        """

        if not isinstance(event, SensorEvent):
            return ()
        try:
            if self._is_allowlisted(event):
                return ()
            if event.event_type == "process_access":
                return _process_access(event)
            if event.event_type == "window_overlap":
                return _window_overlap(
                    event,
                    minimum_overlap_ratio=self.minimum_overlay_overlap_ratio,
                )
            if event.event_type == "module_added":
                return _module_added(event)
            if event.event_type == "module_changed":
                return _module_changed(event)
            if event.event_type == "module_trust":
                return _module_trust(event)
        except (KeyError, TypeError, ValueError, OverflowError):
            return ()
        return ()

    def detect_many(self, events: Iterable[SensorEvent]) -> tuple[EvidenceEvent, ...]:
        evidence: list[EvidenceEvent] = []
        try:
            iterator = iter(events)
        except TypeError:
            return ()
        for event in iterator:
            evidence.extend(self.detect(event))
        return tuple(evidence)


def detect_esp_event(
    event: SensorEvent,
    *,
    minimum_overlay_overlap_ratio: float = 0.55,
) -> tuple[EvidenceEvent, ...]:
    """Stateless convenience wrapper around :class:`EspEventDetector`."""

    return EspEventDetector(
        minimum_overlay_overlap_ratio=minimum_overlay_overlap_ratio
    ).detect(event)


__all__ = [
    "ESP_DETECTOR_ID",
    "SUPPORTED_EVENT_TYPES",
    "EspEventDetector",
    "detect_esp_event",
]
