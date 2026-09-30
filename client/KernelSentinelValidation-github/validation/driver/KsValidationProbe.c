/* Normal load/system-thread fixture. No memory-write or patch IOCTL. */
#include <ntifs.h>
#include <wdmsec.h>
#define KVP_TYPE 0x8357
#define KVP_GET CTL_CODE(KVP_TYPE,0x800,METHOD_BUFFERED,FILE_READ_DATA)
static const GUID g_Class={0x2df7bfa8,0xfcc7,0x4aa0,{0x9f,0x11,0x36,0x88,0x30,0x12,0x9a,0x55}};
static PDEVICE_OBJECT g_Device;
static PETHREAD g_Thread;
static KEVENT g_Stop, g_Ready;
static ULONG g_Tid;
typedef struct { ULONG Version, Tid, Reserved[2]; } KVP_STATE;
C_ASSERT(sizeof(KVP_STATE)==16);
DRIVER_INITIALIZE DriverEntry;

static VOID KvpWorker(PVOID Context)
{
    UNREFERENCED_PARAMETER(Context);
    g_Tid=HandleToULong(PsGetCurrentThreadId());
    KeSetEvent(&g_Ready,IO_NO_INCREMENT,FALSE);
    KeWaitForSingleObject(&g_Stop,Executive,KernelMode,FALSE,NULL);
    PsTerminateSystemThread(STATUS_SUCCESS);
}
static NTSTATUS KvpDispatch(PDEVICE_OBJECT Device,PIRP Irp)
{
    PIO_STACK_LOCATION s=IoGetCurrentIrpStackLocation(Irp);
    NTSTATUS status=STATUS_SUCCESS;
    ULONG_PTR count=0;
    UNREFERENCED_PARAMETER(Device);
    if(s->MajorFunction==IRP_MJ_DEVICE_CONTROL){
        if(s->Parameters.DeviceIoControl.IoControlCode!=KVP_GET ||
           s->Parameters.DeviceIoControl.InputBufferLength!=0 ||
           s->Parameters.DeviceIoControl.OutputBufferLength<sizeof(KVP_STATE))
            status=STATUS_INVALID_PARAMETER;
        else {
            KVP_STATE* state=Irp->AssociatedIrp.SystemBuffer;
            RtlZeroMemory(state,sizeof(*state));state->Version=1;state->Tid=g_Tid;
            count=sizeof(*state);
        }
    }
    Irp->IoStatus.Status=status;Irp->IoStatus.Information=count;
    IoCompleteRequest(Irp,IO_NO_INCREMENT);return status;
}
static VOID KvpUnload(PDRIVER_OBJECT Driver)
{
    UNICODE_STRING dos;
    UNREFERENCED_PARAMETER(Driver);
    KeSetEvent(&g_Stop,IO_NO_INCREMENT,FALSE);
    if(g_Thread){KeWaitForSingleObject(g_Thread,Executive,KernelMode,FALSE,NULL);ObDereferenceObject(g_Thread);}
    RtlInitUnicodeString(&dos,L"\\DosDevices\\KsValidationProbe");IoDeleteSymbolicLink(&dos);
    IoDeleteDevice(g_Device);
}
NTSTATUS DriverEntry(PDRIVER_OBJECT Driver,PUNICODE_STRING RegistryPath)
{
    UNICODE_STRING name,dos,sddl;
    OBJECT_ATTRIBUTES oa;
    HANDLE threadHandle=NULL;
    NTSTATUS status;
    UNREFERENCED_PARAMETER(RegistryPath);
    KeInitializeEvent(&g_Stop,NotificationEvent,FALSE);KeInitializeEvent(&g_Ready,NotificationEvent,FALSE);
    RtlInitUnicodeString(&name,L"\\Device\\KsValidationProbe");
    RtlInitUnicodeString(&dos,L"\\DosDevices\\KsValidationProbe");
    RtlInitUnicodeString(&sddl,L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
    status=IoCreateDeviceSecure(Driver,0,&name,KVP_TYPE,FILE_DEVICE_SECURE_OPEN,TRUE,&sddl,&g_Class,&g_Device);
    if(!NT_SUCCESS(status))return status;
    Driver->MajorFunction[IRP_MJ_CREATE]=KvpDispatch;
    Driver->MajorFunction[IRP_MJ_CLOSE]=KvpDispatch;
    Driver->MajorFunction[IRP_MJ_CLEANUP]=KvpDispatch;
    Driver->MajorFunction[IRP_MJ_DEVICE_CONTROL]=KvpDispatch;
    Driver->DriverUnload=KvpUnload;
    InitializeObjectAttributes(&oa,NULL,OBJ_KERNEL_HANDLE,NULL,NULL);
    status=PsCreateSystemThread(&threadHandle,SYNCHRONIZE,&oa,NULL,NULL,KvpWorker,NULL);
    if(!NT_SUCCESS(status)){IoDeleteDevice(g_Device);return status;}
    status=ObReferenceObjectByHandle(threadHandle,SYNCHRONIZE,*PsThreadType,KernelMode,(PVOID*)&g_Thread,NULL);
    if(!NT_SUCCESS(status)){
        KeSetEvent(&g_Stop,IO_NO_INCREMENT,FALSE);
        ZwWaitForSingleObject(threadHandle,FALSE,NULL);ZwClose(threadHandle);
        IoDeleteDevice(g_Device);return status;
    }
    ZwClose(threadHandle);
    KeWaitForSingleObject(&g_Ready,Executive,KernelMode,FALSE,NULL);
    status=IoCreateSymbolicLink(&dos,&name);
    if(!NT_SUCCESS(status)){KvpUnload(Driver);return status;}
    g_Device->Flags|=DO_BUFFERED_IO;g_Device->Flags&=~DO_DEVICE_INITIALIZING;
    return STATUS_SUCCESS;
}
