"""외부 프로세스에서 UE5 런타임을 읽는 공용 계층 (MECCHA CHAMELEON 4.0.2)

`ReadProcessMemory` 만 쓴다. 게임에 아무것도 주입하지 않는다.

## FNamePool 을 IDA 없이 역추적한 기록

Dumper-7 이 덤프한 `Offsets::GNames = 0x0977D900` 으로는 이름이 안 풀렸다.
블록 배열이 통상 위치(+0x00 / +0x10)에 없었고, 헤더 비트 배치 3종 × 테이블
오프셋 14종을 전수 탐색해도 0번 이름이 "None" 으로 안 나왔다.

그래서 `AppendString`(RVA `0x01392470`)을 capstone 으로 디스어셈블했다.

```asm
0139247F  cmp   byte ptr [rip+...], 0          ; -> 0x097B6158   초기화 플래그
01392490  lea   r8,  [rip+...]                 ; -> 0x097B6400   ★ 진짜 FNamePool
013924B4  shr   ecx, 0x10                      ; 블록 인덱스 = id >> 16
013924CA  lea   ecx, [rax + rax]               ; within = (id & 0xFFFF) * 2
013924CD  add   rcx, [r8 + rdx*8 + 0x10]       ; ★ Blocks 배열 = pool + 0x10
```

엔트리 디코더(RVA `0x01392230`):

```asm
01392251  movzx eax, word ptr [rcx]            ; header = *(uint16*)entry
0139225F  shr   edi, 6                         ; ★ length = header >> 6
0139226E  test  al, 1                          ; ★ wide   = header & 1
01392259  lea   rdx, [rcx + 2]                 ; 문자열은 entry + 2
```

**결론:** pool 은 `0x097B6400` 이고 `0x0977D900` 이 아니다. 지연 초기화 싱글턴이라
Dumper-7 이 다른 참조를 잡은 것으로 보인다. 덤퍼가 준 값이라고 무조건 맞는 게
아니라는 실증 사례다 — 값을 쓰기 전에 **한 번은 검증**해야 한다.
"""

import struct

import pymem

GAME_EXE = "PenguinHotel-Win64-Shipping.exe"

# ── 4.0.2 오프셋 ─────────────────────────────────────────────────────────
GOBJECTS_RVA = 0x095DC5A0      # Dumper-7 제공, 검증됨
NAMEPOOL_RVA = 0x097B6400      # AppendString 디스어셈블로 직접 확인
NAMEPOOL_BLOCKS = 0x10         # FNameEntryAllocator::Blocks

NAME_OFFSET = 0x18             # UObject::NamePrivate
CLASS_OFFSET = 0x10            # UObject::ClassPrivate
EXEC_FUNCTION_OFF = 0xD8       # UFunction::ExecFunction
PROCESS_EVENT_IDX = 0x4C       # UObject vtable 슬롯

SUPER_STRUCT_OFF = 0x40        # UStruct::SuperStruct — 상속 사슬을 타고 올라간다
CDO_OFF = 0x110                # UClass::ClassDefaultObject

GOBJ_CHUNKS = 0x00
GOBJ_NUM = 0x14
CHUNK_SIZE = 65536
ITEM_STRIDE = 0x18
OBJ_HEADER_SPAN = 0xE0         # vtable + Class + Name + ExecFunction 을 한 번에


class UObjectRow:
    __slots__ = ("addr", "vtable", "cls", "name_id", "exec_fn")

    def __init__(self, addr, vtable, cls, name_id, exec_fn):
        self.addr = addr
        self.vtable = vtable
        self.cls = cls
        self.name_id = name_id
        self.exec_fn = exec_fn


