# 런처의 UE4SS 설치 — 요구사항과 미해결 문제

담당: **동효님** (`game_launcher.py`). 이 문서는 은지님(2026-09-29)과 성민님(2026-09-29)이
보내 주신 요구사항을 합치고, 우리 레포 상태와 대조해 충돌을 정리한 것이다.

## 왜 런처가 설치해야 하나

탐지기 두 개가 UE4SS 위에서 돈다.

| 기능 | 필요한 Lua 모드 | 담당 |
|---|---|---|
| 에임봇 탐지 (`client/detectors/aimbot`) | `DamageLogger` | 은지 |
| 오토페인트 행동 탐지 | `GZZPaintObserver` | 성민 |

**UE4SS 본체는 런처가 한 번만 설치하고, 각 기능은 자기 모드 폴더만 추가한다**(성민님).
두 기능이 각자 UE4SS 를 설치하면 서로 다른 빌드의 `dwmapi.dll` 과 `UE4SS.dll` 이 섞인다.

## 배치

```
<게임>/Chameleon/Binaries/Win64/
├─ PenguinHotel-Win64-Shipping.exe
├─ dwmapi.dll                         ← UE4SS 사이드로딩용 프록시
└─ ue4ss/
   ├─ UE4SS.dll
   ├─ UE4SS-settings.ini
   ├─ UE4SS_Signatures/StaticConstructObject.lua    (은지: 이 게임 전용)
   └─ Mods/
      ├─ mods.txt                     ← DamageLogger : 1 / GZZPaintObserver : 1
      ├─ shared/UEHelpers/UEHelpers.lua            (성민: observer.lua 가 씀)
      ├─ DamageLogger/Scripts/main.lua
      └─ GZZPaintObserver/Scripts/{main.lua, observer.lua}
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
>                       mods=["DamageLogger", "GZZPaintObserver"])
> ```
>
> 형식과 이유는 `client/Launcher/ue4ss_manifest.py` 독스트링에 있습니다.
> **이 파일을 안 남기면 예전처럼 DETECTED 100 이 납니다** — 조용히 통과시키지
> 않습니다. 반대로 등록부에 없는 프록시 DLL 이나 모드는 그대로 잡힙니다.

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

**고치는 쪽은 랑언이다. 동효님이 신경 쓸 일은 아니지만, 고치기 전에는 정상 세션이
전부 DETECTED 로 나오므로 이 순서를 알고 계셔야 한다.**

고치는 방향은 앞의 두 번과 같다. 이름이나 존재 여부가 아니라 **우리가 설치한 그 묶음인지**
로 가른다. 즉 팀이 고정한 UE4SS 묶음의 해시와 일치하고 `mods.txt` 활성 모드가
`DamageLogger`, `GZZPaintObserver` 뿐일 때만 뺀다. **다른 UE4SS 나 다른 모드는 그대로 잡는다.**
그래서 런처가 어떤 해시를 깔았는지 알려 줘야 한다 — 아래 "런처가 남길 것" 참고.

## 버전 고정

**어떤 UE4SS 묶음을 쓸지는 랑언이 정한다(2026-09-29 결정).** 정해지면 이 문서에 해시와 함께 적는다.

지금 확인된 것:

| 어디 | 버전 |
|---|---|
| 랑언 PC 설치본 | `v3.0.1 Beta #0 - Git SHA #24b12662` |
| 성민님이 동작 확인한 것 | `v3.0.1 Beta #0 - Git SHA #f6d5f942` |

**이름은 같은 v3.0.1 Beta #0 인데 Git SHA 가 다르다.** 성민님이 경고한 상황이 실제로
일어나 있다. 둘 중 하나로 정하고 팀 전체가 같은 묶음을 쓴다.

레포에는 아직 **`client/ue4ss/Mods/DamageLogger/Scripts/main.lua` 하나뿐이다.**
UE4SS 런타임, `UE4SS_Signatures`, `UEHelpers`, `GZZPaintObserver` 는 없다.
묶음을 어디에 둘지(레포에 커밋할지, 별도 배포로 받을지)도 정해야 한다.

## 설치 절차

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
```
다른 팀 모드도 지우지 않는다.

> 랑언 PC 의 `mods.txt` 에는 `GodMode : 1` 같은 **치트 모드가 들어 있다.** 실제로 만나는
> 상황이니 참고. 이건 지우면 안 되고(측정용), 우리 탐지기가 잡아야 할 대상이다.

### 4. 매 실행 전 검사
아래가 있고 팀 버전과 같은지 본다. 정상이면 **다시 복사하지 않는다.**
```
dwmapi.dll
ue4ss/UE4SS.dll
ue4ss/Mods/shared/UEHelpers/UEHelpers.lua
ue4ss/Mods/DamageLogger/Scripts/main.lua
ue4ss/Mods/GZZPaintObserver/Scripts/{main.lua, observer.lua}
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

## 런처가 남길 것 (랑언 요청)

`filesystem` 탐지기가 "우리가 깐 UE4SS" 를 가려내려면 근거가 필요하다.
설치·검사 결과를 `client/Launcher/logs/ue4ss_install.json` 같은 파일로 남겨 주면 좋겠다.

```json
{
  "game_dir": "...", "game_version": "4.0.2",
  "ue4ss_sha": "...", "installed_by": "launcher",
  "mods": {"DamageLogger": "<해시>", "GZZPaintObserver": "<해시>"},
  "verified_at": "...", "log_check": "ok"
}
```
형식은 정해진 게 아니니 편한 대로 바꾸셔도 된다. 필요한 건 **"이 묶음은 런처가 깐 것"**
이라는 사실과 해시다.

## 아직 안 정해진 것

1. UE4SS 묶음 버전 — **랑언이 정함.** 어느 SHA 로 할지, 묶음을 레포에 넣을지
2. `UE4SS_Signatures/StaticConstructObject.lua` 를 누가 주는지 (은지님 것으로 보임)
3. `GZZPaintObserver` 와 `UEHelpers` 를 레포 어디에 둘지 (지금 없음)
4. 설치 충돌이 났을 때 런처 동작 — 멈출지, 그 모듈만 끄고 갈지
5. 게임 폴더 쓰기 권한이 없을 때 (Steam 폴더는 보통 관리자 권한이 필요할 수 있음)
