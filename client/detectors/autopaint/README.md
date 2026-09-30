# GZZ AutoPaint Detector — 0.4.1

이 ZIP은 AutoPaint 모듈 하나의 내용입니다. ZIP을 열면 바로 `main.py`가 있으며,
전용 폴더에 압축을 풀고 그 폴더를 팀에서 정한 AutoPaint 모듈 위치에 넣습니다.

```text
AutoPaint 모듈 폴더/
  main.py
  gzz_anticheat/
  README.md
  pyproject.toml
  docs/
  tests/
```

이 폴더에서 `py main.py ...`로 실행합니다. ZIP 안에 추가 detector/autopaint 폴더는 없습니다.
공통 shared와 Lua 모드는 별도 위치에서 관리하며 이 ZIP에 중복 포함하지 않습니다.
Lua 소스는 별도 `GZZPaintObserver-0.2.0-common.zip`으로 제공합니다. 기존 설치본도 그대로 사용할 수 있습니다.

Windows / Meccha Chameleon / 제공된 UE 5.6.1 SDK 기준의 허가된 연구용입니다.
Python DLL·런타임 탐지와 UE4SS Lua 관찰 기록의 실시간 행동 판정을 제공합니다.
게임 서버를 수정하지 않습니다. AutoPaint 원본도 수정하지 않습니다.

현재 구현 범위: 내부 호출 수집, DLL·행동 점수, 로컬 기록, shared를 통한 결과 전송.
0.4.1은 0.4.0의 배포 구조를 모듈 단위로 수정한 버전입니다. 탐지 규칙·점수·7필드 Event와 main.py 실행 함수는 그대로입니다.
이번 검증은 모의 센서와 로컬 수신기 기준입니다. 실제 중앙 receiver의 /api/detection 종단 검증은 서버 완성 후 진행합니다.
수집기 등록 성공은 모든 호출을 관찰한다는 보장이 아니며, 호출 없음만으로 치트 판정을 하지 않습니다.

새 실행·통합 방법은 [0.4.1 연결 안내](docs/Integration-0.4.1.md), 판정 규칙은 [행동 탐지 안내](docs/BehaviorDetector-0.3.0.md)를 참고하세요.
게임에 설치한 Lua 0.2.0은 그대로 사용합니다. Lua 내용은 같고 저장소의 소스 위치만 공통 observers 폴더로 옮겼습니다.

## 실행과 서버 전송

팀 시작점은 `main.py`입니다. 기존 `py -m gzz_anticheat`도 같은 실행 함수를 사용합니다.
기본값 `--telemetry managed`는 시작 시 shared를 한 번 초기화하고 종료 시 flush/shutdown을 호출합니다.
서버 설정이 없거나 잘못되면 세션을 시작하지 않고 오류를 표시합니다.

중앙 서버에 연결할 때는 공통 shared 패키지의 부모 폴더를 Launcher의 PYTHONPATH에 넣고,
PowerShell에서 실제 전달받은 값으로 설정합니다. 경로 설정은 연결 안내에 있습니다.
아래 주소는 예시이며 운영 서버 주소가 아닙니다.

