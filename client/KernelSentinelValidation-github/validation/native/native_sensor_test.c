/* Executes UNMODIFIED integrity.h and code_scan.h against owned buffers and
 * deterministic OS API doubles. Uses real user-mode BCrypt SHA-256.
 * This proves algorithm behavior, NOT kernel-mode runtime acquisition. */
#include <windows.h>
#include <winternl.h>
#include <bcrypt.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

#define KS_DEVICE_TYPE 0x8337
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_NOT_FOUND ((NTSTATUS)0xc0000225)
#define STATUS_PARTIAL_COPY ((NTSTATUS)0x8000000d)
#define STATUS_BUFFER_OVERFLOW ((NTSTATUS)0x80000005)
#define STATUS_INSUFFICIENT_RESOURCES ((NTSTATUS)0xc000009a)
#define STATUS_BUFFER_TOO_SMALL ((NTSTATUS)0xc0000023)
#define STATUS_DATA_ERROR ((NTSTATUS)0xc000003e)
#define STATUS_INVALID_IMAGE_FORMAT ((NTSTATUS)0xc000007b)
#define STATUS_RETRY ((NTSTATUS)0xc000022d)
#define POOL_FLAG_PAGED 1
#define POOL_FLAG_NON_PAGED 2
#define MM_COPY_MEMORY_VIRTUAL 1
#define IRP_MJ_CREATE 0
#define IRP_MJ_CLOSE 2
#define IRP_MJ_DEVICE_CONTROL 14
#define Executive 0
#define KernelMode 0
#define MAXULONG 0xFFFFFFFFUL
typedef ULONG KIRQL, KMUTEX;
typedef void (*PDRIVER_DISPATCH)(void);
typedef struct { PDRIVER_DISPATCH MajorFunction[28]; } DRIVER_OBJECT, *PDRIVER_OBJECT;
typedef struct { PVOID VirtualAddress; } MM_COPY_ADDRESS;
static int fail_read, partial_read, fail_alloc, allocations, routine_index;
static int module_mode;
static UCHAR probes[4][64];
static UCHAR image[16384];
static SIZE_T test_compare(const void *a, const void *b, SIZE_T n)
{ SIZE_T i; for(i=0;i<n && ((const UCHAR*)a)[i]==((const UCHAR*)b)[i];++i) {} return i; }
#define RtlCompareMemory test_compare
static PVOID test_alloc(ULONG flags, SIZE_T size, ULONG tag)
{ PVOID p; (void)flags;(void)tag; if(fail_alloc)return NULL; p=calloc(1,size); if(p)++allocations; return p; }
static void test_free(PVOID p, ULONG tag) { (void)tag; if(p){--allocations;free(p);} }
#define ExAllocatePool2 test_alloc
#define ExFreePoolWithTag test_free
static void KeInitializeSpinLock(KSPIN_LOCK *x){*x=0;}
static void KeInitializeMutex(KMUTEX *x, ULONG y){*x=y;}
static void KeAcquireSpinLock(KSPIN_LOCK *x,KIRQL *y){(void)x;*y=0;}
static void KeReleaseSpinLock(KSPIN_LOCK *x,KIRQL y){(void)x;(void)y;}
static void KeWaitForSingleObject(KMUTEX *a,int b,int c,int d,void *e)
{(void)a;(void)b;(void)c;(void)d;(void)e;}
static void KeReleaseMutex(KMUTEX *a,int b){(void)a;(void)b;}
static NTSTATUS MmCopyMemory(void *out, MM_COPY_ADDRESS in, SIZE_T n, ULONG f, SIZE_T *copied)
{
    (void)f;
    if(fail_read){*copied=0;return STATUS_PARTIAL_COPY;}
    *copied=partial_read && n ? n-1 : n;
    memcpy(out,in.VirtualAddress,*copied);return STATUS_SUCCESS;
}
static void test_init_unicode(PUNICODE_STRING out,PCWSTR value)
{out->Buffer=(PWSTR)value;out->Length=(USHORT)(wcslen(value)*2);out->MaximumLength=out->Length+2;}
#define RtlInitUnicodeString test_init_unicode
static PVOID MmGetSystemRoutineAddress(PUNICODE_STRING name)
{(void)name;return probes[(routine_index++)%4];}
#include "aux_klib.h"
static NTSTATUS AuxKlibInitialize(void){return STATUS_SUCCESS;}
static NTSTATUS AuxKlibQueryModuleInformation(PULONG bytes,ULONG element,PVOID out)
{
    PAUX_MODULE_EXTENDED_INFO m=out;
    assert(element==sizeof(*m));
    if(module_mode==1)return STATUS_DATA_ERROR;
    if(!out){*bytes=sizeof(*m);return STATUS_SUCCESS;}
    if(*bytes<sizeof(*m)){*bytes=sizeof(*m);return STATUS_BUFFER_TOO_SMALL;}
    memset(m,0,sizeof(*m));
    m->BasicInfo.ImageBase=image;m->ImageSize=sizeof(image);
    memcpy(m->FullPathName,"\\SystemRoot\\fixture.sys",24);
    *bytes=sizeof(*m);return STATUS_SUCCESS;
}

