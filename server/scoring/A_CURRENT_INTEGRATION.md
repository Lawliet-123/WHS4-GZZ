# A 정책·Receiver 최신 통합 확인 (2026-10-03)

## 1. 기준과 범위

최초 작업은 팀 `main`의 `90e90cc`를 기준으로 수행함. 이후 PR #87까지 병합된
`c45cc39`와 이 브랜치를 임시로 합쳐 호환성을 다시 확인함. PR #83의 Replay
calibration/RiskInput, PR #84의 InputSignature 0점 전송, PR #86의 종료 직전 RPC
검사, PR #87의 Hide Anywhere 런처 등록·종료 처리가 포함된 상태임.
기존 작업 폴더의 미커밋 조사 파일은 그대로 보존하고 별도 브랜치에서 수정함.
충돌로 미병합인 PR #85의 모듈 이름·개인정보·런처 변경은 이번 작업에 섞지 않음.

기존 조사 문서와 원본 Replay는 삭제하거나 고쳐 쓰지 않음. 이전 설명은 당시
확인 기록이며, 현재 상태는 이 문서와 각 정책 문서의 마지막 갱신 절을 참조함.

A의 분석 함수·테스트·Receiver 호환성 확인을 수행함. 원본 점수, Shared 최상위
7필드, 공통 Registry 등록, Replay calibration 임계값은 그대로 유지함.
공통 저장 코드에서는 PR #86의 `sample_id=0`을 인식하는 최소 호환 수정만 수행함.
공통 Profile에서는 현재 Hide 경로와 Hash의 0점 snapshot 의미를 갱신함.
이 두 공통 파일 변경은 B 검토가 필요한 별도 호환 패치이며, 임의의 가중치·TTL·
종합 위험도·최종 판정이나 새로운 테이블을 구현한 것은 아님.

## 2. Hide Anywhere 생산자 변경에 맞춰 수정함

이전에 단일 패턴 일치만으로 3점이 발생하고 읽기 실패 구분이 부족했으나,
현재 생산자는 `client/detectors/hide_anywhere/`로 이동하여 지속적인
`Rule(required=3)`과 유효성 필드를 사용하도록 수정됨. A 정책과 검증 도구가
삭제된 경로와 이전 reason을 사용하고 있어 현재 생산자 계약으로 갱신함.

| 관측 | 실제 생산자 점수·보고 | A 해석 |
|---|---|---|
| 동일 Pawn에서 값 1·2회 일치 | 핵심 점수 0, pending reason | 확인 완료로 승격하지 않음 |
| 동일 Pawn에서 3회 연속 일치 | 3점, confirmed reason·확인 횟수 | 확인 보고를 보존함. 실제 숨기 행동 성공의 증명은 아님 |
| 확인 후 계속 같은 패턴 | 반복 3점 | snapshot이며 새 사건 점수로 누적하지 않음 |
| 읽기 실패·누락 | 유효성/오류 필드와 ERROR | 정상 0점과 구분함. 확인 횟수 초기화를 실제 생성기로 검증함 |
| Pawn 변경 | 새 연속 횟수로 시작 | 이전 Pawn의 확인 횟수를 이어 쓰지 않음 |
| DLL 또는 Viewport 보조 근거 | 합계 최대 2점 | 핵심 확인 점수에 다시 더하지 않음 |
| 핵심 값 확인 성공·보조 조회 실패 | 핵심 3점, observation_status=unavailable | 핵심 패턴과 부가 조회의 유효성을 구분함 |

`hide_value_confirmed`, `consecutive_matches`, `required_matches`, measurement_valid와
confirmed reason이 대응할 때만 확인된 패턴으로 주석을 생성함. 과거 Replay의
`Hide Anywhere Value Pattern Matched`는 원점수를 보존하되 3회 확인 데이터로
재라벨링하지 않음.

ServerBridge는 현재 `shared.logger.send_detection(event)`를 호출하며 Shared가
전송 ID를 생성함. 이전 `UUID hex:순번` 오류와 잘못된 logger import가 현재
코드에서 수정된 것을 확인하고, 조사 도구의 과거 오류 재현을 현재 계약 검증으로
바꿈. bridge의 큐 등록 검사는 모의 receipt를 사용하며 운영 서버 ACK 검증이 아님.

### PR #87 런처 연결 확인 및 제한

