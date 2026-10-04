# LocalGuard — input_signature

역할표 3번 담당 범위인 **알려진 핵 EXE 해시 대조, YARA 메모리 시그니처 검사, 하트비트 송신 클라이언트**를 한 폴더에 묶었다. 탐지 결과는 로컬 파일에 남기고, 중앙 전송 설정이 있으면 `shared.logger`의 대기열에도 넣는다. 게임 값을 쓰거나 핵을 자동 차단·밴하지 않는다.

## 구성

| 파일 | 역할 |
|---|---|
| `rules/known_cheat_executables.json` | 팀의 WHS4-GZZ 저장소에서 확인한 핵 EXE 빌드 4개의 정확한 파일 크기·SHA-256 |
| `executable_hashes.py` · `hash_monitor.py` | 게임과 같은 Windows 계정·세션으로 확인된 실행 중 프로세스의 디스크 EXE를 5초 간격으로 해시 대조 |
| `rules/repository_cheats.yar` · `yara_scanner.py` | 알려진 팀 핵의 게임·외부 후보 프로세스 메모리 시그니처 검사 및 실행 진입점 |
| `heartbeat.py` · `heartbeat.schema.json` | 5~10초 간격 상태 기록과 선택적 HTTPS 전송 |
| `windows_process.py` | 읽기 전용 프로세스 식별·게임 DLL 범위 확인 지원 |
| `replay_events.py` · `event.schema.json` | 7개 필드 Event를 로컬에 기록하고, 설정된 경우 `shared.logger`에 전달 |

`raw_score`는 분석용 원시 신호다. 해시의 0/1과 YARA의 0/3은 다른 척도이므로 합산하거나 밴 임계값으로 사용하지 않는다. 이 모듈은 확정 판정이나 서버 scoring을 하지 않는다. 해시는 **정확히 같은 EXE 빌드**만 찾으며, DLL·Python 스크립트·재빌드된 파일은 이 방식으로 확인할 수 없다. YARA는 읽을 수 있는 메모리의 알려진 패턴만 확인한다. 접근 거부·타임아웃·부분 검사는 정상 0점으로 채우지 않는다.

해시 검사는 Windows가 제공한 소유자 SID로 게임 계정을 확인한다. 같은 로그인 세션에 있어도 다른 계정의 프로세스는 검사 범위 밖이다. SID를 얻지 못한 프로세스는 이름으로 예외 처리하거나 게임 계정이라고 추측하지 않고 `owner_unavailable_count`에 별도 기록한다. 이들 및 다른 계정에서 실행된 핵은 해시 검사의 사각지대이며, `complete: true`는 **소유자가 확인된 게임 계정·세션의 이번 스냅샷**에만 적용된다. 실제 검사 대상에서 접근 거부가 나면 `complete: false`이며 정상 0점 Event를 만들지 않는다.

## 실행과 검증

