# 담당 3 배포 인수인계 — 2026-10-06

## main 반영 및 검증 기준

중앙 서버 런타임 PR #131의 병합과 Dashboard 배포 설정·스크립트의 main 반영이 완료됐다.
현재 문서의 기준 main 및 실행 검증 커밋은 다음과 같다.

```text
faee8200cd18b7bb3966b7dcde859ee0f651e5ee
```

이 SHA는 이번 로컬 검증 기준이다. 실제 운영 배포는 팀에서 검토한 특정 main SHA를
별도로 공유한 뒤 진행한다. 관련 파일은 저장소에서 사용하고 정적 빌드는 확정한 커밋에서
다시 생성한다. 운영 토큰·서버 환경 파일·인증서·개인키·DB·dist를 Git에 포함하지 않는다.

## 배포 파일과 경로

| 파일 | 역할 |
|---|---|
| `deploy/dashboard/README.md` | VM 적용 순서, 담당 범위, 인증 및 시험 기준 |
| `build_release.sh` | 검토 커밋 확인, 프런트 검사·빌드, 커밋 및 파일 해시 기록 |
| `install_release.sh` | 정적 릴리스 배치와 current 링크 교체, 이전 릴리스 보존 |
| `render_nginx.py`, `nginx/` | 인증서 준비용 HTTP 및 운영 HTTPS·프록시 설정 |
| `renew_nginx.sh` | 인증서 갱신 후 Nginx 구문 확인 및 reload |

FastAPI는 `127.0.0.1:8000`, Uvicorn worker 1로 유지한다.
외부 `/dashboard-api/api/dashboard/...` 요청은 Nginx에서
`/api/dashboard/...`로 변환하며 Bearer와 조회 쿼리를 유지한다.
Dashboard 연결 창에는 `/dashboard-api`를 입력한다.
탐지·하트비트는 `/api/detection`, `/api/heartbeat`로 전달한다.

## 최신 검증 결과

2026-10-06, 위 main 커밋에서 다음을 다시 확인했다.

- 프런트 테스트 166개와 일반 프로덕션 빌드 통과.
- Dashboard 백엔드 테스트 62개 통과.
- C 앱 합성 HTTP 통합 시험 통과: 이벤트 7개 저장, Scoring 판정,
  Heartbeat 수신, Launcher Overview 및 기존 조회 API 확인.
- 실제 Windows Nginx의 로컬 HTTPS 시험 통과: 정적 파일, health JSON,
  Dashboard 접두어 변환, query/cursor 전달, 오류 401/403/404/422/503 보존,
  수신 POST·Idempotency-Key 전달, 상위 서버 중단 시 502 및 합성 Bearer 로그 미기록.

Nginx 시험은 모의 상위 서버와 합성 토큰을 사용했다. 위 결과는 Ubuntu 운영 배포나
실제 게임에서 배포 화면까지 이어지는 전체 시험의 완료를 의미하지 않는다.
Ubuntu 정적 파일 설치, systemd, DNS, 공인 인증서, 운영 브라우저 LIVE와
운영 환경의 게임 E2E는 해당 환경에서 확인한다.

## VM 및 연결 현황

| 항목 | 확인 내용 |
|---|---|
| 프로젝트 | `whs4-gzz` |
| VM | `gzz-telemetry` |
| Zone | `asia-northeast3-a` |
| OS | Ubuntu 24.04.5 LTS |
| 담당 3 접속 | 본인 OS Login SSH 접속 및 sudo 확인 완료, 사용자 실행 결과 기준 |
| Nginx·인증서 | 담당 1 인수인계 기준 미설치, 실제 적용 확인 필요 |
| 운영 도메인 | 미정, 사용할 수 있는 기존 팀 도메인 없음 |

운영 토큰은 담당 2 은지가 VM에서 생성·관리한다. Dashboard 토큰은 비공개 전달 또는
담당 2의 직접 입력으로 사용하며 브라우저 메모리에만 보관한다.
`GZZ_DASHBOARD_CURSOR_SECRET`은 서버 내부 서명용으로 사용하고 외부에 전달하지 않는다.

## 보관·외부 백업 기본안

기본 보관 기간은 30일이며 발표용·중요 E2E 자료는 별도로 보존한다.
원본 JSONL·ledger·여러 SQLite DB의 일관된 삭제 범위를 조사하기 전에는
자동 삭제를 활성화하지 않는다.

VM 외부 백업은 **Google Cloud Storage(GCS)를 기본안**으로 한다.
담당 1 성민이 버킷·접근 권한을 준비하고, 담당 2 은지가 백업 업로드·복원 절차를 연결한다.
버킷 이름·위치·보존 설정과 실제 업로드·복원 성공 여부는 별도로 확정·검증한다.
기본안 확정과 실제 GCS 백업 구축 완료를 구분한다.

## 다음 적용 순서

1. 실제 운영 도메인과 VM 고정 외부 IP, 80/443 허용·외부 8000 차단 결과를 확인한다.
2. 팀에서 검토한 배포용 main 전체 SHA를 공유한다.
3. 담당 2가 FastAPI·환경변수·토큰·systemd·영구 저장소를 적용하고
   `/health` 및 Dashboard Bearer 조회를 확인한다.
4. 담당 3 찬준이 DNS·Nginx·정적 Dashboard·HTTPS 및 인증서 자동 갱신을 적용한다.
5. `/dashboard-api`로 LIVE 연결하고 외부 HTTPS에서 Launcher → Receiver/저장 →
   Scoring → Dashboard를 공동 확인한다.
6. VM 재부팅, GCS 백업·복원, 인증서 갱신 결과를 검증한다.

첫 합성 시험 식별자는 `deployment_smoke_20261006_01` / `synthetic_player`이다.
Heartbeat용 `client_id`는 실제 전송한 값을 담당 2에게 전달받는다.
