# Dashboard 통합 상태

이 문서는 8-A 화면이 종합 안티치트 관제 역할을 하기 위해 현재 연결할 수 있는 범위와, 8-B·Receiver·Scoring·Launcher 쪽에서 추가로 확정해야 하는 범위를 구분한다. 화면에 보인다는 이유만으로 아직 없는 서버 기능을 구현된 것처럼 표시하지 않는다.

## 현재 프론트에서 연결된 범위

- Receiver, Scoring, Launcher 연결 상태와 Event 인덱스 상태
- 전체 13개 보호 모듈 카탈로그와 Launcher component 상태
- 공통 Event의 세션, 플레이어, 모듈, submodule, 종류, 판정, 검색 필터
- 세션별 플레이어 판정 요약과 최대 관측 시각
- 플레이어별 Scoring Final Verdict, 정책 상태, 최신 근거, Launcher 실행 상태
- 서버 `sequence` 기준 플레이어 Event 타임라인과 Event 상세
- Snapshot과 Launcher status를 각각 조회하고, 한쪽 갱신 실패 시 마지막 성공 자료 유지
- 자동 갱신 실패 표시, Event watermark 초기화 시 전체 재동기화
- 연결·상세 조회 오류를 해당 dialog·drawer 안에서 표시
- Dashboard v2 응답의 핵심 중첩 구조를 런타임에 검증하고 계약 불일치 시 안전하게 중단
- Evidence 화면·JSON 복사 전 직접 식별 필드와 자유 문자열의 절대 경로 제거
- 스크롤 위치와 좌측 메뉴 활성 항목 동기화

필터는 상단 요약, 세션, 플레이어, 타임라인, Event 표, Launcher 대상 목록에 같은 범위로 적용한다. 모듈 카드는 다른 모듈로 다시 이동할 수 있도록 `보호 모듈` 차원만 제외한 나머지 범위에서 서로 비교한다. 모듈의 실행 상태는 Event 필터와 별개이므로 현재 조회 대상의 heartbeat component를 사용한다.

## 프론트에서 임의로 만들지 않는 값

- Scoring이 주지 않는 모듈별 점수와 확률
- 세션 전체를 대표하는 최종 판정
- `UNKNOWN` 또는 `INCONCLUSIVE`를 정상으로 바꾼 값
- `raw_score`를 모듈끼리 더한 종합 점수
- `timestamp_ms`의 시간 기준이 불명확한 경우의 실제 시각
- stale heartbeat만으로 추정한 종료 원인

현재 세션 표의 판정은 서버의 세션 판정이 아니라 **그 세션에서 가장 우선 검토가 필요한 플레이어 판정 요약**이다. `max_observed_timestamp_ms`는 세션 길이가 아니라 **최대 관측 시각**으로 표시한다.

## 8-B·백엔드와 확정해야 하는 항목

1. 운영 인증
   - 현재 Bearer token 직접 입력과 Vite `/dashboard-api` proxy는 로컬 통합 시험용이다.
   - 운영에서는 same-origin BFF·reverse proxy 또는 Dashboard 로그인 세션이 필요하다.

2. 대용량 조회
   - 현재 API는 cursor를 제공하지만 화면은 안전 한도까지 여러 페이지를 합쳐 조회한다.
   - 실운영에서는 Event 종류·판정·날짜 필터와 서버 측 페이지 이동을 API에 추가해야 한다.

3. 판정 집계
   - 현재 Scoring은 플레이어 Final Verdict를 제공하지만 세션 Final Verdict와 비교 가능한 모듈 점수 목록은 제공하지 않는다.
   - 이 값이 확정되기 전까지 화면은 플레이어 판정 요약만 사용한다.

4. 증거 자료
   - 현재 `capabilities.evidence_images=false`이고 이미지·추가 로그 조회 API가 없다.
   - 지원 시 인증된 same-origin URL, 접근 권한, 보존 기간, 404 처리 규격이 필요하다.

5. 활성 세션
   - 현재 응답에는 현재 실행 중인 세션과 과거 종료 세션을 구분하는 공통 필드가 없다.
   - 오래된 `stopped`·`stale` heartbeat가 전체 상태를 오염시키지 않도록 session lifecycle 또는 active flag가 필요하다.

6. 개인정보 제거
   - 프론트도 알려진 직접 식별 Evidence 키와 reason·log의 절대 경로를 숨기지만, 임의 형식 문자열의 모든 개인정보를 판별할 수는 없다.
   - Receiver 또는 Dashboard backend 응답 단계에서 allowlist 기반 제거를 한 번 더 적용해야 한다.

## Launcher·모듈 등록 확인 사항

현재 main 기준 화면은 등록 계약을 그대로 표현한다. 다음 기능은 이름만 보고 구현됐다고 확대 해석하지 않는다.

- Self Defense: 현재 확인 가능한 범위는 watchdog과 프로세스 생존 상태다.
- Input Signature: 현재 확인 가능한 범위는 YARA와 알려진 실행 파일 해시다.
- Kernel Watcher: 실제 실행 경로와 heartbeat 등록 여부를 Launcher 담당과 최종 확인해야 한다.

## 종단 통합 확인 순서

1. Launcher가 같은 `session_id`와 `player_id`로 각 component heartbeat를 전송한다.
2. 탐지기가 Shared 7필드 Event를 Receiver에 전송한다.
3. Dashboard overview에서 세션·플레이어·component 상태가 나타나는지 확인한다.
4. Event 상세의 ID, sequence, module, submodule이 Receiver 저장 내용과 같은지 확인한다.
5. Scoring snapshot의 Final Verdict와 Dashboard 판정이 같은지 확인한다.
6. 프로세스 중지·전송 장애·복구 순서가 시스템 상태와 플레이어 실행 상태에 반영되는지 확인한다.
7. API 일시 실패 후 마지막 성공 자료가 남고 `LIVE · 지연`으로 바뀌는지 확인한다.
8. 새 Event가 들어온 뒤 자동 갱신으로 중복 없이 추가되는지 확인한다.
