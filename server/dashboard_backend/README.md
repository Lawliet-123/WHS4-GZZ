# 8-B Dashboard backend v2

API 기준: backend v2와 Launcher Overview 상태 연결 수정. 2026-10-06 통합 검증 기준은 main `31dc833`이며 React 화면은 `server/Dashboard`에 있다.

## Launcher Overview 상태 연결

`launcher_heartbeat=False`, 고정 connection unknown, 빈 module_statuses를 수정했습니다. 기존 HeartbeatStore 읽기 함수를 이용하므로 C의 수신·저장·인증·server/main.py는 추가 수정하지 않습니다.

- capability.launcher_heartbeat는 heartbeat 저장소가 연결되어 해당 상태를 조회할 수 있는지 나타냅니다. 실제 연결 여부는 connection 및 launcher_statuses로 확인합니다.
- Launcher는 현재 송신 코드의 계약대로 `client_id=launcher-<시작ms>`와 components.launcher를 함께 가진 발신자로 식별합니다. 스캐너-only 하트비트는 Launcher 상태의 근거로 쓰지 않습니다.
- 같은 session/player의 여러 실행은 client_id의 시작 시각이 가장 최신인 것을 선택합니다. 예전 프로세스의 지연 전송으로 최신 실행을 덮어쓰지 않습니다.
- 최근 healthy는 connection.Launcher.state=online, 필수 구성 요소 문제는degraded, 수신 만료는stale, 종료 보고는stopped, 시작/정리 중은starting/stopping, 데이터 부족은unknown입니다.
- freshness는 received_at_utc 기준이며 component age_ms와 수신 이후 경과시간도 반영합니다. 중복 ACK는freshness를 갱신하지 않습니다.
- module_statuses를 현재 선택된 Launcher의 components로 채웁니다. session_id/player_id/client_id/status_id/last_seen_at을 함께 제공하므로 여러 PC의 같은 module id를 혼동하지 않습니다. launcher 본체도 포함합니다.
- `/status`의 state와 launcher 필드에도 같은 판정 규칙을 적용합니다. source가 잘려 전체 목록을 읽지 못한 경우unknown으로 표시합니다.

특정 PC 연결 상태는 다음처럼 조회합니다.

```text
GET /api/dashboard/overview?session_id=<세션>&player_id=<PC식별자>
```

두 인자는 함께 전달합니다. 이 필터는 연결·모듈 상태 표시 범위만 선택하며 세션/플레이어 목록과 counts의 기존 페이지 범위를 바꾸지 않습니다. 인자가 없으면 connection.Launcher는 반환된 세션 페이지의 각 session/player 상태를 요약하고 scope=returned_session_page로 명시합니다. 단일 PC가 아니라 여러 상태가 섞이면degraded로 표시하며 state_counts를 제공합니다. 전체 DB의 모든 과거 세션을 하나의 접속으로 합치지 않습니다.

Receiver online의scope=local_detection_storage는 이번 요청에서 Shared feed 읽기가 성공했다는 뜻입니다. Scoring online의scope=local_scoring_read는 공개 snapshot 함수로 읽기 성공을 확인했다는 뜻입니다. 둘 모두 모든 원격 탐지기의 전송 성공이나 최종 판정의 정상 여부를 의미하지 않습니다. Scoring 읽기 실패는unavailable, feed 읽기 실패는 기존503으로 처리합니다.

만료 설정은 기존 GZZ_DASHBOARD_STALE_AFTER_MS를 사용하며 기본값30000ms입니다. 네트워크가 끊긴 경우와 Launcher가 비정상 종료한 경우를 구분할 근거가 없으면stale로만 표시합니다.

## 이번 버전

C의 `server/main.py`에 8-B 조회 라우터를 추가 등록하고, B의 conservative-v1 판정을 연결했습니다. C의 기존 `/health`, `/api/dashboard/heartbeat/{session_id}/{client_id}`, `/api/dashboard/verdict/{session_id}/{player_id}`는 그대로 유지합니다.

