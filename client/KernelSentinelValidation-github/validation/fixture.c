#include <windows.h>
__declspec(dllexport) unsigned long KsValidationMarker(void) { return 0x4b535654; }
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance; (void)reason; (void)reserved;
    return TRUE;
}