PR #87에서 Hide Anywhere가 `client/Launcher/modules.py`에 상주 모듈로 등록됨.
런처는 실제 게임 PID·session_id·player_id를 전달하고, 중앙 전송 설정이 없을
때만 `--local-only`를 붙임. 모듈 전용 `PYTHONPATH`와 outbox를 유지한 채 실행하며,
게임 종료 시 수집기가 정상 종료한 경우 STOPPED로 처리하고 Shared flush 시간을
확보함. PR #87과 이 브랜치를 임시 병합한 상태에서 런처 인자·환경변수·outbox
계약을 별도로 확인함.

PR #87은 공통 Event의 7필드나 Hide 점수·reason·evidence 형식을 바꾸지 않았으므로
A Policy 코드는 추가 변경하지 않음. 다만 실제 통합 및 Replay 수집에는 아래 제한이
남아 있음.

- Hide는 아직 런처의 공통 `--t0`를 받지 않고 수집기 시작 기준
  `timestamp_ms`와 `evidence.timestamp_basis=local_session_start`를 사용함.
  다른 탐지기의 세션 공통 시각과 직접 비교하여 overlap을 감산하면 잘못된 시간
  대응이 생길 수 있으므로, B의 시간 기반 중복 보정 전에 시계 기준을 맞추거나
  서로 다른 timestamp_basis를 가진 관측은 자동 보정 대상에서 제외해야 함.
- Hide 수집기는 게임에 `PROCESS_VM_READ` 핸들을 유지함. ESP와 동시에 실행하면
  ESP가 이 수집기를 `memory_read` 근거로 탐지하는 문제가 PR #87에도 명시됨.
  ESP가 런처 등록 PID를 제외하기 전까지 정상 통합 세션은 두 모듈을 분리하여
  검증해야 하며, 해당 ESP 점수를 정상 플레이 근거로 사용하면 안 됨.
- Hide의 Replay manifest는 `--play-label`이 없으면 `normal_`로 시작하는 세션만
  NORMAL로 추론함. 런처 기본 `ac_...` 세션은 CHEAT로 기록되므로 정상 Replay
  수집 시에는 `normal_...` 세션명을 명시해야 함. 이는 중앙 7필드 Event의 점수
  판정과는 별개지만 Replay calibration 라벨을 오염시킬 수 있음.

## 3. 0점과 측정 불가를 구분함

- PR #82 이후 MemoryIntegrity·Whistle 공통 러너는 NORMAL 0점과 ERROR/OFFLINE
  0점도 전송함. A 함수의 양수-only 설명을 현재 동작으로 수정함.
- PR #84 이후 InputSignature도 실제 생성한 0점 Event를 중앙 sink에 전달함.
  `ReplaySession.emit()`에 0·3점을 입력하여 두 건 모두 전달되는 것을 검증함.
- 실행 파일 해시 Profile은 `positive_only`에서 `snapshot`으로 수정함.
  검사 성공 0점은 최신 관측 갱신이며 검사 실패·부분 무일치를 전체 정상으로
  보완하지 않음. 검사를 못 해서 Event가 없을 때 가짜 0점을 만들지 않음.
- YARA A 함수는 유효한 0점에도 `yara_pid:<pid>:<scope>`를 반환하도록 수정함.
  다른 PID/검사 범위의 결과를 같은 대상의 정상 복귀로 보지 않음.
- ERROR/OFFLINE/measurement_valid=false는 `MEASUREMENT_UNAVAILABLE`로 유지함.
  약한 이상 근거의 `NORMAL + raw_score>0`도 raw_score를 임의로 0으로 바꾸지 않음.
- Heartbeat는 생존 확인 경로를 유지함. 개별 검사 결과나 Scoring 점수로 합치지 않음.

### YARA 공통 저장 연동은 아직 남음

현재 공통 저장소는 YARA를 module-level 최신 한 건으로 보관함. A의 entity_key만
추가해도 대상별 SQLite 상태가 생기지는 않음. 전체 snapshot으로 바꾸면 PID A의
양수 이후 PID B의 0점이 양수 상태를 덮을 수 있으므로 이번 작업에서 그렇게 바꾸지 않음.

Profile의 기존 `per_entity_positive_only` 호환 분류는 보수적으로 유지하고 note에
PR #84의 0점 전송 및 PID/scope 저장 미연결을 명시함. 이는 sender가 여전히
양수만 보낸다는 뜻이 아님. B에서 PID·scope별 저장/조회·해소 범위를 확정한 뒤
Profile/RiskInput까지 함께 변경해야 함. 지금 YARA 최신 한 건을 전체 현재 위험도로
사용하면 안 됨. YARA의 기본 상한 3과 사용자 규칙 상한 10도 기존 검토 제한을 유지함.

