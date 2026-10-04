# Dashboard 화면 설계

## 목적

이 화면의 기준 단위는 ESP나 특정 담당 모듈이 아니라 한 세션에서 함께 실행되는 종합 안티치트다. 운영자는 다음 질문에 위에서 아래 순서로 답을 얻어야 한다.

1. 중앙 수집·판정·Launcher 연결이 살아 있는가
2. 13개 보호·탐지 컴포넌트 중 실행 실패나 지연이 있는가
3. 어떤 세션과 플레이어가 현재 검토 대상인가
4. 최종 판정에 어떤 Event 채널과 정책 상태가 사용됐는가
5. 원본 reasons·evidence·운영 로그는 무엇인가

## 정보 구조

```text
┌──────────┬────────────────────────────────────────────────┐
│ MECCHA   │ 종합 현황                     DEMO/LIVE · 연결 │
│          ├────────────────────────────────────────────────┤
│ 종합     │ Receiver · Scoring · Launcher · 인덱스        │
│ 대상     ├────────────────────────────────────────────────┤
│ 모듈     │ Event 범위 KPI · 판정 분포 · 운영 Event       │
│ 이벤트   ├────────────────────────────────────────────────┤
│ 시스템   │ 13개 Launcher 컴포넌트 상태·전송 상태         │
│          ├───────────┬────────────────────┬───────────────┤
│          │ 대상 목록 │ 최종 판정·정책     │ 최신 원본 관측│
│          ├───────────┴────────────────────┴───────────────┤
│          │ sequence 기반 통합 타임라인 · 종류/모듈 구분  │
│          ├────────────────────────────────────────────────┤
│          │ 전체 Event 표 → 우측 evidence 상세 drawer     │
└──────────┴────────────────────────────────────────────────┘
```

화면에는 데이터 이름, 상태, 수치와 조작 요소를 우선한다. 긴 소개 문구나 반복 면책 박스는 두지 않는다. 합성 데이터는 `DEMO`, 실서버 데이터는 `LIVE` 배지로 구분한다.

## 실행 상태와 탐지 채널 분리

Launcher heartbeat의 component는 프로세스 실행 단위다. `input_signature` 하나가 YARA와 실행 파일 해시 Event를 만들고, `memory_integrity` 하나가 여러 메모리 무결성 Event 채널을 만든다. 반대로 `external_access`와 `module_integrity`는 실행 컴포넌트가 둘이지만 중앙 Event에서는 같은 `module=external_access`를 사용하고 `evidence.submodule`로 나뉜다.

따라서 화면을 두 층으로 분리한다.

- 실행 상태: `launcher`, 13개 component, required, pid, freshness, source, transport
- 탐지 근거: Event module/submodule, raw score, policy, Final Verdict

실행 중인 프로세스를 의심 근거로 칠하지 않고, 양수 원본 관측을 실행 실패로 표시하지 않는다.

## 최종 판정과 원점수

| 값 | 화면 표시 | 의미 |
| --- | --- | --- |
| `SUSPICIOUS` | 의심 근거 있음 | 보정된 ACTIVE 근거가 존재함 |
| `INCONCLUSIVE` | 판단 보류 | 미해결·보류·측정 불가 신호가 남음 |
| `NO_ACTIVE_EVIDENCE` | 활성 근거 없음 | 현재 평가 가능한 범위에 ACTIVE 근거가 없음 |
| `UNKNOWN` | 판정 없음 | 판정 공급자 미연결 또는 데이터 없음 |

`raw_score`는 Event 생산자가 만든 원본 값이다. 모듈마다 상한, emission 방식과 threshold가 다르므로 `raw_score > 0`만으로 위험 색을 선택하지 않는다. 위험 강조는 Scoring의 Final Verdict와 policy/calibration 결과를 기준으로 한다. 숫자 `score`와 `confidence`가 `null`이면 `—`로 유지한다.

정책 영역은 최신 관측마다 다음을 보여 준다.

- `signal.state`, `emission`, 측정 가능 여부
- threshold 확정·pending·advisory 구분
- event history 또는 entity scope 필요 여부
- issues, overlap 후보와 미해결 사유

## 전역 관제

- Receiver: 이번 조회에서 Shared detection 저장소를 읽을 수 있었는지 표시
- Scoring: 공개 snapshot 조회가 가능한지 표시
- Launcher: 반환된 세션·플레이어의 `state_counts`, `connected_pairs/observed_pairs` 표시
- 모듈 표: `overview.module_statuses`의 모든 component를 세션·플레이어별로 표시
- Launcher 표: `overview.launcher_statuses`에서 stale, stopped, degraded, unknown을 빠르게 찾음
- capability: Final Verdict, heartbeat, evidence image 지원 여부를 데이터 없음과 구분

`counts.scope=indexed_events`이면 세션·플레이어 KPI에 Event 기반 범위임을 표시한다. heartbeat-only 대상은 전역 실행 상태에는 포함될 수 있지만 indexed count에는 포함되지 않는다.

## 타임라인과 Event

Event는 `sequence`가 서버 저장 순서이고 `timestamp_ms`는 생산자가 보낸 경과시간이다. 현재 backend의 `time_basis=unknown`은 모든 모듈 시계가 검증됐다는 뜻이 아니다. 기본 타임라인은 `sequence`를 안정적인 순서로 사용하고, 경과시간은 보조 정보와 기준 미확인 표시로 제공한다.

Event 화면은 다음을 구분한다.

- `event_kind=detection`: 탐지기 관측. 0점 정상 sample도 포함될 수 있음
- `event_kind=operational`: SelfDefense 등 생존·운영 상태 기록
- `module`: 중앙 Scoring 채널
- `submodule`: 현재 `external_access`의 `external_process`, `module_integrity` 등

따라서 표 제목은 전체 Event 또는 관측 로그로 두고 Event 종류 필터를 제공한다. 정상 0점 Event를 탐지 확정처럼 표시하지 않는다.

## 상호작용

- 세션 선택에 따라 플레이어 선택지를 좁혀 잘못된 조합을 줄인다.
- 세션·플레이어·Event module·submodule·Event 종류·최종 판정·검색을 함께 지원한다.
- 대상 선택 시 판정 snapshot과 heartbeat status를 독립적으로 조회해 한쪽 장애가 다른 쪽 정보를 숨기지 않게 한다.
- Event 행이나 타임라인 항목을 누르면 원본 공통 7필드와 backend metadata를 상세 drawer에 표시한다.
- `module_statuses`는 일부만 자르지 않고 전체 보기와 문제 상태 우선 정렬을 지원한다.
- API 오류 시 마지막 성공 자료를 유지하고 오류 범위를 표시하며 demo로 자동 전환하지 않는다.

## 반응형과 접근성

- 넓은 화면: 전역 상태, 모듈 표, 대상 master-detail을 동시에 표시
- 중간 화면: 모듈 그룹과 상세 카드를 세로로 재배치
- 작은 화면: sidebar drawer, 필터 1열, Event 표 카드 전환

색상만으로 상태를 구분하지 않고 아이콘과 문구를 같이 사용한다. 표·drawer·dialog는 키보드로 조작할 수 있어야 하며 긴 ID와 evidence는 레이아웃을 넘지 않게 줄바꿈한다.
