# Launcher

사용자가 LocalGuard·SelfDefense·KernelWatcher·탐지기·게임을 각각 찾아 실행하지
않도록, 이것 하나가 순서대로 켜고 상태를 보여주고 끝날 때 정리한다.

```bash
python client/Launcher/main.py
```

| 옵션 | 뜻 |
|---|---|
| `--session ID` | 세션 이름 (기본: 시각으로 자동 생성) |
| `--player ID` | 플레이어 식별자 (기본 `player_001`) |
| `--only a,b` | 그 모듈만 실행 |
| `--no-launch-game` | 게임은 내가 직접 켠다. 뜰 때까지 기다리기만 |
| `--wait-game SEC` | 게임을 기다리는 시간 (기본 180초) |
| `--status-every SEC` | 상태 화면 간격 (기본 10초) |

커널 모듈을 쓰려면 **관리자 권한**으로 실행해야 한다. 아니면 그 모듈만 건너뛴다.

---

## 내 모듈을 붙이려면 — `modules.py` 에 한 줄

런처 본체(`main.py`, `process_manager.py`)는 건드릴 필요가 없다.

```python
Module(
    name="input_signature",
    owner="3번 (동효)",
    argv=[PY, "client/LocalGuard/input_signature/main.py",
          "--session", "{session}", "--player", "{player}"],
    mode=CONTINUOUS,     # 또는 ONESHOT + every_s=30.0
    needs_game=True,
    needs_admin=False,
)
```

자리표시자 `{session}` `{player}` `{t0}` `{window}` 는 런처가 채운다.

| 항목 | 뜻 |
|---|---|
| `mode=CONTINUOUS` | 자기가 알아서 계속 돈다. 런처는 살아 있는지만 본다 |
| `mode=ONESHOT` + `every_s` | 한 번 돌고 끝난다. 런처가 그 주기로 다시 부른다 |
| `needs_game=False` | 게임보다 **먼저** 뜬다 (SelfDefense·KernelWatcher) |
| `needs_admin=True` | 관리자 권한이 없으면 건너뛴다 |

**실행 방식이 모듈마다 다르니 확인하고 적어야 한다.** 예를 들어 `external_access`
는 상대 import 를 써서 `python -m client.LocalGuard...` 로만 돌고, 직접 실행하면
`ImportError` 가 난다. 등록하기 전에 그 명령을 손으로 한 번 돌려보는 게 빠르다.

### 주기 실행 모듈이라면 `{t0}` 를 꼭 받아 주세요

`ONESHOT` 은 실행할 때마다 새 프로세스다. 각자 자기 시작 시각을 기준으로
`timestamp_ms` 를 매기면 **실행이 바뀔 때마다 시각이 0 으로 되돌아가고**
ReplayAnalyzer 에서 타임라인이 깨진다. 그래서 런처가 세션 전체의 기준 시각을
`{t0}` 로 넘긴다. 받아서 기준으로 쓰면 된다
(`memory_integrity/run_session.py` 의 `--t0` 참고).

---

## 파일 나눔

| 파일 | 담당 | 하는 일 |
|---|---|---|
| `main.py` | 랑언 | 전체 순서 |
| `process_manager.py` | 랑언 | 실행·생존 확인·종료 |
| `modules.py` | 공용 | 모듈 등록표 |
| `ui.py` | **동효** | 상태 화면 (지금은 콘솔 표) |
| `game_launcher.py` | **동효** | 게임 찾기·실행 (지금은 최소 동작) |

동효님 두 파일은 **인터페이스만 맞춰서 최소 버전**을 채워뒀습니다. 런처가 돌아가야
다른 분들이 자기 모듈을 붙여볼 수 있어서 먼저 만든 것이고, 안을 통째로 바꾸셔도
`main.py` 는 손댈 필요가 없습니다. 지켜야 할 함수는 각 파일 맨 위에 적어뒀습니다.

`ui.render(rows, ctx)` 의 `rows` 한 줄:

```python
{"name": "memory_integrity", "owner": "2번 (재민·랑언)", "status": "RUNNING",
 "mode": "oneshot", "runs": 3, "last_code": 1, "uptime_s": 12.4,
 "detail": "의심 발견", "log": "...logs/memory_integrity.log"}
```

`status`: `MISSING` / `SKIPPED` / `PENDING` / `RUNNING` / `DONE` / `WARN` / `FAILED` / `STOPPED`
서버 연결 상태는 `ctx["server"]` 로 들어갑니다(하트비트 붙이면 그 값만 채우면 됩니다).

---

## 워치독(4번)과 역할이 겹치지 않게

둘 다 "모듈이 죽었는지" 를 보지만 하는 일이 다르다.

| | 하는 일 |
|---|---|
| **런처** | 실행하고, 상태를 보고, 끝낼 때 정리한다. **되살리지 않는다** |
| **워치독** | 죽었는지 감지해서 보고하고, 필요하면 되살린다 |

둘 다 재시작하면 같은 모듈을 두 번 띄우거나, 서로 죽인 것을 되살리려고 싸운다.
그래서 `process_manager.py` 에는 재시작 코드가 없다. 주기 실행(`ONESHOT`)은
"죽어서 되살리는 것"이 아니라 "원래 주기적으로 도는 검사"라서 다르다.

---

## 모듈 출력은 어디에

콘솔에 같이 찍으면 읽을 수 없고, 무엇보다 Windows 파이프 버퍼가 가득 차면
자식 프로세스가 멈춘다. 그래서 모듈마다 따로 보낸다.

```
client/Launcher/logs/<모듈>.log
```

실행할 때마다 헤더(`[launcher] 시각  run #N` + 실제 명령)를 남기므로, 안 붙을 때
그 파일을 보면 무슨 명령이 어떻게 실패했는지 바로 나온다. 탐지 결과 자체는
각 모듈이 원래 쓰던 자리(`logs/detection/` 등)에 그대로 쌓인다.

---

## 안 만들어진 모듈이 있어도 멈추지 않는다

2026-09-27 기준 `SelfDefense`, `KernelWatcher`, `input_signature` 는 폴더만 있다.
런처는 이 모듈들을 `MISSING` 으로 보여주고 나머지를 계속 띄운다. 조용히 넘기지도
않는다 — 아직 안 만든 것과, 만들었는데 안 붙는 것은 원인이 다르기 때문이다.
