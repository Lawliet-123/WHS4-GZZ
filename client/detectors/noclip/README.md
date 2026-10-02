# Noclip Detector

MECCHA CHAMELEON의 Noclip 이상 행위를 탐지하는 실시간 detector입니다.

UE4SS Lua Logger가 플레이어 상태를 주기적으로 CSV에 기록하고,
Python detector가 새 로그를 실시간으로 읽어 탐지 점수를 계산합니다.

탐지 결과는 로컬 파일에 저장하며,
공통 7필드 Event 형식으로 `shared`를 통해 중앙 서버에도 전달합니다.

## 구조

```text
client/
├── detectors/
│   └── noclip/
│       ├── main.py
│       └── README.md
│
└── ue4ss/
    └── Mods/
        └── NoclipLogger/
            └── Scripts/
                └── main.lua
```

## 동작 흐름

```text
MECCHA CHAMELEON
        ↓
UE4SS NoclipLogger
        ↓
noclip_log.csv
        ↓
Noclip Detector
        ↓
탐지 점수 계산
        ↓
├─ events.jsonl
├─ detection_results.csv
└─ shared.send_detection()
        ↓
중앙 서버
```

## 탐지 기준

Noclip detector는 현재 다음 기준으로 점수를 계산합니다.

| 조건 | 점수 |
|---|---:|
| Collision OFF | +1 |
| Collision OFF 상태 5초 이상 지속 | +2 |
| Blocking Object 통과 감지 | +2 |

`raw_score >= 3`이면 SUSPICIOUS 상태로 판단합니다.

탐지 후 5초 동안 SUSPICIOUS 상태를 유지합니다.

## 공통 Event

중앙 서버로 전달하는 결과는 팀 공통 7필드 형식을 사용합니다.

```json
{
  "session_id": "session_001",
  "player_id": "player_001",
  "module": "noclip",
  "timestamp_ms": 5000,
  "evidence": {
    "collision": 0,
    "blocked_path": 0,
    "status": "SUSPICIOUS",
    "measurement_complete": true,
    "collision_valid": true,
    "blocked_path_valid": true,
    "detection_hold_active": false,
    "sample_id": 6
  },
  "reasons": [
    "Collision Disabled",
    "Collision Disabled Too Long"
  ],
  "raw_score": 3
}
```

## 실행

```bash
python3 client/detectors/noclip/main.py \
  --session-id session_001 \
  --player-id player_001 \
  --log-file /path/to/noclip_log.csv
```

옵션 확인:

```bash
python3 client/detectors/noclip/main.py --help
```

런처 연동 시에는 다음 옵션을 함께 사용할 수 있습니다.

- `--from-end`: detector 시작 전에 CSV에 이미 남아 있던 완성된 행은 건너뛰고 이후 행만 처리
- `--t0 <EPOCH>`: 런처 세션 시작 epoch를 받아 다른 모듈과 `timestamp_ms` 기준을 맞춤

```bash
python3 client/detectors/noclip/main.py \
  --session-id session_001 \
  --player-id player_001 \
  --log-file /path/to/noclip_log.csv \
  --from-end \
  --t0 1760000000.000
```

`NoclipLogger`의 CSV는 현재 작업 디렉터리가 아니라 Lua `main.lua`의 실제 위치를
기준으로 `NoclipLogger/noclip_log.csv`에 생성됩니다.

## Shared 0.2.0 연동

프로그램 시작 시 `configure_client(ClientConfig.from_env())`를 한 번 호출하고,
종료 시 `flush_client()`와 `shutdown_client()`를 호출합니다.

점수를 계산한 **모든 샘플**은 `raw_score=0`인 정상 결과를 포함해
먼저 로컬 `events.jsonl`에 기록한 뒤 같은 공통 Event를
`send_detection()`에 전달합니다.

`send_detection()`의 `queued`는 Shared의 로컬 outbox에 저장되었다는 뜻이며,
중앙 Receiver가 저장을 완료했다는 뜻은 아닙니다.

공통 Event 최상위는 기존 7필드만 유지하고 추가 상태 정보는
`evidence` 안에 기록합니다.

- `status=NORMAL`: 필요한 관측이 완료되었고 현재 Noclip 판정 기준에서 의심 상태가 아님
- `status=SUSPICIOUS`: 기존 Noclip 판정 기준에서 의심 상태임
- `status=ERROR`: 센서 관측값 일부 또는 전체가 유효하지 않아 완전한 정상 측정으로 볼 수 없음
- `measurement_complete`: collision과 blocked_path 관측이 모두 유효한지 표시
- `collision_valid`, `blocked_path_valid`: 각 관측 채널의 유효성
- `detection_hold_active`: 직전 탐지 후 5초 유지 상태인지 표시
- `sample_id`: Lua 관측 순번이며 Shared의 전송 `event_id`와는 다른 값
- `error_code`: 관측이 불완전할 때 원인을 구분하는 Noclip 전용 코드

Lua logger는 Collision을 읽지 못하거나 LineTrace에 실패하면 `-1`을 기록할 수 있습니다.
이 값은 정상 0점으로 처리하지 않고 관측 실패로 구분합니다.

플레이어/Pawn/위치 자체를 얻지 못해 Lua가 샘플을 만들지 않은 경우에는
Python detector도 점수를 계산하지 않으므로 Event를 임의 생성하지 않습니다.
따라서 **Event 미수신을 정상 0점으로 해석하면 안 됩니다.**

런처는 각 탐지기 프로세스에 서로 다른 `GZZ_TELEMETRY_OUTBOX` 경로를 제공합니다.
지속 재시도가 필요한 배포 환경에서는
`GZZ_TELEMETRY_RETRY_MODE=persistent`를 사용할 수 있습니다.

중앙 서버 전송에 문제가 발생하더라도
이미 기록된 로컬 Noclip 결과와 탐지 동작은 유지합니다.

## 로컬 출력

기본적으로 다음 파일을 생성합니다.

- `events.jsonl`: 공통 7필드 Event
- `detection_results.csv`: SUSPICIOUS 구간별 탐지 결과

## 테스트

로컬 테스트에서 다음 항목을 확인했습니다.

- CSV 새 행 실시간 수집
- Collision OFF 지속시간 계산
- 5초 이후 `raw_score` 1 → 3 변화
- 공통 7필드 JSONL 생성
- 정상 `raw_score=0` 결과의 로컬 기록 및 shared outbox 등록
- 관측 실패(`-1`)와 정상 0점의 구분
- shared 전송 큐 등록

`queued`는 로컬 outbox 등록 성공만 의미합니다.
실제 중앙 Receiver의 `/api/detection` 저장 성공 여부는
중앙 서버 연동 환경에서 별도로 E2E 검증합니다.

## ReplayAnalyzer

ReplayAnalyzer는 실시간 detector가 아니라 탐지 성능을 사후 검증하는 도구입니다.

Noclip Detector에서 생성한 데이터를 이용해
threshold, FP/FN, detection latency 등을 검증할 수 있습니다.