```powershell
$env:GZZ_TELEMETRY_URL = "https://telemetry.example.com"
$env:GZZ_TELEMETRY_TOKEN = "SERVER_ISSUED_TOKEN"
$env:GZZ_TELEMETRY_OUTBOX = Join-Path $PWD "telemetry-outbox/autopaint.sqlite3"
py main.py --session-id normal_host_001 --player-id player_042 --label NORMAL --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

서버 없이 기존 플레이 로그만 수집할 때는 `--telemetry off`를 명시합니다. 아래 수집 예제는 이 모드입니다.
같은 프로세스에서 통합 프로그램이 shared를 먼저 초기화했다면 `--telemetry external`을 사용합니다.
이 경우 AutoPaint는 결과만 전달하고, 모든 detector가 끝난 뒤 통합 프로그램이 flush/shutdown을 담당합니다.

매 평가 결과를 `events.jsonl`에 먼저 기록하고 같은 dict를 `send_detection()`에 전달합니다.
0점·같은 점수도 전송하며 콘솔 표시 간격은 전송 건수에 영향을 주지 않습니다.
전송 대기 등록 실패는 stderr에 `ENQUEUE_FAILED`로 표시하고 로컬 탐지는 계속합니다.
`queued`는 로컬 대기열 등록이며 서버 성공을 뜻하지 않습니다. 종료 때 `flushed=False`면 미확인 전송이 남은 상태입니다.
기존 JSONL을 자동으로 다시 읽어 전송하지는 않습니다. 대기열 등록 실패 결과는 로컬 파일로 별도 확인해야 합니다.

## 가장 먼저 할 일

1. 전용 AutoPaint 폴더에 ZIP을 풉니다. 바로 보이는 `main.py`가 시작점입니다. 해당 폴더에서 터미널을 엽니다.
2. 해당 게임에서 이미 동작하는 UE4SS가 있는지 확인합니다.
   없거나 버전을 모르면 게임 실행 파일 경로, UE4SS 설치 여부/버전부터 확인해야 합니다.
   UE 5.6이라는 이유만으로 모든 UE4SS 배포본이 호환된다고 가정하지 않습니다.
3. 게임을 **종료한 상태**에서 별도 Lua ZIP의 `GZZPaintObserver`를 게임 UE4SS의 `Mods` 안에 복사합니다. 팀 공통 폴더의 같은 소스를 사용해도 됩니다.
   결과는 `Mods/GZZPaintObserver/Scripts/main.lua`, `Scripts/observer.lua`입니다.
4. 기존 `Mods/mods.txt` 내용을 보존하고 다음 줄을 추가합니다.

```text
GZZPaintObserver : 1
```

UE4SS 바이너리/로더는 이 패키지에 포함하지 않습니다. 이 프로그램도 UE4SS를 자동 설치하거나 DLL을 주입하지 않습니다.
연구 수집기를 켠 상태의 정상 로그와 치트 로그를 비교해야 합니다.
한 PC에서 게임 1개, Python 수집 세션 1개만 실행하세요. 테스트 도중 Lua hot reload는 하지 마세요.
설치 해제는 게임 종료 후 해당 mod만 비활성화하면 됩니다. 다른 mod/게임 파일은 변경할 필요가 없습니다.

## 정상 플레이 수집

아래 경로는 예시입니다. 터미널 위치는 `main.py`와 `gzz_anticheat`가 있는 폴더이고,
`--lua-mod-dir`는 **복사해 설치한 게임 쪽 모드 폴더**를 가리켜야 합니다.
팀 저장소의 Lua 소스 폴더가 아니라 게임에 설치한 모드 폴더입니다.

```powershell
py main.py --telemetry off --session-id normal_host_001 --player-id player_042 --label NORMAL --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

1. 게임은 메뉴 상태부터 켜 두어도 됩니다. Python을 시작하고 방을 만듭니다.
2. UE4SS 로그에 `[GZZPaintObserver] session=normal_host_001`이 나오는지 봅니다.
3. Python의 `Lua=RECEIVING`, `hooks=...`를 확인합니다. 메뉴에서는 입력 함수 후킹이 아직 준비되지 않을 수 있습니다.
4. 방에 들어온 시점을 다른 터미널에서 표시합니다.

```powershell
py -m gzz_anticheat.mark --session-id normal_host_001 --state IN_ROOM
```

5. 정상 페인트 모드 진입 → 실제 칠하기 → 해제를 2~3회, 합계 약 30초 수행합니다.
6. 마지막 동작 뒤 2초 정도 기다린 후 **Python을 먼저 Ctrl+C로 종료**, 이후 게임을 종료합니다.
7. 호출 요약을 확인합니다.

```powershell
py -m gzz_anticheat.paint_summary logs/normal_host_001
```

