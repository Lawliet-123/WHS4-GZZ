#!/usr/bin/env python3
"""External box and skeleton overlay for MECCHA CHAMELEON (UE5.6)."""
import sys
import struct
import math
import ctypes
import time
import threading
from collections import deque
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

import pymem
from PyQt5.QtWidgets import (
    QApplication, QWidget, QCheckBox, QComboBox, QLabel,
    QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QFrame, QColorDialog,
    QSpinBox, QDoubleSpinBox, QMessageBox, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPen, QColor, QFont


# ---------------------------------------------------------------------------
# Bootstrap offsets: stable UObject/UStruct/FField layout used to resolve
# everything else dynamically at runtime.
# ---------------------------------------------------------------------------
OFFSETS = {
    "UObjectBase::ClassPrivate": 0x10,
    "UObjectBase::NamePrivate": 0x18,
    "UObjectBase::OuterPrivate": 0x20,

    "UStruct::SuperStruct": 0x40,
    "UStruct::ChildProperties": 0x50,

    "FField::Next": 0x18,
    "FField::NamePrivate": 0x20,
    "FProperty::Offset_Internal": 0x44,

    # Nested struct layouts are extremely stable; keep as fallback.
    "FCameraCacheEntry::POV": 0x10,
    "FMinimalViewInfo::Location": 0x0,
    "FMinimalViewInfo::Rotation": 0x18,
    "FMinimalViewInfo::FOV": 0x30,
}


# Reflected fields below were checked against the Dumper-7 SDK for
# 5.6.1-44394996+++UE5+Release-5.6-Chameleon.  The three explicitly native
# fields are not named by the SDK (they are inside padding) and therefore stay
# behind the supported executable fingerprints.
BUILD_OFFSETS = {
    "BP_FirstPersonCharacter_Main_C::Mesh": 0x418,
    "BP_FirstPersonCharacter_Main_C::BodyCapsule": 0x420,
    "BP_FirstPersonCharacter_Main_C::Dead": 0x5AA,
    "USkinnedMeshComponent::SkeletalMesh": 0x578,
    "USkinnedMeshComponent::SkinnedAsset": 0x580,
    "USkinnedMeshComponent::LeaderPoseComponent": 0x588,
    # Fingerprint-gated native double-buffer fallback.  The SDK-declared
    # CachedComponentSpaceTransforms field below is the primary pose source.
    "USkinnedMeshComponent::ComponentSpaceTransformsArray": 0x5F0,
    "USkinnedMeshComponent::CurrentReadComponentTransforms": 0x638,
    "USkeletalMeshComponent::CachedComponentSpaceTransforms": 0x9B8,
    "USkeletalMesh::Skeleton": 0xF8,
    # Non-reflected native field.  The exact shipping executable's
    # K2_GetComponentToWorld getter copies this FTransform from +0x1E0.
    "USceneComponent::ComponentToWorld": 0x1E0,
    "USceneComponent::TransformFlags": 0x1A0,
    "UCapsuleComponent::CapsuleHalfHeight": 0x540,
    "UCapsuleComponent::CapsuleRadius": 0x544,
}

VERIFIED_PROPERTY_MAP = {
    "BP_FirstPersonCharacter_Main_C::Mesh":
        ("BP_FirstPersonCharacter_Main_C", "Mesh"),
    "BP_FirstPersonCharacter_Main_C::BodyCapsule":
        ("BP_FirstPersonCharacter_Main_C", "BodyCapsule"),
    "BP_FirstPersonCharacter_Main_C::Dead":
        ("BP_FirstPersonCharacter_Main_C", "Dead"),
    "USkinnedMeshComponent::SkeletalMesh":
        ("SkinnedMeshComponent", "SkeletalMesh"),
    "USkinnedMeshComponent::SkinnedAsset":
        ("SkinnedMeshComponent", "SkinnedAsset"),
    "USkinnedMeshComponent::LeaderPoseComponent":
        ("SkinnedMeshComponent", "LeaderPoseComponent"),
    "USkeletalMeshComponent::CachedComponentSpaceTransforms":
        ("SkeletalMeshComponent", "CachedComponentSpaceTransforms"),
    "USkeletalMesh::Skeleton":
        ("SkeletalMesh", "Skeleton"),
    "UCapsuleComponent::CapsuleHalfHeight":
        ("CapsuleComponent", "CapsuleHalfHeight"),
    "UCapsuleComponent::CapsuleRadius":
        ("CapsuleComponent", "CapsuleRadius"),
    "USceneComponent::TransformFlags":
        ("SceneComponent", "bAbsoluteLocation"),
}

NATIVE_BUILD_OFFSET_KEYS = (
    "USceneComponent::ComponentToWorld",
    "USkinnedMeshComponent::ComponentSpaceTransformsArray",
    "USkinnedMeshComponent::CurrentReadComponentTransforms",
)

LAZY_BLUEPRINT_PROPERTY_KEYS = frozenset((
    "BP_FirstPersonCharacter_Main_C::Mesh",
    "BP_FirstPersonCharacter_Main_C::BodyCapsule",
    "BP_FirstPersonCharacter_Main_C::Dead",
))


@dataclass(frozen=True)
class SkeletonProfile:
    skeleton_name: str
    bone_names: Tuple[str, ...]
    parents: Tuple[int, ...]
    draw_edges: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class SkeletonPose:
    profile_name: str
    world_points: Tuple[Tuple[float, float, float], ...]
    draw_edges: Tuple[Tuple[int, int], ...]
    # Component-space points and mesh identity let a collector reject/rebase a
    # sample without ever keeping stale world-space bones on screen.
    component_points: Tuple[Tuple[float, float, float], ...] = ()
    mesh: int = 0


@dataclass(frozen=True)
class CapsuleGeometry:
    center: Tuple[float, float, float]
    half_height: Optional[float]
    radius: Optional[float]


@dataclass(frozen=True)
class CameraSnapshot:
    loc: Tuple[float, float, float]
    rot: Tuple[float, float, float]
    fov: float

    def __getitem__(self, key):
        # Preserve the reader/projection mapping interface while preventing the
        # GUI or a later camera sample from mutating a published frame.
        if key not in ("loc", "rot", "fov"):
            raise KeyError(key)
        return getattr(self, key)


@dataclass(frozen=True)
class PlayerRenderSnapshot:
    is_local: bool
    position: Tuple[float, float, float]
    index: int
    actor: int
    role: Optional[str]
    capsule: Optional[CapsuleGeometry]
    pose: Optional[SkeletonPose]
    root_transform: Optional[tuple] = None
    pose_captured_at: Optional[float] = None


@dataclass(frozen=True)
class CachedSkeletonPose:
    captured_at: float
    pose: SkeletonPose
    actor_position: Tuple[float, float, float]
    root_transform: Optional[tuple]
    world_epoch: int


@dataclass(frozen=True)
class FrameRenderSnapshot:
    sequence: int
    started_at: float
    finished_at: float
    collection_ms: float
    camera: Optional[CameraSnapshot]
    local_role: Optional[str]
    players: Tuple[PlayerRenderSnapshot, ...]
    stats: Tuple[Tuple[str, object], ...]
    skeleton_failures: Tuple[Tuple[str, int], ...]
    error: Optional[str] = None


class LatestSnapshotStore:
    """One overwrite-only slot: slow reads can never build a render backlog."""

    def __init__(self, initial):
        self._lock = threading.Lock()
        self._latest = initial

    def publish(self, snapshot):
        with self._lock:
            self._latest = snapshot

    def publish_if_sequence(self, snapshot, expected_sequence):
        """Replace only the still-current base frame with its enrichment."""
        with self._lock:
            if getattr(self._latest, "sequence", None) != expected_sequence:
                return False
            self._latest = snapshot
            return True

    def latest(self):
        with self._lock:
            return self._latest


# Parsed from the cooked Skeleton/SkeletalMesh assets shipped with the same
# game build.  Auxiliary LINK mouth/eye bones are intentionally not part of
# draw_edges; their real hierarchy remains in parents and is still count-checked.
PAINTMAN_PROFILE = SkeletonProfile(
    skeleton_name="paintman_Skeleton",
    bone_names=(
        "amm", "loot", "spine1", "spine2", "spine3", "neck", "head",
        "head_end", "shoulder_L", "upper_arm_L", "lower_arm_L", "hand_L",
        "hand_L_end", "shoulder_R", "upper_arm_R", "lower_arm_R", "hand_R",
        "hand_R_end", "hip_L", "upper_leg_L", "lower_leg_L", "foot_L",
        "foot_L_end", "hip_R", "upper_leg_R", "lower_leg_R", "foot_R",
        "foot_R_end",
    ),
    parents=(-1, 0, 1, 2, 3, 4, 5, 6, 4, 8, 9, 10, 11, 4, 13, 14,
             15, 16, 1, 18, 19, 20, 21, 1, 23, 24, 25, 26),
    draw_edges=(
        (0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7),
        (4, 8), (8, 9), (9, 10), (10, 11), (11, 12),
        (4, 13), (13, 14), (14, 15), (15, 16), (16, 17),
        (1, 18), (18, 19), (19, 20), (20, 21), (21, 22),
        (1, 23), (23, 24), (24, 25), (25, 26), (26, 27),
    ),
)

NEWPENGUN_PROFILE = SkeletonProfile(
    skeleton_name="newpengun_Skeleton",
    bone_names=(
        "NP_amm", "ボーン", "ボーン_001", "ボーン_001_end", "spine1",
        "spine2", "spine3", "neck", "head", "ボーン_021", "ボーン_022",
        "U_mouth", "U_mouth_end", "ボーン_023", "L_mouth", "L_mouth_end",
        "ボーン_026", "eye_R", "eye_R_end", "ボーン_027", "eye_L",
        "eye_L_end", "shoulder_L", "upper_arm_L", "lower_arm_L", "hand_L",
        "hand_L_end", "shoulder_R", "upper_arm_R", "lower_arm_R", "hand_R",
        "hand_R_end", "hip_L", "leg_L", "foot_L", "foot_L_end", "hip_R",
        "leg_R", "foot_R", "foot_R_end",
    ),
    parents=(-1, 0, 1, 2, 1, 4, 5, 6, 7, 8, 9, 10, 11, 9, 13, 14,
             8, 16, 17, 8, 19, 20, 6, 22, 23, 24, 25, 6, 27, 28, 29, 30,
             1, 32, 33, 34, 1, 36, 37, 38),
    draw_edges=(
        (0, 1), (1, 4), (4, 5), (5, 6), (6, 7), (7, 8),
        (6, 22), (22, 23), (23, 24), (24, 25), (25, 26),
        (6, 27), (27, 28), (28, 29), (29, 30), (30, 31),
        (1, 32), (32, 33), (33, 34), (34, 35),
        (1, 36), (36, 37), (37, 38), (38, 39),
    ),
)

SKELETON_PROFILES: Dict[str, SkeletonProfile] = {
    PAINTMAN_PROFILE.skeleton_name.lower(): PAINTMAN_PROFILE,
    NEWPENGUN_PROFILE.skeleton_name.lower(): NEWPENGUN_PROFILE,
}

SKELETON_MESH_PROFILES = {
    ("paintman", "paintman_skeleton"): PAINTMAN_PROFILE,
    ("paintman_hukuyoka", "paintman_skeleton"): PAINTMAN_PROFILE,
    ("paintman_cube", "paintman_skeleton"): PAINTMAN_PROFILE,
    ("sk_link_penguin", "newpengun_skeleton"): NEWPENGUN_PROFILE,
}


# ---------------------------------------------------------------------------
# Dynamic offset resolver: walks class FField property chains.
# ---------------------------------------------------------------------------
class OffsetResolver:
    """Resolves engine class property offsets by walking ChildProperties."""

    def __init__(self, pm, objects):
        self.pm = pm
        self.objects = objects
        self.cache = dict(OFFSETS)

    def _field_name(self, field):
        return self.objects.fnames.resolve(ru32(self.pm, field + self.cache["FField::NamePrivate"]))

    def _resolve_on_class(self, cls, prop_name):
        prop = rp(self.pm, cls + self.cache["UStruct::ChildProperties"])
        depth = 0
        while prop and depth < 512:
            name = self._field_name(prop)
            if name == prop_name:
                return ru32(self.pm, prop + self.cache["FProperty::Offset_Internal"])
            prop = rp(self.pm, prop + self.cache["FField::Next"])
            depth += 1
        return None

    def resolve_from_class(self, cls, prop_name):
        """Resolve a reflected field from an already loaded class hierarchy."""
        seen = set()
        current = cls
        while current and current not in seen:
            seen.add(current)
            offset = self._resolve_on_class(current, prop_name)
            if offset is not None:
                return offset
            current = rp(
                self.pm, current + self.cache["UStruct::SuperStruct"])
        return None

    def resolve(self, class_name, prop_name):
        key = f"{class_name}::{prop_name}"
        if key in self.cache:
            return self.cache[key]
        cls = self.objects.find_class(class_name)
        if not cls:
            return None
        offset = self.resolve_from_class(cls, prop_name)
        if offset is not None:
            self.cache[key] = offset
        return offset

    def resolve_map(self, mapping):
        out = {}
        for key, (cls, prop) in mapping.items():
            val = self.resolve(cls, prop)
            if val is None:
                raise RuntimeError(f"Could not resolve offset {key} ({cls}.{prop})")
            out[key] = val
        return out


# ---------------------------------------------------------------------------
# Memory primitives
# ---------------------------------------------------------------------------
def rp(pm, addr):
    try:
        return struct.unpack("<Q", pm.read_bytes(addr, 8))[0]
    except Exception:
        return 0


def ru32(pm, addr):
    try:
        return struct.unpack("<I", pm.read_bytes(addr, 4))[0]
    except Exception:
        return 0


def ru16(pm, addr):
    try:
        return struct.unpack("<H", pm.read_bytes(addr, 2))[0]
    except Exception:
        return 0


def rvec3(pm, addr):
    try:
        return struct.unpack("<ddd", pm.read_bytes(addr, 24))
    except Exception:
        return (0.0, 0.0, 0.0)


def rrot(pm, addr):
    """Read an FRotator (Pitch/Yaw/Roll as floats, 12 bytes)."""
    try:
        return struct.unpack("<fff", pm.read_bytes(addr, 12))
    except Exception:
        return (0.0, 0.0, 0.0)


def read_array(pm, addr):
    """Read a UE TArray header (data pointer, signed Num, signed Max)."""
    try:
        return struct.unpack("<Qii", pm.read_bytes(addr, 0x10))
    except Exception:
        return 0, 0, 0


def dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def finite_vector(values, limit=1.0e8):
    return all(math.isfinite(v) and abs(v) <= limit for v in values)


def weak_object_ptr_is_null(raw):
    """Match this build's weak-pointer getter: serial 0 resolves as null."""
    if len(raw) != 8:
        return False
    try:
        _, serial = struct.unpack("<ii", raw)
    except struct.error:
        return False
    return serial == 0


def read_pe_fingerprint(pm, module_base):
    """Return (SizeOfImage, TimeDateStamp, CheckSum) from a loaded PE image."""
    try:
        if pm.read_bytes(module_base, 2) != b"MZ":
            return None
        pe_offset = ru32(pm, module_base + 0x3C)
        pe = module_base + pe_offset
        if pe_offset < 0x40 or pe_offset > 0x1000 or pm.read_bytes(pe, 4) != b"PE\0\0":
            return None
        timestamp = ru32(pm, pe + 0x8)
        optional = pe + 0x18
        magic = ru16(pm, optional)
        if magic not in (0x10B, 0x20B):
            return None
        image_size = ru32(pm, optional + 0x38)
        checksum = ru32(pm, optional + 0x40)
        return image_size, timestamp, checksum
    except Exception:
        return None


def invert_matrix4(matrix):
    """Invert a finite 4x4 matrix with Gauss-Jordan elimination."""
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        return None
    if not finite_vector((value for row in matrix for value in row), 1.0e12):
        return None

    work = []
    for row_index, row in enumerate(matrix):
        identity = [1.0 if row_index == col else 0.0 for col in range(4)]
        work.append([float(value) for value in row] + identity)

    for col in range(4):
        pivot_row = max(range(col, 4), key=lambda row: abs(work[row][col]))
        pivot = work[pivot_row][col]
        if abs(pivot) < 1.0e-12:
            return None
        if pivot_row != col:
            work[col], work[pivot_row] = work[pivot_row], work[col]

        pivot = work[col][col]
        work[col] = [value / pivot for value in work[col]]
        for row in range(4):
            if row == col:
                continue
            factor = work[row][col]
            if factor == 0.0:
                continue
            work[row] = [work[row][i] - factor * work[col][i] for i in range(8)]

    inverse = tuple(tuple(work[row][col] for col in range(4, 8)) for row in range(4))
    return inverse if finite_vector((value for row in inverse for value in row), 1.0e12) else None


def transform_position_row(point, matrix):
    """Apply an Unreal FMatrix using its row-vector convention."""
    x, y, z = point
    result = (
        x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0] + matrix[3][0],
        x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1] + matrix[3][1],
        x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2] + matrix[3][2],
        x * matrix[0][3] + y * matrix[1][3] + z * matrix[2][3] + matrix[3][3],
    )
    # This path handles affine component transforms, never projection matrices.
    # Requiring w=1 keeps a corrupt/projective matrix from being normalized into
    # a plausible-looking bone position.
    if not finite_vector(result, 1.0e12) or abs(result[3] - 1.0) > 1.0e-6:
        return None
    world = result[:3]
    return world if finite_vector(world) else None


def multiply_matrix4(left, right):
    return tuple(
        tuple(sum(left[row][k] * right[k][col] for k in range(4))
              for col in range(4))
        for row in range(4)
    )


