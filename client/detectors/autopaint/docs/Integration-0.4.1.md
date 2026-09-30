# AutoPaint 0.4.1 모듈 배치·실행

0.4.0 전달본은 팀 저장소 구조까지 포함해 main.py가 detector/autopaint 안에 있었다.
0.4.1 ZIP은 모듈 자체의 내용만 담으며 main.py가 ZIP 최상단에 있다.

## 모듈 위치

ZIP을 전용 AutoPaint 폴더에 풀고 그 폴더 전체를 팀에서 지정한 모듈 위치로 옮긴다.

```text
AutoPaint 모듈 폴더/
  main.py
  gzz_anticheat/
  README.md
  pyproject.toml
  docs/
  tests/
```

Launcher는 이 폴더의 main.py를 실행한다. main.py만 복사하지 않고 gzz_anticheat도 함께 둔다.
공통 shared와 GZZPaintObserver는 모듈 밖에서 관리한다.
main.py 내용과 탐지·전송 동작은 0.4.0과 같다. 버전 표시는 0.4.1이다.

## 서버 없이 실행

main.py가 보이는 모듈 폴더에서 PowerShell을 연다.
게임에 GZZPaintObserver가 설치돼 있으면 실제 경로로 바꿔 실행한다.

```powershell
py main.py --telemetry off --session-id normal_host_001 --player-id player_042 --label NORMAL --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

`--session-id`는 필수다. 기본 실행은 상주형이며 Ctrl+C로 종료한다.
Lua 옵션을 생략하면 DLL·런타임 검사만 수행한다. 도움말은 `py main.py --help`로 확인한다.

## 공통 shared 연결

공통 shared는 한 벌만 두고 Launcher가 해당 패키지의 부모 경로를 PYTHONPATH로 전달한다.
예를 들어 저장소가 아래처럼 구성됐다면 PYTHONPATH는 `D:\TeamProject`다.

```text
D:\TeamProject\shared\__init__.py
D:\TeamProject\shared\logger.py
```

기존 중첩 배치를 아직 사용한다면 `D:\TeamProject\shared\GZZ-Shared-0.1.0`을 지정한다.
이 경로 바로 아래에 shared/logger.py가 있어야 한다. 이 ZIP은 공통 shared를 다시 설치하거나 변경하지 않는다.

아래는 직접 실행 예시다. 경로·URL·토큰은 실제 값으로 바꾼다.

```powershell
$env:PYTHONPATH = "D:\TeamProject;$env:PYTHONPATH"
$env:GZZ_TELEMETRY_URL = "https://telemetry.example.com"
$env:GZZ_TELEMETRY_TOKEN = "SERVER_ISSUED_TOKEN"
$env:GZZ_TELEMETRY_OUTBOX = "D:\TeamProject\telemetry-outbox\autopaint.sqlite3"
py main.py --session-id normal_host_002 --player-id player_042 --label NORMAL --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

모드별 소유권은 다음과 같다.

- managed(기본값): AutoPaint 프로세스가 shared를 한 번 초기화하고 종료 시 flush/shutdown을 담당한다.
- external: 같은 프로세스에서 통합 프로그램이 이미 초기화한 shared를 사용한다. AutoPaint는 공통 sender를 종료하지 않는다.
- off: 로컬 기록만 수행한다. shared 패키지나 서버 설정 없이 사용할 수 있다.

별도 자식 프로세스에는 부모의 초기화된 Python 객체가 공유되지 않는다. Launcher가 별도 프로세스로 실행할 때는 managed를 사용한다.
같은 프로세스 통합에서는 모듈의 main(argv)를 호출하며 argv에 `--telemetry external`을 넣는다.
main은 탐지 루프가 끝날 때까지 반환하지 않는다. 통합 실행·중지 방식은 Launcher와 맞춘다.

## 기록과 실패 처리

평가한 모든 7필드 Event를 로컬 events.jsonl에 먼저 기록한 뒤 같은 결과를 shared에 전달한다.
0점·동일 점수도 전달한다. 직접 HTTP 요청은 만들지 않는다.
전송 진단은 stderr로 출력한다. queued는 서버 성공이 아니라 로컬 대기열 등록이다.
대기열 등록 실패는 ENQUEUE_FAILED로 표시하고 로컬 기록을 유지한다.
종료 때 flushed=False면 전송 확인이 끝나지 않은 자료가 남았을 수 있다.

0.4.0에서 확인한 자동 재시작·공통 세션 시각·재시도 운영 정책 등은 이번 배포 구조 수정에 포함하지 않는다.
동일 세션 폴더의 재사용은 여전히 거절하며, timestamp_ms는 AutoPaint 자체 세션 시작 기준이다.
실제 중앙 서버 성공 여부는 receiver 완성 후 /api/detection 종단 테스트로 확인한다.

## 테스트와 Lua 파일

공통 shared를 모듈 검색 경로에 설정한 뒤 모듈 폴더에서 실행한다.

```powershell
py -m unittest discover -s tests -v
```

Python 테스트 102개가 포함된다. Lua 관찰기 테스트 12개는 기존 전체 소스에 남겨 두고 이 모듈 ZIP에서는 제외했다.
별도 `GZZPaintObserver-0.2.0-common.zip`에는 main.lua와 observer.lua 원본 두 파일이 있다.
UE4SS 런타임과 UEHelpers는 포함하지 않는다. 기존에 검증한 게임 설치본의 공통 의존성을 사용한다.
