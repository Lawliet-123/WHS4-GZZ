# Dashboard v2 연동 계약

구현 기준은 `server/dashboard_backend/router.py`, `service.py`, `index.py`다. 모든 `/api/dashboard/*` 요청은 다음 헤더를 사용한다.

```http
Authorization: Bearer <GZZ_DASHBOARD_TOKEN>
Accept: application/json
```

## Overview

```http
GET /api/dashboard/overview?limit=100
```

화면에서 사용하는 필드:

- `connection.Receiver`, `connection.Scoring`, `connection.Launcher`
- `counts.events`, `counts.sessions`, `counts.players`
- `sessions`, `players`, `assessments`
- `module_statuses`, `launcher_statuses`
- `index.through_sequence`, `index.catching_up`

`assessments[].score`와 `confidence`는 현재 `null`이다. `assessment_available=false`, `status=UNKNOWN`, `data_state=missing|not_connected`를 정상 판정으로 바꾸지 않는다. `events`는 이 응답에서 의도적으로 빈 배열이다.

## Events

```http
GET /api/dashboard/events?limit=200&after_sequence=0
GET /api/dashboard/events?limit=200&cursor=<next_cursor>
```

필터는 `session_id`, `player_id`, `module`, `submodule`, `q`를 지원한다. 첫 응답의 `through_sequence`가 조회 범위를 고정하고, `has_more=true`이면 `next_cursor`로 다음 페이지를 읽는다. `cursor`와 `after_sequence`를 동시에 보내면 안 된다.

Event는 공통 7필드와 조회 metadata로 구성된다.

```json
{
  "session_id": "esp_001",
  "player_id": "player_042",
  "module": "esp",
  "timestamp_ms": 66282,
  "evidence": {"submodule": "overlay_correlation"},
  "reasons": ["PROCESS_VM_READ handle active"],
  "raw_score": 3,
  "id": "canonical-event-id",
  "sequence": 125,
  "event_kind": "detection",
  "time_basis": "unknown",
  "evidence_image": null,
  "log_excerpt": null
}
```

자동 갱신은 현재 가장 큰 `sequence`를 `after_sequence`로 보내 새 Event만 합친다. 같은 `timestamp_ms`의 Event가 있어도 `id`와 `sequence`로 구분한다.

## Event 상세

```http
GET /api/dashboard/events/{event_id}
```

목록과 같은 Event 구조를 반환한다. 현재 backend capability `evidence_images=false`이므로 이미지 URL을 가정하지 않는다.

## 대상 Snapshot

```http
GET /api/dashboard/sessions/{session_id}/players/{player_id}/snapshot
```

최종 판정과 모듈별 최신 상태를 반환한다.

- `status`: `SUSPICIOUS | INCONCLUSIVE | NO_ACTIVE_EVIDENCE | UNKNOWN`
- `final_verdict`: nullable
- `modules[]`: 최신 공통 Event 상태의 `event_id`, `sequence`, `raw_score`, `evidence`, `reasons`
- `policy`: Scoring 정책 snapshot

## 대상 실행 상태

```http
GET /api/dashboard/sessions/{session_id}/players/{player_id}/status
```

`launcher.state`와 `sources[].components[].state`는 서버 수신 시각과 component age를 반영한 값이다. UI에서 raw heartbeat 시각으로 다시 계산하지 않는다. `source list truncated`, `no Launcher heartbeat` 같은 reason도 원문 상태를 바꾸지 않는다.

## 오류 처리

| HTTP | 화면 처리 |
| --- | --- |
| 401 | 인증 정보 오류 |
| 403 | 접근 권한 오류 |
| 404 | 대상 데이터 없음 |
| 422 | 조회 조건 또는 cursor 오류 |
| 502 | 중앙 서버 응답 오류 |
| 503 | 저장소·중앙 서버 사용 불가 |

오류 응답 본문과 토큰을 화면이나 console에 그대로 출력하지 않는다. 실서버 오류 시 합성 데이터로 자동 대체하지 않는다.
