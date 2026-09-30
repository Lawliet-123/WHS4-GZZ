#include <ntifs.h>
#include <wdmsec.h>
#include "protocol.h"
#include "access_policy.h"
#include "integrity.h"
#include "code_scan.h"
#include "thread_scan.h"
#pragma comment(lib, "Wdmsec.lib")

#define KS_NAME L"\\Device\\KernelSentinel"
#define KS_DOS L"\\DosDevices\\KernelSentinel"
#define KS_LEASE_100NS (10ULL * 10000000ULL)
static const GUID g_Class = {0x21bfb094,0x94e6,0x46f5,{0x9d,0x65,0xb3,0x87,0xe8,0x99,0xe8,0x64}};
static PDEVICE_OBJECT g_Device;
static IO_REMOVE_LOCK g_RemoveLock;
static KSPIN_LOCK g_Lock;
static PEPROCESS g_Game;
static ULONG g_Pid, g_Mode;
static ULONGLONG g_Generation, g_CreateTime, g_LastBeat;
static KS_EVENT g_Queue[KS_QUEUE_CAPACITY];
static ULONG g_Head, g_Count;
static ULONGLONG g_Dropped, g_DroppedTotal, g_Sequence;
static volatile LONG64 g_OperationId;
static PVOID g_ObRegistration;
static BOOLEAN g_ProcessRegistered, g_ThreadRegistered, g_ImageRegistered;
DRIVER_INITIALIZE DriverEntry;

