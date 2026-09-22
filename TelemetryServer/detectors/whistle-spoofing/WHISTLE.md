# 휘파람(Provocation) 탐지 — 사용법과 한계

담당: 말랑미 · 대상: MECCHA CHAMELEON 4.0.2 · 역할표 2번(메모리·코드 변조 감시)

---

## 무엇을 잡나

핵이 **게임 함수 테이블을 바꿔치기했는지**를 본다.

언리얼은 살아있는 오브젝트를 `GObjects` 전역 배열로 관리하고, 오브젝트마다
함수 테이블(vtable)이 있다. 그 **76번(0x4C) 슬롯이 `ProcessEvent`** 이고,
블루프린트 함수 호출이 전부 여기를 지나가서 핵이 제일 먼저 노리는 자리다.

판정은 **주소 범위 비교**다.

```
정상 : vtable[0x4C] ∈ [게임 모듈 베이스, 베이스 + 이미지 크기)
후킹 : vtable[0x4C] ∈ 주입된 DLL 의 주소 공간 → 그 DLL 이름까지 역추적
```

정상 값을 미리 저장해둘 필요가 없다. UE5 는 `AActor` 가 `UObject::ProcessEvent`
를 오버라이드해서 **정상 상태에서도 같은 슬롯에 구현이 여러 개**다.
값을 하나로 고정해두는 방식이었으면 오탐이 났다.

### 핵 종류를 가리지 않는다

후킹 여부만 보므로 휘파람·갓모드가 같은 검사에 걸린다.
핵별로 추가되는 건 "어느 핵인지" 이름 붙이는 부분뿐이다.

| 검사 | 특정하는 것 |
|---|---|
| W-1 ExecFunction 교체 | 어떤 **함수**가 바뀌었나 (`Play()`) |
| W-2 캐릭터 vtable 변조 | 어떤 **클래스**가 바뀌었나 |
| W-3 도발 사운드 교체 | 어떤 **사운드**로 바뀌었나 |

---

## 실행

```bash
# 게임이 켜져 있어야 한다
python whistle.py whistle_001
```

출력은 팀 공통 형식이다 (`../../LocalGuard/memory_integrity/core/result.py`).

```json
{"session_id": "whistle_001", "module": "whistle", "timestamp_ms": 18400,
 "status": "DETECTED", "severity": "HIGH",
 "reasons": ["exec_function_hooked"],
 "evidence": {"module": "whistle_v14.dll (주입됨)",
              "detail": "Play() 가 whistle_v14.dll 을 가리킵니다"},
 "score": 60}
```

종료코드 — `0` 정상 / `1` 의심 이상 / `2` 검사 실패(ERROR·OFFLINE)

**`status` 를 먼저 보고 집계해야 한다.** `OFFLINE`(게임 꺼짐)과
`ERROR`(오프셋 불일치 등)는 탐지율·오탐률 집계에서 빼고, 제외 건수를
표에 같이 실어야 한다. 검사를 못 한 것을 깨끗한 것으로 뭉개면 조용한
미탐지가 된다.

`pymem` 이 필요하다. `pip install pymem`

---

## 측정 결과

같은 세션에서 **주입 → 언훅** 순으로 A/B 를 잡았다.

| 검사 | 깨끗한 상태 | 후킹 상태 |
|---|---|---|
| W-1 ExecFunction | 0 | `Play()` → `whistle_v14.dll` |
| W-2 캐릭터 vtable | 0 | `..._Survivor_..._1point4_C` → `whistle_v14.dll` |
| W-3 사운드 교체 | 0 | `Industry_Hatch_Plastic_Closing_05` |

표본: 오브젝트 61,273 / UFunction 17,282 / 캐릭터 20. 소요 1.7초.

전체 A/B 기록과 교차 검증은 `../MEASUREMENT.md` 에 있다.

> **오탐 0% 는 정상 세션 1개 기준이고, 치트가 깔린 PC 에서 잰 값이다.**
> 깨끗한 설치본에서 PC 를 바꿔가며 다시 재야 통계적 의미가 생긴다.

