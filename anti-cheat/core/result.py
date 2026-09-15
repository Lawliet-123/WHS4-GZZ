"""탐지기 공통 결과 계약

팀이 파이썬으로 통일하기로 했으므로, 탐지기마다 결과 모양이 다르면
합산도 오탐률 집계도 안 된다. 이 파일이 그 계약이다.

원안(팀 제안):
    {"detector": "noclip", "score": 3, "result": "SUSPICIOUS",
     "reasons": ["Wall Crossing", "Collision Disabled"]}

원안의 네 키는 그대로 두고, 측정(WBS 7.2)에 필요한 것만 얹었다.

    {
      "detector": "filesystem",
      "score": 80,                       # 0~100 고정 스케일
      "result": "DETECTED",              # score 에서 공통 규칙으로 유도
      "reasons": ["ue4ss_proxy_dll"],    # 집계용 안정 코드 (snake_case, 불변)
      "detail": "dwmapi.dll 이 게임 Binaries 폴더에 있습니다",   # 사람이 읽는 설명
      "evidence": [                      # 근거. 없으면 운영자가 조치 못 한다
        {"type": "file", "value": "C:\\\\...\\\\dwmapi.dll", "note": "UE4SS 프록시"}
      ],
      "meta": {"elapsed_ms": 412, "target_pid": 17052, ...}
    }

## 왜 이렇게 바꿨는가

**score 를 0~100 으로 고정한다.** 원안의 `score: 3` 은 몇 점 만점인지가 없다.
탐지기마다 범위가 다르면 합산이 성립하지 않는다.

**result 를 score 에서 유도한다.** 임계값이 탐지기마다 다르면 "SUSPICIOUS" 가
탐지기마다 다른 뜻이 된다. `grade()` 하나만 쓴다.

**reasons 를 안정 코드로 둔다.** 자유 문자열이면 오타 하나로 다른 항목이 되어
"어떤 신호가 몇 번 울렸는가"를 셀 수 없다. 사람이 읽는 문장은 `detail` 로 뺀다.

**ERROR 를 별도 상태로 둔다.** 이게 가장 중요하다. 오프셋이 안 맞아 GObjects 를
못 읽었을 때 그건 CLEAN 이 아니다. **검사를 못 한 것을 깨끗한 것으로 뭉개면
조용한 미탐지가 된다.** 우리가 `AActor::ProcessEvent` 오버라이드에서 겪은 실패가
정확히 이 모양이었다 — 에러가 안 나고 그냥 결과가 비어서 하루를 날렸다.
"""

import time
from dataclasses import dataclass, field

CLEAN = "CLEAN"
SUSPICIOUS = "SUSPICIOUS"
DETECTED = "DETECTED"
ERROR = "ERROR"          # 검사 자체가 성립하지 않았다. CLEAN 과 절대 섞지 않는다.

# 공통 임계값. 탐지기가 각자 정하면 등급의 의미가 깨진다.
T_SUSPICIOUS = 20
T_DETECTED = 60


def grade(score):
    """점수를 등급으로 바꾼다. 모든 탐지기가 이 함수만 쓴다."""
    if score >= T_DETECTED:
        return DETECTED
    if score >= T_SUSPICIOUS:
        return SUSPICIOUS
    return CLEAN


@dataclass
class Evidence:
    """판정의 근거 한 건.

    type: file | module | address | process | handle | window | value
    """
    type: str
    value: str
    note: str = ""

    def to_dict(self):
        return {"type": self.type, "value": self.value, "note": self.note}


