"""런처 하트비트 — 모듈 전체의 생존 상태를 중앙 서버에 **한 발신자로** 보낸다.

## 왜 런처가 보내나

input_signature 의 TELEMETRY_CONTRACT.md 가 이미 정해 두었다: 런처가 여러 구성 요소를
관리할 때는 런처 한 곳에서 상태를 모아 한 발신자로 보내고, 자식 YARA 의
`--heartbeat-url` 은 비워 중복 발신을 피한다. 서버 쪽 수신(`POST /api/heartbeat`)과
대시보드 조회는 재민님 #91(10/4)로 들어왔고, 런처 자동 전송만 남아 있었다.

하트비트는 탐지 점수가 아니다. "이 PC 의 안티치트가 지금 돌고 있는가" 만 알린다.
끊기면 감시 공백이지 핵 판정 근거가 아니다(server/receiver/HEARTBEAT.md).

## 무엇을 쓰나

보내는 코드는 동효님 `client/LocalGuard/input_signature/heartbeat.py` 의
`HeartbeatClient` 를 **그대로** 쓴다. meccha-heartbeat-3 형식, 순번, 서버 ACK 검증
(같은 session·client·sequence 가 돌아와야 성공), 로컬 JSONL, 리다이렉트 거부까지
들어 있고, 서버 수신기와 실제 HTTP 로 맞춰 본 구현이다. 복사하면 두 벌이 갈라지므로
파일 경로로 불러온다.

## 무엇을 채우나

components = `launcher` + 모듈마다 하나. 상태는 **런처가 실제로 확인한 프로세스
상태**(process_manager)로만 채운다. 시작만 한 것을 running 으로 올리거나 오래된
상태를 정상으로 남기지 않는다(계약 문서). 하트비트를 보내기 직전마다(probe) 다시 읽는다.

    PENDING  starting   게임을 기다리는 중
    RUNNING  running
    DONE     running    주기 검사가 정상으로 한 바퀴 끝나고 다음 주기를 기다림
                        (주기 없는 단발이면 stopped)
    WARN     degraded   검사 실패(종료코드 2)나 비정상 종료 뒤 다음 주기를 기다림
    RESTART  degraded   되살리는 중
    FAILED   failed
    STOPPED  stopped
    MISSING  unknown    (필수 아님) 파일이 없어 못 띄움
    SKIPPED  stopped    (필수 아님) 관리자 권한 등 조건이 안 맞음

`launcher` 는 런처 본체다. 메인 루프가 10초 넘게 안 돌면 degraded 로 낮춘다
(상태를 읽는 쪽이 멈췄는데 모듈 상태를 그대로 믿을 수 없다). 게임을 기다리거나
정리하는 단계는 원래 루프가 안 도니 예외다(details.phase 로 보인다).

## 설정

    서버 주소  MECCHA_TELEMETRY_HEARTBEAT_URL (전체 URL)
               없으면 GZZ_TELEMETRY_URL + "/api/heartbeat", 둘 다 없으면 로컬 기록만
    토큰       MECCHA_HEARTBEAT_TOKEN  (탐지용 GZZ_TELEMETRY_TOKEN 과 따로다)
    로컬 기록  logs/heartbeat/<세션>_<t0 ms>.jsonl (항상)

원격은 HTTPS 만, http 는 127.0.0.1·localhost 시험용만 허용한다(HeartbeatClient 가 검사).
하트비트에 문제가 생겨도 런처와 탐지는 멈추지 않는다 — 화면 "서버" 칸에 이유를 띄운다.
"""

import importlib.util
import os
import time
from typing import Optional

import registry
from modules import ONESHOT, REPO
from process_manager import (DONE, FAILED, MISSING, PENDING, RESTARTING, RUNNING,
                             SKIPPED, STOPPED, WARN)