---

## 못 잡는 것 (먼저 적는다)

- **외부 RPM 방식** — ESP 처럼 읽기만 하는 핵은 흔적이 없다. Ring 3 에서는
  원리적으로 탐지 불가다. Ring 0 이 필요한데 이번 범위 밖이다.
- **게임 모듈 내부에 코드를 심는 경우** — 범위 검사를 통과한다.
  난이도는 올라가지만 불가능하지 않다.
- **취약점 자체** — 이 탐지기는 *구현 방식 하나*를 잡는 것이지 취약점을
  없애는 게 아니다. 후킹을 안 쓰고 RPC 를 직접 호출만 해도 그대로 뚫린다.
  실측한 휘파람 취약점 6종은 **서버측에서만 닫힌다.**
- **상시 감시용이 아니다.** 오브젝트 5.7만 개를 외부에서 읽으면 구조적으로
  13~23초가 걸린다. 주기적 스캔용이다.

---

## RPC 호출 관측 (`whistle_rpc.py` + `native/whistle_hook`) — 미검증

위 한계 중 "호출 시점을 외부에서 볼 수 없다"를 **인프로세스 후크**로 푼 것이다.
멘토 검수에서 안티치트도 후킹을 써도 된다는 확인을 받고 만들었다.

`ProcessEvent` 를 후킹해 도발 RPC 호출을 그 자리에서 보고 5가지를 판정한다 —
호출자 역할, 생존 상태, 대상 지정, 호출 빈도, 입력 이벤트 유무.

```bash
cmake -B build -DSDK_DIR=<Dumper-7 CppSDK 경로>    # SDK 는 커밋하지 않는다
cmake --build build --config Release
# DLL 을 주입하면 자기 옆에 ac-whistle.jsonl 을 남긴다
python whistle_rpc.py whistle_rpc_001
```

기본은 **탐지만** 하고, 옆에 `ac-whistle.block` 파일을 두면 차단까지 한다.

> ⚠️ **빌드만 되어 있고 게임 연동 검증을 아직 안 했다.**
> 주입하면 게임이 죽을 수 있으니 반드시 사설방에서만 시험할 것.

같은 후크가 다른 핵의 RPC 호출(`ServerPaintBatch` 등)도 볼 수 있다.
함수 이름 목록만 바꾸면 된다.

---

## 파일

여기(`TelemetryServer/detectors/whistle-spoofing/`)에 있는 것 — 휘파람 핵 담당 몫.

```
main.py                   휘파람 러너 (등록표만 갖고 2번 러너를 재사용)
whistle.py                W-1/W-2/W-3 정적 스캔         ← 검증 완료
whistle_rpc.py            후크 로그 → 공통 형식          ← 검증 완료 (A/B)
native/whistle_hook/      ProcessInternal 후크 (C++)     ← 관측 성공
measurements/             A/B 원본 로그 2세션
```

2번 모듈(`LocalGuard/memory_integrity/`)에서 빌려 쓰는 것 —
`sys.path` 한 줄로 건너간다.

```
core/result.py        탐지기 공통 결과 계약 + 팀 형식 변환
core/unreal.py        UE5 런타임 외부 읽기 + FName 해석
run_session.py        세션 묶기·공통 형식 출력·종료 코드
```

`core/` 를 공용 `shared/` 로 올릴지는 8번(공통 로그 규격) 확정 후에 정한다.
지금 올리면 주인이 정해지지 않은 자리를 선점하게 된다.

`core/unreal.py` 의 `NAMEPOOL_RVA` 는 Dumper-7 이 준 `GNames` 값이 틀려서
`AppendString` 디스어셈블로 직접 찾은 것이다. 게임이 업데이트되면 다시 떠야 한다.
생성자가 `resolve(0) != "None"` 이면 예외를 던지므로, 오프셋이 틀리면
조용히 빈 결과가 나오지 않고 `ERROR` 로 보고된다.
