# 중앙 서버 A 담당 탐지기 전송 규격 조사

조사일: 2026-10-02. 기준: 팀 `main`의 `f098b5d`에서 분기한 `feat/scoring-a-detector-policies`.

LocalGuard 계열, Whistle Spoofing, Hide Anywhere를 조사함. 사용자 요청에 따라 ESP는 조사 및 정책 구현 대상에서 제외함. 하트비트는 탐지 점수와 분리된 경로이므로 이번 점수 조사에서 제외함.

소스 확인과 기존 리플레이 검사, 입력값을 공급한 로컬 재현을 수행함. 게임 실행, 실제 네트워크 전송, 중앙 서버 수신 성공은 이번에 검증하지 않음. 기존 Scoring 공통 코드와 탐지기 코드는 변경하지 않음.

## 1. 실제 전송 이름과 점수

폴더명·런처 등록명과 이벤트의 `module` 값이 다를 수 있어 실제 생성 코드를 기준으로 조사함. 전송 방식은 로컬 기록과 중앙 전송을 구분하여 확인함.

| 실제 `module` | 현재 점수 구성 | 중앙 전송 조건 | 실행 주기 | 근거 |
|---|---|---|---|---|
| `external_access` | 위험 권한 2+2+3, 서명 최대 +2, 경로 +1. 생성되는 양수 점수는 2~10. VM_READ 단독은 이벤트 없음 | 허용목록에 없는 위험 접근이 관찰될 때, 원인 프로세스별 양수 이벤트 | 기본 3초 | `client/LocalGuard/external_access/process_access/access_rights.py:20`, `detector.py:28`, `detector.py:35`, `runner.py:213` |
| `localguard_yara` | 일치 규칙 점수의 최댓값. 기본 저장소 규칙은 모두 3점. 사용자 지정 규칙은 1~10점 허용 | 로컬은 검사 성공 시 0점도 기록. 중앙은 양수만 | 기본 YARA 검사 시작 간격 60초. 검사 소요에 따라 달라짐 | `client/LocalGuard/input_signature/yara_scanner.py:190`, `yara_scanner.py:244`, `yara_scanner.py:273`, `yara_scanner.py:503`, `replay_events.py:199` |
| `localguard_executable_hash` | 알려진 실행 파일 해시 일치가 하나 이상이면 1점, 아니면 0점. 일치 파일 수를 합산하지 않음 | 로컬은 완전 검사 또는 일치 발견 시 기록. 중앙은 양수만 | 기본 5초, 별도 모니터 스레드 | `client/LocalGuard/input_signature/hash_monitor.py:64`, `hash_monitor.py:71`, `hash_monitor.py:82`, `yara_scanner.py:497`, `replay_events.py:199` |
| `filesystem` | 파일·UE4SS·외부 Lua 모드 등의 규칙 점수 누적, 100점 상한 | 로컬은 0점/상태도 기록. 중앙은 양수만 | 런처는 검사 프로세스를 30초 주기로 실행. 독립 watch 기본 간격은 15초 | `client/LocalGuard/memory_integrity/detectors/filesystem.py:165`, `core/result.py:96`, `run_session.py:309`, `client/Launcher/modules.py:169` |
| `injection` | ProcessEvent 후킹 70, ExecFunction 후킹 70, .text 변조 60, 비신뢰 모듈 40. 누적 상한 100 | 위와 같음 | 위와 같음 | `client/LocalGuard/memory_integrity/detectors/injection.py:67`, `injection.py:149` |
| `value_tamper` | 변조된 설정 필드 종류마다 55점, 누적 상한 100 | 위와 같음 | 위와 같음 | `client/LocalGuard/memory_integrity/detectors/value_tamper.py:314` |
| `overlay_hook` | 비신뢰 인라인 후킹 60점, 오버레이 관련 후킹 +60점. 누적 상한 100 | 위와 같음 | 위와 같음 | `client/LocalGuard/memory_integrity/detectors/overlay_hook.py:289`, `overlay_hook.py:294` |
| `godmode_runtime` | **현재 소스는 근거를 추가해도 점수를 올리지 않으므로 0점** | 양수 조건을 통과하지 못하여 중앙 전송 없음 | 메모리 탐지기와 같이 실행 | `client/LocalGuard/memory_integrity/detectors/godmode_runtime.py:118`, `run_session.py:309` |
| `noclip_runtime` | **현재 소스는 근거를 추가해도 0점** | 위와 같음 | 위와 같음 | `client/LocalGuard/memory_integrity/detectors/noclip_runtime.py:107`, `run_session.py:309` |
| `aimbot_runtime` | **현재 소스는 근거를 추가해도 0점** | 위와 같음 | 메모리 탐지기와 같이 실행. 검사 한 번은 3초 동안 목표 60Hz로 회전값 수집 | `client/LocalGuard/memory_integrity/detectors/aimbot_runtime.py:14`, `aimbot_runtime.py:17`, `aimbot_runtime.py:220`, `run_session.py:309` |
| `whistle` | 관련 ExecFunction 후킹 60 / 기타 40, 캐릭터 vtable 후킹 60, 소리 교체 35. 누적 상한 100 | 메모리 탐지기와 같은 실행기를 사용하여 양수만 중앙 전송 | 런처 30초 주기, 독립 watch 기본 15초 | `client/detectors/whistle-spoofing/main.py:72`, `whistle.py:128`, `whistle.py:150`, `whistle.py:164`, `client/Launcher/modules.py:180` |
| `whistle_rpc` | 역할·사망·대상 위반 각각 60, 쿨다운·입력 부재 각각 40, 미분류 코드 30. 같은 코드의 발생 횟수는 점수에 곱하지 않음. 누적 상한 100 | 위와 같음. 런처/반복 경로는 새 로그 구간을 검사하여 양수만 보냄 | 위와 같음 | `client/detectors/whistle-spoofing/whistle_rpc.py:86`, `whistle_rpc.py:178`, `whistle_rpc.py:297`, `whistle_rpc.py:393` |
| `hide_anywhere` | 6개 고정값 조합 일치 시 3점. 미일치 시 `injected_module` + `viewport_hook`로 0~2점 | 설정된 ServerBridge가 있으면 0점 포함 매 평가 결과의 전송을 시도함. **현재 전송 호환 오류 존재** | 기본 1초. `--modules-only` 또는 메모리 관측기 초기화 실패 시 공통 이벤트 생성 경로가 실행되지 않음 | `client/detectors/Hide_anywhere_detector/mecha_detector_v9.py:66`, `mecha_detector_v9.py:85`, `mecha_logger.py:652`, `mecha_logger.py:774`, `mecha_logger.py:788` |

