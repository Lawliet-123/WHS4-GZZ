# LocalGuard — input_signature

역할표 3번 담당 범위인 **알려진 핵 EXE 해시 대조, YARA 메모리 시그니처 검사, 하트비트 송신 클라이언트**를 한 폴더에 묶었다. `memory_integrity/`나 Launcher·TelemetryServer·HWID 생성기의 구현은 수정하지 않는다. 게임 값을 쓰거나 핵을 자동 차단·밴하지 않는다.

## 구성

| 파일 | 역할 |
|---|---|
| `rules/known_cheat_executables.json` | 팀의 WHS4-GZZ 저장소에서 확인한 핵 EXE 빌드 4개의 정확한 파일 크기·SHA-256 |
| `executable_hashes.py` · `hash_monitor.py` | 게임과 같은 Windows 세션에서 실행 중인 프로세스의 디스크 EXE를 5초 간격으로 해시 대조 |
| `rules/repository_cheats.yar` · `yara_scanner.py` | 알려진 팀 핵의 게임·외부 후보 프로세스 메모리 시그니처 검사 및 실행 진입점 |
| `heartbeat.py` · `heartbeat.schema.json` | 5~10초 간격 상태 기록, 선택적 HTTPS 전송, 1번이 제공한 HWID 동봉 |
| `windows_process.py` | 읽기 전용 프로세스 식별·게임 DLL 범위 확인 지원 |
| `replay_events.py` · `event.schema.json` | 이 모듈의 7개 필드 실험용 Event를 로컬에 기록 |

`raw_score`는 분석용 원시 신호다. 해시의 0/1과 YARA의 0/3은 다른 척도이므로 합산하거나 밴 임계값으로 사용하지 않는다. 이 모듈은 확정 판정이나 서버 scoring을 하지 않는다. 해시는 **정확히 같은 EXE 빌드**만 찾으며, DLL·Python 스크립트·재빌드된 파일은 이 방식으로 확인할 수 없다. YARA는 읽을 수 있는 메모리의 알려진 패턴만 확인한다. 접근 거부·타임아웃·부분 검사는 정상 0점으로 채우지 않는다.

## 실행과 검증

Windows의 64비트 Python 3.10~3.13과 실행 중인 `PenguinHotel-Win64-Shipping.exe`가 필요하다. 아래는 이 PC에서 검증한 3.12 예시이며, 팀원의 설치 버전에 맞게 `-3.12`만 바꿀 수 있다. 이 폴더에서 실행한다.

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
& .\.venv\Scripts\python.exe .\yara_scanner.py --auto-external-python
```

기본 실행은 게임 PID를 자동 탐색하고 세션 ID를 자동 생성하며, Ctrl+C까지 반복한다. 테스트 라벨은 기본 `unknown`이다. 실험용 라벨이 필요하면 `--session-id`, `--label normal|cheat`, `--cheat-name`, `--player-id`, `--seconds`를 명시한다. 게임 프로세스가 여러 개면 `--pid`를 준다. 인자 전체는 `python yara_scanner.py --help`에서 확인한다.

결과는 `sessions/<session-id>/`의 `manifest.json`, `events.jsonl`, `raw/yara_scan.jsonl`, `raw/executable_hashes.jsonl`, `raw/heartbeat.jsonl`에 남는다. `sessions/`는 개인 PC 정보가 들어갈 수 있어 Git 추적에서 제외했다. 로그를 팀에 공유할 때는 PID·로컬 경로 등을 검토한다.

테스트는 다음과 같이 실행한다. 네이티브 fixture 검사 한 건은 C 컴파일러가 없으면 건너뛴다. 실험용 세션 생성·검증 도구는 `tests/`에만 있다.

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## HWID·TelemetryServer 연동 경계

1번 모듈이 `{"hwid":"<64자리 소문자 16진수>"}` JSON 파일을 제공한다고 **임시로 가정**했다. 이 모듈은 HWID를 계산하거나 원시 하드웨어 정보를 수집하지 않는다. 서버 URL을 설정하려면 HWID 파일도 지정해야 한다.

```powershell
$env:MECCHA_HWID_FILE = 'C:\path\from-component-1\hwid.json'
$env:MECCHA_TELEMETRY_HEARTBEAT_URL = 'https://telemetry.example/api/heartbeat'
$env:MECCHA_HEARTBEAT_TOKEN = 'receiver가 발급한 토큰'
& .\.venv\Scripts\python.exe .\yara_scanner.py --heartbeat-interval 5
```

URL을 설정하지 않으면 하트비트는 로컬 JSONL에만 남는다. 원격 URL은 HTTPS가 필수이며 HTTP는 loopback 테스트에만 허용한다. HWID는 **전송 본문에만** 포함하고 로컬 하트비트 파일에는 기록하지 않는다. 서버는 같은 `session_id`·`client_id`·`sequence`로 확인 응답해야 한다. 요청·응답 형식은 [`TELEMETRY_CONTRACT.md`](TELEMETRY_CONTRACT.md)에 명시한 **임시 계약**으로, 1번·6번 담당자와 합의 후 확정해야 한다.

현재 구현된 것은 **하트비트 송신**이다. 탐지 Event를 6번 서버에 올리는 `/events` 계약·업로더, 중앙 scoring, 최종 Launcher의 단일 집계 하트비트는 아직 연결되지 않았다. 특히 기존 `memory_integrity/core/result.py`의 팀 Event에는 `window_id`, `sample_id`, `status`, `severity`가 있는데 이 모듈의 `events.jsonl`에는 없다. **두 형식을 그대로 한 스트림으로 합치면 안 된다.** 6번·7번과 최종 Event 계약 및 점수 척도를 확정한 뒤 변환기를 붙여야 한다. Launcher는 당분간 이 스캐너를 별도 프로세스로 실행하고 이 모듈의 `events.jsonl`과 하트비트 상태를 소비할 수 있다. 최종적으로 Launcher가 세션 전체의 하트비트를 보내면 이 자식 프로세스에는 서버 URL을 주지 않아 중복 전송을 피해야 한다.

## 업로드 범위

이 폴더의 소스·규칙·스키마·테스트·문서만 PR에 포함한다. `.venv/`, `sessions/`, `fixture-sessions/`, `__pycache__/`, 게임 파일, 치트 실행 파일, 로컬 HWID 파일과 토큰은 올리지 않는다. 게임 읽기 핸들 보유자 탐색과 `client/LocalGuard/memory_integrity/`는 이번 변경 범위가 아니다.
