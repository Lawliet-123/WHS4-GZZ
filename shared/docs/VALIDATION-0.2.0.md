# Shared 0.2.0 / SelfDefense 0.1.0 검증 기록

2026-09-30, Windows / Python 3.14.6. 실제 Launcher·게임·중앙 서버 없이 로컬 테스트와 합성 데이터로 검증했다.

## 소스 테스트 결과

- 기존 shared 39개 + 신규 shared 9개 = 48개 통과.
- SelfDefense 신규 20개 통과.
- AutoPaint·Lua 관찰기 모의 실행·전송을 포함한 전체 회귀 143개 통과.
- 첫 전체 실행은 기존 .test-deps/lupa 읽기 권한 때문에 Lua 테스트를 불러오지 못했다. 읽기 권한을 허용한 재실행에서 143개 모두 통과했다. 의존성 설치나 Lua 코드는 변경하지 않았다.

## 추가 검증 내용

- 기존 bounded 기본값과 기존 공개 API 유지.
- persistent에서 max_attempts를 넘는 일시 오류 후 복구, ID·본문·시각 보존.
- 로컬 HTTP 수신기를 통한 재시도·중복 저장 방지.
- 새 Event가 장애 중 sender 대기를 우회하지 않음.
- 재실행 시 대기시간·미전송 보존, failed 자동 부활 없음.
- 인증/형식/인증서 오류의 자동 재시도 중단, 큐 상한 유지.
- evidence 선택 필드 round-trip, 추가 최상위 키 거절.
- registry 없음/API 오류/잘못된 PID를 정상 상태로 처리하지 않음.
- 공유 registry 호출 인자, 자기 자신·AutoPaint 제외, 상태 변화 중복 보고 방지.
- 워치독 로컬 기록 후 전송, 전송 실패 때 기록 유지, flush 실패 때도 shutdown 시도.
- 같은 세션 재실행에서 로그 분리·세션 시간 유지·충돌 거절.
- 다른 작업 디렉터리에서 main.py 직접 실행, 합성 데이터의 중앙 전송 금지.

## 남은 검증

- 실제 registry가 수행하는 프로세스 재시작, 동시 잠금, stopping/orphaned, 재시작 한도.
- 런처의 정상 종료 신호와 SelfDefense 자체 재시작.
- 실제 HTTPS /api/detection, 토큰, 모듈 허용 목록, 운영 보고의 scoring/화면 분리.
- Python 3.12 실행. 3.14 통과를 3.12 검증 완료로 표현하지 않는다.
- 게임에서 새 정상/치트 로그 수집. 이번 합성 로그는 ReplayAnalyzer의 게임 표본이 아니다.

## 이 배포본에서 shared 검증 재현

shared 폴더의 부모인 레포 루트에서 실행한다.

```powershell
py -m unittest discover -s shared/tests -t . -p "test_shared*.py" -v
py -m shared.examples.shared_demo
```

위의 SelfDefense 20개·전체 143개는 2026-09-30 개발 프로젝트에서 실행한 이력이다. 이 shared 전용 ZIP에는 해당 모듈·전체 테스트가 들어 있지 않다. 이번 경로 변경 배포본의 재검증은 [PACKAGE-VALIDATION](PACKAGE-VALIDATION.md)에 기록했다. 게임 로그·토큰·대기 DB·registry는 배포하지 않는다.
