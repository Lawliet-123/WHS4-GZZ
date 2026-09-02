"""
직접 작성한 UE5 메모리 리딩 엔진 (읽기 전용 단계용 공용 모듈).

Meccha-Chameleon-Tools 저장소의 core.py는 "참고 자료"로만 사용했고,
여기 있는 코드는 import가 아니라 새로 짠 것. 다만 아래 두 가지는 이 게임
바이너리(UE5.6, PenguinHotel-Win64-Shipping.exe)에 대한 "사실 정보"라서
분석 단계에서 이미 확인한 값을 그대로 재사용함 (코드가 아니라 데이터):
  - GUObjectArray를 찾기 위한 x64 머신코드 시그니처
  - UObjectBase/UStruct/FField의 부트스트랩 오프셋 (엔진 버전 고정값)

패턴 스캐너는 원본이 순수 파이썬 for문으로 166MB를 바이트 단위로 훑어서
연결에 ~20초 걸렸음. 여기서는 re 모듈(C로 구현된 정규식 엔진)을 써서
같은 일을 훨씬 빠르게 하도록 다시 짰음.
"""
import re
import struct
import math
import pymem
import pymem.process

PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"

# UE5.6 리플렉션 구조체 부트스트랩 오프셋 (엔진 고정값)
OFF_CLASS_PRIVATE = 0x10       # UObjectBase::ClassPrivate
OFF_NAME_PRIVATE = 0x18        # UObjectBase::NamePrivate
OFF_SUPER_STRUCT = 0x40        # UStruct::SuperStruct
OFF_CHILD_PROPERTIES = 0x50    # UStruct::ChildProperties
OFF_FIELD_NEXT = 0x18          # FField::Next
OFF_FIELD_NAME = 0x20          # FField::NamePrivate
OFF_PROPERTY_OFFSET = 0x44     # FProperty::Offset_Internal

# 카메라 쪽은 UObject 프로퍼티가 아니라 순수 C++ 구조체(FMinimalViewInfo 등)라
# 리플렉션으로 못 찾고 고정 오프셋을 그대로 씀 (엔진 버전 고정값)
OFF_CAMERACACHE_POV = 0x10        # FCameraCacheEntry::POV
OFF_VIEWINFO_LOCATION = 0x0       # FMinimalViewInfo::Location
OFF_VIEWINFO_ROTATION = 0x18      # FMinimalViewInfo::Rotation
OFF_VIEWINFO_FOV = 0x30           # FMinimalViewInfo::FOV
OFF_SCENECOMPONENT_BOUNDS = 0x108 # USceneComponent::Bounds (FBoxSphereBounds)
OFF_CLEON_IS_HUNTER = 0xC3A       # BP cLeon Character::IsHunter
OFF_CLEON_IS_LIVE_SELF = 0xC3C    # BP cLeon Character::IsLiveSelf

# GUObjectArray를 가리키는 lea 명령 시그니처: 48 8D 05 ?? ?? ?? ?? 48 89 01 45 8B D1
SIG_GUOBJECTARRAY = bytes.fromhex("488D0500000000488901458BD1")
MASK_GUOBJECTARRAY = "1110000111111"

# FNamePool 후보 패턴 (여러 컴파일 변형)
FNAMEPOOL_CANDIDATES = [
    (bytes.fromhex("488D0D00000000E8000000004C8BC0"), "111000010000111"),
    (bytes.fromhex("488D0D00000000E800000000488B"), "11100001000011"),
    (bytes.fromhex("488D3500000000"), "1110000"),
    (bytes.fromhex("488D3D00000000"), "1110000"),
]
FNAMEPOOL_DELTA = 0xE3B40

# 2026-08-30 Dumper-7 dump. These are diagnostics only: pattern scanning and
# reflection remain the source of truth so a game update can still be detected.
DUMP_REFERENCE_RVAS = {
    "GUObjectArray": 0x95DC5A0,
    "FNamePool": 0x977D900,
    "GWorld": 0x9613260,
    "ProcessEvent": 0x15AFFE0,
}

