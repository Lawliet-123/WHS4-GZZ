# Shared 0.2.0 레포 배치본 검증

검증일: 2026-10-01. Windows / Python 3.14.6.

- 기존 0.2.0 ZIP의 런타임 Python 파일 11개와 바이트 단위로 동일.
- ZIP 최상위 shared 하나, shared/shared 중첩 없음. 문서의 상대 파일 링크 검사 통과.
- 실제 ZIP을 별도 임시 폴더에 풀어 원본 작업 디렉터리 코드 없이 검증.
- 패키지 경로를 지정한 unittest 탐색: 48개 통과.
- shared/tests 디렉터리 직접 탐색: 같은 48개 통과.
- 새 모듈 경로로 실행한 로컬 HTTP 데모: PASS; 고유 Event 2건, pending 0, failed 0.
- Python 3.12 문법 검사 통과. 3.12 런타임 실행 검증은 아님.
- 실제 중앙 HTTPS 서버·게임·런처 테스트는 수행하지 않음. SelfDefense 코드·테스트는 이 ZIP에 없음.

라이브러리 버전은 0.2.0을 유지한다. 이번 작업은 배치와 문서·테스트 import 수정이다. 이전 ZIP과 원본 로그·DB·설정은 변경하지 않았다. GitHub 업로드나 삭제도 수행하지 않았다.

기준 ZIP: GZZ-Shared-0.2.0.zip

SHA-256: `cd979fe51024678a4f9c88327147a025a1b58b0cd5a6e0d7ad3a4bdc80357537`
