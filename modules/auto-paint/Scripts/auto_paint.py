#!/usr/bin/env python3
"""MECCA CHAMELEON auto-paint-only launcher with diagnostic logging."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import logging
import os
import platform
import queue
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "Meccha Auto Paint"
APP_VERSION = "1.4.1-v7.1-continuous-coverage"
DEFAULT_GAME_PROCESS = "PenguinHotel-Win64-Shipping.exe"
CREATE_NO_WINDOW = 0x08000000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def resource_base() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BASE_DIR = resource_base()
NATIVE_DIR = BASE_DIR / "native"
MESH_DIR = BASE_DIR / "mesh-profiles"
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR)))


def resolve_data_dir() -> Path:
    candidates = (
        LOCAL_APPDATA / "MecchaCamouflage" / "auto-paint-only",
        BASE_DIR / "auto-paint-data",
        Path(tempfile.gettempdir()) / "MecchaCamouflage" / "auto-paint-only",
    )
    for candidate in candidates:
        probe = candidate / f".write-test-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    raise RuntimeError("진단 로그를 저장할 수 있는 폴더를 찾지 못했습니다.")


DATA_DIR = resolve_data_dir()
RUNTIME_DIR = DATA_DIR / "runtime"
LOG_DIR = DATA_DIR / "logs"
DIAGNOSTIC_DIR = DATA_DIR / "diagnostics"
BRIDGE_DEBUG_DIR = LOCAL_APPDATA / "MecchaCamouflage" / "runtime"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setup_logger() -> tuple[logging.Logger, Path, float]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"auto-paint-{stamp}-pid{os.getpid()}.log"
    logger = logging.getLogger("meccha_auto_paint")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger, path, started


LOGGER, SESSION_LOG, SESSION_STARTED = setup_logger()


def log_json(label: str, value, level=logging.DEBUG) -> None:
    LOGGER.log(level, "%s:\n%s", label, safe_json(value))


def install_exception_logging() -> None:
    def unhandled(exc_type, exc_value, exc_tb):
        LOGGER.critical(
            "Unhandled main-thread exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = unhandled

    if hasattr(threading, "excepthook"):
        def thread_unhandled(args):
            LOGGER.critical(
                "Unhandled worker-thread exception in %s",
                args.thread.name if args.thread else "unknown",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = thread_unhandled


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def configure_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wt.BOOL
    kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wt.BOOL
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.LPWSTR,
        ctypes.POINTER(wt.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wt.BOOL
    return kernel32


def find_game_process(process_name: str) -> tuple[int | None, str | None]:
    try:
        kernel32 = configure_kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid:
            error = ctypes.get_last_error()
            LOGGER.error("CreateToolhelp32Snapshot failed: win32=%s", error)
            return None, None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                LOGGER.error("Process32FirstW failed: win32=%s", ctypes.get_last_error())
                return None, None
            wanted = process_name.casefold()
            while True:
                actual = entry.szExeFile
                if actual.casefold() == wanted:
                    return int(entry.th32ProcessID), actual
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        LOGGER.exception("Game process enumeration failed")
    return None, None


def query_process_details(pid: int) -> tuple[str | None, int | None]:
    kernel32 = configure_kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        LOGGER.error("OpenProcess(%s) failed: win32=%s", pid, ctypes.get_last_error())
        return None, None
    try:
        capacity = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        executable = None
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            executable = buffer.value
        else:
            LOGGER.error("QueryFullProcessImageNameW failed: win32=%s", ctypes.get_last_error())

        created = wt.FILETIME()
        exited = wt.FILETIME()
        kernel = wt.FILETIME()
        user = wt.FILETIME()
        creation_filetime = None
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            creation_filetime = (created.dwHighDateTime << 32) | created.dwLowDateTime
        else:
            LOGGER.error("GetProcessTimes failed: win32=%s", ctypes.get_last_error())
        return executable, creation_filetime
    finally:
        kernel32.CloseHandle(handle)


def build_start_block(pid: int, guid_bytes: bytes, token: bytes, bridge_hash: bytes) -> bytes:
    block = bytearray(128)
    struct.pack_into("<I", block, 0, 0x3153434D)
    struct.pack_into("<I", block, 4, 128)
    struct.pack_into("<I", block, 8, 1)
    struct.pack_into("<I", block, 12, pid & 0xFFFFFFFF)
    block[16:32] = guid_bytes
    block[32:64] = token
    block[64:96] = bridge_hash
    struct.pack_into("<I", block, 108, 1)
    return bytes(block)


def parse_injector_result(stdout: str):
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and value.get("event") == "result":
            return value
    return None


class BridgeSession:
    def __init__(self, port: int, instance_id: uuid.UUID, token: bytes, bridge_hash: str):
        self.port = port
        self.instance_id = instance_id
        self._token = token
        self.bridge_hash = bridge_hash
        self.last_error = ""

    @staticmethod
    def _read_line(sock: socket.socket, initial=b"") -> tuple[bytes, bytes]:
        data = initial
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 1024 * 1024:
                raise ValueError("bridge hello exceeded 1 MiB")
        if b"\n" in data:
            line, _, rest = data.partition(b"\n")
            return line, rest
        return data, b""

    def request(self, payload, timeout=30):
        self.last_error = ""
        request_obj = payload if isinstance(payload, dict) else None
        if request_obj is None:
            request_text = payload
            try:
                request_obj = json.loads(payload)
            except Exception:
                request_obj = {"type": "unknown"}
        else:
            request_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        request_type = request_obj.get("type", "unknown") if isinstance(request_obj, dict) else "unknown"
        LOGGER.debug("Bridge request begin: type=%s port=%s timeout=%s", request_type, self.port, timeout)
        try:
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        except OSError as error:
            self.last_error = f"Bridge connection failed: {type(error).__name__}: {error}"
            LOGGER.error(self.last_error)
            return None
        try:
            hello = {
                "type": "hello",
                "bootstrap_protocol": 1,
                "instance_id": self.instance_id.hex,
                "token": self._token.hex(),
            }
            sock.sendall((json.dumps(hello, separators=(",", ":")) + "\n").encode("utf-8"))
            hello_line, buffered = self._read_line(sock)
            if not hello_line:
                self.last_error = "Bridge returned an empty authentication response"
                LOGGER.error(self.last_error)
                return None
            hello_response = json.loads(hello_line.decode("utf-8", "replace"))
            LOGGER.debug(
                "Bridge hello: success=%s stage=%s protocol=%s hash=%s",
                hello_response.get("success"),
                hello_response.get("stage"),
                hello_response.get("metadata", {}).get("bootstrap_protocol"),
                hello_response.get("metadata", {}).get("bridge_hash"),
            )
            if not (hello_response.get("success") and hello_response.get("stage") == "hello"):
                self.last_error = f"Bridge authentication rejected: {safe_json(hello_response)}"
                LOGGER.error(self.last_error)
                return None

            sock.sendall((request_text.rstrip("\n") + "\n").encode("utf-8"))
            sock.settimeout(timeout)
            response = bytearray(buffered)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise ValueError("bridge response exceeded 8 MiB")
            if not response:
                self.last_error = "Bridge returned no command response"
                LOGGER.error(self.last_error)
                return None
            decoded = response.decode("utf-8", "replace").strip()
            result = json.loads(decoded)
            LOGGER.info(
                "Bridge response: type=%s success=%s stage=%s applied=%s failures=%s",
                request_type,
                result.get("success"),
                result.get("stage"),
                result.get("applied"),
                result.get("failures"),
            )
            return result
        except Exception as error:
            self.last_error = f"Bridge request failed: {type(error).__name__}: {error}"
            LOGGER.exception("%s (type=%s)", self.last_error, request_type)
            return None
        finally:
            sock.close()


def parse_color(hex_color: str) -> tuple[int, int, int]:
    try:
        if len(hex_color) == 7 and hex_color.startswith("#"):
            return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    except ValueError:
        pass
    return 255, 255, 255


V7_STAGE_GUIDANCE = {
    "profile_winding_untrusted": "메시 앞뒤 방향을 확실히 판정하지 못해 오도색을 차단했습니다. 진단 ZIP을 보내주세요.",
    "strict_capture_validation_failed": "배경 캡처가 정확하지 않아 중단했습니다. 캐릭터를 벽에 더 가깝게 두고 다시 시도하세요.",
    "capture_state_changed": "캡처 중 카메라나 캐릭터가 움직였습니다. 둘 다 고정한 뒤 다시 시도하세요.",
    "strict_direct_plan_failed": "화면에서 직접 확인된 배경색 표본이 부족합니다. 벽을 등지고 캐릭터 전신이 보이게 조정하세요.",
    "strict_uv_overlap_conflict": "같은 UV 위치에 서로 다른 색이 겹쳐 정확한 결과를 만들 수 없어 중단했습니다. 진단 ZIP을 보내주세요.",
    "screen_hittest_refine_failed": "실제 화면의 캐릭터 표면과 UV를 충분히 직접 연결하지 못했습니다. 캐릭터 전신이 보이도록 한 뒤 진단 ZIP을 보내주세요.",
    "screen_hittest_tuning_invalid": "화면 정밀 표본 설정이 올바르지 않습니다. 기본 v7 설정으로 복원해 주세요.",
    "screen_hittest_uv_island_lookup_failed": "화면 표본을 안전한 UV 영역에 충분히 연결하지 못했습니다. 진단 ZIP을 보내주세요.",
    "adaptive_uv_coverage_failed": "UV 표본 사이를 번짐 없이 연결할 수 없어 잘못 칠하는 대신 중단했습니다. 진단 ZIP을 보내주세요.",
    "capture_source_invalid": "지원하지 않는 캡처 소스입니다. v7 기본 화면색 모드를 사용해 주세요.",
}


def user_error_message(stage: str, fallback: str) -> str:
    return V7_STAGE_GUIDANCE.get(stage, fallback)


def build_paint_payload(pid: int, process_name: str) -> dict:
    fill_color = "#FFFFFF"
    r, g, b = parse_color(fill_color)
    return {
        "type": "paint_full_route",
        "native_apply_mode": "mesh_first_paint",
        "route": "f10_mesh_first_paint",
        "server_batch_rpc": "packed",
        "packed_route": "component",
        "preview_only": False,
        "unpreview_only": False,
        # V7 always keeps the projection/capture artifacts needed to diagnose a
        # mismatch that can only be reproduced in the live game.
        "research_artifacts": True,
        "process": {"pid": pid, "name": process_name},
        "tuning": {
            # V7 maps visible screen pixels back to exact runtime UVs with the
            # uncached game hit-test before the character is hidden and captured.
            "strict_accuracy": True,
            "visible_direct_only": True,
            "deduplicate_uv": True,
            "screen_hittest_refine": True,
            "screen_hittest_cell_px": 3,
            "screen_hittest_max_samples": 12000,
            "screen_hittest_min_samples": 128,
            "screen_hittest_max_projection_delta_px": 2.0,
            "min_direct_samples": 128,
            "max_projection_error_px": 1.0,
            "hide_all_character_meshes": True,
            "capture_source": "final_color_ldr",
            "capture_settle_ms": 64,
            "camera_stability_translation_cm": 5.0,
            "camera_stability_angle_deg": 1.5,
            "component_stability_translation_cm": 5.0,
            "component_stability_angle_deg": 2.0,
            "pose_stability_avg_cm": 2.0,
            "pose_stability_max_cm": 8.0,
            # The fixed diameter is only the minimum. V7.1 measures directional
            # neighbor gaps inside each UV island and expands each solid brush
            # just enough to remove the dotted/unpainted grid.
            "stroke_size_texels": 4.0,
            "coverage_step_texels": 4.0,
            "adaptive_uv_brush": True,
            "adaptive_uv_brush_max_radius_texels": 14.0,
            "adaptive_uv_brush_neighbor_scale": 0.58,
            "adaptive_uv_brush_min_coverage": 0.90,
            "side_source_max_uv": 0.08,
            "front_back_source_max_uv": 0.45,
            "auto_material": False,
            "auto_material_properties": False,
            "metallic": 0.0,
            "roughness": 1.0,
            # FinalColorLDR already contains lighting. An emissive mask prevents
            # the curved character from darkening the captured screen color again.
            "emissive": 1.0,
            "front_region_mode": "paint",
            "side_region_mode": "paint",
            "back_region_mode": "paint",
            "fill_color": fill_color,
            "fill_color_r": round(r / 255.0, 8),
            "fill_color_g": round(g / 255.0, 8),
            "fill_color_b": round(b / 255.0, 8),
            "fill_metallic": 1.0,
            "fill_roughness": 0.0,
            "fill_emissive": 0.0,
            "server_batch_limit": 50,
            "server_batch_delay_ms": 50,
        },
    }


class AutoPaintEngine:
    def __init__(self, game_process=DEFAULT_GAME_PROCESS):
        self.game_process = game_process
        self.session: BridgeSession | None = None
        self.instance_dir: Path | None = None
        self.progress_path: Path | None = None
        self._connect_lock = threading.Lock()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)

    def _native_manifest(self) -> dict:
        result = {}
        for name in ("runtime-bridge.dll", "runtime-injector.exe"):
            path = NATIVE_DIR / name
            item = {"path": str(path), "exists": path.exists()}
            if path.exists():
                item.update({"size": path.stat().st_size, "sha256": file_sha256(path)})
            result[name] = item
        profiles = []
        if MESH_DIR.exists():
            for path in sorted(MESH_DIR.glob("*.json")):
                profiles.append({"name": path.name, "size": path.stat().st_size, "sha256": file_sha256(path)})
        result["mesh_profiles"] = profiles
        return result

    def find_game(self) -> tuple[int | None, str | None]:
        pid, actual = find_game_process(self.game_process)
        LOGGER.info("Game lookup: requested=%s pid=%s actual=%s", self.game_process, pid, actual)
        return pid, actual

    def inject_bridge(self) -> str:
        bridge_source = NATIVE_DIR / "runtime-bridge.dll"
        injector_source = NATIVE_DIR / "runtime-injector.exe"
        log_json("Native resource manifest", self._native_manifest(), logging.INFO)
        if not bridge_source.is_file() or not injector_source.is_file():
            return f"Native files not found: {NATIVE_DIR}"
        if not list(MESH_DIR.glob("*.json")):
            return f"Mesh profiles not found: {MESH_DIR}"

        pid, actual_name = self.find_game()
        if pid is None:
            return f"Game process '{self.game_process}' not found"
        executable, creation_filetime = query_process_details(pid)
        LOGGER.info(
            "Game details: pid=%s name=%s executable=%s creation_filetime=%s",
            pid,
            actual_name,
            executable,
            creation_filetime,
        )
        if not executable:
            return "Could not read game executable path"
        if creation_filetime is None:
            return "Could not read game process creation time"

        instance_id = uuid.uuid4()
        token = os.urandom(32)
        bridge_hash_bytes = bytes.fromhex(file_sha256(bridge_source))
        bridge_hash = bridge_hash_bytes.hex()
        instance_dir = RUNTIME_DIR / f"i-{instance_id.hex}"
        instance_dir.mkdir(parents=True, exist_ok=False)
        bridge_target = instance_dir / f"meccha-direct-bridge-v1-{bridge_hash}-{instance_id.hex}.dll"
        injector_target = instance_dir / "runtime-injector.exe"
        shutil.copy2(bridge_source, bridge_target)
        shutil.copy2(injector_source, injector_target)
        profile_target = instance_dir / "mesh-profiles"
        profile_target.mkdir()
        for source in MESH_DIR.glob("*.json"):
            shutil.copy2(source, profile_target / source.name)

        progress_path = LOG_DIR / f"native-progress-{instance_id.hex}.json"
        progress_sidecar = Path(str(bridge_target) + ".progress.path")
        progress_sidecar.write_text(str(progress_path), encoding="utf-8")
        self.instance_dir = instance_dir
        self.progress_path = progress_path
        LOGGER.info("Injection staging: instance=%s dir=%s", instance_id.hex, instance_dir)
        LOGGER.info("Native progress sidecar: %s -> %s", progress_sidecar, progress_path)

        start_block = build_start_block(pid, instance_id.bytes, token, bridge_hash_bytes)
        command = [
            str(injector_target),
            "--direct",
            str(pid),
            str(creation_filetime),
            executable,
            str(bridge_target),
        ]
        LOGGER.info(
            "Injector command: executable=%s mode=--direct pid=%s game=%s bridge=%s",
            injector_target,
            pid,
            executable,
            bridge_target,
        )
        started = time.monotonic()
        injector_record = {
            "time_utc": utc_now(),
            "command": "--direct",
            "pid": pid,
            "game_process": actual_name,
            "game_executable": executable,
            "creation_filetime": creation_filetime,
            "bridge": str(bridge_target),
            "bridge_sha256": bridge_hash,
            "instance_id": instance_id.hex,
        }
        try:
            result = subprocess.run(
                command,
                input=start_block,
                capture_output=True,
                timeout=20,
                creationflags=CREATE_NO_WINDOW,
            )
            stdout = result.stdout.decode("utf-8", "replace")
            stderr = result.stderr.decode("utf-8", "replace")
            injector_record.update({
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            })
        except subprocess.TimeoutExpired as error:
            stdout = (error.stdout or b"").decode("utf-8", "replace")
            stderr = (error.stderr or b"").decode("utf-8", "replace")
            injector_record.update({
                "returncode": None,
                "stdout": stdout,
                "stderr": stderr,
                "error": "timeout",
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            })
            self._write_runtime_json("last-injector.json", injector_record)
            LOGGER.error("Injector timed out")
            return "Injector timed out"
        except Exception as error:
            injector_record.update({
                "returncode": None,
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc(),
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            })
            self._write_runtime_json("last-injector.json", injector_record)
            LOGGER.exception("Injector execution failed")
            return f"Injector execution failed: {type(error).__name__}: {error}"

        self._write_runtime_json("last-injector.json", injector_record)
        log_json("Injector result", injector_record, logging.INFO)
        parsed = parse_injector_result(stdout)
        if parsed is None:
            detail_lines = (stderr or stdout).strip().splitlines()
            detail = detail_lines[-1] if detail_lines else "no injector output"
            return f"Injector exited {result.returncode}: {detail}"
        if not parsed.get("success") or parsed.get("state") != "listening":
            return f"Injector failed: {parsed.get('detail')} (state={parsed.get('state')})"
        port = parsed.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            return f"Injector returned invalid port: {port}"

        self.session = BridgeSession(port, instance_id, token, bridge_hash)
        LOGGER.info("Bridge session created: port=%s instance=%s hash=%s", port, instance_id.hex, bridge_hash)
        return ""

    def ensure_bridge(self) -> str:
        with self._connect_lock:
            if self.session is not None:
                ping = self.session.request({"type": "ping"}, timeout=2)
                if ping and ping.get("success"):
                    return ""
                LOGGER.warning("Existing bridge session is unavailable; reinjecting")
                self.session = None
            error = self.inject_bridge()
            if error:
                LOGGER.error("Bridge injection failed: %s", error)
                return error
            for attempt in range(1, 21):
                ping = self.session.request({"type": "ping"}, timeout=2) if self.session else None
                if ping and ping.get("success"):
                    LOGGER.info("Bridge ready after ping attempt %s", attempt)
                    return ""
                time.sleep(0.25)
            error = self.session.last_error if self.session else "Bridge session missing"
            return f"Bridge did not become ready: {error}"

    def ping(self) -> bool:
        if self.session is None:
            return False
        response = self.session.request({"type": "ping"}, timeout=2)
        return bool(response and response.get("success"))

    def paint(self):
        error = self.ensure_bridge()
        if error:
            response = {"success": False, "stage": "bridge_setup_failed", "message": error}
            self._write_last_response(None, response)
            return response
        pid, actual_name = self.find_game()
        if pid is None:
            response = {"success": False, "stage": "game_not_found", "message": "Game process not found"}
            self._write_last_response(None, response)
            return response
        payload = build_paint_payload(pid, actual_name or self.game_process)
        log_json("Paint request", payload, logging.INFO)
        started = time.monotonic()
        response = self.session.request(payload, timeout=180) if self.session else None
        if response is None:
            response = {
                "success": False,
                "stage": "transport_error",
                "message": self.session.last_error if self.session else "Bridge session missing",
            }
        diagnostic = {
            "time_utc": utc_now(),
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "request": payload,
            "response": response,
        }
        self._write_runtime_json("last-response.json", diagnostic)
        log_json("Paint response", response, logging.INFO)
        return response

    def cancel(self):
        if self.session is None:
            return {"success": False, "stage": "transport_error", "message": "Bridge not connected"}
        response = self.session.request({"type": "cancel_paint"}, timeout=10)
        if response is None:
            response = {"success": False, "stage": "transport_error", "message": self.session.last_error}
        log_json("Cancel response", response, logging.INFO)
        return response

    def shutdown(self):
        if self.session is None:
            return None
        response = self.session.request({"type": "shutdown"}, timeout=5)
        log_json("Shutdown response", response, logging.INFO)
        self.session = None
        return response

    def _write_last_response(self, request, response):
        self._write_runtime_json(
            "last-response.json",
            {"time_utc": utc_now(), "request": request, "response": response},
        )

    @staticmethod
    def _write_runtime_json(name: str, value) -> None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            (RUNTIME_DIR / name).write_text(safe_json(value), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to write runtime diagnostic: %s", name)

    def diagnostic_manifest(self, reason: str) -> dict:
        pid, actual = find_game_process(self.game_process)
        executable = None
        creation = None
        if pid is not None:
            executable, creation = query_process_details(pid)
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "reason": reason,
            "created_utc": utc_now(),
            "session_started_utc": datetime.fromtimestamp(SESSION_STARTED, timezone.utc).isoformat(),
            "python": sys.version,
            "python_executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
            "platform": platform.platform(),
            "architecture": platform.architecture(),
            "is_admin": is_admin,
            "base_dir": str(BASE_DIR),
            "data_dir": str(DATA_DIR),
            "session_log": str(SESSION_LOG),
            "game": {
                "requested_name": self.game_process,
                "pid": pid,
                "actual_name": actual,
                "executable": executable,
                "creation_filetime": creation,
            },
            "bridge": {
                "connected": self.session is not None,
                "port": self.session.port if self.session else None,
                "instance_id": self.session.instance_id.hex if self.session else None,
                "bridge_hash": self.session.bridge_hash if self.session else None,
                "last_error": self.session.last_error if self.session else None,
                "progress_path": str(self.progress_path) if self.progress_path else None,
            },
            "resources": self._native_manifest(),
        }

    def create_diagnostic_bundle(self, reason="manual") -> Path:
        DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle = DIAGNOSTIC_DIR / f"auto-paint-diagnostics-{stamp}-{reason}.zip"
        manifest = self.diagnostic_manifest(reason)
        files: list[tuple[Path, str]] = []
        files.append((SESSION_LOG, f"logs/{SESSION_LOG.name}"))
        for name in ("last-injector.json", "last-response.json"):
            path = RUNTIME_DIR / name
            files.append((path, f"runtime/{name}"))
        if self.progress_path:
            files.append((self.progress_path, f"runtime/{self.progress_path.name}"))
        if self.instance_dir and self.instance_dir.exists():
            for path in self.instance_dir.glob("*.progress.*"):
                files.append((path, f"runtime/{path.name}"))
        if BRIDGE_DEBUG_DIR.exists():
            for path in sorted(BRIDGE_DEBUG_DIR.iterdir()):
                try:
                    if (
                        path.is_file()
                        and path.stat().st_mtime >= SESSION_STARTED - 5
                        and path.stat().st_size <= 32 * 1024 * 1024
                        and path.suffix.casefold() in {".json", ".bmp", ".log", ".txt"}
                    ):
                        files.append((path, f"bridge-debug/{path.name}"))
                except OSError:
                    LOGGER.exception("Could not inspect bridge debug file: %s", path)
        seen = set()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("diagnostic-manifest.json", safe_json(manifest))
            archive.writestr(
                "README_KO.txt",
                "이 ZIP 파일을 오류 화면과 함께 전달하세요. 인증 토큰은 포함하지 않습니다.\n",
            )
            for path, archive_name in files:
                try:
                    resolved = str(path.resolve()).casefold()
                    if resolved in seen or not path.is_file():
                        continue
                    seen.add(resolved)
                    archive.write(path, archive_name)
                except OSError:
                    LOGGER.exception("Could not add diagnostic file: %s", path)
        LOGGER.info("Diagnostic bundle created: %s (%s bytes)", bundle, bundle.stat().st_size)
        return bundle


class UiLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue):
        super().__init__(logging.INFO)
        self.events = events

    def emit(self, record):
        try:
            self.events.put(("log", self.format(record)))
        except Exception:
            pass


class AutoPaintWindow:
    COLORS = {
        "bg": "#10131b",
        "panel": "#171c28",
        "border": "#30384b",
        "text": "#e7ebf5",
        "muted": "#98a2b8",
        "green": "#55e89c",
        "red": "#ff6b6b",
        "blue": "#7aa7ff",
        "yellow": "#ffcc66",
    }

    def __init__(self, engine: AutoPaintEngine):
        self.engine = engine
        self.events: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("560x430")
        self.root.minsize(520, 400)
        self.root.configure(bg=self.COLORS["bg"])
        self._closing = False
        self._paint_running = False
        self._paint_completed = False
        self._last_progress = ""
        self._latest_bundle: Path | None = None
        self._build_ui()
        self._attach_ui_logger()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)
        self.root.after(250, self._poll_progress)
        threading.Thread(target=self._initial_connect, daemon=True, name="initial-connect").start()

    def _attach_ui_logger(self):
        handler = UiLogHandler(self.events)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s | %(message)s", "%H:%M:%S"))
        LOGGER.addHandler(handler)

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=self.COLORS["bg"], padx=18, pady=16)
        outer.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            outer,
            text="AUTO PAINT ONLY",
            bg=self.COLORS["bg"],
            fg=self.COLORS["blue"],
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            outer,
            text="V7.1 연속 커버리지 모드 · 캐릭터 전신이 보이게 한 뒤 카메라와 캐릭터를 고정하고 Start Painting을 누르세요.",
            bg=self.COLORS["bg"],
            fg=self.COLORS["muted"],
            font=("Malgun Gothic", 9),
        ).pack(anchor="w", pady=(2, 12))

        status_panel = tk.Frame(
            outer,
            bg=self.COLORS["panel"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        status_panel.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="게임 및 브리지 확인 중...")
        self.status_label = tk.Label(
            status_panel,
            textvariable=self.status_var,
            bg=self.COLORS["panel"],
            fg=self.COLORS["yellow"],
            font=("Malgun Gothic", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=500,
        )
        self.status_label.pack(fill=tk.X)
        self.progress = ttk.Progressbar(status_panel, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(9, 3))
        self.progress_var = tk.StringVar(value="대기 중")
        tk.Label(
            status_panel,
            textvariable=self.progress_var,
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            font=("Malgun Gothic", 8),
            anchor="w",
        ).pack(fill=tk.X)

        buttons = tk.Frame(outer, bg=self.COLORS["bg"])
        buttons.pack(fill=tk.X, pady=12)
        self.start_button = self._button(buttons, "Start Painting", self.COLORS["green"], self._start_paint)
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.stop_button = self._button(buttons, "Stop Painting", self.COLORS["red"], self._stop_paint)
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        utility = tk.Frame(outer, bg=self.COLORS["bg"])
        utility.pack(fill=tk.X, pady=(0, 10))
        self._button(utility, "로그 폴더 열기", self.COLORS["blue"], self._open_logs).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
        )
        self._button(utility, "진단 ZIP 만들기", self.COLORS["yellow"], self._make_diagnostics).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0)
        )

        log_panel = tk.Frame(
            outer,
            bg=self.COLORS["panel"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1,
        )
        log_panel.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            log_panel,
            height=8,
            bg=self.COLORS["panel"],
            fg=self.COLORS["muted"],
            insertbackground=self.COLORS["text"],
            relief=tk.FLAT,
            padx=8,
            pady=8,
            wrap=tk.WORD,
            font=("Consolas", 8),
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            outer,
            text=f"세션 로그: {SESSION_LOG}",
            bg=self.COLORS["bg"],
            fg="#687189",
            font=("Consolas", 7),
            anchor="w",
        ).pack(fill=tk.X, pady=(5, 0))

    def _button(self, parent, text, color, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.COLORS["panel"],
            fg=color,
            activebackground=self.COLORS["border"],
            activeforeground=color,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.COLORS["border"],
            font=("Malgun Gothic", 10, "bold"),
            pady=8,
            cursor="hand2",
        )

    def post_status(self, text, color="yellow"):
        self.events.put(("status", text, color))

    def _initial_connect(self):
        pid, _ = self.engine.find_game()
        if pid is None:
            self.post_status("게임을 찾지 못했습니다. 게임에 입장한 뒤 Start Painting을 누르세요.", "red")
            return
        self.post_status(f"게임 발견 (PID {pid}) · 브리지 연결 중...", "yellow")
        error = self.engine.ensure_bridge()
        if error:
            self.post_status(f"브리지 연결 실패: {error}", "red")
            self._auto_diagnostics("connect-error")
        else:
            self.post_status("Bridge Connected · 페인팅 준비 완료", "green")

    def _start_paint(self):
        if self._paint_running:
            LOGGER.warning("Start ignored because another paint request is running")
            return
        if self._paint_completed and not messagebox.askyesno(
            "전체 재도색",
            "다시 실행하면 현재 페인트 위에 전체 결과를 다시 칠합니다.\n\n"
            "캐릭터나 카메라 위치가 바뀐 경우에만 다시 실행하는 것을 권장합니다.\n\n"
            "계속할까요?",
        ):
            return
        self._paint_running = True
        self.start_button.configure(state=tk.DISABLED)
        self.progress["value"] = 0
        self.progress_var.set("페인트 요청 준비 중")
        self.post_status("Auto Paint 시작 중...", "yellow")
        threading.Thread(target=self._paint_worker, daemon=True, name="paint-request").start()

    def _paint_worker(self):
        try:
            response = self.engine.paint()
            if response.get("success"):
                stage = response.get("stage", "accepted")
                message = response.get("message", "페인트 요청이 처리되었습니다")
                self.events.put(("paint-completed",))
                self.post_status(f"완료 [{stage}] · 위치가 바뀌었을 때만 다시 실행하세요", "green")
            else:
                stage = response.get("stage", "unknown_error")
                message = user_error_message(stage, response.get("message", "알 수 없는 오류"))
                self.post_status(f"오류 [{stage}] · {message}", "red")
                self._auto_diagnostics(f"paint-{stage}")
        except Exception as error:
            LOGGER.exception("Paint worker crashed")
            self.post_status(f"Python 예외: {type(error).__name__}: {error}", "red")
            self._auto_diagnostics("python-exception")
        finally:
            self._paint_running = False
            self.events.put(("paint-idle",))

    def _stop_paint(self):
        if not self._paint_running:
            self.post_status("현재 진행 중인 페인팅 작업이 없습니다.", "yellow")
            return
        self.post_status("페인팅 중지 요청 중...", "yellow")
        threading.Thread(target=self._stop_worker, daemon=True, name="cancel-request").start()

    def _stop_worker(self):
        response = self.engine.cancel()
        if response.get("success"):
            self.post_status("페인팅 중지 요청 완료", "green")
        else:
            stage = response.get("stage", "stop_error")
            message = response.get("message", "중지 실패")
            self.post_status(f"중지 오류 [{stage}] · {message}", "red")
            self._auto_diagnostics(f"stop-{stage}")

    def _poll_progress(self):
        if self._closing:
            return
        path = self.engine.progress_path
        if path and path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
                if raw != self._last_progress:
                    progress = json.loads(raw)
                    self._last_progress = raw
                    log_json("Native progress", progress, logging.INFO)
                    percent = max(0.0, min(100.0, float(progress.get("progress", 0.0)) * 100.0))
                    stage = progress.get("stage", "unknown")
                    message = progress.get("message", "")
                    step = progress.get("step", 0)
                    total = progress.get("total_steps", 0)
                    self.progress["value"] = percent
                    self.progress_var.set(f"{stage} · {message} ({step}/{total})")
                    if progress.get("terminal"):
                        result = progress.get("result", "done")
                        if result == "done":
                            self.post_status("Auto Paint 완료", "green")
                            self.progress["value"] = 100
                        elif result == "cancelled":
                            self.post_status("Auto Paint 취소됨", "yellow")
                        else:
                            display_message = user_error_message(stage, message)
                            self.post_status(f"네이티브 페인트 오류 [{stage}] · {display_message}", "red")
                            self._auto_diagnostics(f"native-{stage}")
            except (OSError, ValueError, json.JSONDecodeError):
                # The native side replaces this file while the UI polls it. A partial
                # read is transient, so retry on the next tick without a scary traceback.
                LOGGER.debug("Progress file is temporarily unreadable; retrying")
        self.root.after(250, self._poll_progress)

    def _auto_diagnostics(self, reason):
        def worker():
            try:
                bundle = self.engine.create_diagnostic_bundle(reason)
                self.events.put(("diagnostic", bundle, False))
            except Exception:
                LOGGER.exception("Automatic diagnostic bundle failed")

        threading.Thread(target=worker, daemon=True, name="auto-diagnostics").start()

    def _make_diagnostics(self):
        self.post_status("진단 ZIP 생성 중...", "yellow")

        def worker():
            try:
                bundle = self.engine.create_diagnostic_bundle("manual")
                self.events.put(("diagnostic", bundle, True))
            except Exception as error:
                LOGGER.exception("Manual diagnostic bundle failed")
                self.post_status(f"진단 ZIP 생성 실패: {error}", "red")

        threading.Thread(target=worker, daemon=True, name="manual-diagnostics").start()

    @staticmethod
    def _open_logs():
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_DIR)

    def _open_bundle_location(self, path: Path):
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        except OSError:
            os.startfile(path.parent)

    def _append_log(self, text):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 120:
            self.log_text.delete("1.0", f"{line_count - 100}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_events(self):
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    _, text, color = event
                    self.status_var.set(text)
                    self.status_label.configure(fg=self.COLORS.get(color, self.COLORS["text"]))
                elif event[0] == "log":
                    self._append_log(event[1])
                elif event[0] == "paint-idle":
                    self.start_button.configure(state=tk.NORMAL)
                elif event[0] == "paint-completed":
                    self._paint_completed = True
                    self.start_button.configure(text="다시 캡처·재도색")
                elif event[0] == "diagnostic":
                    _, bundle, user_requested = event
                    self._latest_bundle = bundle
                    self.status_var.set(f"진단 ZIP 생성됨: {bundle.name}")
                    self.status_label.configure(fg=self.COLORS["blue"])
                    if user_requested:
                        self._open_bundle_location(bundle)
                        messagebox.showinfo(
                            "진단 ZIP 생성 완료",
                            f"아래 ZIP 파일을 전달해 주세요.\n\n{bundle}",
                        )
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _close(self):
        self._closing = True
        LOGGER.info("UI closing")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def log_environment(args) -> None:
    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "started_utc": utc_now(),
        "python": sys.version,
        "python_executable": sys.executable,
        "argv": ["<script>"] + sys.argv[1:],
        "platform": platform.platform(),
        "base_dir": str(BASE_DIR),
        "native_dir": str(NATIVE_DIR),
        "mesh_dir": str(MESH_DIR),
        "data_dir": str(DATA_DIR),
        "session_log": str(SESSION_LOG),
        "game_process": args.game_process,
    }
    log_json("Session environment", manifest, logging.INFO)


def headless_bridge_test(engine: AutoPaintEngine) -> int:
    LOGGER.info("Starting headless bridge test")
    error = engine.ensure_bridge()
    if error:
        LOGGER.error("Headless bridge test failed: %s", error)
        print(f"FAIL: {error}")
        engine.create_diagnostic_bundle("headless-failure")
        return 1
    if not engine.ping():
        LOGGER.error("Headless bridge ping failed")
        print("FAIL: bridge ping failed")
        engine.create_diagnostic_bundle("headless-ping-failure")
        return 1
    engine.shutdown()
    LOGGER.info("Headless bridge test passed")
    print("auto-paint-only direct bridge test: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--game-process", default=DEFAULT_GAME_PROCESS)
    parser.add_argument("--headless-bridge-test", action="store_true")
    args = parser.parse_args()
    install_exception_logging()
    log_environment(args)
    engine = AutoPaintEngine(args.game_process)
    if args.headless_bridge_test:
        return headless_bridge_test(engine)
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        pass
    AutoPaintWindow(engine).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