DUMP_REFERENCE_FIELDS = {
    "PlayerController.AcknowledgedPawn": 0x350,
    "Controller.ControlRotation": 0x320,
    "PlayerController.PlayerCameraManager": 0x360,
    "PlayerCameraManager.CameraCachePrivate": 0x1530,
    "GameStateBase.PlayerArray": 0x2C0,
    "PlayerState.PawnPrivate": 0x320,
    "Actor.RootComponent": 0x1B8,
    "SceneComponent.RelativeLocation": 0x140,
}


def read_ptr(pm, addr):
    try:
        return struct.unpack("<Q", pm.read_bytes(addr, 8))[0]
    except Exception:
        return 0


def read_u32(pm, addr):
    try:
        return struct.unpack("<I", pm.read_bytes(addr, 4))[0]
    except Exception:
        return 0


def read_u16(pm, addr):
    try:
        return struct.unpack("<H", pm.read_bytes(addr, 2))[0]
    except Exception:
        return 0


def read_u8(pm, addr):
    try:
        return pm.read_bytes(addr, 1)[0]
    except Exception:
        return 0


def read_f32(pm, addr):
    try:
        return struct.unpack("<f", pm.read_bytes(addr, 4))[0]
    except Exception:
        return 0.0


def read_vec3(pm, addr):
    try:
        return struct.unpack("<ddd", pm.read_bytes(addr, 24))
    except Exception:
        return (0.0, 0.0, 0.0)


def read_box_sphere_bounds(pm, addr):
    """Read UE5 FBoxSphereBounds: Origin, BoxExtent, SphereRadius."""
    try:
        values = struct.unpack("<ddddddd", pm.read_bytes(addr, 56))
    except Exception:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    origin = values[:3]
    extent = values[3:6]
    radius = values[6]
    if not all(0.01 < value < 10000.0 for value in extent):
        return None
    max_extent = max(extent)
    extent_radius = math.sqrt(sum(value * value for value in extent))
    if not max_extent <= radius <= extent_radius * 1.1:
        return None
    return {"origin": origin, "extent": extent, "radius": radius}


def write_vec3(pm, addr, value):
    """Write a three-component UE5 double vector/rotator."""
    try:
        values = tuple(float(component) for component in value)
        if len(values) != 3 or not all(math.isfinite(component) for component in values):
            return False
        pm.write_bytes(addr, struct.pack("<ddd", *values), 24)
        return True
    except Exception:
        return False


def read_tarray(pm, addr):
    """TArray 헤더(data ptr, count, capacity) 읽기."""
    data = read_ptr(pm, addr)
    count = read_u32(pm, addr + 8)
    return data, count


def mask_to_regex(pattern, mask):
    """mask 문자열('1'=고정 바이트, '0'=와일드카드)을 정규식으로 변환."""
    parts = []
    for i, b in enumerate(pattern):
        if mask[i] == "1":
            parts.append(re.escape(bytes([b])))
        else:
            parts.append(b".")
    return re.compile(b"".join(parts), re.DOTALL)



class MemScanner:
    """모듈 메모리를 한 번에 읽어 정규식으로 스캔 (원본보다 빠른 방식)."""

    def __init__(self, pm, module_name):
        self.pm = pm
        mod = pymem.process.module_from_name(pm.process_handle, module_name)
        if not mod:
            raise RuntimeError(f"모듈을 찾을 수 없음: {module_name}")
        self.base = mod.lpBaseOfDll
        self.size = mod.SizeOfImage
        self._data = None

    def _dump(self):
        if self._data is None:
            self._data = self.pm.read_bytes(self.base, self.size)
        return self._data

    def find(self, pattern, mask):
        regex = mask_to_regex(pattern, mask)
        m = regex.search(self._dump())
        return self.base + m.start() if m else 0

    def find_all(self, pattern, mask):
        regex = mask_to_regex(pattern, mask)
        for m in regex.finditer(self._dump()):
            yield self.base + m.start()


