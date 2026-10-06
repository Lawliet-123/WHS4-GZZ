"""Bounded observation of one already-running game through the production Launcher.

This command does not install Sysmon, prepare UE4SS, launch the game, or generate
detection events. Run it in an elevated console after the scoped test server and
Sysmon are ready. Every collector still executes its production entry point.
Success verifies Launcher lifecycle and ESP observation coverage, not the
functional measurement or detection quality of all four modules.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import replace
from hashlib import sha256
import importlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GAME_EXE = "PenguinHotel-Win64-Shipping.exe"
MODULE_NAMES = ("module_integrity", "external_access", "esp", "hide_anywhere")
ENVIRONMENT_KEYS = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC"}
SUMMARY_SCHEMA = "meccha-realgame-launcher-e2e-1"
ESP_SENSORS = {"collector", "game", "privilege", "sysmon", "overlay", "modules", "handles"}


class SafetyError(ValueError):
    """A fixed, safe-to-display validation message without supplied values."""


def write_json(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_setup(path: Path) -> dict:
    """Accept only the disposable credentials produced by realgame_server.py."""
    if path.stat().st_size > 64 * 1024:
        raise SafetyError("test setup is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SafetyError("test setup must be an object")
    run_id, port = value.get("run_id"), value.get("port")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{12}", run_id):
        raise SafetyError("test setup has an invalid disposable run ID")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise SafetyError("test setup has an invalid port")
    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str):
        raise SafetyError("test setup has no loopback endpoint")
    parsed = urlsplit(endpoint)
    if (endpoint != f"http://127.0.0.1:{port}" or parsed.hostname != "127.0.0.1"
            or parsed.port != port):
        raise SafetyError("test setup endpoint must be the exact IPv4 loopback origin")
    if (value.get("result") != "READY" or value.get("tokens_are_disposable_test_literals") is not True
            or value.get("empty_start_verified") is not True or value.get("seeded_events") != 0):
        raise SafetyError("test setup does not describe an empty disposable real-game server")
    tokens = value.get("test_tokens")
    expected = {name: f"synthetic-browser-{kind}-{run_id}" for name, kind in (
        ("GZZ_TELEMETRY_TOKEN", "receiver"), ("MECCHA_HEARTBEAT_TOKEN", "heartbeat"),
        ("GZZ_DASHBOARD_TOKEN", "dashboard"))}
    if not isinstance(tokens, dict) or tokens != expected:
        raise SafetyError("test setup credentials do not match its disposable run ID")
    # Do not carry server paths or dashboard credentials into the Launcher.
    return {"endpoint": endpoint, "port": port, "run_id": run_id,
            "detection_token": tokens["GZZ_TELEMETRY_TOKEN"],
            "heartbeat_token": tokens["MECCHA_HEARTBEAT_TOKEN"]}


def base_environment(source=None) -> dict[str, str]:
    source = os.environ if source is None else source
    return {key: source[key] for key in source if key.upper() in ENVIRONMENT_KEYS}


def isolated_environment(output: Path, setup: dict, source=None) -> dict[str, str]:
    environment = base_environment(source)
    environment.update({
        "TEMP": str(output / "tmp"), "TMP": str(output / "tmp"),
        # ESP constructs an installation-secret path even with identity disabled.
        # Give Windows libraries an owned profile rather than inheriting a live one.
        "USERPROFILE": str(output / "profile"),
        "LOCALAPPDATA": str(output / "profile" / "localappdata"),
        "APPDATA": str(output / "profile" / "appdata"),
        "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "AC_LAUNCHER_LOG_DIR": str(output / "launcher"),
        "GZZ_TELEMETRY_URL": setup["endpoint"], "GZZ_TELEMETRY_TOKEN": setup["detection_token"],
        "GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK": "true", "GZZ_TELEMETRY_USE_ENVIRONMENT_PROXY": "false",
        "MECCHA_TELEMETRY_HEARTBEAT_URL": setup["endpoint"] + "/api/heartbeat",
        "MECCHA_HEARTBEAT_TOKEN": setup["heartbeat_token"],
    })
    return environment


def windows_preflight(game_pid: int) -> dict:
    """Query only the game instance and Sysmon readiness, never event contents."""
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise SafetyError("an elevated Windows console is required")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$games = @(Get-Process -Name 'PenguinHotel-Win64-Shipping' -ErrorAction SilentlyContinue | ForEach-Object {
    [pscustomobject]@{pid=$_.Id; create_time=$_.StartTime.ToUniversalTime().ToFileTimeUtc(); image=$_.Path}
})
$services = @(Get-Service -Name 'Sysmon','Sysmon64' -ErrorAction SilentlyContinue | Where-Object Status -eq 'Running')
$channel = Get-WinEvent -ListLog 'Microsoft-Windows-Sysmon/Operational' -ErrorAction SilentlyContinue
[pscustomobject]@{games=$games; sysmon_running=($services.Count -gt 0); channel_enabled=($channel.IsEnabled -eq $true)} | ConvertTo-Json -Depth 4 -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=base_environment(), capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)
    if result.returncode:
        raise SafetyError("game/Sysmon preflight query failed")
    try:
        state = json.loads(result.stdout.lstrip("\ufeff"))
    except (ValueError, TypeError):
        raise SafetyError("game/Sysmon preflight query returned invalid data") from None
    games = state.get("games", [])
    if len(games) != 1 or games[0].get("pid") != game_pid:
        raise SafetyError("exactly one game instance with the requested PID must be running")
    game = games[0]
    if (type(game.get("create_time")) is not int or game["create_time"] <= 0
            or not isinstance(game.get("image"), str) or Path(game["image"]).name.casefold() != GAME_EXE.casefold()):
        raise SafetyError("the game process identity could not be verified")
    if state.get("sysmon_running") is not True or state.get("channel_enabled") is not True:
        raise SafetyError("running Sysmon with its enabled Operational channel is required")
    return {"pid": game_pid, "create_time": game["create_time"], "image": game["image"],
            "elevated": True, "sysmon_running": True, "channel_enabled": True}


def selected_modules(module_catalog, output: Path) -> list:
    by_name = {module.name: module for module in module_catalog}
    if any(name not in by_name for name in MODULE_NAMES):
        raise SafetyError("production Launcher is missing a scoped collector")
    selected = []
    for name in MODULE_NAMES:
        original = by_name[name]
        argv = list(original.argv)
        if name in ("module_integrity", "external_access"):
            argv[argv.index("--output") + 1] = str(output / "localguard" / f"{name}.jsonl")
            session_dir = output / "localguard"
        elif name == "esp":
            argv += ["--config", str(output / "esp" / "config.json"), "--scenario", "normal"]
            session_dir = output / "esp" / "sessions"
        else:
            argv[argv.index("--out") + 1] = str(output / "hide")
            argv += ["--play-label", "NORMAL", "--sysmon"]
            session_dir = output / "hide"
        # Copies preserve production modes, entry points and cleanup contracts.
        selected.append(replace(original, argv=argv, session_log_dir=str(session_dir),
                                env=dict(original.env), optional_paths=list(original.optional_paths)))
    return selected


def prepare_esp_config(output: Path) -> None:
    config = json.loads((REPO / "client/detectors/esp/config.example.json").read_text(encoding="utf-8"))
    config["database_path"] = str(output / "esp" / "anti_esp.sqlite3")
    config["telemetry"]["root"] = str(output / "esp" / "sessions")
    config["identity"]["enabled"] = False
    config["response"] = {"mode": "observe"}
    write_json(output / "esp" / "config.json", config)


def observation_result(manifest: Path, *, session: str, player: str, game: dict, esp_pid=None) -> dict:
    """Read only coverage metadata from this newly owned ESP session."""
    if not manifest.exists():
        return {"coverage": "INSUFFICIENT", "reason": "ESP manifest is missing"}
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"coverage": "INSUFFICIENT", "reason": "ESP manifest could not be read"}
    if not isinstance(document, dict):
        return {"coverage": "INSUFFICIENT", "reason": "ESP manifest is invalid"}
    summary = document.get("observation_summary")
    if not isinstance(summary, dict):
        return {"coverage": "INSUFFICIENT", "reason": "ESP observation summary is missing"}
    producer = document.get("producer")
    producer = producer if isinstance(producer, dict) else {}
    # Match the production Windows API conversion exactly. int/int division
    # rounds differently from int/float for contemporary FILETIME values.
    created_at = game["create_time"] / 10_000_000.0 - 11_644_473_600.0
    instance = sha256(f"{session}\0{game['pid']}\0{created_at:.9f}".encode("utf-8")).hexdigest()
    profile_valid = (document.get("schema_version") == "meccha.telemetry-session.v1"
                     and document.get("session_id") == session and document.get("player_id") == player
                     and document.get("game_executable") == GAME_EXE and document.get("status") == "completed"
                     and document.get("failure_reason") is None and producer.get("name") == "meccha-esp-localguard"
                     and producer.get("version") == "0.2.0" and type(esp_pid) is int and esp_pid > 0
                     and producer.get("pid") == esp_pid
                     and producer.get("observation_contract") == "meccha.esp-observation.v1"
                     and summary.get("schema_version") == "meccha.esp-observation.v1"
                     and summary.get("session_id") == session and summary.get("player_id") == player
                     and summary.get("game_instance_sha256") == instance)
    integer_keys = ("poll_count", "healthy_poll_count", "insufficient_poll_count", "first_poll_ms", "last_poll_ms",
                    "timestamp_regression_count", "game_instance_missing_poll_count")
    clean = {key: summary.get(key) if type(summary.get(key)) is int and summary[key] >= 0 else None
             for key in integer_keys}
    confidence_keys = ("minimum_observation_confidence", "min_observation_confidence", "last_observation_confidence")
    clean.update({key: summary.get(key) if type(summary.get(key)) in (int, float)
                  and math.isfinite(summary[key]) and 0 <= summary[key] <= 100 else None
                  for key in confidence_keys})
    last_status = summary.get("last_status")
    clean["last_status"] = last_status if isinstance(last_status, str) and last_status in {"LOW", "REVIEW", "HIGH", "CRITICAL", "INSUFFICIENT"} else "invalid"
    clean["game_instance_changed"] = summary.get("game_instance_changed") if type(summary.get("game_instance_changed")) is bool else None
    clean["game_instance_matches_requested_target"] = summary.get("game_instance_sha256") == instance
    sensors = summary.get("required_sensors")
    sensors = sensors if isinstance(sensors, dict) else {}
    clean["required_sensors"] = {}
    sensors_valid = set(sensors) == ESP_SENSORS
    for name in sorted(ESP_SENSORS):
        row = sensors.get(name)
        row = row if isinstance(row, dict) else {}
        counts = {key: row.get(key) if type(row.get(key)) is int and row[key] >= 0 else None
                  for key in ("poll_count", "online_poll_count", "observed_count", "online_observed_count")}
        status = row.get("last_status")
        counts["last_status"] = status if isinstance(status, str) and status in {"online", "waiting", "unavailable", "error", "disabled"} else "invalid"
        clean["required_sensors"][name] = counts
        sensors_valid = sensors_valid and (all(type(counts[key]) is int for key in
            ("poll_count", "online_poll_count", "observed_count", "online_observed_count"))
            and type(clean["poll_count"]) is int and counts["poll_count"] == clean["poll_count"]
            and counts["online_poll_count"] == clean["poll_count"] and type(counts["online_observed_count"]) is int
            and 0 < counts["online_observed_count"] <= counts["observed_count"] <= clean["poll_count"]
            and counts["last_status"] == "online")
    healthy = (profile_valid and all(clean[key] is not None for key in integer_keys + confidence_keys)
               and clean["poll_count"] > 0 and clean["healthy_poll_count"] == clean["poll_count"]
               and clean["insufficient_poll_count"] == 0 and clean["last_status"] in {"LOW", "REVIEW", "HIGH", "CRITICAL"}
               and clean["first_poll_ms"] <= clean["last_poll_ms"] and clean["minimum_observation_confidence"] == 60.0
               and clean["min_observation_confidence"] >= clean["minimum_observation_confidence"]
               and clean["last_observation_confidence"] >= clean["minimum_observation_confidence"]
               and clean["game_instance_changed"] is False and clean["game_instance_missing_poll_count"] == 0
               and clean["timestamp_regression_count"] == 0 and sensors_valid)
    return {"coverage": "HEALTHY" if healthy else "INSUFFICIENT", "profile_identity_valid": profile_valid,
            "observation_summary": clean,
            "manifest_status": document.get("status") if isinstance(document.get("status"), str)
            and document["status"] in {"completed", "running", "failed"} else "invalid"}


def lifecycle_complete(proof: dict) -> bool:
    cleanup = proof.get("cleanup", {})
    terminal = proof.get("terminal_modules", [])
    clean_stop = ({row["name"] for row in terminal} == set(MODULE_NAMES)
                  and all(row["status"] == "STOPPED" and row["last_code"] == 0 for row in terminal)
                  and set(cleanup.get("graceful", [])) == set(MODULE_NAMES)
                  and not any(cleanup.get(key) for key in ("forced", "unsignaled", "defaulted")))
    return (proof.get("launcher_exit_code") == 0 and not proof.get("collectors_remaining")
            and proof.get("game_after", {}).get("same_instance") is True
            and proof.get("all_four_running_snapshot_count", 0) > 0 and clean_stop
            and not proof.get("registry_capture_failed") and proof.get("stop_reason") == "duration_elapsed"
            and proof.get("esp", {}).get("coverage") == "HEALTHY")


def run_launcher(args, setup: dict, output: Path, before: dict) -> int:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    launcher = importlib.import_module("main")
    registry = launcher.registry
    manager_class = launcher.ProcessManager
    selected = selected_modules(launcher.MODULES, output)
    deadline = time.monotonic() + args.duration
    proof = {"schema_version": SUMMARY_SCHEMA, "session_id": args.session, "player_id": args.player,
             "run_id": setup["run_id"], "duration_seconds": args.duration, "game_before": before,
             "modules": list(MODULE_NAMES), "synthetic_detections": False,
             "result_scope": "launcher_lifecycle_and_esp_observation",
             "all_detector_functional_e2e": "NOT_ASSESSED",
             "coverage_gaps": ["UE4SS preparation and existing UE4SS log reads deliberately skipped",
                               "No injected-DLL or unregistered-reader whitelist challenge performed"],
             "health_snapshot_count": 0, "all_four_running_snapshot_count": 0}
    captured = {}

    def capture(manager, phase):
        document = registry.load()
        compact = {key: document.get(key) for key in (
            "session_id", "launcher_pid", "launcher_create_time", "stopping", "entries", "modules")}
        # Registry contains production argv/path metadata but never inherited tokens.
        captured.update(compact)
        write_json(output / "registry-before-stop.json", compact)
        snapshot = [{key: row.get(key) for key in (
            "name", "status", "mode", "runs", "restarts", "started_by", "last_code", "uptime_s")}
                    for row in manager.snapshot()]
        proof["last_health"] = {"phase": phase, "modules": snapshot}
        proof["health_snapshot_count"] += 1
        entries = document.get("entries") or {}
        states = manager.states
        all_running = (set(entries) == set(MODULE_NAMES) and {row["name"] for row in snapshot} == set(MODULE_NAMES)
                       and all(row["status"] == "RUNNING" for row in snapshot))
        for name in MODULE_NAMES:
            entry = entries.get(name) or {}
            all_running = all_running and (type(entry.get("pid")) is int and entry["pid"] > 0
                and type(entry.get("create_time")) is int and entry["create_time"] > 0
                and states[name].pid == entry["pid"]
                and registry.create_time(entry["pid"]) == entry["create_time"]
                and registry.is_alive(entry["pid"], entry["create_time"]))
        proof["all_four_running_snapshot_count"] += int(all_running)
        with (output / "health.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(proof["last_health"]) + "\n")

    class BoundedManager(manager_class):
        def poll(self):
            result = super().poll()
            capture(self, "running")
            if (registry.create_time(args.game_pid) != before["create_time"]
                    or launcher.game_launcher.find_game_pid() != args.game_pid):
                proof["stop_reason"] = "game_identity_changed"
                raise KeyboardInterrupt
            if time.monotonic() >= deadline:
                proof["stop_reason"] = "duration_elapsed"
                raise KeyboardInterrupt
            return result

        def stop_all(self, *positional, **keywords):
            try:
                capture(self, "before_stop")
            except Exception:
                proof["registry_capture_failed"] = True
            result = super().stop_all(*positional, **keywords)
            proof["cleanup"] = result
            proof["terminal_modules"] = [{key: row.get(key) for key in ("name", "status", "last_code")}
                                         for row in self.snapshot()]
            return result

    old_modules, old_prepare, old_manager = launcher.MODULES, launcher._prepare_ue4ss, launcher.ProcessManager
    launcher.MODULES, launcher._prepare_ue4ss, launcher.ProcessManager = selected, lambda *_: None, BoundedManager
    try:
        code = launcher.main(["--only", ",".join(MODULE_NAMES), "--session", args.session,
                              "--player", args.player, "--no-launch-game", "--no-game-path-prompt",
                              "--wait-game", "1", "--status-every", "5"])
        proof["launcher_exit_code"] = code
    finally:
        launcher.MODULES, launcher._prepare_ue4ss, launcher.ProcessManager = old_modules, old_prepare, old_manager
        current = registry.create_time(args.game_pid)
        proof["game_after"] = {"pid": args.game_pid, "create_time": current,
                               "same_instance": current == before["create_time"]}
        proof["collectors_remaining"] = [name for name, entry in (captured.get("entries") or {}).items()
                                         if registry.is_alive(entry.get("pid"), entry.get("create_time", 0))]
        proof["esp"] = observation_result(output / "esp" / "sessions" / args.session / "manifest.json",
            session=args.session, player=args.player, game=before,
            esp_pid=((captured.get("entries") or {}).get("esp") or {}).get("pid"))
        proof["result"] = "LIFECYCLE_VERIFIED" if lifecycle_complete(proof) else "INCOMPLETE"
        write_json(output / "summary.json", proof)
        print(json.dumps({"result": proof["result"], "summary_path": str(output / "summary.json"),
                          "result_scope": proof["result_scope"],
                          "all_detector_functional_e2e": proof["all_detector_functional_e2e"],
                          "esp_coverage": proof["esp"]["coverage"], "collectors_remaining": proof["collectors_remaining"]}))
    return 0 if proof["result"] == "LIFECYCLE_VERIFIED" else 3


def main(argv=None, *, preflight=windows_preflight, launcher_runner=run_launcher) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new, nonexisting owned runtime directory")
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--game-pid", type=int, required=True)
    parser.add_argument("--player", default="local_e2e")
    parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    try:
        if not math.isfinite(args.duration) or not 1 <= args.duration <= 240:
            raise SafetyError("duration must be between 1 and 240 seconds")
        if args.game_pid <= 0 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.player):
            raise SafetyError("invalid game PID or player ID")
        if not re.fullmatch(r"normal_[A-Za-z0-9_-]{1,53}", args.session):
            raise SafetyError("session must start normal_ and contain at most 60 safe characters")
        output = args.output.expanduser().resolve()
        if output.exists() or not output.parent.is_dir():
            raise SafetyError("output must be a new directory inside an existing parent")
        setup = read_setup(args.setup.resolve())
        before = preflight(args.game_pid)  # No Launcher imports or runtime writes before this succeeds.
        environment = isolated_environment(output, setup)
        output.mkdir(exist_ok=False)
        for subdirectory in ("tmp", "launcher", "localguard", "esp", "hide", "profile",
                             "profile/localappdata", "profile/appdata"):
            (output / subdirectory).mkdir()
        prepare_esp_config(output)
        os.environ.clear()
        os.environ.update(environment)
        return launcher_runner(args, setup, output, before)
    except SafetyError as error:
        print(f"realgame preflight/run failed: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Only deliberate validation messages are displayed; never dump setup/environment.
        print(f"realgame preflight/run failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
