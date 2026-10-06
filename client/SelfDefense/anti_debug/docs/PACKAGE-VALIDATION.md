# SelfDefense-AntiDebug-0.1.0-testfix1 검증

2026-10-06, Windows x64, Python 3.14.6.
기준 main: 31dc8332a770f51f2f50856637dcf8a1a4cee102.

- 배포 후보 ZIP을 독립 폴더와 팀 레포 구조에 각각 풀어 검사했다.
- 일반 Python과 venv Python에서 각 배치의 단위 테스트 51개를 모두 통과했다. 같은 검사를 네 번 실행한 것이며 서로 다른 204개 항목이라는 뜻은 아니다.
- 후보 ZIP 코드의 실제 프로세스 시험 5개를 일반/venv에서 각각 통과했다.
- 모니터는 일반/venv Python 각각으로 실행했다. 디버깅 대상은 직접 실행되는 시험용 base Python이며, 중간 실행기만 검사하지 않도록 실제 PID를 확인했다. 시험 자식 종료를 확인했다.
- 운영 Python 파일은 기존 SelfDefense-AntiDebug-0.1.0 ZIP과 바이트 단위로 같다. 원본 Launcher·shared와 기존 배포 ZIP은 변경하지 않았다.
- 재현 명령과 변경 이유는 [TESTFIX](TESTFIX.md), 실측 결과는 [NATIVE-RESULT.json](NATIVE-RESULT.json)에 있다.
- Python 3.12 문법과 문서 링크, ZIP 무결성 검사를 수행했다. 3.12 런타임 시험은 아니다.
- 최종 ZIP은 검증 후보와 코드가 같으며 결과 JSON·검증 문서·합성 예시만 확정했다.

전체 Launcher.main, 실제 게임·팀 탐지기 동시 실행, 중앙 HTTPS, Dashboard 화면은 미검증이다. 이 결과는 정상/핵 플레이 로그가 아니다.
testfix1은 시험 도구·문서 보완 묶음 이름이다. 운영 버전·탐지 규칙·점수·출력 계약을 바꾸지 않았다.