class NamePool:
    """FName 문자열 풀 디코더."""

    def __init__(self, pm, pool_addr):
        self.pm = pm
        self.pool_addr = pool_addr
        self.table_offset = 0x10
        self.style = "ue5"

    def _decode_one(self, entry_id, table_offset, style):
        block_idx = entry_id >> 16
        within = (entry_id & 0xFFFF) << 1
        block_addr = read_ptr(self.pm, self.pool_addr + table_offset + block_idx * 8)
        if not block_addr:
            return None
        header = read_u16(self.pm, block_addr + within)
        if style == "ue4":
            is_wide, length = header & 1, header >> 1
        elif style == "custom":
            is_wide, length = header & 1, (header >> 6) & 0x3FF
        else:  # ue5
            length, is_wide = header & 0x3FF, (header >> 10) & 1
        if not (0 < length <= 512):
            return None
        raw_len = length * 2 if is_wide else length
        raw = self.pm.read_bytes(block_addr + within + 2, raw_len)
        return raw.decode("utf-16-le", errors="ignore") if is_wide else raw.decode("latin-1")

    def resolve(self, entry_id):
        try:
            name = self._decode_one(entry_id, self.table_offset, self.style)
            if name is not None:
                return name
        except Exception:
            pass
        for table_offset in (0x8, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70):
            for style in ("custom", "ue5", "ue4"):
                try:
                    name = self._decode_one(entry_id, table_offset, style)
                    if name is not None:
                        self.table_offset, self.style = table_offset, style
                        return name
                except Exception:
                    continue
        return None


class ObjectTable:
    """GUObjectArray를 순회하며 UObject 인스턴스를 찾는다."""

    CHUNK_CAPACITY = 0x10000
    ENTRY_STRIDE = 0x18

    def __init__(self, pm, guobject_array, names):
        self.pm = pm
        self.guobject_array = guobject_array
        self.names = names
        self._class_cache = {}
        self._meta_class = None

    def name_of(self, obj):
        return self.names.resolve(read_u32(self.pm, obj + OFF_NAME_PRIVATE))

    def class_of(self, obj):
        return read_ptr(self.pm, obj + OFF_CLASS_PRIVATE)

    def class_name_of(self, obj):
        cls = self.class_of(obj)
        return self.name_of(cls) if cls else ""

    def all_objects(self):
        header = self.guobject_array + 0x10
        chunks_ptr = read_ptr(self.pm, header)
        total = read_u32(self.pm, header + 0x14)
        max_chunks = read_u32(self.pm, header + 0x18)
        if not chunks_ptr or total == 0 or max_chunks == 0:
            return
        remaining = total
        for chunk_idx in range(max_chunks):
            if remaining <= 0:
                break
            chunk = read_ptr(self.pm, chunks_ptr + chunk_idx * 8)
            if not chunk:
                break
            count_here = min(self.CHUNK_CAPACITY, remaining)
            for slot in range(count_here):
                obj = read_ptr(self.pm, chunk + slot * self.ENTRY_STRIDE)
                if obj:
                    yield obj
            remaining -= count_here

    def find_class(self, class_name):
        cached = self._class_cache.get(class_name)
        if cached and self.name_of(cached) == class_name:
            return cached
        if self._meta_class is None:
            for obj in self.all_objects():
                if self.name_of(obj) == "Class":
                    self._meta_class = obj
                    break
        if not self._meta_class:
            return 0
        for obj in self.all_objects():
            if self.class_of(obj) == self._meta_class and self.name_of(obj) == class_name:
                self._class_cache[class_name] = obj
                return obj
        return 0

    def first_instance_of(self, class_name, skip_default=True):
        cls = self.find_class(class_name)
        if not cls:
            return 0
        for obj in self.all_objects():
            if self.class_of(obj) == cls:
                name = self.name_of(obj)
                if skip_default and name and name.startswith("Default__"):
                    continue
                return obj
        return 0