#include "integrity.h"
#include "code_scan.h"

static unsigned checks;
#define CHECK(x) do { if(!(x)){fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);exit(1);}++checks; } while(0)

static void test_integrity(void)
{
    DRIVER_OBJECT driver={0};
    KW_DIAGNOSTICS *d=calloc(1,sizeof(*d));
    ULONG i;
    for(i=0;i<28;++i)driver.MajorFunction[i]=(PDRIVER_DISPATCH)(image+1024);
    memset(probes,0x90,sizeof(probes));
    KwInitializeDiagnostics(&driver);
    KwCollectDiagnostics(&driver,d);
    CHECK(d->ProbeValidMask==15 && d->ProbeChangedMask==0 && d->ProbeReadFailedMask==0);
    CHECK(d->DispatchChangedMask==0 && d->Count==1 && d->ModuleStatus==0);
    for(i=0;i<4;++i){
        probes[i][31]^=1;
        KwCollectDiagnostics(&driver,d);
        CHECK(d->ProbeChangedMask==(1UL<<i));
        probes[i][31]^=1;
        KwCollectDiagnostics(&driver,d);CHECK(d->ProbeChangedMask==0);
        probes[i][32]^=1;
        KwCollectDiagnostics(&driver,d);CHECK(d->ProbeChangedMask==0);
        probes[i][32]^=1;
    }
    partial_read=1;KwCollectDiagnostics(&driver,d);
    CHECK(d->ProbeReadFailedMask==15 && d->ProbeChangedMask==0);partial_read=0;
    fail_read=1;KwCollectDiagnostics(&driver,d);
    CHECK(d->ProbeReadFailedMask==15 && d->ProbeChangedMask==0);fail_read=0;
    for(i=0;i<3;++i){
        driver.MajorFunction[g_DispatchIndices[i]]=(PDRIVER_DISPATCH)(image+2048);
        KwCollectDiagnostics(&driver,d);CHECK(d->DispatchChangedMask==(1UL<<i));
        driver.MajorFunction[g_DispatchIndices[i]]=(PDRIVER_DISPATCH)(image+1024);
        KwCollectDiagnostics(&driver,d);CHECK(d->DispatchChangedMask==0);
    }
    module_mode=1;KwCollectDiagnostics(&driver,d);
    CHECK(d->ModuleStatus!=0 && d->Count==0);module_mode=0;
    free(d);CHECK(allocations==0);
}