backend v2와 이후 Launcher 상태 연결은 현재 main에 반영되어 있습니다. React 화면과의 검증 및 실행 방법은 [Dashboard README](../Dashboard/README.md)를 함께 확인합니다. C가 보고한 GodMode 실게임 E2E와 합성 입력 HTTP·브라우저 검증은 구분합니다.

## 판정 표시 계약

| status | 표시 의미 |
|---|---|
| SUSPICIOUS | 평가된 의심 근거 있음. 치트 확정 아님 |
| INCONCLUSIVE | 평가 불완전. 정상 판정으로 표시하지 않음 |
| NO_ACTIVE_EVIDENCE | 현재 평가 범위에서 활성 의심 근거 없음. 전체 검사·PC의 정상 보증 아님 |
| UNKNOWN | 평가 데이터가 없거나 판정 공급자가 연결되지 않음 |

overview의 assessments와 snapshot은 B 판정 status를 그대로 제공합니다. `final_verdict`에 B의 전체 응답을 보존합니다. 여기에는 assessment_complete, evidence_unit_count, active_module_count, active_modules, unresolved_modules, reason_codes 등이 있습니다. `reason_codes`도 별도 필드로 노출합니다.

최종 숫자 점수·치트 확률·confidence는 B가 제공하지 않아 `score=null`, `confidence=null`입니다. raw_score는 원본 탐지 이벤트와 modules에서만 표시하며 임의 합산하지 않습니다. assessment_complete=true가 전체 안티치트 coverage 정상이라는 뜻은 아닙니다.

C의 verdict API는 기록이 없으면404입니다. 8-B의 overview/snapshot에서는 해당 행을 `data_state=missing`, `status=UNKNOWN`, `assessment_available=false`로 표현합니다. 서버 오류는 missing/정상으로 바꾸지 않고503으로 응답합니다.

## C 기존 API와 추가 API

| API | 의미 |
|---|---|
| GET /health | C 서버 HTTP 상태. 전체 모듈 연결 보증 아님 |
| GET /api/dashboard/heartbeat/{session_id}/{client_id} | C의 특정 발신자 최근 heartbeat |
| GET /api/dashboard/verdict/{session_id}/{player_id} | C의 원본 Final Verdict |
| GET /api/dashboard/overview | 8-B 현황, 세션·PC 목록, 판정 projection |
| GET /api/dashboard/events | 8-B 전체 원본 이벤트 목록 |
| GET /api/dashboard/events/{event_id} | 8-B 원본 이벤트 상세 |
| GET /api/dashboard/sessions/{session_id}/players/{player_id}/snapshot | 최신 모듈 상태·정책·Final Verdict |
| GET /api/dashboard/sessions/{session_id}/players/{player_id}/history | 사건형 모듈 이력, 현재 GodMode |
| GET /api/dashboard/sessions/{session_id}/players/{player_id}/status | 여러 heartbeat 발신자와 component 상태 |

목록 기본 limit100, 최대200. 이벤트 필터: session_id/player_id/module/submodule/q. 검색은 event_id와 원본 JSON 문자열에 적용하고 `%`, `_`는 문자로 취급합니다. 상세는 id를 사용하며 timestamp_ms만으로 ID를 만들지 않습니다.

모든 `/api/dashboard/` 경로는 Dashboard 인증을 사용합니다. C의 기존401/404/422/503를 유지합니다. 추가 API의 잘못된 조회/cursor는422, 상세 미존재는404, 색인 갱신 중 아직 찾지 못한 상세는503입니다. 서버 응답 형식 오류는 서버측 원격 client에서502로 처리합니다. 장애 시 mock으로 전환하지 않습니다.

## 인증과 브라우저 연결

