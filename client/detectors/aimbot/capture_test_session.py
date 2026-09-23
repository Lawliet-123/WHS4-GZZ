"""
capture_test_session.py
로그 담당(7번, 송희)이 요청한 공통 제출 규격으로 폴더를 만들어주는 도구.

요청 규격:
  <test_id>/
    ├── manifest.json     정상/핵 여부, 핵 ON/OFF 구간
    ├── events.jsonl      팀 공통 Event 포맷. raw_score를 계산한 시점마다 기록
                          (탐지된 순간만이 아님), session_id는 test_id로 통일
    └── raw/
        └── <원본 로그 파일>  (main.lua가 남긴 원본, 형식 자유)

사용 예:
  # 정상 플레이 테스트
  python capture_test_session.py --test-id aimbot_normal_001 --label normal \
      --raw-log logs/meccha_aim_telemetry.jsonl

  # 에임봇 사용 테스트 (세션 전체가 핵 사용 구간이라고 가정할 때)
  python capture_test_session.py --test-id aimbot_cheat_001 --label cheat \
      --raw-log logs/meccha_aim_telemetry.jsonl

  # 세션 중 일부 구간만 핵을 켰던 경우 (ms 단위, 세션 시작 기준 경과시간)
  python capture_test_session.py --test-id aimbot_cheat_002 --label cheat \
      --raw-log logs/meccha_aim_telemetry.jsonl \
      --cheat-window 12000 45000
"""

import argparse
import json
import shutil
from pathlib import Path

from detector.aimbot_detector import AimbotDetector
from sensors.meccha_aim_telemetry_sensor import MecchaAimTelemetrySensor


def load_raw_events(raw_log_path: Path):
    """원본 JSONL을 실시간 판정과 동일한 Sensor로 변환한다.

    단순히 ShotEvent를 다시 만들면 aim_trace, aimed_candidate_*가 빠져 신호
    5·12가 재현되지 않는다. 제출 패키지의 events.jsonl도 main.py와 완전히
    같은 변환 경로를 사용해야 한다.
    """
    return list(MecchaAimTelemetrySensor(raw_log_path).read_events())


def build_events_jsonl(raw_events, out_path: Path, test_id: str):
    detector = AimbotDetector()
    with out_path.open("w", encoding="utf-8") as f:
        for event in raw_events:
            # 매 발사/확정 결과마다 그 시점까지의 raw_score를 다시 계산해 반환한다.
            # (탐지된 순간만이 아니라 "계산한 시점마다" 기록해야 하는 요구사항을 만족)
            record = detector.ingest_event(event)
            # 원본 로그의 session_YYYY... 값은 raw/에 그대로 보존한다. 통합용
            # Event에는 팀이 정한 테스트 단위 ID(normal_001, aimbot_001)를 넣어
            # ReplayAnalyzer/Dashboard가 한 번의 실험을 바로 구분할 수 있게 한다.
            record["session_id"] = test_id
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return detector


def build_manifest(test_id, label, cheat_windows_ms):
    return {
        "test_id": test_id,
        "module": "aimbot",
        "label": label,  # "normal" | "cheat"
        "cheat_type": "aimbot" if label == "cheat" else None,
        "cheat_windows_ms": cheat_windows_ms,  # [[start_ms, end_ms], ...], normal이면 []
    }


def main():
    parser = argparse.ArgumentParser(description="테스트 세션을 공통 제출 규격 폴더로 패키징")
    parser.add_argument("--test-id", required=True, help="예: aimbot_normal_001")
    parser.add_argument("--label", required=True, choices=["normal", "cheat"])
    parser.add_argument("--raw-log", required=True, type=Path, help="main.lua가 남긴 원본 JSONL 경로")
    parser.add_argument("--out-dir", type=Path, default=Path("test_sessions"))
    parser.add_argument(
        "--cheat-window",
        nargs=2,
        type=int,
        action="append",
        metavar=("START_MS", "END_MS"),
        help="핵을 켠 구간 (세션 시작 기준 경과 ms). 여러 번 지정 가능. "
             "label=cheat이고 한 번도 안 주면 세션 전체를 핵 구간으로 간주.",
    )
    args = parser.parse_args()

    if not args.raw_log.exists():
        raise SystemExit(f"raw log를 찾을 수 없음: {args.raw_log}")

    session_dir = args.out_dir / args.test_id
    raw_dir = session_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_events = load_raw_events(args.raw_log)
    if not raw_events:
        print(f"[WARNING] {args.raw_log}에 이벤트가 하나도 없음 — 빈 events.jsonl이 만들어짐")

    events_path = session_dir / "events.jsonl"
    build_events_jsonl(raw_events, events_path, args.test_id)

    raw_copy_path = raw_dir / args.raw_log.name
    # 이미 패키지 안 raw/의 파일을 다시 패키징할 때는 source와 destination이
    # 동일하다. Windows에서는 자기 자신으로 CopyFile2를 호출하면 파일 잠금
    # 오류가 나므로 복사를 건너뛴다.
    if args.raw_log.resolve() != raw_copy_path.resolve():
        shutil.copy2(args.raw_log, raw_copy_path)

    cheat_windows_ms = args.cheat_window or []
    if args.label == "cheat" and not cheat_windows_ms and raw_events:
        session_end_ms = raw_events[-1].timestamp_ms
        cheat_windows_ms = [[0, session_end_ms]]

    manifest = build_manifest(args.test_id, args.label, cheat_windows_ms)
    with (session_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[OK] {session_dir} 생성 완료")
    print(f"  - events.jsonl: {len(raw_events)}줄")
    print(f"  - manifest.json: label={args.label}, cheat_windows_ms={cheat_windows_ms}")
    print(f"  - raw/{args.raw_log.name}")


if __name__ == "__main__":
    main()