class Reflection:
    """FProperty 체인을 걸어서 이름으로 오프셋을 찾는다 (UE 리플렉션)."""

    def __init__(self, pm, table):
        self.pm = pm
        self.table = table
        self.cache = {}

    def _field_name(self, field):
        return self.table.names.resolve(read_u32(self.pm, field + OFF_FIELD_NAME))

    def _find_on_class(self, cls, prop_name):
        prop = read_ptr(self.pm, cls + OFF_CHILD_PROPERTIES)
        depth = 0
        while prop and depth < 512:
            if self._field_name(prop) == prop_name:
                return read_u32(self.pm, prop + OFF_PROPERTY_OFFSET)
            prop = read_ptr(self.pm, prop + OFF_FIELD_NEXT)
            depth += 1
        return None

    def offset_of(self, class_name, prop_name):
        key = (class_name, prop_name)
        if key in self.cache:
            return self.cache[key]
        cls = self.table.find_class(class_name)
        if not cls:
            return None
        seen = set()
        result = None
        while cls and cls not in seen:
            seen.add(cls)
            result = self._find_on_class(cls, prop_name)
            if result is not None:
                break
            cls = read_ptr(self.pm, cls + OFF_SUPER_STRUCT)
        if result is not None:
            self.cache[key] = result
        return result

    def resolve_all(self, mapping):
        """mapping: {label: (class_name, prop_name)} -> {label: offset}"""
        out = {}
        for label, (cls_name, prop_name) in mapping.items():
            off = self.offset_of(cls_name, prop_name)
            if off is None:
                raise RuntimeError(f"오프셋 resolve 실패: {label} ({cls_name}.{prop_name})")
            out[label] = off
        return out


# 우리가 읽어야 할 필드들: {라벨: (리플렉션 클래스명, 프로퍼티명)}
FIELD_MAP = {
    "Engine.GameViewport": ("Engine", "GameViewport"),
    "GameViewportClient.World": ("GameViewportClient", "World"),
    "World.OwningGameInstance": ("World", "OwningGameInstance"),
    "World.GameState": ("World", "GameState"),
    "GameInstance.LocalPlayers": ("GameInstance", "LocalPlayers"),
    "Player.PlayerController": ("Player", "PlayerController"),
    "PlayerController.AcknowledgedPawn": ("PlayerController", "AcknowledgedPawn"),
    "Controller.ControlRotation": ("Controller", "ControlRotation"),
    "PlayerController.PlayerCameraManager": ("PlayerController", "PlayerCameraManager"),
    "PlayerCameraManager.CameraCachePrivate": ("PlayerCameraManager", "CameraCachePrivate"),
    "GameStateBase.PlayerArray": ("GameStateBase", "PlayerArray"),
    "PlayerState.PawnPrivate": ("PlayerState", "PawnPrivate"),
    "Actor.RootComponent": ("Actor", "RootComponent"),
    "SceneComponent.RelativeLocation": ("SceneComponent", "RelativeLocation"),
}


