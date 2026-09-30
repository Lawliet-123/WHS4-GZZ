#pragma once
/* Shared by the kernel implementation and portable regression tests.
   ULONG/ULONGLONG/UCHAR/CHAR/BOOLEAN must already have Windows widths. */
typedef struct {
    ULONGLONG Base;
    ULONG Size;
    BOOLEAN Valid;
    UCHAR Hash[32];
    CHAR Path[256];
} KS_CODE_BASELINE;

static int KsCodePathEqual(const CHAR* Left, const CHAR* Right)
{
    ULONG i;
    for (i = 0; i < 256; ++i) {
        UCHAR a = (UCHAR)Left[i], b = (UCHAR)Right[i];
        if (a >= 'A' && a <= 'Z') a += 'a' - 'A';
        if (b >= 'A' && b <= 'Z') b += 'a' - 'A';
        if (a != b) return 0;
        if (a == 0) return 1; /* Ignore all bytes following the string terminator. */
    }
    return 0; /* Unterminated paths are not a valid identity. */
}
static void KsCopyCodePath(CHAR* Destination, const CHAR* Source)
{
    ULONG i;
    int ended = 0;
    for (i = 0; i < 255; ++i) {
        if (!ended && Source[i] == 0) ended = 1;
        Destination[i] = ended ? 0 : Source[i];
    }
    Destination[255] = 0;
}
/* Caller serializes access. The original hash is retained on detected changes. */
static ULONG KsUpdateCodeCache(KS_CODE_BASELINE* Entries, ULONG Count, ULONG* EvictionCursor,
    ULONGLONG Base, ULONG Size, const CHAR* Path, const UCHAR* Current, UCHAR* Baseline)
{
    ULONG i, slot = Count, freeSlot = Count, flags;
    for (i = 0; i < Count; ++i) {
        if (Entries[i].Base == 0 && freeSlot == Count) freeSlot = i;
        if (Entries[i].Base == Base && Entries[i].Size == Size &&
            KsCodePathEqual(Entries[i].Path, Path)) { slot = i; break; }
    }
    if (slot == Count) {
        slot = freeSlot != Count ? freeSlot : (*EvictionCursor)++ % Count;
        Entries[slot].Valid = 0;
    }
    if (Entries[slot].Valid) {
        flags = 1;
        for (i = 0; i < 32; ++i) {
            Baseline[i] = Entries[slot].Hash[i];
            if (Baseline[i] != Current[i]) flags |= 2;
        }
    } else {
        Entries[slot].Base = Base; Entries[slot].Size = Size;
        KsCopyCodePath(Entries[slot].Path, Path);
        for (i = 0; i < 32; ++i) Entries[slot].Hash[i] = Baseline[i] = Current[i];
        Entries[slot].Valid = 1;
        flags = 4;
    }
    return flags;
}