static void make_image(void)
{
    IMAGE_DOS_HEADER *dos=(IMAGE_DOS_HEADER*)image;
    IMAGE_NT_HEADERS64 *nt=(IMAGE_NT_HEADERS64*)(image+0x100);
    IMAGE_SECTION_HEADER *s=(IMAGE_SECTION_HEADER*)(image+0x100+sizeof(*nt));
    memset(image,0,sizeof(image));dos->e_magic=IMAGE_DOS_SIGNATURE;dos->e_lfanew=0x100;
    nt->Signature=IMAGE_NT_SIGNATURE;nt->FileHeader.Machine=IMAGE_FILE_MACHINE_AMD64;
    nt->FileHeader.NumberOfSections=3;nt->FileHeader.SizeOfOptionalHeader=sizeof(IMAGE_OPTIONAL_HEADER64);
    nt->OptionalHeader.Magic=IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    memcpy(s[0].Name,".text",5);s[0].VirtualAddress=0x1000;s[0].Misc.VirtualSize=512;
    s[0].Characteristics=IMAGE_SCN_MEM_EXECUTE|IMAGE_SCN_MEM_READ;
    memcpy(s[1].Name,".data",5);s[1].VirtualAddress=0x2000;s[1].Misc.VirtualSize=512;
    s[1].Characteristics=IMAGE_SCN_MEM_READ|IMAGE_SCN_MEM_WRITE;
    memcpy(s[2].Name,".INIT",5);s[2].VirtualAddress=0x3000;s[2].Misc.VirtualSize=512;
    s[2].Characteristics=IMAGE_SCN_MEM_EXECUTE|IMAGE_SCN_MEM_DISCARDABLE;
    memset(image+0x1000,0x90,512);
}

static void test_code(void)
{
    KS_CODE_RESULT r;
    UCHAR original[32];
    IMAGE_NT_HEADERS64 *nt=(IMAGE_NT_HEADERS64*)(image+0x100);
    IMAGE_SECTION_HEADER *s=(IMAGE_SECTION_HEADER*)(image+0x100+sizeof(*nt));
    make_image();KsInitializeCode();CHECK(NT_SUCCESS(g_HashStatus));
    KsScanCode(&r);CHECK(r.Status==0 && r.Flags==4 && r.BytesHashed==512);
    memcpy(original,r.Current,32);
    KsScanCode(&r);CHECK(r.Status==0 && r.Flags==1);
    image[0x1000+17]^=1;KsScanCode(&r);
    CHECK(r.Status==0 && r.Flags==3 && memcmp(r.Baseline,original,32)==0);
    CHECK(memcmp(r.Current,original,32)!=0);
    KsScanCode(&r);CHECK(r.Flags==3); /* changed bytes do not replace the original baseline */
    image[0x1000+17]^=1;KsScanCode(&r);CHECK(r.Status==0 && r.Flags==1);
    image[0x2000]^=1;image[0x3000]^=1;KsScanCode(&r);
    CHECK(r.Status==0 && r.Flags==1); /* data/discardable code are outside content coverage */
    s[1].Name[0]^=1;KsScanCode(&r);CHECK(r.Status==0 && r.Flags==3);
    s[1].Name[0]^=1;KsScanCode(&r);CHECK(r.Status==0 && r.Flags==1);
    fail_read=1;KsScanCode(&r);CHECK(r.Status!=0 && r.Flags==0);fail_read=0;
    fail_alloc=1;KsScanCode(&r);CHECK(r.Status!=0 && r.Flags==0);fail_alloc=0;
    s[0].Misc.VirtualSize=sizeof(image);KsScanCode(&r);
    CHECK(r.Status!=0 && r.Flags==0);s[0].Misc.VirtualSize=512;
    nt->FileHeader.NumberOfSections=97;KsScanCode(&r);
    CHECK(r.Status!=0 && r.Flags==0);nt->FileHeader.NumberOfSections=3;
    image[0x1000]^=1;KsInvalidateCodeBase(image);KsScanCode(&r);
    CHECK(r.Status==0 && r.Flags==4); /* pre-baseline mutation is not detectable */
    KsScanCode(&r);CHECK(r.Status==0 && r.Flags==1);
    BCryptCloseAlgorithmProvider(g_HashProvider,0);g_HashProvider=NULL;
    CHECK(allocations==0);
}

int main(void)
{
    CHECK(sizeof(void*)==8);
    test_integrity();test_code();
    printf("native_sensor_test: %u checks passed; actual headers, OS API doubles, real BCrypt SHA-256\n",checks);
    return 0;
}