static VOID KsInitEvent(KS_EVENT* Event, ULONG Kind)
{
    LARGE_INTEGER now;
    RtlZeroMemory(Event, sizeof(*Event));
    Event->Kind = Kind;
    KeQuerySystemTimePrecise(&now);
    Event->Timestamp100ns = (ULONGLONG)now.QuadPart;
}
static VOID KsPath(KS_EVENT* Event, PCUNICODE_STRING Path)
{
    USHORT chars;
    if (Path == NULL || Path->Buffer == NULL) return;
    chars = Path->Length / sizeof(WCHAR);
    if (chars >= KS_PATH_CHARS) { chars = KS_PATH_CHARS - 1; Event->Flags |= KS_FLAG_TRUNCATED; }
    RtlCopyMemory(Event->Path, Path->Buffer, chars * sizeof(WCHAR));
}
static VOID KsPush(KS_EVENT* Event)
{
    KIRQL irql;
    KeAcquireSpinLock(&g_Lock, &irql);
    Event->Sequence = ++g_Sequence;
    if (g_Count == KS_QUEUE_CAPACITY) {
        g_Head = (g_Head + 1) % KS_QUEUE_CAPACITY;
        --g_Count; ++g_Dropped; ++g_DroppedTotal;
    }
    g_Queue[(g_Head + g_Count) % KS_QUEUE_CAPACITY] = *Event;
    ++g_Count;
    KeReleaseSpinLock(&g_Lock, irql);
}
static BOOLEAN KsTarget(HANDLE Pid, ULONGLONG* Generation)
{
    KIRQL irql;
    BOOLEAN match;
    KeAcquireSpinLock(&g_Lock, &irql);
    match = g_Game != NULL && g_Pid == HandleToULong(Pid);
    *Generation = g_Generation;
    KeReleaseSpinLock(&g_Lock, irql);
    return match;
}
static VOID KsClear(VOID)
{
    PEPROCESS old;
    KIRQL irql;
    KeAcquireSpinLock(&g_Lock, &irql);
    old = g_Game; g_Game = NULL; g_Pid = 0; g_Mode = 0; g_CreateTime = 0;
    ++g_Generation;
    KeReleaseSpinLock(&g_Lock, irql);
    if (old != NULL) ObDereferenceObject(old);
}
static VOID KsProcess(PEPROCESS Process, HANDLE Pid, PPS_CREATE_NOTIFY_INFO Info)
{
    KS_EVENT event;
    PEPROCESS released = NULL;
    KIRQL irql;
    KsInitEvent(&event, Info != NULL ? KsProcessCreate : KsProcessExit);
    event.TargetPid = HandleToULong(Pid);
    /* ImageBase overloaded only for process events: process creation identity. */
    event.ImageBase = (ULONGLONG)PsGetProcessCreateTimeQuadPart(Process);
    if (Info != NULL) {
        event.SourcePid = HandleToULong(Info->ParentProcessId);
        event.ActorPid = HandleToULong(Info->CreatingThreadId.UniqueProcess);
        KsPath(&event, Info->ImageFileName);
    }
    KeAcquireSpinLock(&g_Lock, &irql);
    event.Generation = g_Generation;
    if (Info == NULL && Process == g_Game) {
        released = g_Game; g_Game = NULL; g_Pid = 0; g_Mode = 0; g_CreateTime = 0;
        event.Kind = KsTargetExit;
        ++g_Generation;
    }
    KeReleaseSpinLock(&g_Lock, irql);
    if (released != NULL) ObDereferenceObject(released);
    KsPush(&event);
}
static VOID KsThread(HANDLE Pid, HANDLE Tid, BOOLEAN Create)
{
    KS_EVENT event;
    KsInitEvent(&event, Create ? KsThreadCreate : KsThreadExit);
    if (!KsTarget(Pid, &event.Generation)) return;
    event.TargetPid = HandleToULong(Pid);
    event.ThreadId = HandleToULong(Tid);
    event.ActorPid = HandleToULong(PsGetCurrentProcessId());
    KsPush(&event);
}
static VOID KsImage(PUNICODE_STRING Path, HANDLE Pid, PIMAGE_INFO Info)
{
    KS_EVENT event;
    if (Info->SystemModeImage) KsInvalidateCodeBase(Info->ImageBase);
    KsInitEvent(&event, Info->SystemModeImage ? KsKernelImage : KsGameImage);
    if (!KsTarget(Pid, &event.Generation) && !Info->SystemModeImage) return;
    event.TargetPid = HandleToULong(Pid);
    event.ImageBase = (ULONGLONG)(ULONG_PTR)Info->ImageBase;
    event.ImageSize = (ULONGLONG)Info->ImageSize;
    KsPath(&event, Path);
    /* No hashing, disk access, process attach, or policy modification here. */
    KsPush(&event);
}
static OB_PREOP_CALLBACK_STATUS KsPre(PVOID Context, POB_PRE_OPERATION_INFORMATION Info)
{
    PEPROCESS owner, actor = PsGetCurrentProcess(), recipient, source;
    BOOLEAN isThread, self, enforce, lease;
    ULONG mode, before, original, after;
    ACCESS_MASK* desired;
    KIRQL irql;
    KS_EVENT event, *post;
    UNREFERENCED_PARAMETER(Context);
    Info->CallContext = NULL;
    isThread = Info->ObjectType == *PsThreadType;
    owner = isThread ? IoThreadToProcess((PETHREAD)Info->Object) : (PEPROCESS)Info->Object;
    if (Info->Operation == OB_OPERATION_HANDLE_DUPLICATE) {
        desired = &Info->Parameters->DuplicateHandleInformation.DesiredAccess;
        original = Info->Parameters->DuplicateHandleInformation.OriginalDesiredAccess;
        source = (PEPROCESS)Info->Parameters->DuplicateHandleInformation.SourceProcess;
        recipient = (PEPROCESS)Info->Parameters->DuplicateHandleInformation.TargetProcess;
    } else {
        desired = &Info->Parameters->CreateHandleInformation.DesiredAccess;
        original = Info->Parameters->CreateHandleInformation.OriginalDesiredAccess;
        source = actor; recipient = actor;
    }
    KsInitEvent(&event, KsHandlePre);
    KeAcquireSpinLock(&g_Lock, &irql);
    if (g_Game == NULL || owner != g_Game) {
        KeReleaseSpinLock(&g_Lock, irql);
        return OB_PREOP_SUCCESS;
    }
    mode = g_Mode;
    lease = KeQueryInterruptTime() - g_LastBeat < KS_LEASE_100NS;
    event.Generation = g_Generation;
    event.TargetPid = g_Pid;
    KeReleaseSpinLock(&g_Lock, irql);
    before = *desired;
    self = actor == owner && recipient == owner;
    enforce = mode == 2;
    after = KsRestrictAccess(before, isThread, enforce, lease, Info->KernelHandle, self);
    *desired = after;
    event.ActorPid = HandleToULong(PsGetProcessId(actor));
    /* Snapshot current execution context, not a call stack or driver attribution. */
    event.ImageBase = (ULONGLONG)HandleToULong(PsGetCurrentThreadId());
    event.ImageSize = (ULONGLONG)PsGetProcessCreateTimeQuadPart(actor);
    event.SourcePid = HandleToULong(PsGetProcessId(source));
    event.RecipientPid = HandleToULong(PsGetProcessId(recipient));
    event.ThreadId = isThread ? HandleToULong(PsGetThreadId((PETHREAD)Info->Object)) : 0;
    event.OriginalAccess = original; event.BeforeAccess = before; event.AfterAccess = after;
    event.Flags = (Info->KernelHandle ? KS_FLAG_KERNEL : 0) | (self ? KS_FLAG_SELF : 0) |
        (before != after ? KS_FLAG_STRIPPED : 0) | (isThread ? KS_FLAG_THREAD : 0) |
        (enforce && lease ? KS_FLAG_ENFORCE : 0) |
        (Info->Operation == OB_OPERATION_HANDLE_DUPLICATE ? KS_FLAG_DUPLICATE : 0) |
        KS_FLAG_CALLER_IDENTITY;
    event.OperationId = (ULONGLONG)InterlockedIncrement64(&g_OperationId);
    post = ExAllocatePool2(POOL_FLAG_NON_PAGED, sizeof(KS_EVENT), 'pOsK');
    if (post != NULL) { *post = event; Info->CallContext = post; }
    else { Info->CallContext = NULL; event.Flags |= KS_FLAG_NO_POST; }
    KsPush(&event);
    return OB_PREOP_SUCCESS;
}
static VOID KsPost(PVOID Context, POB_POST_OPERATION_INFORMATION Info)
{
    KS_EVENT* event = (KS_EVENT*)Info->CallContext;
    LARGE_INTEGER now;
    UNREFERENCED_PARAMETER(Context);
    if (event == NULL) return;
    event->Kind = KsHandlePost;
    event->Status = (ULONG)Info->ReturnStatus;
    if (NT_SUCCESS(Info->ReturnStatus)) {
        event->GrantedAccess = Info->Operation == OB_OPERATION_HANDLE_DUPLICATE ?
            Info->Parameters->DuplicateHandleInformation.GrantedAccess :
            Info->Parameters->CreateHandleInformation.GrantedAccess;
    }
    KeQuerySystemTimePrecise(&now); event->Timestamp100ns = (ULONGLONG)now.QuadPart;
    KsPush(event);
    ExFreePoolWithTag(event, 'pOsK');
}
static NTSTATUS KsComplete(PIRP Irp, NTSTATUS Status, ULONG_PTR Count)
{
    Irp->IoStatus.Status = Status; Irp->IoStatus.Information = Count;
    IoCompleteRequest(Irp, IO_NO_INCREMENT); return Status;
}
static NTSTATUS KsOpenClose(PDEVICE_OBJECT Device, PIRP Irp)
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    UNREFERENCED_PARAMETER(Device);
    if (stack->MajorFunction == IRP_MJ_CLEANUP) KsClear();
    return KsComplete(Irp, STATUS_SUCCESS, 0);
}
static NTSTATUS KsSetPolicy(const KS_POLICY_REQUEST* Request)
{
    PEPROCESS process = NULL, old;
    KIRQL irql;
    NTSTATUS status;
    KS_EVENT event;
    if (Request->Version != KS_VERSION || Request->Reserved != 0 || Request->Mode > 2)
        return STATUS_INVALID_PARAMETER;
    if (Request->Mode == 0) { KsClear(); return STATUS_SUCCESS; }
    if (Request->Pid <= 4 || Request->Pid == HandleToULong(PsGetCurrentProcessId()) ||
        Request->ExpectedCreateTime == 0) return STATUS_INVALID_PARAMETER;
    status = PsLookupProcessByProcessId(ULongToHandle(Request->Pid), &process);
    if (!NT_SUCCESS(status)) return status;
    if ((ULONGLONG)PsGetProcessCreateTimeQuadPart(process) != Request->ExpectedCreateTime) {
        ObDereferenceObject(process); return STATUS_INVALID_PARAMETER;
    }
    if (PsGetProcessExitStatus(process) != STATUS_PENDING) {
        ObDereferenceObject(process); return STATUS_PROCESS_IS_TERMINATING;
    }
    /* Separate local reference: the exit callback can release the policy reference. */
    ObReferenceObject(process);
    KsInitEvent(&event, KsPolicy);
    KeAcquireSpinLock(&g_Lock, &irql);
    old = g_Game; g_Game = process; g_Pid = Request->Pid; g_Mode = Request->Mode;
    g_CreateTime = Request->ExpectedCreateTime; g_LastBeat = KeQueryInterruptTime();
    event.Generation = ++g_Generation; event.TargetPid = g_Pid;
    event.AfterAccess = g_Mode;
    KeReleaseSpinLock(&g_Lock, irql);
    if (old != NULL) ObDereferenceObject(old);
    if (PsGetProcessExitStatus(process) != STATUS_PENDING) {
        PEPROCESS released = NULL;
        KeAcquireSpinLock(&g_Lock, &irql);
        if (g_Game == process && g_Generation == event.Generation) {
            released = g_Game; g_Game = NULL; g_Pid = 0; g_Mode = 0; g_CreateTime = 0;
            ++g_Generation;
        }
        KeReleaseSpinLock(&g_Lock, irql);
        if (released != NULL) ObDereferenceObject(released);
        ObDereferenceObject(process);
        return STATUS_PROCESS_IS_TERMINATING;
    }
    ObDereferenceObject(process);
    KsPush(&event); return STATUS_SUCCESS;
}
static NTSTATUS KsControl(PDEVICE_OBJECT Device, PIRP Irp)
{
    PIO_STACK_LOCATION s = IoGetCurrentIrpStackLocation(Irp);
    ULONG code = s->Parameters.DeviceIoControl.IoControlCode;
    ULONG in = s->Parameters.DeviceIoControl.InputBufferLength;
    ULONG out = s->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID buffer = Irp->AssociatedIrp.SystemBuffer;
    KIRQL irql;
    if (KeGetCurrentIrql() != PASSIVE_LEVEL) return KsComplete(Irp, STATUS_INVALID_DEVICE_STATE, 0);
    if (code == KS_IOCTL_POLICY) {
        KS_POLICY_REQUEST request;
        if (in != sizeof(request)) return KsComplete(Irp, STATUS_INVALID_PARAMETER, 0);
        request = *(KS_POLICY_REQUEST*)buffer;
        return KsComplete(Irp, KsSetPolicy(&request), 0);
    }
    if (code == KS_IOCTL_HEARTBEAT) {
        KeAcquireSpinLock(&g_Lock, &irql); g_LastBeat = KeQueryInterruptTime();
        KeReleaseSpinLock(&g_Lock, irql); return KsComplete(Irp, STATUS_SUCCESS, 0);
    }
    if (code == KS_IOCTL_STATUS) {
        KS_STATUS* status = (KS_STATUS*)buffer;
        if (out < sizeof(*status)) return KsComplete(Irp, STATUS_BUFFER_TOO_SMALL, 0);
        RtlZeroMemory(status, sizeof(*status));
        KeAcquireSpinLock(&g_Lock, &irql);
        status->Version = KS_VERSION; status->Pid = g_Pid; status->Mode = g_Mode;
        status->LeaseActive = g_Game != NULL && KeQueryInterruptTime() - g_LastBeat < KS_LEASE_100NS;
        status->Generation = g_Generation; status->CreateTime = g_CreateTime;
        status->DroppedTotal = g_DroppedTotal; status->QueueCount = g_Count;
        status->Capabilities = 31 | ((g_QuerySystem && g_QueryThread) ? 32 : 0);
        KeReleaseSpinLock(&g_Lock, irql); return KsComplete(Irp, STATUS_SUCCESS, sizeof(*status));
    }
    if (code == KS_IOCTL_EVENTS) {
        KS_BATCH* batch = (KS_BATCH*)buffer;
        ULONG capacity, count, i;
        if (out < FIELD_OFFSET(KS_BATCH, Events) + sizeof(KS_EVENT))
            return KsComplete(Irp, STATUS_BUFFER_TOO_SMALL, 0);
        capacity = (out - FIELD_OFFSET(KS_BATCH, Events)) / sizeof(KS_EVENT);
        KeAcquireSpinLock(&g_Lock, &irql);
        count = min(min(capacity, g_Count), 64UL);
        batch->Version = KS_VERSION; batch->Count = count; batch->Dropped = g_Dropped; g_Dropped = 0;
        for (i = 0; i < count; ++i) batch->Events[i] = g_Queue[(g_Head + i) % KS_QUEUE_CAPACITY];
        g_Head = (g_Head + count) % KS_QUEUE_CAPACITY; g_Count -= count;
        KeReleaseSpinLock(&g_Lock, irql);
        return KsComplete(Irp, STATUS_SUCCESS, FIELD_OFFSET(KS_BATCH, Events) + count * sizeof(KS_EVENT));
    }
    if (code == KS_IOCTL_DIAGNOSTICS) {
        if (out < sizeof(KW_DIAGNOSTICS)) return KsComplete(Irp, STATUS_BUFFER_TOO_SMALL, 0);
        KwCollectDiagnostics(Device->DriverObject, (KW_DIAGNOSTICS*)buffer);
        return KsComplete(Irp, STATUS_SUCCESS, sizeof(KW_DIAGNOSTICS));
    }
    if (code == KS_IOCTL_CODE) {
        if (out < sizeof(KS_CODE_RESULT)) return KsComplete(Irp, STATUS_BUFFER_TOO_SMALL, 0);
        KsScanCode((KS_CODE_RESULT*)buffer);
        return KsComplete(Irp, STATUS_SUCCESS, sizeof(KS_CODE_RESULT));
    }
    if (code == KS_IOCTL_THREADS) {
        if (in != 0) return KsComplete(Irp, STATUS_INVALID_PARAMETER, 0);
        if (out < sizeof(KS_THREAD_RESULT)) return KsComplete(Irp, STATUS_BUFFER_TOO_SMALL, 0);
        KsCollectThreads((KS_THREAD_RESULT*)buffer);
        return KsComplete(Irp, STATUS_SUCCESS, sizeof(KS_THREAD_RESULT));
    }
    return KsComplete(Irp, STATUS_INVALID_DEVICE_REQUEST, 0);
}
static VOID KsUnregister(VOID)
{
    if (g_ObRegistration != NULL) { ObUnRegisterCallbacks(g_ObRegistration); g_ObRegistration = NULL; }
    if (g_ImageRegistered) { PsRemoveLoadImageNotifyRoutine(KsImage); g_ImageRegistered = FALSE; }
    if (g_ThreadRegistered) { PsRemoveCreateThreadNotifyRoutine(KsThread); g_ThreadRegistered = FALSE; }
    if (g_ProcessRegistered) { PsSetCreateProcessNotifyRoutineEx(KsProcess, TRUE); g_ProcessRegistered = FALSE; }
}
static NTSTATUS KsDispatch(PDEVICE_OBJECT Device, PIRP Irp)
{
    NTSTATUS status = IoAcquireRemoveLock(&g_RemoveLock, Irp);
    if (!NT_SUCCESS(status)) return KsComplete(Irp, status, 0);
    if (IoGetCurrentIrpStackLocation(Irp)->MajorFunction == IRP_MJ_DEVICE_CONTROL)
        status = KsControl(Device, Irp);
    else
        status = KsOpenClose(Device, Irp);
    /* Irp is only an opaque remove-lock tag after completion; never dereference it. */
    IoReleaseRemoveLock(&g_RemoveLock, Irp);
    return status;
}
static VOID KsUnload(PDRIVER_OBJECT Driver)
{
    UNICODE_STRING name;
    if (NT_SUCCESS(IoAcquireRemoveLock(&g_RemoveLock, Driver)))
        IoReleaseRemoveLockAndWait(&g_RemoveLock, Driver);
    KsUnregister(); KsClear();
    if (g_HashProvider != NULL) BCryptCloseAlgorithmProvider(g_HashProvider, 0);
    RtlInitUnicodeString(&name, KS_DOS); IoDeleteSymbolicLink(&name); IoDeleteDevice(g_Device);
}
NTSTATUS DriverEntry(PDRIVER_OBJECT Driver, PUNICODE_STRING RegistryPath)
{
    NTSTATUS status;
    UNICODE_STRING name, dos, sddl;
    OB_CALLBACK_REGISTRATION registration;
    OB_OPERATION_REGISTRATION operations[2];
    UNREFERENCED_PARAMETER(RegistryPath);
    KeInitializeSpinLock(&g_Lock);
    IoInitializeRemoveLock(&g_RemoveLock, 'rSsK', 0, 0);
    KsInitializeCode();
    KsInitializeThreads();
    RtlInitUnicodeString(&name, KS_NAME); RtlInitUnicodeString(&dos, KS_DOS);
    RtlInitUnicodeString(&sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
    status = IoCreateDeviceSecure(Driver, 0, &name, KS_DEVICE_TYPE, FILE_DEVICE_SECURE_OPEN,
        TRUE, &sddl, &g_Class, &g_Device);
    if (!NT_SUCCESS(status)) goto fail;
    Driver->MajorFunction[IRP_MJ_CREATE] = KsDispatch;
    Driver->MajorFunction[IRP_MJ_CLOSE] = KsDispatch;
    Driver->MajorFunction[IRP_MJ_CLEANUP] = KsDispatch;
    Driver->MajorFunction[IRP_MJ_DEVICE_CONTROL] = KsDispatch;
    Driver->DriverUnload = KsUnload;
    KwInitializeDiagnostics(Driver);
    status = PsSetCreateProcessNotifyRoutineEx(KsProcess, FALSE);
    if (!NT_SUCCESS(status)) goto fail;
    g_ProcessRegistered = TRUE;
    status = PsSetCreateThreadNotifyRoutine(KsThread);
    if (!NT_SUCCESS(status)) goto fail;
    g_ThreadRegistered = TRUE;
    status = PsSetLoadImageNotifyRoutine(KsImage);
    if (!NT_SUCCESS(status)) goto fail;
    g_ImageRegistered = TRUE;
    RtlZeroMemory(&registration, sizeof(registration)); RtlZeroMemory(operations, sizeof(operations));
    registration.Version = OB_FLT_REGISTRATION_VERSION;
    registration.OperationRegistrationCount = 2;
    /* Research altitude only; production requires a properly assigned altitude. */
    RtlInitUnicodeString(&registration.Altitude, L"386410.7811");
    registration.OperationRegistration = operations;
    operations[0].ObjectType = PsProcessType;
    operations[0].Operations = OB_OPERATION_HANDLE_CREATE | OB_OPERATION_HANDLE_DUPLICATE;
    operations[0].PreOperation = KsPre; operations[0].PostOperation = KsPost;
    operations[1] = operations[0]; operations[1].ObjectType = PsThreadType;
    status = ObRegisterCallbacks(&registration, &g_ObRegistration);
    if (!NT_SUCCESS(status)) goto fail;
    status = IoCreateSymbolicLink(&dos, &name);
    if (!NT_SUCCESS(status)) goto fail;
    g_Device->Flags |= DO_BUFFERED_IO; g_Device->Flags &= ~DO_DEVICE_INITIALIZING;
    return STATUS_SUCCESS;
fail:
    KsUnregister(); KsClear();
    if (g_HashProvider != NULL) BCryptCloseAlgorithmProvider(g_HashProvider, 0);
    if (g_Device != NULL) IoDeleteDevice(g_Device);
    return status;
}