첫 통과 기준: `BeginStroke`, `EndStroke`, `PaintAtScreenPosition`, `PaintAtUVWithBrush`가 등록 가능하고,
본인 Pawn을 식별하며 후킹 오류/드롭/시계 문제가 없어야 합니다. 모든 함수가 호출되어야 하는 것은 아닙니다.
`IA_PaintStart/IA_PaintShot`는 현재 게임에서 등록되지 않아 행동 판정의 필수 조건에서 제외했습니다.
`Lua=RECEIVING`은 파일 수신 상태이지 후킹 전체가 정상이라는 의미는 아닙니다.

## AutoPaint 수집

게임을 완전히 종료 후 재실행하여 이전 테스트의 bridge DLL 영향을 분리합니다.
새 세션 ID를 사용합니다.

```powershell
py main.py --telemetry off --session-id autopaint_host_001 --player-id player_042 --label CHEAT --network-role host --lua-mod-dir "D:\Game\PenguinHotel\Binaries\Win64\ue4ss\Mods\GZZPaintObserver"
```

별도 터미널에서 단계마다 표시합니다. 모두 세션 시작 후 경과 ms로 저장됩니다.

```powershell
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state IN_ROOM
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state INJECTED
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state ON
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state PAINT_DONE
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state OFF
py -m gzz_anticheat.mark --session-id autopaint_host_001 --state BRIDGE_STOP
```

- `INJECTED`: 연결/주입 완료 직후.
- `ON`: Camouflage 실행 버튼을 누르기 직전. 버튼 클릭과 표식의 수동 시간차가 있다는 점을 기록/기억합니다.
- `PAINT_DONE`: 실제 칠하기 완료를 확인했을 때. 즉시 끝났다면 그 사실을 표시합니다.
- `OFF`: 중지 버튼을 누른 시점. **실제 마지막 페인트 호출 시점과 다를 수 있습니다.**
- `BRIDGE_STOP`: 브릿지 종료 직후.

ON/OFF는 사람이 지정한 테스트 구간이지 탐지 결과에서 역산한 정답이 아닙니다.
이미 칠하기가 완료된 뒤 중지를 누른 것은 로그 수집을 무효로 만들지 않습니다.
핵심은 실제 호출 구간을 `paint_calls.jsonl`에서 확인하는 것입니다.
마지막 단계 뒤 2초 정도 기다리고 Python을 Ctrl+C로 종료합니다.

우선 호스트 정상 1회 + 호스트 AutoPaint 1회를 진행합니다.
성공 후 참가자에서도 `--network-role client`와 별도 세션 ID로 같은 조합을 수집합니다.
호스트 자신의 행동과 원격 참가자의 행동은 동일하지 않으므로,
추후 호스트가 다른 참가자를 관찰하는 테스트는 역할을 구분하여 따로 진행합니다.

## 세션 결과물

```text
logs/normal_host_001/
  manifest.json
  events.jsonl
  raw/
    integrity.jsonl
    paint_calls.jsonl
    annotations.jsonl
```

`annotations.jsonl`은 수동 표시를 했을 때 생성됩니다.
Lua가 동작하지 않으면 `paint_calls.jsonl`은 생성되지 않고 Python에 MISSING이 표시됩니다.
빈 정상 호출 로그를 만들어 수집 성공처럼 보이게 하지 않습니다.
기존 flat 형식 로그와 원본 ZIP은 수정하지 않으며 새 세션 폴더를 덮어쓰지 않습니다.

- `manifest.json`: 첨부 noclip 예제의 session_id/player_id/label/cheat_type/cheat_start_ms/cheat_end_ms를 유지하고 진단 정보를 확장했습니다.
  정상은 `NORMAL`, 치트는 `CHEAT`, 모르는 경우 `UNKNOWN`입니다.
  ON/OFF 누락 시 시각은 null이며 추측하지 않습니다.
  여러 ON/OFF 구간은 `cheat_intervals`에 보존하고 단일 시작/종료 필드는 null로 둡니다.
- `events.jsonl`: 합의한 **7개 최상위 필드만** 사용합니다.
  raw_score를 계산한 **매 시점** 기록하며 동일 점수/0점도 빠뜨리지 않습니다.
  게임 미발견·센서 실패처럼 계산 불가능한 시점은 0점으로 위장하지 않고 raw 상태에 남깁니다.
