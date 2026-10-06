"""Bounded local ESP capture for an explicitly authorized private test room.

No game launch, injection, memory writes, installation or network transmission.
The unmodified ESP requests pymem's ALL_ACCESS handle/SeDebugPrivilege defaults.
Only successful, observed sessions are exported; failures remain local.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import ntpath
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from uuid import uuid4


REPO = Path(__file__).resolve().parents[4]
ESP_ROOT = REPO / "client/detectors/esp"
ESP_SOURCE = REPO / "modules/esp/esp.py"
ESP_SHA256 = "897dd9da21b39e738306c79b8225a8b9bd35de8a52d038c83ce4d829f39da973"
GAME_EXE = "PenguinHotel-Win64-Shipping.exe"
SENSORS = frozenset({"collector", "game", "privilege", "sysmon", "overlay", "modules", "handles"})
VALID_STATUS = {"LOW", "REVIEW", "HIGH", "CRITICAL"}


def emit(kind: str, **fields: object) -> None:
    print(json.dumps({"kind": kind, **fields}, allow_nan=False, sort_keys=True), flush=True)


def checked_call(function, *values, **options):
    try:
        return function(*values, **options)
    except Exception as error:
        error.capture_failure_stage = function.__name__
        raise


def write_json(path: Path, document: object, *, exclusive: bool = True) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(document, stream, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")


def isolated_environment(run_dir: Path, windows_root: str, python: Path) -> dict[str, str]:
    """Build from an allowlist, never from a copy of the inherited environment."""
    windows = Path(windows_root)
    if not windows.is_absolute():
        raise ValueError("Windows root must be absolute")
    profile = run_dir / "profile"
    locations = {"USERPROFILE": profile, "APPDATA": profile / "AppData/Roaming",
                 "LOCALAPPDATA": profile / "AppData/Local", "TEMP": run_dir / "temp",
                 "TMP": run_dir / "temp"}
    for location in set(locations.values()):
        location.mkdir(parents=True, exist_ok=True)
    return {"SystemRoot": str(windows), "WINDIR": str(windows),
            "PATH": os.pathsep.join((str(python.parent), str(windows / "System32"), str(windows))),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8", **{key: str(value) for key, value in locations.items()}}


def select_game(processes: object, game_pid: int, expected_created: float | None = None):
    values = tuple(processes)
    if len(values) != 1:
        raise ValueError("Exactly one approved game must already be running")
    game = values[0]
    if game.pid != game_pid or ntpath.basename(game.image_path or "") != GAME_EXE:
        raise ValueError("The approved game identity does not match")
    created = game.created_at
    if type(created) not in (float, int) or not math.isfinite(created) or created <= 0:
        raise ValueError("The game creation time is unavailable")
    if expected_created is not None and not math.isclose(created, expected_created, abs_tol=1e-6, rel_tol=0):
        raise ValueError("The approved game process ended or changed")
    return game


def platform_preflight(game_pid: int, expected_created: float | None = None):
    if os.name != "nt" or sys.version_info < (3, 11) or struct.calcsize("P") != 8:
        raise ValueError("64-bit Windows Python 3.11+ is required")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError("Administrator privileges are required")
    import win32service
    import win32evtlog as evt
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
    try:
        services = win32service.EnumServicesStatus(manager)
        if not any("sysmon" in (name + " " + display).casefold()
                   and status[1] == win32service.SERVICE_RUNNING for name, display, status in services):
            raise ValueError("Sysmon must already be running")
    finally:
        win32service.CloseServiceHandle(manager)
    handles = []
    try:
        channel = "Microsoft-Windows-Sysmon/Operational"
        config = evt.EvtOpenChannelConfig(channel)
        handles.append(config)
        enabled = evt.EvtGetChannelConfigProperty(config, evt.EvtChannelConfigEnabled)
        if not bool(enabled[0] if isinstance(enabled, tuple) else enabled):
            raise ValueError("The Sysmon channel is disabled")
        handles.append(evt.EvtQuery(channel, evt.EvtQueryChannelPath | evt.EvtQueryReverseDirection,
                                    "*[System[(EventID=10)]]"))
    finally:
        for handle in reversed(handles):
            close = getattr(handle, "close", None) or getattr(handle, "Close", None)
            if close:
                close()
    for root in (REPO, ESP_ROOT):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    from anti_esp.windows_api import find_processes_by_name
    return select_game(find_processes_by_name(GAME_EXE), game_pid, expected_created)


def verified_frame(module, overlay, now: float) -> dict[str, object] | None:
    """Extract scalar readiness only from a real, fresh original render snapshot."""
    frame = overlay._snapshots.latest()
    if not isinstance(frame, module.FrameRenderSnapshot) or not overlay.isVisible() or not overlay.config.enabled:
        return None
    if frame.error is not None or not isinstance(frame.camera, module.CameraSnapshot):
        return None
    if not all(math.isfinite(v) for v in (*frame.camera.loc, *frame.camera.rot, frame.camera.fov)):
        return None
    if not 0 < frame.camera.fov < 180 or not 0 <= now - frame.started_at < overlay.STALE_AFTER_SECONDS:
        return None
    if not 0 <= frame.collection_ms < overlay.STALE_AFTER_SECONDS * 1000:
        return None
    if not isinstance(frame.players, tuple) or not all(isinstance(p, module.PlayerRenderSnapshot) for p in frame.players):
        return None
    if not all(all(math.isfinite(v) for v in p.position) for p in frame.players):
        return None
    local = sum(p.is_local is True for p in frame.players)
    if local < 1 or dict(frame.stats).get("collection_valid") is not True:
        return None
    return {"frame_sequence": frame.sequence, "local_player_count": local,
            "remote_player_count": sum(p.is_local is False for p in frame.players),
            "camera_valid": True, "overlay_visible": True, "collection_ms": frame.collection_ms}


def validate_ready(record: dict, child_pid: int, game_pid: int, t0: float, latest: float) -> dict:
    if record.get("kind") != "esp_read_ready" or record.get("source_sha256") != ESP_SHA256:
        raise ValueError("Missing audited ESP readiness")
    if record.get("pid") != child_pid or record.get("game_pid") != game_pid:
        raise ValueError("ESP readiness identity mismatch")
    timestamp = record.get("observed_at_epoch")
    if type(timestamp) not in (float, int) or not math.isfinite(timestamp) or not t0 <= timestamp <= latest + 0.25:
        raise ValueError("Invalid observed readiness time")
    for name in ("frame_sequence", "local_player_count", "remote_player_count"):
        if type(record.get(name)) is not int or record[name] < (1 if name != "remote_player_count" else 0):
            raise ValueError("Invalid actual frame counts")
    if record.get("camera_valid") is not True or record.get("overlay_visible") is not True:
        raise ValueError("Actual camera/overlay readiness is required")
    duration = record.get("collection_ms")
    if type(duration) not in (float, int) or not math.isfinite(duration) or not 0 <= duration <= 2000:
        raise ValueError("Invalid observed frame duration")
    return {key: record[key] for key in ("observed_at_epoch", "frame_sequence", "local_player_count",
            "remote_player_count", "camera_valid", "overlay_visible", "collection_ms")}


def validate_handle_open(record: dict, child_pid: int, game_pid: int, created: float, t0: float, latest: float) -> dict:
    if (record.get("kind") != "esp_handle_open" or record.get("source_sha256") != ESP_SHA256
            or record.get("pid") != child_pid or record.get("game_pid") != game_pid
            or record.get("birth_verified") is not True or record.get("source_config_enabled") is not True):
        raise ValueError("Verified pinned ESP handle opening is required")
    birth = record.get("game_created_at")
    if type(birth) not in (float, int) or not math.isfinite(birth) or not math.isclose(birth, created, abs_tol=1e-6, rel_tol=0):
        raise ValueError("ESP handle opening game birth differs")
    before, opened = record.get("opening_started_epoch"), record.get("observed_at_epoch")
    if (not all(type(v) in (float, int) and math.isfinite(v) for v in (before, opened))
            or not t0 <= before <= opened <= latest + 0.25):
        raise ValueError("Invalid actual ESP handle opening time")
    return {"opening_started_epoch": before, "observed_at_epoch": opened, "birth_verified": True,
            "source_config_enabled": True}


def lifecycle_timings(proof: dict) -> tuple[int, int, int]:
    t0, opened, ready_epoch, exited = (proof["session_t0_epoch"], proof["handle_open"]["observed_at_epoch"],
        proof["ready"]["observed_at_epoch"], proof["esp_exit_epoch"])
    on, off = measured_timings(t0, opened, exited)
    if not opened <= ready_epoch <= exited:
        raise ValueError("Actual handle/readiness/exit observations are out of order")
    return on, round((ready_epoch - t0) * 1000), off


def esp_child(args) -> int:
    game = platform_preflight(args.game_pid, args.expected_created)
    if hashlib.sha256(ESP_SOURCE.read_bytes()).hexdigest() != ESP_SHA256:
        raise ValueError("The supplied ESP source fingerprint changed")
    spec = importlib.util.spec_from_file_location("bounded_original_esp", ESP_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original_init, original_paint = module.Overlay.__init__, module.Overlay.paintEvent
    original_pymem = module.pymem.Pymem
    from anti_esp.windows_api import find_processes_by_name
    class PinnedPymem(original_pymem):
        def __init__(self, process_name=None, *values, **options):
            if process_name != GAME_EXE:
                raise ValueError("Only the approved game can be opened")
            select_game(find_processes_by_name(GAME_EXE), game.pid, game.created_at)
            super().__init__(game.pid)
            try:
                select_game(find_processes_by_name(GAME_EXE), game.pid, game.created_at)
            except BaseException:
                self.close_process()
                raise
        def open_process_from_id(self, process_id):
            if process_id != game.pid:
                raise ValueError("Only the approved game PID can be opened")
            select_game(find_processes_by_name(GAME_EXE), game.pid, game.created_at)
            if module.Config().enabled is not True:
                raise ValueError("The original ESP must start enabled")
            before = time.time()
            super().open_process_from_id(process_id)
            opened = time.time()
            try:
                select_game(find_processes_by_name(GAME_EXE), game.pid, game.created_at)
            except BaseException:
                self.close_process()
                raise
            emit("esp_handle_open", source_sha256=ESP_SHA256, pid=os.getpid(), game_pid=game.pid,
                 game_created_at=game.created_at, birth_verified=True, source_config_enabled=True,
                 opening_started_epoch=before, observed_at_epoch=opened)
    module.pymem.Pymem = PinnedPymem
    ready = False
    last_heartbeat = 0.

    def measured_paint(overlay, event):
        nonlocal ready
        original_paint(overlay, event)
        if ready:
            return
        facts = verified_frame(module, overlay, time.monotonic())
        if facts is not None and overlay.esp.pm.process_id == game.pid:
            ready = True
            emit("esp_read_ready", source_sha256=ESP_SHA256, pid=os.getpid(), game_pid=game.pid,
                 observed_at_epoch=time.time(), **facts)

    def bounded_init(overlay, *values, **options):
        original_init(overlay, *values, **options)
        timer = module.QTimer(overlay)
        def check_stop():
            nonlocal last_heartbeat
            now = time.monotonic()
            if (Path(args.run_dir) / "esp-stop").exists() or now >= args.deadline:
                module.QApplication.instance().quit()
                return
            if ready and now - last_heartbeat >= 0.5:
                facts = verified_frame(module, overlay, now)
                emit("esp_read_health", source_sha256=ESP_SHA256, pid=os.getpid(), game_pid=game.pid,
                     observed_at_epoch=time.time(), fresh=facts is not None, **(facts or {}))
                last_heartbeat = now
        timer.timeout.connect(check_stop)
        timer.start(50)
        overlay._bounded_capture_timer = timer

    module.Overlay.__init__, module.Overlay.paintEvent = bounded_init, measured_paint
    # Original main uses QApplication(sys.argv); do not pass helper options to Qt.
    sys.argv = [str(ESP_SOURCE)]
    return int(module.main())


def safe_health(controller) -> dict:
    snapshot = controller.snapshot()
    states = controller.sensor_status()
    confidence = snapshot.get("observation_confidence")
    healthy = (snapshot.get("status") in VALID_STATUS and type(confidence) in (float, int)
               and math.isfinite(confidence) and confidence >= 60
               and all(states.get(name, {}).get("status") == "online" for name in SENSORS)
               and not any(states[name].get("partial") is True or states[name].get("truncated") is True for name in SENSORS))
    return {"healthy": healthy, "status": snapshot.get("status"),
            "observation_confidence": confidence,
            "sensors": {name: states.get(name, {}).get("status") for name in sorted(SENSORS)}}


def stop_own_child(child, marker: Path) -> tuple[float, bool]:
    marker.touch(exist_ok=False)
    forced = False
    try:
        child.wait(timeout=6)
    except subprocess.TimeoutExpired:
        forced = True
        child.kill()
        child.wait(timeout=6)
    return time.time(), forced


def own_process_job(child):
    """Closing this Windows job terminates only the worker and its descendants."""
    job = api = None
    try:
        import win32api
        import win32job
        api = win32api
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        process = win32api.OpenProcess(0x0100 | 0x0001, False, child.pid)
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            win32api.CloseHandle(process)
    except BaseException:
        try:
            if job is not None and api is not None:
                api.CloseHandle(job)
        finally:
            child.kill()
            child.wait(timeout=6)
        raise
    return job


def measured_timings(t0: float, ready_epoch: float, exited_epoch: float) -> tuple[int, int]:
    if not all(type(v) in (float, int) and math.isfinite(v) for v in (t0, ready_epoch, exited_epoch)):
        raise ValueError("Measured timings must be finite")
    if not t0 <= ready_epoch <= exited_epoch:
        raise ValueError("Measured timings are out of order")
    return round((ready_epoch - t0) * 1000), round((exited_epoch - t0) * 1000)


def validate_capture(session_dir: Path, session: str, player: str, scenario: str, esp_pid: int | None,
                     game_pid: int, game_created_at: float) -> tuple[dict, int]:
    from shared.schema import decode_event
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != "meccha.telemetry-session.v1" or manifest.get("status") != "completed"
            or manifest.get("failure_reason") is not None or manifest.get("session_id") != session
            or manifest.get("player_id") != player or manifest.get("game_executable") != GAME_EXE
            or manifest.get("test_metadata", {}).get("scenario") != "unvalidated_realgame"
            or manifest.get("test_metadata", {}).get("requested_scenario") != scenario
            or manifest.get("producer", {}).get("name") != "meccha-esp-localguard"
            or manifest.get("producer", {}).get("observation_contract") != "meccha.esp-observation.v1"):
        raise ValueError("Capture schema, identity or completion is invalid")
    summary = manifest.get("observation_summary", {})
    expected_instance = hashlib.sha256(f"{session}\0{game_pid}\0{game_created_at:.9f}".encode("utf-8")).hexdigest()
    polls = summary.get("poll_count")
    if (summary.get("schema_version") != "meccha.esp-observation.v1" or summary.get("session_id") != session
            or summary.get("player_id") != player or type(polls) is not int or polls < 2
            or summary.get("healthy_poll_count") != polls or summary.get("insufficient_poll_count") != 0
            or summary.get("timestamp_regression_count") != 0 or summary.get("game_instance_changed") is not False
            or summary.get("game_instance_missing_poll_count") != 0
            or summary.get("game_instance_sha256") != expected_instance
            or summary.get("last_status") not in VALID_STATUS):
        raise ValueError("Complete healthy observation coverage is required")
    for key in ("minimum_observation_confidence", "min_observation_confidence", "last_observation_confidence"):
        value = summary.get(key)
        if type(value) not in (float, int) or not math.isfinite(value) or not 60 <= value <= 100:
            raise ValueError("Insufficient observation confidence")
    coverage = summary.get("required_sensors", {})
    if set(coverage) != SENSORS:
        raise ValueError("All enabled production sensors must be covered")
    for sensor in coverage.values():
        if (sensor.get("poll_count") != polls or sensor.get("online_poll_count") != polls
                or sensor.get("last_status") != "online" or type(sensor.get("online_observed_count")) is not int
                or not 1 <= sensor["online_observed_count"] <= sensor.get("observed_count", -1) <= polls):
            raise ValueError("Actual successful sensor observation is required")
    if summary.get("last_poll_ms", 0) <= summary.get("first_poll_ms", 0):
        raise ValueError("A positive observation interval is required")
    count = matching = 0
    with (session_dir / "events.jsonl").open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = decode_event(line)
            if (event["session_id"] != session or event["player_id"] != player or event["module"] != "esp"
                    or event["evidence"].get("synthetic") is True):
                raise ValueError("Only genuine matching ESP events are accepted")
            count += 1
            evidence = event["evidence"]
            matching += int(esp_pid is not None and event["raw_score"] > 0
                            and (evidence.get("source_pid") == esp_pid or evidence.get("window_pid") == esp_pid))
    if type(manifest.get("event_count")) is not int or manifest["event_count"] != count:
        raise ValueError("Capture event count does not match")
    if scenario == "esp" and matching == 0:
        raise ValueError("No detector event attributable to the actual ESP child")
    return manifest, matching


def annotate_completed(session_dir: Path, manifest: dict, proof: dict) -> None:
    """Annotate only this owned completed manifest; event/raw bytes are untouched."""
    if manifest.get("status") != "completed" or manifest.get("failure_reason") is not None:
        raise ValueError("Only completed captures can receive measured annotations")
    if proof.get("status") != "validated" or manifest["test_metadata"].get("scenario") != "unvalidated_realgame":
        raise ValueError("Only validated pending captures can be promoted")
    metadata = dict(manifest["test_metadata"])
    scenario = proof.get("scenario")
    if metadata.get("requested_scenario") != scenario:
        raise ValueError("Requested scenario differs from validated proof")
    if scenario == "esp":
        on, first_ready, off = lifecycle_timings(proof)
        metadata.update(cheat_on_ms=on, first_read_ready_ms=first_ready, cheat_off_ms=off)
    elif scenario == "normal":
        if proof.get("handle_open") is not None or proof.get("ready") is not None or proof.get("esp_exit_epoch") is not None:
            raise ValueError("NORMAL capture cannot contain ESP lifecycle evidence")
        metadata.update(cheat_on_ms=None, first_read_ready_ms=None, cheat_off_ms=None)
    else:
        raise ValueError("Unknown scenario")
    metadata["scenario"] = scenario
    metadata["timing_provenance"] = {"kind": "observed_original_esp_lifecycle" if metadata["scenario"] == "esp" else "no_esp_spawned",
        "on_basis": "successful pinned game handle opening, observed immediately after API return" if proof.get("ready") else None,
        "first_read_ready_ms": metadata["first_read_ready_ms"],
        "handle_open_observation_bounds_ms": [round((proof["handle_open"][key] - proof["session_t0_epoch"]) * 1000)
            for key in ("opening_started_epoch", "observed_at_epoch")] if proof.get("ready") else None,
        "off_basis": "observed exact child process exit" if proof.get("ready") else None,
        "launch_precedes_on": bool(proof.get("ready")), "off_observation_poll_seconds": 0.1,
        "source_sha256": ESP_SHA256 if proof.get("ready") else None,
        "source_requested_access": "PROCESS_ALL_ACCESS (0x1f0fff), SeDebugPrivilege attempted" if proof.get("ready") else None,
        "fresh_read_heartbeat_count": proof.get("fresh_read_heartbeat_count", 0),
        "maximum_heartbeat_gap_seconds": proof.get("maximum_heartbeat_gap_seconds"),
        "remote_player_count_at_first_ready": proof["ready"]["remote_player_count"] if proof.get("ready") else None,
        "remote_rendering_claimed": False}
    updated = dict(manifest, test_metadata=metadata)
    temporary = session_dir / (".manifest-realgame-" + uuid4().hex + ".tmp")
    write_json(temporary, updated)
    temporary.replace(session_dir / "manifest.json")


def warmup_settings(settings, run_dir: Path, session_id: str):
    return replace(settings, database_path=run_dir / "warmup.sqlite3",
        telemetry=replace(settings.telemetry, session_id=session_id, scenario="unvalidated_warmup"))


def capture_worker(args) -> int:
    game = checked_call(platform_preflight, args.game_pid, args.expected_created)
    from anti_esp.config import load_settings
    from anti_esp.controller import AntiEspController
    from anti_esp.core.session import SessionTelemetryWriter
    from anti_esp.windows_api import find_processes_by_name
    from anti_esp.sysmon import SysmonPoller
    from ReplayAnalyzer.tools.export_esp_replay import export_session
    run_dir = Path(args.run_dir).resolve()
    session_dir = run_dir / "sessions" / args.session_id
    settings = load_settings(run_dir / "config.json")
    def exact_game_provider(name):
        return (select_game(find_processes_by_name(name), game.pid, game.created_at),)
    # Warmup is persisted separately and is never eligible for Replay labels.
    poller = SysmonPoller(include_existing=True)
    warmup_id = args.session_id + "_warmup"
    warmup_writer = SessionTelemetryWriter(run_dir / "warmup", session_id=warmup_id,
        game_executable=GAME_EXE, player_id=args.player_id, test_metadata={"scenario": "unvalidated_warmup"})
    warmup = checked_call(AntiEspController, warmup_settings(settings, run_dir, warmup_id),
        telemetry_writer=warmup_writer, process_provider=exact_game_provider, poller=poller,
        anticheat_pid_provider=lambda: (), team_event_sink=None)
    warmup_started = time.monotonic()
    try:
        warmup.start()
        while True:
            exact_game_provider(GAME_EXE)
            if warmup.fatal_error is not None:
                raise ValueError("Production warmup collector failed")
            summary = json.loads(warmup_writer.manifest_path.read_text("utf-8")).get("observation_summary", {})
            if safe_health(warmup)["healthy"] and summary.get("healthy_poll_count", 0) >= 2:
                break
            if time.monotonic() - warmup_started > 30:
                raise ValueError("Production collector did not establish healthy warmup coverage")
            time.sleep(0.1)
    except BaseException as error:
        warmup.mark_failed("WarmupRejected")
        error.capture_failure_stage = "warmup"
        raise
    finally:
        warmup.close()
    emit("warmup_complete", healthy_poll_count=summary["healthy_poll_count"], replay_eligible=False)
    t0, started = time.time(), time.monotonic()
    deadline = started + args.duration
    writer = SessionTelemetryWriter(run_dir / "sessions", session_id=args.session_id,
        game_executable=GAME_EXE, player_id=args.player_id,
        test_metadata={"scenario": "unvalidated_realgame", "requested_scenario": args.scenario,
                       "cheat_on_ms": None, "cheat_off_ms": None})
    controller = checked_call(AntiEspController, settings, telemetry_writer=writer, session_started_at=t0,
                                   process_provider=exact_game_provider, poller=poller,
                                   anticheat_pid_provider=lambda: (), team_event_sink=None)
    child = None
    messages = queue.Queue()
    proof = {"schema_version": "meccha.realgame-esp-lifecycle.v1", "session_id": args.session_id,
             "scenario": args.scenario, "session_t0_epoch": t0, "handle_open": None, "ready": None, "esp_exit_epoch": None,
             "forced_termination": False, "central_telemetry": "off", "source_sha256": ESP_SHA256,
             "fresh_read_heartbeat_count": 0, "maximum_heartbeat_gap_seconds": 0.}
    last_health = None
    active_until = None
    ready_deadline = None
    success = False
    stage = "capture_worker"
    try:
        controller.start()
        while time.monotonic() < deadline:
            now = time.monotonic()
            exact_game_provider(GAME_EXE)
            if controller.fatal_error is not None:
                raise ValueError("Production collector failed")
            health = safe_health(controller)
            if health != last_health:
                emit("collector_health", **health)
                last_health = health
            if args.scenario == "esp" and child is None:
                if health["healthy"]:
                    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--esp-child",
                        "--approved-private-test-room",
                        "--run-dir", str(run_dir), "--game-pid", str(game.pid), "--expected-created", str(game.created_at),
                        "--deadline", str(deadline + 5)]
                    child = subprocess.Popen(command, cwd=run_dir, env=dict(os.environ), stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
                    def read_messages():
                        for line in child.stdout:
                            if len(line) <= 4096:
                                try:
                                    record = json.loads(line)
                                    if isinstance(record, dict) and record.get("kind") in {"esp_handle_open", "esp_read_ready", "esp_read_health"}:
                                        messages.put(record)
                                except (ValueError, TypeError):
                                    pass
                    threading.Thread(target=read_messages, daemon=True).start()
                    proof["esp_pid"] = child.pid
                    proof["esp_spawn_epoch"] = time.time()
                    ready_deadline = min(deadline - args.esp_seconds - 5, now + args.readiness_timeout)
                elif now - started > 20:
                    raise ValueError("Collector did not become healthy before ESP launch")
            if child is not None and proof["ready"] is None:
                while proof["ready"] is None:
                    try:
                        record = messages.get_nowait()
                    except queue.Empty:
                        break
                    if record.get("kind") == "esp_handle_open":
                        if proof["handle_open"] is not None:
                            raise ValueError("Duplicate original ESP handle opening")
                        proof["handle_open"] = validate_handle_open(record, child.pid, game.pid, game.created_at, t0, time.time())
                        emit("esp_handle_open_observed", timestamp_ms=measured_timings(t0,
                            record["observed_at_epoch"], record["observed_at_epoch"])[0])
                    elif record.get("kind") == "esp_read_ready":
                        if proof["handle_open"] is None:
                            raise ValueError("Read readiness arrived without an actual handle opening")
                        proof["ready"] = validate_ready(record, child.pid, game.pid, t0, time.time())
                        if proof["ready"]["observed_at_epoch"] < proof["handle_open"]["observed_at_epoch"]:
                            raise ValueError("Read readiness precedes actual handle opening")
                        active_until = time.monotonic() + args.esp_seconds
                        emit("esp_verified_on", timestamp_ms=measured_timings(t0,
                            proof["handle_open"]["observed_at_epoch"], record["observed_at_epoch"])[0],
                            first_read_ready_ms=round((record["observed_at_epoch"] - t0) * 1000),
                            local_player_count=record["local_player_count"], remote_player_count=record["remote_player_count"])
                    else:
                        raise ValueError("Read heartbeat arrived before original ESP readiness")
                if proof["ready"] is None and (child.poll() is not None or now >= ready_deadline):
                    raise ValueError("Actual ESP camera/local-player/overlay readiness was not established")
            if child is not None and proof["ready"] is not None and proof["esp_exit_epoch"] is None:
                if not health["healthy"]:
                    raise ValueError("Collector became insufficient during actual ESP interval")
                while True:
                    try:
                        heartbeat = messages.get_nowait()
                    except queue.Empty:
                        break
                    if heartbeat.get("kind") != "esp_read_health":
                        raise ValueError("Duplicate original ESP lifecycle handshake")
                    if heartbeat.get("kind") == "esp_read_health" and heartbeat.get("fresh") is True:
                        facts = validate_ready(dict(heartbeat, kind="esp_read_ready"), child.pid, game.pid, t0, time.time())
                        gap = max(0., facts["observed_at_epoch"] - proof.get("last_fresh_epoch", proof["ready"]["observed_at_epoch"]))
                        proof["maximum_heartbeat_gap_seconds"] = max(proof["maximum_heartbeat_gap_seconds"], gap)
                        proof["last_fresh_epoch"] = facts["observed_at_epoch"]
                        proof["fresh_read_heartbeat_count"] += 1
                if (time.time() - proof.get("last_fresh_epoch", proof["ready"]["observed_at_epoch"]) > 2
                        or proof["maximum_heartbeat_gap_seconds"] > 2):
                    raise ValueError("Actual ESP stopped producing fresh camera/local-player frames")
                if child.poll() is not None:
                    raise ValueError("ESP exited before the bounded active interval")
                if now >= active_until:
                    exited, forced = stop_own_child(child, run_dir / "esp-stop")
                    proof.update(esp_exit_epoch=exited, forced_termination=forced, esp_exit_code=child.returncode)
                    if forced or child.returncode != 0:
                        raise ValueError("Original ESP did not stop gracefully")
                    if proof["fresh_read_heartbeat_count"] < 2:
                        raise ValueError("Continuous actual read coverage was not established")
                    emit("esp_verified_off", timestamp_ms=lifecycle_timings(proof)[2])
            time.sleep(0.1)
        if not safe_health(controller)["healthy"]:
            raise ValueError("Collector observation was insufficient at completion")
        if args.scenario == "esp" and (proof["ready"] is None or proof["esp_exit_epoch"] is None):
            raise ValueError("The actual bounded ESP interval was not completed")
        exact_game_provider(GAME_EXE)
        stage = "close"
        controller.close()
        stage = "validate_capture"
        manifest, matching = validate_capture(session_dir, args.session_id, args.player_id, args.scenario,
                                              child.pid if child else None, game.pid, game.created_at)
        proof.update(status="validated", attributable_esp_event_count=matching,
                     original_manifest_sha256=hashlib.sha256((session_dir / "manifest.json").read_bytes()).hexdigest())
        stage = "annotate_completed"
        annotate_completed(session_dir, manifest, proof)
        proof["status"] = "completed"
        write_json(run_dir / "lifecycle-proof.json", proof)
        stage = "export_session"
        destination = export_session(session_dir, Path(args.output_root))
        replay_path = destination / "manifest.json"
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        # Export only the controlled scalar annotation, never the local proof/raw.
        replay["source"]["realgame_validation"] = json.loads(
            (session_dir / "manifest.json").read_text(encoding="utf-8"))["test_metadata"]["timing_provenance"]
        temporary = destination / (".manifest-realgame-" + uuid4().hex + ".tmp")
        write_json(temporary, replay)
        temporary.replace(replay_path)
        success = True
        emit("replay_exported", session_id=args.session_id, label="CHEAT" if args.scenario == "esp" else "NORMAL",
             event_count=manifest["event_count"], attributable_esp_event_count=matching, output=str(destination), raw_uploaded=False)
        return 0
    except Exception as error:
        controller.mark_failed(type(error).__name__)
        proof.update(status="failed", failure_type=type(error).__name__, failure_stage=stage)
        emit("capture_failed", error_type=type(error).__name__, failure_stage=stage,
             reason=str(error) if isinstance(error, ValueError) else "Local capture failed")
        return 2
    finally:
        if child is not None and child.poll() is None:
            marker = run_dir / "esp-stop"
            if not marker.exists():
                exited, forced = stop_own_child(child, marker)
                proof.update(esp_exit_epoch=exited, forced_termination=forced, esp_exit_code=child.returncode)
            else:
                child.kill()
                child.wait(timeout=6)
        controller.close()
        if not success and not (run_dir / "lifecycle-proof.json").exists():
            write_json(run_dir / "lifecycle-proof.json", proof)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--scenario", choices=("normal", "esp"), default="normal")
    result.add_argument("--approved-private-test-room", action="store_true",
        help="acknowledge explicit authorization for the identified private game test room")
    result.add_argument("--game-pid", type=int, required=True)
    result.add_argument("--duration", type=float, default=120)
    result.add_argument("--esp-seconds", type=float, default=60)
    result.add_argument("--readiness-timeout", type=float, default=30)
    result.add_argument("--player-id", default="local_realgame")
    result.add_argument("--output-root", type=Path, default=REPO / "ReplayAnalyzer/replay-data/esp")
    result.add_argument("--session-id", default=None)
    result.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--esp-child", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--expected-created", type=float, help=argparse.SUPPRESS)
    result.add_argument("--deadline", type=float, help=argparse.SUPPRESS)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not args.approved_private_test_room:
        raise ValueError("Explicit authorization for an identified private test room must be acknowledged")
    if args.esp_child:
        return esp_child(args)
    if args.worker:
        return capture_worker(args)
    if not 5 <= args.duration <= 3600 or not 1 <= args.readiness_timeout <= 120:
        raise ValueError("Duration/readiness timeout must be bounded")
    if args.scenario == "esp" and not 5 <= args.esp_seconds <= args.duration - 30:
        raise ValueError("ESP interval must leave room for preflight/readiness and shutdown")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", args.player_id):
        raise ValueError("Player identifier is invalid")
    game = checked_call(platform_preflight, args.game_pid)
    if hashlib.sha256(ESP_SOURCE.read_bytes()).hexdigest() != ESP_SHA256:
        raise ValueError("The supplied ESP source fingerprint changed")
    session = args.session_id or (args.scenario + "_realgame_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:12])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", session):
        raise ValueError("Session identifier is invalid")
    run_dir = ESP_ROOT / "data/realgame_capture" / session
    run_dir.mkdir(parents=True, exist_ok=False)
    if (args.output_root / session).exists():
        raise ValueError("Replay destination already exists")
    environment = isolated_environment(run_dir, os.environ.get("SystemRoot", r"C:\Windows"), Path(sys.executable))
    config = {"game_executable": GAME_EXE, "poll_interval_seconds": 0.5, "overlay_scan_interval_seconds": 2,
        "database_path": str(run_dir / "anti_esp.sqlite3"), "event_window_seconds": 900,
        "overlay": {"enabled": True, "minimum_overlap_ratio": 0.55, "cooldown_seconds": 5},
        "module_monitor": {"enabled": True, "scan_interval_seconds": 5, "verify_signatures": True},
        "handle_monitor": {"enabled": True, "scan_interval_seconds": 2, "cooldown_seconds": 5},
        "telemetry": {"enabled": True, "root": str(run_dir / "sessions"), "session_id": session,
                      "player_id": args.player_id, "scenario": args.scenario},
        "identity": {"enabled": False}, "response": {"mode": "observe"}}
    write_json(run_dir / "config.json", config)
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker", "--scenario", args.scenario,
        "--approved-private-test-room",
        "--game-pid", str(game.pid), "--expected-created", str(game.created_at), "--run-dir", str(run_dir),
        "--session-id", session, "--player-id", args.player_id, "--duration", str(args.duration),
        "--esp-seconds", str(args.esp_seconds), "--readiness-timeout", str(args.readiness_timeout),
        "--output-root", str(args.output_root.resolve())]
    emit("capture_prepared", session_id=session, scenario=args.scenario, local_artifacts=str(run_dir), central_telemetry="off")
    child = checked_call(subprocess.Popen, command, cwd=run_dir, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    job = checked_call(own_process_job, child)
    try:
        return child.wait(timeout=args.duration + 60)
    except BaseException:
        # The worker's own child is independently bounded by its absolute deadline.
        child.kill()
        child.wait(timeout=6)
        raise
    finally:
        import win32api
        win32api.CloseHandle(job)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as failure:
        emit("capture_rejected", error_type=type(failure).__name__,
             failure_stage=getattr(failure, "capture_failure_stage", "main"))
        raise SystemExit(2)
