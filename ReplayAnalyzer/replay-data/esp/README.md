# ESP replay data

## Sessions

- `normal_002`: 정상 플레이. ESP 사용 구간 없음.
- `normal_001`: 보관된 정상 플레이 캡처를 추가 내보낸 자료. 132개 Event의 점수·시각·세션 ID는 그대로 유지하고, 공개용 evidence에서 개인정보와 전체 경로를 제거했다. 원본 raw는 로컬에만 남긴다.
- `esp_001`: ESP 테스트. 테스트 메타데이터 기준 `30000ms`에 ON, `70000ms`에 OFF.

`esp_001`과 `normal_002`의 기존 센서 로그는 `raw/`에 보관했다. 새 `normal_001`은 원본 파일 SHA-256과 수집기 정보를 manifest에 남겼으며 raw를 복사하지 않는다. 새 시험을 한 것처럼 세션 ID를 바꾸거나 점수를 다시 계산하지 않았다.

`esp_only_001`에는 `module="esp"` Event가 없다. 다른 모듈의 이전 형식 Event 5개가 있는 자료이므로 ESP CHEAT 세션 수에 포함하면 안 된다. ESP 분석에 실제로 사용되는 자료는 CHEAT 1개, NORMAL 2개다.

## Timing note

`esp_001`의 첫 공통 탐지는 `60163ms`, 마지막 공통 탐지는 `231241ms`이다. 테스트 메타데이터에 기록된 OFF 시각 이후에도 프로세스 접근 또는 오버레이 관측이 계속됐으므로, `cheat_end_ms`는 테스트 구간 라벨이고 탐지 흔적의 마지막 시각을 뜻하지 않는다. 로그만으로 OFF 이후 관측이 계속된 원인은 확정할 수 없다.

`normal_002`에서도 `58591ms`에 저점수 모듈 이벤트 2건이 기록됐다. 정상 기준선에서 나온 이벤트이므로 삭제하거나 재분류하지 않았다.

## Calibration limitations

`normal_001`에는 1점 Event 119개와 3점 Event 13개가 있다. 정상 라벨을 유지하므로 threshold 2와 3에서는 오탐 표본으로 분석된다. 이 자료는 이전 수집기의 결과이며 현재 런처 제외·개인정보 보호 수정의 실게임 성능을 입증하지 않는다. 정상 표본의 높은 점수를 숨기거나 CHEAT로 재분류하지 않는다.

요청받은 추가 CHEAT 2개 + NORMAL 2개 중 이 내보내기는 NORMAL 1개만 보충한다. 남은 CHEAT 2개 + NORMAL 1개는 최신 수집기로 독립된 실제 게임 세션을 수집해야 한다. 합성 E2E 시험 자료를 실게임 Replay로 넣지 않는다. ON/OFF가 확인되지 않았다면 시각을 추정해 채우지 않는다.

## Export an existing capture

저장소 루트에서 실행한다. 수집 완료된 ESP 세션과 정확한 `test_metadata.scenario`가 필요하다. 기존 목적지 폴더가 있으면 덮어쓰지 않으며, 실패·관측 불가·빈 세션은 내보내지 않는다.

```powershell
py -3 ReplayAnalyzer/tools/export_esp_replay.py --source client/detectors/esp/data/sessions/normal_003 --output-root ReplayAnalyzer/replay-data/esp
py -3 -m unittest ReplayAnalyzer.tests.test_export_esp_replay -v
```

`normal_003`은 실제 새 수집 폴더로 바꾼다. 이 명령은 수집이나 ESP ON/OFF를 실행하지 않고 기존 결과의 공개용 사본만 만든다.
