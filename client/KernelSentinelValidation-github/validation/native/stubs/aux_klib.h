#pragma once
/* User-mode API double; used ONLY by native_sensor_test.c. */
typedef struct { PVOID ImageBase; } AUX_MODULE_BASIC_INFO;
typedef struct {
    AUX_MODULE_BASIC_INFO BasicInfo;
    ULONG ImageSize;
    USHORT FileNameOffset;
    UCHAR FullPathName[256];
} AUX_MODULE_EXTENDED_INFO, *PAUX_MODULE_EXTENDED_INFO;
static NTSTATUS AuxKlibInitialize(void);
static NTSTATUS AuxKlibQueryModuleInformation(PULONG, ULONG, PVOID);
