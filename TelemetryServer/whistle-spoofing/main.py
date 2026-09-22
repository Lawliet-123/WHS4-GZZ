"""휘파람(Provocation) 핵 탐지 러너.

9/20 회의에서 핵마다 담당자가 정해졌고(회의록 「중간 회의」), 구조안대로
핵마다 자기 폴더에 자기 main.py 를 둔다. **휘파람은 내(조랑언) 담당이다.**
회의에 참석하지 못해 회의록 목록과 구조안 핵 폴더 목록 양쪽에 휘파람이
빠져 있어서, 이미 머지된 `modules/whistle-spoofing` 과 같은 이름으로 만들었다.

## 왜 러너를 따로 두는가

탐지기 자체는 세션 묶기·공통 형식 변환을 몰라도 되게 두었다. 그 일은
2번 러너(`LocalGuard/memory_integrity/run_session.py`)가 이미 하고 있어서,
여기서는 **등록표만 바꿔서 그대로 재사용한다.** 로직을 복사하면 세션 id 와
기준 시각(t0)을 맞추는 코드가 두 벌이 되고, 둘이 어긋나는 순간
ReplayAnalyzer 에서 타임라인이 안 겹친다.

## 사용법

    python main.py --session rpc_001
    python main.py --session rpc_001 --log-dir ../logs/detections --log-name whistle-spoofing

두 번째 형태가 구조안의 "logs/detections/각자핵.jsonl 에 계속 추가" 방식이다.
**기본값으로 박지 않았다** — 6번 scoring 이 읽을 폴더명·파일 단위가 확정되면
그때 기본값을 바꾼다.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))

# 자기 폴더를 먼저 넣어 whistle / whistle_rpc 를 최상위 모듈로 부른다.
# 폴더 이름에 하이픈이 있어 점 표기(import a.b)로는 못 부른다.
sys.path.insert(0, _HERE)
# 러너와 core/ 는 2번 모듈이 갖고 있다.
sys.path.insert(0, os.path.join(_REPO, "LocalGuard", "memory_integrity"))

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
