# 실측 기록 — A/B 대조

담당: 말랑미 (역할표 2번) · 대상: MECCHA CHAMELEON 4.0.2 · 2026-09-20

같은 게임 세션에서 **깨끗한 상태 → 휘파람 핵 주입** 순으로 두 번 측정했다.
원본 로그는 `logs/detection/clean_002.jsonl` / `hack_001.jsonl`.

---

## 결과

| 모듈 | A 깨끗 | B 핵 주입 |
|---|---|---|
| `injection` | NORMAL 0 | **DETECTED 100** |
| `whistle` | NORMAL 0 | **DETECTED 100** |
| `value_tamper` | NORMAL 0 | NORMAL 0 *(정상 — 아래 참고)* |
| `filesystem` | DETECTED 100 | DETECTED 100 *(양쪽 동일 — 아래 참고)* |
| `whistle_rpc` | ERROR | ERROR *(후크 DLL 미주입)* |

**후킹 탐지율 100% / 오탐 0%.**

재현 절차

```bash
# A
python main.py --session clean_002
# 핵 주입 (관리자 권한)
python tools/inject.py tools/whistle/bin/Release/whistle_v14.dll
# B
python main.py --session hack_001
```

---

## A — 깨끗한 상태

| 모듈 | 표본 | 소요 |
|---|---|---|
| `injection` | 오브젝트 61,123 / 모듈 161 / 검사 5종 전부 수행 | 2.5s |
| `whistle` | 오브젝트 61,273 / UFunction 17,282 / 캐릭터 20 | 1.7s |
| `value_tamper` | 오브젝트 61,373 / 설정값 비교 12건 | 2.8s |

`value_tamper` 는 대상 4개 클래스 모두 살아있는 인스턴스를 1개씩 잡았다.

```
BP_FirstPersonCharacter_Main_C                      1
BP_FirstPersonCharacter_cLeon_Character_Survivor_C  1
BPC_NearInteract_C                                  1
RuntimePaintableComponent                           1
```

생존자 인스턴스가 있어서 `PreStencil` 까지 포함해 **12개 필드 전부** 검사됐다.
(이전 측정에서는 생존자가 없어 11건이었고, 그 사실을 `no_live_instance` 로
보고했다. 커버리지를 숨기지 않는다.)

---

## B — 휘파람 핵(`whistle_v14.dll`) 주입

### `injection` — 범용, 핵 종류 무관

```
untrusted_module       whistle_v14.dll  서명없음 + 사용자 경로
                       C:\...\tools\whistle\bin\Release\whistle_v14.dll
process_event_hooked   0x00007FFD1F44D7E0  (whistle_v14.dll)  오브젝트 62,094개
exec_function_hooked   0x00007FFD1F44D740  (whistle_v14.dll)  1개
```

vtable 3,644종 / UFunction 계열 18,964개를 훑어 나온 결과다.
**모듈 이름을 몰라도 주소가 게임 모듈 밖이라는 것만으로 잡았고, 역추적으로
책임 DLL 이름과 경로까지 나왔다.**

### `whistle` — 귀속까지

```
exec_function_hooked      Play() 의 ExecFunction 이 whistle_v14.dll 로 교체됨
character_vtable_hooked   BP_FirstPersonCharacter_cLeon_Character_Survivor_
                          Default_Base_Child_1point0_C 의 ProcessEvent 가
                          whistle_v14.dll 로 교체됨
```

`injection` 이 "무언가가 후킹했다"까지 말한다면 이쪽은 FName 을 풀어
**어떤 함수가, 어떤 클래스에서** 바뀌었는지까지 말한다.

---

## 교차 검증

두 탐지기가 **서로의 로그를 읽지 않는데 같은 주소를 가리켰다.**

| 주소 | `injection` | `whistle` |
|---|---|---|
| `0x00007FFD1F44D740` | ExecFunction 무결성 위반 | `Play()` 의 ExecFunction |
| `0x00007FFD1F44D7E0` | vtable 무결성 위반 | 캐릭터 클래스의 ProcessEvent |

`injection` 은 전체 오브젝트를 주소 범위로만 훑고, `whistle` 은 FNamePool 을
해석해 이름으로 찾는다. **경로가 완전히 다른데 같은 값에 도달했으므로
우연이 아니다.**