def _rotate_vector_quaternion(vector, quaternion):
    """Apply Unreal's FQuat::RotateVector formula to one vector."""
    vx, vy, vz = vector
    qx, qy, qz, qw = quaternion
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def decode_ftransform(raw):
    """Validate a UE5 double-precision FTransform and its affine inverse."""
    if len(raw) != 0x60:
        return None
    try:
        quaternion = struct.unpack_from("<4d", raw, 0x00)
        translation = struct.unpack_from("<3d", raw, 0x20)
        scale = struct.unpack_from("<3d", raw, 0x40)
    except struct.error:
        return None
    if (not finite_vector(quaternion, 10.0)
            or not finite_vector(translation)
            or not finite_vector(scale, 1.0e4)
            or any(abs(value) < 1.0e-6 for value in scale)):
        return None
    norm_sq = sum(value * value for value in quaternion)
    if not math.isfinite(norm_sq) or abs(norm_sq - 1.0) > 0.01:
        return None
    norm = math.sqrt(norm_sq)
    quaternion = tuple(value / norm for value in quaternion)

    axes = (
        _rotate_vector_quaternion((scale[0], 0.0, 0.0), quaternion),
        _rotate_vector_quaternion((0.0, scale[1], 0.0), quaternion),
        _rotate_vector_quaternion((0.0, 0.0, scale[2]), quaternion),
    )
    if not finite_vector((value for axis in axes for value in axis), 1.0e4):
        return None
    matrix = (
        (*axes[0], 0.0),
        (*axes[1], 0.0),
        (*axes[2], 0.0),
        (*translation, 1.0),
    )

    inverse = invert_matrix4(matrix)
    if inverse is None:
        return None
    for product in (multiply_matrix4(matrix, inverse),
                    multiply_matrix4(inverse, matrix)):
        for row in range(4):
            for col in range(4):
                expected = 1.0 if row == col else 0.0
                if abs(product[row][col] - expected) > 1.0e-5:
                    return None
    return matrix, inverse, translation, scale


def clip_line_to_viewport(p1, p2, width, height):
    """Liang-Barsky clip of a 2D segment to the overlay viewport."""
    if width <= 0 or height <= 0 or not finite_vector((*p1, *p2), 1.0e7):
        return None
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1), (dx, width - x1),
                 (-dy, y1), (dy, height - y1)):
        if abs(p) < 1.0e-12:
            if q < 0.0:
                return None
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > t1:
                return None
            t0 = max(t0, ratio)
        else:
            if ratio < t0:
                return None
            t1 = min(t1, ratio)
    return ((x1 + t0 * dx, y1 + t0 * dy),
            (x1 + t1 * dx, y1 + t1 * dy))


# ---------------------------------------------------------------------------
# Pattern scanner
# ---------------------------------------------------------------------------
class PatternScanner:
    CHUNK_SIZE = 0x200000  # 2 MiB chunks to avoid huge allocations on shipping exes

    def __init__(self, pm, module_name):
        self.pm = pm
        self.module = pymem.process.module_from_name(pm.process_handle, module_name)
        if not self.module:
            raise RuntimeError(f"Module {module_name} not found")
        self.base = self.module.lpBaseOfDll
        self.size = self.module.SizeOfImage

    def _match_at(self, data, offset, pattern, mask):
        pat_len = len(pattern)
        for j in range(pat_len):
            if mask[j] and data[offset + j] != pattern[j]:
                return False
        return True

    def scan_all(self, pattern, mask):
        """Yield every match address in ascending order."""
        pat_len = len(pattern)
        if pat_len == 0 or self.size == 0:
            return
        step = self.CHUNK_SIZE
        for start in range(0, self.size, step):
            # Overlap reads by pat_len so patterns spanning chunk boundaries aren't missed.
            end = min(start + step + pat_len, self.size)
            read_size = end - start
            try:
                data = self.pm.read_bytes(self.base + start, read_size)
            except Exception:
                continue
            scan_len = len(data) - pat_len
            for i in range(scan_len):
                if self._match_at(data, i, pattern, mask):
                    yield self.base + start + i

    def scan(self, pattern, mask):
        for addr in self.scan_all(pattern, mask):
            return addr
        return 0


# ---------------------------------------------------------------------------
# FName + object array
# ---------------------------------------------------------------------------
class FNameResolver:
    # FNamePool block-pointer tables sit at different offsets depending on UE5 version.
    BLOCK_TABLE_OFFSETS = (0x8, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38,
                           0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70)

    def __init__(self, pm, fname_pool):
        self.pm = pm
        self.fname_pool = fname_pool
        self.block_table_off = 0x10
        self.header_style = "ue5"  # or "ue4"
        self._detect_layout()

    def _read_entry(self, entry_id, table_off, style):
        block_idx = entry_id >> 16
        within = (entry_id & 0xFFFF) << 1
        block_addr = rp(self.pm, self.fname_pool + table_off + block_idx * 8)
        if not block_addr:
            return None
        hdr = ru16(self.pm, block_addr + within)
        if style == "ue4":
            # UE4: bIsWide (1 bit), Len (15 bits)
            is_wide = hdr & 1
            length = hdr >> 1
        elif style == "custom":
            # MECCHA CHAMELEON build: bIsWide (bit 0), Len (bits 6-15)
            is_wide = hdr & 1
            length = (hdr >> 6) & 0x3FF
        else:
            # Standard UE5: Len (10 bits), bIsWide (1 bit), LowercaseProbeHash (5 bits)
            length = hdr & 0x3FF
            is_wide = (hdr >> 10) & 1
        if length == 0 or length > 512:
            return None
        if is_wide:
            raw = self.pm.read_bytes(block_addr + within + 2, length * 2)
            return raw.decode("utf-16-le", errors="ignore")
        else:
            raw = self.pm.read_bytes(block_addr + within + 2, length)
            return raw.decode("latin-1")

    def _detect_layout(self):
        """Probe block-table offsets and header styles until entry 0 is 'None'."""
        for off in self.BLOCK_TABLE_OFFSETS:
            for style in ("custom", "ue5", "ue4"):
                try:
                    if self._read_entry(0, off, style) == "None":
                        self.block_table_off = off
                        self.header_style = style
                        return
                except Exception:
                    continue

    def resolve(self, entry_id):
        try:
            name = self._read_entry(entry_id, self.block_table_off, self.header_style)
            if name is not None:
                return name
        except Exception:
            pass
        # If the cached layout fails, re-probe once per call until something works.
        for off in self.BLOCK_TABLE_OFFSETS:
            for style in ("custom", "ue5", "ue4"):
                if off == self.block_table_off and style == self.header_style:
                    continue
                try:
                    name = self._read_entry(entry_id, off, style)
                    if name is not None:
                        self.block_table_off = off
                        self.header_style = style
                        return name
                except Exception:
                    continue
        return None


class UObjectArray:
    def __init__(self, pm, guobject_array, fname_pool):
        self.pm = pm
        self.guobject_array = guobject_array
        self.fnames = FNameResolver(pm, fname_pool)
        self._meta_class_addr = None
        self._class_cache = {}

    def _obj_name(self, obj):
        return self.fnames.resolve(ru32(self.pm, obj + OFFSETS["UObjectBase::NamePrivate"]))

    def _obj_class(self, obj):
        return rp(self.pm, obj + OFFSETS["UObjectBase::ClassPrivate"])

    def iter_objects(self):
        objects_ptr = rp(self.pm, self.guobject_array + 0x10)
        if not objects_ptr:
            return
        chunk_idx = 0
        while chunk_idx < 64:
            chunk = rp(self.pm, objects_ptr + chunk_idx * 8)
            if not chunk:
                break
            for within in range(0x10000):
                obj = rp(self.pm, chunk + within * 0x18)
                if obj:
                    yield obj
            chunk_idx += 1

    def _meta_class(self):
        # Don't cache a failed search; the object array may still be loading.
        if self._meta_class_addr is None or not self._meta_class_addr:
            for obj in self.iter_objects():
                if self._obj_name(obj) == "Class":
                    self._meta_class_addr = obj
                    break
        return self._meta_class_addr

    def find_class(self, name):
        cached = self._class_cache.get(name)
        if cached:
            # Validate the cached pointer still names itself correctly.
            if self._obj_name(cached) == name:
                return cached
            del self._class_cache[name]
        meta = self._meta_class()
        if not meta:
            return 0
        for obj in self.iter_objects():
            if self._obj_class(obj) == meta and self._obj_name(obj) == name:
                self._class_cache[name] = obj
                return obj
        return 0

    def find_first_instance(self, class_name, skip_default=True):
        cls = self.find_class(class_name)
        if not cls:
            return 0
        for obj in self.iter_objects():
            if self._obj_class(obj) == cls:
                name = self._obj_name(obj)
                if skip_default and name and name.startswith("Default__"):
                    continue
                return obj
        return 0


