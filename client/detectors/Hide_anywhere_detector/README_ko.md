# 메챠 카멜레온 로거 + 공통 이벤트 v9

## 파일 구성

- `mecha_logger.py`: Windows x64 실시간 수집기
- `mecha_detector_v9.py`: Hide Anywhere 점수 계산 및 공통 이벤트 생성
- `test_detector.py`: 단위·SDK·실행파일·DLL 검증 테스트

외부 패키지는 필요하지 않으며 Python 3.10 이상 64비트 버전을 사용한다.

## 정상 플레이 수집

```powershell
py -3.12 .\mecha_logger.py --pid 1234 --session-id normal_001 --player-id player_042 --module hide_anywhere --play-label NORMAL --code
```

## 핵 사용 수집

아래 예시는 핵을 세션 시작 후 30초에 켜고 70초에 끈 테스트다.

```powershell
py -3.12 .\mecha_logger.py --pid 1234 --session-id hide_anywhere_002 --player-id player_042 --module hide_anywhere --play-label CHEAT --cheat-start-ms 30000 --cheat-end-ms 70000 --code
```

종료는 `Ctrl+C`다. 시간은 로거 시작을 `0ms`로 한 경과시간이다. ON/OFF 시간을
실행 전에 정하지 않았다면 수집 후 `manifest.json`의 두 값을 실제 기록에 맞게
수정한다. 핵을 끄지 않고 종료했다면 `cheat_end_ms`는 `null`로 둔다.

## 출력 구조

```text
logs/
└── hide_anywhere_002/
    ├── manifest.json
    ├── events.jsonl
    └── raw/
        └── mecha_log.jsonl
```

- `raw/mecha_log.jsonl`: 기존 수집기 형식을 그대로 유지한 원시 로그
- `events.jsonl`: ReplayAnalyzer/Dashboard 공통 이벤트 전용 파일
- `manifest.json`: 정상/핵 여부와 핵 ON/OFF 시간

`events.jsonl`은 탐지된 순간만이 아니라 로거가 점수를 계산한 매 샘플을 기록한다.
Hide Anywhere의 전체 값 패턴은 3점, DLL 로드와 vtable 후킹은 각각 1점이며 값
패턴이 없을 때 최대 2점이다.

## 테스트

```powershell
py -3.12 -m unittest -v .\test_detector.py
```

SDK ZIP, 게임 EXE 또는 `meccha.dll`이 같은 폴더에 없으면 해당 정적 검증만
`skipped` 처리된다. 나머지 테스트가 `OK`이면 로거와 공통 포맷 검증은 통과한 것이다.
