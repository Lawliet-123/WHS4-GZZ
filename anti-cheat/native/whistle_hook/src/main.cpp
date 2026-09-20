/* 휘파람 RPC 검증 후크 — 인프로세스 안티치트 (MECCHA CHAMELEON 4.0.2)
 *
 * 멘토 검수에서 "안티치트도 후킹으로 진행해도 된다"는 확인을 받아 만들었다.
 * 그 전까지는 외부 관찰(ReadProcessMemory)만 썼는데, 그 방식으로는
 * **RPC 호출 시점 자체를 볼 수 없어서** 실측한 취약점 6가지 중 하나도
 * 탐지하지 못했다. 후킹이 허용되면서 5가지가 탐지 가능해진다.
 *
 * ── 무엇을 보는가 ──────────────────────────────────────────────────────
 *
 *   V1 호출 경로   직전에 입력 핸들러가 안 돌았으면 UFunction 직접 호출이다
 *   V2 호출 빈도   호출 간격. 실측으로 1.016초에 5회가 전부 통과했다
 *   V3 호출자 역할 폰의 IsHunter(+0x0C3A). 술래는 원래 도발을 못 한다
 *   V4 생존 상태   폰의 Dead(+0x05AA). 죽은 사람/관전자는 원래 못 한다
 *   V5 대상 지정   호출된 폰이 내 로컬 폰이 아니면 남을 대신 울린 것이다
 *   V6 위치 노출   **탐지 불가.** 복제 데이터를 읽는 것이라 흔적이 없다
 *
 * ── 이 코드의 위치를 정확히 해둘 것 ────────────────────────────────────
 *
 * 이건 **서버가 해야 할 검사를 클라이언트에서 흉내 낸 것**이다.
 * 치터는 우리 DLL 을 안 띄우면 그만이므로 그 자체로는 방어가 아니다.
 * 목적은 두 가지다.
 *   (1) 정상 클라이언트에서 위반을 관측·기록해 서버에 신고할 근거를 만든다
 *   (2) **S-1~S-3 서버 검사가 실제로 취약점을 닫는다는 것을 실증한다.**
 *       게임 서버 소스가 없어 못 하던 실증을, 같은 판정 로직을 후크에
 *       넣어 대신한다. docs/23 의 권고안이 탁상공론이 아님을 보이는 용도다.
 *
 * ── 후킹 구현에서 조심한 것 ────────────────────────────────────────────
 *
 * AActor 가 UObject::ProcessEvent 를 오버라이드한다. 원본 포인터를 하나만
 * 저장하면 actor 계열 vtable 221개가 **조용히 빠진다.** 이 프로젝트에서
 * 이미 하루를 날린 함정이라 vtable 별로 원본을 따로 들고 있는다.
 */

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "SDK/Basic.hpp"
#include "SDK/CoreUObject_classes.hpp"
#include "SDK/Engine_classes.hpp"

using namespace CLeonSDK;

/* ── 4.0.2 오프셋 ─────────────────────────────────────────────────────── */
static constexpr int kIsHunterOffset = 0x0C3A;   // ABP_..._cLeon_Character_C::IsHunter
static constexpr int kDeadOffset     = 0x05AA;   // ABP_FirstPersonCharacter_Main_C::Dead

/* 게임 애셋에 오타가 있다. Provocation 이 아니라 Provoaction 인 자산도 있다.

   **SDK 헤더의 C++ 식별자와 런타임 FName 은 다르다.**
   Dumper-7 은 헤더에서 괄호를 언더스코어로 바꾼다.

       헤더   Provocation_Local_   Provocation_Client_   Provocation_Server_
       런타임 Provocation(Local)   Provocation(Client)   Provocation(Server)

   처음에 헤더 이름을 그대로 써서 "Provocation_" 접두사로 검사했고,
   **후크는 정상인데 도발 호출을 한 건도 못 잡았다.** 에러가 나지 않아
   NORMAL 로 보고됐다. 런타임 이름은 밖에서 GObjects 를 훑어 확인했다.
   ProvocationRemote 도 그때 같이 나왔다. */
static bool IsProvocationFn(const std::string& Name)
{
    // Provocation(Local) / (Client) / (Server) / ProvocationRemote 를 모두 받는다.
    return Name.rfind("Provocation", 0) == 0;
}
static bool IsProvocationInput(const std::string& Name)
{
    return Name.find("InpActEvt_IA_Provocation") != std::string::npos;
}

