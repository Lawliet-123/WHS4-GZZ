#pragma once
#include <aux_klib.h>
#pragma comment(lib, "Aux_Klib.lib")
/* Fixed read-only probes. No IOCTL accepts a kernel address from the caller. */
#define KW_DIAG_PROBES 4
#define KW_DIAG_MODULES 1024
typedef struct {
    ULONGLONG Address;
    ULONG ReadStatus, Reserved;
    UCHAR Baseline[32], Current[32];
} KS_PROBE;
typedef struct { ULONGLONG Baseline, Current; } KS_DISPATCH;
typedef struct {
    ULONGLONG Base;
    ULONG Size;
    CHAR Path[256];
    ULONG Reserved;
} KW_MODULE_RECORD;
typedef struct {
    ULONG Version, Count, ModuleStatus, ProbeValidMask;
    ULONG ProbeChangedMask, ProbeReadFailedMask, DispatchChangedMask, Reserved;
    KS_PROBE Probes[4];
    KS_DISPATCH Dispatch[3];
    KW_MODULE_RECORD Modules[KW_DIAG_MODULES];
} KW_DIAGNOSTICS;
C_ASSERT(sizeof(KS_PROBE) == 80);
C_ASSERT(sizeof(KW_MODULE_RECORD) == 272);
C_ASSERT(FIELD_OFFSET(KW_DIAGNOSTICS, Modules) == 400);
C_ASSERT(sizeof(KW_DIAGNOSTICS) == 278928);
static PVOID g_ProbeAddresses[4];
static UCHAR g_ProbeBaseline[4][32];
static ULONG g_ProbeValid;
static NTSTATUS g_AuxStatus;
static PDRIVER_DISPATCH g_DispatchBaseline[3];
static const ULONG g_DispatchIndices[3] = {IRP_MJ_CREATE, IRP_MJ_CLOSE, IRP_MJ_DEVICE_CONTROL};

static NTSTATUS KwReadProbe(PVOID Address, UCHAR* Output)
{
    MM_COPY_ADDRESS source;
    SIZE_T copied = 0;
    NTSTATUS status;
    if (Address == NULL) return STATUS_NOT_FOUND;
    source.VirtualAddress = Address;
    status = MmCopyMemory(Output, source, 32, MM_COPY_MEMORY_VIRTUAL, &copied);
    if (NT_SUCCESS(status) && copied != 32) return STATUS_PARTIAL_COPY;
    return status;
}
static VOID KwInitializeDiagnostics(PDRIVER_OBJECT DriverObject)
{
    static const PCWSTR names[4] = {L"NtOpenProcess", L"NtQuerySystemInformation",
        L"MmCopyVirtualMemory", L"PsLookupProcessByProcessId"};
    ULONG i;
    UNICODE_STRING name;
    g_AuxStatus = AuxKlibInitialize();
    for (i = 0; i < 4; ++i) {
        RtlInitUnicodeString(&name, names[i]);
        g_ProbeAddresses[i] = MmGetSystemRoutineAddress(&name);
        if (NT_SUCCESS(KwReadProbe(g_ProbeAddresses[i], g_ProbeBaseline[i])))
            g_ProbeValid |= 1UL << i;
    }
    for (i = 0; i < 3; ++i)
        g_DispatchBaseline[i] = DriverObject->MajorFunction[g_DispatchIndices[i]];
}
static VOID KwCollectDiagnostics(PDRIVER_OBJECT DriverObject, KW_DIAGNOSTICS* Out)
{
    ULONG i, bytes, allocated, attempt;
    NTSTATUS status = g_AuxStatus;
    PAUX_MODULE_EXTENDED_INFO modules = NULL;
    RtlZeroMemory(Out, sizeof(*Out));
    Out->Version = 1; Out->ProbeValidMask = g_ProbeValid;
    /* METHOD_BUFFERED SystemBuffer is nonpaged: valid MmCopyMemory destination. */
    for (i = 0; i < 4; ++i) {
        Out->Probes[i].Address = (ULONGLONG)(ULONG_PTR)g_ProbeAddresses[i];
        RtlCopyMemory(Out->Probes[i].Baseline, g_ProbeBaseline[i], 32);
        Out->Probes[i].ReadStatus = (ULONG)KwReadProbe(g_ProbeAddresses[i], Out->Probes[i].Current);
        if (!NT_SUCCESS((NTSTATUS)Out->Probes[i].ReadStatus)) Out->ProbeReadFailedMask |= 1UL << i;
        else if ((g_ProbeValid & (1UL << i)) &&
            RtlCompareMemory(Out->Probes[i].Current, g_ProbeBaseline[i], 32) != 32)
            Out->ProbeChangedMask |= 1UL << i;
    }
    for (i = 0; i < 3; ++i) {
        Out->Dispatch[i].Baseline = (ULONGLONG)(ULONG_PTR)g_DispatchBaseline[i];
        Out->Dispatch[i].Current = (ULONGLONG)(ULONG_PTR)DriverObject->MajorFunction[g_DispatchIndices[i]];
        if (Out->Dispatch[i].Current != Out->Dispatch[i].Baseline) Out->DispatchChangedMask |= 1UL << i;
    }
    if (NT_SUCCESS(status)) {
        for (attempt = 0; attempt < 3; ++attempt) {
            bytes = 0;
            status = AuxKlibQueryModuleInformation(&bytes, sizeof(*modules), NULL);
            if (!NT_SUCCESS(status)) break;
            if (bytes == 0 || bytes > KW_DIAG_MODULES * sizeof(*modules)) {
                status = STATUS_BUFFER_OVERFLOW; break;
            }
            allocated = bytes;
            modules = ExAllocatePool2(POOL_FLAG_PAGED, allocated, 'mDsK');
            if (modules == NULL) { status = STATUS_INSUFFICIENT_RESOURCES; break; }
            status = AuxKlibQueryModuleInformation(&bytes, sizeof(*modules), modules);
            if (NT_SUCCESS(status) && bytes <= allocated && bytes % sizeof(*modules) == 0) {
                Out->Count = bytes / sizeof(*modules);
                for (i = 0; i < Out->Count; ++i) {
                    Out->Modules[i].Base = (ULONGLONG)(ULONG_PTR)modules[i].BasicInfo.ImageBase;
                    Out->Modules[i].Size = modules[i].ImageSize;
                    RtlCopyMemory(Out->Modules[i].Path, modules[i].FullPathName, 255);
                }
            } else if (NT_SUCCESS(status)) status = STATUS_DATA_ERROR;
            ExFreePoolWithTag(modules, 'mDsK'); modules = NULL;
            if (status != STATUS_BUFFER_TOO_SMALL) break;
        }
    }
    Out->ModuleStatus = (ULONG)status;
}
