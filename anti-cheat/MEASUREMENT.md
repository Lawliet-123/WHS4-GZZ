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

## 아직 검증하지 못한 것

핵이 `Provocation(Server)` 를 **직접 호출**하면 그때는 `ProcessEvent` 를
통과해야 한다. 외부 코드가 블루프린트 함수를 부르는 길이 그것뿐이기 때문이다.
그렇다면 "입력 이벤트 없이 ProcessEvent 로 들어온 도발" 자체가 위반 신호가 된다.

**이 가설은 검증하지 못했다.** 보유한 `whistle_v14.dll` 은 소리를 바꾸는
핵이라 정상 입력으로 동작한다. RPC 를 직접 부르는 경로를 태워보지 않았다.

## 다음에 할 것

1. RPC 를 직접 호출하는 경로로 재시험한다 (가설 확인/반증)
2. 아니면 가로채는 지점을 바꾼다 — `Provocation` UFunction 들의
   `ExecFunction` 을 개별 교체하면 VM 경로도 잡힌다. 초당 수천 번이 아니라
   분당 몇 번이라 비용도 낮다.

## 남겨둔 것

이 과정에서 세 가지를 고쳤고 전부 **에러 없이 조용히 넘어가는** 종류였다.

- 후크 로그 경로를 한 자리만 봐서, 정상 동작을 "로그 없음"으로 읽었다
- 위반만 기록해서, "안 불렀다"와 "불렀는데 정상"과 "후크가 안 붙었다"가
  전부 NORMAL 로 나왔다
- 헤더 이름 `Provocation_Local_` 로 매칭했는데 런타임 FName 은
  `Provocation(Local)` 이었다 (Dumper-7 이 괄호를 언더스코어로 바꾼다)

지금은 관측 수(`calls` / `process_event`)를 같이 남기므로, 같은 증상이
다시 나오면 원인이 로그에서 바로 갈린다.
