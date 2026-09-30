#pragma once
// Diagnostic API intervals only. No native stack capture, memory reading, or verdict.
#include <windows.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cwchar>

namespace ks_audit {
static HANDLE file = INVALID_HANDLE_VALUE;
static ULONGLONG created = 0;
static LONGLONG frequency = 0;
static volatile LONG64 nextId = 0;
static volatile LONG failed = 0;
static SRWLOCK lock = SRWLOCK_INIT;
inline ULONGLONG value(const FILETIME& t) {
    return (static_cast<ULONGLONG>(t.dwHighDateTime) << 32) | t.dwLowDateTime;
}
inline void row(const char* api, const char* phase, LONGLONG id, DWORD pid,
                DWORD requested, DWORD flags, ULONG_PTR result, DWORD error) {
    DWORD saved = GetLastError();
    FILETIME now; LARGE_INTEGER qpc;
    GetSystemTimePreciseAsFileTime(&now); QueryPerformanceCounter(&qpc);
    char text[1024];
    int n = sprintf_s(text,
        "{\"type\":\"client_call\",\"api\":\"%s\",\"phase\":\"%s\",\"call_id\":%lld,"
        "\"pid\":%lu,\"process_create_time\":%llu,\"tid\":%lu,\"target_pid\":%lu,"
        "\"utc_filetime\":%llu,\"qpc\":%lld,\"qpc_frequency\":%lld,"
        "\"requested_access\":%lu,\"api_flags\":%lu,\"result\":%llu,\"last_error\":%lu}\n",
        api, phase, id, GetCurrentProcessId(), created, GetCurrentThreadId(), pid,
        value(now), qpc.QuadPart, frequency, requested, flags,
        static_cast<ULONGLONG>(result), error);
    AcquireSRWLockExclusive(&lock);
    DWORD written = 0;
    if (n <= 0 || file == INVALID_HANDLE_VALUE ||
        !WriteFile(file, text, static_cast<DWORD>(n), &written, nullptr) || written != static_cast<DWORD>(n))
        InterlockedExchange(&failed, 1);
    ReleaseSRWLockExclusive(&lock);
    SetLastError(saved);
}
template<class F>
inline auto invoke(const char* api, DWORD pid, DWORD requested, DWORD flags, F fn) -> decltype(fn()) {
    DWORD saved = GetLastError();
    LONGLONG id = InterlockedIncrement64(&nextId);
    row(api, "begin", id, pid, requested, flags, 0, 0);
    SetLastError(saved);
    auto result = fn();
    DWORD error = GetLastError();
    row(api, "end", id, pid, requested, flags, (ULONG_PTR)result, error);
    SetLastError(error);
    return result;
}
}

class CcpAuditSession {
public:
    CcpAuditSession() {
        DWORD saved = GetLastError();
        FILETIME c{}, e{}, k{}, u{}; LARGE_INTEGER f{};
        if (GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u) && QueryPerformanceFrequency(&f)) {
            ks_audit::created = ks_audit::value(c); ks_audit::frequency = f.QuadPart;
            wchar_t name[128];
            swprintf_s(name, L"client_calls_%lu_%llu.jsonl", GetCurrentProcessId(), ks_audit::created);
            ks_audit::file = CreateFileW(name, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW,
                                          FILE_ATTRIBUTE_NORMAL, nullptr);
            if (ks_audit::file != INVALID_HANDLE_VALUE) fwprintf(stderr, L"API audit: %ls\n", name);
        }
        SetLastError(saved);
    }
    bool ok() const { return ks_audit::file != INVALID_HANDLE_VALUE; }
    ~CcpAuditSession() {
        if (ok()) { CloseHandle(ks_audit::file); ks_audit::file = INVALID_HANDLE_VALUE; }
        if (ks_audit::failed) fputs("API audit write failed; intervals are incomplete.\n", stderr);
    }
    CcpAuditSession(const CcpAuditSession&) = delete;
    CcpAuditSession& operator=(const CcpAuditSession&) = delete;
};

inline HANDLE CcpAuditOpenProcess(DWORD access, BOOL inherit, DWORD pid) {
    return ks_audit::invoke("OpenProcess", pid, access, 0,
                           [&] { return OpenProcess(access, inherit, pid); });
}
inline HANDLE CcpAuditCreateToolhelp32Snapshot(DWORD flags, DWORD pid) {
    return ks_audit::invoke("CreateToolhelp32Snapshot", pid, 0, flags,
                           [&] { return CreateToolhelp32Snapshot(flags, pid); });
}
inline BOOL CcpAuditModule32FirstW(HANDLE snapshot, LPMODULEENTRY32W module, DWORD pid) {
    return ks_audit::invoke("Module32FirstW", pid, 0, 0,
                           [&] { return Module32FirstW(snapshot, module); });
}
inline BOOL CcpAuditQueryFullProcessImageNameW(HANDLE process, DWORD flags, LPWSTR path, PDWORD size, DWORD pid) {
    return ks_audit::invoke("QueryFullProcessImageNameW", pid, 0, flags,
                           [&] { return QueryFullProcessImageNameW(process, flags, path, size); });
}
inline BOOL CcpAuditGetProcessTimes(HANDLE process, LPFILETIME c, LPFILETIME e, LPFILETIME k, LPFILETIME u, DWORD pid) {
    return ks_audit::invoke("GetProcessTimes", pid, 0, 0,
                           [&] { return GetProcessTimes(process, c, e, k, u); });
}
