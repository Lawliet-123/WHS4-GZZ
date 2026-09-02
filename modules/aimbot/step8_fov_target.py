"""Step 8: read-only FOV target selection.

Projects other players into screen space, keeps only candidates inside a circle
around the crosshair, and reports the target closest to the circle centre.
Nothing in this file writes to the game process or moves the camera.
"""
import argparse
import math
import time

from engine import GameLink
from step3_other_players_positions import list_other_players
from step5_target_angle import direction_to_angles


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def camera_axes(rotation):
    """Return Unreal-style forward, right and up unit vectors."""
    pitch, yaw, roll = (math.radians(value) for value in rotation)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)

    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-cr * sp * cy - sr * sy, -cr * sp * sy + sr * cy, cr * cp)
    return forward, right, up


def world_to_screen(world, camera, width, height):
    """Project an Unreal world position to pixels; return None if behind view."""
    origin = camera["location"]
    relative = tuple(world[i] - origin[i] for i in range(3))
    forward, right, up = camera_axes(camera["rotation"])
    depth = dot(relative, forward)
    if depth <= 0.01:
        return None

    fov = camera["fov"]
    if not 1.0 <= fov <= 179.0:
        return None
    focal = width / (2.0 * math.tan(math.radians(fov) / 2.0))
    screen_x = width / 2.0 + dot(relative, right) * focal / depth
    screen_y = height / 2.0 - dot(relative, up) * focal / depth
    return screen_x, screen_y, depth


def screen_distance(point, width, height):
    return math.hypot(point[0] - width / 2.0, point[1] - height / 2.0)


class FovTargetSelector:
    """Closest-to-crosshair selection with a small release hysteresis."""

    def __init__(self, radius, release_scale=1.15, priority="screen"):
        if priority not in ("screen", "distance"):
            raise ValueError("priority must be 'screen' or 'distance'")
        self.radius = radius
        self.release_radius = radius * release_scale
        self.priority = priority
        self.locked_pawn = None

    def choose(self, candidates):
        by_pawn = {candidate["pawn"]: candidate for candidate in candidates}
        locked = by_pawn.get(self.locked_pawn)
        if locked is not None and locked["screen_dist"] <= self.release_radius:
            return locked

        inside = [candidate for candidate in candidates if candidate["screen_dist"] <= self.radius]
        if self.priority == "distance":
            key = lambda candidate: (
                candidate["dist"] if candidate.get("dist") is not None else float("inf"),
                candidate["screen_dist"],
            )
        else:
            key = lambda candidate: candidate["screen_dist"]
        selected = min(inside, key=key, default=None)
        self.locked_pawn = selected["pawn"] if selected else None
        return selected


def collect_candidates(link, camera, width, height, aim_height):
    candidates = []
    for player in list_other_players(link):
        # During the hiding phase survivors may use the common Character_C
        # Pawn class instead of a class containing the word "Survivor".
        # Accept cLeon character Pawns, but explicitly reject hunters and
        # spectator Pawns.
        class_name = player.get("class_name") or ""
        state = player.get("character_state")
        if (
            "BP_FirstPersonCharacter_cLeon_Character" not in class_name
            or "Hunter" in class_name
            or "SpectatePawn" in class_name
            or state is None
            or state["is_hunter"]
            or not state["is_live"]
        ):
            continue
        bounds = player.get("bounds")
        aim_point = (
            bounds["origin"]
            if bounds is not None
            else (player["pos"][0], player["pos"][1], player["pos"][2] + aim_height)
        )
        projected = world_to_screen(aim_point, camera, width, height)
        if projected is None:
            continue
        sx, sy, depth = projected
        candidates.append(
            {
                **player,
                "aim_point": aim_point,
                "aim_source": "bounds" if bounds is not None else "fallback-height",
                "screen": (sx, sy),
                "depth": depth,
                "screen_dist": screen_distance((sx, sy), width, height),
            }
        )
    return candidates


def parse_args():
    parser = argparse.ArgumentParser(description="읽기 전용 화면 FOV 타깃 선택 진단")
    parser.add_argument("--width", type=int, default=1920, help="게임 화면 너비")
    parser.add_argument("--height", type=int, default=1080, help="게임 화면 높이")
    parser.add_argument("--radius", type=float, default=180.0, help="FOV 원 반지름(px)")
    parser.add_argument("--aim-height", type=float, default=80.0, help="Pawn 원점 위 조준 높이(UE 단위)")
    parser.add_argument("--hz", type=float, default=20.0, help="초당 판정 횟수")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.width <= 0 or args.height <= 0 or args.radius <= 0 or args.hz <= 0:
        raise ValueError("width, height, radius, hz는 양수여야 합니다")

    print("게임 프로세스 연결 중...")
    link = GameLink()
    selector = FovTargetSelector(args.radius)
    interval = 1.0 / args.hz
    print(
        f"읽기 전용 FOV 선택 시작: {args.width}x{args.height}, "
        f"radius={args.radius:.0f}px, aim_height={args.aim_height:.1f}"
    )
    print("Ctrl+C로 종료합니다. 게임 메모리는 수정하지 않습니다.\n")

    while True:
        started = time.perf_counter()
        camera = link.get_camera()
        if camera is None or not link.camera_is_sane(camera):
            selector.locked_pawn = None
            print("카메라 정보 없음/비정상")
        else:
            candidates = collect_candidates(link, camera, args.width, args.height, args.aim_height)
            target = selector.choose(candidates)
            inside_count = sum(candidate["screen_dist"] <= args.radius for candidate in candidates)
            if target is None:
                print(f"후보={len(candidates)}  원 내부={inside_count}  target=없음")
            else:
                pitch, yaw = direction_to_angles(camera["location"], target["aim_point"])
                sx, sy = target["screen"]
                print(
                    f"후보={len(candidates)}  원 내부={inside_count}  "
                    f"target=0x{target['pawn']:X}  screen=({sx:.1f},{sy:.1f})  "
                    f"center_error={target['screen_dist']:.1f}px  "
                    f"desired=(pitch={pitch:.2f}, yaw={yaw:.2f})"
                )

        elapsed = time.perf_counter() - started
        time.sleep(max(0.0, interval - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료.")