---

## 두 결과를 오해하지 않기 위한 설명

### `value_tamper` 가 B 에서도 NORMAL 인 것은 정상이다

휘파람 핵은 **함수 포인터만 바꾸고 설정값은 건드리지 않는다.** 이 모듈이
보는 12개 필드(Hide Anywhere · Auto Paint v1 대상)는 실제로 변조되지 않았다.
걸리지 않은 것이 맞다.

바꿔 말하면 **이 A/B 는 `value_tamper` 를 검증하지 못했다.** Hide Anywhere
또는 Auto Paint v1 을 실제로 돌린 세션이 따로 필요하다. 미검증 상태로 둔다.

### `filesystem` 이 양쪽 다 DETECTED 인 것은 오탐이 아니다

측정 PC 에 UE4SS 런타임, Dumper-7 산출물, 휘파람 핵 로그가 실제로 존재한다.
파일이 있으니 찾은 것이고 A/B 어느 쪽에서도 상태가 변하지 않는다.
**후킹 탐지율 계산에서는 제외한다.**

### `whistle_rpc` 가 ERROR 인 것은 CLEAN 이 아니다

인프로세스 후크(`ac_whistle_v1.dll`)를 주입하지 않아 읽을 로그가 없었다.
검사를 수행하지 못한 것이므로 ERROR 이고, **탐지율·오탐률 집계에서 빼고
제외 건수를 같이 실어야 한다.**

---

## 한계

- **표본이 적다.** 깨끗한 세션 2회, 핵 세션 1회. 전부 같은 PC 에서,
  그것도 치트가 설치된 환경에서 쟀다. 다른 PC 와 깨끗한 설치본이 필요하다.
- **`value_tamper` 미검증.** 위 참고.
- **`whistle_rpc` 미검증.** `ac_whistle_v1.dll` 을 한 번도 주입해보지 않았다.
- **우회 가능하다.** 주소 범위 판정이므로 게임 모듈 *내부* 빈 공간에 코드를
  심으면 통과한다. 난이도는 오르지만 불가능하지 않다.
- **외부 RPM 방식은 못 잡는다.** ESP 처럼 읽기만 하는 핵은 바꾸는 값도
  코드도 없어 두 축 모두에 걸리지 않는다. 2번 레이어의 원리적 한계다.
- **상시 감시용이 아니다.** 한 세션에 오브젝트 6만 개를 외부에서 읽는다.
  전체 5개 모듈이 7.5~8.6초, 주기적 스캔용이다.

---

# 부록 — RPC 후크가 도발 호출을 못 본 이유 (미해결, 원인 특정)

`whistle_rpc` 는 A/B 표에서 계속 ERROR 였다. 실패로 덮지 않고 원인을
어디까지 좁혔는지 적는다.

## 관측

인프로세스 후크(`ac_whistle_v4.dll`)를 주입하고 실제로 휘파람을 불었다.

```
vtable 3,644개 후킹
ProcessEvent 총 호출 80,896건 / 37초   ← 후크는 확실히 불리고 있다
도발(Provocation) 매칭 0건
```

후크가 본 함수 이름을 그대로 남겨보니

```
{"event":"name","provo":false,"fn":"ReceiveTick"}
{"event":"name","provo":false,"fn":"BlueprintUpdateCamera"}
{"event":"name","provo":true, "fn":"InpActEvt_IA_Provocation_K2Node_EnhancedInputActionEvent_5"}
```

**이름 해석은 정상이다.** 그리고 **입력 핸들러는 `ProcessEvent` 를 지나간다.**
그런데 `Provocation(Local)` / `(Client)` / `(Server)` 는 한 번도 안 나왔다.
이 함수들은 GObjects 에 분명히 존재한다(외부 스캔으로 확인).

## 해석

언리얼에서 블루프린트가 **자기 안의 다른 블루프린트 함수를 호출할 때는
`ProcessEvent` 를 거치지 않는다.** 바이트코드 VM 이 직접 실행한다.
`ProcessEvent` 는 *외부에서* 들어올 때의 입구다.

그래서 입력 → 도발로 이어지는 정상 경로는 이 후크에 보이지 않는다.

