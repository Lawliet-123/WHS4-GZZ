# ESP replay data

## 2026-10-06 실제 추가 수집

팀에서 승인한 비공개 방과 동일한 게임 인스턴스에서 독립 세션 3개를 새로 수집했다.
운영 수집기와 원본 ESP를 사용했으며 이벤트를 합성하거나 이전 로그를 복제하지 않았다.

| 세션 | 라벨 | Event | 실제 ON | 실제 OFF |
| --- | --- | ---: | ---: | ---: |
| `normal_realgame_20261006_capture02` | NORMAL | 63 | 없음 | 없음 |
| `esp_realgame_20261006_capture01` | CHEAT / ESP | 82 | 5706ms | 80409ms |
| `esp_realgame_20261006_capture02` | CHEAT / ESP | 67 | 5189ms | 77984ms |

ON은 실제 게임 핸들을 연 시각, OFF는 해당 ESP 프로세스 종료 관측이다. 실제 카메라와
로컬 캐릭터의 읽기 준비 완료는 각각 19208ms / 17655ms이며 그 뒤 최소 60초간
읽기를 유지했다. 각 ESP 프로세스에 직접 연결되는 탐지 Event는 12건 / 11건이다.
총 Event 건수에는 Windows 기본 프로세스 등의 관측도 포함되므로 전부 핵의 직접
근거라고 해석하면 안 된다. 원격 플레이어 렌더링은 입증하지 않았고 provenance에도
`remote_rendering_claimed=false`로 남겼다.

Sysmon·핸들·모듈·오버레이·관리자 권한을 실제로 관측했다. 초기 준비 로그는 별도로
보존하고 측정 세션의 모든 poll이 충분한 상태였는지 검증했다. 원본 raw/로컬 DB는
업로드하지 않았고 기존 exporter의 개인정보 필터와 공통 Event 7필드를 유지했다.
정상 세션의 높은 점수도 수정하지 않았으므로 오탐 보정용으로 그대로 사용할 수 있다.

앞서 추가한 보관 NORMAL 1개와 이번 NORMAL 1개 + CHEAT 2개로 요청된 추가 표본 수는
충족했다. ESP 자료는 총 CHEAT 3개 / NORMAL 3개이며 `esp_only_001`은 계속 제외한다.
아래 보관 세션 설명과 당시 미완료 메모는 과거 기록이다. threshold 확정은 이 새 자료를
포함해 scoring/ReplayAnalyzer 담당자가 다시 판단해야 하며 여기서 임의로 변경하지 않았다.

## Sessions

- `normal_002`: 정상 플레이. ESP 사용 구간 없음.
- `normal_001`: 보관된 정상 플레이 캡처를 추가 내보낸 자료. 132개 Event의 점수·시각·세션 ID는 그대로 유지하고, 공개용 evidence에서 개인정보와 전체 경로를 제거했다. 원본 raw는 로컬에만 남긴다.
- `esp_001`: ESP 테스트. 테스트 메타데이터 기준 `30000ms`에 ON, `70000ms`에 OFF.

`esp_001`과 `normal_002`의 기존 센서 로그는 `raw/`에 보관했다. 새 `normal_001`은 원본 파일 SHA-256과 수집기 정보를 manifest에 남겼으며 raw를 복사하지 않는다. 새 시험을 한 것처럼 세션 ID를 바꾸거나 점수를 다시 계산하지 않았다.

`esp_only_001`에는 `module="esp"` Event가 없다. 다른 모듈의 이전 형식 Event 5개가 있는 자료이므로 ESP CHEAT 세션 수에 포함하면 안 된다. 위 새 캡처를 추가하기 전 보관 자료는 CHEAT 1개, NORMAL 2개였다.

## Timing note

`esp_001`의 첫 공통 탐지는 `60163ms`, 마지막 공통 탐지는 `231241ms`이다. 테스트 메타데이터에 기록된 OFF 시각 이후에도 프로세스 접근 또는 오버레이 관측이 계속됐으므로, `cheat_end_ms`는 테스트 구간 라벨이고 탐지 흔적의 마지막 시각을 뜻하지 않는다. 로그만으로 OFF 이후 관측이 계속된 원인은 확정할 수 없다.

`normal_002`에서도 `58591ms`에 저점수 모듈 이벤트 2건이 기록됐다. 정상 기준선에서 나온 이벤트이므로 삭제하거나 재분류하지 않았다.

## Calibration limitations

`normal_001`에는 1점 Event 119개와 3점 Event 13개가 있다. 정상 라벨을 유지하므로 threshold 2와 3에서는 오탐 표본으로 분석된다. 이 자료는 이전 수집기의 결과이며 현재 런처 제외·개인정보 보호 수정의 실게임 성능을 입증하지 않는다. 정상 표본의 높은 점수를 숨기거나 CHEAT로 재분류하지 않는다.

보관 `normal_001` 내보내기 당시에는 요청받은 추가 CHEAT 2개 + NORMAL 2개 중 NORMAL 1개만 보충했다. 남았던 CHEAT 2개 + NORMAL 1개는 위 2026-10-06 실제 캡처로 추가했다. 합성 E2E 시험 자료를 실게임 Replay로 넣지 않으며, ON/OFF가 확인되지 않았다면 시각을 추정해 채우지 않는다.

## Export an existing capture

저장소 루트에서 실행한다. 수집 완료된 ESP 세션과 정확한 `test_metadata.scenario`가 필요하다. 기존 목적지 폴더가 있으면 덮어쓰지 않으며, 실패·관측 불가·과거 빈 세션과 CHEAT 0건 세션은 내보내지 않는다.

```powershell
py -3 ReplayAnalyzer/tools/export_esp_replay.py --source client/detectors/esp/data/sessions/normal_003 --output-root ReplayAnalyzer/replay-data/esp
py -3 -m unittest ReplayAnalyzer.tests.test_export_esp_replay -v
```

`normal_003`은 실제 새 수집 폴더로 바꾼다. 이 명령은 수집이나 ESP ON/OFF를 실행하지 않고 기존 결과의 공개용 사본만 만든다.

새 수집기의 NORMAL 0건은 `meccha.esp-observation.v1` 관측 요약이 있을 때만 별도로
검증한다. 최소 두 번의 충분한 poll, 모든 필수 센서의 실제 성공, 동일 게임 인스턴스,
일관된 관측 시각, 실패·누락·partial/truncated 없음, 마지막 LOW 상태를 요구한다.
가짜 0점 Event 없이 빈 events.jsonl과 검증된 요약만 내보낸다. analyzer의 모듈 분류를
위해 이 경우 출력 상위 폴더 이름은 `esp`여야 한다. 요약은 producer 진단이며 조작
방지 인증은 아니다. exporter는 raw를 읽지 않으며 선언된 raw 건수의 일관성만 확인한다.
새 실제 수집은 `client/detectors/esp/scripts/collect_replay.ps1 -ExportReplay`로
준비 조건·실제 raw 건수·대상 PID까지 검증할 수 있다. 실제 플레이는 별도로 확인해야 한다.
