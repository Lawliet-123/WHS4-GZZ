# shared 사용법

## 공개 함수와 인스턴스 API

일반적인 팀 코드에서는 `shared.logger`를 사용한다. 여러 독립 클라이언트나 저장소가 필요한 테스트에서는 `DetectionClient`와 `DetectionWriter`를 직접 생성한다. 둘은 같은 구현을 사용한다.

클라이언트의 설정과 서버의 설정은 서로 독립이다. 서버에서 `write_detection()`을 쓰기 위해 클라이언트 토큰이나 전송 스레드를 만들 필요는 없다.

```python
from shared.config import ClientConfig
from shared.logger import (
    configure_client, send_detection, get_client_status,
    flush_client, shutdown_client,
)
from shared.errors import SharedError

configure_client(ClientConfig.from_env())
try:
    # result는 기존 detector가 만든 7필드 dict다. 점수와 timestamp를 바꾸지 않는다.
    try:
        receipt = send_detection(result)
        print(receipt.event_id, receipt.status)  # queued: 로컬 보관, 서버 성공 아님
    except SharedError as exc:
        # 실패를 UI/진단 로그에 알린다. detector 전체를 종료하거나 실패를 숨기지 않는다.
        print(type(exc).__name__)
    print(get_client_status())
finally:
    delivered = flush_client(timeout=3)
    stopped = shutdown_client(timeout=5)
    # delivered=False면 미전송/실패가 남는다. stopped=False면 요청이 아직 진행 중이다.
```

이 코드는 호출 위치를 보여주는 예다. AutoPaint에는 아직 연결하지 않았다. `configure_client()`는 매 Event가 아니라 프로세스 시작 시 한 번 호출한다. 종료 제어는 Launcher 쪽에서 맡는다. `send_detection()`은 HTTP를 기다리지 않지만 로컬 검증·SQLite 저장은 동기 작업이며 디스크 오류/잠금에 따라 짧게 대기하거나 실패할 수 있다.

기존 raw 로그와 `events.jsonl`은 계속 보존한다. shared는 이 파일에 추가로 같은 줄을 쓰지 않고 별도 전송 대기 DB를 사용한다.

## 상태 해석

`get_client_status()`는 `pending`, `failed`, `payload_bytes`, `worker_alive`, `closed`, `last_delivery`, `last_code`, `last_event_id`, `acknowledged_this_run`을 제공한다.

- `IDLE`: 아직 시도하지 않음
- `SENDING`: 요청 중
- `DELIVERED`: 최근 요청의 저장 완료 응답을 확인함
- `RETRYING`: 일시 실패 후 다음 시도 대기
- `FAILED`: 해당 결과는 자동 재시도를 중단하고 보관함
- `ERROR`: 내부 저장 작업 등의 오류로 작업자가 멈춤

이 값은 최근 전송 상태다. 게임 상태, 지속적인 서버 가용성, 하트비트 생존 상태를 뜻하지 않는다. 현재 backlog는 pending/failed를 함께 본다. 이전 성공 상태가 남아 있어도 worker_alive=False이면 작업자는 종료된 상태다.

`flush_client()`는 대기와 실패가 모두 0일 때만 True다. `shutdown_client()`는 남은 자료를 삭제하지 않는다. 네트워크 작업이 종료 제한 시간을 넘기면 False를 반환한다. 그때 같은 outbox를 새 client로 열지 말고 종료를 다시 확인한다.

## 실패를 다시 보내기

인증/형식 오류와 재시도 한도 초과 결과는 `failed`로 남고 용량 제한에 포함된다. 원인을 고친 뒤에만 명시적으로 재시도한다.

```python
client = configure_client(ClientConfig.from_env())
print(client.failures())                # 본문/키 없이 event_id, 시도 횟수, 오류 코드
client.retry_failed(event_id)           # 한 건
# client.retry_failed()                 # 모든 실패 건: 원인을 확인한 뒤에만 사용
```

키를 교체하려면 기존 client를 종료하고 새 설정으로 다시 생성한다. outbox에는 접근 토큰을 저장하지 않는다. 미전송 pending은 프로그램 재시작 후 자동 재개하지만 failed는 자동 재개하지 않는다.

발행자가 명시적으로 같은 `event_id`를 다시 넘기는 경우 같은 본문이어야 한다. 이미 failed인 같은 ID의 재등록은 `EventAlreadyFailedError`이며 명시적 retry_failed가 필요하다. 정상적인 새 평가 시점에는 새 ID를 사용한다. 기본값은 enqueue 때 UUID를 새로 생성하며 HTTP 재시도에는 그 UUID를 재사용한다.

