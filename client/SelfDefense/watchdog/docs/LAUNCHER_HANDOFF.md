# 런처 담당자 전달 — Watchdog 0.2.1

기존에 전달한 client/SelfDefense/main.py 경로를 정정합니다. 팀에서 정한 하위 폴더 구조에 맞춰 진입점은 `client/SelfDefense/watchdog/main.py`입니다. 공개 런처 코드는 수정하지 않았습니다.

## 반영 요청

1. SelfDefense-Watchdog-0.2.1.zip 내용을 `client/SelfDefense/watchdog/`에 넣어 주세요. 압축 안의 main.py가 해당 위치에 바로 와야 합니다. anti_debug/, integrity/는 그대로 둡니다.
2. modules.py의 기존 self_defense 항목을 아래 내용으로 교체해 주세요. 별도 항목을 추가해 두 개를 띄우지 않습니다.

```python
Module(
    name="self_defense",
    owner="4번 (성민)",
    argv=[PY, "client/SelfDefense/watchdog/main.py",
          "--session-id", "{session}",
          "--player-id", "{player}",
          "--t0", "{t0}",
          "--telemetry", "{telemetry}"],
    mode=CONTINUOUS,
    needs_game=False,
    restart=True,
    stop_grace_s=30.0,
    session_log_dir="client/SelfDefense/watchdog/logs",
    note="워치독: registry 기반 생존 확인·복구 요청·운영 상태 보고",
),
```

3. `{t0}`는 기존처럼 Unix 초를 전달합니다. SelfDefense가 ms로 변환합니다. 상주 실행에는 --demo, --duration을 넣지 않습니다.
4. 기본 로그 위치도 `client/SelfDefense/watchdog/logs`로 바뀝니다. 기존 로그는 이동·삭제하지 않았습니다. 새 세션으로 전환해 주세요.
5. 기존 registry.spawn의 모듈별 outbox 분리는 그대로 사용합니다. managed에는 GZZ_TELEMETRY_URL, GZZ_TELEMETRY_TOKEN이 필요하며 지속 재시도는 GZZ_TELEMETRY_RETRY_MODE=persistent로 설정합니다. URL이 없으면 기존 런처의 {telemetry}=off를 사용합니다.
6. AC_LAUNCHER_LOG_DIR을 사용한다면 런처와 워치독이 같은 절대 경로를 받도록 해 주세요.
7. 실제 Launcher.main에서 시작·정상 종료·워치독 자체 재시작·새 세션 시작을 확인 부탁드립니다.

모듈 등록명 self_defense와 이벤트 module=selfdefense는 유지했습니다. 폴더명 변경 때문에 서버 식별자나 재시작 한도가 갈라지지 않도록 한 것입니다. 등록명까지 바꾸려면 --self-name과 관련 소비자도 함께 협의해야 합니다.

stop_grace_s=30은 registry 잠금과 shared flush/shutdown 여유입니다. 정상 경로의 테스트는 10초 설정에서도 마감됐지만 registry 호출의 무한 정지를 해결하거나 종료 시간을 보장하는 설정은 아닙니다.

## 이번에 연결한 범위

기준: 2026-10-01 공개 Launcher 커밋 acc9d2afe7d21e576b098f654c04643be8455b61. 해당 원본 registry/process_manager를 새 임시 폴더에 복사해 테스트용 프로세스로 검증합니다. 전체 Launcher.main·실제 게임·중앙 HTTPS 서버는 이번 테스트 범위가 아닙니다. 결과는 [PACKAGE-VALIDATION](PACKAGE-VALIDATION.md)을 참고해 주세요.

- 생존 확인·복구 요청은 registry.restartable_names/restart_if_dead로 합니다. 별도 프로세스 생성 코드는 추가하지 않았습니다.
- stopping·런처 사망·잘못된 세션에서는 재시작하지 않습니다. 자기 자신은 런처가 복구합니다.
- restart=False인 AutoPaint/input_signature 등과 ONESHOT 대상은 이 목록에서 빠집니다. 이들까지 감시하려면 감시 목록과 재시작 허용 목록을 분리하는 계약이 필요합니다.
- 운영 Event는 module=selfdefense, raw_score=0, evidence.kind=module_health입니다. 치트 판정·정상 플레이 표본과 분리해 주세요.
- 동일 오류는 상태 변화가 없으면 반복 전송하지 않으며 raw에는 매 점검을 기록합니다.
- 하트비트·안티디버깅·자체 무결성은 이 워치독에 추가하지 않았습니다.
- 세션 사전 확인은 원자적 잠금이나 보안 인증이 아닙니다. registry 읽기 실패 세분화, 권한 문제, 콘솔 없는 패키징은 별도 협의·검증 대상입니다.

공유 registry를 복사해서 별도 운영하거나 각 모듈에서 다른 재시작 규칙을 구현하지 않습니다.
