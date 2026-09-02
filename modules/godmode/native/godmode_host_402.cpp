#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
constexpr std::uintptr_t kGObjects = 0x095DC5A0;
constexpr std::uintptr_t kGNames = 0x0977D900;
constexpr std::uintptr_t kAppendString = 0x01392470;
constexpr int kProcessEventIndex = 0x4C;
constexpr std::uintptr_t kNameOffset = 0x18;
constexpr std::uintptr_t kClassOffset = 0x10;
constexpr std::uintptr_t kInvincibleOffset = 0x05AB;

using ProcessEventFn = void (*)(void*, void*, void*);
std::atomic<ProcessEventFn> g_original{nullptr};
std::atomic<bool> g_running{true};
std::atomic<bool> g_enabled{false};
std::atomic<unsigned long long> g_blocked_calls{0};
HMODULE g_module{};
std::mutex g_patch_mutex;
struct Patch { std::uintptr_t* slot; std::uintptr_t original; };
std::vector<Patch> g_patches;
struct UnrealString { wchar_t* data{}; int num{}; int max{}; };
using AppendStringFn = void (*)(const void*, UnrealString*);
AppendStringFn g_append_string{};
std::mutex g_name_mutex;
std::unordered_map<std::uint64_t, std::string> g_name_cache;

std::wstring dll_directory() {
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(g_module, path, MAX_PATH);
    std::wstring out(path);
    const auto slash = out.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"." : out.substr(0, slash);
}

