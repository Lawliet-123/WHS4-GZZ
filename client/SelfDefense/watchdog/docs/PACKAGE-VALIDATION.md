# SelfDefense Watchdog 0.2.1 배포본 검사

2026-10-01 / Windows / Python 3.14.6.

- ZIP 최상위 main.py 확인. 압축 내용을 client/SelfDefense/watchdog에 넣는 배포본.
- anti_debug/integrity/공개 Launcher 코드는 배포·수정하지 않음.
- 실제 후보 ZIP을 서로 다른 폴더에 풀어 검사.
- ZIP 루트에서 워치독 단위 테스트 30개 통과.
- 팀 레포 구조 client/SelfDefense/watchdog에서 같은 30개 통과. 서로 다른 60개 테스트가 아님.
- PYTHONPATH 없이 shared·Launcher 기본 경로 탐색, 로그 위치, -m 패키지 실행 검사 포함.
- 다른 작업 디렉터리에서 --help, --shared-root를 지정한 합성 데모 실행·종료 확인.
- 배포본 SelfDefense + 배포된 shared 0.2.0 + 다운로드한 원본 Launcher로 프로세스 연동 10개 항목 통과.
- Python 3.12 문법 및 문서 상대 링크 검사 통과. 3.12 런타임 검증은 아님.
- 런처·shared 파일 무변경 확인. 기존 0.1.0/0.2.0 배포본 보존.
- 최종 ZIP은 검사한 후보와 동일한 코드이며 검증 문서·결과 JSON만 확정함.
- 실제 게임·Launcher.main 전체·중앙 HTTPS 서버는 테스트하지 않음.

의존 배포본: GZZ-Shared-0.2.0-repo-layout.zip

SHA-256: `2f8da90127db18f7c729c8b5bc948bf72a1791e0fed4d07bf6349c8ff7300f16`

결과와 런처 파일 해시는 [INTEGRATION-RESULT](INTEGRATION-RESULT.json) 참조. 로컬 사용자·임시 폴더 경로만 공유본에서 제외했다. 테스트용 프로세스 기록이므로 정상/핵 게임 표본으로 사용하지 않는다.