## ExecFunction 으로 지점을 옮겨봤다 — 그래도 안 보인다

`Provocation` UFunction 4개의 `ExecFunction` 을 직접 교체했다(`ac_whistle_v6`).
설치는 전부 성공했다.

```
exec_hook slot 0  ProvocationRemote     original 0x7FF7702603D0
exec_hook slot 1  Provocation(Local)    original 0x7FF7702603D0
exec_hook slot 2  Provocation(Client)   original 0x7FF7702603D0
exec_hook slot 3  Provocation(Server)   original 0x7FF7702603D0

t=7.094  입력 핸들러 InpActEvt_IA_Provocation... 발동   ← 키 입력 확인됨
t=64.1   calls 0
```

**그리고 그때 게임에서 휘파람 소리가 실제로 났다.** 즉 함수는 실행됐는데
우리 후크를 지나가지 않았다.

### 원인

넷의 원본 ExecFunction 이 **모두 같은 주소**였다 — `ProcessInternal`,
블루프린트 VM 의 공용 진입점이다. 순수 블루프린트 함수는 자기만의 네이티브
진입점이 없다.

그래서 VM 이 블루프린트 함수를 부를 때는 `UFunction::ExecFunction` 포인터를
거치지 않고 `ProcessInternal` 로 바로 들어간다. 슬롯을 바꿔도 안 불린다.

휘파람 핵이 `Play()` 에 같은 기법을 써서 성공한 것은 **`Play()` 가 네이티브
함수**(`UAudioComponent::Play`)이기 때문이다. 네이티브 UFunction 은
`ExecFunction` 이 실제 진입점이라 교체가 먹힌다.

**정리: ExecFunction 후킹은 네이티브 함수에만 통한다. 순수 블루프린트
함수에는 통하지 않는다.**

## 다음에 할 것

남은 지점은 `ProcessInternal` 자체다. 주소는 이미 알아냈다
(`0x7FF7702603D0`). 다만 모든 블루프린트 함수 호출이 여기를 지나가므로
`FFrame::Node` 를 읽어 어떤 함수인지 가려야 하고, FFrame 레이아웃을
확인해야 한다. 빈도도 ProcessEvent 급이다.

**시간이 없어 여기서 멈춘다. 추측을 결론으로 적지 않는다.**

## 남겨둔 것

이 과정에서 세 가지를 고쳤고 전부 **에러 없이 조용히 넘어가는** 종류였다.

- 후크 로그 경로를 한 자리만 봐서, 정상 동작을 "로그 없음"으로 읽었다
- 위반만 기록해서, "안 불렀다"와 "불렀는데 정상"과 "후크가 안 붙었다"가
  전부 NORMAL 로 나왔다
- 헤더 이름 `Provocation_Local_` 로 매칭했는데 런타임 FName 은
  `Provocation(Local)` 이었다 (Dumper-7 이 괄호를 언더스코어로 바꾼다)

지금은 관측 수(`calls` / `process_event`)를 같이 남기므로, 같은 증상이
다시 나오면 원인이 로그에서 바로 갈린다.

---

# ESP 측정 — "못 잡는다"를 실측으로 확인

2026-09-20. 게임을 새로 켜고 **아무것도 주입하지 않은 상태에서 ESP 만**
실행했다(`modules/esp/esp.py`). 화면에는 박스가 정상적으로 그려지고 있었다.

```
세션 esp_only_001

  filesystem     DETECTED 100   (파일 흔적 — ESP 와 무관, 원래 있던 것)
  injection      NORMAL     0   오브젝트 59,438 / 모듈 160 — 주입·후킹 흔적 없음
  whistle        NORMAL     0   UFunction 17,232 / 캐릭터 20
  value_tamper   NORMAL     0   설정값 비교 15건 — 값 변조 없음
```

**ESP 가 실제로 동작하는 동안 2번 레이어의 세 검사가 모두 정상으로 나왔다.**

## 이것은 오탐도 미탐도 아니다

ESP 는 외부 프로세스에서 `ReadProcessMemory` 로 **읽기만** 한다.

