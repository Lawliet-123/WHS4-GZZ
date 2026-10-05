# 런처 전달 사항 — Watchdog 0.3.0

## 교체

- ZIP 내용을 client/SelfDefense/watchdog/에 넣습니다. main.py 진입점은 그대로입니다.
- 2026-10-06 확인한 main 31dc8332a770f51f2f50856637dcf8a1a4cee102의 self_defense 경로·인자·로그 경로·stop_grace_s=30은 이미 맞습니다. 항목 추가나 경로 재수정은 필요 없습니다.
- 기존 --session-id, --player-id, --t0, --telemetry를 그대로 사용합니다.
- shared·anti_debug·integrity·Launcher는 교체하지 않습니다. 기존 실행을 종료하고 새 세션으로 확인합니다.
- 0.3.0-testfix1은 운영 코드 변경 없이 테스트·문서만 보완했습니다. 기존 .gitignore와 팀원 추가 파일을 삭제하지 말고 ZIP의 동명 파일만 반영해 주세요. [수정 원인·재검사 명령](TESTFIX.md)을 참고해 주세요.

## 변경 동작

0.2.1은 재시작 가능 목록만 감시했습니다. 0.3.0은 등록부 entries 전체와 modules.py의 실행 방식·재시작 허용 여부를 읽습니다.

- 1번 external_access·module_integrity: 생존 확인, 기존 registry로 재시작 요청.
- 2번 memory_integrity: 생존·종료 관찰. 코드 0/1 완료, 2 검사 실패, 나머지 비정상 종료. 주기 실행은 계속 런처 책임입니다.
- 3번 input_signature: 종료 감지는 하지만 restart=False를 유지합니다.
- autopaint 등 다른 restart=False 항목도 관찰합니다. 자기 자신은 되살리지 않습니다.
- 접근 거부·놓친 종료 코드는 alive/정상 완료로 취급하지 않습니다.

Windows 읽기 전용 핸들을 모듈별로 보관하고 대상 변경·등록 제거·워치독 종료 때 해제합니다. 임의 프로세스 탐색, 게임 메모리 접근, 새 하트비트는 없습니다.

등록명 self_defense, Event module=selfdefense, raw_score=0, evidence.kind=module_health를 유지합니다. 최초 관찰과 상태 변화 Event가 늘어나므로 Dashboard는 evidence.target_module별로 구분해야 합니다. completed는 검사 실행 완료이지 정상 플레이 보증이 아닙니다. 운영 Event를 치트 점수에 합산하지 않습니다.

## 검증 범위

원본 registry.py·process_manager.py·modules.py를 변경하지 않고 임시 폴더의 테스트 프로세스로 검사합니다. 재시작 경쟁·한도·워치독 복구·공통 시간·3번 종료·2번 종료 코드·PID 불일치·정상 종료·런처 사망·로컬 receiver 재전송을 확인합니다. [검증 기록](PACKAGE-VALIDATION.md), [실행 결과](INTEGRATION-RESULT.json) 참고.

전체 Launcher.main·실게임·배포된 중앙 HTTPS는 런처/서버 담당자의 통합 테스트로 남습니다.

## 추가 협의

이번 버전을 붙이기 위해 필수로 런처를 바꿔야 하는 것은 아닙니다. 다음 사각지대를 없애려면 상태 공유 계약이 필요합니다.

1. 현재 등록부에는 mode·마지막 종료 코드·다음 실행 예정·PENDING/MISSING/SKIPPED 등이 없습니다. 워치독 시작 전에 끝난 검사와 아예 시작하지 못한 모듈까지 판단하려면 세션과 PID/생성 시각에 연결된 수명주기 스냅샷 또는 이벤트가 필요합니다.
2. 게임 종료 때문에 상주 모듈이 먼저 끝나는 경우, registry.stopping 전에는 의도를 알 수 없습니다. 해당 정보를 공유할지 협의 부탁드립니다.
3. modules.py의 실행 방식과 ONESHOT 종료 코드 계약을 변경할 때 알려 주세요. 현재 0 정상 검사 / 1 의심 발견 / 2 검사 실패 / 그 외 비정상 종료입니다.
4. 설치 후 self_defense 중복 실행 없이 시작·Ctrl+C 종료·워치독 복구를 전체 런처 흐름에서 확인해 주세요. SelfDefense 루트 main.py는 추가하지 않습니다.

2차 차단 기능이나 새로운 하트비트는 이번 변경에 포함되지 않습니다.