void log_line(const std::string& text) {
    const auto path = dll_directory() + L"\\GodModeHost402.log";
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    const std::string line = text + "\r\n";
    DWORD written{};
    WriteFile(file, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
    CloseHandle(file);
}

struct NamePool {
    std::uintptr_t pool{};
    int table_offset{0x10};
    int style{1};

    std::string entry(std::uint32_t id, int table, int entry_style) const {
        const auto block_index = id >> 16;
        const auto within = static_cast<std::uintptr_t>(id & 0xFFFF) << 1;
        const auto block = *reinterpret_cast<std::uintptr_t*>(pool + table + block_index * 8);
        if (!block) return {};
        const auto header = *reinterpret_cast<std::uint16_t*>(block + within);
        bool wide{};
        int length{};
        if (entry_style == 0) { wide = (header & 1) != 0; length = header >> 1; }
        else if (entry_style == 2) { wide = (header & 1) != 0; length = (header >> 6) & 0x3FF; }
        else { length = header & 0x3FF; wide = ((header >> 10) & 1) != 0; }
        if (length <= 0 || length > 512) return {};
        if (wide) {
            const auto* src = reinterpret_cast<const wchar_t*>(block + within + 2);
            std::string out; out.reserve(length);
            for (int i = 0; i < length; ++i) out.push_back(src[i] < 128 ? static_cast<char>(src[i]) : '?');
            return out;
        }
        return std::string(reinterpret_cast<const char*>(block + within + 2), length);
    }

    void detect() {
        constexpr int offsets[]{0x8,0x10,0x18,0x20,0x28,0x30,0x38,0x40,0x48,0x50,0x58,0x60,0x68,0x70};
        for (int off : offsets) for (int st : {2,1,0}) if (entry(0, off, st) == "None") { table_offset=off; style=st; return; }
    }

    std::string resolve(std::uint32_t id) const { return entry(id, table_offset, style); }
};
NamePool g_names;

std::string object_name(void* object) {
    if (!object || !g_append_string) return {};
    const auto* fname = reinterpret_cast<const void*>(reinterpret_cast<std::uintptr_t>(object) + kNameOffset);
    const auto key = *reinterpret_cast<const std::uint64_t*>(fname);
    {
        std::lock_guard<std::mutex> lock(g_name_mutex);
        const auto found = g_name_cache.find(key);
        if (found != g_name_cache.end()) return found->second;
    }
    UnrealString value{};
    g_append_string(fname, &value);
    std::string out;
    if (value.data && value.num > 0 && value.num < 513) {
        out.reserve(static_cast<std::size_t>(value.num));
        for (int i = 0; i < value.num - 1; ++i) out.push_back(value.data[i] < 128 ? static_cast<char>(value.data[i]) : '?');
    }
    {
        std::lock_guard<std::mutex> lock(g_name_mutex);
        g_name_cache.emplace(key, out);
    }
    return out;
}

void hooked_process_event(void* object, void* function, void* params) {
    bool block = false;
    if (g_enabled.load(std::memory_order_relaxed) && function && params) {
        const auto name = object_name(function);
        if (name == "KillPlayer") {
            auto* target = *reinterpret_cast<std::uint8_t**>(params);
            if (target && *(target + kInvincibleOffset)) block = true;
        } else if (name == "AntiChatTrace") {
            auto* target = *reinterpret_cast<std::uint8_t**>(reinterpret_cast<std::uint8_t*>(params) + 0x30);
            if (target && *(target + kInvincibleOffset)) block = true;
        }
    }
    if (block) {
        g_blocked_calls.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (auto original = g_original.load(std::memory_order_relaxed)) original(object, function, params);
}

void patch_vtable(void* object) {
    if (!object) return;
    auto* vtable = *reinterpret_cast<std::uintptr_t**>(object);
    if (!vtable) return;
    auto* slot = vtable + kProcessEventIndex;
    const auto hook = reinterpret_cast<std::uintptr_t>(&hooked_process_event);
    std::lock_guard<std::mutex> lock(g_patch_mutex);
    for (const auto& p : g_patches) if (p.slot == slot) return;
    DWORD old{};
    if (!VirtualProtect(slot, sizeof(*slot), PAGE_EXECUTE_READWRITE, &old)) return;
    const auto original = *slot;
    if (!g_original.load()) g_original.store(reinterpret_cast<ProcessEventFn>(original));
    *slot = hook;
    FlushInstructionCache(GetCurrentProcess(), slot, sizeof(*slot));
    DWORD ignored{}; VirtualProtect(slot, sizeof(*slot), old, &ignored);
    g_patches.push_back({slot, original});
    log_line("ProcessEvent hook installed; total=" + std::to_string(g_patches.size()));
}

void scan_and_patch() {
    const auto base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    const auto objects = base + kGObjects;
    auto*** chunks = *reinterpret_cast<void****>(objects + 0x00);
    const int num = *reinterpret_cast<int*>(objects + 0x14);
    if (!chunks || num <= 0 || num > 4000000) return;
    for (int i = 0; i < num; ++i) {
        auto* chunk = reinterpret_cast<std::uint8_t*>(chunks[i / 65536]);
        if (!chunk) continue;
        auto* object = *reinterpret_cast<void**>(chunk + static_cast<std::uintptr_t>(i % 65536) * 0x18);
        if (!object) continue;
        auto* cls = *reinterpret_cast<void**>(reinterpret_cast<std::uintptr_t>(object) + kClassOffset);
        const auto cls_name = object_name(cls);
        if (cls_name.find("BP_FirstPersonCharacter_cLeon_Character_Hunter") != std::string::npos ||
            cls_name.find("BP_GameMode_cLeon") != std::string::npos) patch_vtable(object);
    }
}

void restore() {
    std::lock_guard<std::mutex> lock(g_patch_mutex);
    const auto hook = reinterpret_cast<std::uintptr_t>(&hooked_process_event);
    for (const auto& p : g_patches) {
        DWORD old{};
        if (VirtualProtect(p.slot, sizeof(*p.slot), PAGE_EXECUTE_READWRITE, &old)) {
            if (*p.slot == hook) *p.slot = p.original;
            DWORD ignored{}; VirtualProtect(p.slot, sizeof(*p.slot), old, &ignored);
        }
    }
}

DWORD WINAPI worker(void*) {
    log_line("GodModeHost402 worker started");
    const auto base = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    g_append_string = reinterpret_cast<AppendStringFn>(base + kAppendString);
    log_line("GodModeHost402 AppendString initialized");
    log_line("GodModeHost402 loaded; create GodModeHost402.on beside DLL to enable");
    unsigned long long last_blocked{};
    while (g_running.load()) {
        const bool enabled = GetFileAttributesW((dll_directory() + L"\\GodModeHost402.on").c_str()) != INVALID_FILE_ATTRIBUTES;
        if (enabled != g_enabled.exchange(enabled)) log_line(enabled ? "GodMode ON" : "GodMode OFF");
        scan_and_patch();
        const auto blocked = g_blocked_calls.load(std::memory_order_relaxed);
        if (blocked != last_blocked) {
            log_line("blocked server death call; total=" + std::to_string(blocked));
            last_blocked = blocked;
        }
        Sleep(1000);
    }
    restore();
    return 0;
}
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = instance;
        DisableThreadLibraryCalls(instance);
        log_line("GodModeHost402 DLL_PROCESS_ATTACH");
        if (HANDLE thread = CreateThread(nullptr, 0, worker, nullptr, 0, nullptr)) {
            CloseHandle(thread);
        } else {
            log_line("GodModeHost402 CreateThread failed; win32=" + std::to_string(GetLastError()));
        }
    } else if (reason == DLL_PROCESS_DETACH) {
        g_running.store(false);
    }
    return TRUE;
}
