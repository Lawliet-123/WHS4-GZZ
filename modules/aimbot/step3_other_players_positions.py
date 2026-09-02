"""
3단계: 다른 플레이어들의 좌표 출력 (읽기 전용).

원리:
World -> GameState -> PlayerArray 를 순회해서 각 PlayerState의 Pawn을 얻고,
내 Pawn과 다른 것만 골라 Pawn -> RootComponent -> RelativeLocation 을 읽는다.
2단계에서 만든 "세기"에 좌표 읽기만 추가한 것.
"""
import math
import time
from engine import GameLink


def distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def list_other_players(link: GameLink):
    world = link.get_world()
    my_pawn = link.get_local_pawn(world)
    my_pos = link.get_actor_position(my_pawn) if my_pawn else None

    result = []
    for player_state in link.iter_player_states(world):
        pawn = link.get_pawn_of(player_state)
        if not pawn or pawn == my_pawn:
            continue
        pos = link.get_actor_position(pawn)
        if pos is None:
            continue
        dist = distance(my_pos, pos) if my_pos else None
        result.append(
            {
                "pawn": pawn,
                "pos": pos,
                "dist": dist,
                "class_name": link.table.class_name_of(pawn),
                "bounds": link.get_actor_bounds(pawn),
                "character_state": link.get_cleon_character_state(pawn),
            }
        )
    return result


def main():
    print("게임 프로세스에 연결 시도 중...")
    link = GameLink()
    print("연결 성공. GUObjectArray:", hex(link.guobject_array))
    print("Ctrl+C 로 종료. 2초마다 다른 플레이어 좌표 출력.\n")

    while True:
        players = list_other_players(link)
        print(f"--- 다른 플레이어 {len(players)}명 ---")
        for i, p in enumerate(players):
            x, y, z = p["pos"]
            dist_str = f"{p['dist']:.1f}" if p["dist"] is not None else "?"
            print(f"[{i}] pawn=0x{p['pawn']:X}  pos=({x:.1f}, {y:.1f}, {z:.1f})  거리={dist_str}")
        print()
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료.")