LocalGuard 등록표의 실제 이름은 `client/LocalGuard/memory_integrity/run_session.py:69`에서 확인함. 이벤트는 `core/result.py:367`에서 `res.detector`를 `module`로 사용함. `memory_integrity`, `input_signature`, `whistle_spoofing`이라는 런처 이름으로 중앙 정책을 등록하면 실제 이벤트 이름과 맞지 않음.

## 2. 중앙 점수 해석에 적용할 사항

### 2.1 양수만 전송하는 모듈

양수 전송을 확인한 모듈에서 마지막 이벤트는 마지막으로 관찰한 탐지 근거임. 이후 핵이 꺼졌거나 검사가 실패해도 중앙에 0점으로 갱신되지 않을 수 있음. 새 이벤트가 없다는 사실만으로 정상 상태, 계속 핵을 사용하는 상태 중 어느 쪽도 확정하지 않도록 해야 함. 점수의 유효 시간 및 유지·만료 기준은 B와 협의가 필요함.

메모리·휘파람 계열은 `run_session.py:309`의 양수 조건을 사용함. YARA·해시 계열은 `input_signature/replay_events.py:199`의 양수 조건을 사용함. 외부 접근은 `external_access/process_access/detector.py:37`에서 위험 권한이 없으면 이벤트 자체를 생성하지 않음.

### 2.2 ERROR/OFFLINE과 정상 0점

