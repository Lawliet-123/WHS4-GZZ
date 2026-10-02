from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OverlaySettings:
    enabled: bool = True
    minimum_overlap_ratio: float = 0.55
    cooldown_seconds: float = 30.0


@dataclass(frozen=True)
class AllowlistSettings:
    paths: tuple[str, ...] = ()
    sha256: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleMonitorSettings:
    enabled: bool = False
    scan_interval_seconds: float = 5.0
    verify_signatures: bool = True


@dataclass(frozen=True)
class HandleMonitorSettings:
    enabled: bool = False
    scan_interval_seconds: float = 2.0
    cooldown_seconds: float = 5.0


@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool = False
    root: Path = Path("data/sessions")
    session_id: str | None = None
    player_id: str = "local_player"
    scenario: str | None = None
    cheat_on_ms: int | None = None
    cheat_off_ms: int | None = None


@dataclass(frozen=True)
class IdentitySettings:
    enabled: bool = False
    pepper_environment: str = "MECCHA_GUARD_IDENTITY_PEPPER"


@dataclass(frozen=True)
class Settings:
    game_executable: str = "PenguinHotel-Win64-Shipping.exe"
    poll_interval_seconds: float = 0.5
    overlay_scan_interval_seconds: float = 2.0
    database_path: Path = Path("data/anti_esp.sqlite3")
    event_window_seconds: float = 900.0
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    allowlist: AllowlistSettings = field(default_factory=AllowlistSettings)
    module_monitor: ModuleMonitorSettings = field(default_factory=ModuleMonitorSettings)
    handle_monitor: HandleMonitorSettings = field(default_factory=HandleMonitorSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    identity: IdentitySettings = field(default_factory=IdentitySettings)
    response_mode: str = "observe"


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _normalise_paths(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(Path(str(value))).casefold() for value in values if str(value).strip())


def _normalise_hashes(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    hashes: list[str] = []
    for value in values:
        candidate = str(value).strip().lower()
        if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
            hashes.append(candidate)
    return tuple(hashes)


def _optional_nonnegative_int(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer or null")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer or null") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")
    return parsed


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be an object")

    overlay_raw = raw.get("overlay") if isinstance(raw.get("overlay"), dict) else {}
    allowlist_raw = raw.get("allowlist") if isinstance(raw.get("allowlist"), dict) else {}
    response_raw = raw.get("response") if isinstance(raw.get("response"), dict) else {}
    module_raw = (
        raw.get("module_monitor")
        if isinstance(raw.get("module_monitor"), dict)
        else {}
    )
    handle_raw = (
        raw.get("handle_monitor")
        if isinstance(raw.get("handle_monitor"), dict)
        else {}
    )
    telemetry_raw = (
        raw.get("telemetry") if isinstance(raw.get("telemetry"), dict) else {}
    )
    identity_raw = (
        raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    )

    database_value = Path(str(raw.get("database_path", "data/anti_esp.sqlite3")))
    if not database_value.is_absolute():
        database_value = config_path.parent / database_value
    telemetry_root = Path(str(telemetry_raw.get("root", "data/sessions")))
    if not telemetry_root.is_absolute():
        telemetry_root = config_path.parent / telemetry_root

    response_mode = str(response_raw.get("mode", "observe")).strip().lower()
    if response_mode != "observe":
        raise ValueError("only the non-destructive 'observe' response mode is supported")

    game_executable = str(
        raw.get("game_executable", "PenguinHotel-Win64-Shipping.exe")
    ).strip()
    if not game_executable or any(separator in game_executable for separator in ("/", "\\")):
        raise ValueError("game_executable must be a file name, not a path")

    session_value = telemetry_raw.get("session_id")
    session_id = None if session_value is None else str(session_value).strip()
    if session_value is not None and not session_id:
        raise ValueError("telemetry.session_id must be non-empty or null")
    player_id = str(telemetry_raw.get("player_id", "local_player")).strip()
    if not player_id:
        raise ValueError("telemetry.player_id must be non-empty")
    scenario_value = telemetry_raw.get("scenario")
    scenario = None if scenario_value is None else str(scenario_value).strip().lower()
    if scenario_value is not None and not scenario:
        raise ValueError("telemetry.scenario must be non-empty or null")
    cheat_on_ms = _optional_nonnegative_int(
        telemetry_raw.get("cheat_on_ms"), name="telemetry.cheat_on_ms"
    )
    cheat_off_ms = _optional_nonnegative_int(
        telemetry_raw.get("cheat_off_ms"), name="telemetry.cheat_off_ms"
    )
    if (
        cheat_on_ms is not None
        and cheat_off_ms is not None
        and cheat_off_ms < cheat_on_ms
    ):
        raise ValueError("telemetry.cheat_off_ms must be >= cheat_on_ms")
    pepper_environment = str(
        identity_raw.get("pepper_environment", "MECCHA_GUARD_IDENTITY_PEPPER")
    ).strip()
    if not pepper_environment:
        raise ValueError("identity.pepper_environment must be non-empty")

    return Settings(
        game_executable=game_executable,
        poll_interval_seconds=_bounded_float(
            raw.get("poll_interval_seconds"), default=0.5, minimum=0.1, maximum=10.0
        ),
        overlay_scan_interval_seconds=_bounded_float(
            raw.get("overlay_scan_interval_seconds"),
            default=2.0,
            minimum=0.5,
            maximum=60.0,
        ),
        database_path=database_value.resolve(),
        event_window_seconds=_bounded_float(
            raw.get("event_window_seconds"), default=900.0, minimum=30.0, maximum=86400.0
        ),
        overlay=OverlaySettings(
            enabled=bool(overlay_raw.get("enabled", True)),
            minimum_overlap_ratio=_bounded_float(
                overlay_raw.get("minimum_overlap_ratio"),
                default=0.55,
                minimum=0.1,
                maximum=1.0,
            ),
            cooldown_seconds=_bounded_float(
                overlay_raw.get("cooldown_seconds"),
                default=30.0,
                minimum=1.0,
                maximum=3600.0,
            ),
        ),
        allowlist=AllowlistSettings(
            paths=_normalise_paths(allowlist_raw.get("paths")),
            sha256=_normalise_hashes(allowlist_raw.get("sha256")),
        ),
        module_monitor=ModuleMonitorSettings(
            enabled=bool(module_raw.get("enabled", False)),
            scan_interval_seconds=_bounded_float(
                module_raw.get("scan_interval_seconds"),
                default=5.0,
                minimum=1.0,
                maximum=300.0,
            ),
            verify_signatures=bool(module_raw.get("verify_signatures", True)),
        ),
        handle_monitor=HandleMonitorSettings(
            enabled=bool(handle_raw.get("enabled", False)),
            scan_interval_seconds=_bounded_float(
                handle_raw.get("scan_interval_seconds"),
                default=2.0,
                minimum=0.5,
                maximum=60.0,
            ),
            cooldown_seconds=_bounded_float(
                handle_raw.get("cooldown_seconds"),
                default=5.0,
                minimum=1.0,
                maximum=3600.0,
            ),
        ),
        telemetry=TelemetrySettings(
            enabled=bool(telemetry_raw.get("enabled", False)),
            root=telemetry_root.resolve(),
            session_id=session_id,
            player_id=player_id,
            scenario=scenario,
            cheat_on_ms=cheat_on_ms,
            cheat_off_ms=cheat_off_ms,
        ),
        identity=IdentitySettings(
            enabled=bool(identity_raw.get("enabled", False)),
            pepper_environment=pepper_environment,
        ),
        response_mode=response_mode,
    )
