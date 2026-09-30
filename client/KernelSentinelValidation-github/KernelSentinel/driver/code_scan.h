#pragma once
#include <ntimage.h>
#include <bcrypt.h>
#pragma comment(lib, "Cng.lib")
/* Incremental read-only SHA-256 of non-discardable executable PE sections.
   First successful scan is a local baseline, NOT a known-good baseline. */
#define KS_IOCTL_CODE CTL_CODE(KS_DEVICE_TYPE, 0x805, METHOD_BUFFERED, FILE_READ_DATA)
typedef struct {
    ULONG Version, Status, Flags, ModuleCount;
    ULONGLONG Base;
    ULONG Size, BytesHashed;
    UCHAR Baseline[32], Current[32];
    CHAR Path[256];
} KS_CODE_RESULT;
C_ASSERT(sizeof(KS_CODE_RESULT) == 352);
#include "code_cache.h"
static KS_CODE_BASELINE g_CodeBaselines[1024];
static KSPIN_LOCK g_CodeLock;
static KMUTEX g_CodeMutex;
static ULONGLONG g_CodeEpoch;
static ULONG g_CodeCursor;
static ULONG g_CodeEvictionCursor;
static BCRYPT_ALG_HANDLE g_HashProvider;
static NTSTATUS g_HashStatus;

