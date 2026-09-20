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

/* 게임 애셋에 오타가 있다. Provocation 이 아니라 Provoaction 인 자산도 있다. */
static bool IsProvocationFn(const std::string& Name)
{
    return Name.rfind("Provocation_", 0) == 0;   // Provocation_Local_/Client_/Server_
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
             "{\"t\":%.3f,\"event\":\"stats\",\"calls\":%llu,\"violations\":%llu}",
             static_cast<double>(Now - gStartTick) / 1000.0,
             gCalls.load(), gViolations.load());
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

    if (IsProvocationInput(Name))
    {
        gLastInputTick.store(GetTickCount64());
        return false;
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