/* ── 설정 ─────────────────────────────────────────────────────────────── */
/* 쿨다운은 게임 디자인 값을 모르므로 보수적으로 잡는다. 실측에서 1.016초에
   5회(간격 약 0.2초)가 전부 통과했다. 정상 연타의 상한을 넉넉히 두고
   그보다 빠른 것만 위반으로 본다. 이 값은 측정으로 조정해야 한다. */
static constexpr double kMinIntervalSec = 0.60;

/* 입력 핸들러가 돌고 나서 이 시간 안에 RPC 가 오면 정상 경로로 인정한다. */
static constexpr double kInputGraceSec = 0.50;

/* ── 상태 ─────────────────────────────────────────────────────────────── */
using ProcessEventFn = void(*)(void*, UFunction*, void*);

static std::unordered_map<void**, ProcessEventFn> gOriginalByVTable;
static std::shared_mutex                          gOriginalLock;
static std::unordered_set<void**>                 gKnownVTables;
static thread_local int gInHook = 0;

static std::atomic<bool> gRunning{true};
static std::atomic<bool> gBlockMode{false};      // .block 파일이 있으면 차단까지
static std::atomic<unsigned long long> gViolations{0};
static std::atomic<unsigned long long> gCalls{0};
/* 도발과 무관한 것까지 포함한 ProcessEvent 총 호출 수.

   진단용이다. calls 가 0 일 때 원인이 두 가지인데 구분이 안 됐다 —
   후크가 아예 안 불리는 것과, 불리는데 함수 이름 매칭이 틀린 것.
   실제로 후자였고(런타임 FName 이 헤더와 다름) 알아내는 데 오래 걸렸다.
   이 값이 크고 calls 가 0 이면 이름 매칭을 의심하면 된다. */
static std::atomic<unsigned long long> gTotalPE{0};

static std::mutex gLogLock;
static std::wstring gLogPath;
static ULONGLONG gStartTick = 0;

/* 마지막 입력 핸들러 시각 (V1 판정용) */
static std::atomic<ULONGLONG> gLastInputTick{0};
/* 마지막 도발 시각 (V2 판정용) */
static std::atomic<ULONGLONG> gLastProvoTick{0};

/* ── 로그 ─────────────────────────────────────────────────────────────── */
static std::wstring DllDirectory()
{
    HMODULE Self{};
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                       GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&DllDirectory), &Self);
    wchar_t Buf[MAX_PATH]{};
    GetModuleFileNameW(Self, Buf, MAX_PATH);
    std::wstring Out(Buf);
    const auto Slash = Out.find_last_of(L'\\');
    return Slash == std::wstring::npos ? L"." : Out.substr(0, Slash);
}