## 4. PR #86의 마지막 RPC 검사까지 연결함

일반 검사에서는 `sample_id=1`, RPC 전용 마지막 검사에서는 `sample_id=0`이
나오나 기존 Scoring은 1만 의미 중복 키로 인정하는 문제가 있었음. 실제 생산자 →
HTTP Receiver 테스트에서 별도 event_id의 같은 마지막 구간이 history에 두 번
쌓이는 것을 재현하여 `storage.py`의 허용값을 정수 0/1로 수정함.
문자열·bool·음수·다른 sample 번호는 의미 중복 키로 신뢰하지 않음.

- Shared 재전송: 기존 event_id idempotency를 사용함.
- RPC 의미 중복: `(session_id, player_id, module, evidence.window_id,
  evidence.sample_id)`를 사용함. 0도 같은 규칙으로 처리함.
- 같은 구간의 다른 내용: 최초 이력을 덮지 않고 기존 conflict audit를 유지함.
- 후크가 멈춰 `meta.hook_live=false`라도 새 구간에 기록된 양수 위반은 보존함.
  hook_live만으로 측정 불가로 바꾸지 않음.
- 새 위반이 없고 후크가 stale이면 ERROR 0점임. 후크가 살아 있는 새 무위반
  구간의 NORMAL 0점과 구분함. 정상 구간이 이전 양수 history를 삭제하지 않음.
- 지금 게임 시작 이전 로그의 위반은 종료 구간으로 구제하지 않음.
- cursor를 같은 t0로 이어 읽으면 한 번만 평가하며, `--overwrite`로 세션 시각이
  달라지면 이전 unread 위반을 새 세션으로 가져오지 않는 것을 검증함.

테이블/DB 형식은 바꾸지 않음. 이 수정 이전에 이미 `semantic_key_valid=0`으로
저장된 sample 0 이력을 자동으로 재작성하지도 않음. 기존 DB에 그런 자료가
있다면 B와 별도 점검해야 하며, 원본 JSONL/DB를 삭제해서 해결하지 않음.

## 5. 근거가 대응할 때만 overlap 후보를 생성함

LocalGuard·Hide의 overlap_tags가 비어 있어 동일 원인을 상관 분석으로 연결하지
못했으나 B와 나눈 후보 방향에 맞춰 `policies/overlap.py`를 추가함.
태그는 상관 후보이며 자동 감점·병합·최종 점수 보정이 아님.

| 후보 태그 | A 쪽 성립 조건 |
|---|---|
| hide_anywhere_injection | Hide의 유효한 meccha.dll 관측과 module 조회 성공 / Injection의 알려진 주입 reason과 정확한 meccha.dll 토큰 |
| hide_anywhere_value_tamper | Hide의 유효한 3회 확인 / Value Tamper의 Hide 6개 필드 중 reason과 관측 필드가 대응 |
| process_injection | Injection의 알려진 주입 reason과 runtime-bridge.dll 관측 |
| autopaint_artifact | 알려진 AutoPaint bridge 파일 / 운영 YARA 규칙 / AutoPaint 실행 이미지 카탈로그 ID 중 실제 근거가 존재 |
| godmode_behavior | Runtime의 알려진 무적 reason·값 근거·ReadProcessMemory 출처 |
| aimbot_behavior | Runtime의 알려진 회전 reason·값 근거·ReadProcessMemory 출처 |
| noclip_behavior | Runtime의 알려진 충돌 reason·값 근거·ReadProcessMemory 출처 |

PID 일치, 양수 점수, 임의 detail 문자열만으로 태그를 만들지 않음. 테스트용
YARA 규칙, Godmode도 공유하는 LoadLibrary 규칙, 이름이 비슷한 다른 DLL은
AutoPaint 또는 Hide 근거로 승격하지 않음. 실패·0점·조사 범위 초과에서도 태그를
만들지 않음. ESP와 Whistle 자체의 태그는 대응 기준을 추가로 합의하기 전까지
비워 두며 다른 탐지기와 무조건 합치지 않음.

기존 correlation은 같은 session/player·공통 tag·시간 창으로 후보를 만듦.
대상 key가 서로 다르다고 자동 제외하지 않으므로, B의 실제 risk 보정에서는
원인·대상·시간이 대응하는지 추가 확인해야 함. 태그 공유만으로 최종 감산하면 안 됨.

## 6. 검증 범위와 실행

