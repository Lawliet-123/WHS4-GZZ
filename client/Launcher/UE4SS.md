# 런처의 UE4SS 설치 — 요구사항과 미해결 문제

담당: **동효님** (`game_launcher.py`). 이 문서는 은지님(2026-09-29)과 성민님(2026-09-29)이
보내 주신 요구사항을 합치고, 우리 레포 상태와 대조해 충돌을 정리한 것이다.

## 왜 런처가 설치해야 하나

탐지기 네 개가 UE4SS 위에서 돈다(2026-10-01 기준).

| 기능 | 필요한 Lua 모드 | 레포의 모드 소스 | 모드가 남기는 기록 | 담당 |
|---|---|---|---|---|
| 에임봇 탐지 | `DamageLogger` | `client/ue4ss/Mods/DamageLogger` | 자기 폴더 `meccha_aim_telemetry.jsonl` | 은지 |
| 오토페인트 행동 탐지 | `GZZPaintObserver` | `client/ue4ss/Mods/GZZPaintObserver` | 자기 폴더 `session.control` 등 | 성민 |
| 노클립 탐지 | `NoclipLogger` | `client/ue4ss/Mods/NoclipLogger` | 자기 폴더 `noclip_log.csv` | 송희 |
| 갓모드 탐지 | `GodModeTelemetry` | `client/detectors/godmode/telemetry_mod/GodModeTelemetry` | `%LOCALAPPDATA%\MECCHA-GZZ-godmode-telemetry.jsonl` | 재민 |

`modules/godmode/Mods/GodMode`, `modules/noclip` 은 **핵 PoC** 다. 설치 대상이 아니다.

**UE4SS 본체는 런처가 한 번만 설치하고, 각 기능은 자기 모드 폴더만 추가한다**(성민님).
기능마다 UE4SS 를 따로 설치하면 서로 다른 빌드의 `dwmapi.dll` 과 `UE4SS.dll` 이 섞인다.

## 배치

```
<게임>/Chameleon/Binaries/Win64/
├─ PenguinHotel-Win64-Shipping.exe
├─ dwmapi.dll                         ← UE4SS 사이드로딩용 프록시
└─ ue4ss/
   ├─ UE4SS.dll
   ├─ UE4SS-settings.ini
   ├─ UE4SS_Signatures/StaticConstructObject.lua    (선택: 별도 검증 파일을 제공할 때만)
   └─ Mods/
      ├─ mods.txt                     ← 아래 네 모드를 : 1 로
      ├─ shared/UEHelpers/UEHelpers.lua            (성민: observer.lua 가 씀)
      ├─ DamageLogger/Scripts/main.lua
      ├─ GZZPaintObserver/Scripts/{main.lua, observer.lua}
      ├─ NoclipLogger/Scripts/main.lua
      └─ GodModeTelemetry/Scripts/main.lua
```

## 먼저 풀어야 할 것 — 우리 안티치트가 이 설치를 핵으로 잡는다

> **해결됨 (9/29). 동효님이 하실 일이 한 줄 생겼습니다.**
>
> 탐지기 쪽은 `client/Launcher/logs/ue4ss_install.json` 을 보고 **해시가 맞는 파일만**
> 점수에서 뺍니다. 그 파일은 런처가 설치하면서 남겨야 합니다. 설치가 끝난 직후
> 한 번 부르시면 됩니다 — 해시는 안에서 뜹니다.
>
> ```python
> import ue4ss_manifest
> ue4ss_manifest.record(game_root, installed_paths,
>                       bundle={"name": "UE4SS", "version": "...", "sha256": "..."},
>                       mods=["DamageLogger", "GZZPaintObserver",
>                             "NoclipLogger", "GodModeTelemetry"],
>                       out_path=ue4ss_manifest.DEFAULT_PATH)
> ```
>
> **설치한 모드는 전부 `mods` 와 `installed_paths` 에 넣어야 한다.** 빠진 모드는
> 우리 것이어도 `third_party_lua_mod`(45점)로 그대로 잡힌다. 모드 폴더 안에 실행 중
> 생기는 기록 파일(`noclip_log.csv` 등)은 넣지 않아도 된다 — 등록한 파일만 대조한다.
>
> 형식과 이유는 `client/Launcher/ue4ss_manifest.py` 독스트링에 있습니다.
> **이 파일을 안 남기면 예전처럼 DETECTED 100 이 납니다** — 조용히 통과시키지
> 않습니다. 반대로 등록부에 없는 프록시 DLL 이나 모드는 그대로 잡힙니다.
> 런처는 탐지기와 같은 고정 경로에 기록합니다. 옛 `GZZ_UE4SS_MANIFEST` 값으로
> 기록 위치를 바꾸지 않습니다.

