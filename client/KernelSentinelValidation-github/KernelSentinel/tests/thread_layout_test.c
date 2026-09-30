#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
typedef uint32_t ULONG;
typedef uint64_t ULONGLONG;
typedef uint16_t WCHAR;
typedef unsigned char UCHAR;
#define C_ASSERT(x) _Static_assert((x), #x)
#define FIELD_OFFSET(t, f) offsetof(t, f)
#include "protocol.h"
#include "thread_layout.h"
int main(void)
{
    KS_NATIVE_PROCESS p;
    memset(&p, 0, sizeof(p));
    assert(KsNativeSpan(&p, 255) == 0);
    assert(KsNativeSpan(&p, 256) == 256);
    p.Count = 2;
    assert(KsNativeSpan(&p, 416) == 416);
    assert(KsNativeSpan(&p, 415) == 0);
    p.Count = UINT32_MAX;
    assert(KsNativeSpan(&p, 4096) == 0);
    p.Count = 1; p.Next = 336;
    assert(KsNativeSpan(&p, 4096) == 336);
    p.Next = 337; assert(KsNativeSpan(&p, 4096) == 0);
    p.Next = 256; assert(KsNativeSpan(&p, 4096) == 0);
    p.Next = 8192; assert(KsNativeSpan(&p, 4096) == 0);
    assert(sizeof(KS_THREAD_RESULT) == 163872);
    assert(offsetof(KS_THREAD_RECORD, CreateTime) == 16);
    assert(offsetof(KS_THREAD_RECORD, Start) == 24);
    return 0;
}
