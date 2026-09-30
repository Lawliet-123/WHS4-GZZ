#pragma once
#include "thread_layout.h"
/* Some kernel-header configurations omit this documented access-right macro. */
#ifndef THREAD_QUERY_INFORMATION
#define THREAD_QUERY_INFORMATION 0x0040UL
#endif
/* Read-only sensor. No thread suspension, attach, NMI, writes or caller addresses. */
typedef NTSTATUS (NTAPI *KS_QUERY_SYSTEM)(ULONG, PVOID, ULONG, PULONG);
typedef NTSTATUS (NTAPI *KS_QUERY_THREAD)(HANDLE, ULONG, PVOID, ULONG, PULONG);
static KS_QUERY_SYSTEM g_QuerySystem;
static KS_QUERY_THREAD g_QueryThread;
static ULONG g_ThreadOsBuild;
static volatile LONG g_ThreadScanBusy;

static VOID KsInitializeThreads(VOID)
{
    RTL_OSVERSIONINFOW version;
    UNICODE_STRING name;
    RtlZeroMemory(&version, sizeof(version));
    version.dwOSVersionInfoSize = sizeof(version);
    if (!NT_SUCCESS(RtlGetVersion(&version))) return;
    g_ThreadOsBuild = version.dwBuildNumber;
    /* Enable only the user's explicitly targeted native ABI. */
    if (version.dwMajorVersion != 10 || version.dwBuildNumber != 19045) return;
    RtlInitUnicodeString(&name, L"ZwQuerySystemInformation");
    g_QuerySystem = (KS_QUERY_SYSTEM)MmGetSystemRoutineAddress(&name);
    RtlInitUnicodeString(&name, L"ZwQueryInformationThread");
    g_QueryThread = (KS_QUERY_THREAD)MmGetSystemRoutineAddress(&name);
}

static VOID KsCollectThreads(KS_THREAD_RESULT* Out)
{
    UCHAR* snapshot = NULL;
    ULONG capacity = 256 * 1024, needed = 0, attempt, offset, span, i;
    KS_NATIVE_PROCESS* process;
    KS_NATIVE_THREAD* native;
    KS_THREAD_RECORD* record;
    PETHREAD thread;
    HANDLE handle;
    PVOID start;
    /* ThreadTimes (class 1): Create, Exit, Kernel, User. */
    LARGE_INTEGER times[4];
    NTSTATUS status = STATUS_NOT_SUPPORTED;
    ULONGLONG began = KeQueryInterruptTime();
    RtlZeroMemory(Out, sizeof(*Out));
    Out->Version = KS_VERSION; Out->OsBuild = g_ThreadOsBuild;
    if (!g_QuerySystem || !g_QueryThread) { Out->Status = (ULONG)status; return; }
    if (InterlockedCompareExchange(&g_ThreadScanBusy, 1, 0) != 0) {
        Out->Status = (ULONG)STATUS_DEVICE_BUSY; return;
    }
    for (attempt = 0; attempt < 5; ++attempt) {
        if (capacity > 16 * 1024 * 1024) { status = STATUS_BUFFER_OVERFLOW; goto done; }
        snapshot = ExAllocatePool2(POOL_FLAG_PAGED, capacity, 'tSsK');
        if (!snapshot) { status = STATUS_INSUFFICIENT_RESOURCES; goto done; }
        status = g_QuerySystem(5, snapshot, capacity, &needed);
        if (status != STATUS_INFO_LENGTH_MISMATCH && status != STATUS_BUFFER_TOO_SMALL) break;
        ExFreePoolWithTag(snapshot, 'tSsK'); snapshot = NULL;
        if (needed > 16 * 1024 * 1024) { status = STATUS_BUFFER_OVERFLOW; goto done; }
        capacity = max(capacity * 2, needed);
    }
    if (!NT_SUCCESS(status)) goto done;
    if (!snapshot || needed > capacity || needed < sizeof(KS_NATIVE_PROCESS)) {
        status = STATUS_DATA_ERROR; goto done;
    }
    offset = 0;
    for (;;) {
        if (needed - offset < sizeof(KS_NATIVE_PROCESS)) { status = STATUS_DATA_ERROR; goto done; }
        process = (KS_NATIVE_PROCESS*)(snapshot + offset);
        span = KsNativeSpan(process, needed - offset);
        if (!span || process->Pid > MAXULONG) { status = STATUS_DATA_ERROR; goto done; }
        native = (KS_NATIVE_THREAD*)(process + 1);
        for (i = 0; i < process->Count; ++i) {
            if (KeQueryInterruptTime() - began > 2ULL * 10000000ULL) {
                status = STATUS_IO_TIMEOUT; Out->Flags |= 1; goto done;
            }
            ++Out->Enumerated;
            if (native[i].Pid != process->Pid || native[i].Tid > MAXULONG) {
                status = STATUS_DATA_ERROR; goto done;
            }
            if (!native[i].Tid) continue; /* idle pseudo-thread */
            thread = NULL;
            status = PsLookupThreadByThreadId((HANDLE)(ULONG_PTR)native[i].Tid, &thread);
            if (!NT_SUCCESS(status)) { ++Out->LookupFailed; continue; }
            if (HandleToULong(PsGetThreadProcessId(thread)) != (ULONG)process->Pid) {
                ++Out->LookupFailed; ObDereferenceObject(thread); continue;
            }
            if (!PsIsSystemThread(thread)) { ObDereferenceObject(thread); continue; }
            if (Out->Count == KS_THREAD_MAX) {
                ObDereferenceObject(thread); Out->Flags |= 1;
                status = STATUS_BUFFER_OVERFLOW; goto done;
            }
            record = &Out->Threads[Out->Count++];
            record->Pid = (ULONG)process->Pid; record->Tid = (ULONG)native[i].Tid;
            record->Flags = 1; record->CreateTime = native[i].CreateTime;
            record->SnapshotStart = native[i].SnapshotStart;
            handle = NULL; start = NULL;
            status = ObOpenObjectByPointer(thread, OBJ_KERNEL_HANDLE, NULL,
                THREAD_QUERY_INFORMATION, *PsThreadType, KernelMode, &handle);
            if (NT_SUCCESS(status)) {
                RtlZeroMemory(times, sizeof(times));
                status = g_QueryThread(handle, 1, times, sizeof(times), NULL);
                if (NT_SUCCESS(status) && ((ULONGLONG)times[0].QuadPart != record->CreateTime ||
                    times[1].QuadPart != 0 || !record->CreateTime)) status = STATUS_RETRY;
                if (NT_SUCCESS(status)) status = g_QueryThread(handle, 9, &start, sizeof(start), NULL);
                if (NT_SUCCESS(status) && (!start || (ULONG_PTR)start < (ULONG_PTR)MmSystemRangeStart))
                    status = STATUS_DATA_ERROR;
                if (NT_SUCCESS(status)) record->Start = (ULONGLONG)(ULONG_PTR)start;
                ZwClose(handle);
            }
            record->Status = (ULONG)status;
            ObDereferenceObject(thread);
        }
        if (!process->Next) break;
        offset += span;
    }
    status = STATUS_SUCCESS;
done:
    Out->Status = (ULONG)status;
    Out->DurationMs = (ULONG)((KeQueryInterruptTime() - began) / 10000);
    if (snapshot) ExFreePoolWithTag(snapshot, 'tSsK');
    InterlockedExchange(&g_ThreadScanBusy, 0);
}
