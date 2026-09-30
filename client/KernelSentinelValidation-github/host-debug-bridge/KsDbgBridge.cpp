#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <ws2tcpip.h>
#include <dbgeng.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <thread>

#pragma comment(lib, "Dbgeng.lib")
#pragma comment(lib, "Ws2_32.lib")

struct Request { SOCKET socket; std::string action; };
static std::mutex queue_lock;
static std::queue<Request> pending;
static std::atomic<bool> quit(false);
static IDebugControl* control = nullptr;
static IDebugDataSpaces3* data = nullptr;
static IDebugSymbols* symbols = nullptr;
static IDebugClient* client = nullptr;
static std::atomic<int64_t> changed_at(0);
static std::array<unsigned char, 32> entry_original{};
static ULONG64 entry_address = 0, dispatch_original = 0, dispatch_replacement = 0, dispatch_slot = 0;
static bool initialized = false;
static std::atomic<bool> entry_changed(false), dispatch_changed(false);
static std::string restore_entry();
static std::string restore_dispatch();

static std::string hex64(ULONG64 number) {
    std::ostringstream stream; stream << "0x" << std::hex << number; return stream.str();
}
static void check(HRESULT hr, const char* where) {
    if (FAILED(hr)) throw std::runtime_error(std::string(where) + " HRESULT=" + hex64((unsigned)hr));
}
static ULONG64 symbol(const char* name) {
    ULONG64 value = 0; check(symbols->GetOffsetByName(name, &value), name); return value;
}
static ULONG64 evaluate(const char* expression) {
    DEBUG_VALUE value{}; ULONG remaining = 0;
    check(control->Evaluate(expression, DEBUG_VALUE_INT64, &value, &remaining), expression);
    if (!value.I64) throw std::runtime_error(std::string("Zero debugger expression: ") + expression);
    return value.I64;
}
static void read_exact(ULONG64 address, void* buffer, ULONG size) {
    ULONG count = 0; check(data->ReadVirtualUncached(address, buffer, size, &count), "ReadVirtualUncached");
    if (count != size) throw std::runtime_error("Short kernel read");
}
static void write_exact(ULONG64 address, const void* buffer, ULONG size) {
    ULONG count = 0; check(data->WriteVirtualUncached(address, const_cast<void*>(buffer), size, &count), "WriteVirtualUncached");
    if (count != size) throw std::runtime_error("Short kernel write");
}
static int64_t seconds_now() {
    return std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
static void require_clean_entry() {
    std::array<unsigned char, 32> current{}; read_exact(entry_address, current.data(), 32);
    if (current != entry_original) throw std::runtime_error("Entry differs from the captured original bytes");
}
static void require_clean_dispatch() {
    ULONG64 current = 0; read_exact(dispatch_slot, &current, 8);
    if (current != dispatch_original) throw std::runtime_error("CREATE pointer differs from baseline");
}
static void require_same_boot() {
    ULONG64 probe = 0; read_exact(symbol("KernelSentinel!g_ProbeAddresses") + 8, &probe, 8);
    if (probe != entry_address || symbol("nt!NtQuerySystemInformation") != entry_address ||
        symbol("KernelSentinel!KsDispatch") != dispatch_original ||
        symbol("KernelSentinel!KsOpenClose") != dispatch_replacement)
        throw std::runtime_error("Symbols or protected probe changed since bridge initialization");
}
static void initialize() {
    check(control->Execute(DEBUG_OUTCTL_IGNORE, ".reload /f nt; .reload /f KernelSentinel.sys", DEBUG_EXECUTE_DEFAULT), "Reload symbols");
    auto nt = symbol("nt!NtQuerySystemInformation");
    auto probes = symbol("KernelSentinel!g_ProbeAddresses");
    read_exact(probes + 8, &entry_address, 8);
    if (entry_address != nt) throw std::runtime_error("Sentinel second probe does not match NT symbol");
    read_exact(symbol("KernelSentinel!g_ProbeBaseline") + 32, entry_original.data(), 32);
    const unsigned char prefix[] = {0x40,0x53,0x48,0x83,0xec,0x30,0x45,0x33,0xd2};
    if (memcmp(entry_original.data(), prefix, sizeof(prefix)))
        throw std::runtime_error("NT entry bytes are not the reviewed equivalent-XOR sequence");
    dispatch_original = symbol("KernelSentinel!KsDispatch");
    dispatch_replacement = symbol("KernelSentinel!KsOpenClose");
    ULONG loaded_index = 0, first_index = 0, second_index = 0; ULONG64 base = 0;
    check(symbols->GetModuleByModuleName("KernelSentinel", 0, &loaded_index, &base), "Get KernelSentinel module");
    check(symbols->GetModuleByOffset(dispatch_original, 0, &first_index, nullptr), "Find KsDispatch module");
    check(symbols->GetModuleByOffset(dispatch_replacement, 0, &second_index, nullptr), "Find KsOpenClose module");
    if (loaded_index != first_index || loaded_index != second_index || dispatch_original == dispatch_replacement)
        throw std::runtime_error("Dispatch symbols do not belong to one loaded KernelSentinel module");
    check(control->Execute(DEBUG_OUTCTL_IGNORE, ".expr /s masm", DEBUG_EXECUTE_DEFAULT), "Set MASM expressions");
    check(control->Execute(DEBUG_OUTCTL_IGNORE,
        "r @$t8 = @@c++(&((KernelSentinel!g_Device)->DriverObject->MajorFunction[0]))",
        DEBUG_EXECUTE_DEFAULT), "Resolve typed CREATE slot");
    dispatch_slot = evaluate("@$t8");
    ULONG64 baselines[3]{}; read_exact(symbol("KernelSentinel!g_DispatchBaseline"), baselines, sizeof(baselines));
    if (baselines[0] != dispatch_original || baselines[1] != dispatch_original || baselines[2] != dispatch_original)
        throw std::runtime_error("Dispatch baseline is not the expected clean baseline");
    initialized = true;
    try {
    std::array<unsigned char, 32> actual_entry{}, changed_entry = entry_original;
    changed_entry[7] = 0x31;
    read_exact(entry_address, actual_entry.data(), 32);
    if (actual_entry == changed_entry) restore_entry();
    else if (actual_entry != entry_original) throw std::runtime_error("Unexpected NT entry bytes at bridge startup");
    ULONG64 actual_dispatch = 0; read_exact(dispatch_slot, &actual_dispatch, 8);
    if (actual_dispatch == dispatch_replacement) restore_dispatch();
    else if (actual_dispatch != dispatch_original) throw std::runtime_error("Unexpected CREATE pointer at bridge startup");
    require_clean_entry(); require_clean_dispatch();
    } catch (...) { initialized = false; throw; }
}
static std::string restore_entry() {
    if (!initialized) throw std::runtime_error("Bridge has not validated this boot");
    std::array<unsigned char, 32> expected = entry_original, actual{};
    expected[7] = 0x31;
    read_exact(entry_address, actual.data(), 32);
    if (actual == entry_original) { entry_changed = false; return "ALREADY_RESTORED"; }
    if (actual != expected) throw std::runtime_error("Entry no longer matches the one-byte mutation; no write performed");
    const unsigned char original = 0x33;
    write_exact(entry_address + 7, &original, 1);
    require_clean_entry(); entry_changed = false;
    return "RESTORED";
}
static std::string restore_dispatch() {
    if (!initialized) throw std::runtime_error("Bridge has not validated this boot");
    ULONG64 actual = 0; read_exact(dispatch_slot, &actual, 8);
    if (actual == dispatch_original) { dispatch_changed = false; return "ALREADY_RESTORED"; }
    if (actual != dispatch_replacement) throw std::runtime_error("CREATE slot has an unexpected pointer; no write performed");
    write_exact(dispatch_slot, &dispatch_original, 8);
    require_clean_dispatch(); dispatch_changed = false;
    return "RESTORED";
}
static std::string execute_action(const std::string& action) {
    if (action == "INFO") {
        if (!initialized) initialize();
        require_same_boot();
        if (entry_changed || dispatch_changed) throw std::runtime_error("Previous mutation still active");
        require_clean_entry(); require_clean_dispatch();
        return "entry=" + hex64(entry_address) + " dispatch=" + hex64(dispatch_original) +
               " replacement=" + hex64(dispatch_replacement);
    }
    if (!initialized) throw std::runtime_error("Call INFO first");
    require_same_boot();
    if (action == "ENTRY_MUTATE") {
        if (entry_changed || dispatch_changed) throw std::runtime_error("Another mutation is active");
        require_clean_entry(); require_clean_dispatch();
        const unsigned char changed = 0x31;
        write_exact(entry_address + 7, &changed, 1);
        std::array<unsigned char, 32> expected = entry_original, actual{};
        expected[7] = 0x31; read_exact(entry_address, actual.data(), 32);
        if (actual != expected) {
            try { restore_entry(); } catch (...) {}
            throw std::runtime_error("Entry write verification failed; rollback attempted");
        }
        entry_changed = true; changed_at = seconds_now(); return "ENTRY_CHANGED";
    }
    if (action == "ENTRY_RESTORE") return restore_entry();
    if (action == "DISPATCH_MUTATE") {
        if (entry_changed || dispatch_changed) throw std::runtime_error("Another mutation is active");
        require_clean_entry(); require_clean_dispatch();
        write_exact(dispatch_slot, &dispatch_replacement, 8);
        ULONG64 actual = 0; read_exact(dispatch_slot, &actual, 8);
        if (actual != dispatch_replacement) {
            try { restore_dispatch(); } catch (...) {}
            throw std::runtime_error("Dispatch write verification failed; rollback attempted");
        }
        dispatch_changed = true; changed_at = seconds_now(); return "DISPATCH_CHANGED";
    }
    if (action == "DISPATCH_RESTORE") return restore_dispatch();
    if (action == "AUTO_RESTORE") {
        std::string result;
        if (entry_changed) result += restore_entry();
        if (dispatch_changed) result += restore_dispatch();
        return result.empty() ? "CLEAN" : result;
    }
    throw std::runtime_error("Unknown bridge action");
}
static void serve(SOCKET listener, const std::string token) {
    while (!quit) {
        SOCKET peer = accept(listener, nullptr, nullptr);
        if (peer == INVALID_SOCKET) break;
        DWORD timeout = 10000; setsockopt(peer, SOL_SOCKET, SO_RCVTIMEO, (char*)&timeout, sizeof(timeout));
        char buffer[512]{}; int count = recv(peer, buffer, sizeof(buffer)-1, 0);
        if (count <= 0) { closesocket(peer); continue; }
        std::istringstream line(std::string(buffer, count));
        std::string sent_token, action; line >> sent_token >> action;
        if (sent_token != token || action.empty() || action.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ_") != std::string::npos) {
            send(peer, "ERROR authorization_or_action\n", 30, 0); closesocket(peer); continue;
        }
        { std::lock_guard<std::mutex> guard(queue_lock); pending.push({peer, action}); }
        control->SetInterrupt(DEBUG_INTERRUPT_ACTIVE);
    }
}
static void watchdog() {
    while (!quit) {
        Sleep(1000);
        if ((entry_changed || dispatch_changed) && seconds_now() - changed_at.load() > 180) {
            { std::lock_guard<std::mutex> guard(queue_lock); pending.push({INVALID_SOCKET, "AUTO_RESTORE"}); }
            control->SetInterrupt(DEBUG_INTERRUPT_ACTIVE);
        }
    }
}
int main(int argc, char** argv) {
    if (argc != 5) {
        std::cerr << "Usage: KsDbgBridge.exe <bind-VMnet8-IP> <TCP-port> <KDNET-key> <bridge-token>\n";
        return 2;
    }
    try {
        WSADATA wsa{}; if (WSAStartup(MAKEWORD(2,2), &wsa)) throw std::runtime_error("WSAStartup failed");
        SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener == INVALID_SOCKET) throw std::runtime_error("socket failed");
        sockaddr_in endpoint{}; endpoint.sin_family = AF_INET; endpoint.sin_port = htons((unsigned short)std::stoi(argv[2]));
        if (InetPtonA(AF_INET, argv[1], &endpoint.sin_addr) != 1) throw std::runtime_error("Invalid VMnet8 bind IP");
        if (bind(listener, (sockaddr*)&endpoint, sizeof(endpoint)) || listen(listener, 4))
            throw std::runtime_error("Bridge bind/listen failed; check VMnet8 IP and firewall");
        check(DebugCreate(__uuidof(IDebugClient), (void**)&client), "DebugCreate");
        check(client->QueryInterface(__uuidof(IDebugControl), (void**)&control), "IDebugControl");
        check(client->QueryInterface(__uuidof(IDebugDataSpaces3), (void**)&data), "IDebugDataSpaces3");
        check(client->QueryInterface(__uuidof(IDebugSymbols), (void**)&symbols), "IDebugSymbols");
        std::string path = "srv*";
        check(symbols->SetSymbolPath(path.c_str()), "SetSymbolPath");
        std::string connection = "net:port=50000,key=" + std::string(argv[3]);
        check(client->AttachKernel(DEBUG_ATTACH_KERNEL_CONNECTION, connection.c_str()), "AttachKernel");
        std::thread server(serve, listener, std::string(argv[4])); server.detach();
        std::thread timer(watchdog); timer.detach();
        control->SetInterrupt(DEBUG_INTERRUPT_ACTIVE);
        std::cout << "Bridge waiting for target; only fixed guarded test mutations are accepted.\n" << std::flush;
        bool first_event = true;
        while (!quit) {
            HRESULT waited = control->WaitForEvent(DEBUG_WAIT_DEFAULT, INFINITE);
            check(waited, "WaitForEvent");
            Request request{INVALID_SOCKET, ""};
            { std::lock_guard<std::mutex> guard(queue_lock);
              if (!pending.empty()) { request = pending.front(); pending.pop(); } }
            if (request.action.empty() && !first_event)
                throw std::runtime_error("Unexpected kernel break; bridge will not issue GO");
            first_event = false;
            std::string answer = "OK READY\n";
            if (!request.action.empty()) {
                try { answer = "OK " + execute_action(request.action) + "\n"; }
                catch (const std::exception& exc) { answer = std::string("ERROR ") + exc.what() + "\n"; }
                std::cout << request.action << ": " << answer << std::flush;
            }
            check(control->SetExecutionStatus(DEBUG_STATUS_GO), "SetExecutionStatus GO");
            if (request.socket != INVALID_SOCKET) {
                send(request.socket, answer.c_str(), (int)answer.size(), 0);
                closesocket(request.socket);
            }
        }
        closesocket(listener); WSACleanup();
    } catch (const std::exception& exc) {
        std::cerr << "Bridge halted: " << exc.what() << ". Inspect the debugger state before restarting.\n";
        return 1;
    }
    return 0;
}