static VOID KsInitializeCode(VOID)
{
    KeInitializeSpinLock(&g_CodeLock);
    KeInitializeMutex(&g_CodeMutex, 0);
    g_HashStatus = BCryptOpenAlgorithmProvider(&g_HashProvider, BCRYPT_SHA256_ALGORITHM, NULL, 0);
}
static VOID KsInvalidateCodeBase(PVOID Base)
{
    ULONG i;
    KIRQL irql;
    KeAcquireSpinLock(&g_CodeLock, &irql);
    ++g_CodeEpoch;
    for (i = 0; i < 1024; ++i)
        if (g_CodeBaselines[i].Base == (ULONGLONG)(ULONG_PTR)Base) g_CodeBaselines[i].Valid = FALSE;
    KeReleaseSpinLock(&g_CodeLock, irql);
}
static NTSTATUS KsCopyCode(PVOID Base, ULONG ImageSize, ULONG Offset, ULONG Size, UCHAR* Output)
{
    MM_COPY_ADDRESS source;
    SIZE_T copied = 0;
    NTSTATUS status;
    if (Offset > ImageSize || Size > ImageSize - Offset) return STATUS_INVALID_IMAGE_FORMAT;
    source.VirtualAddress = (PUCHAR)Base + Offset;
    status = MmCopyMemory(Output, source, Size, MM_COPY_MEMORY_VIRTUAL, &copied);
    return NT_SUCCESS(status) && copied != Size ? STATUS_PARTIAL_COPY : status;
}
static NTSTATUS KsHashImage(PVOID Base, ULONG Size, KS_CODE_RESULT* Out)
{
    UCHAR* scratch = NULL;
    UCHAR* hashObject = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    ULONG objectSize, returned, ntOffset, tableOffset, sectionCount, i, j, length, total = 0;
    IMAGE_NT_HEADERS64 nt;
    PIMAGE_SECTION_HEADER sections;
    NTSTATUS status = g_HashStatus;
    if (!NT_SUCCESS(status)) return status;
    scratch = ExAllocatePool2(POOL_FLAG_NON_PAGED, 65536 + 96 * sizeof(IMAGE_SECTION_HEADER), 'cSsK');
    if (scratch == NULL) return STATUS_INSUFFICIENT_RESOURCES;
    sections = (PIMAGE_SECTION_HEADER)(scratch + 65536);
    status = KsCopyCode(Base, Size, 0, sizeof(IMAGE_DOS_HEADER), scratch);
    if (!NT_SUCCESS(status)) goto done;
    if (((PIMAGE_DOS_HEADER)scratch)->e_magic != IMAGE_DOS_SIGNATURE ||
        ((PIMAGE_DOS_HEADER)scratch)->e_lfanew < 0) { status = STATUS_INVALID_IMAGE_FORMAT; goto done; }
    ntOffset = (ULONG)((PIMAGE_DOS_HEADER)scratch)->e_lfanew;
    status = KsCopyCode(Base, Size, ntOffset, sizeof(nt), scratch);
    if (!NT_SUCCESS(status)) goto done;
    RtlCopyMemory(&nt, scratch, sizeof(nt));
    sectionCount = nt.FileHeader.NumberOfSections;
    if (nt.Signature != IMAGE_NT_SIGNATURE || nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 || sectionCount == 0 || sectionCount > 96 ||
        nt.FileHeader.SizeOfOptionalHeader < sizeof(IMAGE_OPTIONAL_HEADER64) ||
        ntOffset > MAXULONG - 24UL - nt.FileHeader.SizeOfOptionalHeader) {
        status = STATUS_INVALID_IMAGE_FORMAT; goto done;
    }
    tableOffset = ntOffset + 24UL + nt.FileHeader.SizeOfOptionalHeader;
    status = KsCopyCode(Base, Size, tableOffset, sectionCount * sizeof(IMAGE_SECTION_HEADER), scratch);
    if (!NT_SUCCESS(status)) goto done;
    RtlCopyMemory(sections, scratch, sectionCount * sizeof(IMAGE_SECTION_HEADER));
    status = BCryptGetProperty(g_HashProvider, BCRYPT_OBJECT_LENGTH, (PUCHAR)&objectSize,
        sizeof(objectSize), &returned, 0);
    if (!NT_SUCCESS(status)) goto done;
    hashObject = ExAllocatePool2(POOL_FLAG_NON_PAGED, objectSize, 'hSsK');
    if (hashObject == NULL) { status = STATUS_INSUFFICIENT_RESOURCES; goto done; }
    status = BCryptCreateHash(g_HashProvider, &hash, hashObject, objectSize, NULL, 0, 0);
    if (!NT_SUCCESS(status)) goto done;
    /* Include section descriptors to avoid accepting a changed section layout. */
    status = BCryptHashData(hash, (PUCHAR)sections, sectionCount * sizeof(IMAGE_SECTION_HEADER), 0);
    if (!NT_SUCCESS(status)) goto done;
    for (i = 0; i < sectionCount; ++i) {
        if (!(sections[i].Characteristics & IMAGE_SCN_MEM_EXECUTE) ||
            (sections[i].Characteristics & IMAGE_SCN_MEM_DISCARDABLE)) continue;
        length = sections[i].Misc.VirtualSize;
        if (length == 0) continue;
        if (sections[i].VirtualAddress > Size || length > Size - sections[i].VirtualAddress ||
            length > 16UL*1024*1024 - total) { status = STATUS_BUFFER_OVERFLOW; goto done; }
        for (j = 0; j < length;) {
            ULONG chunk = min(length - j, 65536UL);
            status = KsCopyCode(Base, Size, sections[i].VirtualAddress+j, chunk, scratch);
            if (!NT_SUCCESS(status)) goto done;
            status = BCryptHashData(hash, scratch, chunk, 0);
            if (!NT_SUCCESS(status)) goto done;
            j += chunk;
        }
        total += length;
    }
    if (total == 0) { status = STATUS_NOT_FOUND; goto done; }
    status = BCryptFinishHash(hash, Out->Current, 32, 0);
    if (NT_SUCCESS(status)) Out->BytesHashed = total;
done:
    if (hash != NULL) BCryptDestroyHash(hash);
    if (hashObject != NULL) ExFreePoolWithTag(hashObject, 'hSsK');
    ExFreePoolWithTag(scratch, 'cSsK');
    return status;
}
static VOID KsScanCode(KS_CODE_RESULT* Out)
{
    PAUX_MODULE_EXTENDED_INFO modules = NULL;
    AUX_MODULE_EXTENDED_INFO chosen;
    ULONG bytes = 0, allocated, count, i;
    NTSTATUS status;
    ULONGLONG epoch;
    KIRQL irql;
    BOOLEAN present = FALSE;
    RtlZeroMemory(Out, sizeof(*Out)); Out->Version = 1;
    KeWaitForSingleObject(&g_CodeMutex, Executive, KernelMode, FALSE, NULL);
    status = g_AuxStatus;
    if (!NT_SUCCESS(status)) goto done;
    status = AuxKlibQueryModuleInformation(&bytes, sizeof(*modules), NULL);
    if (!NT_SUCCESS(status)) goto done;
    if (bytes == 0 || bytes > 1024 * sizeof(*modules)) { status = STATUS_BUFFER_OVERFLOW; goto done; }
    allocated = bytes;
    modules = ExAllocatePool2(POOL_FLAG_PAGED, bytes, 'lSsK');
    if (modules == NULL) { status = STATUS_INSUFFICIENT_RESOURCES; goto done; }
    status = AuxKlibQueryModuleInformation(&bytes, sizeof(*modules), modules);
    if (!NT_SUCCESS(status)) goto done;
    if (bytes > allocated || bytes == 0 || bytes % sizeof(*modules) != 0) { status = STATUS_DATA_ERROR; goto done; }
    count = bytes / sizeof(*modules); Out->ModuleCount = count;
    chosen = modules[g_CodeCursor++ % count];
    Out->Base = (ULONGLONG)(ULONG_PTR)chosen.BasicInfo.ImageBase;
    Out->Size = chosen.ImageSize;
    KsCopyCodePath(Out->Path, (const CHAR*)chosen.FullPathName);
    KeAcquireSpinLock(&g_CodeLock, &irql); epoch = g_CodeEpoch; KeReleaseSpinLock(&g_CodeLock, irql);
    status = KsHashImage(chosen.BasicInfo.ImageBase, chosen.ImageSize, Out);
    if (!NT_SUCCESS(status)) goto done;
    bytes = allocated;
    status = AuxKlibQueryModuleInformation(&bytes, sizeof(*modules), modules);
    if (!NT_SUCCESS(status)) goto done;
    if (bytes > allocated || bytes % sizeof(*modules) != 0) { status = STATUS_DATA_ERROR; goto done; }
    for (i = 0; i < bytes / sizeof(*modules); ++i) {
        if (modules[i].BasicInfo.ImageBase == chosen.BasicInfo.ImageBase && modules[i].ImageSize == chosen.ImageSize &&
            KsCodePathEqual((const CHAR*)modules[i].FullPathName, (const CHAR*)chosen.FullPathName)) {
            present = TRUE; break;
        }
    }
    if (!present) { status = STATUS_RETRY; goto done; }
    KeAcquireSpinLock(&g_CodeLock, &irql);
    if (epoch != g_CodeEpoch) {
        KeReleaseSpinLock(&g_CodeLock, irql); status = STATUS_RETRY; goto done;
    }
    Out->Flags = KsUpdateCodeCache(g_CodeBaselines, 1024, &g_CodeEvictionCursor,
        Out->Base, Out->Size, Out->Path, Out->Current, Out->Baseline);
    KeReleaseSpinLock(&g_CodeLock, irql);
done:
    Out->Status = (ULONG)status;
    if (modules != NULL) ExFreePoolWithTag(modules, 'lSsK');
    KeReleaseMutex(&g_CodeMutex, FALSE);
}
