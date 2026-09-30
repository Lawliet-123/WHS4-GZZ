/* Narrow test fixture: one system thread enters a 12-byte executable pool stub.
 * The stub only jumps to our normal worker; its allocation is freed at unload.
 * Device IOCTL is read-only and returns the exact thread/start identity.
 */
#include <ntifs.h>
#include <wdmsec.h>

#define KOP_TYPE 0x8367
#define KOP_GET CTL_CODE(KOP_TYPE, 0x800, METHOD_BUFFERED, FILE_READ_DATA)
#define KOP_TAG 'pOsK'

static const GUID g_Class = {0x3290eb3b, 0x6430, 0x45a0,
    {0xb1,0x12,0x51,0x6b,0x90,0xde,0x58,0x6a}};
static PDEVICE_OBJECT g_Device;
static PETHREAD g_Thread;
static KEVENT g_Stop, g_Ready;
static ULONG g_Tid;
static PVOID g_Stub;

typedef struct {
    ULONG Version, Tid;
    ULONGLONG Start;
    UCHAR Stub[12];
    ULONG Reserved;
} KOP_STATE;
C_ASSERT(sizeof(KOP_STATE) == 32);
DRIVER_INITIALIZE DriverEntry;

static VOID KopWorker(PVOID Context)
{
    UNREFERENCED_PARAMETER(Context);
    g_Tid = HandleToULong(PsGetCurrentThreadId());
    KeSetEvent(&g_Ready, IO_NO_INCREMENT, FALSE);
    KeWaitForSingleObject(&g_Stop, Executive, KernelMode, FALSE, NULL);
    PsTerminateSystemThread(STATUS_SUCCESS);
}

static NTSTATUS KopDispatch(PDEVICE_OBJECT Device, PIRP Irp)
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    NTSTATUS status = STATUS_SUCCESS;
    ULONG_PTR count = 0;
    UNREFERENCED_PARAMETER(Device);
    if (stack->MajorFunction == IRP_MJ_DEVICE_CONTROL) {
        if (stack->Parameters.DeviceIoControl.IoControlCode != KOP_GET ||
            stack->Parameters.DeviceIoControl.InputBufferLength != 0 ||
            stack->Parameters.DeviceIoControl.OutputBufferLength < sizeof(KOP_STATE)) {
            status = STATUS_INVALID_PARAMETER;
        } else {
            KOP_STATE* state = (KOP_STATE*)Irp->AssociatedIrp.SystemBuffer;
            RtlZeroMemory(state, sizeof(*state));
            state->Version = 1;
            state->Tid = g_Tid;
            state->Start = (ULONGLONG)(ULONG_PTR)g_Stub;
            RtlCopyMemory(state->Stub, g_Stub, sizeof(state->Stub));
            count = sizeof(*state);
        }
    }
    Irp->IoStatus.Status = status;
    Irp->IoStatus.Information = count;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
}

static VOID KopUnload(PDRIVER_OBJECT Driver)
{
    UNICODE_STRING name;
    UNREFERENCED_PARAMETER(Driver);
    KeSetEvent(&g_Stop, IO_NO_INCREMENT, FALSE);
    if (g_Thread) {
        KeWaitForSingleObject(g_Thread, Executive, KernelMode, FALSE, NULL);
        ObDereferenceObject(g_Thread);
        g_Thread = NULL;
    }
    RtlInitUnicodeString(&name, L"\\DosDevices\\KsOutsideThreadProbe");
    IoDeleteSymbolicLink(&name);
    IoDeleteDevice(g_Device);
    if (g_Stub) {
        ExFreePoolWithTag(g_Stub, KOP_TAG);
        g_Stub = NULL;
    }
}

NTSTATUS DriverEntry(PDRIVER_OBJECT Driver, PUNICODE_STRING RegistryPath)
{
    UNICODE_STRING name, dos, sddl;
    OBJECT_ATTRIBUTES attributes;
    HANDLE handle = NULL;
    ULONG_PTR destination;
    NTSTATUS status;
    UNREFERENCED_PARAMETER(RegistryPath);
    KeInitializeEvent(&g_Stop, NotificationEvent, FALSE);
    KeInitializeEvent(&g_Ready, NotificationEvent, FALSE);
    RtlInitUnicodeString(&name, L"\\Device\\KsOutsideThreadProbe");
    RtlInitUnicodeString(&dos, L"\\DosDevices\\KsOutsideThreadProbe");
    RtlInitUnicodeString(&sddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
    status = IoCreateDeviceSecure(Driver, 0, &name, KOP_TYPE, FILE_DEVICE_SECURE_OPEN,
                                  TRUE, &sddl, &g_Class, &g_Device);
    if (!NT_SUCCESS(status)) return status;
    g_Stub = ExAllocatePool2(POOL_FLAG_NON_PAGED_EXECUTE, 16, KOP_TAG);
    if (!g_Stub) {
        IoDeleteDevice(g_Device);
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    /* mov rax, imm64; jmp rax. RCX (the PsCreateSystemThread context) is intact. */
    ((UCHAR*)g_Stub)[0] = 0x48;
    ((UCHAR*)g_Stub)[1] = 0xB8;
    destination = (ULONG_PTR)KopWorker;
    RtlCopyMemory((UCHAR*)g_Stub + 2, &destination, sizeof(destination));
    ((UCHAR*)g_Stub)[10] = 0xFF;
    ((UCHAR*)g_Stub)[11] = 0xE0;
    Driver->MajorFunction[IRP_MJ_CREATE] = KopDispatch;
    Driver->MajorFunction[IRP_MJ_CLOSE] = KopDispatch;
    Driver->MajorFunction[IRP_MJ_CLEANUP] = KopDispatch;
    Driver->MajorFunction[IRP_MJ_DEVICE_CONTROL] = KopDispatch;
    Driver->DriverUnload = KopUnload;
    InitializeObjectAttributes(&attributes, NULL, OBJ_KERNEL_HANDLE, NULL, NULL);
    status = PsCreateSystemThread(&handle, SYNCHRONIZE, &attributes, NULL, NULL,
                                  (PKSTART_ROUTINE)(ULONG_PTR)g_Stub, NULL);
    if (!NT_SUCCESS(status)) goto fail;
    status = ObReferenceObjectByHandle(handle, SYNCHRONIZE, *PsThreadType,
                                       KernelMode, (PVOID*)&g_Thread, NULL);
    if (!NT_SUCCESS(status)) {
        KeSetEvent(&g_Stop, IO_NO_INCREMENT, FALSE);
        ZwWaitForSingleObject(handle, FALSE, NULL);
        ZwClose(handle);
        goto fail;
    }
    ZwClose(handle);
    KeWaitForSingleObject(&g_Ready, Executive, KernelMode, FALSE, NULL);
    status = IoCreateSymbolicLink(&dos, &name);
    if (!NT_SUCCESS(status)) {
        KeSetEvent(&g_Stop, IO_NO_INCREMENT, FALSE);
        KeWaitForSingleObject(g_Thread, Executive, KernelMode, FALSE, NULL);
        ObDereferenceObject(g_Thread);
        g_Thread = NULL;
        goto fail;
    }
    g_Device->Flags |= DO_BUFFERED_IO;
    g_Device->Flags &= ~DO_DEVICE_INITIALIZING;
    return STATUS_SUCCESS;
fail:
    ExFreePoolWithTag(g_Stub, KOP_TAG);
    g_Stub = NULL;
    IoDeleteDevice(g_Device);
    return status;
}
