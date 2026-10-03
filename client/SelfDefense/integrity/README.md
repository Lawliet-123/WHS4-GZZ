# SelfDefense Integrity 0.1.0

안티치트 배포 파일이 승인된 버전과 같은지 검사한다. 파일 내용 변경·삭제·검사 범위 안의 새 코드 파일을 기록한다. 실행 중인 프로세스의 생존 확인은 기존 watchdog 역할이다.

파일 복구, 프로그램 종료·재시작, 차단, 플레이어 제재는 하지 않는다. 하트비트·anti_debug·커널 기능도 포함하지 않는다. 기존 AutoPaint의 DLL/프로세스 기반 integrity_score와 별개다.

## 설치

압축 내용을 `client/SelfDefense/integrity/`에 넣는다. 그 안에 바로 main.py가 있어야 한다. watchdog 폴더는 바꾸지 않는다.

```text
통합 레포/
├─ client/SelfDefense/integrity/
│  ├─ main.py
│  ├─ baseline.py
│  ├─ scanner.py
│  ├─ reporting.py
│  ├─ build_baseline.py
│  ├─ examples/demo.py
│  ├─ tests/test_integrity.py
│  └─ docs/
└─ shared/                       # 별도 배포된 shared 0.2.0
```

표준 라이브러리와 팀 shared 0.2.0을 사용한다. 게임·UE4SS·런처 없이 단독 테스트할 수 있다. shared나 런처를 ZIP에 중복 포함하지 않는다.

## 우선 데모 실행

통합 레포 최상위에서 PowerShell 또는 CMD를 연다.

```powershell
py client/SelfDefense/integrity/examples/demo.py
```

새 임시 폴더의 가짜 파일로 정상 → 변경 → 삭제 → 추가 → 기준 파일 오류 → 복원 후 정상을 검사한다. 실제 팀 코드나 게임은 건드리지 않는다. 출력의 output 경로에 결과를 남긴다. 모두 synthetic=true이고 전송은 꺼져 있다. 정상/핵 플레이 로그 대신 제출하면 안 된다.

압축본을 독립 폴더에서 시험한다면 shared 폴더의 상위 경로를 넘긴다.

```powershell
py examples/demo.py --shared-root "C:\Team\MECCHA-CHAMELEON"
```

## 실제 배포본의 정상 기준 만들기

이 작업은 배포 담당자가 검토한 정상 릴리스에서 한 번 수행한다. 사용자 PC에서 매번 기준을 생성하면 변조된 파일도 정상으로 인정하게 된다.

통합 레포 최상위에서 다음을 실행한다. `--root .`는 현재 통합 레포다.

```powershell
py client/SelfDefense/integrity/build_baseline.py --root . --include-dir client --include-dir shared --release-id team_v1 --output release-manifests/team_v1.json
```

출력 JSON의 baseline_sha256을 별도의 승인된 릴리스 설정에 보관한다. 이후 실행에서는 이 고정값을 전달한다. 시작할 때 현재 manifest를 Get-FileHash로 다시 계산해 넣는 방식은 사용하지 않는다. 코드·정상 기준·설정이 모두 확정된 뒤 배포한다. 이미 있는 기준 파일은 덮어쓰지 않는다.

- client와 shared의 `.py`, `.pyw`, `.exe`, `.dll`, `.pyd`, `.lua` 파일을 재귀적으로 선택한다. Launcher, LocalGuard, SelfDefense, KernelWatcher, 각 detector와 client 아래 UE4SS도 이 규칙에 포함된다.
- `.git`, `.venv`, `venv`, `__pycache__`, `logs`, `docs`, `tests`, `examples`, `telemetry-outbox`, `outbox`, `sessions`, `replay_exports` 이름의 폴더는 제외한다.
- JSON, TOML 등 고정 설정도 검사하려면 `--file client/경로/config.json`처럼 개별 추가한다. `--file-list`는 같은 상대 경로들을 담은 JSON 배열이다. 런타임에 바뀌는 설정·등록부·로그는 넣지 않는다.
- 지정 범위 바깥의 게임 설치 폴더, Python 설치·site-packages, 서버 코드는 자동 검사하지 않는다. 실행에 쓰이는 고정 파일이 다른 경로나 확장자라면 릴리스 담당자가 범위를 추가해야 한다.
- 최대 512개, 파일당 64 MiB, 파일 전체 512 MiB, 기준 JSON 512 KiB. 초과하면 기준 생성을 실패시킨다. 임의로 일부를 빼고 성공 처리하지 않는다. 팀 UE4SS 배포본 등으로 한도를 넘으면 실제 용량을 확인해 별도 조정·성능 검증한다.

## 실제 파일 검사

아래 PIN 자리에 위에서 승인·보관한 64자리 소문자 SHA-256 값을 넣는다. 이 예시는 로컬 검사다.

```powershell
py client/SelfDefense/integrity/main.py --session-id integrity_check_001 --player-id player_042 --root . --baseline release-manifests/team_v1.json --baseline-sha256 PIN --telemetry off --once
```

상주 검사하려면 `--once`를 뺀다. 시작 직후 한 번 검사하고, 검사 완료 후 기본 30초를 기다린 뒤 다음 검사를 한다. `--interval 30`으로 변경 가능하다. Ctrl+C로 종료한다. 기준 파일과 실행 인자의 pin을 매 회차 비교하며, 실행 중 기준 파일을 자동 갱신하지 않는다.