Windows의 64비트 Python 3.10~3.13과 실행 중인 `PenguinHotel-Win64-Shipping.exe`가 필요하다. 아래는 이 PC에서 검증한 3.12 예시이며, 팀원의 설치 버전에 맞게 `-3.12`만 바꿀 수 있다. 이 폴더에서 실행한다.

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
& .\.venv\Scripts\python.exe .\yara_scanner.py --auto-external-python
```

기본 실행은 게임 PID를 자동 탐색하고 세션 ID를 자동 생성하며, Ctrl+C까지 반복한다. 테스트 라벨은 기본 `unknown`이다. 실험용 라벨이 필요하면 `--session-id`, `--label normal|cheat`, `--cheat-name`, `--player-id`, `--seconds`를 명시한다. 게임 프로세스가 여러 개면 `--pid`를 준다. 인자 전체는 `python yara_scanner.py --help`에서 확인한다.

런처가 실행할 때는 `--session-id {session} --player-id {player} --t0 {t0}`를 넘긴다. `--t0`는 런처가 세션을 시작한 Unix epoch **초** 값이며, 1일보다 오래됐거나 미래인 값은 설정 오류로 거부한다. 지정하면 Event, 하트비트, 수동 ON/OFF 표식의 `timestamp_ms`가 같은 세션 시작 시각을 기준으로 기록된다. 지정하지 않은 독립 실행은 기존처럼 이 검사기의 시작 시각을 기준으로 한다. `--seconds`는 `--t0`가 있어도 검사기 **자체 실행 시간**을 제한한다.

결과는 `sessions/<session-id>/`의 `manifest.json`, `events.jsonl`, `raw/yara_scan.jsonl`, `raw/executable_hashes.jsonl`, `raw/heartbeat.jsonl`에 남는다. `sessions/`는 개인 PC 정보가 들어갈 수 있어 Git 추적에서 제외했다. 로그를 팀에 공유할 때는 PID·로컬 경로 등을 검토한다.

중앙 탐지 전송을 사용하려면 **런처가 실행하는 바로 그 Python**에 `requirements.txt`를 설치하고, 런처 환경에 `GZZ_TELEMETRY_URL`(HTTPS 서버 origin)과 `GZZ_TELEMETRY_TOKEN`을 제공한다. 스캐너는 시작할 때 한 번 `configure_client(ClientConfig.from_env())`를 호출한다. 매 평가 결과는 기존 `events.jsonl`에 기록한 뒤 `send_detection()`으로 보낸다. 종료 시 `flush_client()`와 `shutdown_client()`를 호출한다. `GZZ_TELEMETRY_OUTBOX`를 별도로 지정하지 않으면 이 모듈 전용 `telemetry-outbox/client.sqlite3`를 사용한다. 다른 모듈과 같은 outbox를 공유하지 않는다. `send_detection()`의 `queued`는 **로컬 대기열 저장**이지 서버 수신 성공이 아니다. 전송 오류는 스캐너의 표준 오류 및 런처의 `input_signature.log`에 남고, 탐지 점수나 로컬 결과를 바꾸지 않는다. 실제 중앙 저장은 receiver와 함께 종단 테스트해야 한다.

테스트는 다음과 같이 실행한다. 네이티브 fixture 검사 한 건은 C 컴파일러가 없으면 건너뛴다. 실험용 세션 생성·검증 도구는 `tests/`에만 있다.

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## TelemetryServer 연동 경계

하트비트 수신 URL을 설정하면 로컬 기록과 함께 같은 상태 스냅샷을 서버에도 전송한다. URL을 설정하지 않으면 로컬 JSONL에만 남는다.

```powershell
$env:MECCHA_TELEMETRY_HEARTBEAT_URL = 'https://telemetry.example/api/heartbeat'
$env:MECCHA_HEARTBEAT_TOKEN = 'receiver가 발급한 토큰'
& .\.venv\Scripts\python.exe .\yara_scanner.py --heartbeat-interval 5
```

원격 URL은 HTTPS가 필수이며 HTTP는 loopback 테스트에만 허용한다. 서버는 같은 `session_id`·`client_id`·`sequence`로 확인 응답해야 한다. 요청·응답 형식은 [`TELEMETRY_CONTRACT.md`](TELEMETRY_CONTRACT.md)에 명시한 **임시 계약**으로, 서버·Launcher 담당자와 합의 후 확정해야 한다.

탐지 Event의 중앙 경로는 이제 `/events`가 아니라 `shared`의 `POST /api/detection`이다. `server/receiver/router.py`에 해당 수신 라우터도 있다. 다만 중앙 scoring 연결과 실제 배포 서버에서의 저장 성공은 별도 검증이 필요하다. 하트비트의 `POST /api/heartbeat`는 이 탐지 API와 **별개**이며, 수신 API가 확정되지 않아 현재는 로컬 기록 및 임시 계약에 따른 선택적 송신까지만 지원한다. 최종 Launcher의 단일 집계 하트비트도 아직 연결되지 않았다. 런처 등록표는 이미 `yara_scanner.py`에 `--session-id {session} --player-id {player}`를 전달하지만, `--t0 {t0}`는 아직 전달하지 않는다. 이 스캐너는 같은 세션 폴더를 다시 사용할 수 없으므로 재시작 정책도 별도 협의가 필요하다. 나중에 Launcher가 세션 전체 하트비트를 보내면 자식 스캐너의 `--heartbeat-url`은 비워 중복 발신을 피해야 한다.

## 업로드 범위

이 폴더의 소스·규칙·스키마·테스트·문서만 포함한다. `.venv/`, `sessions/`, `telemetry-outbox/`, `fixture-sessions/`, `__pycache__/`, 게임 파일, 치트 실행 파일과 토큰은 올리지 않는다. 게임 읽기 핸들 보유자 탐색과 `client/LocalGuard/memory_integrity/`는 이번 변경 범위가 아니다.
