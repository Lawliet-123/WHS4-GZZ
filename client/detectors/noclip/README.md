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
    "blocked_path": 0
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

## Shared 연동

프로그램 시작 시 shared client를 설정하고,
각 샘플의 공통 Event를 `send_detection()`으로 전달합니다.

종료 시 `flush_client()`와 `shutdown_client()`를 실행합니다.

중앙 서버 전송에 문제가 발생하더라도
로컬 Noclip 탐지는 계속 동작하도록 구성되어 있습니다.

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
- shared 전송 큐 등록

실제 중앙 Receiver와의 `/api/detection` E2E 테스트는
중앙 서버 연동 환경에서 별도로 수행합니다.

## ReplayAnalyzer

ReplayAnalyzer는 실시간 detector가 아니라 탐지 성능을 사후 검증하는 도구입니다.

Noclip Detector에서 생성한 데이터를 이용해
threshold, FP/FN, detection latency 등을 검증할 수 있습니다.