- 게임에 모듈을 주입하지 않는다 → `injection` 에 걸릴 것이 없다
- 함수 포인터를 바꾸지 않는다 → 후킹 검사에 걸릴 것이 없다
- 게임 값을 쓰지 않는다 → `value_tamper` 에 걸릴 것이 없다

**바꾼 것이 없으므로 변조 감시로는 원리적으로 보이지 않는다.**
2번(메모리·코드 변조 감시)의 정의상 범위 밖이고, 탐지기를 더 만들어도
이 결론은 바뀌지 않는다.

## 그러면 어디서 잡히나

ESP 가 남기는 유일한 흔적은 **게임 프로세스를 여는 핸들**이다
(`OpenProcess` + `PROCESS_VM_READ`). 그것은 역할표 **1번(외부 접근·모듈 감시)**
의 "위험한 프로세스 핸들 감시" 항목이다.

유저 모드에서 남의 프로세스가 가진 핸들을 열거하려면
`NtQuerySystemInformation(SystemHandleInformation)` 이 필요하고, 안정적으로
하려면 커널 콜백(`ObRegisterCallbacks`)이 낫다 — 역할표 5번이다.

**2번의 한계를 숨기지 않고 1번·5번으로 넘긴다.**

---

# Auto Paint v1 측정

2026-09-20. `modules/auto-paint` 를 실행한 상태에서 측정했다.

```
세션 autopaint_001

  filesystem     DETECTED   100   (파일 흔적 — 원래 있던 것)
  injection      SUSPICIOUS  40   untrusted_module
  whistle        NORMAL       0
  value_tamper   NORMAL       0   설정값 비교 15건
```

## 잡은 근거

```
meccha-direct-bridge-v1-e223ff07…-b4947bff….dll   서명없음 + 기타 경로
C:\Users\<user>\AppData\Local\MecchaCamouflage\auto-paint-only\runtime\
    i-b4947bff…\meccha-direct-bridge-v1-…dll
```

**DLL 이름이 설치마다 해시로 랜덤화되어 있다.** 이름 목록으로는 원리적으로
못 잡는다. 서명과 경로로 잡았다.

1차 실측에서 모듈 이름 화이트리스트가 오탐 115건을 내서 점수에서 뺐는데,
이번 결과가 그 판단의 근거가 된다. 같은 세션에서 이름 화이트리스트는
"미확인 118건"을 냈고 그 안에 진짜 하나가 섞여 있었다. **서명+경로는
161개 중 정확히 1건만 집었다.**

## `value_tamper` 가 NORMAL 인 것은 내 가설이 틀렸기 때문이다

`URuntimePaintableComponent` 의 속도 제한(`MinScreenPaintDistance`,
`MaxBatchSize`, `MaxNetworkBatchesPerTick`, `MaxReplicatedPaintStrokesPerTick`,
`AutoFlushThreshold`, `bAutoFlushStrokes`, `bRealtimeNetworkSync`)을
"봇이 빨리 칠하려면 풀어야 한다"고 추론해서 대상에 넣었다.

**핵 소스를 확인해보니 이 필드들을 하나도 쓰지 않는다.**
Auto Paint v1 은 제한값을 건드리지 않고 정상 속도로 RPC 를 호출한다.

그래서 이 7개 필드는 **검증된 탐지면이 아니라 추측**이다. 지우지는 않는다 —
제한을 푸는 변종이 나오면 걸린다. 다만 실측으로 확인된 것처럼 보이지
않도록 여기에 적어둔다.

## Auto Paint v1 의 실제 탐지면

| 경로 | 결과 |
|---|---|
| 주입된 DLL (서명+경로) | **SUSPICIOUS 40** ← 유일하게 확인된 탐지면 |
| 코드 패치 | 없음 (`process_event_hooked` 안 걸림) |
| 설정값 변조 | 없음 |
| RPC 호출 패턴 | **미구현** — `ServerPaintBatch` / `PaintAtUVWithBrush` |

40점(SUSPICIOUS)에 그치는 것이 맞다. 정상 오버레이·드라이버도 서명이 없을
수 있어 단독으로 확정하지 않는다.

확정하려면 RPC 호출 패턴을 봐야 하고, 그건 휘파람에서 막힌 것과 같은
문제다 — `ProcessEvent` 가 아니라 `ExecFunction` 을 가로채야 한다.