- Lua를 연결한 경우 `raw_score = max(integrity_score, 유효한 behavior_score)`입니다.
  두 점수와 각각의 탐지 여부는 evidence에 분리합니다. 임계값은 각각 10점입니다.
  `behavioral_scoring_enabled: 1`, `behavior_valid: 1`일 때 행동 점수가 유효합니다.
  수집 불량은 행동 정상 0점이 아니라 판단 보류입니다. raw의 `behavior_assessment`에 사유를 기록합니다.
  DLL 검사만 가능한 구간에도 해당 점수는 저장하되, 부족한 관측을 콘솔의 INCOMPLETE로 표시합니다.
  Lua 옵션을 사용하지 않으면 기존 DLL 검사만 수행합니다. 0점은 정상 확정이 아닙니다.
- `--event-heartbeat-ms`는 이제 콘솔 표시 간격에만 적용되며 파일 저장을 줄이지 않습니다.

시각: Python과 수동 표시는 공통 세션의 단조 시계를 사용합니다.
Lua는 같은 PC의 UTC를 세션 시작 UTC와 비교하여 경과 ms를 만듭니다.
Python은 UTC와 단조 시계의 차이가 250ms를 넘으면 `clock_alignment_valid=false`로 표시합니다.
Lua의 고해상도 UTC 호출 실패 시 `clock_resolution_ms=1000`으로 명시합니다.
이런 세션은 정밀 호출 간격/순서 판정에 사용하지 마세요. 시계를 바꾸거나 절전하지 마세요.

## Lua가 관찰하는 것

- `PaintTick` 및 Blueprint 입력 이벤트 함수 등록 시도. 두 IA 항목은 현재 판정에서 제외.
- `BeginStroke`, `EndStroke`, 스트로크 flush/send 요청.
- `PaintAtUVWithBrush`, `PaintAtUV`, `PaintAtScreenPosition`.
- `ServerPaintBatch`, compact/packed 대안, relay, multicast 경로.
- 대상 object/owner, 로컬 Pawn, IsPaintMode/IsBrushing, authority/local role.
- 읽을 수 있는 배치 개수, 첫 스트로크의 UV/브러시/GUID 표본.
- 함수별 등록·존재 여부, 호출/오류 횟수, 큐 드롭, UE4SS 버전.

**대상 물체의 owner는 칠한 플레이어와 같다고 볼 수 없습니다.**
기본 `painter_identity`는 unresolved이며 원격 가해자를 자동 귀속하지 않습니다.
읽기 실패 필드는 생략하거나 read_ok=false로 남깁니다. 0/false로 대체하지 않습니다.

UE4SS 문서에 따라 native `/Script/` 함수는 pre, Blueprint 함수는 post 관찰로 구분합니다.
따라서 입력 이벤트 내부에서 페인트가 호출되면 로그상 페인트가 먼저 나올 수 있습니다.
짧은 시간차만으로 “입력 없이 페인트 발생”을 확정하면 안 됩니다.
RegisterHook은 UFunction 실행 관찰입니다. 패킷 캡처·RPC 송신 증명·전체 C++ 호출 추적이 아닙니다.
클라이언트의 Server RPC가 호스트에서만 실행되면 참가자 로그에서 보이지 않을 수 있습니다.

안전/성능: 원본 인자/반환값을 바꾸지 않고, 페인트/RPC를 직접 실행하지 않습니다.
입력 자동화·후킹 은닉·강제 차단·커널 기능은 없습니다.
목록에 있는 함수만 관찰하고 매 프레임 전체 UObject를 순회하지 않습니다.
등록 재시도/상태 갱신 약 1초, 큐 flush 목표 약 100ms, 큐 8192건 상한입니다.
대형 배치는 전량 복사하지 않고 개수와 첫 스트로크만 읽습니다.
실제 비용은 게임 FPS/호출량/디스크에 따라 달라지므로 실측이 필요합니다.
pcall은 Lua 오류를 기록할 뿐, 호환되지 않는 UE4SS의 native crash까지 막지는 못합니다.

## 점수와 한계

