# Dashboard v2 연동 계약

구현 기준은 `server/dashboard_backend/router.py`, `service.py`, `index.py`와 `server/scoring`이다. 모든 `/api/dashboard/*` 요청은 다음 헤더를 사용한다.

```http
Authorization: Bearer <GZZ_DASHBOARD_TOKEN>
Accept: application/json
```

## Overview

```http
GET /api/dashboard/overview?limit=100
GET /api/dashboard/overview?after_session=<session>&limit=100
GET /api/dashboard/overview?session_id=<session>&player_id=<player>
```

`session_id`와 `player_id`는 함께 보내야 한다. 이 두 값은 Launcher 연결·component 상태의 조회 범위를 선택하며 세션 목록과 Event count를 필터링하는 인자가 아니다.

주요 필드:

- `capabilities`: `final_assessment`, `launcher_heartbeat`, `heartbeat_query`, `evidence_images`
- `connection.Receiver`: `scope=local_detection_storage`의 로컬 저장소 읽기 상태
- `connection.Scoring`: `scope=local_scoring_read`의 판정 조회 상태
- `connection.Launcher`: 집계 상태, `state_counts`, `observed_pairs`, `connected_pairs`
- `counts`: `scope=indexed_events`, Event·세션·플레이어·운영 Event 수
- `sessions`, `players`, `assessments`: 반환된 세션 페이지의 대상과 판정
- `module_statuses`: 선택된 Launcher source의 component 상태
- `launcher_statuses`: 세션·플레이어별 Launcher 판정
- `index`: Shared feed 색인 watermark와 `catching_up`

`counts.sessions`와 `counts.players`는 Event가 색인된 대상 수다. heartbeat만 있는 세션도 `sessions`, `players`, `assessments`, Launcher 상태에는 나타날 수 있으므로 전체 접속자 수로 해석하지 않는다.

`assessments[].score`와 `confidence`는 현재 `null`이다. `assessment_available=false`, `status=UNKNOWN`, `data_state=missing|not_connected`를 정상 판정으로 바꾸지 않는다. `overview.events`는 의도적으로 빈 배열이다.

## Event 목록

```http
GET /api/dashboard/events?limit=200&after_sequence=0
GET /api/dashboard/events?limit=200&cursor=<next_cursor>
```

필터는 `session_id`, `player_id`, `module`, `submodule`, `q`를 지원한다. 첫 응답의 `through_sequence`가 조회 범위를 고정한다. `has_more=true`이면 같은 필터와 `next_cursor`로 이어 읽으며 `cursor`와 `after_sequence`를 동시에 보내지 않는다.

```json
{
  "session_id": "esp_001",
  "player_id": "player_042",
  "module": "external_access",
  "timestamp_ms": 58200,
  "evidence": {
    "submodule": "module_integrity",
    "status": "NORMAL",
    "target_process": "PenguinHotel-Win64-Shipping.exe",
    "target_pid": 6840,
    "scan_duration_ms": 142,
    "observed_modules": 136,
    "added_modules": 0,
    "removed_modules": 0,
    "changed_modules": 0,
    "allowed_modules": 0,
    "baseline_created": false,
    "initial_audit_performed": false
  },
  "reasons": [],
  "raw_score": 0,
  "id": "canonical-event-id",
  "sequence": 125,
  "event_kind": "detection",
  "time_basis": "unknown",
  "evidence_image": null,
  "log_excerpt": null
}
```

`module_integrity`는 Launcher component 이름이다. 중앙 Event에서는 별도 root module을 만들지 않고 `module=external_access`, `evidence.submodule=module_integrity`를 사용한다. 다른 하위 채널은 `submodule=external_process`다.

`event_kind=detection`은 탐지기 채널에서 온 관측이라는 뜻이며 양수 탐지 확정을 뜻하지 않는다. 정상 0점 sample도 포함된다. `event_kind=operational`은 `selfdefense` 또는 `evidence.kind=module_health`인 운영 상태 Event다.

## Event 상세

```http
GET /api/dashboard/events/{event_id}
```

목록과 같은 구조를 반환한다. 현재 `capabilities.evidence_images=false`이고 `evidence_image`, `log_excerpt`는 `null`이다. 지원 여부를 확인하지 않고 이미지 URL을 추정하지 않는다.

## 대상 Snapshot

```http
GET /api/dashboard/sessions/{session_id}/players/{player_id}/snapshot
```