Windows Python 3.13의 격리 테스트 환경에서 서버 requirements-dev와 지정된
yara-python 4.5.4를 설치해 검사함. 단위/로컬 통합 검증이며 실제 게임 탐지율,
클라우드 HTTPS, 런처 종료 타이밍의 실게임 재현 완료를 의미하지 않음.

| 최종 검증 그룹 | 결과 |
|---|---|
| Scoring 전체 (생산자 HTTP E2E 6개 포함) | 276개 통과 |
| Receiver detection·heartbeat | 30개 통과 |
| Shared 전송·기록·재시도 | 48개 통과 |
| External Access 핸들·모듈 무결성 | 44개 통과 |
| InputSignature/YARA/Hash/Heartbeat | 72개 통과 |
| Launcher 단위 테스트 | 25개 통과 |
| ESP 실제 생성 경로 호환성 도구 | 8개 통과 |
| A 계약 조사 도구의 합성 probe | assertion 통과 |
| PR #87 main + 이 브랜치 임시 병합 회귀 | Scoring 276·Receiver 30·Launcher 25·Hide 29개 통과(환경 자료 의존 6개 skip) |

서로 다른 실행 그룹 결과이며 이를 실게임 표본 수로 합산하지 않음.

```powershell
python -X utf8 -m unittest discover -s server/scoring/tests
python -X utf8 -m unittest discover -s server/receiver/tests
python -X utf8 -m unittest discover -s shared/tests
python -X utf8 -m unittest discover -s client/LocalGuard/external_access -t .
python -X utf8 -m unittest discover -s client/LocalGuard/input_signature/tests
python -X utf8 -m unittest discover -s client/Launcher/tests
python -X utf8 server/scoring/tools/audit_a_contracts.py
python -X utf8 server/scoring/tools/audit_esp_policy.py --repo-root .
```

HTTP E2E는 FastAPI TestClient → 실제 Shared writer → 실제 Scoring SQLite를
사용함. 검사 내용은 다음과 같음.

- 기존 external_access scoped state 교차 덮어쓰기 방지·RPC 중복·conflict·503 복구.
- 현재 Hide 생산자의 0/0/3점·실패 상태를 원본 7필드 그대로 저장함.
- 마지막 RPC sample 0의 exact retry·의미 중복·정상/오류 history·재시작.
- 현재 Hide와 LocalGuard 캡처를 시험 세션에 재생하여 기본 Registry가 대응하는
  두 Hide overlap 후보만 생성하며 점수 3/40/100은 변경하지 않는 것을 확인함.

FastAPI/Starlette의 폐기 예정 API 경고는 발생하나 검사 실패는 아님.

## 7. 체크리스트와 후속 담당

### 이번 작업 완료

- [x] 최신 병합 코드의 생산자·A 정책 계약을 다시 맞춤.
- [x] Hide의 3회 확인·읽기 실패·보조 조회 실패·구버전 캡처 의미를 구분함.
- [x] PR #82/#84의 정상 0점 전송과 Hash snapshot Profile을 반영함.
- [x] YARA 유효 0점의 PID/scope key를 준비함. 실제 scoped 저장 완료와 구분함.
- [x] 마지막 RPC sample 0 중복 방지 오류를 수정하고 HTTP/재시작 회귀 검사를 추가함.
- [x] 근거 조건부 overlap 태그와 양성·음성·기본 Registry 호환 검사를 추가함.
- [x] PR #87 main과 임시 병합하여 코드 충돌 없음과 Hide 런처 인자·환경·outbox 계약을 확인함.
- [x] 이전 조사 내용·Replay·사용자의 미커밋 파일을 보존함.

### B와 연결/결정해야 함

- [ ] 공통 storage.py/Profile의 최소 호환 변경을 B가 검토해야 함.
- [ ] YARA PID/scope 상태 저장·정상 복귀·종료 PID 처리 범위를 구현/합의해야 함.
- [ ] overlap 후보의 실제 감산 여부와 대상·시간 대응 기준을 정해야 함.
- [ ] Hide의 `local_session_start`와 공통 `--t0` 시계를 맞추기 전에는 시간 차이만으로 overlap 감산하지 않아야 함.
- [ ] whistle_rpc TTL 및 종합 risk/최종 판정 정책을 Replay 기반으로 확정해야 함.
- [ ] 이전 DB에 sample 0의 비의미 이력이 있으면 보존한 채 점검해야 함.

### C·런처·실게임 검증

