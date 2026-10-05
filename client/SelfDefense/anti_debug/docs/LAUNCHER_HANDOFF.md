# SelfDefense 세 모듈 연결 요청

2026-10-06. 런처 소스를 직접 수정하지 않았다. main 31dc8332a770f51f2f50856637dcf8a1a4cee102의 registry 사본으로 테스트를 보완했다. 0.1.0-testfix1은 운영 코드 변경 없이 테스트·문서만 갱신한 묶음이다. [재검사 방법](TESTFIX.md)을 참고한다.

## 진입점과 역할

| 모듈 | 진입점 | 런처 등록 이름 |
| --- | --- | --- |
| Watchdog 0.3.0 | client/SelfDefense/watchdog/main.py | 기존 self_defense 유지 |
| Integrity 0.1.0 | client/SelfDefense/integrity/main.py | selfdefense_integrity 제안 |
| AntiDebug 0.1.0 | client/SelfDefense/anti_debug/main.py | selfdefense_anti_debug 제안 |

별도 ZIP을 각 폴더에 풀어 설치한다. AntiDebug ZIP에는 watchdog/integrity/shared를 중복 넣지 않았다. SelfDefense 루트에 main.py를 새로 만들 필요는 없다.

## 공통 전달값

- --session-id {session} --player-id {player} --t0 {t0} --telemetry {telemetry}
- t0는 Unix 초이며 세 모듈에 같은 값을 첫 실행부터 전달.
- 각 모듈의 로그 위치는 해당 하위 폴더/logs. 쓰기 가능한 외부 위치라면 --output-dir로 지정.
- managed는 HTTPS 주소·토큰과 각 프로세스 전용 GZZ_TELEMETRY_OUTBOX 필요. registry.spawn의 모듈별 outbox 분리를 유지.
- AC_LAUNCHER_LOG_DIR을 사용하면 세션 소유 런처와 워치독·anti_debug가 같은 경로를 받도록 전달.
- CONTINUOUS, needs_game=False, restart=True, stop_grace_s=30 제안. I/O 정지까지 종료 시간을 보장한다는 뜻은 아님.
- --once, --duration, --self-only, --synthetic, --demo는 운영 상주 등록에 넣지 않음.

## AntiDebug 추가 항목 예

```python
Module(
    name="selfdefense_anti_debug",
    owner="4번 (성민)",
    argv=[PY, "client/SelfDefense/anti_debug/main.py",
          "--session-id", "{session}", "--player-id", "{player}",
          "--t0", "{t0}", "--telemetry", "{telemetry}"],
    mode=CONTINUOUS,
    needs_game=False,
    restart=True,
    stop_grace_s=30.0,
    session_log_dir="client/SelfDefense/anti_debug/logs",
    note="등록된 안티치트 프로세스의 네이티브 디버거 연결 관측",
),
```

anti_debug는 `--registry-path`로 등록부 JSON을 직접 지정할 수도 있다. 기본은 AC_LAUNCHER_LOG_DIR 또는 client/Launcher/logs/anticheat_pids.json이다. registry.py를 import하거나 등록부를 수정하지 않는다. entries의 restartable 값과 무관하게 실행 중인 등록 프로세스를 확인한다. 런처의 PID+생성 시각을 확인하지 못하면 자식 목록을 검사하지 않고 ERROR로 보고한다.

## 나머지 두 모듈

Watchdog: 기존 self_defense 항목의 경로를 client/SelfDefense/watchdog/main.py로 맞춘다. 중복 항목을 만들지 않는다. 자세한 기존 요청은 watchdog/docs/LAUNCHER_HANDOFF.md에 있다.

Integrity: --root, --baseline, --baseline-sha256을 추가로 받아야 한다. root는 최종 통합 배포본, baseline은 승인된 기준 JSON, pin은 승인된 고정 해시다. 이 설정을 읽는 부분은 런처에서 추가해야 하며 존재하지 않는 placeholder가 이미 구현돼 있다고 가정하지 않는다. 기준을 만들지 않은 상태에서 integrity를 운영 자동 실행하지 않는다.

anti_debug와 런처 파일까지 반영해 배포본을 확정한 다음 integrity 기준을 생성한다. 매 실행 시 기준이나 pin을 다시 계산하지 않는다. 새 릴리스는 새 기준·pin·세션으로 적용한다.

## 결과 구분

세 모듈 모두 module=selfdefense, raw_score=0이다.

- watchdog: evidence.kind=module_health
- integrity: evidence.kind=file_integrity
- anti_debug: evidence.kind=debugger_presence

치트 점수로 자동 합산하지 않는다. Dashboard는 status·검사 범위·scan_complete를 함께 표시한다. anti_debug의 stopping은 정상 종료 절차이며, EXITED는 디버거 연결 해제가 아니라 대상 프로세스 종료다.

## 반영 후 공동 테스트

1. 같은 세션·시간축으로 세 모듈 실행, 개별 outbox, 중복 실행 없음 확인.
2. 정상 종료 시 manifest 마감·shared 정리 확인.
3. 각 모듈 강제 종료 후 기존 registry 재시작 정책 확인. 시험용 프로세스로만 검증.
4. 정상 배포 파일 검사, 시험 복사본의 변경·삭제·추가 검사.
5. 시험용 안티치트 프로세스의 디버거 연결·해제 확인. 개발자 IDE 연결도 탐지 가능 범위에서는 정상적으로 기록됨.
6. 권한 부족·등록부 누락·세션 불일치가 정상으로 숨겨지지 않는지 확인.
7. 실제 중앙 HTTPS receiver ACK와 Dashboard 표시 확인.

1차에는 차단·재시작 지시를 anti_debug가 보내지 않는다. 2차 실행 제한 정책은 별도 승인 후 진행한다.