# ---------------------------------------------------------------------------
# Game reader
# ---------------------------------------------------------------------------
class MecchaESP:
    PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"
    MODULE_NAME = "PenguinHotel-Win64-Shipping.exe"
    SKELETON_PROFILE_MISS_RETRY_SECONDS = 2.0
    CLEON_ROSTER_REFRESH_SECONDS = 0.10
    CLEON_ROSTER_OFFSET_RETRY_SECONDS = 1.0
    CONTEXT_POINTER_REFRESH_SECONDS = 0.50
    REFLECTED_OFFSET_RETRY_SECONDS = 1.0
    PLAYER_ARRAY_DROP_GRACE_CYCLES = 6
    # (SizeOfImage, TimeDateStamp, CheckSum).  The final entry is the current
    # Steam executable; the relevant UE 5.6 component layouts were rechecked in
    # that binary before enabling native (non-reflected) skeleton offsets.
    SUPPORTED_BUILD_FINGERPRINTS = frozenset((
        (0x0A3FA000, 0x15CBD51C, 0x0A041AE7),
        (0x0A3FB000, 0x018D3C6F, 0x0A046E92),
        (0x0A3FB000, 0x4F2390A3, 0x0A03AB06),
    ))

    GUOBJECT_SIG = bytes([
        0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00,
        0x48, 0x89, 0x01, 0x45, 0x8B, 0xD1
    ])
    GUOBJECT_MASK = bytes([1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1])

    # Multiple FNamePool references can appear; we verify by trying to read names.
    FNAMEPOOL_PATTERNS = (
        # lea rcx,[FNamePool]; call FName::FName; mov r8,rax
        (bytes([0x48, 0x8D, 0x0D, 0x00, 0x00, 0x00, 0x00,
                0xE8, 0x00, 0x00, 0x00, 0x00,
                0x4C, 0x8B, 0xC0]),
         bytes([1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1])),
        # lea rcx,[FNamePool]; call FName::FName; mov rax,[rbx+...]
        (bytes([0x48, 0x8D, 0x0D, 0x00, 0x00, 0x00, 0x00,
                0xE8, 0x00, 0x00, 0x00, 0x00,
                0x48, 0x8B]),
         bytes([1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1])),
        # lea rsi,[FNamePool]
        (bytes([0x48, 0x8D, 0x35, 0x00, 0x00, 0x00, 0x00]),
         bytes([1, 1, 1, 0, 0, 0, 0])),
        # lea rdi,[FNamePool]
        (bytes([0x48, 0x8D, 0x3D, 0x00, 0x00, 0x00, 0x00]),
         bytes([1, 1, 1, 0, 0, 0, 0])),
    )
    FNAMEPOOL_DELTA = 0xE3B40

    OFFSET_MAP = {
        "UWorld::GameState": ("World", "GameState"),
        "UWorld::OwningGameInstance": ("World", "OwningGameInstance"),
        "UGameInstance::LocalPlayers": ("GameInstance", "LocalPlayers"),
        "UPlayer::PlayerController": ("Player", "PlayerController"),
        "UEngine::GameViewport": ("Engine", "GameViewport"),
        "UGameViewportClient::World": ("GameViewportClient", "World"),
        "AGameStateBase::PlayerArray": ("GameStateBase", "PlayerArray"),
        "APlayerState::PawnPrivate": ("PlayerState", "PawnPrivate"),
        "AController::PlayerState": ("Controller", "PlayerState"),
        "APlayerController::AcknowledgedPawn": ("PlayerController", "AcknowledgedPawn"),
        "APlayerController::PlayerCameraManager": ("PlayerController", "PlayerCameraManager"),
        "APlayerCameraManager::CameraCachePrivate": ("PlayerCameraManager", "CameraCachePrivate"),
        "AActor::RootComponent": ("Actor", "RootComponent"),
        "USceneComponent::AttachParent": ("SceneComponent", "AttachParent"),
        "USceneComponent::AttachSocketName":
            ("SceneComponent", "AttachSocketName"),
        "USceneComponent::RelativeLocation": ("SceneComponent", "RelativeLocation"),
        "USceneComponent::RelativeRotation": ("SceneComponent", "RelativeRotation"),
        "USceneComponent::RelativeScale3D": ("SceneComponent", "RelativeScale3D"),
        # Note: UWorld::PersistentLevel and ULevel::Actors are only used in the
        # level-actors fallback; they are resolved lazily with hardcoded defaults.
    }

    def __init__(self):
        self.pm = pymem.Pymem(self.PROCESS_NAME)
        module = pymem.process.module_from_name(
            self.pm.process_handle, self.MODULE_NAME)
        self._build_fingerprint = (
            read_pe_fingerprint(self.pm, module.lpBaseOfDll) if module else None)
        self._advanced_build_ok = (
            self._build_fingerprint in self.SUPPORTED_BUILD_FINGERPRINTS)
        self.guobject_array = self._scan_guobject_array()
        if not self.guobject_array:
            raise RuntimeError("Could not find GUObjectArray via pattern scan")
        self.fname_pool = self._scan_fname_pool()
        if not self.fname_pool:
            raise RuntimeError("Could not find FNamePool via pattern scan or delta fallback")
        self.objects = UObjectArray(self.pm, self.guobject_array, self.fname_pool)
        # Sanity-check globals; on failure we still open, but warn in overlay.
        self._globals_ok = self._verify_globals()
        self.resolver = OffsetResolver(self.pm, self.objects)
        self.offsets = self.resolver.resolve_map(self.OFFSET_MAP)
        self._layout_warnings = []
        self._configure_build_offsets()
        self._isa_cache = {}
        self._object_class_cache = {}
        self._reflected_offset_miss_cache = {}
        self._character_component_cache = {}
        self._actor_root_cache = {}
        self._skeleton_binding_cache = {}
        self._skeleton_profile_miss_cache = {}
        self._capsule_dimensions_cache = {}
        # These Blueprint fields are absent from a home-lobby SDK dump, but are
        # present once the cLeon gameplay package is loaded.  Resolve them from
        # the live GameState class on first use instead of permanently disabling
        # the roster path from the incomplete dump alone.
        self._cleon_roster_offsets = None
        self._cleon_roster_offset_retry_after = 0.0
        self._last_cleon_roster_snapshot = None
        self._playerstate_role_cache = {}
        self._skeleton_failure_counts = {}
        self._world_epoch = 0
        self._runtime_context_identity = None
        self._last_nontrivial_player_array_count = 0
        self._player_array_drop_streak = 0
        self._last_remote_rendered_count = 0
        self._last_actor_transforms = {}
        self._local_controller_cache = None
        self._camera_manager_cache = None
        # Fill in the stable nested struct offsets from the bootstrap dict.
        for key in ("FCameraCacheEntry::POV", "FMinimalViewInfo::Location",
                    "FMinimalViewInfo::Rotation", "FMinimalViewInfo::FOV"):
            self.offsets[key] = OFFSETS[key]
        self.gengine = self.objects.find_first_instance("GameEngine")
        if not self.gengine:
            raise RuntimeError("Could not find GEngine instance")

    def _add_layout_warning(self, message):
        warnings = getattr(self, "_layout_warnings", None)
        if warnings is None:
            warnings = []
            self._layout_warnings = warnings
        if message not in warnings:
            warnings.append(message)

    def _configure_build_offsets(self):
        """Install reflected layouts independently from native build layouts."""
        if not self._advanced_build_ok:
            self._add_layout_warning(
                f"unsupported PE fingerprint: {self._build_fingerprint!r}")

        # FProperty::Offset_Internal is runtime metadata.  These named fields
        # remain safe to use on a new executable build when reflection resolves
        # them; only unresolved fields on an exact known build use SDK constants.
        for key, (class_name, property_name) in VERIFIED_PROPERTY_MAP.items():
            expected = BUILD_OFFSETS[key]
            try:
                resolved = self.resolver.resolve(class_name, property_name)
            except Exception:
                resolved = None
            if resolved is not None:
                self.offsets[key] = resolved
                if resolved != expected:
                    self._add_layout_warning(
                        f"{key}: runtime {resolved:#x} != SDK {expected:#x}")
                continue
            if (self._advanced_build_ok
                    and key not in LAZY_BLUEPRINT_PROPERTY_KEYS):
                self.offsets[key] = expected
                self._add_layout_warning(
                    f"{key}: runtime unresolved; using exact SDK {expected:#x}")
            else:
                self._add_layout_warning(
                    f"{key}: runtime unresolved; waiting for loaded class")

        # Padding/native fields have no FProperty metadata.  Keep them behind
        # fingerprints that were checked against the shipping executable.
        if self._advanced_build_ok:
            for key in NATIVE_BUILD_OFFSET_KEYS:
                self.offsets[key] = BUILD_OFFSETS[key]

    def _scan_guobject_array(self):
        scanner = PatternScanner(self.pm, self.MODULE_NAME)
        addr = scanner.scan(self.GUOBJECT_SIG, self.GUOBJECT_MASK)
        if not addr:
            return 0
        rel = struct.unpack("<i", self.pm.read_bytes(addr + 3, 4))[0]
        return addr + 7 + rel

    def _scan_fname_pool(self):
        # The delta has been stable for this build; use it as the default.
        delta_candidate = self.guobject_array - self.FNAMEPOOL_DELTA
        if self._verify_fname_pool(delta_candidate):
            return delta_candidate
        # Try a few common FNamePool signatures as backups.
        scanner = PatternScanner(self.pm, self.MODULE_NAME)
        for sig, mask in self.FNAMEPOOL_PATTERNS:
            for addr in scanner.scan_all(sig, mask):
                rel = struct.unpack("<i", self.pm.read_bytes(addr + 3, 4))[0]
                candidate = addr + 7 + rel
                if self._verify_fname_pool(candidate):
                    return candidate
        # Even if unverified, fall back to the delta so the ESP can still open.
        # Name resolution may self-correct via the resolver's lazy offset probe.
        return delta_candidate

    def _verify_fname_pool(self, pool_addr):
        resolver = FNameResolver(self.pm, pool_addr)
        if resolver.resolve(0) == "None":
            return True
        # Some builds don't keep "None" at id 0; settle for any printable name.
        for probe in (0, 1, 2, 3, 4, 5):
            name = resolver.resolve(probe)
            if name and 0 < len(name) <= 128 and name.isprintable():
                return True
        return False

    def _verify_globals(self):
        # GUObjectArray + 0x10 is TUObjectArray::Objects; read its header.
        obj_array = self.guobject_array + 0x10
        num = ru32(self.pm, obj_array + 0x14)
        max_chunks = ru32(self.pm, obj_array + 0x18)
        if num == 0 or num > 10_000_000 or max_chunks == 0 or max_chunks > 64:
            return False
        # We should be able to find the meta Class object.
        return self.objects.find_class("Class") != 0

    def _get_world(self):
        viewport = rp(self.pm, self.gengine + self.offsets["UEngine::GameViewport"])
        if not viewport:
            return 0
        return rp(self.pm, viewport + self.offsets["UGameViewportClient::World"])

    def _set_runtime_context(self, world, gamestate):
        """Invalidate pointer-bound caches across world/GameState transitions."""
        identity = (world or 0, gamestate or 0)
        if getattr(self, "_runtime_context_identity", None) == identity:
            return
        self._runtime_context_identity = identity
        self._last_role_gamestate = identity[1]
        self._last_known_local_role = None
        self._last_cleon_roster_snapshot = None
        self._cleon_roster_offset_retry_after = 0.0
        self._playerstate_role_cache = {}
        self._last_nontrivial_player_array_count = 0
        self._player_array_drop_streak = 0
        self._last_remote_rendered_count = 0
        self._world_epoch = getattr(self, "_world_epoch", 0) + 1
        for cache_name in (
                "_character_component_cache", "_actor_root_cache",
                "_skeleton_binding_cache", "_skeleton_profile_miss_cache",
                "_capsule_dimensions_cache", "_object_class_cache",
                "_reflected_offset_miss_cache", "_isa_cache"):
            cache = getattr(self, cache_name, None)
            if cache is not None:
                cache.clear()
        self._local_controller_cache = None
        self._camera_manager_cache = None

    def _get_local_controller(self, world):
        if not world:
            return 0
        now = time.monotonic()
        cached = getattr(self, "_local_controller_cache", None)
        if (cached is not None and cached[0] == world
                and now < cached[2] and cached[1]):
            return cached[1]
        gi = rp(self.pm, world + self.offsets["UWorld::OwningGameInstance"])
        if not gi:
            return 0
        lp_data, lp_count, _ = read_array(self.pm, gi + self.offsets["UGameInstance::LocalPlayers"])
        if not lp_data or lp_count == 0:
            return 0
        local_player = rp(self.pm, lp_data)
        if not local_player:
            return 0
        controller = rp(
            self.pm, local_player + self.offsets["UPlayer::PlayerController"])
        if controller:
            self._local_controller_cache = (
                world, controller, now + self.CONTEXT_POINTER_REFRESH_SECONDS)
        return controller

    def _read_pov(self, pov_addr):
        """Read a minimal view POV from the given address."""
        location_offset = self.offsets["FMinimalViewInfo::Location"]
        rotation_offset = self.offsets["FMinimalViewInfo::Rotation"]
        fov_offset = self.offsets["FMinimalViewInfo::FOV"]
        start = min(location_offset, rotation_offset, fov_offset)
        end = max(location_offset + 0x18, rotation_offset + 0x18,
                  fov_offset + 4)
        raw = self.pm.read_bytes(pov_addr + start, end - start)
        return {
            "loc": struct.unpack_from("<3d", raw, location_offset - start),
            "rot": struct.unpack_from("<3d", raw, rotation_offset - start),
            "fov": struct.unpack_from("<f", raw, fov_offset - start)[0],
        }

    def get_camera(self):
        world = self._get_world()
        if not world:
            return None
        gamestate = rp(self.pm, world + self.offsets["UWorld::GameState"])
        # A single failed RPM must not look like a real world transition and
        # invalidate every pointer-bound cache.  A later nonzero identity will
        # still commit an actual map/GameState change immediately.
        if gamestate:
            self._set_runtime_context(world, gamestate)
        pc = self._get_local_controller(world)
        if not pc:
            return None
        now = time.monotonic()
        cached_cam = getattr(self, "_camera_manager_cache", None)
        if (cached_cam is not None and cached_cam[0] == pc
                and now < cached_cam[2]):
            cam = cached_cam[1]
        else:
            cam = rp(
                self.pm,
                pc + self.offsets["APlayerController::PlayerCameraManager"])
            if cam:
                self._camera_manager_cache = (
                    pc, cam, now + self.CONTEXT_POINTER_REFRESH_SECONDS)
        if not cam:
            return None

        # Primary: CameraCachePrivate (always reflects the current camera).
        cc = cam + self.offsets["APlayerCameraManager::CameraCachePrivate"]
        pov = cc + self.offsets["FCameraCacheEntry::POV"]
        try:
            camera = self._read_pov(pov)
        except Exception:
            camera = None

        # Fallback: PlayerCameraManager->ViewTarget.POV (some spectate/free-look modes).
        if (camera is None or
            (abs(camera["loc"][0]) < 0.01 and abs(camera["loc"][1]) < 0.01 and abs(camera["loc"][2]) < 0.01) or
            camera["fov"] <= 0.0):
            vt_off = self.offsets.get("APlayerCameraManager::ViewTarget")
            vt_pov_off = self.offsets.get("FTViewTarget::POV")
            if vt_off is not None and vt_pov_off is not None:
                try:
                    fallback = self._read_pov(cam + vt_off + vt_pov_off)
                    if fallback["fov"] > 0.0:
                        camera = fallback
                except Exception:
                    pass

        if camera is None or camera["fov"] <= 0.0:
            return None
        return camera

    def _class_name(self, obj):
        if not obj:
            return ""
        cls = rp(self.pm, obj + OFFSETS["UObjectBase::ClassPrivate"])
        return self.objects._obj_name(cls) if cls else ""

    def _object_class(self, obj):
        if not obj:
            return 0
        cache = getattr(self, "_object_class_cache", None)
        if cache is None:
            cache = {}
            self._object_class_cache = cache
        cls = cache.get(obj)
        if cls:
            return cls
        cls = rp(self.pm, obj + OFFSETS["UObjectBase::ClassPrivate"])
        if cls:
            cache[obj] = cls
        return cls

    def _class_is_a(self, cls, target_class_name):
        if not cls:
            return False
        cache_key = (cls, target_class_name)
        cached = self._isa_cache.get(cache_key)
        if cached is not None:
            return cached
        seen = set()
        current = cls
        for _ in range(64):
            if not current or current in seen:
                break
            seen.add(current)
            class_name = self.objects._obj_name(current)
            if not class_name:
                # Name and SuperStruct reads can fail transiently.  Never turn
                # one such miss into a process-lifetime negative type cache.
                return False
            if class_name == target_class_name:
                self._isa_cache[cache_key] = True
                return True
            try:
                raw_super = self.pm.read_bytes(
                    current + OFFSETS["UStruct::SuperStruct"], 8)
                current = struct.unpack("<Q", raw_super)[0]
            except Exception:
                return False
        self._isa_cache[cache_key] = False
        return False

    def _object_is_a(self, obj, target_class_name):
        """Check an object's UClass chain instead of trusting name substrings."""
        return self._class_is_a(self._object_class(obj), target_class_name)

    def _resolve_reflected_offset(self, key, obj=0):
        """Resolve a named field lazily once its Blueprint class is loaded."""
        existing = self.offsets.get(key)
        if existing is not None:
            return existing
        mapping = VERIFIED_PROPERTY_MAP.get(key)
        if mapping is None:
            return None

        resolved = None
        if obj:
            cls = self._object_class(obj)
            miss_cache = getattr(self, "_reflected_offset_miss_cache", None)
            if miss_cache is None:
                miss_cache = {}
                self._reflected_offset_miss_cache = miss_cache
            miss_key = (cls, key)
            now = time.monotonic()
            if cls and now >= miss_cache.get(miss_key, 0.0):
                try:
                    resolved = self.resolver.resolve_from_class(
                        cls, mapping[1])
                except Exception:
                    resolved = None
                if resolved is None:
                    # A process read can fail transiently while UE updates or
                    # streams metadata.  Throttle retries, but never turn one
                    # miss into a whole-match Players: 0 failure.
                    miss_cache[miss_key] = (
                        now + self.REFLECTED_OFFSET_RETRY_SECONDS)
                else:
                    miss_cache.pop(miss_key, None)
        else:
            try:
                resolved = self.resolver.resolve(*mapping)
            except Exception:
                resolved = None

        # FProperty offsets are unsigned 32-bit values, but rejecting implausibly
        # large class offsets keeps a damaged metadata walk from being cached.
        if (resolved is None or isinstance(resolved, bool)
                or not isinstance(resolved, int)
                or not 0 < resolved < 0x10000):
            return None
        self.offsets[key] = resolved
        expected = BUILD_OFFSETS.get(key)
        if expected is not None and resolved != expected:
            self._add_layout_warning(
                f"{key}: lazy runtime {resolved:#x} != SDK {expected:#x}")
        return resolved

    def character_role(self, actor):
        """Return the cooked cLeon role family for a character, if present."""
        cls = self._object_class(actor)
        if self._class_is_a(
                cls, "BP_FirstPersonCharacter_cLeon_Character_Hunter_C"):
            return "hunter"
        if self._class_is_a(
                cls, "BP_FirstPersonCharacter_cLeon_Character_Survivor_C"):
            return "survivor"
        return None

    def character_dead_state(self, actor, assume_character=False):
        """Read the game's independent one-byte Dead flag; None means unsafe."""
        if (not assume_character
                and not self._object_is_a(actor, "BP_FirstPersonCharacter_Main_C")):
            return None
        offset = self._resolve_reflected_offset(
            "BP_FirstPersonCharacter_Main_C::Dead", actor)
        if offset is None:
            return None
        for _ in range(2):
            try:
                before = self.pm.read_bytes(actor + offset, 1)
                after = self.pm.read_bytes(actor + offset, 1)
            except Exception:
                continue
            if (len(before) == 1 and before == after
                    and before[0] in (0, 1)):
                return bool(before[0])
        return None

    def _pointer_array_snapshot(self, address, max_count=64):
        """Read one bounded UObject-pointer TArray header and payload."""
        try:
            header = self.pm.read_bytes(address, 0x10)
            data, count, capacity = struct.unpack("<Qii", header)
            if (count < 0 or capacity < count or capacity > max_count
                    or (count and not data)):
                return None
            raw = self.pm.read_bytes(data, count * 8) if count else b""
        except Exception:
            return None
        pointers = tuple(
            pointer for pointer in struct.unpack(f"<{count}Q", raw)
            if pointer) if count else ()
        return header, raw, pointers

    def _stable_pointer_array_values(self, address, max_count=64):
        """Return one header/payload pair repeated unchanged, including nulls."""
        for _ in range(2):
            before = self._pointer_array_snapshot(address, max_count)
            after = self._pointer_array_snapshot(address, max_count)
            if (before is None or after is None
                    or before[0] != after[0] or before[1] != after[1]):
                continue
            _data, count, capacity = struct.unpack("<Qii", before[0])
            values = (
                struct.unpack(f"<{count}Q", before[1]) if count else ())
            return values, count, capacity
        return None

    def _cleon_live_rosters(self, gamestate):
        """Return replicated (hunters, live survivors, phase), or None."""
        if not gamestate or not self._object_is_a(gamestate, "BP_GameState_cLeon_C"):
            self._last_cleon_roster_snapshot = None
            self._cleon_roster_source = "not-cleon"
            return None
        now = time.monotonic()

        def _recent_snapshot(unavailable_source="unavailable"):
            cached = getattr(self, "_last_cleon_roster_snapshot", None)
            if (cached is not None and cached[0] == gamestate
                    and now - cached[1] <= 0.25):
                self._cleon_roster_source = "cached"
                return cached[2]
            self._cleon_roster_source = unavailable_source
            return None

        offsets = getattr(self, "_cleon_roster_offsets", None)
        if offsets is None:
            retry_after = getattr(
                self, "_cleon_roster_offset_retry_after", 0.0)
            if now < retry_after:
                return _recent_snapshot("resolver-cooldown")
            try:
                game_state_class = self._object_class(gamestate)
                hunter_offset = self.resolver.resolve_from_class(
                    game_state_class, "HuntersPlayerState")
                survivor_offset = self.resolver.resolve_from_class(
                    game_state_class, "LiveSurvivors_PlayerState")
                phase_offset = self.resolver.resolve_from_class(
                    game_state_class, "MainGamePhase")
            except Exception:
                self._cleon_roster_offset_retry_after = (
                    now + self.CLEON_ROSTER_OFFSET_RETRY_SECONDS)
                return _recent_snapshot("resolver-unavailable")
            resolved_offsets = (hunter_offset, survivor_offset, phase_offset)
            if (not game_state_class
                    or any(value is None or isinstance(value, bool)
                           or not isinstance(value, int)
                           or not 0 < value < 0x10000
                           for value in resolved_offsets)
                    or len(set(resolved_offsets)) != len(resolved_offsets)):
                self._cleon_roster_offset_retry_after = (
                    now + self.CLEON_ROSTER_OFFSET_RETRY_SECONDS)
                return _recent_snapshot("resolver-unavailable")
            offsets = resolved_offsets
            self._cleon_roster_offsets = offsets
            self._cleon_roster_offset_retry_after = 0.0
        else:
            cached = getattr(self, "_last_cleon_roster_snapshot", None)
            if (cached is not None and cached[0] == gamestate
                    and now - cached[1] < self.CLEON_ROSTER_REFRESH_SECONDS):
                self._cleon_roster_source = "cached"
                return cached[2]
        # Role changes can move one PlayerState between these arrays. Read both
        # as one transaction and accept only when the whole pair is unchanged.
        for _ in range(2):
            try:
                phase_before = self.pm.read_bytes(gamestate + offsets[2], 1)
            except Exception:
                continue
            if len(phase_before) == 1 and phase_before[0] == 3:
                self._last_cleon_roster_snapshot = None
                self._cleon_roster_source = "lobby"
                return frozenset(), frozenset(), 3
            hunters_before = self._pointer_array_snapshot(gamestate + offsets[0])
            survivors_before = self._pointer_array_snapshot(gamestate + offsets[1])
            hunters_after = self._pointer_array_snapshot(gamestate + offsets[0])
            survivors_after = self._pointer_array_snapshot(gamestate + offsets[1])
            try:
                phase_after = self.pm.read_bytes(gamestate + offsets[2], 1)
            except Exception:
                continue
            if (hunters_before is None or survivors_before is None
                    or hunters_after is None or survivors_after is None):
                continue
            if (phase_before != phase_after or len(phase_before) != 1
                    or phase_before[0] not in (0, 1, 2, 3)
                    or hunters_before[:2] != hunters_after[:2]
                    or survivors_before[:2] != survivors_after[:2]):
                continue
            hunters = frozenset(hunters_before[2])
            survivors = frozenset(survivors_before[2])
            if hunters & survivors:
                continue
            all_players = hunters | survivors
            if any(pointer % 8 != 0 or not self._object_is_a(
                    pointer, "BP_FirstPersonPlayerState_Online_cLeon_C")
                    for pointer in all_players):
                return _recent_snapshot()
            result = (hunters, survivors, phase_before[0])
            self._last_cleon_roster_snapshot = (gamestate, now, result)
            self._cleon_roster_source = "live"
            return result
        return _recent_snapshot()

    def _character_component(
            self, actor, offset_key, component_class, refresh=False):
        if not actor:
            return 0
        if not self._object_is_a(actor, "BP_FirstPersonCharacter_Main_C"):
            return 0
        offset = self._resolve_reflected_offset(offset_key, actor)
        if offset is None:
            return 0
        cache_key = (actor, offset_key)
        component_cache = getattr(self, "_character_component_cache", None)
        if component_cache is None:
            component_cache = {}
            self._character_component_cache = component_cache
        cached_component = component_cache.get(cache_key)
        if cached_component and not refresh:
            return cached_component
        component = rp(self.pm, actor + offset)
        if not component or not self._object_is_a(component, component_class):
            component_cache.pop(cache_key, None)
            return 0
        # Instanced Blueprint components in this class are actor-owned.  Rejecting
        # a different Outer prevents a plausible-looking stale pointer being used.
        outer = rp(self.pm, component + OFFSETS["UObjectBase::OuterPrivate"])
        if outer != actor:
            component_cache.pop(cache_key, None)
            return 0
        component_cache[cache_key] = component
        return component

    def _character_mesh(self, actor, refresh=False):
        return self._character_component(
            actor, "BP_FirstPersonCharacter_Main_C::Mesh", "SkeletalMeshComponent",
            refresh)

    def _character_capsule(self, actor):
        return self._character_component(
            actor, "BP_FirstPersonCharacter_Main_C::BodyCapsule", "CapsuleComponent")

    def _component_relative_transform(self, component):
        if not component or not self._object_is_a(component, "SceneComponent"):
            return None
        try:
            attach_offset = self.offsets["USceneComponent::AttachParent"]
            socket_offset = self.offsets["USceneComponent::AttachSocketName"]
            location_offset = self.offsets["USceneComponent::RelativeLocation"]
            rotation_offset = self.offsets["USceneComponent::RelativeRotation"]
            scale_offset = self.offsets["USceneComponent::RelativeScale3D"]
            flags_offset = self.offsets["USceneComponent::TransformFlags"]
            start = min(attach_offset, socket_offset, location_offset, rotation_offset,
                        scale_offset, flags_offset)
            end = max(attach_offset + 8, socket_offset + 8, location_offset + 24,
                      rotation_offset + 24, scale_offset + 24,
                      flags_offset + 1)
            block = self.pm.read_bytes(component + start, end - start)
            attach_parent = struct.unpack_from(
                "<Q", block, attach_offset - start)[0]
            attach_socket = block[socket_offset - start:socket_offset - start + 8]
            location = struct.unpack_from(
                "<3d", block, location_offset - start)
            rotation = struct.unpack_from(
                "<3d", block, rotation_offset - start)
            scale = struct.unpack_from(
                "<3d", block, scale_offset - start)
            flags = block[flags_offset - start]
        except Exception:
            return None
        if (not finite_vector(location) or not finite_vector(rotation, 1.0e6)
                or not finite_vector(scale, 1.0e4)
                or any(abs(value) < 1.0e-6 for value in scale)):
            return None
        # The three absolute-transform flags are bits 2..4 in the reflected
        # bAbsoluteLocation byte.  Their partial-parent semantics need the
        # engine's native transform path; reject them instead of approximating.
        if attach_parent:
            # A socket attachment also needs the parent's socket/bone transform;
            # composing only SceneComponent relatives would be incorrect.
            if attach_socket != bytes(8) or flags & 0x1C:
                return None
        return attach_parent, location, rotation, scale

    @staticmethod
    def _relative_transform_matrix(location, rotation, scale):
        forward, right, up = rotation_to_axes(rotation)
        matrix = (
            tuple(value * scale[0] for value in forward) + (0.0,),
            tuple(value * scale[1] for value in right) + (0.0,),
            tuple(value * scale[2] for value in up) + (0.0,),
            tuple(location) + (1.0,),
        )
        if not finite_vector(
                (value for row in matrix for value in row), 1.0e8):
            return None
        return matrix

    def _component_world_transform_from_chain(self, component):
        """Compose reflected relative transforms when native padding moved."""
        if not component:
            return None

        def _read_chain():
            current = component
            seen = set()
            chain = []
            for _ in range(16):
                if not current or current in seen:
                    return None
                seen.add(current)
                relative = self._component_relative_transform(current)
                if relative is None:
                    return None
                parent, location, rotation, scale = relative
                # Matrix composition is equivalent to UE FTransform composition
                # for positive uniform scales.  Reject non-uniform/mirrored
                # components, which could otherwise introduce unsupported shear.
                tolerance = 1.0e-6 * max(1.0, *(abs(value) for value in scale))
                if (any(value <= 0.0 for value in scale)
                        or max(scale) - min(scale) > tolerance):
                    return None
                chain.append((current, parent, location, rotation, scale))
                if not parent:
                    return tuple(chain)
                current = parent
            return None

        # Re-read attached chains to confirm topology.  Location/rotation are
        # expected to change while a character moves, so use the newer complete
        # sample instead of requiring byte-identical transform values.
        chain = None
        for _ in range(2):
            before = _read_chain()
            if (before is not None and len(before) == 1
                    and before[0][1] == 0):
                chain = before
                break
            after = _read_chain()
            before_topology = (
                tuple((node[0], node[1]) for node in before)
                if before is not None else None)
            after_topology = (
                tuple((node[0], node[1]) for node in after)
                if after is not None else None)
            if before_topology is not None and before_topology == after_topology:
                chain = after
                break
        if chain is None:
            return None

        local_to_world = None
        for _current, _parent, location, rotation, scale in chain:
            local_matrix = self._relative_transform_matrix(
                location, rotation, scale)
            if local_matrix is None:
                return None
            local_to_world = (
                local_matrix if local_to_world is None
                else multiply_matrix4(local_to_world, local_matrix))

        inverse = invert_matrix4(local_to_world)
        if inverse is None:
            return None
        translation = tuple(local_to_world[3][:3])
        world_scale = tuple(math.sqrt(sum(
            local_to_world[row][column] ** 2 for column in range(3)))
            for row in range(3))
        if (not finite_vector(translation)
                or not finite_vector(world_scale, 1.0e4)
                or any(value < 1.0e-6 for value in world_scale)):
            return None
        return tuple(chain), (local_to_world, inverse, translation, world_scale)

    def _root_world_transform(self, actor):
        if not actor:
            return None
        root = rp(self.pm, actor + self.offsets["AActor::RootComponent"])
        transform = self._component_relative_transform(root)
        if not root or transform is None or transform[0] != 0:
            return None
        return root, transform[1], transform[2], transform[3]

    def _capsule_transform_data(self, actor):
        capsule = self._character_capsule(actor)
        if not capsule:
            return None
        snapshot = self._component_world_transform_snapshot(capsule)
        if snapshot is None:
            return None
        _, (matrix, _, center, scale) = snapshot
        if not finite_vector(center):
            return None
        return capsule, center, scale, matrix[2][:3]

    def read_capsule_geometry(self, actor):
        """Read capsule center and optional exact dimensions in one pass."""
        transform_data = self._capsule_transform_data(actor)
        if transform_data is None:
            return None
        capsule, center, scale, local_up = transform_data
        half_offset = self.offsets.get("UCapsuleComponent::CapsuleHalfHeight")
        radius_offset = self.offsets.get("UCapsuleComponent::CapsuleRadius")
        dimensions_cache = getattr(self, "_capsule_dimensions_cache", None)
        if dimensions_cache is None:
            dimensions_cache = {}
            self._capsule_dimensions_cache = dimensions_cache
        dimensions = dimensions_cache.get(capsule)
        if dimensions is None and half_offset is not None and radius_offset == half_offset + 4:
            try:
                half_height, radius = struct.unpack(
                    "<ff", self.pm.read_bytes(capsule + half_offset, 8))
            except Exception:
                half_height = radius = 0.0
            if (math.isfinite(half_height) and math.isfinite(radius)
                    and 0.0 < radius <= half_height <= 10000.0):
                dimensions = (half_height, radius)
                dimensions_cache[capsule] = dimensions

        exact = (
            dimensions is not None
            and all(abs(value - 1.0) <= 1.0e-6 for value in scale)
            and math.hypot(local_up[0], local_up[1]) <= 2.0e-4
            and abs(abs(local_up[2]) - 1.0) <= 2.0e-4)
        if exact:
            return CapsuleGeometry(center, dimensions[0], dimensions[1])
        return CapsuleGeometry(center, None, None)

    def capsule_center(self, actor):
        geometry = self.read_capsule_geometry(actor)
        return geometry.center if geometry is not None else None

    def capsule_bounds(self, actor):
        """Return a verified upright BodyCapsule (center, half-height, radius)."""
        geometry = self.read_capsule_geometry(actor)
        if (geometry is None or geometry.half_height is None
                or geometry.radius is None):
            return None
        return geometry.center, geometry.half_height, geometry.radius

    def _component_world_transform_snapshot(self, component):
        """Read one validated, engine-updated ComponentToWorld FTransform."""
        transform_offset = self.offsets.get("USceneComponent::ComponentToWorld")
        flags_offset = self.offsets.get("USceneComponent::TransformFlags")
        if transform_offset is not None and flags_offset is not None:
            for _ in range(2):
                try:
                    start = min(flags_offset, transform_offset)
                    end = max(flags_offset + 1, transform_offset + 0x60)
                    # Both fields are close in known builds.  One bulk RPM call
                    # avoids four cross-process calls per component.
                    try:
                        block = self.pm.read_bytes(component + start, end - start)
                        flags = block[
                            flags_offset - start:flags_offset - start + 1]
                        raw = block[
                            transform_offset - start:transform_offset - start + 0x60]
                    except Exception:
                        flags = self.pm.read_bytes(component + flags_offset, 1)
                        raw = self.pm.read_bytes(
                            component + transform_offset, 0x60)
                except Exception:
                    break
                # bComponentToWorldUpdated is bit 0 in this exact native layout.
                if len(flags) != 1 or not flags[0] & 0x01 or len(raw) != 0x60:
                    continue
                decoded = decode_ftransform(raw)
                if decoded is not None:
                    return raw, decoded

        # ComponentToWorld lives in native padding and is deliberately absent
        # on an unverified executable.  All inputs below are reflected named
        # fields, so composing the attachment chain remains build-independent.
        return self._component_world_transform_from_chain(component)

    def _skeleton_profile(self, mesh):
        mesh_asset_offset = self.offsets.get("USkinnedMeshComponent::SkinnedAsset")
        legacy_mesh_offset = self.offsets.get("USkinnedMeshComponent::SkeletalMesh")
        skeleton_offset = self.offsets.get("USkeletalMesh::Skeleton")
        if (mesh_asset_offset is None or legacy_mesh_offset is None
                or skeleton_offset is None):
            return None
        mesh_asset = rp(self.pm, mesh + mesh_asset_offset)
        legacy_mesh_asset = rp(self.pm, mesh + legacy_mesh_offset)
        miss_cache = getattr(self, "_skeleton_profile_miss_cache", None)
        if miss_cache is None:
            miss_cache = {}
            self._skeleton_profile_miss_cache = miss_cache
        miss = miss_cache.get(mesh)
        if (miss is not None and miss[0] == mesh_asset
                and time.monotonic() < miss[1]):
            return None

        def _miss(retry_seconds=None):
            if mesh_asset:
                miss_cache[mesh] = (
                    mesh_asset,
                    (float("inf") if retry_seconds == float("inf")
                     else time.monotonic() + (
                         self.SKELETON_PROFILE_MISS_RETRY_SECONDS
                         if retry_seconds is None else retry_seconds)))
            return None

        binding_cache = getattr(self, "_skeleton_binding_cache", None)
        if binding_cache is None:
            binding_cache = {}
            self._skeleton_binding_cache = binding_cache
        cached = binding_cache.get(mesh)
        if (cached is not None and mesh_asset == cached[1]
                and legacy_mesh_asset in (0, cached[1])):
            return cached
        binding_cache.pop(mesh, None)
        if not mesh_asset or not self._object_is_a(mesh_asset, "SkeletalMesh"):
            return _miss()
        if legacy_mesh_asset and legacy_mesh_asset != mesh_asset:
            return _miss()
        mesh_asset_name = self.objects._obj_name(mesh_asset)
        skeleton = rp(self.pm, mesh_asset + skeleton_offset)
        if not skeleton or not self._object_is_a(skeleton, "Skeleton"):
            return _miss()
        skeleton_name = self.objects._obj_name(skeleton)
        key = ((mesh_asset_name or "").lower(), (skeleton_name or "").lower())
        profile = SKELETON_MESH_PROFILES.get(key)
        if profile is None:
            # A named asset/skeleton pair cannot become supported until the
            # profile table or world changes.  Cache that fact for this epoch;
            # unnamed/transient reads retain the short retry interval.
            return _miss(float("inf") if mesh_asset_name and skeleton_name else None)
        binding = (profile, mesh_asset, skeleton)
        miss_cache.pop(mesh, None)
        binding_cache[mesh] = binding
        return binding

    def _skeleton_fail(self, reason):
        counts = getattr(self, "_skeleton_failure_counts", None)
        if counts is None:
            counts = {}
            self._skeleton_failure_counts = counts
        counts[reason] = counts.get(reason, 0) + 1
        return None

    @staticmethod
    def _valid_pose_header(raw, expected_count):
        if raw is None or len(raw) != 0x10:
            return None
        data, count, capacity = struct.unpack("<Qii", raw)
        if (not data or data % 0x10 != 0 or count != expected_count
                or count < 1 or count > capacity or capacity > 512):
            return None
        return data, count

    def read_skeleton_pose(self, actor, actor_position=None):
        """Read one profile-checked pose with a bounded number of RPM calls.

        The SDK-declared CachedComponentSpaceTransforms array (+0x9B8) is the
        primary source.  The fingerprint-gated native double buffer remains a
        fallback, and only the selector's current buffer is ever accepted.
        """
        mesh = self._character_mesh(actor)
        if not mesh:
            return self._skeleton_fail("no_mesh")
        binding = getattr(self, "_skeleton_binding_cache", {}).get(mesh)
        if binding is None:
            binding = self._skeleton_profile(mesh)
        if binding is None:
            return self._skeleton_fail("profile")
        profile, expected_mesh_asset, _expected_skeleton = binding

        # A follower can source transforms through a LeaderPoseComponent and a
        # bone map. Until that mapping is implemented it must fail closed.
        leader_offset = self.offsets.get("USkinnedMeshComponent::LeaderPoseComponent")
        mesh_asset_offset = self.offsets.get("USkinnedMeshComponent::SkinnedAsset")
        legacy_mesh_offset = self.offsets.get("USkinnedMeshComponent::SkeletalMesh")
        cached_transforms_offset = self.offsets.get(
            "USkeletalMeshComponent::CachedComponentSpaceTransforms")
        transforms_base_offset = self.offsets.get(
            "USkinnedMeshComponent::ComponentSpaceTransformsArray")
        selector_offset = self.offsets.get(
            "USkinnedMeshComponent::CurrentReadComponentTransforms")
        if (leader_offset is None or mesh_asset_offset is None
                or legacy_mesh_offset is None
                or (cached_transforms_offset is None
                    and (transforms_base_offset is None or selector_offset is None))):
            return self._skeleton_fail("layout")
        expected_count = len(profile.bone_names)

        offsets_for_block = [mesh_asset_offset, legacy_mesh_offset, leader_offset]
        if cached_transforms_offset is not None:
            offsets_for_block.append(cached_transforms_offset)
        if transforms_base_offset is not None:
            offsets_for_block.extend((transforms_base_offset,
                                      transforms_base_offset + 0x10))
        if selector_offset is not None:
            offsets_for_block.append(selector_offset)
        block_start = min(offsets_for_block)
        block_end = max(offsets_for_block) + 0x10

        def _slice(block, offset, size):
            begin = offset - block_start
            return block[begin:begin + size]

        def _read_metadata():
            size = block_end - block_start
            try:
                return self.pm.read_bytes(mesh + block_start, size), None
            except Exception:
                # Sparse fake memories and unexpected protected gaps can still
                # use the same parser.  The live process normally takes the one
                # contiguous read above.
                block = bytearray(size)
                available = set()
                fields = [
                    (mesh_asset_offset, 8), (legacy_mesh_offset, 8),
                    (leader_offset, 8),
                ]
                if cached_transforms_offset is not None:
                    fields.append((cached_transforms_offset, 0x10))
                if transforms_base_offset is not None:
                    fields.extend(((transforms_base_offset, 0x10),
                                   (transforms_base_offset + 0x10, 0x10)))
                if selector_offset is not None:
                    fields.append((selector_offset, 4))
                for offset, field_size in fields:
                    try:
                        data = self.pm.read_bytes(mesh + offset, field_size)
                    except Exception:
                        continue
                    begin = offset - block_start
                    block[begin:begin + field_size] = data
                    available.add(offset)
                return bytes(block), frozenset(available)

        def _available(available, offset):
            return available is None or offset in available

        try:
            metadata_before, available_before = _read_metadata()
            if not all(_available(available_before, offset) for offset in (
                    mesh_asset_offset, legacy_mesh_offset, leader_offset)):
                return self._skeleton_fail("read")
            mesh_asset_before = struct.unpack(
                "<Q", _slice(metadata_before, mesh_asset_offset, 8))[0]
            legacy_before = struct.unpack(
                "<Q", _slice(metadata_before, legacy_mesh_offset, 8))[0]
            leader_before = _slice(metadata_before, leader_offset, 8)
        except Exception:
            return self._skeleton_fail("read")
        if (mesh_asset_before != expected_mesh_asset
                or legacy_before not in (0, expected_mesh_asset)):
            getattr(self, "_skeleton_binding_cache", {}).pop(mesh, None)
            return self._skeleton_fail("identity")
        if not weak_object_ptr_is_null(leader_before):
            return self._skeleton_fail("leader")

        candidates = []
        if (cached_transforms_offset is not None
                and _available(available_before, cached_transforms_offset)):
            candidate = _slice(metadata_before, cached_transforms_offset, 0x10)
            decoded = self._valid_pose_header(candidate, expected_count)
            if decoded is not None:
                candidates.append((
                    "sdk-cache", cached_transforms_offset, candidate, None,
                    decoded))
        if (transforms_base_offset is not None and selector_offset is not None
                and _available(available_before, selector_offset)):
            selector_before = _slice(metadata_before, selector_offset, 4)
            if len(selector_before) == 4:
                selector = struct.unpack("<i", selector_before)[0]
                if selector in (0, 1):
                    candidate_offset = transforms_base_offset + selector * 0x10
                    if _available(available_before, candidate_offset):
                        candidate = _slice(metadata_before, candidate_offset, 0x10)
                        decoded = self._valid_pose_header(candidate, expected_count)
                        if decoded is not None:
                            candidates.append((
                                "native-current", candidate_offset, candidate,
                                selector_before, decoded))
        if not candidates:
            return self._skeleton_fail("pose_header")

        # A valid-looking SDK cache header can still point at an unreadable or
        # concurrently replaced allocation.  Try it first, then the exact
        # executable's selected native buffer.  The normal path still uses one
        # candidate; the second candidate costs reads only after a real failure.
        failure_reason = "pose_header"
        for source, header_offset, header_before, selector_before, decoded in candidates:
            data, count = decoded
            transform_snapshot = self._component_world_transform_snapshot(mesh)
            if transform_snapshot is None:
                failure_reason = "transform"
                continue
            try:
                pose_raw = self.pm.read_bytes(data, count * 0x60)
                # The reader does not own the animation thread's synchronization.
                # Require adjacent identical payloads for every source, including
                # the rare case where SDK and native headers alias one allocation.
                pose_verify = self.pm.read_bytes(data, count * 0x60)
                if pose_verify != pose_raw:
                    failure_reason = "race"
                    continue
            except Exception:
                failure_reason = "read"
                continue
            if len(pose_raw) != count * 0x60:
                failure_reason = "read"
                continue
            try:
                metadata_after, available_after = _read_metadata()
            except Exception:
                failure_reason = "read"
                continue
            required_after = {
                mesh_asset_offset, legacy_mesh_offset, leader_offset, header_offset}
            if selector_before is not None:
                required_after.add(selector_offset)
            if (not all(_available(available_after, offset)
                        for offset in required_after)
                    or _slice(metadata_after, mesh_asset_offset, 8)
                    != _slice(metadata_before, mesh_asset_offset, 8)
                    or _slice(metadata_after, legacy_mesh_offset, 8)
                    != _slice(metadata_before, legacy_mesh_offset, 8)
                    or _slice(metadata_after, leader_offset, 8) != leader_before
                    or self._character_mesh(actor, refresh=True) != mesh):
                # Identity/leader changes invalidate every candidate from the
                # original metadata transaction; never mix generations.
                return self._skeleton_fail("race")
            if (_slice(metadata_after, header_offset, 0x10) != header_before
                    or (selector_before is not None
                        and _slice(metadata_after, selector_offset, 4)
                        != selector_before)):
                failure_reason = "race"
                continue

            local_points = []
            pose_valid = True
            for bone_index in range(count):
                translation = struct.unpack_from(
                    "<3d", pose_raw, bone_index * 0x60 + 0x20)
                if not finite_vector(translation, 1.0e6):
                    pose_valid = False
                    break
                local_points.append(translation)
            if not pose_valid:
                failure_reason = "pose_data"
                continue

            local_to_world = transform_snapshot[1][0]
            mesh_origin = transform_position_row(
                (0.0, 0.0, 0.0), local_to_world)
            if mesh_origin is None:
                failure_reason = "transform"
                continue
            if (actor_position is not None
                    and dist(mesh_origin, actor_position) > 5000.0):
                failure_reason = "origin"
                continue
            world_points = []
            for local_point in local_points:
                world_point = transform_position_row(local_point, local_to_world)
                if (world_point is None
                        or dist(world_point, mesh_origin) > 5000.0):
                    pose_valid = False
                    break
                world_points.append(world_point)
            if not pose_valid:
                failure_reason = "pose_data"
                continue

            sources = getattr(self, "_skeleton_source_counts", None)
            if sources is None:
                sources = {}
                self._skeleton_source_counts = sources
            sources[source] = sources.get(source, 0) + 1
            return SkeletonPose(profile.skeleton_name, tuple(world_points),
                                profile.draw_edges, tuple(local_points), mesh)
        return self._skeleton_fail(failure_reason)

    def _pawn_controller(self, pawn):
        if not pawn:
            return 0
        off = self.offsets.get("APawn::Controller")
        if off is None:
            return 0
        return rp(self.pm, pawn + off)

    def _pawn_playerstate(self, pawn):
        if not pawn:
            return 0
        off = self.offsets.get("APawn::PlayerState")
        if off is None:
            return 0
        return rp(self.pm, pawn + off)

    def _actor_owner(self, actor):
        if not actor:
            return 0
        off = self.offsets.get("AActor::Owner")
        if off is None:
            return 0
        return rp(self.pm, actor + off)

    def _actor_position(self, actor):
        """Read the actor root's engine-computed world position."""
        root_offset = self.offsets.get("AActor::RootComponent")
        if actor and root_offset is not None:
            root_cache = getattr(self, "_actor_root_cache", None)
            if root_cache is None:
                root_cache = {}
                self._actor_root_cache = root_cache
            root = root_cache.get(actor)
            if not root:
                root = rp(self.pm, actor + root_offset)
                if root:
                    root_cache[actor] = root
            if root:
                snapshot = self._component_world_transform_snapshot(root)
                if snapshot is not None:
                    transforms = getattr(self, "_last_actor_transforms", None)
                    if transforms is not None:
                        transforms[actor] = snapshot[1]
                    return snapshot[1][2]
        # Compatibility path for an unsupported build where the native offset
        # is deliberately unavailable: only an unattached root is unambiguous.
        transform = self._root_world_transform(actor)
        return transform[1] if transform is not None else None

    def iter_players(self, include_local=False, players_only=False, include_actor=False):
        self._last_actor_transforms = {}
        self._object_class_cache = {}
        world = self._get_world()
        if not world:
            self._last_iter_stats = {"pa_total": 0, "pa_valid": 0,
                                     "level_total": 0, "level_valid": 0,
                                     "dead_filtered": 0, "state_unreadable": 0,
                                     "role_filtered": 0, "rendered": 0,
                                     "local_pawn": False, "roster_mode": "none",
                                     "collection_valid": False}
            return
        gamestate = rp(self.pm, world + self.offsets["UWorld::GameState"])
        if not gamestate:
            self._last_iter_stats = {"pa_total": 0, "pa_valid": 0,
                                     "level_total": 0, "level_valid": 0,
                                     "dead_filtered": 0, "state_unreadable": 1,
                                     "role_filtered": 0, "rendered": 0,
                                     "local_pawn": False, "roster_mode": "none",
                                     "collection_valid": False}
            return
        self._set_runtime_context(world, gamestate)
        pc = self._get_local_controller(world)
        local_ps = rp(self.pm, pc + self.offsets["AController::PlayerState"]) if pc else 0
        local_pawn = (
            rp(self.pm, pc + self.offsets["APlayerController::AcknowledgedPawn"])
            if pc else 0)
        # PawnPrivate is authoritative during possession transitions where
        # AcknowledgedPawn may briefly be null on the local controller.
        if not local_pawn and local_ps:
            local_pawn = rp(
                self.pm, local_ps + self.offsets["APlayerState::PawnPrivate"])
        cleon_mode = self._object_is_a(gamestate, "BP_GameState_cLeon_C")
        rosters = self._cleon_live_rosters(gamestate) if cleon_mode else None
        role_cache = getattr(self, "_playerstate_role_cache", None)
        if role_cache is None:
            role_cache = {}
            self._playerstate_role_cache = role_cache
        class_local_role = self.character_role(local_pawn)
        local_role = (
            class_local_role
            or role_cache.get(local_ps)
            or getattr(self, "_last_known_local_role", None))
        hunters = frozenset()
        live_survivors = frozenset()
        main_phase = None
        if rosters is not None:
            hunters, live_survivors, main_phase = rosters
        # Live observation shows phase 0 can expose two valid but empty arrays.
        # Treating that transition as authoritative removes every player.  In
        # phases 1/2 a non-empty Hunter roster is stable and role-defining.
        roster_roles_available = (
            cleon_mode and rosters is not None
            and main_phase in (1, 2) and bool(hunters))
        local_roster_member = bool(
            local_ps and local_ps in (hunters | live_survivors))
        if roster_roles_available and local_ps:
            if local_ps in hunters:
                local_role = "hunter"
            elif local_ps in live_survivors:
                local_role = "survivor"
        if local_role in ("hunter", "survivor"):
            self._last_known_local_role = local_role
            if local_ps:
                role_cache[local_ps] = local_role
        # Membership still supplies role colors when the local player is between
        # Pawns, but only a roster that also accounts for the local PlayerState may
        # declare an absent remote PlayerState dead.
        roster_authoritative = roster_roles_available and local_roster_member
        self._last_local_role = local_role
        self._last_actor_roles = {}
        if local_pawn:
            self._last_actor_roles[local_pawn] = local_role

        stats = {"pa_total": 0, "pa_valid": 0,
                 "level_total": 0, "level_valid": 0,
                 "dead_filtered": 0, "state_unreadable": 0,
                 "position_unreadable": 0, "pawn_unavailable": 0,
                 "type_filtered": 0,
                 "role_filtered": 0, "rendered": 0,
                 "local_pawn": bool(local_pawn),
                 "collection_valid": True,
                 "roster_mode": (
                     f"authoritative-{getattr(self, '_cleon_roster_source', 'live')}"
                     if roster_authoritative
                     else ("fallback" if cleon_mode else "not-cleon"))}
        seen = set()

        def _result(is_local, pos, idx, actor):
            if include_actor:
                return is_local, pos, idx, actor
            return is_local, pos, idx

        def _is_valid_target(pawn, playerstate):
            if not pawn:
                return False
            # Restrict candidates to the game's actual player-character base.
            # A substring check also matched BP_CharacterAreaTrigger_C at runtime.
            if not self._object_is_a(pawn, "BP_FirstPersonCharacter_Main_C"):
                stats["type_filtered"] += 1
                return False
            # `pawn` is the PawnPrivate value read immediately before this call.
            # Re-reading the same pointer doubled one RPM per player without
            # making the multi-field snapshot atomic.
            if not playerstate:
                stats["state_unreadable"] += 1
                return False

            role = self.character_role(pawn) or role_cache.get(playerstate)
            if roster_roles_available:
                if playerstate in hunters:
                    role = "hunter"
                elif playerstate in live_survivors:
                    role = "survivor"
                elif roster_authoritative:
                    # During an active phase these replicated arrays define the
                    # Hunter role set and the explicitly live Survivor set.
                    stats["dead_filtered"] += 1
                    return False

            if role in ("hunter", "survivor"):
                role_cache[playerstate] = role

            self._last_actor_roles[pawn] = role
            if local_role == "hunter" and role == "hunter":
                stats["role_filtered"] += 1
                return False
            # HuntersPlayerState is authoritative for role, but unlike
            # LiveSurvivors_PlayerState its name does not assert liveness.  The
            # SDK-declared Dead byte is therefore required for every target,
            # including roster members; an unreadable state fails closed.
            dead = self.character_dead_state(pawn, assume_character=True)
            if dead is True:
                stats["dead_filtered"] += 1
                return False
            if dead is None:
                stats["state_unreadable"] += 1
                return False
            return True

        def _emit_actor(actor, idx, stat_key):
            pos = self._actor_position(actor)
            if pos is None:
                stats["position_unreadable"] += 1
                return
            # Drop uninitialized / origin-only positions.
            if abs(pos[0]) < 0.01 and abs(pos[1]) < 0.01 and abs(pos[2]) < 0.01:
                stats["position_unreadable"] += 1
                return
            stats[stat_key] += 1
            stats["rendered"] += 1
            yield _result(False, pos, idx, actor)

        # Local marker for calibration.
        local_live = False
        if include_local and local_pawn:
            roster_member = (
                local_ps in (hunters | live_survivors)
                if roster_authoritative else True)
            local_live = (
                roster_member and self.character_dead_state(local_pawn) is False)
        if (include_local and local_pawn and local_live
                and self._object_is_a(local_pawn, "BP_FirstPersonCharacter_Main_C")):
            pos = self._actor_position(local_pawn)
            if pos is not None:
                stats["rendered"] += 1
                yield _result(True, pos, 0, local_pawn)

        # Pass 1: GameState->PlayerArray. Persistent-level scans can include
        # NPCs/dummies and are intentionally not merged into player rendering.
        if gamestate:
            player_array = self._stable_pointer_array_values(
                gamestate + self.offsets["AGameStateBase::PlayerArray"], 256)
            if player_array is None:
                stats["collection_valid"] = False
                stats["state_unreadable"] += 1
                self._last_iter_stats = stats
                return
            playerstates, pa_count, pa_capacity = player_array
            stats["pa_total"] = pa_count
            previous_count = getattr(
                self, "_last_nontrivial_player_array_count", 0)
            if previous_count > 1 and pa_count <= 1:
                self._player_array_drop_streak = getattr(
                    self, "_player_array_drop_streak", 0) + 1
                if (self._player_array_drop_streak
                        <= self.PLAYER_ARRAY_DROP_GRACE_CYCLES):
                    # Two identical TArray reads prove memory stability, not that
                    # replication has finished updating the array.  Keep the last
                    # complete frame during a short same-world N -> 0/1 collapse.
                    stats["collection_valid"] = False
                    stats["array_drop_guarded"] = True
                    stats["state_unreadable"] += 1
                    self._last_iter_stats = stats
                    return
                self._last_nontrivial_player_array_count = pa_count
                self._player_array_drop_streak = 0
            else:
                self._player_array_drop_streak = 0
                if pa_count > 1:
                    self._last_nontrivial_player_array_count = pa_count
            if pa_count > 0:
                for i, ps in enumerate(playerstates):
                    if not ps or ps == local_ps:
                        continue
                    pawn = rp(self.pm, ps + self.offsets["APlayerState::PawnPrivate"])
                    if not pawn:
                        stats["pawn_unavailable"] += 1
                        continue
                    if pawn == local_pawn or pawn in seen:
                        continue
                    seen.add(pawn)
                    if not _is_valid_target(pawn, ps):
                        continue
                    yield from _emit_actor(pawn, i, "pa_valid")

        # Deliberately do not merge PersistentLevel actors. PlayerArray plus the
        # stable PawnPrivate ownership check is authoritative; level actors retain
        # unpossessed corpse pawns and non-player Character-named triggers.

        # Never publish an all-empty frame whose only explanation is transient
        # pointer/type/death/transform read failure.  Valid dead/role filtering
        # remains publishable, so corpses and same-role Hunters stay hidden.
        transient_failures = sum(stats[key] for key in (
            "state_unreadable", "position_unreadable", "pawn_unavailable"))
        unexpected_type_collapse = (
            getattr(self, "_last_remote_rendered_count", 0) > 0
            and stats["type_filtered"] > 0
            and stats["dead_filtered"] == 0
            and stats["role_filtered"] == 0)
        if (pa_count > 1 and stats["pa_valid"] == 0
                and (transient_failures or unexpected_type_collapse)):
            stats["collection_valid"] = False

        if stats["collection_valid"]:
            self._last_remote_rendered_count = stats["pa_valid"]

        self._last_iter_stats = stats


