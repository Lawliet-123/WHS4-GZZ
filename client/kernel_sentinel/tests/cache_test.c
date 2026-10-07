#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t ULONG;
typedef uint64_t ULONGLONG;
typedef unsigned char UCHAR;
typedef unsigned char BOOLEAN;
typedef char CHAR;
#include "code_cache.h"
static KS_CODE_BASELINE entries[1024];
int main(void)
{
    char a[256], b[256];
    unsigned char current[32] = {1}, baseline[32];
    ULONG eviction = 0, i, pass;
    memset(a, 0x41, sizeof(a)); memset(b, 0x42, sizeof(b));
    strcpy(a, "\\SystemRoot\\drivers\\test.sys");
    strcpy(b, "\\SystemRoot\\drivers\\test.sys");
    assert(memcmp(a,b,256) != 0); /* Old fixed-buffer key rejects the same path. */
    assert(KsCodePathEqual(a,b));
    assert(KsUpdateCodeCache(entries,1024,&eviction,0x1000,4096,a,current,baseline) == 4);
    assert(KsUpdateCodeCache(entries,1024,&eviction,0x1000,4096,b,current,baseline) == 1);
    current[0] = 2;
    assert(KsUpdateCodeCache(entries,1024,&eviction,0x1000,4096,b,current,baseline) == 3);
    assert(baseline[0] == 1);
    assert(KsUpdateCodeCache(entries,1024,&eviction,0x1000,4096,b,current,baseline) == 3);
    entries[0].Valid = 0; /* Real image-load invalidation must still work. */
    assert(KsUpdateCodeCache(entries,1024,&eviction,0x1000,4096,b,current,baseline) == 4);
    memset(entries,0,sizeof(entries));
    for (pass=0; pass<3; ++pass) {
        for (i=0; i<180; ++i) {
            memset(a, (int)(0x41+pass), sizeof(a));
            snprintf(a, sizeof(a), "\\SystemRoot\\drivers\\module%u.sys", (unsigned)i);
            assert(KsUpdateCodeCache(entries,1024,&eviction,0x100000ULL+i*0x10000ULL,
                4096,a,current,baseline) == (pass == 0 ? 4UL : 1UL));
        }
    }
    strcpy(a,"A.sys"); strcpy(b,"a.SYS"); assert(KsCodePathEqual(a,b));
    strcpy(b,"other.sys"); assert(!KsCodePathEqual(a,b));
    memset(a,'a',256); memset(b,'a',256); assert(!KsCodePathEqual(a,b));
    return 0;
}