중앙 서버 토큰은 서버 환경변수 `GZZ_DASHBOARD_TOKEN`에서 관리합니다. 프론트 소스·URL·공개 메시지에 넣지 않습니다. 현재 중앙 API는 브라우저용 로그인·세션 인증이나 CORS를 제공하지 않습니다.

**기본 모드:** 8-B backend와 C를 같은 FastAPI에서 실행합니다. 내부에서 B 공개 조회 함수를 호출하므로 중앙 서버 토큰을 화면에 전달할 필요가 없습니다. 브라우저에서 API를 호출하려면 별도의 Dashboard 로그인/세션 또는 인증 프록시를 C·8-A와 연결해야 합니다. 이번 코드의 API 인증은 여전히 Bearer이며, 이를 제거한 익명 화면 접근을 제공하지 않습니다.

**원격 조회가 필요한 경우:** `CentralDashboardClient`가 C의 health/heartbeat/verdict를 서버에서 호출할 수 있습니다.

```python
import os
from server.dashboard_backend.central_client import CentralDashboardClient

central = CentralDashboardClient(
    os.environ["GZZ_CENTRAL_URL"],
    os.environ["GZZ_CENTRAL_DASHBOARD_TOKEN"],
)
verdict = central.verdict(session_id, player_id)
heartbeat = central.heartbeat(session_id, client_id)
```

이 두 환경변수는 선택적인 원격 client 예시용이며 기본 C 앱은 사용하지 않습니다. `DashboardService(verdict_provider=central.verdict, ...)`로 연결할 수 있습니다. 다만 원격 C가 목록·원본 로그 API를 제공하지 않으므로 이 client만으로 원격 전체 타임라인을 얻을 수는 없습니다. 기본 same-server 배치에서는 이 제한 없이 동일 writer의 공개 feed를 읽습니다.

원격client는 HTTPS를 요구하며 localhost loopback HTTP만 허용합니다. redirect를 따라가지 않아 토큰을 다른 URL로 전달하지 않습니다. 오류 메시지에 실제 토큰이나 upstream 응답 본문을 담지 않습니다.

## C 앱에 들어간 변경

- 시작 시 기존 writer/scoring 복구 뒤 `app.state.dashboard` 생성.
- C의 verifier를 공유해 추가 조회 라우터 등록.
- B의 `get_player_final_verdict`를 provider로 주입. 기본 시간 기반 overlap 창은 설정하지 않음.
- receiver HeartbeatStore에 list_latest/session_inventory 읽기 함수 추가. 기존 accept·ACK·중복 규칙은 변경하지 않음.
- `GZZ_DASHBOARD_INDEX`: 조회 색인 파일. 기본 server/logs/dashboard/dashboard.sqlite3.
- `GZZ_DASHBOARD_CURSOR_SECRET`: 선택적 cursor 서명 비밀. 미설정 시 Dashboard 토큰을 사용하며 토큰 변경 시 이전 cursor 무효.
- `GZZ_DASHBOARD_STALE_AFTER_MS`: 수신 만료 시간. 기본30000ms는 조정 가능한 초기값이며 팀 합의 전 정책안.

기존 C 서버 파일을 통째로 교체하지 말고 기준 커밋에 맞는 patch를 검토해 통합하세요. 기존 v1 패치와 중복 적용하지 않습니다.

## Polling과 목록 처리

초기 화면 갱신은5초 polling을 제안합니다. SSE/WebSocket은 이번에 추가하지 않았습니다.

events의 첫 응답은 items/next_cursor/has_more/through_sequence/index입니다. 같은 필터로 cursor 페이지를 끝까지 읽습니다. 순회 동안 through_sequence를 고정하므로 새 이벤트가 페이지에 섞이지 않습니다. 완료 후 다음 polling에 이전 through_sequence를 after_sequence로 전달합니다. cursor와 after_sequence는 동시에 사용하지 않습니다. 필터 변경 시 둘 다 초기화합니다.

