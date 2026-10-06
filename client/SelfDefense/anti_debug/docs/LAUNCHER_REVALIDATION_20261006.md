# Launcher #125 기준 SelfDefense 재검증

2026-10-06. Windows x64, Python 3.14.6 일반 실행과 venv에서 확인했다.

## 기준

- Launcher: `8cded03366399bc0527b177e1780d4ad25a64c34` (#125).
- registry.py LF SHA-256: `550740a16f31dee41e7f11e64ec4f768134eea44e97f1f520b20618791fe0678`.
- CRLF 기준으로 전달받은 승인값: `1d35421ba74e1bf7939c7479c63729aefab45fa9fbb5ebf29e4758868cbbe8ef`. 이번 실행은 LF 파일로 했다.
- Watchdog 0.3.0-testfix1 / AntiDebug 0.1.0-testfix1 ZIP을 별도 폴더에 풀어 시험했다.
- PR #124의 `f4e5e08d8a97ca384ca3f9aaabd6291a4e6cd8e0`에서 내려받은 Python 23개가 시험 시작 시 ZIP 코드와 모두 같았다.
- 검사 시작 시 #124·#125 모두 미머지 상태였다. 현재 main 전체를 시험했다는 뜻은 아니다.

## 결과

아래 검사를 일반 Python과 venv에서 각각 실행했다. 같은 항목을 환경별로 반복한 것이다.

| 검사 | 일반 Python | venv |
| --- | --- | --- |
| Watchdog 단위 테스트 | 67 통과 | 67 통과 |
| AntiDebug 단위 테스트 | 59 통과 | 59 통과 |
| Launcher venv PID 회귀 테스트 | 9 통과 | 9 통과 |
| Watchdog 실제 프로세스 통합 검사 | 12 통과 | 12 통과 |
| AntiDebug 네이티브 통합 검사 | 5 통과 | 5 통과 |
| 최초 실행·재시작 PID를 AntiDebug reader/probe로 확인 | 2 통과 | 2 통과 |

- Watchdog: 재시작 경쟁, 중복 실행 방지, 재시작 한도, 정상 종료, 런처 종료 후 부활 금지, 로컬 receiver 재전송을 확인했다.
- 이전에 문제가 된 ONESHOT `proc.wait(5)`는 시간을 늘리지 않고 통과했다. 종료 코드 0/1은 completed, 2는 scan_failed, 3은 crashed로 확인했다.
- 시험 종료 후 Watchdog worker 잔류와 예상하지 않은 생존 worker는 없었다.
- AntiDebug: 디버거 연결, 연결 해제, 생성 시각 불일치, 대상 종료, Launcher STOPPING을 확인했다.
- 보완 후 네이티브 검사를 환경별로 세 번 추가했다. 최초 실행 포함 일반 4회·venv 4회, 모두 5개 항목 통과 및 시험 자식 종료를 확인했다. 서로 다른 40개 기능을 검사했다는 의미는 아니다.

### venv PID 실측

| 단계 | 등록 PID | 실제 worker PID | AntiDebug가 읽은 PID |
| --- | --- | --- | --- |
| Launcher 최초 실행 | 24256 | 24256 | 24256 |
| Watchdog 재시작 API | 29036 | 29036 | 29036 |

생성 시각까지 확인한 실제 Windows probe 결과는 두 경우 모두 CLEAR였다. 자식의 venv prefix가 유지되고 `__PYVENV_LAUNCHER__`가 자식 환경에 남지 않는 것도 확인했다. 이 PID는 이번 시험 값이며 설정값이 아니다.

## 재검증 중 발견한 테스트 문제와 수정

기존 AntiDebug 네이티브 테스트의 준비 신호 처리에 경쟁 조건이 있었다.

1. 시험 자식이 준비 파일을 생성한다.
2. JSON 쓰기가 끝나기 전에 부모가 `exists()`를 확인한다.
3. 부모가 빈 파일을 읽어 `JSONDecodeError`로 종료한다.

실제로 venv 시험 한 번에서 발생했다. 새로운 registry와의 API 불일치나 탐지 코드 오류는 아니며, 파일 생성과 내용 완성 사이의 테스트 동기화 문제다.

수정 파일:

- `tests/native_integration.py`: 임시 파일에 준비 JSON을 작성한 뒤 rename/replace로 공개한다. 부모도 파일 존재 여부만 보지 않고 완성된 JSON과 시험 자식 PID를 확인한다. 기존 준비 timeout 10초는 유지한다.
- `tests/test_native_fixture_ready.py`: 파일 없음·빈 파일·부분 JSON·정상 PID·다른 PID·bool PID·비객체 JSON·권한 오류에 대한 회귀 테스트 8개를 추가했다.

기존 AntiDebug 단위 테스트 51개에 새 8개를 더해 59개다. 다른 PID는 여전히 오류로 거절하고, 권한 오류를 준비 중 상태로 숨기지 않는다.

Launcher, Watchdog 운영 코드, AntiDebug 운영 코드, shared, Integrity, 점수와 Event 규격은 바꾸지 않았다. 기존 배포 ZIP도 덮어쓰지 않았다. 따라서 기존 0.1.0-testfix1 ZIP에는 이 추가 테스트 보완이 없다.

## 담당자에게 전달할 내용

> #125의 8cded033 기준으로 재검증했습니다. registry.py LF 해시가 전달주신 값과 일치했습니다. Windows Python 3.14.6 일반/venv에서 Watchdog 단위 67개·통합 12개, AntiDebug 단위 59개·네이티브 5개, Launcher PID 회귀 9개 모두 통과했습니다. 등록 PID와 실제 worker PID, AntiDebug reader PID도 최초 실행과 재시작 모두 일치했습니다. 운영 코드는 추가 수정하지 않았습니다. 다만 AntiDebug 테스트의 준비 파일을 쓰는 중에 읽는 경쟁 조건을 발견해 tests/native_integration.py와 신규 tests/test_native_fixture_ready.py만 보완했습니다. 이 두 테스트 파일을 #124에 추가 반영할 예정입니다. 반영 후 담당자 환경의 Python 3.13.1 및 최종 Launcher.main 기준 종료·재시작 시험 부탁드립니다.

## 재실행 예시

레포 루트의 PowerShell에서 실행한다. 먼저 승인된 Launcher 파일 내용과 줄바꿈 형식을 확인하고 해시를 비교한다. 현재 파일의 해시를 계산했다는 이유만으로 새로운 승인값으로 사용하지 않는다.

```powershell
Get-FileHash client/Launcher/registry.py -Algorithm SHA256
py -m unittest discover -s client/SelfDefense/watchdog/tests -p "test*.py" -q
py -m unittest discover -s client/SelfDefense/anti_debug/tests -p "test*.py" -q
py -m unittest discover -s client/Launcher/tests -p "test_venv_worker_pid.py" -v
py client/SelfDefense/watchdog/tests/launcher_integration.py --launcher-dir client/Launcher --shared-root . --reference-commit 8cded03366399bc0527b177e1780d4ad25a64c34 --report .test-results/watchdog-8cded03-base.json
py client/SelfDefense/anti_debug/tests/native_integration.py --launcher-dir client/Launcher --shared-root . --reference-commit 8cded03366399bc0527b177e1780d4ad25a64c34 --registry-sha256 550740a16f31dee41e7f11e64ec4f768134eea44e97f1f520b20618791fe0678 --output-dir .test-results/anti-debug-8cded03-base
```

CRLF 체크아웃이면 확인 후 승인된 CRLF 해시를 넣는다. 다른 코드 변경이 있으면 이 승인을 재사용하지 않고 변경 내용을 다시 검토한다. 최종 merge 커밋으로 검사한다면 reference-commit도 실제 검토한 전체 커밋 ID로 바꾼다.

venv 검사는 `py` 대신 해당 venv의 `Scripts/python.exe`를 사용하고, 결과 경로를 `-venv` 등 존재하지 않는 새 이름으로 바꾼다. 기존 결과를 삭제해 덮어쓰지 않는다.

## 확인 범위와 남은 일

- native 테스트의 디버깅 대상은 테스트가 직접 만든 base Python 자식이다. 모니터는 일반/venv 각각으로 실행했다. 별도 PID 확인에서는 registry.spawn이 만든 실제 venv 자식을 reader/probe로 검사했다.
- 실제 게임·치트·전체 탐지 모듈을 동시에 실행하거나 전체 Launcher.main을 실행한 검증은 아니다.
- 중앙 HTTPS 수신과 Dashboard 화면 종단 검증도 아니다. Watchdog의 receiver 시험은 격리된 로컬 HTTP 시험용 수신기다.
- Python 3.13.1 런타임은 이 PC에 없어 직접 실행하지 않았다. 팀원 환경의 재실행은 남아 있다.
- #124·#125 반영 후 AntiDebug 등록 및 세 SelfDefense 모듈의 종료·재시작 시험은 Launcher 담당자가 진행한다.
- 최종 배포본 Integrity baseline 생성은 여전히 최종 클라이언트 파일 확정 후에 한다.

초기 격리 실행에서는 도구 sandbox의 임시 디렉터리 권한 오류로 단위 테스트가 실패했다. 이후 일반 실행 권한으로 재검사했으며, 제한 환경 실패와 실제 준비 파일 경쟁 조건 실패도 기록을 삭제하지 않고 보존했다.

로컬 원본 기록: `AntiCheat/reports/selfdefense-launcher-8cded03-20261006-01`(격리 환경), `-02`(기존 테스트 및 경쟁 조건), `-03`(테스트 보완 후 PASS·추가 반복). PID 직접 비교 결과는 `-02/identity-base`, `-02/identity-venv`에 있다.
