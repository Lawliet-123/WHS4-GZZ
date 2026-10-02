# 서버 담당 A/B 연결 안내

## A: 초기화와 기록

server 시작 시 한 번 실행한다.

```python
from shared.config import WriterConfig
from shared.logger import configure_writer, write_detection

writer = configure_writer(WriterConfig.from_env())
```

receiver는 인증, player/session/module 권한, body 크기·형식을 검사한 뒤 호출한다.

```python
# event_id는 요청의 Idempotency-Key다. 새로 만들지 않는다.
receipt = write_detection(result, event_id=event_id)
response_body = receipt.to_ack()
# HTTP 200 + application/json + response_body
```

예외를 무조건 잡아서 200으로 바꾸면 안 된다. ValidationError는 422, IdempotencyConflict는 409, 저장소 장애/잠금 지연은 503 등의 응답으로 연결한다. 자세한 운영 사유는 서버 내부에 기록하되 접근 토큰이나 원본 개인정보를 오류 응답에 노출하지 않는다.

기본 writer는 동기 디스크 작업이다. FastAPI를 쓰면 sync route 또는 thread pool 등으로 호출하고 async 이벤트 루프를 직접 오래 점유하지 않게 한다. 실제 FastAPI 앱과 접근 제어는 A 담당자의 구현 범위다.

## 파일 구성

```text
server/logs/detections/
  writer-index.sqlite3
  writer.lock
  autopaint/
    session_id/
      player_id.jsonl
```

JSONL 한 줄은 기존 공통 Event다. 전송 ID를 넣은 wrapper로 감싸지 않는다. ledger에는 전송 ID, body digest, 원본 body, 파일 위치, 처리 순서와 기록 완료 상태가 있다. 다른 플레이어의 동일 세션을 구분하기 위해 player_id까지 경로에 포함한다.

ledger와 JSONL은 한 저장 단위다. 둘을 함께 백업해야 하며 한쪽만 지우거나 JSONL을 외부에서 편집하면 writer가 불일치를 감지해 실패할 수 있다. 로그 회전·정리·압축·retention은 별도 설계가 필요하다. 서버 저장 용량 상한은 이번 라이브러리에서 자동 관리하지 않는다.

파일 기록 전에 pending ledger를 남기고, JSONL 쓰기와 flush/fsync가 끝난 뒤 committed로 변경한다. 프로그램이 중간에 종료되면 다음 접근에서 동일한 미완성 tail을 확인해 이어 쓴다. 이미 완료된 줄을 다시 append하지 않으며, 예상하지 못한 파일 변경에는 덮어쓰기로 복구하지 않고 오류를 낸다.

현재 검증은 일반적인 프로세스 중단을 모사한 테스트다. 손상된 디스크, 전원 차단, 외부 변조를 모두 복구하는 시스템은 아니다.

## B: scoring에 전달하는 방법

실시간 호출에 연결할 수도 있지만 저장 직후 서버가 종료되는 구간을 고려해야 한다. 기본 writer는 다음 복구용 feed를 제공한다.

```python
batch = writer.iter_stored(after_sequence=last_committed_cursor, limit=1000)
for record in batch:
    # record.event_id: 동일 입력 중복 처리를 막는 키
    # record.result: 원래 7필드 Event
    # record.sequence: 저장 순서의 내부 cursor, Event의 timestamp가 아님
    scoring.process(record.result, event_id=record.event_id)
    # 처리 결과/중복 방지 이력/cursor를 안전하게 저장하는 방법은 B가 구현
```

`scoring.process`는 연결 위치를 설명하는 가상 인터페이스이며 이 패키지에 구현된 함수가 아니다. B의 실제 API에 맞춰 adapter를 작성한다. 처리와 cursor 저장 사이의 실패도 고려해 event_id 기반으로 재실행에 안전해야 한다.

중복 응답이라는 이유만으로 해당 결과가 이미 scoring됐다고 가정하지 않는다. 실시간 전달과 feed 복구를 함께 쓰면 B가 같은 event_id를 한 번만 반영해야 한다.

AutoPaint의 raw_score는 두 채널 중 큰 값이다. behavior만 분석할 때는 evidence.behavior_valid=1인 behavior_score를 사용한다. 매 평가 시점의 점수는 새로운 치트 사건 개수가 아니며, 수신 순서가 event-time 순서라고 가정해서도 안 된다.

## 배포 확인

- 운영 주소는 HTTPS, 인증서 검증을 유지한다.
- 기본 JSONL+SQLite는 같은 로컬 저장소를 공유하는 단일 서버 배포를 위한 기준 구현이다.
- Cloud Run 등의 임시 로컬 파일시스템만 사용하면 재시작 시 자료가 사라질 수 있다. 지속 가능한 저장소 또는 다른 DetectionSink를 연결한다.
- 여러 서버 인스턴스가 각자 로컬 ledger를 가지면 서로의 중복을 알 수 없다. 분산 배포에는 공용 DB 등의 중복 관리가 필요하다.
- 권한·rate limit·토큰 발급·보관 기간·개인정보 정책·클라우드 자격증명은 A 담당자가 관리한다.

[Cloud Run 파일 저장 특성](https://docs.cloud.google.com/run/docs/container-contract#file_system)
