# AutoPaint 행동 탐지 0.3.0 사용 안내

이 문서는 0.3.0의 판정·실험 안내입니다. 현재 실행은 [0.4.1 통합 안내](Integration-0.4.1.md)를 따르세요.
서버 없이 기존 명령을 쓸 때는 `--telemetry off`를 추가합니다. 행동 판정 규칙은 동일합니다.

## 변경 내용

Lua는 기존처럼 관찰 기록을 저장하고 Python이 실행 중에 새 기록을 읽어 행동 점수를 계산한다.
Lua 내부에 판정 규칙을 추가하거나 게임 함수의 인자·반환값을 변경하지 않았다.
게임 서버, AutoPaint 원본, 커널, SelfDefense는 변경하지 않았다.

게임에 설치되어 정상 작동하던 GZZPaintObserver 0.2.0은 그대로 사용할 수 있다.
이번에는 Python이 있는 AntiCheat 폴더를 새 버전으로 사용하면 된다.
실행 중인 세션은 먼저 Ctrl+C로 종료한다. 기존 로그는 남겨 두고 새 폴더·세션 ID로 테스트한다.

## 실행 방법

1. 새 AntiCheat 폴더를 데스크톱으로 옮긴다. `gzz_anticheat` 폴더와 `README.md`가 보이는 위치다.
2. 탐색기에서 이 폴더를 연 뒤 주소창에 `cmd`를 입력하고 Enter를 누른다.
3. 기존 Lua 모드가 설치된 게임을 실행한다. 메뉴에서 Python을 먼저 시작해도 된다.
4. 아래 명령을 실행한다. `--lua-mod-dir`만 실제 게임 쪽 모드 경로로 바꾼다.

```bat
py -m gzz_anticheat --session-id normal_host_behavior_001 --player-id player_042 --label NORMAL --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

방 입장 직후에는 메뉴·대상 식별·관측 구간 확보 때문에 INCOMPLETE 또는 WARMING_UP이 나올 수 있다.
방 입장 후 몇 초가 지나 `BehaviorState=READY`가 되는지 확인한다.
`Lua=RECEIVING`만으로 행동 판정 준비가 완료된 것은 아니다.

정상 페인트 모드 진입, 일반 칠하기, 모드 해제를 진행한다.
마지막 조작 뒤 3초 정도 기다리고 Python을 Ctrl+C로 종료한다.

AutoPaint 테스트는 게임을 완전히 종료하고 다시 켠 뒤 별도 세션으로 진행한다.
허가된 기존 연구 환경만 사용하며 이 안내는 보안 제품 해제나 주입 실패 우회 절차를 포함하지 않는다.

```bat
py -m gzz_anticheat --session-id autopaint_host_behavior_001 --player-id player_042 --label CHEAT --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

다른 CMD 창도 같은 AntiCheat 폴더에서 열고, 기존처럼 단계마다 표시한다.

```bat
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state IN_ROOM
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state INJECTED
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state ON
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state PAINT_DONE
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state OFF
py -m gzz_anticheat.mark --session-id autopaint_host_behavior_001 --state BRIDGE_STOP
```

이 명령을 한꺼번에 실행하지 않는다. 해당 동작 직전·직후에 각각 표시한다.
단계 표시를 빠뜨려도 탐지 규칙 자체에는 영향을 주지 않는다. 시간 정답만 미확정으로 남는다.
같은 ID는 다시 사용할 수 없으므로 두 번째 테스트는 `_002`처럼 바꾼다.
비호스트는 세션 이름의 host를 client로 바꾸고 `--network-role client`를 사용한다.
네트워크 역할과 NORMAL/CHEAT 라벨은 판정을 바꾸는 스위치가 아니다.

## 화면에서 확인할 내용

예시 표시이며 실제 세션 시각과 점수는 달라질 수 있다.

```text
[gzz] DETECTED score=32 t=135000ms Integrity=32 Behavior=12 BehaviorState=READY
```

- `Integrity`: DLL·런타임 점수. 10점 이상이면 해당 채널에서 탐지.
- `Behavior`: 현재 행동 구간 점수. READY이고 10점 이상이면 행동 탐지.
- `score`: 두 채널의 유효 점수 중 큰 값. 합산하지 않음.
- `BehaviorState`: 수집·판정 상태. READY 이외 상태의 행동 0점은 정상 판정이 아님.

AutoPaint 중지 후 약 2초의 관측 구간이 지나면 행동 점수는 내려갈 수 있다.
DLL이 남아 있으면 Integrity 점수는 유지될 수 있다.
이미 탐지한 이력은 `manifest.json`의 `behavior_summary.ever_detected`와 최초 탐지 시각에 남는다.
탐지와 강제 차단은 다르며, 현재는 기록·표시만 한다.

## 규칙 v1

평가 시각보다 200ms 이전까지의 최근 2초를 본다. 화면 호출이 약간 뒤에 기록되는 경우를 확인할 여유를 둔다.
기본 평가 주기는 약 1초다. 엄격한 실시간 보장이나 실게임 지연 측정값은 아니다.

먼저 본인 캐릭터·같은 World·같은 페인트 컴포넌트의 기록을 분리한다.
다른 캐릭터의 반영 호출은 점수에서 제외하고, 대상 식별이 불확실한 관련 호출은 판단 보류로 처리한다.

