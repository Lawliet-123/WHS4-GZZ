# Integrity 연결 요청

워치독의 기존 self_defense 등록을 바꾸는 요청이 아니다. 별도 파일 검사 모듈이다. 공개 Launcher 소스는 수정하지 않았다.

## 추가할 항목

- 진입점: client/SelfDefense/integrity/main.py
- 등록명 제안: selfdefense_integrity
- 방식: CONTINUOUS, needs_game=False, restart=True
- 로그: client/SelfDefense/integrity/logs
- 종료 유예 제안: 30초. Console Break 수신·shared 종료 여유이며 모든 I/O 종료 보장은 아님
- 공통 인자: --session-id {session} --player-id {player} --t0 {t0} --telemetry {telemetry}
- 필수 추가 인자: --root 통합배포본절대경로 --baseline 승인된기준JSON절대경로 --baseline-sha256 승인된고정해시
- 선택 인자: --interval 30, --shared-root shared의상위경로, --output-dir 쓰기가능한로그경로

root/baseline/pin은 아직 런처에 존재하는 placeholder라고 가정하지 않는다. 릴리스 설정에서 읽어 실제 argv 값으로 전달하는 부분은 런처 담당자가 추가한다. 매 실행 시 기준을 재생성하거나 기준 파일의 현재 해시를 pin으로 계산하면 안 된다. --once, --duration, --synthetic는 상주 등록에 넣지 않는다.

같은 실행 시간 원점을 첫 실행부터 넘긴다. 별도 프로세스인 integrity 전용 GZZ_TELEMETRY_OUTBOX가 필요하다. registry의 모듈별 outbox 분리를 그대로 사용하면 된다. HTTPS 주소·키가 없으면 off로 로컬 검증한다.

기준 배포본 확정 전에는 이 항목을 자동 활성화하지 않는다. 승인 기준과 pin은 배포 담당자가 정상 릴리스에서 만들고 보관해야 한다. 새 버전·기준 적용은 새 세션에서 한다.

## 결과 소비 측에 알릴 내용

이벤트 module은 selfdefense, evidence.kind는 file_integrity다. watchdog의 module_health와 구분한다. raw_score는 0으로 유지한다. 파일 변경만으로 플레이어 치트 점수를 올리지 않는다.

evidence.status=NORMAL/DETECTED/ERROR와 scan_complete를 화면에 표시한다. DETECTED+scan_complete=false는 확인된 이상 외에 검사하지 못한 부분도 있다는 뜻이다. 복구·차단·게임 시작 제한 정책은 아직 구현하지 않았다.

## 실제 런처 반영 후 확인

1. 공유 session/player/t0로 실행되고 중복 프로세스가 없는지.
2. 정상 배포본의 첫 Event가 NORMAL이며 시간축이 다른 모듈과 맞는지.
3. 임시 시험 배포본 파일 변경 시 DETECTED, 읽기/기준 오류 시 ERROR인지. 운영 원본은 바꾸지 않음.
4. 정상 종료 시 STOPPED manifest와 shared flush/shutdown이 남는지.
5. 강제 종료 후 기존 registry 정책으로 재실행되고 기존 로그·시각을 이어받는지.
6. 중앙 receiver의 ACK, 저장 데이터, Dashboard 상태 표시까지 확인.

현재는 단독 CLI·합성 파일·로컬 HTTP 수신기로 검증했다. 실제 Launcher 전체 실행·팀 중앙 HTTPS 종단 검증은 아직 아니다.
