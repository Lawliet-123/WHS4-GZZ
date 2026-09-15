"""후크가 남긴 RPC 위반 기록을 팀 공통 계약으로 바꾼다.

판정은 C++ 후크(`native/whistle_hook/src/main.cpp`)가 인프로세스에서 한다.
RPC 호출 시점은 외부에서 볼 수 없어서 후킹이 아니면 방법이 없다.
이 파일은 그 결과를 읽어 `result.py` 계약으로 옮기는 보고 계층이다.

팀이 파이썬으로 통일했으므로 **다른 탐지기와 같은 형식으로 나온다.**
후크가 C++ 인 것은 구현 제약이지 계약의 예외가 아니다.

사용법:
    python detectors/whistle_rpc.py                      # 기본 경로에서 읽는다
    python detectors/whistle_rpc.py <ac-whistle.jsonl>
"""

import json
import os
import sys

# 이 파일을 직접 실행해도(`python detectors/whistle_rpc.py`) core/ 를 찾게 한다.
# 팀원마다 실행 방식이 달라서 둘 다 되게 해둔다.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.result import DetectorResult, Evidence, to_team_event

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 후크는 **자기 DLL 이 있는 폴더**에 로그를 쓴다(main.cpp `DllDirectory()`).
# 빌드 위치가 사람마다 달라서 후보를 순서대로 본다. 인자로 직접 넘겨도 된다.
LOG_CANDIDATES = [
    os.path.join(_ROOT, "logs", "raw", "ac-whistle.jsonl"),
    os.path.join(_ROOT, "native", "whistle_hook", "bin", "Release", "ac-whistle.jsonl"),
    os.path.join(_ROOT, "native", "whistle_hook", "bin", "ac-whistle.jsonl"),
]


def default_log():
    """존재하는 첫 후보를 돌려준다. 하나도 없으면 첫 후보(오류 메시지용)."""
    for p in LOG_CANDIDATES:
        if os.path.exists(p):
            return p
    return LOG_CANDIDATES[0]

# 후크가 내보내는 위반 코드 -> (점수, 대응하는 실측 취약점, 서버측 권고)
RULES = {
    "role_violation":     (60, "V3 호출자 역할", "S-1"),
    "dead_caller":        (60, "V4 생존 상태", "S-1"),
    "foreign_target":     (60, "V5 대상 지정", "S-2"),
    "cooldown_violation": (40, "V2 호출 빈도", "S-3"),
    "no_input_event":     (40, "V1 호출 경로", "S-1"),
}


def scan(path=None):
    r = DetectorResult("whistle_rpc")
    path = path or default_log()

    if not os.path.exists(path):
        return r.fail(f"후크 로그가 없습니다: {path}\n"
                      f"    ac_whistle_v1.dll 을 주입했는지 확인하세요.")

    started = False
    violations = []
    hooks_total = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                kind = ev.get("event")
                if kind == "start":
                    started = True
                elif kind == "hooks":
                    hooks_total = ev.get("total", hooks_total)
                elif ev.get("codes"):
                    violations.append(ev)
    except Exception as e:
        return r.fail(f"로그를 읽지 못했습니다: {e}")

    if not started:
        # 후크가 붙지 못한 것과 "위반이 없는 것"은 다르다.
        # 이걸 CLEAN 으로 뭉개면 조용한 미탐지가 된다.
        return r.fail("후크 시작 기록이 없습니다. DLL 이 로드되지 않았습니다.")

    r.meta["log"] = path
    r.meta["hooked_vtables"] = hooks_total
    r.meta["provocation_calls"] = len(violations)

    if not violations:
        r.detail = f"vtable {hooks_total}개 후킹 — 도발 RPC 위반 없음"
        return r

    # 같은 코드가 여러 번 나와도 점수는 한 번만 준다.
    # 반복은 확신을 높이지만 등급을 무한히 올리면 의미가 없어진다.
    seen = set()
    counts = {}
    for ev in violations:
        for code in ev.get("codes", []):
            counts[code] = counts.get(code, 0) + 1

    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if code in seen:
            continue
        seen.add(code)
        points, vuln, fix = RULES.get(code, (30, "미분류", "-"))
        r.add(code, points,
              f"{vuln} 위반 {n}회 (서버측 권고 {fix})",
              [Evidence("value", f"{n}회", vuln)])

    # 근거로 실제 로그 몇 줄을 같이 넘긴다. 운영자가 조치하려면 필요하다.
    for ev in violations[:3]:
        r.evidence.append(Evidence(
            "value", f"t={ev.get('t')}s {ev.get('fn')}",
            ev.get("detail", "")))

    blocked = sum(1 for ev in violations if ev.get("blocked"))
    if blocked:
        r.meta["blocked"] = blocked
        r.detail += f" / 차단 {blocked}건"
    return r


def main(argv=None):
    """단독 실행. 인자로 session_id 를 주면 그대로 쓴다 (측정 실험용)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    session = argv.pop(0) if argv and not argv[0].endswith(".jsonl") else "whistle_rpc_001"
    res = scan(argv[0] if argv else None)
    print(json.dumps(to_team_event(res, session), ensure_ascii=False, indent=2))
    # 종료코드: 0 정상 / 1 의심 이상 / 2 검사 실패
    return {"NORMAL": 0, "ERROR": 2, "OFFLINE": 2}.get(
        to_team_event(res, session)["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