| 조건 | 가산 |
|---|---:|
| 같은 본인 컴포넌트의 PaintAtUVWithBrush가 2초 구간에 8회 이상 | 6 |
| 위 조건이 있고, 전후 150ms 이내 같은 컴포넌트의 PaintAtScreenPosition으로 대응되지 않는 UV 호출이 8회 이상 | 4 |
| 위 반복 UV 조건이 있고, 페인트 모드 밖 UV 호출이 8회 이상 | 3 |
| 위 반복 UV 조건이 있고, 같은 구간의 ServerPaintBatch가 2회 이상이며 스트로크 합이 16 이상 | 2 |

각 조건은 구간당 한 번만 가산한다. 총 10점 이상이면 행동 탐지다.
8회·2초·150ms와 가중치는 현재 연구 대상의 초기 규칙값이며 통계적으로 확정된 경계가 아니다.
RPC가 없어도 첫 두 조건으로 10점에 도달한다. F를 켜도 UV 직접 호출과 화면 호출의 불일치를 평가한다.
BeginStroke/EndStroke 상태는 추적하지만 BeginStroke 한 번으로 이후 모든 UV 호출을 정상 처리하지 않는다.
IA 입력 함수, IsBrushing=false, Multicast 호출량만으로 점수를 주지 않는다.

필수 관찰 함수는 PaintAtUVWithBrush, PaintAtScreenPosition, BeginStroke, EndStroke다.
이 네 함수의 등록과 본인 Pawn 식별이 확인된 뒤 약 2.35초의 연속 관측 구간을 확보한다.
누락 순번, 파일 파싱 오류, 드롭·쓰기·콜백 오류 증가, 수집기 재시작, Pawn 변경, 3초 이상 상태 미수신은 기존 구간을 초기화한다.
새로운 정상 health와 연속 관측 구간을 확보한 뒤 재개한다. 시계 정렬이 잘못되면 행동 판정을 보류한다.
UTC와 단조 시계 사이의 미세한 오차를 위해 미래 시각에는 10ms까지만 여유를 둔다. 그보다 앞선 기록은 보류한다.

## 로그

세션 폴더 구조와 공통 Event 최상위 7개 필드는 유지한다.
점수를 계산한 매 시점의 Event를 저장하며, 0점도 저장한다.
둘 다 평가할 수 없으면 Event에 가짜 0점을 쓰지 않고 raw에 실패 상태만 남긴다.

`events.jsonl`의 `evidence`에서 다음 값을 확인한다.

- `integrity_score`, `integrity_valid`, `integrity_detected`.
- `behavior_score`, `behavior_valid`, `behavior_detected`.
- `behavioral_scoring_enabled`, `behavior_rule_version`.
- `behavior_uv_calls`, `behavior_unmatched_uv_calls`, `behavior_mode_off_uv_calls`.
- `behavior_batch_calls`, `behavior_batch_strokes`, `behavior_uv_during_stroke`.

행동 판단의 대상, 관측 구간, 상태와 보류 이유는 `raw/integrity.jsonl`의 `behavior_assessment`에 저장한다.
기존 `raw/paint_calls.jsonl`은 계속 원본 호출 기록으로 남는다.
`timestamp_ms`는 모두 세션 시작 후 경과시간이다.

## 기존 로그를 게임 없이 재검증

AntiCheat 폴더의 CMD에서 실행한다. 원본 ZIP은 수정하지 않는다.

```bat
py -m gzz_anticheat.behavior_replay "C:\Users\N519\Downloads\autopaint_host_modeon_002.zip"
```

게임 실행이나 AutoPaint 재실행 없이 행동 점수만 계산한다. DLL 점수와 정답 라벨은 규칙 입력으로 사용하지 않는다.
원본 로그로 이미 규칙을 설계했으므로 이 결과는 개발 표본 재생 검증이며 독립적인 정확도 평가가 아니다.

결과도 저장하려면 새 출력 폴더를 지정한다.

```bat
py -m gzz_anticheat.behavior_replay "C:\Users\N519\Downloads\autopaint_host_modeon_002.zip" --output-dir replay_results_v1
```

출력은 세션별 `manifest.json`, `events.jsonl`, `raw/behavior.jsonl`이다.
새 플레이 로그가 아니라 기존 로그에서 파생한 결과임을 manifest에 명시한다.
`raw/behavior.jsonl`은 파생 진단이고, 원본 호출 기록은 manifest의 source 경로에 보존한다.
재생의 계산 불가 구간은 진단에 남고 Event 분포에는 포함되지 않는다.
기존 live `events.jsonl`과 재생 Event를 동일한 새 플레이 표본으로 중복 집계하지 않는다.

## 남은 검증

추가 정상 동작, 브러시·UI 변경, 로딩·재접속, 높은 호출량에서의 오탐·미탐과 성능은 후속 실게임 테스트 대상이다.
정상 화면 호출과 UV 호출이 섞이면 시간 기반 대응이 실제 인과관계를 증명하지 못한다.
또한 낮은 호출량이나 다른 칠하기 경로는 이 규칙의 범위 밖일 수 있다.
비호스트에도 같은 로컬 기준을 적용하지만 비호스트 AutoPaint의 정량 실측 결과를 새로 만들었다고 주장하지 않는다.
수집기와 파일은 같은 PC에서 변조될 수 있다. 이 구현은 SelfDefense나 변조 불가능한 보안 경계를 제공하지 않는다.