@dataclass
class DetectorResult:
    detector: str
    score: int = 0
    reasons: list = field(default_factory=list)     # 안정 코드
    detail: str = ""                                # 사람이 읽는 설명
    evidence: list = field(default_factory=list)    # Evidence 목록
    meta: dict = field(default_factory=dict)
    error: str = ""                                 # 비어있지 않으면 result = ERROR
    unreachable: bool = False                       # 대상이 없었다 (ERROR 의 하위 사유)

    def add(self, code, points, detail="", evidence=None):
        """신호 하나를 기록한다.

        점수는 더하되 100 을 넘지 않는다. 신호가 여러 개면 확신이 올라가지만
        선형으로 무한히 커지면 등급이 의미를 잃는다.
        """
        if code not in self.reasons:
            self.reasons.append(code)
        self.score = min(100, self.score + points)
        if detail:
            self.detail = (self.detail + " / " + detail).strip(" /")
        for e in (evidence or []):
            self.evidence.append(e)
        return self

    def fail(self, why):
        """검사를 수행하지 못했다. CLEAN 으로 떨어뜨리지 않기 위한 경로."""
        self.error = why
        return self

    def unavailable(self, why):
        """게임이 떠 있지 않아 검사할 대상이 없었다.

        `fail()` 과 등급은 같다(ERROR). 게임이 안 켜진 세션을 CLEAN 으로
        보고하면 그것도 조용한 미탐지다.

        구분하는 이유는 하나다. **오탐률 집계에서 다루는 방식이 다르다.**
        오프셋이 틀려서 실패한 건 우리가 고쳐야 할 결함이고,
        게임이 안 켜져 있던 건 측정을 다시 하면 되는 일이다.
        팀 규격에서는 이쪽이 OFFLINE 으로 나간다.
        """
        self.unreachable = True
        return self.fail(why)

    @property
    def result(self):
        return ERROR if self.error else grade(self.score)

    def to_dict(self):
        d = {
            "detector": self.detector,
            "score": self.score,
            "result": self.result,
            "reasons": self.reasons,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.evidence:
            d["evidence"] = [e.to_dict() for e in self.evidence]
        if self.error:
            d["error"] = self.error
        if self.meta:
            d["meta"] = self.meta
        return d


def summarize(results):
    """여러 탐지기 결과를 하나로 합친다.

    최고 점수를 전체 점수로 쓴다. 평균을 쓰면 탐지기를 늘릴수록 점수가
    희석돼서, 탐지기를 추가할수록 탐지가 어려워지는 뒤집힌 동작이 된다.

    **등급은 전수 검사가 성립했을 때만 발급한다.**
    검사를 하나라도 못 했으면 ERROR 다. 부분 커버리지에 CLEAN 을 주면
    이 파일이 §모듈 주석에서 금지한 바로 그 조용한 미탐지가 된다.

    이 함수는 처음에 `grade(score) if ok else ERROR` 였다. 성공한 탐지기가
    하나만 있어도 등급을 매겨서, **검사 5개 중 4개가 실패한 세션이 CLEAN 으로
    보고됐다.** 개별 DetectorResult.result 는 계약을 지키는데 합산에서만
    깨지는, 가장 눈에 안 띄는 형태였다. 코드 리뷰에서 잡혔다.

    교훈: **주석으로 선언한 계약은 지켜지지 않는다.** 아래 검수 테스트가
    이 규칙을 강제한다.
    """
    ok = [r for r in results if r.result != ERROR]
    errored = [r for r in results if r.result == ERROR]
    score = max([r.score for r in ok], default=0)
    complete = bool(results) and not errored
    return {
        "score": score,
        "result": grade(score) if complete else ERROR,
        "complete": complete,          # 등급을 읽기 전에 이걸 먼저 봐야 한다
        "detectors": [r.to_dict() for r in results],
        "checked": len(ok),
        "errored": len(errored),
        # 숫자만으로는 조치할 수 없다. 어느 탐지기가 왜 실패했는지 넘긴다.
        "errors": [{"detector": r.detector, "error": r.error} for r in errored],
        "hits": [r.detector for r in ok if r.score >= T_SUSPICIOUS],
    }


# ═══════════════════════════════════════════════════════════════════════
# 팀 공통 출력 형식 (허송희, 2026-09-15 #일반)
# ═══════════════════════════════════════════════════════════════════════
#
#   {"session_id": "noclip_001", "module": "noclip", "timestamp_ms": 507000,
#    "status": "SUSPICIOUS", "severity": "HIGH",
#    "reasons": [...], "evidence": {...}, "score": 3}
#
#   status: NORMAL / SUSPICIOUS / DETECTED / WARNING / OFFLINE / ERROR
#
# 위쪽 DetectorResult 는 우리 내부 계약이고, 이 아래가 그것을 팀 형식으로
# 옮기는 계층이다. 둘을 합치지 않는 이유:
#
#   - 팀 형식에는 등급 유도 규칙이 없다. score 가 자유 스케일이라
#     (재민님 예시는 3점) 우리 0~100 임계값을 그대로 적용할 수 없다.
#   - 팀 형식의 evidence 는 평평한 dict 인데 우리 것은 목록이다.
#     운영자가 읽을 근거를 잃지 않으려면 변환이 필요하다.
#
# 내부 계약을 지키면서 밖으로는 팀 형식으로 나간다.

STATUS_MAP = {
    CLEAN: "NORMAL",
    SUSPICIOUS: "SUSPICIOUS",
    DETECTED: "DETECTED",
    ERROR: "ERROR",
}

# 세션 기준 시각. **모든 모듈이 같은 값을 써야** ReplayAnalyzer 에서
# 타임라인이 겹친다. 모듈마다 자기 import 시각을 쓰면 축이 어긋난다.
_SESSION_T0 = time.time()


def set_session_start(epoch_s=None):
    """세션 시작 시각을 맞춘다. 오케스트레이터(main.py)가 한 번만 부른다."""
    global _SESSION_T0
    _SESSION_T0 = time.time() if epoch_s is None else epoch_s
    return _SESSION_T0


def severity_of(res):
    """심각도. 팀 규격에 유도 규칙이 없어 점수에서 단조 증가로 만든다.

    status 와 따로 두는 이유는 재민님 예시가 SUSPICIOUS + HIGH 였기 때문이다.
    등급은 "얼마나 확실한가", 심각도는 "맞다면 얼마나 나쁜가"로 읽었다.
    """
    if res.error:
        return "NONE"
    if res.score >= T_DETECTED:
        return "HIGH"
    if res.score >= T_SUSPICIOUS:
        return "MEDIUM"
    return "LOW" if res.score else "NONE"


def _evidence_dict(res):
    """Evidence 목록을 팀 형식의 평평한 dict 로 옮긴다.

    같은 type 이 여러 개면 `module`, `module_2`, `module_3` 으로 번호를 붙인다.
    덮어쓰면 근거가 조용히 사라진다.
    """
    out = {}
    for e in res.evidence:
        key = e.type
        n = 2
        while key in out:
            key = f"{e.type}_{n}"
            n += 1
        out[key] = f"{e.value} ({e.note})" if e.note else e.value

    # 팀 형식에는 detail·meta 자리가 없다. 버리면 운영자가 조치를 못 하고,
    # 측정(7번)에서 소요 시간·표본 수를 못 센다. evidence 안에 담아 보낸다.
    if res.detail:
        out["detail"] = res.detail
    if res.error:
        out["error"] = res.error
    if res.meta:
        out["meta"] = res.meta
    return out


def to_team_event(res, session_id, timestamp_ms=None):
    """DetectorResult 를 팀 공통 이벤트로 바꾼다.

    session_id 는 한 번의 테스트를 가리키는 이름이다 (`whistle_001`).
    측정 실험에서는 손으로 지정해야 비교가 된다. 자동 생성하지 않는다.

    timestamp_ms 는 세션 시작 이후 경과 ms 로 본다. 재민님 예시의 507000 이
    epoch 로는 1970년이라 경과 시간이 맞다고 판단했다.
    **이 해석은 허송희님 확인이 필요하다.**
    """
    if timestamp_ms is None:
        timestamp_ms = int((time.time() - _SESSION_T0) * 1000)

    status = STATUS_MAP[res.result]
    if res.unreachable:
        # ERROR 의 하위 사유. 등급이 ERROR 인 것은 변하지 않는다.
        status = "OFFLINE"

    ev = {
        "session_id": session_id,
        "module": res.detector,
        "timestamp_ms": timestamp_ms,
        "status": status,
        "severity": severity_of(res),
        "reasons": list(res.reasons),
        "evidence": _evidence_dict(res),
        "score": res.score,
    }
    return ev


def _selftest():
    """계약 검수. import 시점에 돌아 회귀를 막는다.

    주석은 코드를 강제하지 못한다. 이 세 줄이 강제한다.
    """
    clean = DetectorResult("a")
    bad = DetectorResult("b").fail("오프셋 불일치")
    hit = DetectorResult("c").add("x", 70)

    assert summarize([clean, bad])["result"] == ERROR, "부분 커버리지는 등급을 받으면 안 된다"
    assert summarize([])["result"] == ERROR, "탐지기를 안 돌린 세션은 CLEAN 이 아니다"
    assert summarize([clean])["result"] == CLEAN
    assert summarize([clean, hit])["result"] == DETECTED, "합산은 최댓값이어야 한다"
    assert summarize([clean, bad])["complete"] is False

    # ── 팀 형식 변환 ────────────────────────────────────────────────
    # 여기서 지켜야 할 것은 하나다. **검사를 못 한 결과가 NORMAL 로 나가면 안 된다.**
    # 내부에서 막아둔 조용한 미탐지가 출력 변환에서 되살아나는 게 가장 흔한 회귀다.
    off = DetectorResult("d").unavailable("게임이 실행 중이 아닙니다")

    assert to_team_event(bad, "t")["status"] == "ERROR"
    assert to_team_event(off, "t")["status"] == "OFFLINE"
    assert to_team_event(clean, "t")["status"] == "NORMAL"
    assert to_team_event(hit, "t")["status"] == "DETECTED"
    for r in (bad, off):
        assert to_team_event(r, "t")["status"] != "NORMAL", "미검사는 정상이 아니다"

    # 근거가 조용히 사라지면 안 된다. 같은 type 이 겹쳐도 다 살아야 한다.
    dup = DetectorResult("e").add("x", 10, "설명", [
        Evidence("module", "a.dll"), Evidence("module", "b.dll", "주입됨")])
    got = to_team_event(dup, "t")["evidence"]
    assert got["module"] == "a.dll" and got["module_2"] == "b.dll (주입됨)"
    assert got["detail"] == "설명"

    assert to_team_event(hit, "t", timestamp_ms=507000)["timestamp_ms"] == 507000
    assert severity_of(hit) == "HIGH" and severity_of(off) == "NONE"


_selftest()
