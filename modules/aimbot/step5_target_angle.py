"""
5단계: 목표 방향 벡터 계산 -> 각도로 변환 (계산만, 메모리에는 안 씀).

원리:
1. 내 카메라 위치(camera.location)와, 조준할 대상(가장 가까운 다른 플레이어)의
   좌표(target.pos)를 안다 (4단계, 3단계에서 이미 읽은 값).
2. 방향 벡터 d = target - camera_location.
3. UE의 Rotator(Pitch, Yaw, Roll) 규칙에 맞춰 d를 각도로 변환:
   - Yaw   = atan2(d.y, d.x)                       (수평면에서 X축 기준 회전각)
   - Pitch = atan2(d.z, sqrt(d.x^2 + d.y^2))        (수평 거리 대비 높이 차 각도)
   Roll은 조준에 안 쓰므로 계산하지 않음.

여기서는 "이 각도로 카메라를 돌리면 대상을 정확히 바라보게 된다"는 목표값만
계산해서 출력한다. 실제 카메라 회전값을 읽거나 쓰는 건 각각 6, 8단계.
"""
import math
import time
from engine import GameLink
from step3_other_players_positions import list_other_players


def direction_to_angles(origin, target):
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    dz = target[2] - origin[2]
    horizontal_dist = math.sqrt(dx * dx + dy * dy)
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = math.degrees(math.atan2(dz, horizontal_dist))
    return pitch, yaw


def pick_nearest(players):
    if not players:
        return None
    return min(players, key=lambda p: p["dist"] if p["dist"] is not None else float("inf"))


def main():
    print("게임 프로세스에 연결 시도 중...")
    link = GameLink()
    print("연결 성공. GUObjectArray:", hex(link.guobject_array))
    print("Ctrl+C 로 종료. 1초마다 '조준해야 할 목표 각도' 계산 결과 출력.\n")

    while True:
        cam = link.get_camera()
        players = list_other_players(link)
        nearest = pick_nearest(players)

        if cam is None:
            print("카메라 정보를 읽지 못함")
        elif nearest is None:
            print("주변에 다른 플레이어 없음")
        else:
            target_pitch, target_yaw = direction_to_angles(cam["location"], nearest["pos"])
            print(
                f"가장 가까운 대상 거리={nearest['dist']:.1f}  "
                f"-> 목표 각도: pitch={target_pitch:.2f}, yaw={target_yaw:.2f}   "
                f"(현재 카메라: pitch={cam['rotation'][0]:.2f}, yaw={cam['rotation'][1]:.2f})"
            )
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료.")
