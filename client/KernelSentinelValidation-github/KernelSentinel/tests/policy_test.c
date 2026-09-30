#include <stdint.h>
#include <stddef.h>
#include <assert.h>
typedef uint32_t ULONG;
typedef uint64_t ULONGLONG;
typedef uint16_t WCHAR;
#define C_ASSERT(x) _Static_assert((x), #x)
#define FIELD_OFFSET(t, f) offsetof(t, f)
#include "protocol.h"
#include "access_policy.h"
int main(void)
{
    unsigned long access = 0x1fffffUL;
    int thread, enforce, lease, kernel, self;
    for (thread=0; thread<2; ++thread)
    for (enforce=0; enforce<2; ++enforce)
    for (lease=0; lease<2; ++lease)
    for (kernel=0; kernel<2; ++kernel)
    for (self=0; self<2; ++self) {
        unsigned long actual = KsRestrictAccess(access,thread,enforce,lease,kernel,self);
        if (!enforce || !lease || kernel || self) assert(actual == access);
        else {
            unsigned long mask = thread ? KS_THREAD_STRIP_MASK : KS_PROCESS_STRIP_MASK;
            assert((actual & mask) == 0);
            assert((actual | mask) == access);
        }
    }
    assert(KsRestrictAccess(0x10,0,1,1,0,0) == 0x10); /* read-only preserved */
    assert(KsRestrictAccess(0x28,0,1,1,0,0) == 0); /* VM_WRITE | VM_OPERATION */
    assert(KsRestrictAccess(0x1000,0,1,1,0,0) == 0x1000); /* query retained */
    assert(sizeof(KS_EVENT) == 616);
    assert(offsetof(KS_EVENT, Path) == 96);
    assert(sizeof(KS_STATUS) == 48);
    assert(sizeof(KS_POLICY_REQUEST) == 24);
    return 0;
}
