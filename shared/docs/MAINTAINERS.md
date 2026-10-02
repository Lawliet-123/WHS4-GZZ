# shared 수정·확장 안내

## 이 코드가 하지 않는 일

치트 탐지, 점수 계산, 게임 메모리 접근, UE4SS 후킹, 하트비트, SelfDefense, Launcher, 자동 재시작, 클라우드 배포는 shared에 포함하지 않는다. SelfDefense는 별도 모듈로 shared 공개 API만 사용한다. 0.2.0은 재시도 정책 선택과 evidence 문서를 추가했다.

## 파일별 책임

| 파일 | 책임 | 수정하는 경우 |
| --- | --- | --- |
| shared/config.py | 검증된 설정과 명시적 환경변수 읽기 | 주소·경로·제한 설정 추가 |
| shared/logger.py | 팀이 사용하는 얇은 공개 facade | 초기화/호출 형태 변경 |
| shared/schema.py | 7필드 Event 검증·직렬화 | 합의된 schema 변경 |
| shared/client.py | sender lifecycle·시도 횟수·상태 | 전송 정책·종료 처리 변경 |
| shared/transport.py | DeliveryTransport + 기본 HTTPS adapter | 인증 헤더·HTTP 라이브러리·응답 형식 변경 |
| shared/outbox.py | SQLite 전송 대기 및 실패 보관 | 로컬 대기 저장 방식 변경 |
| shared/storage.py | DetectionSink + JSONL writer·중복/복구 ledger | 서버 저장 방식 변경 |
| shared/_locking.py | 로컬 프로세스 간 파일 잠금 | OS 잠금 변경 |
| shared/_sqlite.py | 명시적 짧은 DB 트랜잭션 | SQLite 설정·오류 변환 변경 |
| shared/errors.py | 호출 측이 처리할 공개 예외 | 실패 종류 추가 |
| shared/tests/test_shared.py | schema·실패·계약·동시성 회귀 테스트 | 공개 동작 변경 시 같이 수정 |
| shared/examples/shared_demo.py | localhost 합성 통합 데모 | 인수인계 smoke test |

## 안정적으로 유지할 경계

1. detector는 shared.logger 또는 DetectionClient에만 의존한다. outbox/HTTP/SQLite를 직접 다루지 않는다.
2. sender는 DeliveryTransport.send(payload, event_id)의 결과만 해석한다. 이 메서드는 바이트 본문과 ID를 바꾸지 않는다.
3. server receiver는 DetectionSink.write_detection(result, event_id=...)를 호출한다. FastAPI를 shared에서 import하지 않는다.
4. import만으로 환경변수를 읽거나 파일을 만들거나 스레드를 실행하지 않는다.
5. Event 본문은 기존 7필드다. 전송 metadata를 넣으려고 detector 결과를 수정하지 않는다.
6. accepted/queued/stored를 혼용하지 않는다. 성공 상태는 해당 단계가 실제로 완료됐을 때만 반환한다.
7. 남은 데이터를 해결하려고 자동으로 DB/JSONL을 지우거나 큐를 비우지 않는다.

## 교체 예: HTTP 라이브러리 변경

새 class에 아래 메서드를 구현하고 DetectionClient의 transport 인자로 주입한다.

```python
def send(self, payload: bytes, event_id: str) -> DeliveryOutcome:
    # 인증, timeout, 응답 검증, redirect 금지 등을 구현
    return DeliveryOutcome("accepted", "stored")
```

위 반환값은 형태 설명이다. 실제 서버 저장 확인 없이 그대로 성공을 반환하면 안 된다. 코드값에는 토큰·응답 본문·개인정보를 넣지 않는다. 새 adapter는 기존 테스트와 동일한 잘못된 응답/인증 실패/재시도 테스트를 통과해야 한다.

HTTP 요청의 전체 절대 제한시간이 필요하면 transport에서 별도로 구현한다. 현재 표준 라이브러리 adapter는 blocking operation timeout이며, 느린 스트림 전체의 최대 시간을 보장하지 않는다.

## 교체 예: 서버가 DB/클라우드 저장소를 사용하는 경우

DetectionSink.write_detection을 구현하고 `configure_writer(writer=custom_sink)`로 주입한다. 기존 local writer와 동시에 같은 목적의 저장 함수를 중복 호출하지 않는다.

새 sink도 같은 ID+본문은 duplicate, 같은 ID+다른 본문은 conflict로 처리하고, 데이터가 실제로 보관된 뒤에만 receipt를 반환해야 한다. DB/파일 구현 상세가 달라져도 detector는 변경하지 않는다.

## 버전과 데이터 이전

- Python library: shared.__version__ = 0.2.0
- HTTP: X-GZZ-Protocol-Version = 1
- outbox / writer ledger: metadata에 각 schema version 1 저장
- AutoPaint 프로그램 버전 0.4.1은 별도이며 탐지 구현을 변경하지 않았다.

0.2.0은 outbox metadata에 sender_retry_not_before 키만 추가하며 테이블/버전은 바꾸지 않는다. 기존 pending/failed/ID/본문을 그대로 보존한다. bounded 기본값과 공개 API는 유지된다. 기존 버전은 이 키를 무시하므로 다운그레이드 시 persistent 재시도/전역 대기는 적용되지 않는다.

DB 구조를 바꿀 때는 migration과 이전 버전 테스트를 추가한다. 버전이 다르다고 빈 DB로 덮어쓰지 않는다. queue endpoint 변경도 자동 이동하지 않는다. 저장소 교체, 삭제, 로그 회전은 별도 기능이며 이번 버전에 없다.

## 테스트 방법

shared 폴더의 부모인 레포 또는 압축 해제 루트에서:

```powershell
py -m unittest discover -s shared/tests -t . -p "test_shared*.py" -v
py -m shared.examples.shared_demo
```

이 배포본에는 shared 테스트만 있다. 다른 모듈·Lua·SelfDefense의 테스트는 각 모듈에서 실행한다. shared 자체에는 추가 의존성이 없다. 네트워크 파일시스템·분산 배포·전원 장애 내구성은 이 테스트로 검증되지 않는다.

## 인수인계 때 확인할 질문

- receiver의 성공·오류 응답이 PROTOCOL과 일치하는가?
- 접근 토큰과 player/session 소유권 검사는 A가 구현했는가?
- 같은 outbox에 sender 두 개가 붙지 않는가?
- receiver 저장소가 재시작/배포 후에도 남는가?
- scoring은 event_id로 처리 중복을 막고 미처리 입력을 복구하는가?
- 반복된 현재 점수를 무조건 누적하지 않는가?
- 새 session/player 정보와 시간 기준은 Launcher/통합 측에서 공급하는가?
- 코드 변경 후 계약 테스트와 실패 테스트를 함께 실행했는가?

추가 작업자가 AI이더라도 먼저 shared/README.md, PROTOCOL.md, 이 문서를 읽고 해당 책임 파일만 수정한다. 중앙 서버 변경을 이유로 AutoPaint 규칙이나 기존 원본 로그를 수정하지 않는다.