정렬은 서버 저장 sequence 순서입니다. 원본 timestamp_ms는 보존하고 evidence.time_basis가 명시적으로 session_relative 또는 unix_epoch_ms인 경우에만 해당 기준을 전달합니다. 그 외 자료는 time_basis=unknown입니다. 서로 다른 세션의 경과시간을 절대 날짜순으로 비교하지 않습니다.

## 2026-10-07 시간·판정 근거 추가 필드

Shared Event의 7필드, ACK, 중복 처리, Scoring 점수·정책은 변경하지 않습니다. 아래 항목은 저장·조회용 추가 메타데이터입니다.

- `DetectionWriter.iter_stored()`의 `StoredDetection.received_at_utc`: 해당 writer의 최초 저장 시각(UTC). 기존 ledger는 nullable 열을 추가하고 과거 시각은 채우지 않습니다. 재전송·복구·재시작은 최초 값을 유지합니다.
- `/events` 목록·상세의 `received_at_utc`: 위 시각을 그대로 전달합니다. 관측 시각이 아니며 UI에서 `수신 … KST`로 구분합니다. 기존 index도 nullable migration을 적용합니다. `observed_at_utc`는 현재 backend에서 생성하지 않습니다.
- `/snapshot`의 `policy.aggregate_evidence`, `policy.aggregate_risk`, `policy.module_evidence`: 주입된 Scoring reader와 기존 B 정책 함수로 읽기 전용 설명을 구성합니다. 전역 Scoring 설정을 바꾸거나 새로운 최종 판정을 발행하지 않습니다.
- `module_evidence`는 `module`, `submodule`, `signal`, `latest_event`, `retained_incident_event`로 구성합니다. `signal`에서 현재 상태와 실제 calibration threshold/mode를 읽고, 사건 보존형 정책은 최신 raw 0과 이전 유효 사건을 구분합니다. 사건 ID는 원본 `event_id`입니다.
- 이력 reader가 없는 호환 구현에서는 보존 사건을 정상 종료로 해석하지 않습니다. DLL 채널은 미확정, AutoPaint·GodMode는 이력 필요 상태로 남깁니다.

최종 판정은 최상위 `final_verdict`와 `assessment_available`을 기준으로 표시합니다. 공급자가 없으면 설명에 ACTIVE 신호가 있어도 `UNKNOWN`입니다. `aggregate_risk`의 빈 입력 결과를 정상 판정으로 대신 사용하지 않습니다. snapshot·이력·Final Verdict는 단일 transaction/watermark가 아니므로 실시간 수신 중 조회 시점 차이가 생길 수 있습니다.

이벤트 목록의 상태는 해당 Event의 명시적 `evidence.status`입니다. 플레이어의 현재 Final Verdict를 과거 Event 상태에 복사하지 않습니다. 같은 핸들의 raw 0 상태와 플레이어의 다른 활성 근거가 공존하는 경우도 보존합니다.

색인은 Shared.iter_stored 공개 함수로 갱신합니다. 한 요청에서 기본2000건까지 반영하며 index.catching_up=true이면 이후 polling으로 나머지를 확인합니다. 색인은 재구축 가능하지만 원본 writer JSONL/ledger는 함께 보존해야 합니다. 원본 writer를 교체했다면 해당 색인도 새 경로로 생성하세요.

overview는 limit/after_session으로 세션 페이지를 반환합니다. 탐지 없이 heartbeat만 있는 세션도 목록에서 발견할 수 있습니다. counts는 scope=indexed_events이며 이벤트가 있는 세션·PC 기준 수입니다. heartbeat만 있는 세션까지 포함한 전체 접속자 수로 사용하지 않습니다. players·assessments는 반환 세션 페이지에 한정됩니다.

overview의 events는 빈 배열이며 별도 목록 API를 사용합니다. module_statuses는 위 Launcher Overview 규칙으로 채웁니다. 세션·PC 전체 보안 판정은 임의로 만들지 않고 assessments의 session/player 단위로 읽습니다.

