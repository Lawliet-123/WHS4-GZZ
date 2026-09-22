"""탐지 세션을 팀 replay-data 양식으로 내보낸다 (7번 ReplayAnalyzer 제출용).

허송희가 2026-09-17 정한 양식이고 `replay-data/noclip/noclip_001/` 이 기준 예시다.

    replay-data/<핵>/<session_id>/
        manifest.json          라벨·구간 (정답지)
        events.jsonl           시계열 이벤트
        raw/<module>.jsonl     원본 로그 (형식 자유)

## 우리 로그와의 차이

`main.py` 는 한 세션에 모듈당 **한 건**을 낸다. 외부 스캔이라 스냅샷 하나가
한 번의 검사이기 때문이다. noclip 예시처럼 초당 여러 줄이 나오지 않는다.

그래서 시계열을 지어내지 않는다. **관측한 만큼만 쓴다.**
`manifest.json` 의 `samples` 로 몇 건인지 밝히고, 없는 구간을 채우지 않는다.

## 라벨은 추론하지 않는다

`label` 은 정답지다. 탐지 결과에서 역산하면 채점이 순환 논리가 된다
(탐지기가 잡았으니 CHEAT 이다 → 탐지기가 맞았다). 그래서 **인자로 받는다.**

사용:
    python replay_export.py <session_id> --cheat whistle-spoofing
    python replay_export.py <session_id> --clean
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGS = os.path.join(HERE, "logs", "detection")

# 우리 모듈 -> 팀 replay-data 폴더 (modules/ 이름과 맞춘다)
CHEAT_DIRS = {
    "whistle-spoofing", "hide-anywhere", "auto-paint", "auto-paint-ver2",
    "esp", "godmode", "noclip", "aimbot",
}


def load_session(log_path):
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    if not events:
        raise SystemExit(f"세션 로그가 비어 있습니다: {log_path}")
    return events


def to_replay_event(ev, player_id):
    """팀 events.jsonl 한 줄로 바꾼다.

    noclip 예시의 키를 그대로 쓴다 — session_id / player_id / module /
    timestamp_ms / evidence / reasons / raw_score.

    우리 `evidence` 는 문자열 dict 이고 예시는 숫자 dict 이지만, 예시에도
    스키마 강제는 없다. 숫자로 바꾸려고 정보를 버리지 않는다.
    """
    return {
        "session_id": ev["session_id"],
        # 이벤트에 박힌 값이 우선이다. 인자는 예전 로그를 위한 대비책이다.
        "player_id": ev.get("player_id") or player_id,
        "module": ev["module"],
        "timestamp_ms": ev["timestamp_ms"],
        "window_id": ev.get("window_id", 0),
        "sample_id": ev.get("sample_id", 0),
        "evidence": ev.get("evidence", {}),
        "reasons": ev.get("reasons", []),
        # `score` 는 2026-09-21 이전 로그의 옛 키다. 그때 찍은 세션 4개를
        # 다시 못 읽으면 지금까지의 측정이 통째로 날아간다.
        "raw_score": ev.get("raw_score", ev.get("score", 0)),
        # 우리 계약의 상태를 같이 싣는다. ERROR/OFFLINE 은 CLEAN 이 아니고
        # 집계에서 빼야 하는데, raw_score 만 보면 0 이라 구분이 안 된다.
        "status": ev.get("status"),
        "severity": ev.get("severity"),
    }


def export(session_id, cheat, out_root, log_dir=None, player_id="player_001"):
    log_dir = log_dir or DEFAULT_LOGS
    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    if not os.path.exists(log_path):
        raise SystemExit(f"세션 로그가 없습니다: {log_path}")

    events = load_session(log_path)
    folder = cheat or "normal"
    dest = os.path.join(out_root, folder, session_id)
    os.makedirs(os.path.join(dest, "raw"), exist_ok=True)

    rows = [to_replay_event(e, player_id) for e in events]
    with open(os.path.join(dest, "events.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 원본은 그대로 같이 넣는다. 가공본만 남기면 재검증을 못 한다.
    with open(os.path.join(dest, "raw", "detection.jsonl"), "w",
              encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    times = [e["timestamp_ms"] for e in events]
    manifest = {
        "label": "CHEAT" if cheat else "NORMAL",
        "player_id": player_id,
        "session_id": session_id,
        "cheat_type": cheat.upper().replace("-", "_") if cheat else None,
        # 외부 스냅샷이라 "언제부터 켰는지"를 우리가 모른다. 지어내지 않는다.
        "cheat_start_ms": None,
        "cheat_end_ms": None,
        # 아래는 우리가 덧붙이는 것. 표본 수를 밝혀야 집계가 정직해진다.
        "source": "external scan (anti-cheat/main.py)",
        "samples": len(events),
        "span_ms": [min(times), max(times)],
        "modules": sorted({e["module"] for e in events}),
        "excluded_from_scoring": sorted(
            {e["module"] for e in events
             if e.get("status") in ("ERROR", "OFFLINE")}),
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return dest, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="탐지 세션 -> replay-data 양식")
    ap.add_argument("session")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cheat", choices=sorted(CHEAT_DIRS),
                   help="핵이 켜져 있던 세션. 폴더 이름이 된다")
    g.add_argument("--clean", action="store_true", help="핵 없는 정상 세션")
    ap.add_argument("--out", default=None, help="replay-data 루트")
    ap.add_argument("--logs", default=None)
    ap.add_argument("--player", default="player_001")
    a = ap.parse_args(argv)

    out = a.out or os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "replay-data")
    dest, manifest = export(a.session, None if a.clean else a.cheat,
                            out, a.logs, a.player)
    print(f"{dest}")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