통합 실행에서는 런처가 같은 session-id, player-id, `--t0`(세션 시작 Unix 초)를 전달한다. 개별 실행에서는 시작 시각을 저장해 같은 세션의 재실행에도 재사용한다. 기준 버전·플레이어·보호 경로가 바뀌면 새 session-id를 쓴다. `timestamp_ms`는 세션 시작 후 경과 ms이고 시간대가 붙지 않는다.

| 결과 | evidence.status | --once 종료 코드 |
| --- | --- | --- |
| 모든 지정 파일 일치·추가 코드 없음 | NORMAL | 0 |
| 내용 불일치·삭제·새 코드 파일 | DETECTED | 1 |
| 기준 오류·읽기 실패·검사 불완전 | ERROR 또는 DETECTED와 scan_complete=false | 2 |
| 로깅 등 수집기 내부 실패 | 종료 진단 | 3 |

읽기 오류와 실제 불일치가 같이 있으면 둘 다 남긴다. 상주 모드는 이상 발견만으로 종료하지 않는다. 정상 업데이트도 승인된 기준과 다르면 불일치다. 업데이트 중 검사하지 말고 새 릴리스 기준·pin·세션을 함께 적용한다.

## 로그와 shared 전송

```text
logs/<session-id>/
├─ session-clock.json
└─ runs/<run-id>/
   ├─ manifest.json
   ├─ events.jsonl
   └─ raw/integrity.jsonl
```

매 검사 회차에 정상 결과도 기록한다. raw에는 모든 파일 결과를, Event에는 개수·오류·앞 20개 이상 항목을 넣는다. 결과가 많으면 findings_truncated=true다. 세션 재실행은 run을 나눠 기존 파일을 덮어쓰지 않는다. sample_id는 run마다 0부터 시작하므로 run_id와 같이 해석한다. 이 자료는 플레이 로그가 아닌 보호 프로그램 운영 로그다.

공통 7필드는 그대로 사용한다. `module=selfdefense`, `evidence.kind=file_integrity`, `evidence.component=integrity`로 구분한다. `raw_score=0`이며 플레이어 치트 점수를 합산하지 않는다. DETECTED는 파일 이상을 확인했다는 의미이지 핵 사용자 확정이 아니다. Dashboard는 raw_score만 보지 말고 status와 scan_complete도 표시해야 한다.

`--telemetry off`가 기본이다. 실제 전송 시 다음 환경변수를 Launcher가 자식 프로세스에 전달하고 `--telemetry managed`를 지정한다.

- GZZ_TELEMETRY_URL: 승인된 중앙 HTTPS 주소
- GZZ_TELEMETRY_TOKEN: 접근 키, 코드·문서·로그에 기록하지 않음
- GZZ_TELEMETRY_OUTBOX: 이 integrity 프로세스만 쓰는 절대 경로. watchdog과 같은 DB를 쓰지 않음
- 선택: GZZ_TELEMETRY_RETRY_MODE=persistent

시작 시 configure_client(ClientConfig.from_env()) 한 번 → 로컬 기록 후 send_detection(event) → 종료 시 flush_client/shutdown_client 순서다. 직접 HTTP 코드는 없다. 같은 Python 프로세스에서 통합 측이 sender를 소유할 때만 external을 쓴다. 별도 프로세스끼리 external로 연결할 수 없다.

전송 설정 실패 시 진단을 출력하고 로컬 검사는 계속한다. 전송 큐 등록은 서버 도착 성공이 아니다. 종료 시 pending/failed를 표시한다. 큐 등록 실패한 로컬 JSONL을 자동 재전송하지 않는다. shared가 이미 보관한 pending/failed의 처리 규칙은 shared 문서를 따른다.

## 보호 한계와 성능

- SHA-256 pin은 기준 JSON 교체를 확인하는 수단이다. 전자서명 검증은 아직 아니다. 공격자가 검사기·pin 전달 설정·Python 실행 환경까지 바꾸면 우회할 수 있다.
- 디스크 파일 검사다. 메모리 패치·후킹·DLL 로드 여부·캐시된 코드 실행을 증명하지 않는다. 관리자/커널 공격자를 막는 보호 장치가 아니다.
- 경로 탈출, 장치명, reparse point·심볼릭 링크·하드링크를 거부한다. 동시 공격에 대한 원자적 파일 시스템 경계는 보장하지 않는다.
- 읽는 중 파일 정보 변화는 오류로 남긴다. 파일 변경 후 다시 복원하거나 검사 사이에만 변조하면 놓칠 수 있다. 읽을 수 없다는 이유만으로 악성이라고 확정하지 않는다.
- 전체 파일을 회차마다 읽는다. 1 MiB 단위로 해시를 계산하며 읽기량·개수를 제한한다. 디스크가 느리거나 게임과 동시에 읽으면 부하가 늘 수 있다. 실배포 용량에 대한 지연·CPU·디스크 측정은 남아 있다.
- 대기 중 종료 신호는 짧은 대기 단위로 확인한다. 파일 I/O 자체가 멈춘 경우 즉시 종료·시간 제한을 보장하지 않는다. --duration은 테스트용 회차 사이 종료 조건이다.
- 로그 보관·회전은 아직 없다. 운영 로그·outbox는 개인정보와 접근 권한을 고려해 관리한다.

런처 전달 사항은 [LAUNCHER_HANDOFF](docs/LAUNCHER_HANDOFF.md), 검증 범위는 [PACKAGE-VALIDATION](docs/PACKAGE-VALIDATION.md)를 참고한다.