# ---------------------------------------------------------------------------
# World-to-screen
# ---------------------------------------------------------------------------
def rotation_to_axes(rot):
    pitch, yaw, roll = [math.radians(x) for x in rot]
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)

    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return forward, right, up


@dataclass(frozen=True)
class ProjectionContext:
    camera_location: Tuple[float, float, float]
    forward: Tuple[float, float, float]
    right: Tuple[float, float, float]
    up: Tuple[float, float, float]
    tan_half_fov: float
    aspect: float
    screen_w: int
    screen_h: int

    @classmethod
    def build(cls, camera, screen_w, screen_h):
        if screen_w <= 0 or screen_h <= 0:
            return None
        try:
            location = tuple(camera["loc"])
            rotation = tuple(camera["rot"])
            fov = float(camera["fov"])
        except (KeyError, TypeError, ValueError):
            return None
        if (not finite_vector(location) or not finite_vector(rotation, 1.0e6)
                or not math.isfinite(fov) or not 1.0 <= fov <= 179.0):
            return None
        forward, right, up = rotation_to_axes(rotation)
        return cls(location, forward, right, up,
                   math.tan(math.radians(fov) / 2.0),
                   screen_w / screen_h, screen_w, screen_h)

    def project(self, world_pos, clip_to_screen=True):
        if not finite_vector(world_pos):
            return None
        dx = world_pos[0] - self.camera_location[0]
        dy = world_pos[1] - self.camera_location[1]
        dz = world_pos[2] - self.camera_location[2]
        view_x = dx * self.forward[0] + dy * self.forward[1] + dz * self.forward[2]
        view_y = dx * self.right[0] + dy * self.right[1] + dz * self.right[2]
        view_z = dx * self.up[0] + dy * self.up[1] + dz * self.up[2]
        if view_x <= 0.1:
            return None
        ndc_x = view_y / (view_x * self.tan_half_fov)
        ndc_y = view_z / (view_x * self.tan_half_fov / self.aspect)
        screen_x = (1.0 + ndc_x) * self.screen_w / 2.0
        screen_y = (1.0 - ndc_y) * self.screen_h / 2.0
        if not finite_vector((screen_x, screen_y), 1.0e7):
            return None
        if clip_to_screen and not (
                0 <= screen_x <= self.screen_w and 0 <= screen_y <= self.screen_h):
            return None
        return screen_x, screen_y


