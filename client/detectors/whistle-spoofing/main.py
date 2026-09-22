"""휘파람(Provocation) 핵 탐지 러너.

9/20 회의에서 핵마다 담당자가 정해졌고(회의록 「중간 회의」), 구조안대로
핵마다 자기 폴더에 자기 main.py 를 둔다. **휘파람은 내(조랑언) 담당이다.**
회의에 참석하지 못해 회의록 목록과 구조안 핵 폴더 목록 양쪽에 휘파람이
빠져 있어서, 이미 머지된 `modules/whistle-spoofing` 과 같은 이름으로 만들었다.

핵 코드와 탐지 코드는 자리를 갈라 둔다 — 핵 PoC·분석은 `hack/<핵>/`,
탐지는 `TelemetryServer/detectors/<핵>/`. 같은 이름 폴더가 두 군데 있으면
`whistle-spoofing` 이 핵 코드인지 탐지 코드인지 헷갈린다.

## 왜 러너를 따로 두는가

탐지기 자체는 세션 묶기·공통 형식 변환을 몰라도 되게 두었다. 그 일은
2번 러너(`LocalGuard/memory_integrity/run_session.py`)가 이미 하고 있어서,
여기서는 **등록표만 바꿔서 그대로 재사용한다.** 로직을 복사하면 세션 id 와
기준 시각(t0)을 맞추는 코드가 두 벌이 되고, 둘이 어긋나는 순간
ReplayAnalyzer 에서 타임라인이 안 겹친다.

## 사용법

    python main.py --session rpc_001
    python main.py --session rpc_001 --log-dir <서버>/logs/detections --log-name whistle-spoofing

두 번째 형태가 구조안의 "logs/detections/각자핵.jsonl 에 계속 추가" 방식이다.
**기본값으로 박지 않았다.**

2026-09-22 구조안에서 scoring 이 중앙 server/ 로 옮겨갔다. 즉 결과를 보내는
경로가 "폴더에 파일 쓰기"에서 "HTTP 전송"으로 바뀐다(`shared/logger.py`).
그 창구가 생기면 이 러너는 --log-dir 대신 그쪽을 부르면 된다.
탐지 로직은 건드릴 것이 없다 — 이미 같은 형식으로 결과만 만들고 있다.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _memory_integrity():
    """2번 모듈 폴더를 찾는다. **상위 폴더 개수를 세지 않는다.**

    이 파일은 detectors/ 가 생기면서 한 번 더 내려갔다. 그때 dirname 을
    세는 코드가 조용히 엉뚱한 폴더를 가리켰다. 또 움직여도 안 깨지게
    올라가면서 찾는다.
    """
    d = _HERE
    while True:
        cand = os.path.join(d, "LocalGuard", "memory_integrity")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            raise SystemExit(
                "LocalGuard/memory_integrity 를 찾지 못했습니다. "
                "레포 안에서 실행하고 있는지 확인해 주세요.")
        d = parent


# 자기 폴더를 먼저 넣어 whistle / whistle_rpc 를 최상위 모듈로 부른다.
# 폴더 이름에 하이픈이 있어 점 표기(import a.b)로는 못 부른다.
sys.path.insert(0, _HERE)
# 러너와 core/ 는 2번 모듈이 갖고 있다.
sys.path.insert(0, _memory_integrity())

import run_session  # noqa: E402

# 휘파람 탐지기 둘. 같은 핵을 **서로 다른 경로로** 본다.
#   whistle      — 후킹 흔적 (외부에서 읽는다. 게임 안에 아무것도 안 넣는다)
#   whistle_rpc  — 호출 시점 (인프로세스 후크가 남긴 로그를 읽는다)
# 둘이 같은 주소를 독립적으로 지목한 것이 교차 검증이 됐다. MEASUREMENT.md 참고.
DETECTORS = [
    ("whistle",     "whistle",     "휘파람 후킹 (ExecFunction/vtable/사운드)"),
    ("whistle_rpc", "whistle_rpc", "도발 RPC 위반 (인프로세스 후크 로그)"),
]


def main(argv=None):
    return run_session.main_with(
        argv, DETECTORS, desc="휘파람 핵 탐지 러너",
        default_log_dir=os.path.join(_HERE, "logs", "detection"))


if __name__ == "__main__":
    sys.exit(main())