class Runtime:
    def __init__(self):
        self.pm = pymem.Pymem(GAME_EXE)
        self.base = self.pm.process_base.lpBaseOfDll
        self.blocks = self.base + NAMEPOOL_RVA + NAMEPOOL_BLOCKS

        self.modules = {}
        for m in self.pm.list_modules():
            n = m.name if isinstance(m.name, str) else m.name.decode("utf-8", "replace")
            self.modules[n.lower()] = (m.lpBaseOfDll, m.lpBaseOfDll + m.SizeOfImage)
        self.game_lo, self.game_hi = self.modules[GAME_EXE.lower()]

        self._names = {}
        self._class_names = {}
        self._cdos = {}
        self._chains = {}
        if self.resolve(0) != "None":
            raise RuntimeError("FNamePool 검증 실패 (0번 이름이 'None' 이 아님) — 빌드 불일치")

    # ── 메모리 ───────────────────────────────────────────────────────────
    def rq(self, addr):
        try:
            return struct.unpack("<Q", self.pm.read_bytes(addr, 8))[0]
        except Exception:
            return 0

    def in_game_module(self, addr):
        return self.game_lo <= addr < self.game_hi

    def owner_of(self, addr):
        for name, (lo, hi) in self.modules.items():
            if lo <= addr < hi:
                return name
        return "알 수 없는 메모리"

    # ── 이름 ─────────────────────────────────────────────────────────────
    def resolve(self, name_id):
        """FName ID 를 문자열로. 디스어셈블로 확인한 레이아웃 그대로다."""
        if name_id in self._names:
            return self._names[name_id]
        try:
            blk = self.rq(self.blocks + (name_id >> 16) * 8)
            if not blk:
                return ""
            off = (name_id & 0xFFFF) * 2
            h = struct.unpack("<H", self.pm.read_bytes(blk + off, 2))[0]
            wide, ln = h & 1, h >> 6
            if ln <= 0 or ln > 1024:
                return ""
            raw = self.pm.read_bytes(blk + off + 2, ln * (2 if wide else 1))
            v = raw.decode("utf-16-le" if wide else "ascii", "replace")
        except Exception:
            v = ""
        self._names[name_id] = v
        return v

    def name_of(self, row):
        return self.resolve(row.name_id)

    def class_name(self, cls_ptr):
        """UClass 포인터 -> 클래스 이름. 클래스는 수가 적어 캐시가 잘 듣는다."""
        if not cls_ptr:
            return ""
        if cls_ptr in self._class_names:
            return self._class_names[cls_ptr]
        try:
            nid = struct.unpack("<I", self.pm.read_bytes(cls_ptr + NAME_OFFSET, 4))[0]
            v = self.resolve(nid)
        except Exception:
            v = ""
        self._class_names[cls_ptr] = v
        return v

    def object_name(self, obj_ptr):
        try:
            nid = struct.unpack("<I", self.pm.read_bytes(obj_ptr + NAME_OFFSET, 4))[0]
            return self.resolve(nid)
        except Exception:
            return ""

    # ── 클래스 사슬 · CDO ────────────────────────────────────────────────
    def cdo_of(self, cls_ptr):
        """UClass -> ClassDefaultObject.

        **기준값을 우리가 저장하지 않기 위해 필요하다.** 값 변조를 잡으려면
        "원래 얼마였나"를 알아야 하는데, 그걸 상수로 박아두면 게임이 패치될
        때마다 틀린다. 언리얼은 클래스마다 기본값 인스턴스를 들고 있으니
        그것을 그대로 기준으로 쓴다. vtable 범위 비교와 같은 원리다.
        """
        if not cls_ptr:
            return 0
        if cls_ptr in self._cdos:
            return self._cdos[cls_ptr]
        try:
            v = self.rq(cls_ptr + CDO_OFF)
        except Exception:
            v = 0
        self._cdos[cls_ptr] = v
        return v

    def class_chain(self, cls_ptr, limit=32):
        """자기 자신부터 최상위까지 클래스 이름을 순서대로 돌려준다.

        블루프린트 클래스는 이름이 `..._Survivor_Default_Fukuyoka_1point4_C`
        처럼 파생돼 있어서, 이름 하나로 매칭하면 자식 클래스를 전부 놓친다.
        """
        if cls_ptr in self._chains:
            return self._chains[cls_ptr]
        out, cur, seen = [], cls_ptr, set()
        while cur and cur not in seen and len(out) < limit:
            seen.add(cur)
            n = self.class_name(cur)
            if not n:
                break
            out.append(n)
            try:
                cur = self.rq(cur + SUPER_STRUCT_OFF)
            except Exception:
                break
        self._chains[cls_ptr] = out
        return out

    def is_a(self, cls_ptr, base_name):
        return base_name in self.class_chain(cls_ptr)

    # ── GObjects ─────────────────────────────────────────────────────────
    def iter_objects(self):
        """살아있는 UObject 전수 순회.

        헤더가 0xE0 안에 연속이라 한 번의 읽기로 네 필드를 다 뽑는다.
        그래도 5.7만 개에 20초쯤 걸린다. 외부 스캐너의 구조적 비용이다.
        """
        g = self.base + GOBJECTS_RVA
        chunks = self.rq(g + GOBJ_CHUNKS)
        num = struct.unpack("<i", self.pm.read_bytes(g + GOBJ_NUM, 4))[0]
        if not chunks or num <= 0 or num > 4_000_000:
            raise RuntimeError(f"GObjects 를 읽지 못했습니다 (chunks=0x{chunks:X} num={num})")

        cc = {}
        for i in range(num):
            ci = i // CHUNK_SIZE
            if ci not in cc:
                cc[ci] = self.rq(chunks + ci * 8)
            chunk = cc[ci]
            if not chunk:
                continue
            obj = self.rq(chunk + (i % CHUNK_SIZE) * ITEM_STRIDE)
            if not obj:
                continue
            try:
                hdr = self.pm.read_bytes(obj, OBJ_HEADER_SPAN)
                exec_fn = struct.unpack("<Q", hdr[EXEC_FUNCTION_OFF:EXEC_FUNCTION_OFF + 8])[0]
            except Exception:
                try:
                    hdr = self.pm.read_bytes(obj, 0x20)
                    exec_fn = None
                except Exception:
                    continue
            vtable = struct.unpack("<Q", hdr[0:8])[0]
            if not vtable:
                continue
            yield UObjectRow(
                obj, vtable,
                struct.unpack("<Q", hdr[CLASS_OFFSET:CLASS_OFFSET + 8])[0],
                struct.unpack("<I", hdr[NAME_OFFSET:NAME_OFFSET + 4])[0],
                exec_fn)
