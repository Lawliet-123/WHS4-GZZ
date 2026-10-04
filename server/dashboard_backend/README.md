# 8-B Dashboard backend v2

기준: 2026-10-04, main `65b5cea` (PR #91 서버 통합 및 #93 반영).

## 이번 버전

C의 `server/main.py`에 8-B 조회 라우터를 추가 등록하고, B의 conservative-v1 판정을 연결했습니다. C의 기존 `/health`, `/api/dashboard/heartbeat/{session_id}/{client_id}`, `/api/dashboard/verdict/{session_id}/{player_id}`는 그대로 유지합니다.

이번 변경은 로컬 브랜치의 코드입니다. 팀 GitHub에 push·PR·배포하지 않았습니다. C가 보고한 GodMode 실게임 E2E와 이번 합성 입력 HTTP 검증은 구분합니다.

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

정렬은 서버 저장 sequence 순서입니다. 원본 timestamp_ms는 보존하되 아직 모든 모듈의 시작점이 검증되지 않아 time_basis=unknown입니다. 서로 다른 세션의 경과시간을 절대 날짜순으로 비교하지 않습니다.

색인은 Shared.iter_stored 공개 함수로 갱신합니다. 한 요청에서 기본2000건까지 반영하며 index.catching_up=true이면 이후 polling으로 나머지를 확인합니다. 색인은 재구축 가능하지만 원본 writer JSONL/ledger는 함께 보존해야 합니다. 원본 writer를 교체했다면 해당 색인도 새 경로로 생성하세요.

overview는 limit/after_session으로 세션 페이지를 반환합니다. 탐지 없이 heartbeat만 있는 세션도 목록에서 발견할 수 있습니다. counts는 scope=indexed_events이며 이벤트가 있는 세션·PC 기준 수입니다. heartbeat만 있는 세션까지 포함한 전체 접속자 수로 사용하지 않습니다. players·assessments는 반환 세션 페이지에 한정됩니다.

overview의 events/module_statuses는 빈 배열이며 별도 목록·status API를 사용합니다. 8-A의 임시 단일 overview 모델을 이에 맞춰 수정해야 합니다. 세션·PC 전체 aggregate status는 임의로 만들지 않고 판정은 assessments의 session/player 단위로 읽습니다.

## Heartbeat 연결 표시

C의 heartbeat 조회는 session_id+client_id, verdict 조회는 session_id+player_id입니다. 둘을 혼동하지 않습니다.

8-B의 status는 session/player에 대응하는 발신자들을 조회합니다. 같은 player에 여러 client_id가 있어도 한 source로 합치지 않습니다. payload.status가 healthy여도 서버 received_at_utc가 만료되면 stale입니다. 중복 ACK는 생존 시각을 갱신하지 않습니다. component의 age_ms와 서버 수신 후 시간도 반영합니다.

stopped 보고 없이 stale이면 네트워크 장애와 프로세스 비정상 종료를 단정하지 않습니다. input_signature heartbeat를 Launcher 전체 heartbeat로 간주하지 않습니다. source 범위 합의 전 전체 state는unknown입니다. connection의 Receiver/Scoring/Launcher도 측정 근거 없이online으로 채우지 않습니다.

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

127.0.0.1:8002는 실행한 PC에서만 쓸 수 있는 주소이며 운영 서버 주소가 아닙니다. 다른 PC/배포 접근 주소와 HTTPS는 C가 확정해야 합니다. 8-A 화면 코드는 아직 이번 기준 main에 없으므로 위 명령은 API 서버만 실행합니다.

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

- C와 로컬 패치 검토·팀 저장소 통합·배포 및 인증 세션 연결.
- 8-A 화면에서 판정3종·null·이유 코드·missing/장애·목록·상세 표시 검증.
- Launcher 전체 heartbeat와 정보원 역할·모듈 매핑 확정.
- GodMode 외 다른 탐지기의 실제 게임/중앙 서버/화면 종단 검증.
- 증거 이미지·민감정보 제거된 로그 API는 아직 null.
- C가 보고한 실게임 GodMode 검증을 실제 화면까지 연결하는 공동 테스트.