- `status`: `SUSPICIOUS | INCONCLUSIVE | NO_ACTIVE_EVIDENCE | UNKNOWN`
- `final_verdict`: `assessment_complete`, 근거 단위 수, 활성·참고·미해결·보류·사용 불가 모듈, reason code
- `modules[]`: Scoring latest state의 최신 원본 관측. 전체 이력이나 실행 상태가 아님
- `policy.modules[]`: 원본 state와 `evaluation.signal`, `evaluation.annotations`
- `data_state`: `available | missing | not_connected`

`policy`에는 emission, policy state, raw fraction, issues, entity key, overlap 후보, notes가 포함된다. 이 값은 실행 여부가 아니며 여러 `raw_score`를 합산한 최종 점수도 아니다.

현재 Replay calibration에서 Noclip threshold는 3이다. 유효한 최신 Noclip `raw_score=3`은 ACTIVE 근거가 될 수 있다. ESP와 `external_access`는 pending이므로 양수 원본 관측만으로 Final Verdict를 `SUSPICIOUS`로 만들지 않는다.

## 사건 이력

```http
GET /api/dashboard/sessions/{session_id}/players/{player_id}/history?module=godmode&after_sequence=0&limit=100
```

현재 Dashboard history endpoint는 GodMode event-delta 이력용이다. 응답의 `final_assessment=false`는 이 목록 자체가 최종 판정이 아니라는 뜻이다. Whistle window history와 `external_access` scoped-state의 전용 Dashboard API는 아직 제공되지 않으므로 원본 `/events`의 module/submodule 조회를 사용하고 8-B와 확장을 협의한다.

## 실행 상태

```http
GET /api/dashboard/sessions/{session_id}/players/{player_id}/status
```

- `launcher`: 최신 Launcher 실행의 서버 계산 상태와 선택 근거
- `sources[]`: Launcher 및 component 발신자별 상태
- `components[]`: `state`, `reported_status`, `required`, `pid`, freshness, details
- `transport`: configured, 연속 실패 수, 마지막 성공 sequence, 마지막 오류 종류
- `has_more_sources`: source 100개 제한으로 잘렸는지 여부

`state`는 서버 수신 시각과 component `age_ms`, `stale_after_ms`를 반영한 값이다. 브라우저가 raw heartbeat만 보고 다시 계산하지 않는다. `stale`만으로 네트워크 장애와 프로세스 비정상 종료를 임의로 구분하지 않는다.

Launcher 본체와 13개 module component는 Event module과 다른 계층이다. `input_signature`, `memory_integrity`, `whistle_spoofing` 같은 component 이름을 그대로 Scoring module로 사용하지 않는다.

## 갱신과 페이지 처리

- Overview: `session_page.has_more`와 `next_after_session`으로 이어 읽음
- Event 초기 조회: cursor를 끝까지 순회하고 첫 `through_sequence` 유지
- Event 증분 조회: 마지막 서버 `sequence`를 `after_sequence`로 전달
- 필터 변경: 기존 cursor와 watermark를 새 조건에 재사용하지 않음
- `index.catching_up=true`: 색인이 한 요청의 sync budget을 소진한 상태이므로 다음 갱신에서 계속 확인

모든 페이지를 무제한으로 브라우저 메모리에 적재하지 않는다. 안전 한도에 도달하면 부분 자료임을 표시하고 서버 페이지 이동 또는 추가 로드를 제공해야 한다.

## 오류와 인증

| HTTP | 화면 처리 |
| --- | --- |
| 401 | Dashboard 인증 정보 오류 |
| 403 | 접근 권한 오류 |
| 404 | 대상 또는 Event 없음 |
| 422 | 조회 조건·ID·cursor 오류 |
| 502 | 중앙 서버 응답 형식 오류 |
| 503 | 저장소·Scoring·중앙 서버 사용 불가 |

오류 응답 본문과 토큰을 화면이나 console에 그대로 출력하지 않는다. 실서버 오류 시 합성 데이터로 자동 전환하지 않는다. Snapshot과 status는 서로 다른 데이터 계층이므로 한쪽 조회 실패가 다른 쪽의 마지막 성공 상태를 지우지 않게 처리한다.

중앙 API는 아직 브라우저 로그인·세션 인증이나 일반 CORS를 제공하지 않는다. 직접 Bearer token을 입력하는 방식은 로컬 통합 시험용이다. 운영 배포는 same-origin 인증 프록시 또는 별도 Dashboard 세션을 사용한다.
