# 0.1.0-testfix1

2026-10-06. 운영 버전·main.py·권한·탐지 규칙·7필드 출력은 0.1.0 그대로다.

과거 시험 도구가 acc9d2a의 registry.py SHA-256에 고정되어 최신 사본을 거절했다. 운영 AntiDebug가 과거 Launcher 코드를 강제한 것이 아니다.

시험 실행 시 --reference-commit과 --registry-sha256을 필수로 받는다. 해시 확인은 제거하지 않았다. 형식·원본·복사본을 확인한 후에만 시험용 registry.py를 import하며, 종료 후에도 원본·사본의 무변경을 확인한다. 지정한 커밋의 출처 검토는 실행자의 책임이며, 임의 파일 해시를 그 자리에서 계산해 승인 값처럼 사용하는 방식은 피한다.

## 재검사

레포 루트, 아래 커밋의 사본 기준이다. 다른 버전은 검토한 커밋과 해시로 두 값을 함께 바꾼다. 결과 폴더는 새 이름이어야 한다.

```powershell
python -m unittest discover -s client/SelfDefense/anti_debug/tests -p 'test*.py' -v
python client/SelfDefense/anti_debug/tests/native_integration.py --launcher-dir client/Launcher --shared-root . --reference-commit 31dc8332a770f51f2f50856637dcf8a1a4cee102 --registry-sha256 fdc50c61d0abf931ee4cade8bea4ed430c26edb44ef0275d322ff6ebe2dcfb8d --output-dir anti-debug-native-new
```

일반 Python과 실제 사용할 venv Python으로 각각 실행한다. 각 시험에서 운영 main.py는 선택한 Python으로 실행된다. 네이티브 디버거 시험의 대상 자식만 직접 실행되는 base Python을 사용한다. venv 중간 실행 프로세스에만 디버거를 연결해 놓고 실제 worker를 검사했다고 잘못 보고하지 않기 위해서다. 대상의 실제 PID가 Popen.pid와 같은지 검사하고 경로·실행 환경을 보고서에 기록한다.

시험용 자식을 만들어 연결 감지, 연결 해제, 생성 시각 불일치, 대상 종료, 런처 stopping을 검사한다. 기존 게임·사용자 프로그램에 연결하지 않는다. AntiDebug 운영 코드는 디버거 연결·해제·프로세스 종료를 수행하지 않는다.

이 시험은 venv 모니터 실행 검증이지, 모든 venv 자식의 디버깅을 Launcher가 등록·추적한다는 보증은 아니다. 실제 Launcher의 등록 PID와 payload PID가 다르면 어떤 PID를 보호 대상으로 볼지도 별도 확인해야 한다.

[검증 기록](PACKAGE-VALIDATION.md), [실행 결과](NATIVE-RESULT.json)를 참고한다. 전체 Launcher.main·실게임·중앙 HTTPS·Dashboard 화면은 공동 종단 시험으로 남는다.