/* JSONL 한 줄. 파이썬 보고 계층이 이걸 읽어 result.py 계약으로 바꾼다. */
static void LogLine(const std::string& Json)
{
    std::lock_guard<std::mutex> Lock(gLogLock);
    HANDLE File = CreateFileW(gLogPath.c_str(), FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                              OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (File == INVALID_HANDLE_VALUE) return;
    const std::string Line = Json + "\r\n";
    DWORD Written{};
    WriteFile(File, Line.data(), static_cast<DWORD>(Line.size()), &Written, nullptr);
    CloseHandle(File);
}

static std::string Escape(const std::string& S)
{
    std::string Out;
    for (char C : S)
    {
        if (C == '"' || C == '\\') { Out.push_back('\\'); Out.push_back(C); }
        else if (static_cast<unsigned char>(C) < 0x20) Out.push_back('?');
        else Out.push_back(C);
    }
    return Out;
}

/* ── 로컬 폰 ──────────────────────────────────────────────────────────── */
static APawn* GetLocalPawn()
{
    UWorld* World = UWorld::GetWorld();
    if (!World || !World->OwningGameInstance) return nullptr;
    auto& Players = World->OwningGameInstance->LocalPlayers;
    if (Players.Num() <= 0) return nullptr;
    ULocalPlayer* LP = Players[0];
    if (!LP || !LP->PlayerController) return nullptr;
    return LP->PlayerController->Pawn;
}

/* ── 규칙 판정 ────────────────────────────────────────────────────────── */
struct Verdict
{
    bool violated = false;
    std::string codes;      // JSON 배열 내용
    std::string detail;

    void add(const char* code, const std::string& why)
    {
        violated = true;
        if (!codes.empty()) codes += ", ";
        codes += "\"";
        codes += code;
        codes += "\"";
        if (!detail.empty()) detail += " / ";
        detail += why;
    }
};

/* 도발 RPC 한 건을 서버가 했어야 할 검사에 통과시켜 본다.
   docs/23 의 S-1(호출자 상태) / S-2(대상 지정) / S-3(빈도) 와 1:1 대응한다. */
static Verdict Judge(void* Object, const std::string& FnName)
{
    Verdict V;
    auto* Base = reinterpret_cast<std::uint8_t*>(Object);
    const ULONGLONG Now = GetTickCount64();

    /* V3 — 호출자 역할 (S-1)
       술래는 원래 도발을 못 한다. 그런데 Provocation 은 술래와 생존자의
       공통 부모에 정의돼 있어서 술래 폰도 함수를 그대로 갖고 있다.
       게임이 술래한테 안 부를 뿐이다. */
    const bool IsHunter = *(Base + kIsHunterOffset) != 0;
    if (IsHunter)
        V.add("role_violation", "술래 폰이 도발 RPC 를 호출했습니다");

    /* V4 — 생존 상태 (S-1) */
    const bool IsDead = *(Base + kDeadOffset) != 0;
    if (IsDead)
        V.add("dead_caller", "사망/관전 상태에서 도발을 발동했습니다");

    /* V5 — 대상 지정 (S-2)
       내 폰이 아닌 폰에 대고 호출했다면 남을 대신 울린 것이다.
       실측에서 18.2m 밖 임의 대상을 지목할 수 있었다. */
    APawn* Mine = GetLocalPawn();
    if (Mine && reinterpret_cast<void*>(Mine) != Object)
        V.add("foreign_target", "다른 플레이어의 폰에 대고 호출했습니다");

    /* V2 — 호출 빈도 (S-3)
       실측에서 1.016초에 5회가 전부 전파됐다. 서버에 쿨다운이 없다. */
    const ULONGLONG Last = gLastProvoTick.exchange(Now);
    if (Last != 0)
    {
        const double Gap = static_cast<double>(Now - Last) / 1000.0;
        if (Gap < kMinIntervalSec)
        {
            char Buf[128];
            snprintf(Buf, sizeof(Buf), "직전 도발과 %.3f초 간격 (최소 %.2f초)",
                     Gap, kMinIntervalSec);
            V.add("cooldown_violation", Buf);
        }
    }

    /* V1 — 호출 경로
       정상이라면 입력 핸들러가 먼저 돌고 곧바로 RPC 가 온다.
       입력 없이 RPC 만 왔다면 UFunction 을 직접 호출한 것이다. */
    const ULONGLONG LastInput = gLastInputTick.load();
    const double SinceInput = LastInput == 0
        ? 1e9 : static_cast<double>(Now - LastInput) / 1000.0;
    if (SinceInput > kInputGraceSec)
        V.add("no_input_event", "입력 핸들러를 거치지 않은 직접 호출입니다");

    return V;
}

/* 관측 사실을 남긴다.

   Report() 는 **위반일 때만** 기록한다. 그래서 로그만 봐서는
   "도발을 한 번도 안 불렀다" 와 "불렀는데 전부 정상이었다" 가 구분되지 않는다.
   후크가 아예 안 붙었어도 로그 모양이 똑같아진다 — 조용한 미탐지다.

   그래서 호출을 하나라도 본 뒤에는 주기적으로 관측 수를 남긴다.
   호출이 없으면 이 줄도 없으므로, 보고 계층이 "검사한 것이 아니다" 를
   구분할 수 있다. */
static std::atomic<ULONGLONG> gLastStatsTick{0};
static constexpr ULONGLONG kStatsIntervalMs = 3000;

static void ReportStats(bool Force)
{
    const ULONGLONG Now = GetTickCount64();
    const ULONGLONG Last = gLastStatsTick.load(std::memory_order_relaxed);
    if (!Force && Now - Last < kStatsIntervalMs) return;
    gLastStatsTick.store(Now, std::memory_order_relaxed);

    char Buf[256];
    snprintf(Buf, sizeof(Buf),
             "{\"t\":%.3f,\"event\":\"stats\",\"calls\":%llu,\"violations\":%llu,"
             "\"process_event\":%llu}",
             static_cast<double>(Now - gStartTick) / 1000.0,
             gCalls.load(), gViolations.load(), gTotalPE.load());
    LogLine(Buf);
}

/* ── 진단 ──────────────────────────────────────────────────────────────
   ProcessEvent 는 초당 수천 번 불리는데 도발 매칭이 0 건이면 원인이 셋이다.
   휘파람을 안 불었거나, 이름 매칭이 틀렸거나, GetName() 자체가 깨졌거나.
   로그만으로는 구분이 안 돼서 실제로 오래 헤맸다.

   그래서 **처음 본 함수 이름 몇 개를 그대로 남긴다.** 이름이 멀쩡하면
   GetName() 은 정상이고, 그러면 남은 원인은 둘로 좁혀진다.
   그리고 "provo" 가 들어간 이름은 대소문자 구분 없이 전부 남긴다 —
   접두사가 아니라 어디에 있든 잡아서 진짜 이름을 보여준다. */
static std::mutex gSampleLock;
static std::unordered_set<std::string> gSeenNames;
static int gSampleLeft = 24;
static int gProvoSampleLeft = 40;

/* 입력 직후 호출 순서 기록.

   `Provocation(Local/Client/Server)` 4개의 ExecFunction 을 전부 교체했는데도
   호출이 안 잡혔다. 가능성이 둘인데 아직 구분을 못 했다.

     (a) VM 이 ExecFunction 경로를 안 탄다 -> ProcessInternal 을 후킹해야 한다
     (b) 그 4개가 애초에 안 불린다 -> 휘파람 소리는 다른 함수가 낸다

   (b)면 엉뚱한 함수를 걸고 있었던 것이므로 (a) 작업이 통째로 불필요하다.
   그래서 **입력 핸들러가 발동한 직후 실제로 무엇이 불리는지** 그대로 남긴다.
   후킹을 추가하지 않고 기록만 하므로 위험이 없다.

   중복도 그대로 남긴다. 순서와 반복이 곧 호출 경로다. */
static std::atomic<int> gSeqLeft{0};
static std::atomic<ULONGLONG> gSeqUntil{0};
static constexpr int kSeqMax = 120;          // 게임 스레드에서 파일을 쓰므로 제한
static constexpr ULONGLONG kSeqWindowMs = 400;

static void SampleName(const std::string& Name)
{
    std::string Lower;
    Lower.reserve(Name.size());
    for (char c : Name) Lower += static_cast<char>(::tolower((unsigned char)c));
    const bool Provo = Lower.find("provo") != std::string::npos;

    {
        std::lock_guard<std::mutex> Lock(gSampleLock);
        if (!Provo && gSampleLeft <= 0) return;
        if (Provo && gProvoSampleLeft <= 0) return;
        if (!gSeenNames.insert(Name).second) return;
        if (Provo) --gProvoSampleLeft; else --gSampleLeft;
    }

    char Buf[512];
    snprintf(Buf, sizeof(Buf),
             "{\"t\":%.3f,\"event\":\"name\",\"provo\":%s,\"fn\":\"%s\"}",
             static_cast<double>(GetTickCount64() - gStartTick) / 1000.0,
             Provo ? "true" : "false", Escape(Name).c_str());
    LogLine(Buf);
}

static void Report(void* Object, const std::string& FnName, const Verdict& V)
{
    const double T = static_cast<double>(GetTickCount64() - gStartTick) / 1000.0;
    char Buf[1024];
    snprintf(Buf, sizeof(Buf),
             "{\"t\":%.3f,\"fn\":\"%s\",\"pawn\":\"0x%p\",\"codes\":[%s],"
             "\"detail\":\"%s\",\"blocked\":%s}",
             T, Escape(FnName).c_str(), Object, V.codes.c_str(),
             Escape(V.detail).c_str(), gBlockMode.load() ? "true" : "false");
    LogLine(Buf);
}

/* ── 후크 ─────────────────────────────────────────────────────────────── */
static ProcessEventFn FindOriginal(void* Object)
{
    void** VTable = *reinterpret_cast<void***>(Object);
    std::shared_lock<std::shared_mutex> Lock(gOriginalLock);
    auto It = gOriginalByVTable.find(VTable);
    return It == gOriginalByVTable.end() ? nullptr : It->second;
}

/* 판정부를 SEH 로 감싸기 위해 분리한다. 소멸자를 가진 지역 변수와
   __try 를 같은 함수에 두면 C2712 가 난다. */
static bool Inspect(void* Object, UFunction* Function)
{
    const std::string Name = Function->GetName();
    /* 도발 호출이 0 건이어도 관측 사실은 남아야 한다. 그래야 "후크가 안
       불린다" 와 "이름 매칭이 틀렸다" 가 구분된다. 여기는 초당 수천 번
       지나가는 자리라 512 번에 한 번만 시각을 본다(실제 기록은 3초 간격). */
    const unsigned long long Seen =
        gTotalPE.fetch_add(1, std::memory_order_relaxed) + 1;
    if ((Seen & 511) == 0) ReportStats(false);
    SampleName(Name);

    if (IsProvocationInput(Name))
    {
        const ULONGLONG Now = GetTickCount64();
        gLastInputTick.store(Now);
        /* 여기서부터 잠깐 동안 지나가는 함수를 전부 적는다. */
        gSeqLeft.store(kSeqMax);
        gSeqUntil.store(Now + kSeqWindowMs);
        char B[256];
        snprintf(B, sizeof(B), "{\"t\":%.3f,\"event\":\"input\",\"window_ms\":%llu}",
                 static_cast<double>(Now - gStartTick) / 1000.0, kSeqWindowMs);
        LogLine(B);
        return false;
    }

    /* 입력 직후 창이 열려 있으면 순서대로 기록한다. */
    if (gSeqLeft.load(std::memory_order_relaxed) > 0)
    {
        if (GetTickCount64() > gSeqUntil.load(std::memory_order_relaxed))
        {
            gSeqLeft.store(0);
        }
        else if (gSeqLeft.fetch_sub(1, std::memory_order_relaxed) > 0)
        {
            char B[512];
            snprintf(B, sizeof(B), "{\"t\":%.3f,\"event\":\"seq\",\"fn\":\"%s\"}",
                     static_cast<double>(GetTickCount64() - gStartTick) / 1000.0,
                     Escape(Name).c_str());
            LogLine(B);
        }
    }
    if (!IsProvocationFn(Name)) return false;

    gCalls.fetch_add(1, std::memory_order_relaxed);
    const Verdict V = Judge(Object, Name);
    if (V.violated)
    {
        gViolations.fetch_add(1, std::memory_order_relaxed);
        Report(Object, Name, V);
        ReportStats(true);            // 위반 옆에 관측 수를 같이 남긴다
        return gBlockMode.load();     // 차단 모드면 원본을 안 부른다
    }
    ReportStats(false);               // 정상 호출도 "봤다"는 사실은 남긴다
    return false;
}

static void HookedProcessEvent(void* Object, UFunction* Function, void* Parms)
{
    ProcessEventFn Original = Object ? FindOriginal(Object) : nullptr;

    /* 재진입 가드. 판정 중에 게임 함수를 부르면 다시 여기로 들어온다. */
    if (gInHook > 0)
    {
        if (Original) Original(Object, Function, Parms);
        return;
    }

    bool Block = false;
    if (Object && Function)
    {
        gInHook++;
        __try { Block = Inspect(Object, Function); }
        __except (EXCEPTION_EXECUTE_HANDLER) {}
        gInHook--;
    }

    if (!Block && Original) Original(Object, Function, Parms);
}

/* ── 설치 ─────────────────────────────────────────────────────────────── */
static bool PatchSlot(void** VTable, int Index, void* New, void** OutOld)
{
    DWORD Old{};
    if (!VirtualProtect(&VTable[Index], sizeof(void*), PAGE_EXECUTE_READWRITE, &Old))
        return false;
    if (OutOld) *OutOld = VTable[Index];
    VTable[Index] = New;
    FlushInstructionCache(GetCurrentProcess(), &VTable[Index], sizeof(void*));
    DWORD Ignored{};
    VirtualProtect(&VTable[Index], sizeof(void*), Old, &Ignored);
    return true;
}

/* ── ExecFunction 후킹 ────────────────────────────────────────────────

   `ProcessEvent` 로는 도발 호출이 보이지 않는다. 언리얼은 블루프린트가
   자기 안의 함수를 부를 때 바이트코드 VM 으로 직접 실행하고, ProcessEvent
   는 **외부에서** 들어올 때의 입구이기 때문이다. 실측으로 확인했다 —
   37초에 ProcessEvent 80,896건이 지나갔는데 도발은 0건이었다.

   그래서 가로채는 지점을 `UFunction::ExecFunction` 으로 옮긴다. VM 경로가
   여기를 지나간다. 휘파람 핵도 같은 벽에 부딪혀 같은 방법을 썼다
   (tools/whistle `HookedPlayExec`). 이 빌드에서 동작이 확인된 기법이다.

   비용도 낮다. ProcessEvent 는 초당 수천 번이지만 여기는 도발을 불 때만
   불린다.

   ## 함정

   `ExecFunction(Context, Stack, Result)` 은 **어떤 함수가 불렸는지 알려주지
   않는다.** 핵은 함수 하나만 걸어서 문제가 없었다. 우리는 넷이라 슬롯마다
   다른 함수가 필요하다. `Stack`(FFrame)에서 UFunction 을 읽는 방법도 있지만
   FFrame 레이아웃을 추측해야 해서, 템플릿으로 슬롯별 썽크를 만든다. */

using NativeExecFn = void (*)(void*, void*, void*);

static constexpr int kMaxExecHooks = 8;
static NativeExecFn gOrigExec[kMaxExecHooks]{};
static std::string gExecName[kMaxExecHooks];
static UFunction* gExecFunc[kMaxExecHooks]{};
static std::atomic<int> gExecCount{0};

/* 판정부를 따로 둔다. Verdict 가 소멸자를 가져서 __try 와 같은 함수에
   두면 C2712 가 난다. ProcessEvent 쪽 Inspect() 와 같은 이유다. */
static void ExecInspect(void* Context, int Slot)
{
    gCalls.fetch_add(1, std::memory_order_relaxed);
    const Verdict V = Judge(Context, gExecName[Slot]);
    if (V.violated)
    {
        gViolations.fetch_add(1, std::memory_order_relaxed);
        Report(Context, gExecName[Slot], V);
        ReportStats(true);
    }
    else
    {
        ReportStats(false);
    }
}

static void OnExecCall(void* Context, int Slot)
{
    if (!Context || gInHook > 0) return;
    gInHook++;
    __try { ExecInspect(Context, Slot); }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
    gInHook--;
}

template <int N>
static void HookedExec(void* Context, void* Stack, void* Result)
{
    OnExecCall(Context, N);
    if (gOrigExec[N]) gOrigExec[N](Context, Stack, Result);
}

static void* ExecThunk(int Slot)
{
    switch (Slot)
    {
    case 0: return reinterpret_cast<void*>(&HookedExec<0>);
    case 1: return reinterpret_cast<void*>(&HookedExec<1>);
    case 2: return reinterpret_cast<void*>(&HookedExec<2>);
    case 3: return reinterpret_cast<void*>(&HookedExec<3>);
    case 4: return reinterpret_cast<void*>(&HookedExec<4>);
    case 5: return reinterpret_cast<void*>(&HookedExec<5>);
    case 6: return reinterpret_cast<void*>(&HookedExec<6>);
    case 7: return reinterpret_cast<void*>(&HookedExec<7>);
    default: return nullptr;
    }
}

/* 도발 UFunction 들의 ExecFunction 을 교체한다. 이미 건 것은 건너뛴다. */
static int InstallExecHooks()
{
    int Added = 0;
    for (int i = 0; i < UObject::GObjects->Num(); ++i)
    {
        UObject* Obj = UObject::GObjects->GetByIndex(i);
        if (!Obj || !Obj->IsA(UFunction::StaticClass())) continue;

        const std::string Name = Obj->GetName();
        if (!IsProvocationFn(Name)) continue;

        auto* Fn = static_cast<UFunction*>(Obj);
        bool Known = false;
        const int Used = gExecCount.load();
        for (int s = 0; s < Used; ++s) if (gExecFunc[s] == Fn) { Known = true; break; }
        if (Known || Used >= kMaxExecHooks) continue;

        void* Thunk = ExecThunk(Used);
        if (!Thunk) continue;

        gOrigExec[Used] = reinterpret_cast<NativeExecFn>(Fn->ExecFunction);
        gExecName[Used] = Name;
        gExecFunc[Used] = Fn;

        DWORD Old{};
        if (!VirtualProtect(&Fn->ExecFunction, sizeof(void*), PAGE_READWRITE, &Old))
            continue;
        Fn->ExecFunction = reinterpret_cast<decltype(Fn->ExecFunction)>(Thunk);
        VirtualProtect(&Fn->ExecFunction, sizeof(void*), Old, &Old);

        gExecCount.store(Used + 1);
        ++Added;

        char Buf[512];
        snprintf(Buf, sizeof(Buf),
                 "{\"t\":%.3f,\"event\":\"exec_hook\",\"slot\":%d,\"fn\":\"%s\","
                 "\"original\":\"0x%p\"}",
                 static_cast<double>(GetTickCount64() - gStartTick) / 1000.0,
                 Used, Escape(Name).c_str(), (void*)gOrigExec[Used]);
        LogLine(Buf);
    }
    return Added;
}

static int InstallHooks()
{
    const int Idx = Offsets::ProcessEventIdx;
    int Patched = 0;

    for (int i = 0; i < UObject::GObjects->Num(); ++i)
    {
        UObject* Obj = UObject::GObjects->GetByIndex(i);
        if (!Obj) continue;
        void** VTable = *reinterpret_cast<void***>(Obj);
        if (!VTable) continue;
        if (gKnownVTables.count(VTable)) continue;

        void* Current = VTable[Idx];
        if (!Current || Current == reinterpret_cast<void*>(&HookedProcessEvent))
        {
            gKnownVTables.insert(VTable);
            continue;
        }

        {
            /* AActor 가 오버라이드하므로 vtable 마다 원본이 다르다.
               하나만 저장하면 actor 계열 221개가 조용히 빠진다. */
            std::unique_lock<std::shared_mutex> Lock(gOriginalLock);
            gOriginalByVTable[VTable] = reinterpret_cast<ProcessEventFn>(Current);
        }
        if (PatchSlot(VTable, Idx, reinterpret_cast<void*>(&HookedProcessEvent), nullptr))
        {
            gKnownVTables.insert(VTable);
            ++Patched;
        }
    }
    return Patched;
}

static DWORD WINAPI Worker(void*)
{
    gStartTick = GetTickCount64();
    gLogPath = DllDirectory() + L"\\ac-whistle.jsonl";

    char Buf[256];
    snprintf(Buf, sizeof(Buf),
             "{\"t\":0.000,\"event\":\"start\",\"min_interval\":%.2f,\"input_grace\":%.2f}",
             kMinIntervalSec, kInputGraceSec);
    LogLine(Buf);

    const std::wstring BlockFlag = DllDirectory() + L"\\ac-whistle.block";
    std::size_t LastTotal = 0;

    while (gRunning.load())
    {
        /* .block 파일이 있으면 차단 모드. 없으면 탐지만 한다.
           기본이 탐지인 이유: 클라 차단은 치터가 우리 DLL 을 안 띄우면
           그만이라 방어가 아니다. 서버 검사의 실증용으로만 켠다. */
        gBlockMode.store(GetFileAttributesW(BlockFlag.c_str()) != INVALID_FILE_ATTRIBUTES);

        /* 도발 RPC 는 ExecFunction 쪽에서만 보인다(위 주석 참고).
           ProcessEvent 후킹은 다른 핵의 vtable 변조를 보기 위해 유지한다. */
        InstallExecHooks();

        const int Added = InstallHooks();
        if (Added > 0 || gKnownVTables.size() != LastTotal)
        {
            LastTotal = gKnownVTables.size();
            snprintf(Buf, sizeof(Buf),
                     "{\"t\":%.3f,\"event\":\"hooks\",\"added\":%d,\"total\":%zu}",
                     static_cast<double>(GetTickCount64() - gStartTick) / 1000.0,
                     Added, gKnownVTables.size());
            LogLine(Buf);
        }
        Sleep(2000);
    }
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE Instance, DWORD Reason, LPVOID)
{
    if (Reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(Instance);
        if (HANDLE T = CreateThread(nullptr, 0, Worker, nullptr, 0, nullptr))
            CloseHandle(T);
    }
    else if (Reason == DLL_PROCESS_DETACH)
    {
        gRunning.store(false);
    }
    return TRUE;
}