기존 Python 탐지: 현재 bridge 해시/문자열, 런타임 경로, 의심 모듈의 스레드,
게임 loopback endpoint와 sidecar 일치, 컨트롤러/인젝터 실행.
0.2.0부터 **디스크에 남은 DLL 파일 단독 존재는 evidence만 남기고 가산하지 않습니다.**
예전 점수와 직접 비교할 때 버전을 구분하세요.

UE4SS 자체도 주입·후킹 도구라 실험 환경에 영향을 줍니다.
정상과 치트 모두 같은 수집기 버전을 켜고 테스트하세요.
수집기 모듈의 비서명 경고가 있더라도 곧바로 AutoPaint라고 단정하지 않습니다.
이 로그는 같은 클라이언트에서 변조/중단할 수 있는 연구용 관측 자료이며 변조 방지 보장은 없습니다.
커널 드라이버나 SelfDefense는 이번 범위가 아닙니다.

## 문제 발생 시 보내줄 것

- 해당 세션 폴더 전체, 게임 버전, UE4SS 버전, 게임 실행 파일과 설치 모드 경로.
- UE4SS 실행 로그에서 GZZPaintObserver/후킹 오류 부분.
- 호스트/참가자 여부, 페인트 실제 성공 여부, 방 입장/버튼 클릭 대략 시각.
- 아래 요약 출력. 암호/계정 토큰 등 무관한 정보는 제외하세요.

```powershell
py -m gzz_anticheat.paint_summary logs/normal_host_001 logs/autopaint_host_001
```

필수 함수의 후킹 실패, 로컬 대상 식별 실패, 드롭이 있으면 수집 상태부터 확인합니다.
기존 표본에서 만든 규칙이므로 호스트/참가자, UI 진입/종료, 브러시 변경 등의 독립 표본으로 오탐·미탐을 검증해야 합니다.

## 개발 검증

공통 shared 패키지를 PYTHONPATH에서 찾을 수 있는 환경에서 실행합니다.
모듈 전달본에는 Python 테스트 102개가 포함됩니다. Lua 소스·모의 실행 테스트 12개는 공통 관찰기 검증 범위로 분리했고 기존 전체 소스에 남겨 두었습니다.

```powershell
py -m unittest discover -s tests -v
py -m gzz_anticheat.replay logs/normal_host_001/raw/integrity.jsonl
py -m gzz_anticheat.behavior_replay logs/normal_host_001 --output-dir replay_results
```

전체 소스의 Lua 모의 테스트에는 선택적으로 `lupa`의 Lua 5.4 런타임이 필요합니다.
모듈 ZIP에는 이 Lua 테스트를 포함하지 않으며, lupa는 탐지기 실행에 필요하지 않습니다.

```powershell
py -m pip install --only-binary=:all: --target .test-deps lupa
```

테스트 fixture는 **합성 데이터**이며 실제 정상/치트 플레이 제출 로그가 아닙니다.
실게임 후킹과 성능은 데스크톱 테스트에서 검증해야 합니다.
`replay`는 저장된 snapshot의 결합 점수를 재현합니다.
`behavior_replay`는 원본 Lua 호출로 행동 점수를 다시 계산하며 DLL 점수·label·ON/OFF를 판정에 사용하지 않습니다.

## API 근거

- [UE4SS Lua 모드 설치](https://docs.ue4ss.com/guides/creating-a-lua-mod.html)
- [RegisterHook의 native/Blueprint 콜백 구분](https://docs.ue4ss.com/dev/lua-api/global-functions/registerhook.html)
- [게임 스레드 실행](https://docs.ue4ss.com/dev/lua-api/global-functions/executeingamethread.html)
- [UE RPC 실행 위치](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-procedure-calls-in-unreal-engine?application_version=5.6)

대상 함수/필드는 제공된 `SDKDump/5.6.1-0+UE5-Chameleon_main/CppSDK/SDK`의
`PenguinHotel_*`, `BP_FirstPersonCharacter_cLeon_Character_*`, `Engine_*` 파일과
읽기 전용 AutoPaint `runtime/src/bridge.cpp`를 대조했습니다.
