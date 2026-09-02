import argparse
import csv
import ctypes
from ctypes import wintypes
from pathlib import Path
import time

from engine import GameLink
from step5_target_angle import direction_to_angles
from step8_fov_target import FovTargetSelector, collect_candidates


VK_F8 = 0x77
user32 = ctypes.WinDLL("user32", use_last_error=True)


def normalize_angle(angle):
    """Normalize degrees to [-180, 180)."""
    return (angle + 180.0) % 360.0 - 180.0


def shortest_delta(current, target):
    """Return the shortest signed angular distance from current to target."""
    return normalize_angle(target - current)


def step_rotation(current, target, max_speed, dt):
    """Move pitch/yaw toward target without exceeding max_speed deg/s."""
    limit = max(0.0, max_speed * dt)
    dp = max(-limit, min(limit, shortest_delta(current[0], target[0])))
    dy = max(-limit, min(limit, shortest_delta(current[1], target[1])))
    return normalize_angle(current[0] + dp), normalize_angle(current[1] + dy), 0.0


def is_activation_pressed():
    return bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)


def find_client_size(process_id):
    """Return the visible top-level client size for a process, or None."""
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != process_id or not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width > 0 and height > 0:
                result.append((width, height))
        return True

    user32.EnumWindows(callback, 0)
    return max(result, key=lambda size: size[0] * size[1], default=None)

class TraceWriter:
    fields = (
        "time", "active", "target_pawn", "target_class", "aim_source", "screen_error_px",
        "observed_pitch", "observed_yaw", "desired_pitch", "desired_yaw",
        "synthetic_pitch", "synthetic_yaw", "delta_pitch", "delta_yaw",
        "write_ok", "readback_pitch", "readback_yaw",
        "readback_error_pitch", "readback_error_yaw",
    )

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=self.fields)
        self.writer.writeheader()

    def write(self, row):
        self.writer.writerow(row)
        self.handle.flush()

    def close(self):
        self.handle.close()


def parse_args():
    parser = argparse.ArgumentParser(description="aim rotation")
    parser.add_argument("--width", type=int, help="Game screen width (auto-detected by default)")
    parser.add_argument("--height", type=int, help="Game screen height (auto-detected by default)")
    parser.add_argument("--radius", type=float, default=700.0, help="FOV circle radius (px)")
    parser.add_argument("--aim-height", type=float, default=80.0, help="Aim height above pawn origin")
    parser.add_argument("--speed", type=float, default=120.0, help="Synthetic maximum angular speed (deg/s)")
    parser.add_argument("--hz", type=float, default=60.0, help="Samples per second")
    parser.add_argument("--status-hz", type=float, default=5.0, help="Console status updates per second")
    parser.add_argument("--always-active", action="store_true", help="Trace without holding right click")
    parser.add_argument("--output", type=Path, default=Path("aim_trace.csv"), help="CSV output path")
    return parser.parse_args()


def main():
    args = parse_args()
    if min(args.radius, args.speed, args.hz, args.status_hz) <= 0:
        raise ValueError("radius, speed, hz, and status-hz must be positive")
    if (args.width is None) != (args.height is None):
        raise ValueError("width and height must be specified together")

    print("Connecting to game process...")
    link = GameLink()
    detected = find_client_size(link.pm.process_id)
    width, height = (args.width, args.height) if args.width else (detected or (1920, 1080))
    print(f"Screen size: {width}x{height}" + (" (auto-detected)" if args.width is None and detected else ""))
    print(f"CSV: {args.output.resolve()}")
    print(f"Verification settings: radius={args.radius:.0f}px, speed={args.speed:.0f} deg/s, samples={args.hz:.0f} Hz")
    if args.always_active:
        print("Activation: always active")
    else:
        print("Activation: hold F8; release it to stop writes")

    selector = FovTargetSelector(args.radius, priority="distance")
    trace = TraceWriter(args.output)
    synthetic = None
    previous = time.perf_counter()
    started = previous
    next_status = started

    try:
        while True:
            frame_start = time.perf_counter()
            dt = min(0.1, max(0.0, frame_start - previous))
            previous = frame_start
            camera = link.get_camera()
            active = args.always_active or is_activation_pressed()

            if camera is None or not link.camera_is_sane(camera):
                selector.locked_pawn = None
                synthetic = None
            else:
                observed = camera["rotation"]
                candidates = collect_candidates(link, camera, width, height, args.aim_height)
                target = selector.choose(candidates) if active else None

                desired = None
                write_ok = None
                readback = None
                if target is not None:
                    desired = direction_to_angles(camera["location"], target["aim_point"])
                    if synthetic is None:
                        control = link.get_control_rotation()
                        synthetic = control if control is not None else (observed[0], observed[1], 0.0)
                    synthetic = step_rotation(synthetic, desired, args.speed, dt)
                    write_ok = link.set_control_rotation(synthetic)
                    if write_ok:
                        readback = link.get_control_rotation()
                else:
                    # Avoid overwriting live input while inactive or without a target.
                    synthetic = None

                trace.write(
                    {
                        "time": f"{frame_start - started:.6f}",
                        "active": int(active),
                        "target_pawn": f"0x{target['pawn']:X}" if target else "",
                        "target_class": target.get("class_name", "") if target else "",
                        "aim_source": target.get("aim_source", "") if target else "",
                        "screen_error_px": f"{target['screen_dist']:.3f}" if target else "",
                        "observed_pitch": f"{observed[0]:.6f}",
                        "observed_yaw": f"{observed[1]:.6f}",
                        "desired_pitch": f"{desired[0]:.6f}" if desired else "",
                        "desired_yaw": f"{desired[1]:.6f}" if desired else "",
                        "synthetic_pitch": f"{synthetic[0]:.6f}" if synthetic else "",
                        "synthetic_yaw": f"{synthetic[1]:.6f}" if synthetic else "",
                        "delta_pitch": f"{shortest_delta(observed[0], synthetic[0]):.6f}" if synthetic else "",
                        "delta_yaw": f"{shortest_delta(observed[1], synthetic[1]):.6f}" if synthetic else "",
                        "write_ok": int(write_ok) if write_ok is not None else "",
                        "readback_pitch": f"{readback[0]:.6f}" if readback else "",
                        "readback_yaw": f"{readback[1]:.6f}" if readback else "",
                        "readback_error_pitch": f"{shortest_delta(synthetic[0], readback[0]):.6f}" if synthetic and readback else "",
                        "readback_error_yaw": f"{shortest_delta(synthetic[1], readback[1]):.6f}" if synthetic and readback else "",
                    }
                )

                if target is not None and frame_start >= next_status:
                    next_status = frame_start + 1.0 / args.status_hz
                    readback_text = (
                        f" readback=({readback[0]:.1f},{readback[1]:.1f})"
                        if readback else " readback=(unavailable)"
                    )
                    print(
                        f"target=0x{target['pawn']:X} error={target['screen_dist']:.1f}px "
                        f"aim={target.get('aim_source', '?')} "
                        f"observed=({observed[0]:.1f},{observed[1]:.1f}) "
                        f"synthetic=({synthetic[0]:.1f},{synthetic[1]:.1f}) "
                        f"write={'OK' if write_ok else 'FAILED'}{readback_text}"
                    )

            time.sleep(max(0.0, 1.0 / args.hz - (time.perf_counter() - frame_start)))
    finally:
        trace.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDone.")
