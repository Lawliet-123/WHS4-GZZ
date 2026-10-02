
"""탐지 결과를 중앙 서버로 보낸다. Shared 라이브러리를 감싼 얇은 층이다.

## 규칙 세 가지

1. **주소가 없으면 아무 일도 하지 않는다.** `GZZ_TELEMETRY_URL` 이 없으면 꺼진 채로
   돈다. 측정용으로 탐지기만 돌리는 경우가 훨씬 많아서, 켜는 쪽이 선택이어야 한다.
2. **전송이 탐지를 막지 않는다.** 여기서 나는 모든 예외를 삼키고 상태만 남긴다.
   서버가 죽었다고 탐지 결과가 로컬에도 안 남으면 그게 더 큰 손해다.
   `run_session` 의 종료코드(0 정상 / 1 의심 / 2 검사실패)도 바꾸지 않는다.
3. **전송 상태를 숨기지 않는다.** 껐는지, 실패했는지, 몇 건이 밀렸는지 summary 에 싣는다.
   조용히 안 보내지는 것이 제일 나쁜 실패다.

## outbox 는 러너마다 따로 둔다

shared 의 대기열(SQLite)은 **한 프로세스만 쓸 수 있다.** 두 탐지기가 같은 파일을
잡으면 뒤에 온 쪽이 `ResourceBusyError` 로 큐에 넣지도 못한다.

Launcher가 GZZ_TELEMETRY_OUTBOX를 지정하면 해당 경로를 사용한다.
단독 실행에서는 기존 LocalGuard 전용 logs/outbox/ 경로를 사용한다.

## 한 번 돌고 끝나는 프로세스

`send_detection()` 은 로컬 큐에 넣고 바로 돌아온다. 실제 전송은 백그라운드 스레드다.
런처가 부르는 방식(30초마다 새 프로세스)에서는 그냥 끝내면 못 보낸 채로 남는다.
그래서 `finish()` 가 `flush` 를 한 번 기다린다. 그래도 못 보낸 건은 대기열에 남아
**다음 실행이 같은 outbox 를 열 때 같이 나간다.**

재시도 모드는 Shared 설정을 따른다. 기본값은 bounded이며,
GZZ_TELEMETRY_RETRY_MODE=persistent 설정 시 지속 재시도를 사용한다.
"""

import os
import sys

ENV_URL = "GZZ_TELEMETRY_URL"


def _find_shared():
    """`shared` 패키지를 담고 있는 폴더를 찾아 돌려준다.

    위로 올라가며 `shared/__init__.py` 를 찾는다. 지금은 레포 루트에 있지만
    처음 배포는 `shared/GZZ-Shared-0.1.0/shared/` 로 한 단계 더 들어가 있었다.
    둘 다 찾도록 해서 어느 쪽이 되든 돈다.

    경로를 추측해 박아 두면 폴더가 한 번 더 움직일 때 조용히 안 보내게 된다.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if os.path.isfile(os.path.join(here, "shared", "__init__.py")):
            return here
        nested = os.path.join(here, "shared")
        if os.path.isdir(nested):
            for name in sorted(os.listdir(nested)):
                if os.path.isfile(os.path.join(nested, name, "shared", "__init__.py")):
                    return os.path.join(nested, name)
        here = os.path.dirname(here)
    return None


class Sender:
    """전송기. 꺼져 있으면 모든 호출이 아무 일도 하지 않는다."""

    def __init__(self, outbox_dir, url=None):
        self.outbox_dir = outbox_dir
        self.url = url or os.environ.get(ENV_URL) or ""
        self.on = False
        self.sent = 0
        self.state = "off"        # off | ok | error
        self.error = ""

    def start(self):
        """켤 수 있으면 켠다. 실패해도 예외를 올리지 않는다."""
        if not self.url:
            self.state = "off"
            return False
        try:
            root = _find_shared()
            if root and root not in sys.path:
                sys.path.insert(0, root)
            import dataclasses

            from shared.config import ClientConfig
            from shared.logger import configure_client, send_detection

            cfg = ClientConfig.from_env()

            # Launcher가 지정한 outbox 경로를 우선 사용한다.
            # 단독 실행에서는 기존 LocalGuard 전용 경로를 유지한다.
            if not os.environ.get("GZZ_TELEMETRY_OUTBOX"):
                os.makedirs(self.outbox_dir, exist_ok=True)
                cfg = dataclasses.replace(
                    cfg,
                    outbox_path=os.path.join(
                        self.outbox_dir, "client.sqlite3"
                    ),
                )

            configure_client(cfg)
            self._send = send_detection
            self.on = True
            self.state = "ok"
            return True
        except Exception as e:
            self.state = "error"
            self.error = f"{type(e).__name__}: {e}"
            return False

    def send(self, shared_event):
        """한 건 보낸다(로컬 대기열에 넣는다). 실패해도 탐지를 막지 않는다."""
        if not self.on:
            return
        try:
            self._send(shared_event)
            self.sent += 1
        except Exception as e:
            self.state = "error"
            # 첫 실패 이유만 남긴다. 30초마다 같은 오류가 덮어쓰면 원인을 놓친다.
            if not self.error:
                self.error = f"{type(e).__name__}: {e}"

    def finish(self, timeout=5.0):
        """대기열을 비우고 정리한다. 상태 요약을 돌려준다."""
        out = {"state": self.state, "queued": self.sent}
        if self.error:
            out["error"] = self.error
        if not self.on:
            return out
        try:
            from shared.logger import (flush_client, get_client_status,
                                       shutdown_client)
            flush_client(timeout)
            try:
                # flush 의 참·거짓은 한 번 실패한 뒤로는 계속 거짓이라 상태로 못 쓴다.
                # 남은 건수를 직접 본다.
                st = get_client_status()
                out["pending"] = getattr(st, "pending", None)
                out["failed"] = getattr(st, "failed", None)
                if out.get("failed"):
                    # 영구 실패는 자동으로 재개되지 않는다. 조용히 지나가면 안 된다.
                    out["state"] = "error"
                    out.setdefault("error", f"전송 실패 {out['failed']}건 (재시도 안 함)")
            except Exception:
                pass
            shutdown_client(timeout)
        except Exception as e:
            out["state"] = "error"
            out.setdefault("error", f"{type(e).__name__}: {e}")
        return out


def make(outbox_dir):
    """켜져 있으면 시작된 Sender, 아니면 꺼진 Sender."""
    s = Sender(outbox_dir)
    s.start()
    return s