**런처가 위 파일을 깔면 `filesystem` 탐지기(2번, 랑언)가 그 PC 를 DETECTED 100 으로 잡는다.**

| 규칙 | 점수 | 무엇에 걸리나 |
|---|---|---|
| `proxy_dll_in_game_dir` | 45 | `dwmapi.dll`. 꺼둔 `dwmapi.dll.off` 는 30 이고, **둘 다 있으면 75** (매칭마다 더한다) |
| `ue4ss_runtime` | 50 | `ue4ss/` 폴더와 `UE4SS-settings.ini` |
| `third_party_lua_mod` | 45 | `mods.txt` 의 기본 동봉 외 활성 모드. **모드가 1개든 5개든 45 고정** |

(`client/LocalGuard/memory_integrity/detectors/filesystem.py:103-149`.
합계가 100 을 넘어 상한 100 으로 잘린다. 랑언 PC 는 `dwmapi.dll` 과 `.off` 가 같이
있어서 이 규칙 하나로 이미 75 다.)

9/27 에 나온 "안티치트가 자기 자신을 신고한다" 의 세 번째 사례다. 앞의 두 번은 프로세스
핸들과 주입 DLL 이었고, 이번엔 파일이다.

고친 방향은 앞의 두 번과 같다(9/29, 랑언). 이름이나 존재 여부가 아니라 **우리가 설치한
그 파일인지**로 가른다. 설치 기록에 적힌 해시와 디스크의 바이트가 같은 파일만 빼고,
**다른 UE4SS·등록 안 된 DLL·등록 안 된 모드는 그대로 잡는다.** 가짜 게임 폴더로 25항목
확인했다(1바이트만 바꿔도, 옆에 `cheat.dll` 을 얹어도 다시 잡힘).

**설치 코드가 기록을 남기기 전까지는 정상 세션이 여전히 DETECTED 100 으로 나온다.**
그래서 2번 정상 세션 수집이 이 설치 코드를 기다리고 있다.

## 버전 고정

> **정했다 (2026-09-30).** 팀 UE4SS 는 성민님이 디스코드에 올린
> **`UE4SS_v3.0.1-1136-g35d1795d.zip`** 이다. 게임 안 `UE4SS.log` 에는
> **`v3.0.1 Beta #0 - Git SHA #f6d5f942`** 로 찍힌다(은지님 설치본과 같다).

