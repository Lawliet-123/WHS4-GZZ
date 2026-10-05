# Dashboard 화면 설계

## 목적

이 화면의 기준 단위는 ESP나 특정 담당 모듈이 아니라 종합 안티치트다. 긴 한 화면을 내려가며 모든 내용을 찾는 대신, 작업 목적에 맞는 페이지에서 다음을 확인한다.

1. 중앙 수집·판정·Launcher 연결이 살아 있는가
2. 13개 보호·탐지 컴포넌트 중 실행 실패나 지연이 있는가
3. 어떤 세션과 플레이어가 현재 검토 대상인가
4. 최종 판정에 어떤 Event 채널과 정책 상태가 사용됐는가
5. 원본 reasons·evidence·운영 로그는 무엇인가

## 정보 구조

| 탐색 | 페이지 | 우선 정보 |
| --- | --- | --- |
| 분석 | 종합 현황 | 작은 핵심 지표, 판정 분포·탐지기별 관측, 중앙 탐지 표, 검토 대상 표 |
| 분석 | 세션 | 세션별 대상·채널·연결 상태·플레이어 판정 요약, 수신 순서별 관측 그래프 |
| 분석 | 플레이어 | 대상 목록 + 판정 / 타임라인 / 모듈 신호 / 실행 상태 / 사건 이력 |
| 분석 | 이벤트 | 필터 + 수신 순서 표 + 원본 상세 drawer |
| 운영 | 보호 모듈 | 그룹별 모듈 표, 상태, 관측 수, 이벤트 조회 |
| 운영 | 시스템 | 연결·capability·Launcher 전체 상태 |

각 페이지는 `#/overview` 등 고유 주소를 가지며 활성 페이지의 내용만 마운트한다. 뒤로/앞으로, 직접 접속, 새로고침을 지원한다. 메뉴 선택은 스크롤 위치가 아닌 현재 주소에 대응한다. 페이지 이동 시 제목에 포커스를 옮기고, 플레이어 상세 탭은 방향키·Home·End로 이동할 수 있다.

화면에는 데이터 이름, 상태, 수치와 조작 요소를 우선한다. 긴 소개 문구나 반복 면책 박스는 두지 않는다. 합성 데이터는 `DEMO`, 실서버 데이터는 `LIVE` 배지로 구분한다.

## 시각 기준과 참고 자료

