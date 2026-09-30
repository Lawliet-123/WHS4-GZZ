/* 테스트 전용 프로세스: 지정 PID에 PROCESS_VM_READ 핸들을 잠시 유지한다.
 * 게임 값을 바꾸거나 DLL을 주입하지 않는다. 실패 코드는 테스트가 접근 거부,
 * 준비 파일 생성 실패 등을 구분하도록 한다. 인자는 PID와 준비 알림 파일이다.
 */
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
    /* 검증 프로세스가 핸들 존재를 관찰할 시간을 준 뒤 정상 해제한다. */
    Sleep(20000);
    CloseHandle(target);
    return 0;
}