CLIENT_PATH = os.path.join(REPO, "client", "LocalGuard", "input_signature", "heartbeat.py")
INTERVAL_S = 5.0          # 계약: 5~10초
TIMEOUT_S = 3.0           # 주기보다 짧아야 한다(HeartbeatClient 가 검사)
LOOP_STALE_S = 10.0       # 런처 메인 루프가 이만큼 안 돌면 launcher 를 degraded 로

_STATUS = {
    PENDING: "starting",
    RUNNING: "running",
    WARN: "degraded",
    RESTARTING: "degraded",
    FAILED: "failed",
    STOPPED: "stopped",
    MISSING: "unknown",
    SKIPPED: "stopped",
}


def endpoint_from_env(environ=None) -> Optional[str]:
    env = os.environ if environ is None else environ
    url = (env.get("MECCHA_TELEMETRY_HEARTBEAT_URL") or "").strip()
    if url:
        return url
    base = (env.get("GZZ_TELEMETRY_URL") or "").strip()
    return base.rstrip("/") + "/api/heartbeat" if base else None


def _load_client_class():
    spec = importlib.util.spec_from_file_location("meccha_heartbeat_client", CLIENT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(CLIENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.HeartbeatClient


def component_of(st):
    """ModuleState 하나를 (status, required, pid, details) 로 바꾼다."""
    m = st.module
    if st.status == DONE:
        status = "running" if (m.mode == ONESHOT and m.every_s) else "stopped"
    else:
        status = _STATUS.get(st.status, "unknown")
    required = st.status not in (MISSING, SKIPPED)
    # 자유 문장(st.detail)은 넣지 않는다. 로컬 경로·사용자 이름이 섞일 수 있다.
    details = {"launcher_status": st.status, "mode": m.mode,
               "runs": st.runs, "restarts": st.restarts}
    # 주기 검사(run_session 계약)의 종료코드는 탐지 결과다: 0 정상 / 1 의심 / 2 검사 실패.
    # 하트비트에 실으면 "이 플레이어에게서 의심이 나왔다" 가 scoring 을 거치지 않고 나간다
    # (계약 문서: 하트비트는 생존·신선도만, 10/4 검토에서 재현). 검사 실패는 이미 WARN ->
    # degraded 로 보인다. 상주 모듈의 종료코드만 생존 정보로 남긴다.
    if st.last_code is not None and m.mode != ONESHOT:
        details["last_code"] = st.last_code
    if st.started_by:
        details["started_by"] = st.started_by
    return status, required, st.pid, details


class LauncherHeartbeat:
    """ProcessManager 상태를 HeartbeatClient 에 넣어 주기적으로 보낸다."""

    def __init__(self, pm, session: str, player: str, t0: float, environ=None,
                 client_class=None, interval_s: float = INTERVAL_S,
                 timeout_s: float = TIMEOUT_S):
        env = os.environ if environ is None else environ
        self.pm = pm
        self.error: Optional[str] = None
        self.client = None
        self.phase = "running"
        self.last_tick = time.monotonic()
        self.endpoint = endpoint_from_env(env)
        self.token = env.get("MECCHA_HEARTBEAT_TOKEN") or None
        t0_ms = int(round(t0 * 1000))
        # 대시보드 조회(GET /api/dashboard/heartbeat/{session_id}/{client_id})는 client_id 를
        # 알아야 한다. 무작위면 아무도 못 찾는다(10/4 검토). 런처 실행마다 달라야 하므로
        # (순번이 1 부터 다시 시작하면 새 client_id — HEARTBEAT.md) 세션 시작 시각을 붙인다.
        self.client_id = f"launcher-{t0_ms}"
        self.log_path = os.path.join(registry.LOG_DIR, "heartbeat", f"{session}_{t0_ms}.jsonl")
        try:
            cls = client_class or _load_client_class()
            # timestamp_ms 를 다른 모듈처럼 런처 세션 시작(t0) 기준으로 맞춘다.
            now_ms = time.monotonic_ns() // 1_000_000
            origin = now_ms - max(0, int((time.time() - t0) * 1000))
            self.client = cls(session_id=session, player_id=player, log_path=self.log_path,
                              endpoint=self.endpoint, token=self.token,
                              interval_seconds=interval_s, timeout_seconds=timeout_s,
                              client_id=self.client_id, origin_ms=origin)
            # 보내기 직전마다 실제 상태를 다시 읽는다. probe 하나가 모듈 전체를 채운다.
            self.client.register_probe("launcher", self._probe)
        except Exception as e:
            self.client = None
            self.error = f"{type(e).__name__}: {e}"[:120]

    # ── 상태 채우기 ──────────────────────────────────────────────────────
    def _refresh_modules(self):
        for st in list(self.pm.states.values()):
            status, required, pid, details = component_of(st)
            if self.phase == "stopped" and status in ("running", "degraded", "starting"):
                # 런처가 정리를 마쳤으면 다음 주기를 기다리던 검사도 더는 안 돈다.
                # 마지막 결과(WARN 등)는 details.launcher_status 에 남는다.
                status = "stopped"
            self.client.update_component(st.name, status, required=required,
                                         pid=pid, details=details)

    def _probe(self):
        self._refresh_modules()
        age = time.monotonic() - self.last_tick
        status = "running"
        if self.phase == "running" and age > LOOP_STALE_S:
            status = "degraded"
        elif self.phase == "stopped":
            status = "stopped"
        return {"status": status, "pid": os.getpid(),
                "details": {"phase": self.phase, "loop_age_ms": int(age * 1000),
                            "modules": len(self.pm.states)}}

    # ── 런처가 부르는 것 ─────────────────────────────────────────────────
    def start(self) -> None:
        if not self.client:
            return
        try:
            self.client.start()        # 첫 하트비트를 바로 보내고 백그라운드 전송 시작
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:120]

    def tick(self) -> None:
        """메인 루프가 돌 때마다 부른다. 런처가 살아서 상태를 읽고 있다는 표시."""
        self.last_tick = time.monotonic()

    def set_phase(self, phase: str) -> None:
        """런처 단계: waiting_game(게임 대기) / running / stopping / stopped.

        메인 루프가 안 도는 게 정상인 단계(게임 대기, 정리)에서는 degraded 로 낮추지 않는다.
        """
        self.phase = phase
        self.tick()

    def stopping(self) -> None:
        """정리 시작. 정리하는 동안 메인 루프가 안 돌아도 degraded 로 보지 않는다."""
        self.set_phase("stopping")

    def close(self) -> None:
        """정리가 끝난 뒤 최종 상태(stopped)를 한 건 보내고 닫는다."""
        if not self.client:
            return
        self.phase = "stopped"
        try:
            # 마지막 한 건은 probe 를 안 돌리므로, 정리 결과를 먼저 넣는다.
            self.client.update_component("launcher", "stopped", pid=os.getpid(),
                                         details={"phase": "stopped",
                                                  "modules": len(self.pm.states)})
            self._refresh_modules()
            self.client.stop(final_status="stopped")
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:120]

    def close_timeout_s(self) -> float:
        """close() 가 서버를 기다릴 수 있는 최대 시간(진행 중 전송 + 마지막 전송)."""
        return 2 * TIMEOUT_S if (self.client and self.endpoint) else 0.0

    def server_text(self) -> str:
        """화면 "서버" 칸에 띄울 말."""
        if self.error and not self.client:
            return f"하트비트 꺼짐 ({self.error})"
        if not self.endpoint:
            return "꺼짐 (서버 주소 없음, 로컬 기록만)"
        c = self.client
        with c.lock:
            t = dict(c.transport)
        if c.failure or self.error:
            return f"하트비트 멈춤 ({c.failure or self.error})"
        hint = "" if self.token else ", 토큰 없음(MECCHA_HEARTBEAT_TOKEN)"
        if t["consecutive_failures"]:
            return f"전송 실패 {t['consecutive_failures']}회 ({t['last_error_type']}{hint})"
        if t["last_success_sequence"]:
            return "연결됨"
        return "확인 중" + hint
