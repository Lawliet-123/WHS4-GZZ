# TelemetryServer 임시 연동 계약

6번 TelemetryServer의 실제 하트비트 API는 아직 정해지지 않았다. 아래는 현재 LocalGuard 클라이언트와 로컬 모의 서버 테스트에 사용한 **임시 계약**이다. 팀에서 서버·Launcher 계약을 확정하면 스키마 버전과 인증·전송 형식을 함께 조정해야 한다.

## 6번 수신 API

`POST /api/heartbeat`, `Content-Type: application/json`, `Accept: application/json`. 원격 서버는 HTTPS를 사용한다. `http://127.0.0.1` 및 `http://localhost`는 로컬 테스트에만 허용한다. 토큰을 발급했다면 `MECCHA_HEARTBEAT_TOKEN` 환경 변수로 전달하고 클라이언트는 `Authorization: Bearer <token>` 헤더로 보낸다. URL 리다이렉트는 따르지 않는다.

요청 본문은 [`heartbeat.schema.json`](heartbeat.schema.json)의 `meccha-heartbeat-3` 구조다. 아래 예시는 필드 모양만 보여준다. 실제 구성 요소의 상태는 측정 결과에 따라 달라진다.

```json
{
  "schema_version": "meccha-heartbeat-3",
  "message_type": "heartbeat",
  "session_id": "yara_test_001",
  "player_id": "player_042",
  "client_id": "8e6c1d9a0f3246ac9b754b738ce6ad92",
  "sequence": 2,
  "timestamp_ms": 5000,
  "sent_at_utc": "2026-09-27T00:00:00+00:00",
  "status": "degraded",
  "components": {
    "game": {"status": "running", "required": true, "pid": 38820, "updated_at_ms": 4900, "stale_after_ms": 15000, "age_ms": 100, "details": {"identity_verified": true}},
    "localguard_file_hash": {"status": "degraded", "required": true, "pid": 31000, "updated_at_ms": 4900, "stale_after_ms": 35000, "age_ms": 100, "details": {"coverage_complete": false, "skipped_count": 3}},
    "localguard_input_signature": {"status": "running", "required": true, "pid": 31000, "updated_at_ms": 4900, "stale_after_ms": 110000, "age_ms": 100, "details": {"scanner": "yara"}}
  },
  "transport": {"configured": true, "consecutive_failures": 0, "last_success_sequence": 1, "last_error_type": null}
}
```

서버는 인증과 스키마를 확인한 후 다음 형태로 응답한다. `session_id`, `client_id`, `sequence`는 받은 요청과 정확히 같아야 한다. 클라이언트는 빈 2xx, HTML, 다른 시퀀스, 리다이렉트를 성공으로 기록하지 않는다.

```json
{"accepted":true,"session_id":"yara_test_001","client_id":"8e6c1d9a0f3246ac9b754b738ce6ad92","sequence":2}
```

서버는 인증된 발신자와 `(session_id, client_id)`별 마지막 **서버 수신 시각** 및 증가하는 시퀀스를 관리한다. 재전송·중복·역순 요청 처리 정책을 합의해야 하며, 클라이언트가 주장한 시각·PID만으로 인증하거나 핵 사용을 확정해서는 안 된다. 합의한 전송 간격은 5~10초이고, 오프라인 운영 경보는 예를 들어 3회 연속 미수신처럼 별도의 임계값으로 만든다. Heartbeat 누락은 탐지 공백이지 자동 밴 근거가 아니다.

Heartbeat는 생존·검사 신선도만 전송한다. 해시/YARA의 규칙 일치, `raw_score`, 중앙 scoring을 위한 탐지 Event는 이 API에 포함되지 않는다. 탐지 Event는 별도로 `events.jsonl`에 남고, `GZZ_TELEMETRY_URL`과 `GZZ_TELEMETRY_TOKEN`이 설정되면 `shared.logger`를 통해 `POST /api/detection`의 전송 대기열에 넣는다. 해당 receiver 라우터는 구현돼 있지만, 이 문서의 임시 하트비트 API와 혼동해서는 안 된다. 중앙 scoring 연결과 실제 서버 배포에서의 종단 검증은 별도로 필요하다.

## Launcher 통합 경계

현재 `yara_scanner.py`는 YARA·해시 구성 요소의 자체 Heartbeat 발신자다. 최종 Launcher가 여러 LocalGuard 구성 요소를 관리할 때는 Launcher 한 곳에서 상태를 집계해 한 발신자로 보내고, 자식 YARA 프로세스의 `--heartbeat-url`은 비워 중복 발신을 피한다. 다른 팀 모듈의 실제 완료·실패 보고 연결은 아직 남아 있다. 시작만 한 상태를 `running`으로 위조하거나 오래된 상태를 정상으로 채우면 안 된다.