def w2s(world_pos, camera, screen_w, screen_h, clip_to_screen=True):
    projector = ProjectionContext.build(camera, screen_w, screen_h)
    return (projector.project(world_pos, clip_to_screen)
            if projector is not None else None)


def _visible_rect(left, top, right, bottom, screen_w, screen_h):
    values = (left, top, right, bottom)
    if not finite_vector(values, 1.0e7) or right - left < 1.0 or bottom - top < 1.0:
        return None
    if right < 0.0 or left > screen_w or bottom < 0.0 or top > screen_h:
        return None
    return values


def intersect_rect(rect, screen_w, screen_h):
    if rect is None or screen_w <= 0 or screen_h <= 0:
        return None
    left, top, right, bottom = rect
    visible = (max(0.0, left), max(0.0, top),
               min(float(screen_w - 1), right), min(float(screen_h - 1), bottom))
    return visible if visible[0] <= visible[2] and visible[1] <= visible[3] else None


def label_position(rect, text_width, ascent, descent, screen_w, screen_h):
    """Place a label beside a box, flipping/clamping at viewport edges."""
    if rect is None or screen_w <= 0 or screen_h <= 0:
        return None
    left, top, right, _ = rect
    x = right + 4.0
    if x + text_width > screen_w:
        x = left - text_width - 4.0
    max_x = max(0.0, screen_w - text_width - 1.0)
    x = min(max(0.0, x), max_x)
    max_baseline = max(float(ascent), screen_h - descent - 1.0)
    baseline = min(max(float(ascent), top + ascent), max_baseline)
    return x, baseline


