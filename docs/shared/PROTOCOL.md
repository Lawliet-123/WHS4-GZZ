# Shared telemetry protocol v1

상태: shared 0.1.0 기준 구현. 팀 receiver와 실제 연결 검증 전이다. 하트비트 규약이 아니며 기존 하트비트 담당의 API를 변경하지 않는다.

## 요청

```http
POST /api/detection
Content-Type: application/json
Accept: application/json
Authorization: Bearer <client-token>
Idempotency-Key: <canonical-lowercase-UUID>
X-GZZ-Protocol-Version: 1
```

본문은 기존 7필드 그대로다. 아래는 합성 예시이지 새 게임 테스트 결과가 아니다.

```json
{
  "session_id": "normal_001",
  "player_id": "player_042",
  "module": "autopaint",
  "timestamp_ms": 1234,
  "evidence": {"behavior_score": 0, "behavior_valid": 1},
  "reasons": [],
  "raw_score": 0
}
```

`window_id`, `sample_id`, `status`, 전송 식별자는 본문에 추가하지 않는다. event_id는 전송 헤더 및 별도 내부 ledger에만 존재한다. Event 점수는 신뢰할 수 있는 서버 사실이 아니라 클라이언트의 보고다.

## 기준 구현의 검증 규칙

- 루트 필드는 위 7개와 정확히 일치해야 한다.
- session_id/player_id/module: 길이 1~128, ASCII 영문·숫자·`_ . -`; `.`, `..`, 끝의 점 및 Windows 예약 이름 제외.
- timestamp_ms: bool이 아닌 정수, 0~2^63-1. 세션 시작 후 경과시간이며 재시도 때 변경하지 않는다.
- raw_score: bool이 아닌 유한한 0 이상 숫자. 현재 AutoPaint는 정수를 사용한다.
- evidence: JSON object. 문자열/유한 숫자/bool/null/list/object 사용 가능. 모듈별 내부 필드는 해당 담당자가 정의한다.
- reasons: 문자열 배열. 빈 배열 허용.
- NaN/Infinity, 중복 JSON key, 비JSON 자료형, 과도한 중첩/요소 수는 거절한다.
- 기본 본문 상한 256 KiB; receiver도 읽기 전/중 제한을 적용해야 한다.

이 규칙의 완화·변경은 schema.py, 계약 테스트, 이 문서를 같이 수정하고 통합 담당자에게 알린다. shared는 점수 가중치나 정상/치트 label을 판단하지 않는다.

## 응답

새 Event를 저장한 경우:

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{"event_id":"요청의 UUID와 동일한 값","status":"stored"}
```

같은 ID와 같은 내용이 이미 저장된 경우에는 `status: "duplicate"`로 200을 반환한다. 같은 ID인데 내용이 다르면 409다. 점수가 같다는 이유만으로 다른 ID의 Event를 중복 처리하지 않는다.

200은 적어도 결과가 서버의 지속 가능한 저장소에 기록됐다는 약속이다. 요청을 메모리 대기열에 넣은 것만으로 200/stored를 반환하지 않는다. 기본 writer는 로컬 파일+SQLite 기준이다. 실제 클라우드 지속성은 배포 담당자가 확보해야 한다.

클라이언트는 HTTP 200, application/json, 일치하는 event_id, stored/duplicate를 모두 확인해야 로컬 대기 자료를 제거한다. 202/204, 빈 본문, HTML, 다른 ID는 저장 완료로 처리하지 않는다. 이 계약을 바꿀 때는 응답 파서와 테스트를 함께 변경한다.

| 응답/실패 | sender 처리 |
| --- | --- |
| 400/401/403/404/405/409/413/415/422 등 영구 오류 | 해당 건을 failed로 보관, 자동 재시도 중단 |
| 408/425/429 및 5xx | 한도 내 재시도 |
| 연결 실패·시간 초과·불명확한 성공 응답 | 같은 ID로 한도 내 재시도 |
| 3xx | 따라가지 않고 failed 처리; 인증 헤더를 다른 주소로 전달하지 않음 |
| 인증서 검증 실패 | 검증을 끄지 않고 failed 처리 |

재시도는 지수 증가+추가 무작위 지연을 사용한다. Retry-After는 초 또는 HTTP 날짜를 해석하되 설정된 최대 대기시간으로 제한한다. 한도 초과 데이터는 삭제하지 않는다. 서버 장애와 인증 실패가 치트 점수로 변환되지는 않는다.

## receiver A 담당의 책임

1. HTTPS 종료, 접근 토큰 검증, 허용된 player/session/module 확인.
2. 요청 크기·빈도 제한과 JSON/필드 검증. shared의 decode_event를 재사용할 수 있으나 클라이언트 검증을 믿고 생략하면 안 된다.
3. Idempotency-Key를 그대로 write_detection에 넘긴다. 서버가 요청마다 새 UUID를 만들면 재시도 중복 방지가 깨진다.
4. 저장 실패는 성공 응답으로 바꾸지 않는다. StorageError/잠금 실패는 일시 오류로 처리하고 원인을 운영 로그에 남긴다.
5. 확인된 결과와 event_id를 scoring에 연결한다. scoring 처리 완료와 저장 완료는 별개다.
6. 서버 운영 키·클라우드 자격증명은 client/shared 배포본에 넣지 않는다.

인증키만으로 변조된 클라이언트 보고의 진실성을 보장할 수는 없다. server receiver, auth, scoring은 이 패키지에 구현되어 있지 않다.

## 중복 저장과 중복 판정은 다르다

보장 범위는 안정된 ID로 재전송하는 at-least-once 전달과 기본 writer의 중복 JSONL 저장 방지다. 모든 장애에서 전송 성공을 보장하거나 중앙 판정을 exactly-once로 만들어 주지는 않는다.

저장 성공 후 scoring 전에 서버가 종료될 수 있다. 따라서 duplicate 응답이라고 무조건 scoring을 건너뛰면 미처리 결과가 남을 수 있다. B는 event_id 기준의 처리 이력과 복구용 입력을 사용해야 한다. 기본 writer.iter_stored의 sequence cursor가 그 복구에 사용될 수 있다.

새 시각의 같은 점수는 새로운 평가 표본이지 통신 중복이 아니다. AutoPaint의 반복 점수를 매번 새 사건으로 단순 가산하지 않도록 B가 별도 규칙을 적용한다.

## 참고

- [HTTP 재시도 의미](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods)
- [Python urllib 대기시간·리다이렉트](https://docs.python.org/3/library/urllib.request.html)
- [Python SQLite 트랜잭션](https://docs.python.org/3/library/sqlite3.html)