메모리·휘파람 로컬 결과의 `status`, `severity`, `window_id`, `sample_id`, 스캔 시간 등의 확장 필드는 `core/result.py:305`의 `to_shared_event()`가 `evidence` 안으로 이동시킴. Shared 최상위 7필드를 유지하면서 상태 정보를 보존함.

일반적인 0점 ERROR/OFFLINE은 양수 조건 때문에 중앙으로 전송되지 않음. 이미 양수 점수가 생긴 후 오류가 발생한 경우처럼 양수 ERROR가 전달되면 `evidence.status`를 우선 확인해야 함. 현재 B의 `policy.py:110`은 ERROR/OFFLINE을 `MEASUREMENT_UNAVAILABLE`로 구분함.

YARA는 읽기 실패·타임아웃을 정상 0점 이벤트로 생성하지 않음. 해시 검사는 일부 프로세스를 확인하지 못해도 일치가 확인되면 1점을 보낼 수 있으므로 `coverage_complete=false`를 함께 확인해야 함.

### 2.3 대상 식별과 중복 후보

| 모듈 | 실제로 있는 식별 근거 | 정책 작성 시 주의사항 | 중복 후보 태그 제안 — 아직 미확정 |
|---|---|---|---|
| `external_access` | `source_pid`, 선택적으로 `sha256`, `source_path` | 하나의 세션·플레이어·모듈 안에서도 여러 원인 프로세스가 있음. PID 재사용을 구분할 생성 시각이 이벤트에 없음. `(source_pid, sha256)`도 완전한 프로세스 수명 식별자는 아님 | `process_access`, 파일 해시가 있을 때 해시 계열과 비교 |
| `localguard_yara` | `pid`, `scope`, `matched_rules`, 선택적 `module_name` | 대상은 게임 프로세스일 수도 외부 Python일 수도 있음. PID와 scope를 함께 검토. 규칙명만으로 동일 사건 확정 불가 | 매칭 규칙·검사 범위별 후보 태그 |
| `localguard_executable_hash` | `matched_executables` 목록 안의 `pid`, `sha256`, `catalogue_ids` | 한 이벤트가 여러 실행 파일을 포함할 수 있어 임의로 첫 PID만 대표로 정하지 않음 | `known_executable_presence` |
| `filesystem` | `file`, `file_2` 등 여러 파일, `meta.game_dir`, `meta.ue4ss_manifest` | 파일 존재는 활성화 증거와 구분. 여러 파일을 하나의 사건으로 단정하지 않음. 최신 코드는 런처 설치 UE4SS의 manifest/해시 기반 제외를 포함함 | `artifact_presence` |
| `injection`, `overlay_hook`, `whistle` | 주소·모듈명·이유 코드, `meta.target_pid` | `target_pid`는 관찰 대상 게임 PID이며 원인 프로세스 PID가 아님. 같은 게임 PID만으로 중복 확정 불가 | 해당 이유 코드에 따라 `exec_function_hook`, `process_event_hook`, `inline_hook` |
| `value_tamper`, `hide_anywhere` | 설정 필드/값, Hide Anywhere v9의 세 가지 플래그 | Hide Anywhere의 설정 변조를 두 채널이 볼 수 있음. v9 공통 이벤트에는 Pawn 주소나 프로세스 수명 식별자가 없음 | `hide_value_pattern`, 설정 변조 관련 후보 |
| `godmode_runtime`, `noclip_runtime`, `aimbot_runtime` | 메타데이터의 Pawn/Controller 주소, 이유 코드 | 점수 미연결부터 확인 필요. 메모리 주소는 다른 라운드·프로세스 수명에서 재사용 가능 | 대응하는 `godmode`, `noclip`, `aimbot` 채널과의 중복 후보 |
| `whistle_rpc` | 이유 코드, `meta.log`, `meta.mode`, window 통계 | 후킹 상태를 보는 `whistle`과 RPC 위반을 보는 채널은 관측 의미가 다름. 원시 RPC의 사건 ID가 공통 이벤트에 없음 | `whistle_rpc_violation` |

