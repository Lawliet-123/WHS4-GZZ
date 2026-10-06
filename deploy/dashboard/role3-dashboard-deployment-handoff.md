# 담당 3 배포 인수인계 — 2026-10-06

최신 main `45af8dfdbc8862e62df5b7043badee843b15ae9b`에 배포 파일 11개를 추가했다.
로컬 브랜치: `feat/dashboard-native-deployment`
로컬 작업 커밋: `8821a2a4d8c4450926155b415c52c053ddd8730d`
확인한 담당 2 브랜치: `feat/server-runtime-deployment`, `3c5caeee978fffd556177d4d64023939479b9f99`
GitHub push·PR 생성 및 Ubuntu 운영 배포는 아직 하지 않았다.

## 적용 파일

- `deploy/dashboard/README.md`: VM 적용 순서, 분담, 인증·시험 기준
- `build_release.sh`: 지정한 검토 커밋 확인 → npm ci/test/build → 커밋·해시 기록
- `install_release.sh`: 해시 확인 → 정적 릴리스 배치 → current 링크 교체 (이전 릴리스 보존)
- `render_nginx.py`와 `nginx/`: localhost:8000 프록시 및 bootstrap/HTTPS 설정
- `renew_nginx.sh`: 인증서 갱신 후 nginx -t 및 reload

dist는 Git이나 이 ZIP에 넣지 않았다. 관련 PR 병합 후 최종 main 커밋에서 다시 빌드한다.
운영 토큰·서버 .env·인증서·개인키·DB는 포함하지 않았다.
FastAPI·systemd·Scoring·React 기능을 수정하지 않았다.

## 확인 결과

- 프런트 테스트 166개, 일반 프로덕션 빌드 통과
- 빌드 릴리스 스크립트 실제 실행·생성 해시 검증 통과 (Windows Git Bash)
- Dashboard 백엔드 테스트 47개 통과
- 기존 C 앱 합성 HTTP 통합 smoke PASS: 저장 이벤트 7개, 판정, Heartbeat, Launcher Overview
- 실제 Windows Nginx의 로컬 HTTPS + 모의 upstream: 구문, 정적 파일, health JSON,
  prefix/query/cursor, 401/403/404/422/503, POST/Idempotency-Key, 상위 서버 중단 502 통과
- 셸 스크립트 구문 및 도메인 렌더링 입력 검증 통과

담당 2가 보고한 479개 전체 회귀 테스트를 이번 작업에서 다시 실행한 것은 아니다.
실제 Ubuntu 배치 스크립트·systemd·DNS·공인 인증서·운영 브라우저 LIVE·게임 E2E는 남아 있다.
실행 검증 커밋은 `4f038d7083f7c9f51fcbbd1ed91f368205e8a0af`이다. 그 이후 변경은 최종 합의 README뿐이며
설정·스크립트·앱 코드가 동일한 것을 확인했다. 문서 변경으로 전체 테스트를 반복하지 않았다.

## 저장소 적용

첨부 patch는 확인한 main을 기준으로 만든 추가 파일 패치다. 기존 checkout을 보존하고
최신 main에서 새 작업 브랜치로 적용한다. 이미 같은 파일을 반영했다면 중복 적용하지 않는다.

```bash
git switch -c feat/dashboard-native-deployment origin/main
git apply --check /path/to/role3-dashboard-deployment.patch
git apply /path/to/role3-dashboard-deployment.patch
git add deploy/dashboard
git commit -m "Add reviewed Dashboard build and Ubuntu Nginx deployment procedures"
```

또는 ZIP의 deploy/dashboard 디렉터리를 같은 저장소 경로에 복사한다.
PR에서는 설정·스크립트·README만 검토하고 빌드 결과를 커밋하지 않는다.

## VM 준비 후 연결

1. 담당 1 성민에게 고정 IP·SSH/sudo 범위를 인계받는다.
2. 담당 3 찬준이 운영 도메인·DNS·Nginx·HTTPS·인증서 자동 갱신을 진행한다.
   운영 도메인은 사용자 답변 기준 **미정**이며, 확정 전에는 자리표시자를 유지한다.
3. 담당 2가 FastAPI/systemd를 먼저 적용하고 /health 및 Bearer 조회를 확인한다.
4. 담당 3이 정적 릴리스·HTTPS 프록시를 적용한다.
5. `/dashboard-api`로 LIVE 연결 후 공동 시험을 진행한다.

시험 식별자: `deployment_smoke_20261006_01` / `synthetic_player`.
Heartbeat client_id는 실제 전송한 값을 별도 전달받는다.
일회성 비밀 전달 또는 담당 2 직접 입력으로 Dashboard 토큰을 전달한다.
Cursor 서명키는 서버 내부에 둔다.

## 최종 합의된 보관·백업

기본 30일 보관, 발표용·중요 E2E 자료 별도 보존이다. 현재 자동 삭제하지 않는다.
원본 JSONL·ledger·여러 SQLite의 정합성을 조사한 뒤 삭제 범위를 결정한다.
VM 외부 백업 위치는 미정이다. GCS를 선택하면 1번이 버킷·권한, 2번이 절차를 맡는다.
모든 운영 토큰과 환경변수는 2번 은지가 생성·관리한다.

현재 환경에는 동작하는 GitHub Git 인증이 없어 push 시도가 인증 단계에서 실패했다.
원격 브랜치와 PR은 생성하지 않았다. 인증된 작업 환경에서 패치를 적용하여 PR을 올린다.
