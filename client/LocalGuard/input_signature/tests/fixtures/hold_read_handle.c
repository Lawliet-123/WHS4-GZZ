#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

static volatile char signature[] = "MECCHA_NATIVE_READ_HANDLE_FIXTURE_20260927";

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    if (signature[0] != 'M') return 5;
    DWORD target_pid = (DWORD)strtoul(argv[1], NULL, 10);
    HANDLE target = OpenProcess(PROCESS_VM_READ, FALSE, target_pid);
    if (!target) return 3;
    FILE *ready = fopen(argv[2], "w");
    if (!ready) {
        CloseHandle(target);
        return 4;
    }
    fprintf(ready, "%lu\n", (unsigned long)GetCurrentProcessId());
    fclose(ready);
    Sleep(20000);
    CloseHandle(target);
    return 0;
}
