#pragma once
/* Native class-5 snapshot ABI, Windows 10 19045 x64 only. Not ETHREAD offsets.
   Buffer ownership belongs to the scanner; no embedded pointers are followed. */
typedef struct {
    ULONG Next, Count;
    UCHAR BeforePid[72];
    ULONGLONG Pid;
    UCHAR Rest[168];
} KS_NATIVE_PROCESS;
typedef struct {
    ULONGLONG KernelTime, UserTime, CreateTime;
    ULONG WaitTime, Padding;
    ULONGLONG SnapshotStart, Pid, Tid;
    ULONG Priority, BasePriority, Switches, State, Reason, Padding2;
} KS_NATIVE_THREAD;
C_ASSERT(sizeof(KS_NATIVE_PROCESS) == 256);
C_ASSERT(FIELD_OFFSET(KS_NATIVE_PROCESS, Pid) == 80);
C_ASSERT(sizeof(KS_NATIVE_THREAD) == 80);
static ULONG KsNativeSpan(const KS_NATIVE_PROCESS* P, ULONG Remaining)
{
    ULONG span;
    if (Remaining < sizeof(*P)) return 0;
    span = P->Next ? P->Next : Remaining;
    if (span < sizeof(*P) || span > Remaining || (P->Next & 7)) return 0;
    if (P->Count > (span - sizeof(*P)) / sizeof(KS_NATIVE_THREAD)) return 0;
    return span;
}