def project_capsule_box(center, half_height, radius, camera, screen_w, screen_h,
                        y_offset=0.0, projector=None):
    """Project the eight corners of an upright collision-capsule AABB."""
    if (not finite_vector(center) or not math.isfinite(half_height)
            or not math.isfinite(radius) or half_height <= 0.0
            or radius <= 0.0 or radius > half_height):
        return None
    corners = (
        (center[0] + sx * radius,
         center[1] + sy * radius,
         center[2] + sz * half_height)
        for sx in (-1.0, 1.0)
        for sy in (-1.0, 1.0)
        for sz in (-1.0, 1.0)
    )
    projector = projector or ProjectionContext.build(camera, screen_w, screen_h)
    if projector is None:
        return None
    projected = [projector.project(point, clip_to_screen=False) for point in corners]
    # Using only the corners still in front would under-estimate a near-plane
    # crossing box, so fail closed when any corner cannot be projected.
    if any(point is None for point in projected):
        return None
    xs = [point[0] for point in projected]
    ys = [point[1] + y_offset for point in projected]
    return _visible_rect(min(xs), min(ys), max(xs), max(ys), screen_w, screen_h)


def project_height_box(center, world_height, width_ratio, camera, screen_w, screen_h,
                       y_offset=0.0, projector=None):
    """Explicit approximate fallback when capsule transform data is unavailable."""
    if (not finite_vector(center) or not math.isfinite(world_height)
            or not math.isfinite(width_ratio) or world_height <= 0.0
            or not 0.05 <= width_ratio <= 2.0):
        return None
    half_height = world_height * 0.5
    projector = projector or ProjectionContext.build(camera, screen_w, screen_h)
    if projector is None:
        return None
    top = projector.project(
        (center[0], center[1], center[2] + half_height), clip_to_screen=False)
    bottom = projector.project(
        (center[0], center[1], center[2] - half_height), clip_to_screen=False)
    if top is None or bottom is None:
        return None
    axis_x = bottom[0] - top[0]
    axis_y = bottom[1] - top[1]
    projected_height = math.hypot(axis_x, axis_y)
    if projected_height < 1.0:
        return None
    half_width = projected_height * width_ratio * 0.5
    perpendicular = (-axis_y / projected_height, axis_x / projected_height)
    corners = tuple(
        (point[0] + sign * perpendicular[0] * half_width,
         point[1] + sign * perpendicular[1] * half_width + y_offset)
        for point in (top, bottom)
        for sign in (-1.0, 1.0)
    )
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return _visible_rect(min(xs), min(ys), max(xs), max(ys), screen_w, screen_h)


