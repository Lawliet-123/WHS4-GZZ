# MECCHA Dashboard

Receiver, Scoring, Launcher와 탐지 모듈의 상태를 한 화면에서 조회하는 8-A React 프론트엔드다. ESP 단일 화면이 아니라 중앙 서버가 가진 세션·플레이어·최종 판정·공통 Event를 표시한다.

## 실행

Node.js 20 이상이 필요하다.

```powershell
cd .\server\Dashboard
npm install
npm run dev
```

브라우저에서 `http://127.0.0.1:4173`을 연다. 개발 서버와 `npm run preview`의 `/dashboard-api` 프록시는 모두 기본적으로 `http://127.0.0.1:8002`의 FastAPI 서버로 전달된다.

검증 명령:

```powershell
npm test -- --run
npm run build
npm run preview
```

## 화면 기능

- Receiver·Scoring·Launcher 연결 상태
- 세션·플레이어·이벤트·의심 판정 수
- 세션·플레이어·모듈·최종 판정 필터와 통합 검색
- 플레이어별 `SUSPICIOUS`, `INCONCLUSIVE`, `NO_ACTIVE_EVIDENCE`, `UNKNOWN` 표시
- 근거 단위, 활성 모듈, 중복 보정, 평가 완료 여부
- 최신 모듈 상태와 모듈별 `raw_score`
- 세션 경과 기준 이벤트 흐름과 이벤트 표
- 공통 Event 7필드, evidence, reasons, event ID 상세 조회 및 JSON 복사
- Launcher와 필수 구성 요소의 서버 계산 상태
- 5초 증분 polling, 수동 새로고침, 오류·빈 상태 처리
- 데스크톱·태블릿·모바일 반응형 UI

## 데이터 연결

기본 화면은 API 응답과 같은 형태의 합성 데이터다. 상단에는 작은 `DEMO` 배지만 표시한다. `연결` 버튼에서 서버 주소와 Dashboard Bearer token을 입력하면 실제 API 모드로 전환된다. 토큰은 React 상태에서만 사용하며 localStorage에 저장하지 않는다.

사용 API:

```text
GET /api/dashboard/overview
GET /api/dashboard/events
GET /api/dashboard/events/{event_id}
GET /api/dashboard/sessions/{session_id}/players/{player_id}/snapshot
GET /api/dashboard/sessions/{session_id}/players/{player_id}/status
Authorization: Bearer <GZZ_DASHBOARD_TOKEN>
```

`overview.events`는 서버 설계상 빈 배열이므로 Event는 `/events`에서 별도로 읽는다. 초기 조회는 cursor 페이지를 이어서 가져오고, 자동 갱신은 마지막 `sequence` 이후 Event만 추가한다. 실서버 요청이 실패해도 합성 데이터로 자동 전환하지 않는다.

## 표시 원칙

- 최종 판정은 Scoring의 값을 그대로 사용한다.
- `UNKNOWN`을 `NO_ACTIVE_EVIDENCE`로 바꾸지 않는다.
- `score`와 `confidence`가 `null`이면 `—`로 표시한다.
- 서로 다른 모듈의 `raw_score`를 합산하거나 0~100 점수를 만들지 않는다.
- Event의 `timestamp_ms`는 세션 경과시간으로 표시하며 절대 시각을 추정하지 않는다.
- Launcher 상태는 브라우저가 계산하지 않고 `/status`와 `overview`의 서버 계산값을 사용한다.
- Receiver `online`은 로컬 detection 저장소 조회 성공, Scoring `online`은 로컬 scoring 조회 성공이라는 범위만 나타낸다.

## 구조

```text
server/Dashboard/
├─ src/
│  ├─ components/           연결 dialog, Event drawer, 공통 아이콘·배지
│  ├─ api.ts                Dashboard v2 API와 Bearer 인증
│  ├─ domain.ts             필터·상태·표시 함수
│  ├─ mockData.ts           실제 응답 구조의 합성 데이터
│  ├─ types.ts              Dashboard v2 타입
│  ├─ App.tsx               화면 상태와 polling
│  └─ styles.css            반응형 디자인
├─ API_REQUIREMENTS.md      실제 API 계약 메모
├─ DESIGN.md                화면 설계 근거
└─ vite.config.ts           4173 포트와 8002 프록시
```

운영 배포에서는 장기 Bearer token을 브라우저 bundle에 포함하지 않고 same-origin BFF 또는 reverse proxy에서 인증을 처리해야 한다. 현재 backend capability상 evidence image는 제공되지 않는다.