- [ ] C에서 writer → configure_scoring → recovery → Receiver 활성화 순서를 연결함.
- [ ] B가 Final Verdict 공개 API/반환형을 확정하면 C의 Dashboard 조회 API와 연결함.
- [ ] 실제 클라이언트 → 운영 HTTPS 수신/재전송 및 게임 종료 마지막 window를 검증함.
- [ ] ESP가 Hide 수집기 PID를 자기 탐지에서 제외하도록 수정한 뒤 두 모듈 동시 실행을 검증함.
- [ ] Hide 정상 Replay는 `normal_...` 세션명을 사용하거나 런처에서 `--play-label NORMAL`을 전달함.
- [ ] 충돌 PR #85는 별도 계약 검토/해소 후 반영함. 이번 패치로 해결 처리하지 않음.

가중치·TTL·최종 판정처럼 팀 결정이 필요한 값은 임의로 채우지 않음.

## 8. PR #89 및 최신 main 후속 갱신 (2026-10-04)

PR #88과 PR #89가 팀 main에 병합된 뒤의 코드를 다시 확인함. B에서 다음 항목을
완료함.

- `localguard_yara`를 `PID + scope` 기준 scoped state로 독립 저장하고 Profile을
  `per_entity_snapshot`으로 변경함. 다른 대상의 NORMAL 0점이 기존 양수 상태를
  덮지 않으며 같은 PID/scope의 0점만 해당 상태를 정상으로 갱신함.
- A가 만든 reason/evidence 기반 overlap tag만 사용하고, 단순 PID 일치나 양수
  점수만으로 중복 처리하지 않음.
- overlap 후보를 같은 원인의 evidence unit으로 묶되 서로 다른 tag의 연쇄 관계를
  하나로 축소하지 않도록 보호함.
- Godmode event_delta를 합산하지 않고 이력에서 qualifying event 존재 여부로
  해석함.
- Receiver → Shared writer → Scoring → Final Verdict 경로를 E2E로 연결함.
  v1 최종 상태는 `SUSPICIOUS`, `INCONCLUSIVE`, `NO_ACTIVE_EVIDENCE`이며,
  `SUSPICIOUS`는 치트 확정을 의미하지 않음.

PR #89 직후 Hide 생산자 폴더가 `client/detectors/hide_anywhere/`로 이동하고
`--t0` 및 `SessionClock` 지원이 추가됨. 폴더 이동 뒤 남아 있던 이전 경로 때문에
A Scoring 회귀 3건이 실패하고 런처가 존재하지 않는 스크립트를 가리키는 문제를
재현함. 다음 호환 수정을 적용함.

- Launcher의 Hide 실행 파일·출력·세션 로그 경로를 새 폴더로 변경함.
- Launcher가 Hide에 공통 `{t0}`를 전달하도록 연결함. 실제 런처 실행 Event는
  `timestamp_basis=launcher_session_start`를 사용하므로 다른 공통 시계 탐지기와
  시간창 비교가 가능함.
- Scoring Profile, A 계약 감사 도구, Hide 정책/E2E 테스트의 생산자 경로를 새
  폴더로 변경함.
- 런처 등록 테스트에 새 경로·PID·session/player·t0·전송 on/off 인자를 추가함.

최신 main과 위 수정을 합친 상태에서 Scoring 341개, Receiver 30개, Launcher
27개, Hide 33개가 통과함. Hide 6개는 SDK/실행 파일 자료가 없는 환경 의존 검사로
skip됨. A 계약 감사와 ESP 생산자 호환 검사 8개도 통과함.

B의 `local_session_start` 차단 guard는 `--t0` 없이 Hide를 단독 실행한 Event에
대해서는 계속 필요함. 런처 공통 t0가 전달된 Event에는 해당 guard가 적용되지
않으므로 정상적인 time-window correlation 후보를 만들 수 있음.

### 최신 남은 작업

- [x] YARA PID/scope scoped state와 `per_entity_snapshot`을 B에서 연결함.
- [x] Hide 런처에 공통 t0를 전달하고 새 생산자 경로를 연결함.
- [x] B의 Aggregate Risk·Final Verdict 및 Receiver E2E를 구현함.
- [ ] Whistle RPC TTL/Expiry는 실제 주기·Replay 근거로 확정해야 함.
- [ ] ESP가 Hide 수집기 PID를 자기탐지에서 제외하도록 수정해야 함.
- [ ] C에서 production startup과 Dashboard 조회 API를 최종 연결해야 함.
- [ ] 운영 HTTPS 및 실제 게임 세션에서 Launcher → Receiver → Final Verdict를 검증해야 함.