**zip 이름의 SHA(`35d1795d`)와 로그의 SHA(`f6d5f942`)가 다른 게 정상이다.**
zip 이름은 배포 시점의 커밋이고, 로그는 DLL 을 빌드한 커밋이다. `35d1795d` 는 다른
게임(The Pathless) 설정 파일만 추가한 커밋이라, UE4SS 배포 과정이 DLL 을 다시 빌드하지
않고 `f6d5f942` 빌드를 그대로 썼다(RE-UE4SS `cmake-experimental.yml` "Only asset
changes detected - reusing last build"). **버전은 zip 이름이 아니라 로그 첫 줄로 확인한다.**

| 어디 | 버전 | 할 일 |
|---|---|---|
| 팀 기준(성민님 zip) | 로그 `f6d5f942` | — |
| 은지님 설치본 | 로그 `f6d5f942` | 없음 |
| 랑언 PC 설치본 | 로그 `24b12662` → 팀 zip 으로 교체함(10/1) | 없음 |

- zip SHA-256 (8,717,962 바이트):
  `050948bdf6b4aae2ff8d834aaebadbf7535d4cb8478fbb579966a5ca3142f86a`
- 내용 확인(10/1, 실행하지 않고): 41항목, DLL 은 `dwmapi.dll` 과 `ue4ss/UE4SS.dll` 둘뿐이고
  나머지는 UE4SS 표준 구성(기본 모드·`Mods/shared/UEHelpers`·설정·`UE4SS_SDK_Backends`)이다.
  `UE4SS.dll` 안에 `f6d5f942` 문자열이 들어 있어 위 "로그 SHA" 설명과 맞는다.
  `UE4SS-settings.ini` 는 기본값(`ConsoleEnabled = 0`)이다.
- 묶음은 지금 **디스코드로 배포**한다. 레포에 커밋할지는 아직 안 정했다(아래).

레포에는 네 모드의 Lua 소스가 있다(위 표). UE4SS 런타임(`UE4SS.dll`, `dwmapi.dll`)과
`UEHelpers`는 레포에 없고, 해시를 확인한 팀 ZIP에서 설치한다. 별도 게임 전용
`StaticConstructObject.lua`는 공유받지 않았으며, 이번에 검증한 ZIP에도 없었다.
기본 설치에는 요구하지 않고 별도 파일이 제공될 때만 고정 SHA-256을 확인한다.

## 설치 절차

설치 준비 코드는 `game_launcher.py`에 있다. 팀 ZIP의 SHA-256은
`PINNED_UE4SS_ZIP_SHA256`에 고정했다. 압축 해제 폴더는
`PINNED_UE4SS_DIRECTORY_SHA256`이 아직 비어 있으므로 기본 설치에는 사용하지 않는다.
`GZZ_UE4SS_BUNDLE`로 ZIP 위치를 지정한다. 별도 시그니처가 실제로 제공될 때만
`GZZ_UE4SS_SIGNATURE`와 `PINNED_SIGNATURE_SHA256`을 사용한다. 함수는
`READY`(파일 준비), `MISSING`, `CONFLICT`, `GAME_RUNNING`, `ERROR`를 구분한다.
`READY`도 게임 안에서 로드됐다는 뜻은 아니다. 새 게임 실행 뒤
`verify_ue4ss_log(game_root, session_id, started_after)`로 다시 확인해야 한다.
`main.py`는 UE4SS 의존 탐지기를 선택했을 때 게임 실행 전에 파일을 준비하고,
게임·탐지기 시작 후 새 `UE4SS.log`에서 네 모드의 로드를 확인한다. AutoPaint의
현재 세션 기록은 탐지기가 `session.control`을 만든 뒤 확인한다. 이미 실행 중인
게임의 파일은 변경하지 않는다. ZIP 누락·충돌 시에도 비의존 탐지기는 계속
실행하지만 UI에 UE4SS 탐지 범위가 검증되지 않았음을 표시한다.

### 1. 게임 위치 찾기
Steam 설치 위치를 찾고, 못 찾으면 **사용자에게 한 번 물어서 저장**한다.
`C:\Program Files (x86)\...` 를 모든 PC 에 고정하면 안 된다(은지·성민님 둘 다).

> **해결됨 (9/29).** 처음에 여기 "`GAME_DIR` 은 찾기에 실패했을 때만 쓰는 값이라 당장
> 깨지진 않는다" 고 적었는데 **틀렸다.** 코드는 그 고정 경로를 *먼저* 썼고, 경로를
> 탐색하는 코드는 아예 없었다. 다른 PC 에서 바로 문제가 되는 값이었다.
>
> 지금은 `game_launcher.find_game_root()` / `find_game_dir()` 가 찾는다.
> 환경변수 `GZZ_GAME_DIR` → 떠 있는 게임 프로세스 → 스팀 라이브러리
> (레지스트리 + `libraryfolders.vdf` + `appmanifest_4704690.acf`) → `GAME_DIR` 순서다.
> 찾은 값은 자식 모듈에 환경변수로 내려간다. **두 층을 따로 준다** — 층을 한 이름으로
> 부르면 받는 쪽마다 다른 걸 가리킨다.
>
>     GZZ_GAME_ROOT   ...\MECCHA CHAMELEON                  filesystem 이 훑는 층
>     GZZ_GAME_BIN    ...\Chameleon\Binaries\Win64          exe·UE4SS 가 있는 층
>
> 동효님이 `game_launcher.py` 를 다시 쓰시면 이 두 함수만 남겨 주시면 됩니다.

게임 버전도 확인한다. **현재 테스트 기준은 4.0.2** 다. 다른 버전에서 모은 로그와 섞이면 안 된다.

### 2. 최초 설치
팀이 고정한 묶음을 위 배치대로 놓는다.

### 3. 모드 활성화 (`mods.txt`)
기존 줄을 **보존**하고 아래를 추가한다. 이미 `: 0` 이면 `: 1` 로 바꾸고, 중복 줄은 만들지 않는다.
```
DamageLogger : 1
GZZPaintObserver : 1
NoclipLogger : 1
GodModeTelemetry : 1
```
다른 팀 모드도 지우지 않는다.
단, 새 설치에서 기존 `mods.txt`가 없다면 배포 묶음의 기본 목록은 가져오지 않고
위 팀 관측 모드 네 개만 만든다. 받은 묶음에는 `CheatManagerEnablerMod : 1`처럼
안티치트 런처가 자동 활성화하면 안 되는 기본 항목이 있기 때문이다.

> 랑언 PC 의 `mods.txt` 에는 `GodMode : 1` 같은 **치트 모드가 들어 있다.** 실제로 만나는
> 상황이니 참고. 측정용이라 지우지는 않지만, **정상 세션을 찍을 때는 반드시 `: 0`** 이어야
> 한다. 켜 둔 채로 찍으면 정상 세션이 핵 세션이 된다.

### 4. 매 실행 전 검사
아래가 있고 팀 버전과 같은지 본다. 정상이면 **다시 복사하지 않는다.**
```
dwmapi.dll
ue4ss/UE4SS.dll
ue4ss/Mods/shared/UEHelpers/UEHelpers.lua
ue4ss/Mods/DamageLogger/Scripts/main.lua
ue4ss/Mods/GZZPaintObserver/Scripts/{main.lua, observer.lua}
ue4ss/Mods/NoclipLogger/Scripts/main.lua
ue4ss/Mods/GodModeTelemetry/Scripts/main.lua
```
`mods.txt` 는 파일 전체 해시가 아니라 **해당 항목이 `: 1` 인지**로 본다(다른 모드가 섞이므로).

**다른 버전의 `dwmapi.dll` 이나 UE4SS 가 이미 있으면 덮어쓰지 말고 설치 충돌로 표시한다.**
해시를 비교해서 판단한다.

> 랑언 PC 에는 `dwmapi.dll.off` 가 있다(UE4SS 를 껐다 켰다 하려고 이름을 바꾼 것).
> 이런 변형도 충돌 후보로 봐야 한다.

### 5. 실행 후 확인
**파일이 있다는 것만으로 성공 처리하면 안 된다.** 게임을 켠 뒤 새 `ue4ss/UE4SS.log` 에서 확인한다.
```
UE4SS 버전과 Git SHA
PS scan successful
[GZZPaintObserver] loaded; waiting for Python session control
[GZZPaintObserver] session=<현재 세션 ID>
```
그다음 AutoPaint detector 쪽에서 `Lua=RECEIVING`, `paint_collector_receiving=1`, hook 등록 상태까지 본다.
`F10`/`~` 콘솔이 열리는지는 성공 기준이 아니다(성민님).

실패(`Lua=MISSING`, `Lua=STALE`, hook 등록 실패)하면 **행동 점수 0 을 정상으로 처리하지 않고**
"행동 탐지 불가" 로 표시해 런처·Dashboard 에 올린다.

### 6. 탐지기에 경로 넘기기
AutoPaint detector 실행 때 실제로 찾은 경로를 준다. 개발자 고정 경로를 넣으면 안 된다.
```
--lua-mod-dir "<게임 Win64>\ue4ss\Mods\GZZPaintObserver"
```
detector 가 `GZZPaintObserver/session.control` 을 만들고 고칠 수 있어야 한다. **쓰기 권한이
없으면 실행 전에 오류를 낸다.** 런처 전체를 관리자 권한으로 올리는 것을 기본 해결책으로 쓰지 않는다.

### 7. 배포 묶음에서 뺄 것
```
UE4SS.log,  충돌 덤프,  Object dump/임시 dump
GZZPaintObserver/session.control   (실행 중 계속 바뀌는 런타임 파일)
paint_calls.jsonl,  기존 세션 로그,  main.lua.backup-*
```
`session.control` 은 **자체 무결성 검사 대상에도 넣지 않는다**(4번 SelfDefense 에도 해당).

### 8. 종료 순서
탐지기를 **먼저** 정상 종료하고, manifest 와 `session.control` 정리가 끝난 뒤 게임을 끈다.
강제 종료하면 manifest 가 `RUNNING` 으로 남는다.

> **해결됨 (9/30).** 예전 `stop_all()` 은 `terminate()`(= `TerminateProcess`)로 끝내서
> 자식의 정리 코드가 한 줄도 안 돌았다. 기다리는 시간을 두긴 했지만 이미 죽인 뒤였다.
>
> 이제 종료를 **요청**(Ctrl+Break)하고 기다린 뒤, 남은 것만 강제로 끈다. 게임 관련
> 모듈이 먼저, SelfDefense·KernelWatcher 가 나중이다. 런처는 게임을 끄지 않으므로
> "탐지기 먼저, 게임 나중" 은 런처가 끝낼 때 항상 지켜진다.
>
> **탐지기 쪽에 한 줄이 필요합니다** — `signal.signal(signal.SIGBREAK,
> signal.default_int_handler)`. 없으면 예전처럼 즉시 끝납니다. 자세한 건
> `README.md` 의 "끌 때 정리 코드가 돌게 하려면".

## 런처가 남길 것 — 설치 기록

> **정해졌다 (9/29).** `ue4ss_manifest.record()` 가 `client/Launcher/logs/ue4ss_install.json`
> 을 쓴다. 파일별 SHA-256 과 모드 이름이 들어가고, 형식은 `ue4ss_manifest.py` 독스트링에
> 있다. 위 "먼저 풀어야 할 것" 의 예시대로 설치 직후 한 번 부르면 된다.
> `game_launcher.prepare_ue4ss()`는 설치 후 이 호출을 한다. Launcher
> `main.py`는 게임을 새로 켜기 전에 준비 함수를 부르고 상태를 UI에 전달한다.

처음 제안한 `game_version` · `log_check`(5번 실행 후 확인 결과)는 지금 기록에 없다.
실행 후 확인을 붙일 때 같이 넣을지 정하면 된다.

## 아직 안 정해진 것

1. 팀 ZIP을 최종 배포본에 어떻게 포함·전달할지 (현재 디스코드 공유)
2. 기존 게임 폴더에 다른 버전이 있을 때의 사용자 안내와 재설치 절차
3. UE4SS가 검증되지 않을 때 각 의존 탐지기를 건너뛸지, 제한 모드로 실행할지
   모듈별 정책과 중앙 판정 표시 방법
4. 다른 PC와 중앙 서버를 포함한 실제 정상·탐지 세션의 종단 검증

정해진 것(10/1 정리): 버전 — 성민님 zip(로그 `f6d5f942`). 모드 소스 위치 — 위 표의 레포
경로. `UEHelpers` — UE4SS 배포본에 들어 있는 것을 쓴다.
