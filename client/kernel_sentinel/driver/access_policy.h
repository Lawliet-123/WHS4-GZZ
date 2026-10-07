#pragma once
/* Documented OB-modifiable bits. VM_READ is observed, deliberately not stripped.
   Process: TERMINATE, CREATE_THREAD, VM_OPERATION, VM_WRITE, DUP_HANDLE,
            SUSPEND_RESUME. Thread: TERMINATE, SUSPEND_RESUME, SET_CONTEXT. */
#define KS_PROCESS_STRIP_MASK 0x0000086BUL
#define KS_THREAD_STRIP_MASK 0x00000013UL
#define KS_PROCESS_OBSERVE_MASK (KS_PROCESS_STRIP_MASK | 0x10UL)
#define KS_THREAD_OBSERVE_MASK (KS_THREAD_STRIP_MASK | 0x08UL)
static unsigned long KsRestrictAccess(unsigned long Access, int IsThread,
    int Enforce, int LeaseActive, int KernelHandle, int SelfHandle)
{
    if (!Enforce || !LeaseActive || KernelHandle || SelfHandle) return Access;
    return Access & ~(IsThread ? KS_THREAD_STRIP_MASK : KS_PROCESS_STRIP_MASK);
}