위 태그는 B에게 검토를 요청할 후보임. 자동 감점·합산·중복 제거 규칙으로 확정하지 않음. 여러 독립 대상을 단일 `entity_key`로 어떻게 표현할지는 공통 인터페이스 및 저장 정책과 함께 협의해야 함.

## 3. 이번에 발견하고 재현한 문제

### 3.1 Hide Anywhere의 event_id가 Shared 규격과 맞지 않음

`server_bridge.py:20`은 `32자리 UUID hex:순번`을 전달함. `shared/schema.py:24`는 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` 형식의 소문자 canonical UUID만 허용함. 실제 `validate_event_id()`에 연결하여 `ServerBridge.send()`를 실행하니 `server_enqueue_error / ValidationError`를 확인함.

기본 logger import 경로도 `mecha_logger.py:664`의 `detection_logger.logger`이므로 현재 저장소의 `shared.logger`와 맞추거나 실행 인자로 명시해야 함. `--server-config`를 지정해야 서버 전송기가 생성됨(`mecha_logger.py:724`). 런처의 현재 `modules.py`에는 Hide Anywhere 실행 항목이 없음.

이벤트 본문이 7필드 검사를 통과하는 것과 실제 outbox/서버에 도달하는 것은 별개임. 담당자에게 event_id 생성과 실행·설정 연결을 확인 요청해야 함.

### 3.2 세 가지 LocalGuard runtime 탐지기의 점수 연결 누락

실제 메모리를 읽지 않고 메모리 관측값 및 규칙 반환값을 공급하여 `scan()`을 실행함. Noclip은 충돌 비트가 꺼진 값, Godmode는 무적 상태값, Aimbot은 규칙 근거가 반환되는 상황을 사용함.

| 모듈 | 생성된 이유 코드 | 근거 개수 | `raw_score` | 변환 후 상태 | 양수 전송 조건 |
|---|---|---:|---:|---|---|
| `noclip_runtime` | `collision_bit_cleared` | 1 | 0 | `NORMAL` | 통과 못함 |
| `godmode_runtime` | `invincible_enabled`, `godmode_value_pattern` | 2 | 0 | `NORMAL` | 통과 못함 |
| `aimbot_runtime` | `control_rotation_pattern` | 1 | 0 | `NORMAL` | 통과 못함 |

각 구현이 reasons/evidence만 추가하고 `DetectorResult.add()`나 점수 대입을 호출하지 않기 때문임. 현재 상태를 정상이라고 해석하거나 서버에서 임의 점수를 부여하면 탐지기 의도와 달라질 수 있음. LocalGuard 담당자에게 점수 부여 계획과 관측 전용 여부를 확인해야 함. 이 세 모듈은 B 담당의 `godmode`, `noclip`, `aimbot`과 이름 및 경로가 다름.

### 3.3 Hide Anywhere의 세 번 확인 로직과 실제 점수 생성 경로가 다름

`mecha_detector_v9.py:37`에 3회 연속 확인하는 `Rule` 클래스는 존재함. 그러나 `mecha_logger.py:778`의 이벤트 생성 경로는 `make_common_event()`를 직접 호출하며 `Rule`을 사용하지 않음. 현재는 고정값 조합이 한 번 일치해도 바로 3점이 생성됨. 정책이나 문서에서 현재 동작을 3회 연속 확정으로 설명하면 안 됨.

### 3.4 Hide Anywhere 0점의 측정 유효성을 공통 이벤트에서 구분하기 어려움

`make_common_event()`는 값이 부족한 경우도 패턴 미일치로 처리함(`mecha_detector_v9.py:26`). 현재 공통 evidence는 세 가지 플래그만 있으므로 관측 실패·정상 미일치를 구분할 수 없음. 일부 필드의 읽기가 실패하면 `field()`가 이전 값을 유지할 수도 있음(`mecha_logger.py:303`). 따라서 모든 플래그가 0이라고 확정 정상으로 처리하거나 신선한 완전 측정이라고 가정하지 않도록 해야 함. 검사 유효성·신선도는 담당자에게 확인 필요함.

### 3.5 YARA 상한은 사용 규칙에 따라 달라짐

B의 `policy.py:68`은 현재 기본 규칙 기준으로 상한 3을 기록함. 기본 `repository_cheats.yar`의 규칙 점수는 모두 3이지만 scanner는 `--rules`를 추가할 수 있고 1~10점을 허용함(`yara_scanner.py:190`). 새 규칙으로 4점 이상이 생성되면 현재 B 코드는 `OUT_OF_AUDITED_RANGE`로 분류함. 버전·규칙 목록을 함께 공유하고 상한 변경이 필요한 경우 B와 협의해야 함.

## 4. 실제 리플레이 데이터 확인

`ReplayAnalyzer/replay-data`의 `events.jsonl`을 검사하되 `raw/`의 원시 로그는 제외함. ESP 이벤트 자체는 대상에서 제외함. ESP 테스트 폴더 등에 함께 기록된 LocalGuard 이벤트는 해당 LocalGuard 모듈의 기존 샘플로 집계함. 테스트 폴더 이름이 NORMAL/CHEAT여도 개별 모듈의 검사 성공을 보장하지 않으므로 이벤트 상태를 따로 확인함.

| 모듈 | 기존 기록 수 | 양수 | 0점 관측 | ERROR/OFFLINE 0점 | 원본 Shared 검증 |
|---|---:|---:|---:|---:|---|
| `filesystem` | 4 | 4 | 0 | 0 | 확장 필드 때문에 원본 거절, 변환 후 4건 통과 |
| `injection` | 48 | 44 | 4 | 0 | 원본 거절, 변환 후 48건 통과 |
| `value_tamper` | 48 | 42 | 6 | 0 | 원본 거절, 변환 후 48건 통과 |
| `whistle` | 40 | 1 | 39 | 0 | 원본 거절, 변환 후 40건 통과 |
| `whistle_rpc` | 40 | 0 | 0 | 40 | 원본 거절, 변환 후 40건 통과 |
| `hide_anywhere` | 147 | 89 | 58 | 0 | 본문 147건 통과. 측정 유효성·전송 성공은 별도 확인 필요 |
| `external_access`, YARA, 실행 파일 해시, `overlay_hook`, runtime 3종 | 0 | — | — | — | 이 리플레이 폴더에서 실전 샘플을 찾지 못함 |

합계 327건 중 확장 필드가 있는 180건을 실제 `to_shared_event()`로 변환하니 전부 7필드 검증을 통과함. **변환 함수를 사용하는 현재 코드의 형식 검증 결과이며, 기존 파일을 수정하거나 실제 서버에 재전송한 결과는 아님.**

`whistle_rpc` 40건이 모두 ERROR이므로 정상 동작/위반 점수 실전 샘플을 확보했다고 말할 수 없음. 후크 활성화 및 관측 성공 상태에서 새 로그를 확보해야 함.

### 실전 예시 위치

| 모듈 | 0점/정상 관측 예시 | 탐지 관측 예시 | 비고 |
|---|---|---|---|
| `filesystem` | 없음 | `ReplayAnalyzer/replay-data/whistle-spoofing/hack_001/events.jsonl:1` | 예전 UE4SS 자기 탐지 영향 가능. 최신 예외 처리 검증용으로 그대로 사용하지 않음 |
| `injection` | `ReplayAnalyzer/replay-data/normal/clean_002/events.jsonl:2` | `ReplayAnalyzer/replay-data/whistle-spoofing/hack_001/events.jsonl:2` | 로컬 확장 형식은 Shared용 변환 필요 |
| `value_tamper` | `ReplayAnalyzer/replay-data/normal/clean_002/events.jsonl:4` | `ReplayAnalyzer/replay-data/hide-anywhere/hide_hack_001/events.jsonl:2` | 로컬 확장 형식은 Shared용 변환 필요 |
| `whistle` | `ReplayAnalyzer/replay-data/normal/clean_002/events.jsonl:3` | `ReplayAnalyzer/replay-data/whistle-spoofing/hack_001/events.jsonl:3` | 탐지 예시 raw_score=100 |
| `whistle_rpc` | 성공한 0점 샘플 없음 | 위반 양수 샘플 없음 | `ReplayAnalyzer/replay-data/normal/wh_clean_004/events.jsonl:2`는 ERROR 예시이며 정상 예시가 아님 |
| `hide_anywhere` v9 | `ReplayAnalyzer/replay-data/hide-anywhere/hide_anywhere_003/events.jsonl:1` | 같은 파일 `:22` | 현재 세 플래그 구조. 0점/3점 예시 확보 |

Hide Anywhere `normal_001`, `hide_anywhere_002` 등의 예전 데이터는 실제 설정값·`matched_fields`·`consecutive_matches`를 기록하는 다른 형식임. 예를 들어 `hide_anywhere_002/events.jsonl:14`는 조합 일치 1회에 1점을 기록하지만 현재 v9 생성 함수는 조합 일치에 3점을 기록함. 필드와 점수 의미가 다른 버전을 섞어 임계치를 보정하지 않도록 구분함.

## 5. 확인된 JSON 예시

### 5.1 외부 접근: 입력값을 공급하여 현재 생성 함수로 재현한 예시

아래는 실전 캡처가 아닌 합성 입력 기반 예시임. VM_READ 단독 입력은 `None`을 반환하여 정상 0점 JSON이 생성되지 않음. 최대 점수 조합은 다음과 같이 생성되고 Shared 검사에 통과함.

```json
{
  "session_id": "audit_synthetic",
  "player_id": "audit_player",
  "module": "external_access",
  "timestamp_ms": 1000,
  "evidence": {
    "submodule": "external_process",
    "source_pid": 1234,
    "source_process": "example.exe",
    "source_path": "C:\\Users\\audit\\Desktop\\example.exe",
    "access_mask": "0x0000002A",
    "access_rights": ["PROCESS_VM_WRITE", "PROCESS_VM_OPERATION", "PROCESS_CREATE_THREAD"],
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "signature_status": "invalid",
    "publisher": null,
    "path_risk": "user_writable_location"
  },
  "reasons": [
    "External process opened PROCESS_VM_WRITE handle",
    "External process opened PROCESS_VM_OPERATION handle",
    "External process opened PROCESS_CREATE_THREAD handle",
    "Process executable signature is invalid",
    "Process executable is located in a user-writable directory"
  ],
  "raw_score": 10
}
```

### 5.2 Hide Anywhere: v9 형식의 기존 실전 캡처

`hide_anywhere_003/events.jsonl:1`과 `:22`에서 확인한 이벤트임. 0점 예시는 핵 테스트 시작 시점의 관측이며 전체 정상 플레이를 증명하지 않음.

```json
{"session_id":"hide_anywhere_003","player_id":"player_042","module":"hide_anywhere","timestamp_ms":40,"evidence":{"hide_value_pattern":0,"injected_module":0,"viewport_hook":0},"reasons":[],"raw_score":0}
```

```json
{"session_id":"hide_anywhere_003","player_id":"player_042","module":"hide_anywhere","timestamp_ms":21462,"evidence":{"hide_value_pattern":1,"injected_module":1,"viewport_hook":1},"reasons":["Hide Anywhere Value Pattern Matched","Injected Module Loaded","Viewport VTable Outside Main Image"],"raw_score":3}
```

다른 모듈의 전체 기존 이벤트는 다음 진단 스크립트의 `--examples`로 확인 가능함. 해당 출력은 소스 파일 경로·줄 번호·실전 캡처 여부·변환 적용 여부를 함께 출력함.

## 6. 이번에 추가한 재현 도구

`server/scoring/tools/audit_a_contracts.py`를 추가함. 저장소의 리플레이를 읽고 Shared 검사 결과를 출력하며 다음을 입력값/Mock으로 재현함. 파일 수정, 중앙 전송, 게임 프로세스 접근을 실행하지 않음.

1. 외부 접근 VM_READ 단독 입력에서 이벤트 미생성, 최대 점수 조합에서 10점 생성.
2. Hide Anywhere의 정상/패턴 조합에서 0점/3점 JSON 생성 및 본문 검증.
3. 실제 ServerBridge의 event_id를 Shared 검사에 연결하여 `ValidationError` 재현.
4. 세 runtime 탐지기의 근거 발견 후 0점/NORMAL 상태와 중앙 전송 조건 탈락 재현.
5. YARA/해시가 사용하는 `ReplaySession.emit()`에 0점·3점을 공급하면 로컬 기록 2건, 중앙 sink 전달 1건임을 확인.

실행 방법:

```powershell
python -B server/scoring/tools/audit_a_contracts.py
python -B server/scoring/tools/audit_a_contracts.py --examples
```

이 진단의 예상 오류 재현은 현재 코드의 문제를 확인하는 것이며, 해당 모듈이 운영 환경에서 정상 동작한다는 통과 판정이 아님.

진단 실행의 assertion을 모두 확인했고 기존 `test_policy`·`test_policy_contract` 22개 테스트도 통과함. 첫 제한 환경 실행은 임시 SQLite 폴더 접근 권한 때문에 실패하여 같은 테스트를 제한 밖에서 다시 실행한 결과임. 전체 Receiver/배포/실전 통합 테스트를 수행한 것은 아님.

```powershell
python -B -m unittest server.scoring.tests.test_policy server.scoring.tests.test_policy_contract
```

## 7. B 및 탐지기 담당자에게 공유할 사항

| 우선순위 | 확인·협의 사항 | 관련 담당 |
|---|---|---|
| 1 | Hide Anywhere의 canonical UUID, `shared.logger` 경로 및 실행 설정 연결 | Hide Anywhere 담당 / A 통합 |
| 1 | runtime 3종에 점수가 없는 이유 및 부여 계획. 의도한 관측 전용인지 확인 | LocalGuard runtime 담당 |
| 1 | `whistle_rpc` 후크 관측 성공 및 양수/성공 0점 실전 샘플 확보 | Whistle 담당 |
| 2 | 정확한 13개 module 중 활성 전송 가능한 항목을 정책에 등록. runtime은 미완료 상태를 명시 | A / B |
| 2 | 양수만 전송하는 모듈의 점수 유지·만료 기준 및 다중 원인 프로세스 저장 방식 | A / B |
| 2 | `overlap_tags` 이름, 코드/대상/시간의 조합으로 중복 후보를 비교하는 방법 | A / B |
| 2 | Hide Anywhere의 측정 유효성·실제 판정 경로·구버전 데이터 취급 | Hide Anywhere 담당 / A / B |
| 3 | 기본 YARA 상한 3과 추가 규칙 1~10점의 프로필 호환 | InputSignature 담당 / B |
| 3 | 부족한 정상·탐지 실전 샘플을 새 코드 버전에서 수집 | 각 탐지기 담당 / ReplayAnalyzer |

최종 위험도 가중치·임계값과 공통 파일 변경은 이번 조사에서 확정하지 않음. A 정책 파일과 테스트는 위 조사 결과를 기준으로 다음 단계에서 작성하며, 공통 등록 및 종합 점수는 B와 연결함.

## 8. 체크리스트

- [x] 최신 팀 main에서 A 작업 브랜치 생성 및 개인 fork에 push함.
- [x] ESP를 제외한 13개 실제 module 이름·점수 생성·중앙 전송 조건을 확인함.
- [x] 로컬 0점 기록과 중앙 양수 전송을 구분함.
- [x] 기존 리플레이 327건 검사와 180건 확장 형식 변환을 확인함.
- [x] 실전 예시와 합성 입력 재현을 구분하여 기록함.
- [x] Hide Anywhere event_id 오류와 runtime 3종 점수 누락을 로컬 재현함.
- [ ] 부족한 실전 정상·탐지 샘플을 수집해야 함.
- [ ] 발견한 탐지기 연결 문제를 담당자에게 공유하고 수정 방향을 확인해야 함.
- [ ] A 정책 파일 및 테스트를 구현해야 함.
- [ ] B의 Registry 등록과 Receiver → Scoring 실제 연결을 검증해야 함.
- [ ] 실제 중앙 서버 전송·재전송·중복 반영 방지 테스트를 수행해야 함.
- [ ] ESP는 코드가 올라온 뒤 별도 조사해야 함.
