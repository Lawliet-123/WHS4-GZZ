#pragma once
#define KS_VERSION 1
#define KS_DEVICE_TYPE 0x8337
#define KS_PATH_CHARS 260
#define KS_QUEUE_CAPACITY 1024
#define KS_IOCTL_STATUS CTL_CODE(KS_DEVICE_TYPE, 0x800, METHOD_BUFFERED, FILE_READ_DATA)
#define KS_IOCTL_POLICY CTL_CODE(KS_DEVICE_TYPE, 0x801, METHOD_BUFFERED, FILE_READ_DATA | FILE_WRITE_DATA)
#define KS_IOCTL_EVENTS CTL_CODE(KS_DEVICE_TYPE, 0x802, METHOD_BUFFERED, FILE_READ_DATA)
#define KS_IOCTL_HEARTBEAT CTL_CODE(KS_DEVICE_TYPE, 0x803, METHOD_BUFFERED, FILE_WRITE_DATA)
#define KS_IOCTL_DIAGNOSTICS CTL_CODE(KS_DEVICE_TYPE, 0x804, METHOD_BUFFERED, FILE_READ_DATA)
#define KS_IOCTL_THREADS CTL_CODE(KS_DEVICE_TYPE, 0x806, METHOD_BUFFERED, FILE_READ_DATA)
#define KS_THREAD_MAX 4096
typedef struct {
    ULONG Pid, Tid, Flags, Status;
    ULONGLONG CreateTime, Start, SnapshotStart;
} KS_THREAD_RECORD;
typedef struct {
    ULONG Version, Status, Count, Enumerated, LookupFailed, OsBuild, DurationMs, Flags;
    KS_THREAD_RECORD Threads[KS_THREAD_MAX];
} KS_THREAD_RESULT;
C_ASSERT(sizeof(KS_THREAD_RECORD) == 40);
C_ASSERT(FIELD_OFFSET(KS_THREAD_RESULT, Threads) == 32);
C_ASSERT(sizeof(KS_THREAD_RESULT) == 163872);

typedef struct _KS_POLICY_REQUEST {
    ULONG Version;
    ULONG Pid;
    ULONG Mode; /* 0 off, 1 observe, 2 enforce */
    ULONG Reserved;
    ULONGLONG ExpectedCreateTime;
} KS_POLICY_REQUEST;

typedef struct _KS_STATUS {
    ULONG Version;
    ULONG Pid;
    ULONG Mode;
    ULONG LeaseActive;
    ULONGLONG Generation;
    ULONGLONG CreateTime;
    ULONGLONG DroppedTotal;
    ULONG QueueCount;
    ULONG Capabilities; /* 1 process, 2 thread, 4 image, 8 OB, 16 diagnostics */
} KS_STATUS;

typedef struct _KS_EVENT {
    ULONGLONG Sequence;
    ULONGLONG Timestamp100ns;
    ULONGLONG Generation;
    ULONGLONG OperationId;
    ULONGLONG ImageBase;
    ULONGLONG ImageSize;
    ULONG Kind;
    ULONG ActorPid;
    ULONG TargetPid;
    ULONG ThreadId;
    ULONG SourcePid;
    ULONG RecipientPid;
    ULONG OriginalAccess;
    ULONG BeforeAccess;
    ULONG AfterAccess;
    ULONG Flags;
    ULONG Status;
    ULONG GrantedAccess;
    WCHAR Path[KS_PATH_CHARS];
} KS_EVENT;

typedef struct _KS_BATCH {
    ULONG Version;
    ULONG Count;
    ULONGLONG Dropped;
    KS_EVENT Events[1];
} KS_BATCH;

enum {
    KsProcessCreate=1, KsProcessExit, KsThreadCreate, KsThreadExit,
    KsGameImage, KsKernelImage, KsHandlePre, KsHandlePost, KsPolicy, KsTargetExit
};
#define KS_FLAG_KERNEL 1
#define KS_FLAG_SELF 2
#define KS_FLAG_STRIPPED 4
#define KS_FLAG_THREAD 8
#define KS_FLAG_TRUNCATED 16
#define KS_FLAG_ENFORCE 32
#define KS_FLAG_NO_POST 64
#define KS_FLAG_DUPLICATE 128
#define KS_FLAG_CALLER_IDENTITY 256 /* handle events: ImageBase=actor TID, ImageSize=actor create time */
C_ASSERT(sizeof(KS_POLICY_REQUEST) == 24);
C_ASSERT(sizeof(KS_STATUS) == 48);
C_ASSERT(sizeof(KS_EVENT) == 616);
C_ASSERT(FIELD_OFFSET(KS_BATCH, Events) == 16);