def box_segments(rect, style="Corner", corner_fraction=0.25):
    left, top, right, bottom = rect
    if str(style).lower() != "corner":
        return (
            ((left, top), (right, top)),
            ((right, top), (right, bottom)),
            ((right, bottom), (left, bottom)),
            ((left, bottom), (left, top)),
        )
    fraction = min(0.5, max(0.1, float(corner_fraction)))
    corner_w = (right - left) * fraction
    corner_h = (bottom - top) * fraction
    return (
        ((left, top), (left + corner_w, top)),
        ((left, top), (left, top + corner_h)),
        ((right, top), (right - corner_w, top)),
        ((right, top), (right, top + corner_h)),
        ((left, bottom), (left + corner_w, bottom)),
        ((left, bottom), (left, bottom - corner_h)),
        ((right, bottom), (right - corner_w, bottom)),
        ((right, bottom), (right, bottom - corner_h)),
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    enabled: bool = True
    box_esp: bool = True
    skeleton_esp: bool = True
    show_local: bool = True
    show_names: bool = True
    show_distance: bool = True
    snap_lines: bool = True
    enemy_color: Tuple[int, int, int] = (255, 0, 0)
    hunter_color: Tuple[int, int, int] = (255, 165, 0)
    local_color: Tuple[int, int, int] = (0, 255, 0)
    box_height_world: float = 100.0
    box_width_ratio: float = 0.45
    box_y_offset: int = 0
    box_style: str = "Corner"
    box_line_width: int = 2
    box_corner_fraction: float = 0.25
    skeleton_line_width: int = 2
    show_debug: bool = False

# ---------------------------------------------------------------------------
# Menu window
# ---------------------------------------------------------------------------
class Menu(QWidget):
    EXPANDED_SIZE = (540, 680)
    COMPACT_SIZE = (540, 98)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setWindowTitle("MECCHA Vision")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._compact = False

        self._build_ui()
        self.setFixedSize(*self.EXPANDED_SIZE)

    def _build_ui(self):
        self.setStyleSheet("""
            QWidget {
                color: #eaf2ff;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QFrame#panel {
                background-color: rgba(8, 13, 24, 247);
                border: 1px solid #273550;
                border-radius: 18px;
            }
            QFrame#header {
                background-color: transparent;
                border: none;
                border-bottom: 1px solid #202c42;
            }
            QFrame[card="true"] {
                background-color: #111a2c;
                border: 1px solid #22304a;
                border-radius: 12px;
            }
            QLabel#eyebrow {
                color: #67e8c8;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 2px;
            }
            QLabel#title {
                color: #f4f8ff;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                color: #f1f6ff;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#caption, QLabel#controlLabel, QLabel#footer {
                color: #8495b0;
                font-size: 10px;
            }
            QLabel#masterTitle {
                color: #f4f8ff;
                font-size: 14px;
                font-weight: 700;
            }
            QCheckBox {
                color: #cbd7e8;
                spacing: 8px;
                min-height: 24px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border: 1px solid #40516e;
                border-radius: 5px;
                background-color: #0b1220;
            }
            QCheckBox::indicator:hover {
                border-color: #67e8c8;
            }
            QCheckBox::indicator:checked {
                background-color: #67e8c8;
                border: 1px solid #67e8c8;
            }
            QCheckBox::indicator:disabled {
                background-color: #172033;
                border-color: #2a354a;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                min-height: 27px;
                background-color: #0b1220;
                color: #e8f0fc;
                border: 1px solid #2b3a55;
                border-radius: 7px;
                padding: 1px 8px;
                selection-background-color: #235d58;
            }
            QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
                border-color: #4e6689;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #67e8c8;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QPushButton#collapseButton {
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                color: #9fb0c9;
                background-color: #111a2c;
                border: 1px solid #2a3955;
                border-radius: 10px;
                font-size: 18px;
                font-weight: 600;
            }
            QPushButton#collapseButton:hover {
                color: #67e8c8;
                border-color: #4c7e78;
                background-color: #15243a;
            }
            QPushButton[colorButton="true"] {
                min-height: 50px;
                text-align: left;
                padding-left: 13px;
                border-radius: 9px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:disabled, QComboBox:disabled,
            QSpinBox:disabled, QDoubleSpinBox:disabled {
                color: #526078;
                background-color: #0d1422;
                border-color: #1d283b;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        outer.addWidget(self.panel)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 145))
        self.panel.setGraphicsEffect(shadow)

        self.header = QFrame()
        self.header.setObjectName("header")
        self.header.setFixedHeight(77)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(18, 10, 16, 10)
        header_layout.setSpacing(12)

        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(1)
        eyebrow = QLabel("EXTERNAL  /  UE 5.6")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("MECCHA // VISION")
        title.setObjectName("title")
        eyebrow.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        brand_layout.addWidget(eyebrow)
        brand_layout.addWidget(title)
        header_layout.addLayout(brand_layout)
        header_layout.addStretch(1)

        self.status_badge = QLabel()
        self.status_badge.setAlignment(Qt.AlignCenter)
        self.status_badge.setFixedSize(122, 30)
        self.status_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_layout.addWidget(self.status_badge)

        self.btn_collapse = QPushButton("−")
        self.btn_collapse.setObjectName("collapseButton")
        self.btn_collapse.setToolTip("Collapse settings")
        self.btn_collapse.clicked.connect(self._toggle_compact)
        header_layout.addWidget(self.btn_collapse)
        panel_layout.addWidget(self.header)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(16, 13, 16, 14)
        body_layout.setSpacing(11)
        panel_layout.addWidget(self.body, 1)

        master = QFrame()
        master.setProperty("card", True)
        master.setFixedHeight(64)
        master_layout = QHBoxLayout(master)
        master_layout.setContentsMargins(15, 8, 15, 8)
        master_text = QVBoxLayout()
        master_text.setSpacing(0)
        master_title = QLabel("Overlay output")
        master_title.setObjectName("masterTitle")
        master_caption = QLabel("Pause every visual layer without changing its settings")
        master_caption.setObjectName("caption")
        master_text.addWidget(master_title)
        master_text.addWidget(master_caption)
        master_layout.addLayout(master_text)
        master_layout.addStretch(1)
        self.cb_enabled = self._chk("ENABLED", "enabled")
        self.cb_enabled.setObjectName("masterToggle")
        self.cb_enabled.toggled.connect(self._refresh_master_status)
        master_layout.addWidget(self.cb_enabled)
        body_layout.addWidget(master)

        cards = QGridLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setHorizontalSpacing(11)
        cards.setVerticalSpacing(0)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)

        visual_card, visual_layout = self._card(
            "VISUAL LAYERS", "Choose what the overlay paints")
        visual_grid = QGridLayout()
        visual_grid.setContentsMargins(0, 5, 0, 0)
        visual_grid.setHorizontalSpacing(8)
        visual_grid.setVerticalSpacing(2)
        self.cb_box = self._chk("Player boxes", "box_esp")
        self.cb_skeleton = self._chk("Skeletons", "skeleton_esp")
        self.cb_names = self._chk("Player labels", "show_names")
        self.cb_dist = self._chk("Distance", "show_distance")
        self.cb_snap = self._chk("Snap lines", "snap_lines")
        self.cb_local = self._chk("Local player", "show_local")
        self.cb_debug = self._chk("Diagnostics", "show_debug")
        visual_grid.addWidget(self.cb_box, 0, 0)
        visual_grid.addWidget(self.cb_skeleton, 0, 1)
        visual_grid.addWidget(self.cb_names, 1, 0)
        visual_grid.addWidget(self.cb_dist, 1, 1)
        visual_grid.addWidget(self.cb_snap, 2, 0)
        visual_grid.addWidget(self.cb_local, 2, 1)
        visual_grid.addWidget(self.cb_debug, 3, 0, 1, 2)
        visual_layout.addLayout(visual_grid)
        visual_layout.addStretch(1)
        cards.addWidget(visual_card, 0, 0)

        geometry_card, geometry_layout = self._card(
            "GEOMETRY", "Tune shape and line proportions")
        geometry_grid = QGridLayout()
        geometry_grid.setContentsMargins(0, 5, 0, 0)
        geometry_grid.setHorizontalSpacing(8)
        geometry_grid.setVerticalSpacing(5)
        geometry_grid.setColumnStretch(0, 1)

        self.cmb_box_style = QComboBox()
        self.cmb_box_style.addItems(["Corner", "2D"])
        self.cmb_box_style.setCurrentText(self.config.box_style)
        self.cmb_box_style.currentTextChanged.connect(self._set_box_style)
        self.spn_box_width = self._double_spin(
            0.10, 1.50, 0.05, self.config.box_width_ratio, 2,
            "box_width_ratio")
        self.spn_height = self._int_spin(
            50, 250, int(self.config.box_height_world), "box_height_world", float)
        self.spn_yoff = self._int_spin(
            -50, 50, self.config.box_y_offset, "box_y_offset")
        self.spn_box_line = self._int_spin(
            1, 6, self.config.box_line_width, "box_line_width")
        self.spn_skeleton_line = self._int_spin(
            1, 6, self.config.skeleton_line_width, "skeleton_line_width")
        self.spn_corner = self._double_spin(
            0.10, 0.50, 0.05, self.config.box_corner_fraction, 2,
            "box_corner_fraction")

        geometry_controls = (
            ("Box style", self.cmb_box_style),
            ("Width ratio", self.spn_box_width),
            ("World height", self.spn_height),
            ("Vertical offset", self.spn_yoff),
            ("Box stroke", self.spn_box_line),
            ("Skeleton stroke", self.spn_skeleton_line),
            ("Corner length", self.spn_corner),
        )
        for row, (label, control) in enumerate(geometry_controls):
            caption = QLabel(label)
            caption.setObjectName("controlLabel")
            geometry_grid.addWidget(caption, row, 0)
            control.setFixedWidth(91)
            geometry_grid.addWidget(control, row, 1)
        geometry_layout.addLayout(geometry_grid)
        cards.addWidget(geometry_card, 0, 1)
        body_layout.addLayout(cards, 1)

        palette, palette_layout = self._card(
            "ROLE PALETTE", "Click a role to choose its overlay color")
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 4, 0, 0)
        color_row.setSpacing(8)
        self.btn_enemy_color = self._color_button(
            "OTHER PLAYERS", self.config.enemy_color, self._pick_enemy_color)
        self.btn_hunter_color = self._color_button(
            "HUNTER", self.config.hunter_color, self._pick_hunter_color)
        self.btn_local_color = self._color_button(
            "LOCAL PLAYER", self.config.local_color, self._pick_local_color)
        color_row.addWidget(self.btn_enemy_color)
        color_row.addWidget(self.btn_hunter_color)
        color_row.addWidget(self.btn_local_color)
        palette_layout.addLayout(color_row)
        body_layout.addWidget(palette)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(2, 0, 2, 0)
        readonly = QLabel("●  READ-ONLY PROCESS VIEW")
        readonly.setObjectName("footer")
        readonly.setStyleSheet("color: #67e8c8;")
        hint = QLabel("[ INSERT ]   [ F1 ]   TOGGLE PANEL")
        hint.setObjectName("footer")
        footer_row.addWidget(readonly)
        footer_row.addStretch(1)
        footer_row.addWidget(hint)
        body_layout.addLayout(footer_row)

        self.cb_box.toggled.connect(self._sync_dependencies)
        self.cb_skeleton.toggled.connect(self._sync_dependencies)
        self._refresh_master_status()
        self._sync_dependencies()

    @staticmethod
    def _card(title, caption):
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 11, 13, 11)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        caption_label = QLabel(caption)
        caption_label.setObjectName("caption")
        layout.addWidget(title_label)
        layout.addWidget(caption_label)
        return card, layout

    def _chk(self, text, attr):
        cb = QCheckBox(text)
        cb.setChecked(getattr(self.config, attr))
        cb.stateChanged.connect(lambda s, a=attr: setattr(self.config, a, bool(s)))
        return cb

    def _int_spin(self, minimum, maximum, value, attr, cast=int):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(value))
        spin.valueChanged.connect(
            lambda new_value, a=attr, c=cast: setattr(self.config, a, c(new_value)))
        return spin

    def _double_spin(self, minimum, maximum, step, value, decimals, attr):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(float(value))
        spin.valueChanged.connect(
            lambda new_value, a=attr: setattr(self.config, a, float(new_value)))
        return spin

    def _set_box_style(self, value):
        self.config.box_style = value
        self._sync_dependencies()

    def _sync_dependencies(self, *_args):
        box_enabled = self.cb_box.isChecked()
        for control in (
                self.cmb_box_style, self.spn_box_width, self.spn_height,
                self.spn_yoff, self.spn_box_line):
            control.setEnabled(box_enabled)
        self.spn_corner.setEnabled(
            box_enabled and self.cmb_box_style.currentText() == "Corner")
        self.spn_skeleton_line.setEnabled(self.cb_skeleton.isChecked())

    def _refresh_master_status(self, *_args):
        enabled = self.config.enabled
        if enabled:
            text, color, border, background = (
                "●  OVERLAY ON", "#73f0d0", "#397669", "#102d2b")
        else:
            text, color, border, background = (
                "●  OVERLAY PAUSED", "#a5b2c5", "#39465d", "#182132")
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(
            f"color: {color}; background-color: {background}; "
            f"border: 1px solid {border}; border-radius: 9px; "
            "font-size: 10px; font-weight: 700;")

    def _toggle_compact(self, *_args):
        self._compact = not self._compact
        self.body.setVisible(not self._compact)
        self.btn_collapse.setText("+" if self._compact else "−")
        self.btn_collapse.setToolTip(
            "Expand settings" if self._compact else "Collapse settings")
        self.setFixedSize(*(self.COMPACT_SIZE if self._compact else self.EXPANDED_SIZE))

    def _color_button(self, label, rgb, callback):
        button = QPushButton()
        button.setProperty("colorButton", True)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        button.setProperty("roleLabel", label)
        self._apply_color_button(button, label, rgb)
        return button

    @staticmethod
    def _apply_color_button(button, label, rgb):
        red, green, blue = rgb
        button.setText(f"{label}\n#{red:02X}{green:02X}{blue:02X}")
        button.setStyleSheet(
            "QPushButton {"
            f"color: #edf4ff; background-color: rgba({red}, {green}, {blue}, 38);"
            f"border: 1px solid rgb({red}, {green}, {blue});"
            "border-radius: 9px; text-align: left; padding-left: 13px;"
            "font-size: 10px; font-weight: 600;"
            "} QPushButton:hover {"
            f"background-color: rgba({red}, {green}, {blue}, 68);"
            "}")

    def _pick_enemy_color(self):
        c = QColorDialog.getColor(QColor(*self.config.enemy_color), self)
        if c.isValid():
            self.config.enemy_color = (c.red(), c.green(), c.blue())
            self._apply_color_button(
                self.btn_enemy_color, "OTHER PLAYERS", self.config.enemy_color)

    def _pick_hunter_color(self):
        c = QColorDialog.getColor(QColor(*self.config.hunter_color), self)
        if c.isValid():
            self.config.hunter_color = (c.red(), c.green(), c.blue())
            self._apply_color_button(
                self.btn_hunter_color, "HUNTER", self.config.hunter_color)

    def _pick_local_color(self):
        c = QColorDialog.getColor(QColor(*self.config.local_color), self)
        if c.isValid():
            self.config.local_color = (c.red(), c.green(), c.blue())
            self._apply_color_button(
                self.btn_local_color, "LOCAL PLAYER", self.config.local_color)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() <= 87:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
class Overlay(QWidget):
    COLLECT_INTERVAL = 1.0 / 30.0
    STALE_AFTER_SECONDS = 0.25
    WINDOW_SYNC_INTERVAL = 0.25
    SKELETON_REFRESH_BUDGET_SECONDS = 0.020
    MAX_SKELETON_REFRESH_PER_CYCLE = 16
    SKELETON_CACHE_TTL_SECONDS = 0.25
    SKELETON_SLOW_SAMPLE_SECONDS = 0.20
    SKELETON_SUSPEND_SECONDS = 1.0

    def __init__(self, esp: MecchaESP, config: Config, menu: Menu):
        super().__init__()
        self.esp = esp
        self.config = config
        self.menu = menu
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle("MECCHA ESP")

        now = time.monotonic()
        initial_snapshot = FrameRenderSnapshot(
            0, now, now, 0.0, None, None, (), (), (), None)
        self._snapshots = LatestSnapshotStore(initial_snapshot)
        self._snapshot_stop = threading.Event()
        self._snapshot_sequence = 0
        self._viewport_size = (1920, 1080)
        self._layout_warnings = tuple(getattr(esp, "_layout_warnings", ()))
        self._last_window_sync = 0.0
        self._last_repaint = 0.0
        self._last_rendered_sequence = -1
        self._last_rendered_frame = None
        self._process_closed = False
        self._process_close_lock = threading.Lock()
        self._worker_state_lock = threading.Lock()
        self._active_workers = {"base", "skeleton"}
        self._skeleton_cache_lock = threading.Lock()
        self._skeleton_pose_cache = {}
        self._skeleton_failure_cooldowns = {}
        self._skeleton_actor_queue = deque()
        self._skeleton_cache_epoch = getattr(esp, "_world_epoch", 0)
        self._skeleton_suspended_until = 0.0
        self._last_skeleton_refresh_ms = 0.0
        self._last_skeleton_refresh_attempts = 0
        self._last_skeleton_failures = ()
        self._skeleton_job_lock = threading.Lock()
        self._skeleton_job_event = threading.Event()
        self._skeleton_pending_frame = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_overlay)
        self.timer.start(16)

        self.game_hwnd = self._find_game_window()
        self._resize_to_game()
        self._skeleton_thread = threading.Thread(
            target=self._skeleton_loop, name="MecchaESPSkeleton", daemon=False)
        self._snapshot_thread = threading.Thread(
            target=self._snapshot_loop, name="MecchaESPReader", daemon=False)
        self._skeleton_thread.start()
        self._snapshot_thread.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop_worker)

    def _find_game_window(self):
        try:
            import win32gui
            return win32gui.FindWindow(None, "Chameleon  ")
        except Exception:
            return 0

    def _resize_to_game(self):
        try:
            import win32gui
            if self.game_hwnd:
                rect = win32gui.GetClientRect(self.game_hwnd)
                tl = win32gui.ClientToScreen(self.game_hwnd, (rect[0], rect[1]))
                br = win32gui.ClientToScreen(self.game_hwnd, (rect[2], rect[3]))
                width = br[0] - tl[0]
                height = br[1] - tl[1]
                self._viewport_size = (max(1, width), max(1, height))
                geometry = self.geometry()
                if (geometry.x(), geometry.y(), geometry.width(), geometry.height()) != (
                        tl[0], tl[1], width, height):
                    self.setGeometry(tl[0], tl[1], width, height)
            else:
                self._viewport_size = (1920, 1080)
                self.setGeometry(0, 0, 1920, 1080)
        except Exception:
            self._viewport_size = (1920, 1080)
            self.setGeometry(0, 0, 1920, 1080)

    def update_overlay(self):
        now = time.monotonic()
        if now - self._last_window_sync >= self.WINDOW_SYNC_INTERVAL:
            self._last_window_sync = now
            self._resize_to_game()
        latest = self._snapshots.latest()
        latest_sequence = latest.sequence
        pose_frame = any(player.pose is not None for player in latest.players)
        keepalive = 0.016 if pose_frame else 0.1
        if (latest is not self._last_rendered_frame
                or now - self._last_repaint >= keepalive):
            self._last_rendered_frame = latest
            self._last_rendered_sequence = latest_sequence
            self._last_repaint = now
            self.update()

    @staticmethod
    def _copy_camera(camera):
        if camera is None:
            return None
        try:
            return CameraSnapshot(
                tuple(camera["loc"]), tuple(camera["rot"]),
                float(camera["fov"]))
        except (KeyError, TypeError, ValueError):
            return None

    def _ensure_skeleton_state(self):
        try:
            self._skeleton_cache_lock
        except (AttributeError, RuntimeError):
            self._skeleton_cache_lock = threading.Lock()
        try:
            self._skeleton_pose_cache
        except (AttributeError, RuntimeError):
            self._skeleton_pose_cache = {}
            self._skeleton_failure_cooldowns = {}
            self._skeleton_cache_epoch = getattr(self.esp, "_world_epoch", 0)
            self._skeleton_suspended_until = 0.0
            self._last_skeleton_refresh_ms = 0.0
            self._last_skeleton_refresh_attempts = 0
            self._last_skeleton_failures = ()
        try:
            self._skeleton_actor_queue
        except (AttributeError, RuntimeError):
            self._skeleton_actor_queue = deque()

    def _sync_skeleton_epoch(self):
        self._ensure_skeleton_state()
        epoch = getattr(self.esp, "_world_epoch", 0)
        if epoch != self._skeleton_cache_epoch:
            with self._skeleton_cache_lock:
                self._skeleton_cache_epoch = epoch
                self._skeleton_pose_cache.clear()
            self._skeleton_failure_cooldowns.clear()
            self._skeleton_actor_queue.clear()
            self._skeleton_suspended_until = 0.0
        return epoch

    def _cached_skeleton_for_player(
            self, actor, position, root_transform, now, world_epoch,
            cached=None):
        """Rebase one cached pose without any process-memory access."""
        self._ensure_skeleton_state()
        if cached is None:
            with self._skeleton_cache_lock:
                cached = self._skeleton_pose_cache.get(actor)
        if cached is None:
            return None
        if (cached.world_epoch != world_epoch
                or now - cached.captured_at > self.SKELETON_CACHE_TTL_SECONDS):
            return None

        old_points = cached.pose.world_points
        if not old_points:
            return None

        points = []
        old_root = cached.root_transform
        if old_root is not None and root_transform is not None:
            try:
                old_world_to_root = old_root[1]
                current_root_to_world = root_transform[0]
            except (IndexError, TypeError):
                return None
            for old_world_point in old_points:
                root_point = transform_position_row(
                    old_world_point, old_world_to_root)
                world_point = (
                    transform_position_row(root_point, current_root_to_world)
                    if root_point is not None else None)
                if (world_point is None or not finite_vector(world_point)
                        or dist(world_point, position) > 5000.0):
                    return None
                points.append(world_point)
        else:
            if (not finite_vector(cached.actor_position)
                    or not finite_vector(position)):
                return None
            delta = tuple(
                position[index] - cached.actor_position[index]
                for index in range(3))
            for old_world_point in old_points:
                world_point = tuple(
                    old_world_point[index] + delta[index]
                    for index in range(3))
                if (not finite_vector(world_point)
                        or dist(world_point, position) > 5000.0):
                    return None
                points.append(world_point)
        return SkeletonPose(
            cached.pose.profile_name, tuple(points), cached.pose.draw_edges,
            cached.pose.component_points, cached.pose.mesh)

    def _skeleton_cache_snapshot(self):
        self._ensure_skeleton_state()
        with self._skeleton_cache_lock:
            return dict(self._skeleton_pose_cache), self._skeleton_cache_epoch

    def _skeleton_cache_size(self):
        self._ensure_skeleton_state()
        with self._skeleton_cache_lock:
            return len(self._skeleton_pose_cache)

    def _skeleton_candidates(self, frame):
        if frame.camera is None:
            return []
        viewport_w, viewport_h = self._viewport_size
        projector = ProjectionContext.build(frame.camera, viewport_w, viewport_h)
        if projector is None:
            return []
        margin_x = viewport_w * 0.25
        margin_y = viewport_h * 0.25
        candidates = []
        for player in frame.players:
            center = projector.project(player.position, clip_to_screen=False)
            if (center is not None
                    and -margin_x <= center[0] <= viewport_w + margin_x
                    and -margin_y <= center[1] <= viewport_h + margin_y):
                candidates.append(player)
        return candidates

    def _enrich_snapshot_with_cached_skeletons(self, frame):
        """Merge the independent pose layer into a fresh base frame in memory."""
        self._ensure_skeleton_state()
        if (not self.config.enabled or not self.config.skeleton_esp
                or frame.error or frame.camera is None):
            return None
        now = time.monotonic()
        if max(0.0, now - frame.started_at) >= self.STALE_AFTER_SECONDS:
            return None
        cache, cache_epoch = self._skeleton_cache_snapshot()
        world_epoch = dict(frame.stats).get("world_epoch", cache_epoch)
        candidate_actors = {
            player.actor for player in self._skeleton_candidates(frame)}
        if not candidate_actors or not cache:
            return None

        attached = 0
        enriched_players = []
        for player in frame.players:
            pose = None
            captured_at = None
            cached = cache.get(player.actor)
            if player.actor in candidate_actors and cached is not None:
                pose = self._cached_skeleton_for_player(
                    player.actor, player.position, player.root_transform,
                    now, world_epoch, cached=cached)
                if pose is not None:
                    captured_at = cached.captured_at
                    attached += 1
            enriched_players.append(replace(
                player, pose=pose, pose_captured_at=captured_at))

        if not attached:
            return None
        stats = dict(frame.stats)
        stats.update({
            "skeleton_refresh_ms": self._last_skeleton_refresh_ms,
            "skeleton_attempts": self._last_skeleton_refresh_attempts,
            "skeleton_cache": len(cache),
            "skeleton_enrich_attempts": len(candidate_actors),
        })
        return replace(
            frame, players=tuple(enriched_players), stats=tuple(stats.items()),
            skeleton_failures=tuple(self._last_skeleton_failures))

    def _refresh_skeleton_cache(self, frame):
        """Refresh a fair batch of poses on the skeleton-only worker."""
        self._ensure_skeleton_state()
        started = time.monotonic()
        self._last_skeleton_refresh_attempts = 0
        if (not self.config.enabled or not self.config.skeleton_esp
                or frame.error or frame.camera is None):
            if not self.config.skeleton_esp:
                with self._skeleton_cache_lock:
                    self._skeleton_pose_cache.clear()
                self._skeleton_failure_cooldowns.clear()
                self._skeleton_actor_queue.clear()
                self._skeleton_suspended_until = 0.0
            self._last_skeleton_refresh_ms = 0.0
            self._last_skeleton_failures = ()
            return

        world_epoch = self._sync_skeleton_epoch()
        now = time.monotonic()
        oldest_age = max(0.0, now - frame.started_at)
        if (frame.collection_ms >= self.STALE_AFTER_SECONDS * 1000.0
                or oldest_age >= self.STALE_AFTER_SECONDS):
            # Do not spend pose reads on a base frame the painter must reject.
            self._last_skeleton_refresh_ms = (now - started) * 1000.0
            self._last_skeleton_failures = ()
            return

        active_actors = {player.actor for player in frame.players}
        with self._skeleton_cache_lock:
            for actor in tuple(self._skeleton_pose_cache):
                cached = self._skeleton_pose_cache[actor]
                if (actor not in active_actors
                        or now - cached.captured_at
                        > self.SKELETON_CACHE_TTL_SECONDS):
                    self._skeleton_pose_cache.pop(actor, None)
        for actor in tuple(self._skeleton_failure_cooldowns):
            if actor not in active_actors:
                self._skeleton_failure_cooldowns.pop(actor, None)

        candidates = self._skeleton_candidates(frame)
        if not candidates or now < self._skeleton_suspended_until:
            self._last_skeleton_refresh_ms = (now - started) * 1000.0
            self._last_skeleton_failures = ()
            return

        candidates_by_actor = {player.actor: player for player in candidates}
        candidate_actors = set(candidates_by_actor)
        queue = self._skeleton_actor_queue
        retained = [actor for actor in queue if actor in candidate_actors]
        queue.clear()
        queue.extend(retained)
        queued = set(retained)
        for player in candidates:
            if player.actor not in queued:
                queue.append(player.actor)
                queued.add(player.actor)

        if (not queue or not self.config.enabled
                or not self.config.skeleton_esp):
            self._last_skeleton_refresh_ms = (
                time.monotonic() - started) * 1000.0
            self._last_skeleton_failures = ()
            return

        failure_totals = {}
        self.esp._skeleton_source_counts = {}
        attempts = 0
        checked = 0
        queue_span = len(queue)
        while (checked < queue_span
               and attempts < self.MAX_SKELETON_REFRESH_PER_CYCLE):
            if (attempts > 0
                    and time.monotonic() - started
                    >= self.SKELETON_REFRESH_BUDGET_SECONDS):
                break
            try:
                stopping = self._snapshot_stop.is_set()
            except (AttributeError, RuntimeError):
                stopping = False
            if (stopping or not self.config.enabled
                    or not self.config.skeleton_esp):
                break
            actor = queue.popleft()
            queue.append(actor)
            checked += 1
            now = time.monotonic()
            if now < self._skeleton_failure_cooldowns.get(actor, 0.0):
                continue
            player = candidates_by_actor[actor]

            self.esp._skeleton_failure_counts = {}
            sample_started = time.monotonic()
            try:
                pose = self.esp.read_skeleton_pose(
                    player.actor, player.position)
            except Exception:
                pose = None
                self.esp._skeleton_failure_counts = {"exception": 1}
            sample_finished = time.monotonic()
            attempts += 1
            actor_failures = dict(
                getattr(self.esp, "_skeleton_failure_counts", {}))
            if (pose is not None
                    and (not pose.component_points or not pose.mesh
                         or len(pose.component_points)
                         != len(pose.world_points))):
                pose = None
                actor_failures["pose_space"] = 1
            if getattr(self.esp, "_world_epoch", world_epoch) != world_epoch:
                pose = None
                actor_failures["epoch"] = 1
            for reason, value in actor_failures.items():
                failure_totals[reason] = (
                    failure_totals.get(reason, 0) + value)

            with self._skeleton_cache_lock:
                if pose is not None:
                    self._skeleton_pose_cache[player.actor] = CachedSkeletonPose(
                        # The payload/mesh transform transaction starts here.
                        # A read slower than the TTL expires naturally instead
                        # of extending the life of already-old animation data.
                        sample_started, pose, player.position,
                        player.root_transform, world_epoch)
                    self._skeleton_failure_cooldowns.pop(player.actor, None)
                else:
                    self._skeleton_pose_cache.pop(player.actor, None)
                    persistent = any(reason in {
                        "build", "identity", "layout", "leader", "no_mesh",
                        "profile", "pose_space"
                    } for reason in actor_failures)
                    retry_delay = 2.0 if persistent else 0.10
                    self._skeleton_failure_cooldowns[player.actor] = (
                        sample_finished + retry_delay)

            if (sample_finished - sample_started
                    >= self.SKELETON_SLOW_SAMPLE_SECONDS):
                self._skeleton_suspended_until = (
                    sample_finished + self.SKELETON_SUSPEND_SECONDS)
                break

        self._last_skeleton_refresh_attempts = attempts
        self._last_skeleton_refresh_ms = (
            time.monotonic() - started) * 1000.0
        self._last_skeleton_failures = tuple(sorted(failure_totals.items()))

    def _collect_snapshot(self):
        started = time.monotonic()
        self._snapshot_sequence += 1
        sequence = self._snapshot_sequence
        # Keep feature membership consistent for every player in this frame even
        # if the menu is toggled while cross-process reads are in progress.
        enabled = bool(self.config.enabled)
        show_local = bool(self.config.show_local)
        box_esp = bool(self.config.box_esp)
        if not enabled:
            finished = time.monotonic()
            return FrameRenderSnapshot(
                sequence, started, finished, (finished - started) * 1000.0,
                None, None, (), (("collection_valid", True),), (), None)

        try:
            initial_camera = self._copy_camera(self.esp.get_camera())
            if initial_camera is None:
                finished = time.monotonic()
                return FrameRenderSnapshot(
                    sequence, started, finished, (finished - started) * 1000.0,
                    None, None, (), (("collection_valid", False),), (), None)
            initial_context = getattr(
                self.esp, "_runtime_context_identity", None)
            initial_epoch = getattr(self.esp, "_world_epoch", None)

            self._ensure_skeleton_state()
            camera_ms = (time.monotonic() - started) * 1000.0
            players = []
            players_started = time.monotonic()
            capsule_ms = 0.0
            for is_local, position, index, actor in self.esp.iter_players(
                    include_local=show_local, include_actor=True):
                role = getattr(self.esp, "_last_actor_roles", {}).get(actor)
                if role is None:
                    role = self.esp.character_role(actor)
                capsule_started = time.monotonic()
                capsule = self.esp.read_capsule_geometry(actor) if box_esp else None
                capsule_ms += (time.monotonic() - capsule_started) * 1000.0
                root_transform = getattr(
                    self.esp, "_last_actor_transforms", {}).get(actor)
                players.append(PlayerRenderSnapshot(
                    is_local, tuple(position), index, actor, role, capsule, None,
                    root_transform, None))

            players_ms = (time.monotonic() - players_started) * 1000.0
            final_camera_started = time.monotonic()
            final_camera = self._copy_camera(self.esp.get_camera())
            final_context = getattr(
                self.esp, "_runtime_context_identity", None)
            final_epoch = getattr(self.esp, "_world_epoch", None)
            context_changed = (
                initial_context is not None
                and (final_context != initial_context
                     or final_epoch != initial_epoch))
            camera = None if context_changed else (final_camera or initial_camera)
            local_role = getattr(self.esp, "_last_local_role", None)
            camera_ms += (time.monotonic() - final_camera_started) * 1000.0
            stats_dict = dict(getattr(self.esp, "_last_iter_stats", {}))
            stats_dict.update({
                "camera_ms": camera_ms,
                "players_ms": players_ms,
                "capsule_ms": capsule_ms,
                "world_epoch": initial_epoch,
                "skeleton_refresh_ms": self._last_skeleton_refresh_ms,
                "skeleton_attempts": self._last_skeleton_refresh_attempts,
                "skeleton_cache": self._skeleton_cache_size(),
            })
            failures = tuple(self._last_skeleton_failures)
            finished = time.monotonic()
            if context_changed:
                stats_dict["context_changed"] = True
                stats = tuple(stats_dict.items())
                return FrameRenderSnapshot(
                    sequence, started, finished,
                    (finished - started) * 1000.0,
                    None, None, (), stats, failures, None)
            stats = tuple(stats_dict.items())
            snapshot = FrameRenderSnapshot(
                sequence, started, finished, (finished - started) * 1000.0,
                camera, local_role, tuple(players), stats, failures, None)
            return snapshot
        except Exception as exc:
            finished = time.monotonic()
            return FrameRenderSnapshot(
                sequence, started, finished, (finished - started) * 1000.0,
                None, None, (), (("collection_valid", False),), (),
                f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _base_frame_is_publishable(frame):
        stats = dict(getattr(frame, "stats", ()))
        if stats.get("context_changed"):
            return True
        if (getattr(frame, "error", None)
                or getattr(frame, "camera", True) is None):
            return False
        return stats.get("collection_valid", True) is True

    def _ensure_skeleton_job_state(self):
        try:
            self._skeleton_job_lock
        except (AttributeError, RuntimeError):
            self._skeleton_job_lock = threading.Lock()
            self._skeleton_job_event = threading.Event()
            self._skeleton_pending_frame = None

    def _submit_skeleton_frame(self, frame):
        """Overwrite the pending skeleton job; old frames never form a queue."""
        self._ensure_skeleton_job_state()
        with self._skeleton_job_lock:
            self._skeleton_pending_frame = frame
            self._skeleton_job_event.set()

    def _take_skeleton_frame(self):
        self._ensure_skeleton_job_state()
        self._skeleton_job_event.wait(0.10)
        if self._snapshot_stop.is_set():
            return None
        with self._skeleton_job_lock:
            frame = self._skeleton_pending_frame
            self._skeleton_pending_frame = None
            self._skeleton_job_event.clear()
            return frame

    def _snapshot_loop(self):
        deadline = time.monotonic()
        try:
            while not self._snapshot_stop.is_set():
                frame = self._collect_snapshot()
                if self._base_frame_is_publishable(frame):
                    try:
                        enriched = self._enrich_snapshot_with_cached_skeletons(
                            frame)
                        if enriched is not None:
                            frame = enriched
                    except Exception:
                        self._last_skeleton_failures = (
                            ("enrich_exception", 1),)
                    self._snapshots.publish(frame)
                    self._submit_skeleton_frame(frame)
                deadline += self.COLLECT_INTERVAL
                now = time.monotonic()
                if deadline <= now:
                    # Never replay missed ticks.  The next cycle starts from now.
                    deadline = now
                    continue
                self._snapshot_stop.wait(deadline - now)
        finally:
            self._snapshot_stop.set()
            self._ensure_skeleton_job_state()
            self._skeleton_job_event.set()
            self._worker_finished("base")

    def _skeleton_loop(self):
        """Run every pose/mesh read away from the base collector."""
        try:
            while not self._snapshot_stop.is_set():
                frame = self._take_skeleton_frame()
                if frame is None:
                    continue
                self._sync_skeleton_epoch()
                try:
                    self._refresh_skeleton_cache(frame)
                except Exception:
                    self._last_skeleton_refresh_attempts = 0
                    self._last_skeleton_failures = (("refresh_exception", 1),)
        finally:
            # A fatal exit from either reader stops its peer.  The last worker
            # alone owns process-handle cleanup, so an active RPM is never closed
            # underneath the other thread.
            self._snapshot_stop.set()
            self._ensure_skeleton_job_state()
            self._skeleton_job_event.set()
            self._worker_finished("skeleton")

    def _ensure_worker_state(self, worker_name=None):
        try:
            self._worker_state_lock
        except (AttributeError, RuntimeError):
            self._worker_state_lock = threading.Lock()
            self._active_workers = set()
        try:
            self._active_workers
        except (AttributeError, RuntimeError):
            self._active_workers = set()
        if worker_name is not None and not self._active_workers:
            self._active_workers.add(worker_name)

    def _worker_finished(self, worker_name):
        self._ensure_worker_state(worker_name)
        with self._worker_state_lock:
            if worker_name not in self._active_workers:
                return
            self._active_workers.remove(worker_name)
            close_process = not self._active_workers
        if close_process:
            self._close_process_once()

    def _close_process_once(self):
        try:
            self._process_close_lock
        except (AttributeError, RuntimeError):
            self._process_close_lock = threading.Lock()
        with self._process_close_lock:
            if getattr(self, "_process_closed", False):
                return
            try:
                self.esp.pm.close_process()
            except Exception:
                pass
            self._process_closed = True

    def stop_worker(self):
        timer = getattr(self, "timer", None)
        if timer is not None:
            timer.stop()
        self._snapshot_stop.set()
        self._ensure_skeleton_job_state()
        self._skeleton_job_event.set()
        worker = getattr(self, "_snapshot_thread", None)
        if (worker is not None and worker.is_alive()
                and threading.current_thread() is not worker):
            worker.join(timeout=1.0)
        try:
            skeleton_worker = self._skeleton_thread
        except (AttributeError, RuntimeError):
            skeleton_worker = None
        if (skeleton_worker is not None and skeleton_worker.is_alive()
                and threading.current_thread() is not skeleton_worker):
            skeleton_worker.join(timeout=1.0)
        if ((worker is None or not worker.is_alive())
                and (skeleton_worker is None or not skeleton_worker.is_alive())):
            self._close_process_once()

    def closeEvent(self, event):
        self.stop_worker()
        super().closeEvent(event)

    @staticmethod
    def _player_color(config, player):
        """Choose a role color without depending on transient local-role state."""
        if player.is_local:
            return config.local_color
        if player.role == "hunter":
            return config.hunter_color
        return config.enemy_color

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Consolas", 10)
        painter.setFont(font)

        w = self.width()
        h = self.height()

        if not self.config.enabled:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(10, 20, "ESP OFF")
            return

        frame = self._snapshots.latest()
        if frame.error:
            painter.setPen(QPen(QColor(255, 96, 96)))
            painter.drawText(10, 20, f"ESP READ ERROR: {frame.error}")
            return
        cam = frame.camera
        if not cam:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(10, 20, "NO CAMERA")
            return
        paint_now = time.monotonic()
        snapshot_age = max(0.0, paint_now - frame.finished_at)
        oldest_data_age = max(0.0, paint_now - frame.started_at)
        if (snapshot_age > self.STALE_AFTER_SECONDS
                or frame.collection_ms > self.STALE_AFTER_SECONDS * 1000.0
                or oldest_data_age > self.STALE_AFTER_SECONDS):
            painter.setPen(QPen(QColor(255, 96, 96)))
            painter.drawText(
                10, 20,
                f"ESP DATA STALE ({snapshot_age * 1000.0:.0f}ms, "
                f"read {frame.collection_ms:.0f}ms)")
            if self.config.show_debug:
                stats = dict(frame.stats)
                painter.drawText(
                    10, 35,
                    f"STALE T:C{stats.get('camera_ms', 0.0):.1f} "
                    f"P{stats.get('players_ms', 0.0):.1f} "
                    f"B{stats.get('capsule_ms', 0.0):.1f} "
                    f"S{stats.get('skeleton_refresh_ms', 0.0):.1f}ms")
            return
        projector = ProjectionContext.build(cam, w, h)
        if projector is None:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(10, 20, "NO CAMERA")
            return

        count = 0
        capsule_boxes = 0
        approximate_boxes = 0
        skeleton_poses = 0
        skeleton_lines = 0
        for player in frame.players:
            color = Overlay._player_color(self.config, player)
            rect, box_source = self._project_box(
                player, cam, w, h, projector)
            center_screen = projector.project(player.position)

            drawn_skeleton_lines = 0
            pose_is_fresh = (
                player.pose is not None
                and player.pose_captured_at is not None
                and max(0.0, paint_now - player.pose_captured_at)
                <= self.SKELETON_CACHE_TTL_SECONDS)
            if self.config.skeleton_esp and pose_is_fresh:
                drawn_skeleton_lines = self._draw_skeleton(
                    painter, player.pose, projector, w, h, color)
                if drawn_skeleton_lines:
                    skeleton_poses += 1
                    skeleton_lines += drawn_skeleton_lines

            if self.config.box_esp and rect is not None:
                self._draw_box(painter, rect, color)
                if box_source == "capsule":
                    capsule_boxes += 1
                else:
                    approximate_boxes += 1

            visible_rect = intersect_rect(rect, w, h)
            if visible_rect is not None:
                left, _, right, bottom = visible_rect
                snap_target = ((left + right) * 0.5, bottom)
            elif center_screen is not None:
                snap_target = center_screen
            else:
                snap_target = None

            if self.config.snap_lines and snap_target is not None:
                painter.setPen(QPen(QColor(*color), 1))
                clipped = clip_line_to_viewport(
                    (w / 2.0, float(h - 1)), snap_target, w - 1, h - 1)
                if clipped is not None:
                    painter.drawLine(int(clipped[0][0]), int(clipped[0][1]),
                                     int(clipped[1][0]), int(clipped[1][1]))

            label_parts = []
            if self.config.show_names:
                label_parts.append(
                    "YOU" if player.is_local else f"Player {player.index}")
            if self.config.show_distance:
                d = int(dist(player.position, cam["loc"]) / 100)
                label_parts.append(f"{d}m")
            if label_parts and (rect is not None or center_screen is not None):
                painter.setPen(QPen(QColor(*color)))
                text = " | ".join(label_parts)
                metrics = painter.fontMetrics()
                if rect is not None:
                    label_rect = rect
                else:
                    label_rect = (*center_screen, *center_screen)
                label_target = label_position(
                    label_rect, metrics.horizontalAdvance(text), metrics.ascent(),
                    metrics.descent(), w, h)
                if label_target is not None:
                    painter.drawText(int(label_target[0]), int(label_target[1]), text)

            if rect is not None or center_screen is not None or drawn_skeleton_lines:
                count += 1

        stats = dict(frame.stats)
        painter.setPen(QPen(QColor(255, 255, 255)))
        if count == 0 and not stats.get("local_pawn", False):
            painter.drawText(10, 20, "WAITING FOR MATCH (NO PLAYER PAWN)")
        else:
            painter.drawText(10, 20, f"Players: {count}")
        if self.config.show_debug:
            line = (f"PA:{stats.get('pa_total', 0)}/{stats.get('pa_valid', 0)} "
                    f"LA:{stats.get('level_total', 0)}/{stats.get('level_valid', 0)} "
                    f"D:{stats.get('dead_filtered', 0)} "
                    f"U:{stats.get('state_unreadable', 0)} "
                    f"R:{stats.get('role_filtered', 0)} "
                    f"RM:{stats.get('roster_mode', 'none')}")
            painter.drawText(10, 35, line)
            painter.drawText(
                10, 50,
                f"BOX:C{capsule_boxes}/A{approximate_boxes} "
                f"SK:{skeleton_poses}/{skeleton_lines}")
            painter.drawText(
                10, 65,
                f"SEQ:{frame.sequence} READ:{frame.collection_ms:.1f}ms "
                f"AGE:{snapshot_age * 1000.0:.1f}ms")
            painter.drawText(
                10, 80,
                f"T:C{stats.get('camera_ms', 0.0):.1f} "
                f"P{stats.get('players_ms', 0.0):.1f} "
                f"B{stats.get('capsule_ms', 0.0):.1f} "
                f"S{stats.get('skeleton_refresh_ms', 0.0):.1f}ms "
                f"SA:{stats.get('skeleton_attempts', 0)} "
                f"SC:{stats.get('skeleton_cache', 0)}")
            skeleton_failures = dict(frame.skeleton_failures)
            if skeleton_failures:
                summary = ",".join(
                    f"{key}:{value}" for key, value in sorted(skeleton_failures.items()))
                painter.drawText(10, 95, f"SKF:{summary}")
            if self._layout_warnings:
                painter.drawText(
                    10, 110, f"LAYOUT WARNING: {self._layout_warnings[0]}")

    def _project_box(self, player, camera, screen_w, screen_h, projector):
        capsule = player.capsule
        if (capsule is not None and capsule.half_height is not None
                and capsule.radius is not None):
            rect = project_capsule_box(
                capsule.center, capsule.half_height, capsule.radius,
                camera, screen_w, screen_h, self.config.box_y_offset, projector)
            return rect, "capsule"
        fallback_center = (
            capsule.center if capsule is not None else player.position)
        source = "approximate-capsule" if capsule is not None else "approximate-root"
        rect = project_height_box(
            fallback_center, self.config.box_height_world, self.config.box_width_ratio,
            camera, screen_w, screen_h, self.config.box_y_offset, projector)
        return rect, source

    def _draw_box(self, painter, rect, color):
        painter.setPen(QPen(QColor(*color), self.config.box_line_width))
        painter.setBrush(Qt.NoBrush)
        for p1, p2 in box_segments(
                rect, self.config.box_style, self.config.box_corner_fraction):
            clipped = clip_line_to_viewport(p1, p2, self.width(), self.height())
            if clipped is not None:
                painter.drawLine(int(clipped[0][0]), int(clipped[0][1]),
                                 int(clipped[1][0]), int(clipped[1][1]))

    def _draw_skeleton(self, painter, pose, projector, screen_w, screen_h, color):
        projected = [
            projector.project(point, clip_to_screen=False)
            for point in pose.world_points
        ]
        painter.setPen(QPen(QColor(*color), self.config.skeleton_line_width))
        painter.setBrush(Qt.NoBrush)
        drawn = 0
        for parent, child in pose.draw_edges:
            p1 = projected[parent]
            p2 = projected[child]
            if p1 is None or p2 is None:
                continue
            clipped = clip_line_to_viewport(p1, p2, screen_w, screen_h)
            if clipped is None:
                continue
            painter.drawLine(int(clipped[0][0]), int(clipped[0][1]),
                             int(clipped[1][0]), int(clipped[1][1]))
            drawn += 1
        return drawn

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PerMonitorAwareV2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    _set_dpi_aware()
    app = QApplication(sys.argv)
    config = Config()
    try:
        esp = MecchaESP()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        message = (
            "ESP initialization failed.\n\n"
            f"{detail}\n\n"
            "Start the game, wait until the lobby has loaded, then run esp.py again.")
        print(message, file=sys.stderr)
        QMessageBox.critical(None, "MECCHA ESP", message)
        return 1
    menu = Menu(config)
    overlay = Overlay(esp, config, menu)
    overlay.show()
    menu.show()

    # Poll Insert/F1 globally to toggle menu visibility.
    VK_INSERT = 0x2D
    VK_F1 = 0x70
    _key_states = {"insert": False, "f1": False}

    def poll_keys():
        newly_pressed = False
        for vk, name in [(VK_INSERT, "insert"), (VK_F1, "f1")]:
            is_down = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
            newly_pressed = newly_pressed or (is_down and not _key_states[name])
            _key_states[name] = is_down
        if newly_pressed:
            menu.setVisible(not menu.isVisible())

    key_timer = QTimer()
    key_timer.timeout.connect(poll_keys)
    key_timer.start(50)

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