## Heartbeat 연결 표시

C의 heartbeat 조회는 session_id+client_id, verdict 조회는 session_id+player_id입니다. 둘을 혼동하지 않습니다.

8-B의 status는 session/player에 대응하는 발신자들을 조회합니다. 같은 player에 여러 client_id가 있어도 한 source로 합치지 않습니다. payload.status가 healthy여도 서버 received_at_utc가 만료되면 stale입니다. 중복 ACK는 생존 시각을 갱신하지 않습니다. component의 age_ms와 서버 수신 후 시간도 반영합니다.

stopped 보고 없이 stale이면 네트워크 장애와 프로세스 비정상 종료를 단정하지 않습니다. input_signature heartbeat를 Launcher 전체 heartbeat로 간주하지 않습니다. 현재 Launcher 계약에 맞는 발신자가 있으면 위 규칙으로 상태를 제공합니다. Receiver/Scoring의 readiness scope와 Launcher 수신 freshness scope는 서로 구분합니다.

## 로컬 실행

레포 루트에서 의존성을 설치하고 C가 사용하는 환경변수를 준비합니다.

```powershell
python -m pip install -r server/requirements-dev.txt

$env:GZZ_TELEMETRY_TOKEN = '<로컬 탐지 토큰>'
$env:MECCHA_HEARTBEAT_TOKEN = '<로컬 heartbeat 토큰>'
$env:GZZ_DASHBOARD_TOKEN = '<로컬 Dashboard 토큰>'
$env:MECCHA_HEARTBEAT_DB = 'work/dashboard-local/heartbeat.sqlite3'
$env:GZZ_TELEMETRY_LOG_ROOT = 'work/dashboard-local/detections'
$env:GZZ_SCORING_DB = 'work/dashboard-local/scoring.sqlite3'
$env:GZZ_DASHBOARD_INDEX = 'work/dashboard-local/dashboard.sqlite3'

python -m uvicorn server.main:app --host 127.0.0.1 --port 8002
```

127.0.0.1:8002는 실행한 PC에서만 쓸 수 있는 주소이며 운영 서버 주소가 아닙니다. 다른 PC/배포 접근 주소와 HTTPS는 C가 확정해야 합니다. 위 명령은 API 서버만 실행합니다. 별도 터미널에서 `server/Dashboard`의 `npm run dev`로 React 화면을 실행하면 `/dashboard-api`가 이 서버로 전달됩니다.

## 검증

```powershell
python -m unittest discover -s server/dashboard_backend/tests -v
python -m unittest discover -s server/receiver/tests -v
python -m unittest discover -s server/scoring/tests -v
python -m unittest discover -s shared/tests -v
python -m server.dashboard_backend.smoke
```

smoke는 C의 실제 server.main 앱을 임시 데이터·임의 로컬 포트로 실행합니다. Shared 실전송, 저장·scoring·기존 C verdict/heartbeat·신규 조회·서버측 client를 확인한 뒤 서버를 종료합니다. 입력은 합성 데이터이며 게임은 실행하지 않습니다.

## 남은 공동 검증

- 운영 서버 배포 및 브라우저 인증 세션 연결. 공개 `/GZZ/`는 API 연결이 차단된 프론트 DEMO입니다.
- 합성 입력으로 확인한 범위와 실제 게임 검증 범위는 [화면 통합 기록](../Dashboard/INTEGRATION_STATUS.md)에서 구분합니다.
- Launcher 전체 heartbeat와 정보원 역할·모듈 매핑 확정.
- GodMode 외 다른 탐지기의 실제 게임/중앙 서버/화면 종단 검증.
- 증거 이미지·민감정보 제거된 로그 API는 아직 null.
- C가 보고한 실게임 GodMode 검증을 실제 화면까지 연결하는 공동 테스트.
