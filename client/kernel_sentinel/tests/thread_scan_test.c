/* Exercises the real collector with API doubles. NOT a WDK/runtime test. */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <assert.h>
typedef uint32_t ULONG;
typedef uint64_t ULONGLONG;
typedef uint16_t WCHAR;
typedef unsigned char UCHAR;
typedef int32_t LONG, NTSTATUS;
typedef uintptr_t ULONG_PTR;
typedef void *PVOID, *HANDLE;
typedef void VOID;
typedef ULONG *PULONG;
typedef struct { int64_t QuadPart; } LARGE_INTEGER;
typedef struct { ULONG dwOSVersionInfoSize, dwMajorVersion, dwBuildNumber; } RTL_OSVERSIONINFOW;
typedef struct { const wchar_t* value; } UNICODE_STRING;
typedef struct { ULONG tid; } FAKE_THREAD, *PETHREAD;
#define NTAPI
#define C_ASSERT(x) _Static_assert((x), #x)
#define FIELD_OFFSET(t, f) offsetof(t, f)
#define NT_SUCCESS(x) ((NTSTATUS)(x) >= 0)
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_NOT_SUPPORTED ((NTSTATUS)0xc00000bb)
#define STATUS_DEVICE_BUSY ((NTSTATUS)0x80000011)
#define STATUS_BUFFER_OVERFLOW ((NTSTATUS)0x80000005)
#define STATUS_INSUFFICIENT_RESOURCES ((NTSTATUS)0xc000009a)
#define STATUS_INFO_LENGTH_MISMATCH ((NTSTATUS)0xc0000004)
#define STATUS_BUFFER_TOO_SMALL ((NTSTATUS)0xc0000023)
#define STATUS_DATA_ERROR ((NTSTATUS)0xc000003e)
#define STATUS_IO_TIMEOUT ((NTSTATUS)0xc00000b5)
#define STATUS_RETRY ((NTSTATUS)0xc000022d)
#define POOL_FLAG_PAGED 1
#define OBJ_KERNEL_HANDLE 1
/* Intentionally omit THREAD_QUERY_INFORMATION to exercise the header fallback. */
#define KernelMode 0
#define MAXULONG UINT32_MAX
#define max(a,b) ((a)>(b)?(a):(b))
#define RtlZeroMemory(p,n) memset((p),0,(n))
#define HandleToULong(h) ((ULONG)(uintptr_t)(h))
static int scenario, allocations, references, handles;
static ULONGLONG ticks;
static FAKE_THREAD fake_thread;
static PVOID thread_type;
static PVOID* PsThreadType = &thread_type;
static PVOID MmSystemRangeStart = (PVOID)(uintptr_t)0xffff800000000000ULL;
static LONG InterlockedCompareExchange(volatile LONG* p, LONG v, LONG expected)
{ LONG old=*p; if (old==expected) *p=v; return old; }
static LONG InterlockedExchange(volatile LONG* p, LONG v) { LONG old=*p; *p=v; return old; }
static ULONGLONG KeQueryInterruptTime(void) { ticks += scenario==6 ? 30000000 : 100; return ticks; }
static NTSTATUS RtlGetVersion(RTL_OSVERSIONINFOW* v)
{ v->dwMajorVersion=10; v->dwBuildNumber=19045; return 0; }
static void RtlInitUnicodeString(UNICODE_STRING* s, const wchar_t* value) { s->value=value; }
static PVOID ExAllocatePool2(ULONG flags, ULONG size, ULONG tag)
{ (void)flags;(void)tag; if(scenario==5)return NULL; ++allocations; return calloc(1,size); }
static void ExFreePoolWithTag(PVOID p, ULONG tag) { (void)tag; --allocations; free(p); }
static NTSTATUS PsLookupThreadByThreadId(HANDLE tid, PETHREAD* out)
{ if(scenario==7)return STATUS_RETRY; fake_thread.tid=HandleToULong(tid); *out=&fake_thread; ++references; return 0; }
static HANDLE PsGetThreadProcessId(PETHREAD t) { (void)t; return (HANDLE)(uintptr_t)4; }
static int PsIsSystemThread(PETHREAD t) { return t->tid==100; }
static void ObDereferenceObject(PETHREAD t) { (void)t; --references; }
static NTSTATUS ObOpenObjectByPointer(PETHREAD t, ULONG a, PVOID b, ULONG c, PVOID d, ULONG e, HANDLE* out)
{ (void)t;(void)a;(void)b;(void)c;(void)d;(void)e; if(scenario==8)return STATUS_RETRY; ++handles;*out=(HANDLE)(uintptr_t)1;return 0; }
static void ZwClose(HANDLE h) { (void)h; --handles; }
#include "protocol.h"
#include "thread_layout.h"
static NTSTATUS QuerySystem(ULONG c, PVOID p, ULONG length, PULONG needed)
{
    KS_NATIVE_PROCESS* proc=p;
    KS_NATIVE_THREAD* ts=(KS_NATIVE_THREAD*)(proc+1);
    assert(c==5 && length>=416);
    *needed=416;
    proc->Count=2;proc->Pid=4;
    ts[0].Pid=ts[1].Pid=4;ts[0].Tid=100;ts[1].Tid=101;
    ts[0].CreateTime=ts[1].CreateTime=123;
    ts[0].SnapshotStart=0xfffff80000001000ULL;
    if(scenario==1)proc->Count=UINT32_MAX;
    if(scenario==9)*needed=length+1;
    return 0;
}
static NTSTATUS QueryThread(HANDLE h, ULONG c, PVOID p, ULONG size, PULONG needed)
{
    (void)h;(void)needed;
    if(c==1){
        assert(size==32);((LARGE_INTEGER*)p)[0].QuadPart=scenario==2?456:123;
        return 0;
    }
    assert(c==9 && size==8);
    if(scenario==3)return STATUS_NOT_SUPPORTED;
    *(PVOID*)p=(PVOID)(uintptr_t)(scenario==4?0:0xfffff80000002000ULL);
    return 0;
}
static PVOID MmGetSystemRoutineAddress(UNICODE_STRING* s)
{ return wcscmp(s->value,L"ZwQuerySystemInformation")==0 ? (PVOID)QuerySystem : (PVOID)QueryThread; }
#include "thread_scan.h"
int main(void)
{
    KS_THREAD_RESULT* out=calloc(1,sizeof(*out));
    KsInitializeThreads();
    for(scenario=0;scenario<=9;++scenario){
        ticks=0;KsCollectThreads(out);
        assert(allocations==0 && references==0 && handles==0 && g_ThreadScanBusy==0);
        if(scenario==0){
            assert(out->Status==0 && out->Count==1 && out->Enumerated==2);
            assert(out->Threads[0].Start==0xfffff80000002000ULL && out->Threads[0].Status==0);
        }
        if(scenario==1 || scenario==9)assert(out->Status==(ULONG)STATUS_DATA_ERROR);
        if(scenario==2 || scenario==3 || scenario==4 || scenario==8)
            assert(out->Status==0 && out->Threads[0].Status!=0 && out->Threads[0].Start==0);
        if(scenario==5)assert(out->Status==(ULONG)STATUS_INSUFFICIENT_RESOURCES);
        if(scenario==6)assert(out->Status==(ULONG)STATUS_IO_TIMEOUT && out->Flags==1);
        if(scenario==7)assert(out->Status==0 && out->Count==0 && out->LookupFailed==2);
    }
    scenario=0;g_ThreadScanBusy=1;KsCollectThreads(out);
    assert(out->Status==(ULONG)STATUS_DEVICE_BUSY);g_ThreadScanBusy=0;
    g_QueryThread=NULL;KsCollectThreads(out);
    assert(out->Status==(ULONG)STATUS_NOT_SUPPORTED);
    free(out);return 0;
}