class GameLink:
    """게임 프로세스에 붙어서 World/Controller/Pawn까지 찾아주는 진입점."""

    def __init__(self):
        self.pm = pymem.Pymem(PROCESS_NAME)
        self.scanner = MemScanner(self.pm, PROCESS_NAME)
        self.guobject_array = self._find_guobject_array()
        if not self.guobject_array:
            raise RuntimeError("GUObjectArray를 찾지 못함")
        self.name_pool_addr = self._find_name_pool()
        if not self.name_pool_addr:
            raise RuntimeError("FNamePool을 찾지 못함")
        self.names = NamePool(self.pm, self.name_pool_addr)
        self.table = ObjectTable(self.pm, self.guobject_array, self.names)
        self.reflection = Reflection(self.pm, self.table)
        self.fields = self.reflection.resolve_all(FIELD_MAP)
        self.gengine = self.table.first_instance_of("GameEngine")
        if not self.gengine:
            raise RuntimeError("GEngine 인스턴스를 찾지 못함")

    def _find_guobject_array(self):
        hit = self.scanner.find(SIG_GUOBJECTARRAY, MASK_GUOBJECTARRAY)
        if not hit:
            return 0
        rel = struct.unpack("<i", self.pm.read_bytes(hit + 3, 4))[0]
        return hit + 7 + rel

    def _looks_like_name_pool(self, addr):
        if not addr:
            return False
        probe = NamePool(self.pm, addr)
        for entry_id in (0, 1, 2, 3):
            name = probe.resolve(entry_id)
            if name is not None and (name == "None" or (0 < len(name) <= 128 and name.isprintable())):
                return True
        return False

    def _find_name_pool(self):
        guess = self.guobject_array - FNAMEPOOL_DELTA
        if self._looks_like_name_pool(guess):
            return guess
        for pattern, mask in FNAMEPOOL_CANDIDATES:
            for hit in self.scanner.find_all(pattern, mask):
                rel = struct.unpack("<i", self.pm.read_bytes(hit + 3, 4))[0]
                candidate = hit + 7 + rel
                if self._looks_like_name_pool(candidate):
                    return candidate
        # Never continue with an unverified address. A stale address here makes
        # every reflected class/property name unreliable after a game update.
        return 0

    def dump_compatibility_report(self):
        """Return read-only diagnostics comparing runtime discovery to the dump."""
        runtime_rvas = {
            "GUObjectArray": self.guobject_array - self.scanner.base,
            "FNamePool": self.name_pool_addr - self.scanner.base,
        }
        rva_checks = {
            name: {
                "runtime": value,
                "dump": DUMP_REFERENCE_RVAS[name],
                "match": value == DUMP_REFERENCE_RVAS[name],
            }
            for name, value in runtime_rvas.items()
        }
        field_checks = {
            name: {
                "runtime": self.fields.get(name),
                "dump": expected,
                "match": self.fields.get(name) == expected,
            }
            for name, expected in DUMP_REFERENCE_FIELDS.items()
        }
        return {"rvas": rva_checks, "fields": field_checks}

    def camera_is_sane(self, camera=None):
        """Reject obviously invalid camera data before later calculations use it."""
        camera = camera or self.get_camera()
        if camera is None:
            return False
        values = (*camera["location"], *camera["rotation"], camera["fov"])
        if not all(float("-inf") < value < float("inf") for value in values):
            return False
        pitch, _yaw, roll = camera["rotation"]
        return 1.0 <= camera["fov"] <= 179.0 and abs(pitch) <= 360.0 and abs(roll) <= 360.0

    def get_world(self):
        viewport = read_ptr(self.pm, self.gengine + self.fields["Engine.GameViewport"])
        if not viewport:
            return 0
        return read_ptr(self.pm, viewport + self.fields["GameViewportClient.World"])

    def get_local_controller(self, world):
        if not world:
            return 0
        game_instance = read_ptr(self.pm, world + self.fields["World.OwningGameInstance"])
        if not game_instance:
            return 0
        lp_data, lp_count = read_tarray(self.pm, game_instance + self.fields["GameInstance.LocalPlayers"])
        if not lp_data or lp_count == 0:
            return 0
        local_player = read_ptr(self.pm, lp_data)
        if not local_player:
            return 0
        return read_ptr(self.pm, local_player + self.fields["Player.PlayerController"])

    def get_local_pawn(self, world=None):
        world = world or self.get_world()
        controller = self.get_local_controller(world)
        if not controller:
            return 0
        return read_ptr(self.pm, controller + self.fields["PlayerController.AcknowledgedPawn"])

    def get_camera(self, world=None):
        """플레이어 카메라의 위치·회전·FOV를 읽는다.

        경로: Controller -> PlayerCameraManager -> CameraCachePrivate(FCameraCacheEntry)
              -> POV(FMinimalViewInfo) -> Location / Rotation / FOV
        """
        controller = self.get_local_controller(world or self.get_world())
        if not controller:
            return None
        cam_manager = read_ptr(self.pm, controller + self.fields["PlayerController.PlayerCameraManager"])
        if not cam_manager:
            return None
        cache = cam_manager + self.fields["PlayerCameraManager.CameraCachePrivate"]
        pov = cache + OFF_CAMERACACHE_POV
        location = read_vec3(self.pm, pov + OFF_VIEWINFO_LOCATION)
        rotation = read_vec3(self.pm, pov + OFF_VIEWINFO_ROTATION)
        fov = read_f32(self.pm, pov + OFF_VIEWINFO_FOV)
        return {"location": location, "rotation": rotation, "fov": fov}

    def get_control_rotation(self, controller=None, world=None):
        """AController::ControlRotation - 캐릭터가 실제로 '조준 중'인 각도.

        카메라 회전(get_camera의 rotation)과 대부분 비슷하지만, 스코프 배율이나
        일부 특수 상태에서는 둘이 어긋날 수 있어 원본은 이 값을 직접 씀.
        8단계에서 실제로 덮어쓰게 될 값도 이 필드.
        """
        controller = controller or self.get_local_controller(world or self.get_world())
        if not controller:
            return None
        return read_vec3(self.pm, controller + self.fields["Controller.ControlRotation"])

    def set_control_rotation(self, rotation, controller=None, world=None):
        """Write ControlRotation with the same double layout used when reading it."""
        controller = controller or self.get_local_controller(world or self.get_world())
        if not controller:
            return False
        return write_vec3(
            self.pm,
            controller + self.fields["Controller.ControlRotation"],
            rotation,
        )

    def get_actor_position(self, actor):
        root = read_ptr(self.pm, actor + self.fields["Actor.RootComponent"])
        if not root:
            return None
        return read_vec3(self.pm, root + self.fields["SceneComponent.RelativeLocation"])

    def get_actor_bounds(self, actor):
        """Return validated world-space bounds from the actor root component."""
        root = read_ptr(self.pm, actor + self.fields["Actor.RootComponent"])
        if not root:
            return None
        bounds = read_box_sphere_bounds(self.pm, root + OFF_SCENECOMPONENT_BOUNDS)
        if bounds is None:
            return None
        position = self.get_actor_position(actor)
        if position is None or math.dist(bounds["origin"], position) > 500.0:
            return None
        return bounds

    def get_cleon_character_state(self, actor):
        """Return role/liveness flags for a verified cLeon character Pawn."""
        class_name = self.table.class_name_of(actor) or ""
        if "BP_FirstPersonCharacter_cLeon_Character" not in class_name:
            return None
        return {
            "is_hunter": bool(read_u8(self.pm, actor + OFF_CLEON_IS_HUNTER)),
            "is_live": bool(read_u8(self.pm, actor + OFF_CLEON_IS_LIVE_SELF)),
        }

    def get_game_state(self, world=None):
        world = world or self.get_world()
        if not world:
            return 0
        return read_ptr(self.pm, world + self.fields["World.GameState"])

    def iter_player_states(self, world=None):
        game_state = self.get_game_state(world)
        if not game_state:
            return
        pa_data, pa_count = read_tarray(self.pm, game_state + self.fields["GameStateBase.PlayerArray"])
        if not pa_data or not (0 <= pa_count <= 4096):
            return
        seen = set()
        for i in range(pa_count):
            ps = read_ptr(self.pm, pa_data + i * 8)
            if ps and ps not in seen:
                seen.add(ps)
                yield ps

    def get_pawn_of(self, player_state):
        return read_ptr(self.pm, player_state + self.fields["PlayerState.PawnPrivate"])