## 환경변수

`ClientConfig.from_env()` 또는 `WriterConfig.from_env()`를 명시적으로 호출할 때만 읽는다. `.env` 파일 자동 로딩 기능은 없다.

| 변수 | 기본값 / 의미 |
| --- | --- |
| `GZZ_TELEMETRY_URL` | 필수, HTTPS origin. 예: `https://telemetry.example.com` |
| `GZZ_TELEMETRY_TOKEN` | 클라이언트 필수, Bearer 접근 토큰 |
| `GZZ_TELEMETRY_DETECTION_PATH` | `/api/detection`; 서버 prefix가 있으면 이 값에 포함 |
| `GZZ_TELEMETRY_OUTBOX` | `telemetry-outbox/client.sqlite3` |
| `GZZ_TELEMETRY_TIMEOUT_SECONDS` | 3; 네트워크 blocking operation 대기시간 |
| `GZZ_TELEMETRY_MAX_ATTEMPTS` | 8; 최초 전송을 포함한 자동 시도 한도 |
| `GZZ_TELEMETRY_RETRY_BASE_SECONDS` | 1 |
| `GZZ_TELEMETRY_RETRY_MAX_SECONDS` | 60; Retry-After도 이 상한 적용 |
| `GZZ_TELEMETRY_RETRY_JITTER_RATIO` | 0.2; 재시도 집중 완화용 추가 지연 비율 |
| `GZZ_TELEMETRY_MAX_QUEUE_EVENTS` | 10000; failed 포함 |
| `GZZ_TELEMETRY_MAX_QUEUE_BYTES` | 33554432; 저장 payload 합계, DB 파일 자체 크기는 아님 |
| `GZZ_TELEMETRY_MAX_EVENT_BYTES` | 262144; UTF-8 JSON 한 건의 최대 크기 |
| `GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK` | false; 테스트에서만 true, 127.0.0.1/localhost/::1 HTTP 허용 |
| `GZZ_TELEMETRY_USE_ENVIRONMENT_PROXY` | false; 조직 프록시가 필요한 경우에만 명시적으로 활성화 |
| `GZZ_TELEMETRY_LOG_ROOT` | 서버용 `server/logs/detections` |
| `GZZ_TELEMETRY_WRITE_LOCK_TIMEOUT_SECONDS` | 서버용 3 |

환경변수를 쓰지 않고 dataclass 생성자에 직접 설정할 수도 있다. 상대 경로는 초기화 당시 작업 디렉터리 기준으로 절대 경로로 변환된다. 통합 배포에서는 Launcher/A 담당자가 명확한 경로를 지정한다.

한 outbox에는 한 sender만 실행할 수 있다. 별도 프로세스의 여러 detector가 직접 보내는 구조라면 detector별 outbox 경로가 필요하다. 같은 모듈을 import한다고 프로세스 간 메모리나 sender가 공유되는 것은 아니다.

outbox는 생성 당시 전송 endpoint에 묶인다. 서버 주소를 바꿨다고 기존 자료를 새 서버에 자동 전송하지 않는다. 새 outbox를 사용하거나 명시적인 데이터 이동 절차를 설계한다.

## 저장 위치와 한계

- outbox와 서버 ledger는 SQLite다. 표준 라이브러리 외 추가 설치가 없다.
- outbox는 성공 응답 확인 후 해당 row를 삭제한다. SQLite는 빈 페이지를 재사용하므로 파일 크기가 즉시 줄어들지는 않는다.
- 큐가 가득 차거나 디스크 저장이 실패하면 예외가 발생하며, 기존 결과를 임의로 삭제하지 않는다. 호출 측은 이 실패를 기록해야 한다.
- 로컬 DB·JSONL은 암호화되지 않는다. 접근 권한, 보관 기간, 개인정보 정책은 배포 담당자가 관리한다.
- 재시도로 서버 도착 순서가 달라질 수 있다. timestamp_ms는 원래 값이고 서버 B는 이벤트 시간과 수신 순서를 구분해야 한다.
- 기본 HTTP timeout은 전체 요청의 절대 시간 제한이 아니다. close의 대기 한도를 초과한 작업은 daemon worker에 남을 수 있다.

자세한 서버 약속은 [PROTOCOL](../docs/shared/PROTOCOL.md), 수정 방법은 [MAINTAINERS](../docs/shared/MAINTAINERS.md)를 참고한다.
