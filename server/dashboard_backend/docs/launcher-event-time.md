# Dashboard-A 시간 표시 계약

기존 `/api/dashboard/events` 및 `/events/{event_id}`의 추가 조회 메타데이터이다. Launcher·Shared의 공통 7필드, ACK, 원본 timestamp_ms/evidence는 변경하지 않는다.

## 기준과 충돌 처리

- `evidence.timestamp_basis=launcher_session_start`는 `time_basis=session_relative`로 연결한다.
- 기존 `evidence.time_basis=session_relative|unix_epoch_ms`도 유지한다.
- 두 선언이 함께 있으면 같은 기준이어야 한다. 충돌하거나 명시적 선언이 잘못된 값이면 `time_basis=unknown`, 관측 시각 미제공이다. session/client ID, 모듈 이름, 값의 크기로 기준을 추정하지 않는다.
- basis가 유효하지만 기준시각이 없거나 잘못된 경우 경과시간 해석은 유지하고 실제 관측 시각은 제공하지 않는다.

## 세 시간의 구분

- `timestamp_ms`: 생산자가 보낸 원본 값. session_relative일 때 세션 기준 경과 ms.
- `observed_at_utc`: session_relative이며 evidence.session_start_unix_ms와 timestamp_ms가 유효한 정수일 때 두 값의 합을 UTC ISO 8601 밀리초 정밀도로 제공한다. 클라이언트가 선언한 시계로부터 계산한 관측 시각이며 별도 서버 시계 검증의 결과가 아니다.
- `received_at_utc`: Shared writer 최초 수락·저장 UTC 메타데이터. 관측 시각으로 대체하지 않는다. 미기록된 과거 자료는 null이고 재전송 시 처음 기록된 값을 유지한다.
- `sequence`: 서버 저장 순서·페이지 커서. 날짜나 경과시간이 아니다.

기준시각은 양의 정수, 경과시간은 0 이상의 정수이며 boolean/string/float는 인정하지 않는다. JavaScript safe integer 범위, 합산 범위 및 UTC 날짜 표현 범위를 벗어나면 observed_at_utc를 제공하지 않는다. 별도 epoch 변환 API는 추가하지 않는다.

## 프론트 표시

1. time_basis=session_relative일 때 원본 timestamp_ms를 분·초·밀리초로 표시한다. 예: 246771 → `경과 04:06.771`.
2. observed_at_utc가 있으면 Asia/Seoul로 변환하고 밀리초를 보존한다. 기준 1791367718186 + 경과 246771 → `관측 2026-10-07 19:12:44.957 KST`.
3. 실제 시각이 없으면 `관측 시각 미제공`을 표시한다. received_at_utc는 `수신`이라는 별도 라벨로 표시한다.
4. unknown은 경과/epoch로 변환하지 않고 원시 timestamp를 표시한다. 유효한 unix_epoch_ms는 기존 명시적 epoch 계약대로 처리하되 수신 시각과 섞지 않는다.
5. 목록과 상세는 같은 projector를 사용한다. history 응답의 계약은 변경하지 않았으므로 이력 화면은 기존 원본 상세 조회로 시간 메타데이터를 확인한다.

기존 프론트 eventTime/EventTimeLabel을 활용하면 된다. 새 시계·저장소·API가 필요하지 않다. 현재 프론트는 초 단위 포맷이므로 경과/관측 표시의 밀리초 정밀도는 Dashboard-A에서 보완한다.

## 배포 후 확인

검토·병합한 main 커밋을 배포한 후 normal_full_Light_20261007_190838/Light의 hide_anywhere 및 kernel_sentinel 목록·상세를 다시 조회한다. 시간 기준, 관측 시각, 원본 evidence/timestamp, 저장 sequence 일치를 확인한다.

received_at_utc=null만으로 누락 원인을 단정하지 않는다. 운영 담당자는 배포 SHA, writer ledger 및 dashboard index의 received_at_utc 열 존재 여부와 해당 event_id의 값을 읽기 전용으로 확인한다. 원본 writer가 null이면 과거 수신 시각은 복원하지 않는다. writer에 값이 있고 index가 null이면 migration/색인 갱신 상태를 검토한다. 토큰·전체 로그를 공유하지 않는다.