2026-10-06 개편은 제공된 6쪽 안티치트 와이어프레임의 개요·대상·이벤트·모듈·시스템 흐름을 참고했다. 시각 기준은 [CrowdStrike Falcon Insight XDR 공식 walkthrough](https://www.crowdstrike.com/tech-hub/endpoint-security/falcon-insight-xdr-walkthrough/)와 [SentinelOne Operations Center 공식 조사 사례](https://www.sentinelone.com/blog/singularity-operations-center-unified-security-operations-for-rapid-triage/)다. 제품의 브랜드나 화면을 복제하지 않고, 통합 목록에서 근거를 확인하는 관제 흐름을 적용한다. 와이어프레임의 제재·정상 처리·검토 메모 버튼은 현재 API에 없으므로 구현된 조작처럼 표시하지 않는다.

- 차콜 배경과 약간 밝은 표면, 얇은 경계선으로 영역을 구분한다.
- 큰 둥근 카드, 그라데이션, 장식용 그림자와 반복 설명을 제거한다.
- 일반 표면과 입력의 모서리는 2px로 제한한다.
- 기본 글자 12~13px, 제목 20px, 사이드바 180px, 상단 바 48px로 맞춘다.
- 명시적 severity는 Critical 빨강 / High 주황 / Medium 노랑 / Low 옅은 청록으로 구분한다. 서버 판정은 SUSPICIOUS 빨강, INCONCLUSIVE 노랑, NO_ACTIVE_EVIDENCE 옅은 녹색, UNKNOWN 회색으로 별도 강조한다. 원점수에는 위험 색상을 붙이지 않는다.
- 요약 수치는 72px 높이 strip으로, 개요와 이벤트의 중심은 8열 탐지 표로 표시한다.
- 표는 경과 시간·플레이어/클라이언트·탐지기·유형/근거·Raw 점수·Severity·대상 판정·상세로 구성한다.

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

`raw_score`는 Event 생산자가 만든 원본 값이다. 모듈마다 상한, emission 방식과 threshold가 다르므로 `raw_score > 0`만으로 severity를 선택하지 않는다. Scoring의 Final Verdict는 severity나 사건 처리 상태가 아니며 표에서는 `대상 판정`으로 별도 표시한다. 숫자 `score`와 `confidence`가 `null`이면 `미제공`으로 유지한다.

현재 공통 계약에는 severity가 없다. 화면은 선택적 `evidence.severity`가 명시적으로 Critical/High/Medium/Low일 때만 해당 표시를 지원하며, 이는 새 필수 계약을 만든 것이 아니다. 값이 없거나 알 수 없는 경우 `미제공`으로 표시한다. 개요의 의심 판정 지표는 실제 SUSPICIOUS 대상 수를 사용한다. 현재 데모에도 심각도를 임의로 추가하지 않았다.

## 관측 시각화

- 판정 도넛은 불러온 세션·플레이어 쌍의 최신 서버 판정을 집계한다. 플레이어 수나 전체 과거 세션의 판정 비율로 확대 해석하지 않는다. 범례를 선택하면 해당 판정의 플레이어 목록으로 이동한다.
- 탐지기별 막대는 관측 건수다. 운영 이벤트는 제외하고 0점 관측은 포함한다. 동일 Event ID를 중복 집계하지 않으며 DLL 하위 모듈과 미등록 채널도 보존한다. 막대를 선택하면 해당 탐지기의 관측 목록으로 이동한다.
- 세션 페이지의 그래프는 선택한 세션에서 고정된 서버 수신 sequence 구간별 관측 건수를 표시한다. 0점 이하와 양수 관측을 구분하지만 핵 판정을 대신하지 않는다. 빈 순번 구간은 해당 세션 기록이 없는 범위이며 게임이 시간상 멈췄다는 뜻이 아니다.
- 모듈별 원점수 합, 검증되지 않은 시간축, 점수→severity 환산 그래프는 만들지 않는다. 숫자와 범례를 함께 표시하고 키보드로 구간별 건수를 읽을 수 있다.
- 모든 그래프는 불러온 데이터 범위만 사용한다. 페이지 조회 제한·동기화·갱신 실패 안내와 DEMO/LIVE 표시를 유지한다.

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

`counts.scope=indexed_events`의 숫자를 접속자·활성 세션 수로 해석하지 않는다. 개요 `탐지 기록`은 실제 적재된 detection 관측 수이며 0점도 포함한다. `연결 클라이언트`는 조회된 최신 Launcher 상태 중 connected=true이고 healthy/online인 고유 client_id 수다. ID가 전혀 없으면 미제공이며 전역 활성 PC 수를 의미하지 않는다. 표의 클라이언트는 해당 대상의 최신 Heartbeat 소스이지 그 Event의 발신자가 확인됐다는 뜻은 아니다. 검토 대상은 Scoring이 `SUSPICIOUS` 또는 `INCONCLUSIVE`로 반환한 대상 수다.

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
- 기본 검색·세션·플레이어 필터를 먼저 보이고 나머지는 상세 필터로 펼친다. 활성 상세 필터 개수는 접힌 상태에서도 표시한다.
- 종합 현황은 전체 조회 범위로 유지하며, 요약에서 목록으로 진입하면 필터를 초기화한다. 목록 페이지 간 이동 시 필터는 유지한다.
- 세션 행은 해당 세션의 플레이어 페이지로, 모듈 행의 이벤트 버튼은 해당 모듈의 이벤트 페이지로 이동한다.
- 대상 선택 시 판정 snapshot과 heartbeat status를 독립적으로 조회해 한쪽 장애가 다른 쪽 정보를 숨기지 않게 한다.
- Event 행이나 타임라인 항목을 누르면 원본 공통 7필드와 backend metadata를 상세 drawer에 표시한다.
- 탐지 표에서 수신 순서·Raw 점수·명시적 severity 정렬과 severity 필터를 지원한다. 여러 세션의 경과 시간을 실제 발생 시각처럼 비교하지 않는다.
- 조사 drawer에는 유형, 원점수, 대상 판정, 평가 완료 여부, 근거 단위, 활성 모듈과 reason codes를 함께 표시한다. 플레이어 판정 화면으로 범위를 유지해 이동할 수 있다.
- `module_statuses`는 일부만 자르지 않고 전체 보기와 문제 상태 우선 정렬을 지원한다.
- API 오류 시 마지막 성공 자료를 유지하고 오류 범위를 표시하며 demo로 자동 전환하지 않는다.

## 반응형과 접근성

- 넓은 화면: 고정 sidebar, 플레이어 master-detail, 목록 중심 구성
- 중간 화면: 플레이어 목록과 상세의 비율 조정, 표 내부 가로 스크롤
- 작은 화면: sidebar drawer, 세로 필터, 상세 탭 스크롤, 목록과 상세 세로 배치

색상만으로 상태를 구분하지 않고 아이콘과 문구를 같이 사용한다. 표·drawer·dialog는 키보드로 조작할 수 있어야 하며 긴 ID와 evidence는 레이아웃을 넘지 않게 줄바꿈한다.
