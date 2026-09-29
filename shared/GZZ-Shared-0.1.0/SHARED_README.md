# GZZ Shared 0.1.0

탐지 결과 전송·기록용 공통 라이브러리다. AutoPaint, 하트비트, SelfDefense, Launcher, 중앙 scoring은 수정하거나 구현하지 않았다.

서버 없이 같은 PC에서 먼저 검증할 수 있다. Python 3.10 이상과 표준 라이브러리만 사용하며, 검증 환경은 Windows / Python 3.14.6이다. 다른 Python 버전과 운영체제는 별도 검증이 필요하다.

## 시작

`shared`, `examples`, `tests` 폴더가 있는 디렉터리에서 실행한다.

```powershell
py -m unittest discover -s tests -p test_shared.py -v
py -m examples.shared_demo
```

데모는 127.0.0.1에 임시 테스트 수신기를 열고 합성 0점/15점 Event를 보낸다. 첫 저장 후 응답 실패를 일부러 발생시켜 재전송을 확인한다. 결과가 `PASS`, 저장 건수가 2이면 성공이다. 출력 파일은 새 `shared_demo_output/demo_...` 폴더에 남는다. 실제 게임 테스트나 실제 중앙 서버 연결은 아니다. 외부 공개용 서버로 사용하지 않는다.

## 인수인계 문서

- [사용법과 설정](shared/README.md): detector 개발자가 읽을 문서
- [서버 연동 규약 v1](docs/shared/PROTOCOL.md): receiver 담당 A가 읽을 문서
- [수정·확장 안내](docs/shared/MAINTAINERS.md): 다음 작업자와 AI가 읽을 문서
- [파일 저장과 scoring 연결](docs/shared/SERVER_HANDOFF.md): A·B 담당자가 함께 읽을 문서
- [검증 결과와 미검증 범위](docs/shared/VALIDATION.md)

## 이번 범위

- 기존 7개 필드 유지, 0점 및 원래 세션 경과시간 보존
- 명시적 초기화, import 시 파일·스레드·네트워크 생성 없음
- 전송 대기 저장, 비동기 HTTP 작업, 제한된 재시도
- 중복 전송 식별, JSONL 기록, 중단된 기록 복구
- 인증·형식 오류 및 저장 실패를 성공으로 표시하지 않음
- 코드의 공개 호출부와 HTTP·대기열·파일 저장 구현 분리

HTTP 규약은 서버 담당자에게 전달할 기준 구현이다. 실제 인증·접근 권한·배포·scoring은 서버 담당자가 연결해야 한다. 중복 저장 방지가 중앙 점수의 정확히 한 번 반영까지 자동 보장하지는 않는다.

팀 저장소에 옮길 때 `shared/` 전체와 `docs/shared/`, 테스트를 함께 전달한다. `config.py`, `logger.py` 두 파일만 복사하면 내부 모듈이 누락된다. 자체 패키징 설정에서는 `shared` 패키지가 배포본에 포함되는지 확인한다.
