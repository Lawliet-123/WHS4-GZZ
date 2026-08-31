import ast
import os
import struct
import inspect
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import esp


def pack_ftransform(quaternion=(0.0, 0.0, 0.0, 1.0),
                    translation=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0)):
    raw = bytearray(0x60)
    struct.pack_into("<4d", raw, 0x00, *quaternion)
    struct.pack_into("<3d", raw, 0x20, *translation)
    struct.pack_into("<3d", raw, 0x40, *scale)
    return bytes(raw)


class Memory:
    def __init__(self):
        self._bytes = {}
        self.read_calls = 0
        self.read_bytes_total = 0

    def put(self, address, data):
        for index, value in enumerate(data):
            self._bytes[address + index] = value

    def read_bytes(self, address, size):
        self.read_calls += 1
        self.read_bytes_total += size
        try:
            return bytes(self._bytes[address + index] for index in range(size))
        except KeyError as exc:
            raise RuntimeError(f"unmapped read at {address:#x} size {size:#x}") from exc


class DenseMemory(Memory):
    """Zero-filled address space used to exercise the live bulk-read path."""

    def read_bytes(self, address, size):
        self.read_calls += 1
        self.read_bytes_total += size
        return bytes(self._bytes.get(address + index, 0) for index in range(size))


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.camera = {"loc": (0.0, 0.0, 0.0),
                       "rot": (0.0, 0.0, 0.0),
                       "fov": 90.0}

    def test_world_to_screen_center_and_unclipped_mode(self):
        self.assertEqual(esp.w2s((100.0, 0.0, 0.0), self.camera, 1920, 1080),
                         (960.0, 540.0))
        self.assertIsNone(esp.w2s((100.0, 1000.0, 0.0), self.camera, 1920, 1080))
        self.assertIsNotNone(esp.w2s((100.0, 1000.0, 0.0), self.camera,
                                    1920, 1080, clip_to_screen=False))

    def test_capsule_and_fallback_boxes(self):
        capsule = esp.project_capsule_box(
            (1000.0, 0.0, 0.0), 100.0, 40.0, self.camera, 1920, 1080)
        self.assertIsNotNone(capsule)
        self.assertAlmostEqual(capsule[0], 920.0)
        self.assertAlmostEqual(capsule[1], 440.0)
        self.assertAlmostEqual(capsule[2], 1000.0)
        self.assertAlmostEqual(capsule[3], 640.0)

        fallback = esp.project_height_box(
            (1000.0, 0.0, 0.0), 180.0, 0.45, self.camera, 1920, 1080)
        self.assertIsNotNone(fallback)
        self.assertAlmostEqual(fallback[2] - fallback[0],
                               (fallback[3] - fallback[1]) * 0.45)

    def test_capsule_near_plane_fails_closed(self):
        self.assertIsNone(esp.project_capsule_box(
            (20.0, 0.0, 0.0), 40.0, 25.0, self.camera, 1920, 1080))

    def test_box_styles_and_line_clipping(self):
        rect = (10.0, 20.0, 110.0, 220.0)
        self.assertEqual(len(esp.box_segments(rect, "2D")), 4)
        self.assertEqual(len(esp.box_segments(rect, "Corner")), 8)
        self.assertEqual(
            esp.clip_line_to_viewport((-10.0, 50.0), (110.0, 50.0), 100, 100),
            ((0.0, 50.0), (100.0, 50.0)))

    def test_partial_rect_anchors_use_visible_bounds(self):
        visible = esp.intersect_rect((400.0, -100.0, 600.0, 1200.0), 1920, 1080)
        self.assertEqual(visible, (400.0, 0.0, 600.0, 1079.0))
        label = esp.label_position(
            (1800.0, -100.0, 2000.0, 300.0), 300.0, 12, 4, 1920, 1080)
        self.assertEqual(label, (1496.0, 12.0))

    def test_fallback_box_survives_camera_roll(self):
        rolled_camera = {"loc": (0.0, 0.0, 0.0),
                         "rot": (0.0, 0.0, 90.0),
                         "fov": 90.0}
        rect = esp.project_height_box(
            (1000.0, 0.0, 0.0), 180.0, 0.45,
            rolled_camera, 1920, 1080)
        self.assertIsNotNone(rect)
        self.assertGreater(rect[2] - rect[0], 1.0)
        self.assertGreater(rect[3] - rect[1], 1.0)


class TransformTests(unittest.TestCase):
    def test_ftransform_yaw_translation_and_inverse_round_trip(self):
        half_sqrt = 2.0 ** -0.5
        decoded = esp.decode_ftransform(pack_ftransform(
            (0.0, 0.0, half_sqrt, half_sqrt),
            (100.0, 200.0, 300.0)))
        self.assertIsNotNone(decoded)
        local_to_world, world_to_local, _, _ = decoded
        world = esp.transform_position_row((1.0, 2.0, 3.0), local_to_world)
        for actual, expected in zip(world, (98.0, 201.0, 303.0)):
            self.assertAlmostEqual(actual, expected)
        round_trip = esp.transform_position_row(world, world_to_local)
        for actual, expected in zip(round_trip, (1.0, 2.0, 3.0)):
            self.assertAlmostEqual(actual, expected)

    def test_invalid_ftransform_is_rejected(self):
        self.assertIsNone(esp.decode_ftransform(pack_ftransform(
            quaternion=(0.0, 0.0, 0.0, 0.5))))
        self.assertIsNone(esp.decode_ftransform(pack_ftransform(
            scale=(1.0, 0.0, 1.0))))

    def test_component_snapshot_requires_updated_flag(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {
            "USceneComponent::ComponentToWorld": 0x1E0,
            "USceneComponent::TransformFlags": 0x1A0,
        }
        component = 0x10000
        memory.put(component + 0x1A0, b"\x01")
        memory.put(component + 0x1E0, pack_ftransform(
            translation=(10.0, 20.0, 30.0)))
        snapshot = reader._component_world_transform_snapshot(component)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot[1][2], (10.0, 20.0, 30.0))

        memory.put(component + 0x1A0, b"\x00")
        self.assertIsNone(reader._component_world_transform_snapshot(component))


class LayoutTests(unittest.TestCase):
    def test_pe_fingerprint_parser(self):
        memory = Memory()
        base = 0x1000
        memory.put(base, b"MZ")
        memory.put(base + 0x3C, struct.pack("<I", 0x80))
        pe = base + 0x80
        memory.put(pe, b"PE\0\0")
        memory.put(pe + 0x8, struct.pack("<I", 0x15CBD51C))
        optional = pe + 0x18
        memory.put(optional, struct.pack("<H", 0x20B))
        memory.put(optional + 0x38, struct.pack("<I", 0x0A3FA000))
        memory.put(optional + 0x40, struct.pack("<I", 0x0A041AE7))
        self.assertEqual(
            esp.read_pe_fingerprint(memory, base),
            (0x0A3FA000, 0x15CBD51C, 0x0A041AE7))

    def test_current_steam_build_is_explicitly_supported(self):
        self.assertIn(
            (0x0A3FB000, 0x018D3C6F, 0x0A046E92),
            esp.MecchaESP.SUPPORTED_BUILD_FINGERPRINTS)

    def test_tarray_max_is_at_offset_c(self):
        memory = Memory()
        memory.put(0x1000, struct.pack("<Qii", 0x2000, 7, 11))
        memory.put(0x1010, struct.pack("<I", 999))
        self.assertEqual(esp.read_array(memory, 0x1000), (0x2000, 7, 11))

    def test_weak_pointer_null_matches_shipping_getter(self):
        self.assertTrue(esp.weak_object_ptr_is_null(struct.pack("<ii", 0, 0)))
        self.assertTrue(esp.weak_object_ptr_is_null(struct.pack("<ii", -1, 0)))
        self.assertFalse(esp.weak_object_ptr_is_null(struct.pack("<ii", 42, 7)))

    def test_offset_resolver_walks_more_than_one_superclass(self):
        memory = Memory()
        class_a, class_b, class_c, field = 0x1000, 0x2000, 0x3000, 0x4000
        for cls, parent, child in ((class_a, class_b, 0),
                                   (class_b, class_c, 0),
                                   (class_c, 0, field)):
            memory.put(cls + esp.OFFSETS["UStruct::SuperStruct"],
                       struct.pack("<Q", parent))
            memory.put(cls + esp.OFFSETS["UStruct::ChildProperties"],
                       struct.pack("<Q", child))
        memory.put(field + esp.OFFSETS["FField::NamePrivate"], struct.pack("<I", 7))
        memory.put(field + esp.OFFSETS["FField::Next"], struct.pack("<Q", 0))
        memory.put(field + esp.OFFSETS["FProperty::Offset_Internal"],
                   struct.pack("<I", 0xABC))

        class Names:
            @staticmethod
            def resolve(value):
                return "Target" if value == 7 else None

        class Objects:
            fnames = Names()

            @staticmethod
            def find_class(name):
                return class_a if name == "Child" else 0

        resolver = esp.OffsetResolver(memory, Objects())
        self.assertEqual(resolver.resolve("Child", "Target"), 0xABC)

    def test_cooked_profile_edges_match_the_parsed_parent_tables(self):
        self.assertEqual(set(esp.SKELETON_MESH_PROFILES.values()),
                         {esp.PAINTMAN_PROFILE, esp.NEWPENGUN_PROFILE})
        for profile in esp.SKELETON_PROFILES.values():
            self.assertEqual(len(profile.bone_names), len(profile.parents))
            for index, parent in enumerate(profile.parents):
                self.assertTrue(parent == -1 or 0 <= parent < index)
            for parent, child in profile.draw_edges:
                self.assertEqual(profile.parents[child], parent)


class SkeletonSnapshotTests(unittest.TestCase):
    def _bulk_pose_reader(self):
        memory = DenseMemory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader._advanced_build_ok = True
        reader.offsets = {
            "USkinnedMeshComponent::SkeletalMesh": 0x578,
            "USkinnedMeshComponent::SkinnedAsset": 0x580,
            "USkinnedMeshComponent::LeaderPoseComponent": 0x588,
            "USkinnedMeshComponent::ComponentSpaceTransformsArray": 0x5F0,
            "USkinnedMeshComponent::CurrentReadComponentTransforms": 0x638,
            "USkeletalMeshComponent::CachedComponentSpaceTransforms": 0x9B8,
            "USkeletalMesh::Skeleton": 0xF8,
            "USceneComponent::ComponentToWorld": 0x1E0,
            "USceneComponent::TransformFlags": 0x1A0,
        }
        mesh, pose_data = 0x10000, 0x20000
        mesh_asset, skeleton = 0x30000, 0x40000
        profile = esp.PAINTMAN_PROFILE
        reader._character_mesh = lambda actor, refresh=False: mesh
        reader._skeleton_profile = lambda found_mesh: (
            profile, mesh_asset, skeleton)
        memory.put(mesh + 0x578, struct.pack("<Q", mesh_asset))
        memory.put(mesh + 0x580, struct.pack("<Q", mesh_asset))
        memory.put(mesh + 0x588, struct.pack("<ii", 0, 0))
        memory.put(mesh + 0x1A0, b"\x01")
        memory.put(mesh + 0x1E0, pack_ftransform(
            translation=(100.0, 200.0, 300.0)))
        memory.put(mesh + 0x638, struct.pack("<i", 0))
        memory.put(mesh + 0x9B8, struct.pack(
            "<Qii", pose_data, len(profile.bone_names), len(profile.bone_names)))
        transforms = bytearray(len(profile.bone_names) * 0x60)
        for index in range(len(profile.bone_names)):
            struct.pack_into("<3d", transforms, index * 0x60 + 0x20,
                             float(index), 0.0, 0.0)
        memory.put(pose_data, transforms)
        return reader, memory, mesh, pose_data, profile

    def _sparse_native_pose_reader(self, missing=(), sdk_data=None):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader._advanced_build_ok = True
        reader.offsets = {
            "USkinnedMeshComponent::SkeletalMesh": 0x578,
            "USkinnedMeshComponent::SkinnedAsset": 0x580,
            "USkinnedMeshComponent::LeaderPoseComponent": 0x588,
            "USkinnedMeshComponent::ComponentSpaceTransformsArray": 0x5F0,
            "USkinnedMeshComponent::CurrentReadComponentTransforms": 0x638,
            "USkeletalMeshComponent::CachedComponentSpaceTransforms": 0x9B8,
            "USkeletalMesh::Skeleton": 0xF8,
            "USceneComponent::ComponentToWorld": 0x1E0,
            "USceneComponent::TransformFlags": 0x1A0,
        }
        mesh, native_data = 0x10000, 0x20000
        mesh_asset, skeleton = 0x30000, 0x40000
        profile = esp.PAINTMAN_PROFILE
        reader._character_mesh = lambda actor, refresh=False: mesh
        reader._skeleton_profile = lambda found_mesh: (
            profile, mesh_asset, skeleton)

        fields = {
            "legacy": (mesh + 0x578, struct.pack("<Q", mesh_asset)),
            "asset": (mesh + 0x580, struct.pack("<Q", mesh_asset)),
            "leader": (mesh + 0x588, struct.pack("<ii", 0, 0)),
            "native": (mesh + 0x5F0, struct.pack(
                "<Qii", native_data, len(profile.bone_names),
                len(profile.bone_names))),
            "alternate": (mesh + 0x600, bytes(0x10)),
            "selector": (mesh + 0x638, struct.pack("<i", 0)),
        }
        for name, (address, data) in fields.items():
            if name not in missing:
                memory.put(address, data)
        if sdk_data is not None:
            memory.put(mesh + 0x9B8, struct.pack(
                "<Qii", sdk_data, len(profile.bone_names),
                len(profile.bone_names)))
        memory.put(mesh + 0x1A0, b"\x01")
        memory.put(mesh + 0x1E0, pack_ftransform(
            translation=(100.0, 200.0, 300.0)))
        transforms = bytearray(len(profile.bone_names) * 0x60)
        for index in range(len(profile.bone_names)):
            struct.pack_into("<3d", transforms, index * 0x60 + 0x20,
                             float(index), 0.0, 0.0)
        memory.put(native_data, transforms)
        return reader, memory

    def test_unsupported_skeleton_profile_is_negative_cached(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {
            "USkinnedMeshComponent::SkeletalMesh": 0x578,
            "USkinnedMeshComponent::SkinnedAsset": 0x580,
            "USkeletalMesh::Skeleton": 0xF8,
        }
        mesh, mesh_asset, skeleton = 0x10000, 0x20000, 0x30000
        memory.put(mesh + 0x578, struct.pack("<Q", mesh_asset))
        memory.put(mesh + 0x580, struct.pack("<Q", mesh_asset))
        memory.put(mesh_asset + 0xF8, struct.pack("<Q", skeleton))
        reader._object_is_a = lambda obj, class_name: True
        reader.objects = type("Objects", (), {
            "_obj_name": staticmethod(
                lambda obj: "UnsupportedMesh" if obj == mesh_asset
                else "UnsupportedSkeleton")})()
        reader._skeleton_binding_cache = {}
        reader._skeleton_profile_miss_cache = {}

        self.assertIsNone(reader._skeleton_profile(mesh))
        self.assertEqual(
            reader._skeleton_profile_miss_cache[mesh][1], float("inf"))
        reads_after_first_miss = memory.read_calls
        with mock.patch.object(esp.time, "monotonic", return_value=1.0e12):
            self.assertIsNone(reader._skeleton_profile(mesh))
        # The two mesh-identity reads remain; the Skeleton pointer/name walk is
        # skipped until the short negative-cache interval expires.
        self.assertEqual(memory.read_calls - reads_after_first_miss, 2)

    def test_sdk_cache_bulk_path_has_five_process_reads(self):
        reader, memory, _, _, _ = self._bulk_pose_reader()
        pose = reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0))
        self.assertIsNotNone(pose)
        self.assertEqual(memory.read_calls, 5)
        self.assertEqual(reader._skeleton_source_counts, {"sdk-cache": 1})

    def test_sdk_cache_rejects_payload_changed_during_read(self):
        reader, memory, _, pose_data, _ = self._bulk_pose_reader()
        original_read = memory.read_bytes
        pose_reads = 0

        def changing_read(address, size):
            nonlocal pose_reads
            raw = original_read(address, size)
            if address == pose_data:
                pose_reads += 1
                if pose_reads == 2:
                    changed = bytearray(raw)
                    changed[0x20] ^= 1
                    return bytes(changed)
            return raw

        memory.read_bytes = changing_read
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))
        self.assertEqual(reader._skeleton_failure_counts, {"race": 1})

    def test_aliased_sdk_and_native_payload_must_both_be_stable(self):
        reader, memory, mesh, pose_data, profile = self._bulk_pose_reader()
        memory.put(mesh + 0x5F0, struct.pack(
            "<Qii", pose_data, len(profile.bone_names),
            len(profile.bone_names)))
        memory.put(mesh + 0x638, struct.pack("<i", 0))
        original_read = memory.read_bytes
        pose_reads = 0

        def always_changing_read(address, size):
            nonlocal pose_reads
            raw = original_read(address, size)
            if address == pose_data:
                pose_reads += 1
                changed = bytearray(raw)
                struct.pack_into("<d", changed, 0x20, float(pose_reads * 100))
                return bytes(changed)
            return raw

        memory.read_bytes = always_changing_read
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))
        self.assertEqual(pose_reads, 4)
        self.assertEqual(reader._skeleton_failure_counts, {"race": 1})

    def test_native_fallback_never_reads_non_current_buffer(self):
        reader, memory, mesh, pose_data, profile = self._bulk_pose_reader()
        memory.put(mesh + 0x9B8, bytes(0x10))
        memory.put(mesh + 0x5F0, bytes(0x10))
        memory.put(mesh + 0x600, struct.pack(
            "<Qii", pose_data, len(profile.bone_names), len(profile.bone_names)))
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))
        self.assertEqual(reader._skeleton_failure_counts["pose_header"], 1)

    def test_unreadable_sdk_payload_falls_back_to_native_current(self):
        reader, _ = self._sparse_native_pose_reader(sdk_data=0x25000)
        pose = reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0))
        self.assertIsNotNone(pose)
        self.assertEqual(reader._skeleton_source_counts, {"native-current": 1})

    def test_sparse_metadata_missing_leader_fails_closed(self):
        reader, _ = self._sparse_native_pose_reader(missing={"leader"})
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))
        self.assertEqual(reader._skeleton_failure_counts, {"read": 1})

    def test_sparse_metadata_missing_selector_fails_closed(self):
        reader, _ = self._sparse_native_pose_reader(missing={"selector"})
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))
        self.assertEqual(reader._skeleton_failure_counts, {"pose_header": 1})

    def test_stable_component_pose_is_transformed_to_world(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {
            "USkinnedMeshComponent::SkeletalMesh": 0x578,
            "USkinnedMeshComponent::SkinnedAsset": 0x580,
            "USkinnedMeshComponent::LeaderPoseComponent": 0x588,
            "USkinnedMeshComponent::ComponentSpaceTransformsArray": 0x5F0,
            "USkinnedMeshComponent::CurrentReadComponentTransforms": 0x638,
            "USkeletalMesh::Skeleton": 0xF8,
            "USceneComponent::ComponentToWorld": 0x1E0,
            "USceneComponent::TransformFlags": 0x1A0,
        }
        mesh = 0x10000
        pose_data = 0x20000
        mesh_asset = 0x30000
        skeleton = 0x40000
        profile = esp.PAINTMAN_PROFILE
        reader._character_mesh = lambda actor, refresh=False: mesh
        reader._skeleton_profile = lambda found_mesh: (profile, mesh_asset, skeleton)

        memory.put(mesh + 0x578, struct.pack("<Q", mesh_asset))
        memory.put(mesh + 0x580, struct.pack("<Q", mesh_asset))
        memory.put(mesh_asset + 0xF8, struct.pack("<Q", skeleton))
        memory.put(mesh + 0x588, struct.pack("<ii", 0, 0))
        memory.put(mesh + 0x1A0, b"\x01")
        memory.put(mesh + 0x1E0, pack_ftransform(
            translation=(100.0, 200.0, 300.0)))
        memory.put(mesh + 0x638, struct.pack("<i", 0))
        memory.put(mesh + 0x5F0,
                   struct.pack("<Qii", pose_data, len(profile.bone_names),
                               len(profile.bone_names)))

        transforms = bytearray(len(profile.bone_names) * 0x60)
        for index in range(len(profile.bone_names)):
            struct.pack_into("<4d", transforms, index * 0x60, 0.0, 0.0, 0.0, 1.0)
            struct.pack_into("<3d", transforms, index * 0x60 + 0x20,
                             float(index), 0.0, 0.0)
            struct.pack_into("<3d", transforms, index * 0x60 + 0x40,
                             1.0, 1.0, 1.0)
        memory.put(pose_data, transforms)

        pose = reader.read_skeleton_pose(0x50000, (100.0, 200.0, 300.0))
        self.assertIsNotNone(pose)
        self.assertEqual(pose.profile_name, "paintman_Skeleton")
        for actual, expected in zip(pose.world_points[0], (100.0, 200.0, 300.0)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(pose.world_points[-1], (127.0, 200.0, 300.0)):
            self.assertAlmostEqual(actual, expected)

        memory.put(mesh + 0x600,
                   struct.pack("<Qii", pose_data, len(profile.bone_names),
                               len(profile.bone_names)))
        memory.put(mesh + 0x638, struct.pack("<i", 1))
        self.assertIsNotNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))

        # The SDK-declared USkeletalMeshComponent cache is the primary source.
        reader.offsets[
            "USkeletalMeshComponent::CachedComponentSpaceTransforms"] = 0x9B8
        cached_pose_data = 0x25000
        cached_transforms = bytearray(transforms)
        struct.pack_into(
            "<3d", cached_transforms,
            (len(profile.bone_names) - 1) * 0x60 + 0x20,
            1027.0, 0.0, 0.0)
        memory.put(cached_pose_data, cached_transforms)
        memory.put(mesh + 0x9B8,
                   struct.pack("<Qii", cached_pose_data, len(profile.bone_names),
                               len(profile.bone_names)))
        cache_preferred_pose = reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0))
        self.assertAlmostEqual(cache_preferred_pose.world_points[-1][0], 1127.0)

        memory.put(mesh + 0x638, struct.pack("<i", 2))
        cache_ignores_native_selector = reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0))
        self.assertAlmostEqual(
            cache_ignores_native_selector.world_points[-1][0], 1127.0)

        memory.put(mesh + 0x5F0, struct.pack("<Qii", 0, 0, 0))
        memory.put(mesh + 0x600, struct.pack("<Qii", 0, 0, 0))
        cache_pose = reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0))
        self.assertAlmostEqual(cache_pose.world_points[-1][0], 1127.0)

        reader.offsets.pop(
            "USkeletalMeshComponent::CachedComponentSpaceTransforms")
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))

        memory.put(mesh + 0x638, struct.pack("<i", 0))
        memory.put(mesh + 0x588, struct.pack("<ii", 42, 7))
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))

        memory.put(mesh + 0x588, struct.pack("<ii", 0, 0))
        memory.put(mesh + 0x1A0, b"\x00")
        self.assertIsNone(reader.read_skeleton_pose(
            0x50000, (100.0, 200.0, 300.0)))


class SnapshotArchitectureTests(unittest.TestCase):
    def test_latest_snapshot_store_never_queues_old_frames(self):
        store = esp.LatestSnapshotStore(-1)
        for sequence in range(100):
            store.publish(sequence)
        self.assertEqual(store.latest(), 99)

    def test_camera_snapshot_is_detached_and_immutable(self):
        source = {"loc": [1.0, 2.0, 3.0], "rot": [4.0, 5.0, 6.0],
                  "fov": 90.0}
        camera = esp.Overlay._copy_camera(source)
        source["loc"][0] = 999.0
        self.assertEqual(camera["loc"], (1.0, 2.0, 3.0))
        with self.assertRaises(AttributeError):
            camera.fov = 100.0

    def test_paint_event_performs_no_process_access(self):
        source = inspect.getsource(esp.Overlay.paintEvent)
        self.assertNotIn("self.esp", source)
        self.assertNotIn("iter_players", source)
        self.assertNotIn("read_skeleton_pose", source)

    def test_paint_and_render_helpers_use_snapshot_only(self):
        class ExplodingESP:
            def __getattribute__(self, name):
                raise AssertionError(f"paint accessed live ESP state: {name}")

        class Metrics:
            @staticmethod
            def horizontalAdvance(text):
                return len(text) * 8

            @staticmethod
            def ascent():
                return 10

            @staticmethod
            def descent():
                return 3

        class Painter:
            Antialiasing = 1

            def __init__(self, target):
                pass

            def setRenderHint(self, *args):
                pass

            def setFont(self, *args):
                pass

            def setPen(self, *args):
                pass

            def setBrush(self, *args):
                pass

            def drawText(self, *args):
                pass

            def drawLine(self, *args):
                pass

            def drawEllipse(self, *args):
                pass

            @staticmethod
            def fontMetrics():
                return Metrics()

        class PaintTarget:
            STALE_AFTER_SECONDS = esp.Overlay.STALE_AFTER_SECONDS
            _project_box = esp.Overlay._project_box
            _draw_box = esp.Overlay._draw_box
            _draw_skeleton = esp.Overlay._draw_skeleton

            @staticmethod
            def width():
                return 800

            @staticmethod
            def height():
                return 600

        now = esp.time.monotonic()
        pose = esp.SkeletonPose(
            "test", ((1000.0, 0.0, -50.0), (1000.0, 0.0, 50.0)),
            ((0, 1),))
        player = esp.PlayerRenderSnapshot(
            False, (1000.0, 0.0, 0.0), 1, 0x12340, "hunter",
            esp.CapsuleGeometry((1000.0, 0.0, 0.0), 90.0, 40.0), pose)
        frame = esp.FrameRenderSnapshot(
            1, now, now, 0.1,
            {"loc": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "fov": 90.0},
            "survivor", (player,), (("local_pawn", True),), (), None)
        target = PaintTarget()
        target.esp = ExplodingESP()
        target.config = esp.Config(show_debug=True)
        target._snapshots = esp.LatestSnapshotStore(frame)
        target._layout_warnings = ()
        with mock.patch.multiple(
                esp, QPainter=Painter, QPen=lambda *args: None,
                QColor=lambda *args: None, QFont=lambda *args: None):
            esp.Overlay.paintEvent(target, None)

    def test_stop_worker_retries_cleanup_and_closes_once(self):
        class Timer:
            stops = 0

            def stop(self):
                self.stops += 1

        class PM:
            closes = 0

            def close_process(self):
                self.closes += 1

        class Worker:
            alive = True
            joins = 0

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                self.joins += 1

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.timer = Timer()
        overlay.esp = type("FakeESP", (), {"pm": PM()})()
        overlay._snapshot_stop = esp.threading.Event()
        overlay._snapshot_thread = Worker()
        overlay._process_closed = False
        overlay._process_close_lock = esp.threading.Lock()

        overlay.stop_worker()
        self.assertTrue(overlay._snapshot_stop.is_set())
        self.assertEqual(overlay._snapshot_thread.joins, 1)
        self.assertEqual(overlay.esp.pm.closes, 0)

        overlay._snapshot_thread.alive = False
        overlay.stop_worker()
        overlay.stop_worker()
        self.assertEqual(overlay.esp.pm.closes, 1)

    def test_worker_finally_owns_eventual_process_cleanup(self):
        class PM:
            closes = 0

            def close_process(self):
                self.closes += 1

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = type("FakeESP", (), {"pm": PM()})()
        overlay._snapshot_stop = esp.threading.Event()
        overlay._snapshot_stop.set()
        overlay._process_closed = False
        overlay._process_close_lock = esp.threading.Lock()
        overlay._snapshot_loop()
        self.assertEqual(overlay.esp.pm.closes, 1)

    def test_collector_feature_gates_expensive_geometry_reads(self):
        actor = 0x12340

        class FakeESP:
            def __init__(self):
                self.capsule_reads = 0
                self.pose_reads = 0
                self._last_actor_roles = {actor: "hunter"}
                self._last_local_role = "survivor"
                self._last_iter_stats = {"local_pawn": True, "rendered": 1}
                self._skeleton_failure_counts = {}

            @staticmethod
            def get_camera():
                return {"loc": (0.0, 0.0, 0.0),
                        "rot": (0.0, 0.0, 0.0), "fov": 90.0}

            @staticmethod
            def iter_players(**kwargs):
                yield False, (1000.0, 0.0, 0.0), 1, actor

            @staticmethod
            def character_role(found_actor):
                raise AssertionError("cached role must not be eagerly re-read")

            def read_capsule_geometry(self, found_actor):
                self.capsule_reads += 1
                return None

            def read_skeleton_pose(self, found_actor, position):
                self.pose_reads += 1
                return None

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(box_esp=False, skeleton_esp=False)
        overlay._snapshot_sequence = 0
        overlay._viewport_size = (1920, 1080)
        snapshot = overlay._collect_snapshot()
        self.assertIsNone(snapshot.error)
        self.assertEqual(overlay.esp.capsule_reads, 0)
        self.assertEqual(overlay.esp.pose_reads, 0)

        overlay.config.box_esp = True
        overlay.config.skeleton_esp = True
        snapshot = overlay._collect_snapshot()
        self.assertIsNone(snapshot.error)
        self.assertEqual(overlay.esp.capsule_reads, 1)
        # Full poses are optional enrichment and must not delay publication of
        # the base player/capsule frame.
        self.assertEqual(overlay.esp.pose_reads, 0)
        overlay._refresh_skeleton_cache(snapshot)
        self.assertEqual(overlay.esp.pose_reads, 1)

    def test_slow_skeleton_sampling_keeps_base_frame_fresh_and_fair(self):
        actors = tuple(0x10000 + index * 0x100 for index in range(16))

        class FakeClock:
            now = 100.0

            @classmethod
            def monotonic(cls):
                return cls.now

            @classmethod
            def advance(cls, seconds):
                cls.now += seconds

        class FakeESP:
            def __init__(self):
                self._world_epoch = 1
                self._last_actor_roles = {
                    actor: "survivor" for actor in actors}
                self._last_local_role = "hunter"
                self._last_iter_stats = {
                    "local_pawn": True, "pa_total": len(actors),
                    "pa_valid": len(actors), "rendered": len(actors)}
                self._last_actor_transforms = {}
                self._character_component_cache = {}
                self._skeleton_failure_counts = {}
                self.pose_reads = []
                self.iteration = 0

            @staticmethod
            def get_camera():
                return {"loc": (0.0, 0.0, 0.0),
                        "rot": (0.0, 0.0, 0.0), "fov": 90.0}

            def iter_players(self, **kwargs):
                order = actors if self.iteration % 2 == 0 else tuple(
                    reversed(actors))
                self.iteration += 1
                for actor in order:
                    index = actors.index(actor)
                    yield False, (1000.0, float(index * 4), 0.0), index, actor

            @staticmethod
            def character_role(found_actor):
                return "survivor"

            @staticmethod
            def read_capsule_geometry(found_actor):
                index = actors.index(found_actor)
                return esp.CapsuleGeometry(
                    (1000.0, float(index * 4), 0.0), 90.0, 40.0)

            @staticmethod
            def _character_mesh(found_actor, refresh=False):
                return found_actor

            @staticmethod
            def _component_world_transform_snapshot(found_mesh):
                index = actors.index(found_mesh)
                raw = pack_ftransform(
                    translation=(1000.0, float(index * 4), 0.0))
                return raw, esp.decode_ftransform(raw)

            def read_skeleton_pose(self, found_actor, position):
                self.pose_reads.append(found_actor)
                FakeClock.advance(1.906 / len(actors))
                return esp.SkeletonPose(
                    "delayed-test",
                    (tuple(position),
                     (position[0], position[1], position[2] + 80.0)),
                    ((0, 1),),
                    ((0.0, 0.0, 0.0), (0.0, 0.0, 80.0)),
                    found_actor)

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(
            box_esp=True, skeleton_esp=True, snap_lines=False)
        overlay._snapshot_sequence = 0
        overlay._viewport_size = (1920, 1080)

        displayed_actors = set()
        with mock.patch.object(esp.time, "monotonic", FakeClock.monotonic):
            for _ in actors:
                frame = overlay._collect_snapshot()
                self.assertIsNone(frame.error)
                self.assertLess(
                    frame.collection_ms,
                    overlay.STALE_AFTER_SECONDS * 1000.0)
                self.assertEqual(len(frame.players), len(actors))
                self.assertTrue(all(
                    player.capsule is not None for player in frame.players))
                enriched = overlay._enrich_snapshot_with_cached_skeletons(frame)
                visible_frame = enriched or frame
                displayed_actors.update(
                    player.actor for player in visible_frame.players
                    if player.pose is not None)
                reads_before = len(overlay.esp.pose_reads)
                overlay._refresh_skeleton_cache(frame)
                self.assertLessEqual(
                    len(overlay.esp.pose_reads) - reads_before, 1)
            final_frame = overlay._collect_snapshot()
            final_frame = (
                overlay._enrich_snapshot_with_cached_skeletons(final_frame)
                or final_frame)
            displayed_actors.update(
                player.actor for player in final_frame.players
                if player.pose is not None)

        self.assertEqual(set(overlay.esp.pose_reads), set(actors))
        self.assertEqual(displayed_actors, set(actors))

    def test_box_base_publishes_without_cached_pose_memory_reads(self):
        actor = 0x12340
        mesh = 0x56780

        class FakeESP:
            def __init__(self):
                self._world_epoch = 1
                self._last_actor_roles = {actor: "survivor"}
                self._last_local_role = "hunter"
                self._last_iter_stats = {"local_pawn": True, "rendered": 1}
                self._last_actor_transforms = {}
                self._skeleton_failure_counts = {}
                self.mesh_reads = 0

            @staticmethod
            def get_camera():
                return {"loc": (0.0, 0.0, 0.0),
                        "rot": (0.0, 0.0, 0.0), "fov": 90.0}

            @staticmethod
            def iter_players(**kwargs):
                yield False, (1000.0, 0.0, 0.0), 1, actor

            @staticmethod
            def character_role(found_actor):
                return "survivor"

            def _character_mesh(self, found_actor, refresh=False):
                self.mesh_reads += 1
                return mesh

            @staticmethod
            def _component_world_transform_snapshot(found_mesh):
                raw = pack_ftransform(translation=(1000.0, 0.0, 0.0))
                return raw, esp.decode_ftransform(raw)

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(box_esp=False, skeleton_esp=True)
        overlay._snapshot_sequence = 0
        overlay._viewport_size = (1920, 1080)
        overlay._ensure_skeleton_state()
        pose = esp.SkeletonPose(
            "cached", ((1000.0, 0.0, 0.0),), (),
            ((0.0, 0.0, 0.0),), mesh)
        now = esp.time.monotonic()
        overlay._skeleton_pose_cache[actor] = esp.CachedSkeletonPose(
            now, pose, (1000.0, 0.0, 0.0), None, 1)

        base = overlay._collect_snapshot()
        self.assertEqual(overlay.esp.mesh_reads, 0)
        self.assertIsNone(base.players[0].pose)
        enriched = overlay._enrich_snapshot_with_cached_skeletons(base)
        self.assertIsNotNone(enriched)
        self.assertEqual(overlay.esp.mesh_reads, 1)
        self.assertIsNotNone(enriched.players[0].pose)

    def test_skeleton_refresh_hard_caps_one_sample_per_cycle(self):
        actors = (0x10000, 0x20000)

        class FakeClock:
            now = 200.0

            @classmethod
            def monotonic(cls):
                return cls.now

            @classmethod
            def advance(cls, seconds):
                cls.now += seconds

        class FakeESP:
            _world_epoch = 1

            def __init__(self):
                self.pose_reads = []
                self._skeleton_failure_counts = {}

            def read_skeleton_pose(self, actor, position):
                self.pose_reads.append(actor)
                FakeClock.advance(0.001 if len(self.pose_reads) == 1 else 1.906)
                return esp.SkeletonPose(
                    "hard-cap", (tuple(position),), (),
                    ((0.0, 0.0, 0.0),), actor)

        players = tuple(
            esp.PlayerRenderSnapshot(
                False, (1000.0, float(index * 10), 0.0), index,
                actor, "survivor", None, None)
            for index, actor in enumerate(actors))
        frame = esp.FrameRenderSnapshot(
            1, FakeClock.now, FakeClock.now, 0.0,
            {"loc": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0),
             "fov": 90.0},
            "hunter", players, (), (), None)
        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(skeleton_esp=True)
        overlay._viewport_size = (1920, 1080)

        with mock.patch.object(esp.time, "monotonic", FakeClock.monotonic):
            overlay._refresh_skeleton_cache(frame)

        self.assertEqual(len(overlay.esp.pose_reads), 1)
        self.assertAlmostEqual(FakeClock.now, 200.001)

    def test_stale_base_frame_skips_skeleton_enrichment(self):
        actor = 0x10000
        now = esp.time.monotonic()
        player = esp.PlayerRenderSnapshot(
            False, (1000.0, 0.0, 0.0), 0, actor, "survivor",
            None, None)
        frame = esp.FrameRenderSnapshot(
            1, now - 0.300, now, 300.0,
            {"loc": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0),
             "fov": 90.0},
            "hunter", (player,), (), (), None)

        class FakeESP:
            _world_epoch = 1
            pose_reads = 0

            def read_skeleton_pose(self, found_actor, position):
                self.pose_reads += 1
                return None

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(skeleton_esp=True)
        overlay._viewport_size = (1920, 1080)
        overlay._refresh_skeleton_cache(frame)
        self.assertEqual(overlay.esp.pose_reads, 0)

    def test_worker_publishes_before_optional_skeleton_refresh(self):
        events = []
        base_frame = object()
        enriched_frame = object()
        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.config = esp.Config()
        overlay.COLLECT_INTERVAL = 0.0
        overlay._snapshot_stop = esp.threading.Event()
        overlay._collect_snapshot = lambda: base_frame
        overlay._snapshots = type("Store", (), {
            "publish": lambda self, frame: events.append(
                "publish_base" if frame is base_frame else "publish_enriched")})()
        overlay._enrich_snapshot_with_cached_skeletons = (
            lambda frame: enriched_frame)

        def refresh(frame):
            events.append("refresh")
            overlay._snapshot_stop.set()

        overlay._refresh_skeleton_cache = refresh
        overlay._close_process_once = lambda: None
        overlay._snapshot_loop()
        self.assertEqual(
            events, ["publish_base", "publish_enriched", "refresh"])

    def test_skeleton_refresh_exception_does_not_kill_reader_loop(self):
        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.config = esp.Config()
        overlay.COLLECT_INTERVAL = 0.0
        overlay._snapshot_stop = esp.threading.Event()
        overlay._collect_snapshot = lambda: object()
        publish_count = [0]

        def publish(frame):
            publish_count[0] += 1
            if publish_count[0] == 2:
                overlay._snapshot_stop.set()

        overlay._snapshots = type("Store", (), {"publish": lambda self, frame: publish(frame)})()
        refresh_count = [0]

        def refresh(frame):
            refresh_count[0] += 1
            if refresh_count[0] == 1:
                raise RuntimeError("synthetic refresh failure")

        overlay._refresh_skeleton_cache = refresh
        overlay._close_process_once = lambda: None
        overlay._snapshot_loop()
        self.assertEqual(publish_count[0], 2)
        self.assertEqual(refresh_count[0], 2)

    def test_cached_component_pose_uses_current_mesh_transform_and_expires(self):
        actor = 0x12340
        mesh = 0x56780
        old_root = esp.decode_ftransform(pack_ftransform())
        current_root = esp.decode_ftransform(pack_ftransform(
            translation=(100.0, 0.0, 0.0)))
        pose = esp.SkeletonPose(
            "test", ((999.0, 999.0, 999.0),), (),
            ((1.0, 2.0, 3.0),), mesh)

        class FakeESP:
            _world_epoch = 7

            def __init__(self):
                self.mesh_refreshes = []

            def _character_mesh(self, found_actor, refresh=False):
                self.mesh_refreshes.append(refresh)
                return mesh

            @staticmethod
            def _component_world_transform_snapshot(found_mesh):
                raw = pack_ftransform(translation=(100.0, 0.0, 0.0))
                return raw, esp.decode_ftransform(raw)

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay._ensure_skeleton_state()
        overlay._skeleton_cache_epoch = 7
        overlay._skeleton_pose_cache[actor] = esp.CachedSkeletonPose(
            9.9, pose, (0.0, 0.0, 0.0), old_root, 7)

        rebased = overlay._cached_skeleton_for_player(
            actor, (100.0, 0.0, 0.0), current_root, 10.0, 7)
        self.assertIsNotNone(rebased)
        for actual, expected in zip(
                rebased.world_points[0], (101.0, 2.0, 3.0)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(overlay.esp.mesh_refreshes, [True])

        self.assertIsNone(overlay._cached_skeleton_for_player(
            actor, (100.0, 0.0, 0.0), current_root, 10.2, 7))

    def test_slow_pose_reprojects_component_points_at_current_mesh(self):
        actor = 0x12340

        class FakeESP:
            def __init__(self):
                self._world_epoch = 1
                self.root_x = 1000.0
                self._last_actor_transforms = {}
                self._character_component_cache = {}
                self._skeleton_failure_counts = {}

            @staticmethod
            def _character_mesh(found_actor, refresh=False):
                return found_actor

            def _component_world_transform_snapshot(self, found_mesh):
                raw = pack_ftransform(
                    translation=(self.root_x, 0.0, 0.0))
                return raw, esp.decode_ftransform(raw)

            def read_skeleton_pose(self, found_actor, position):
                # The pose was transformed using the old mesh position, then the
                # actor moved before the synchronous read returned.
                pose = esp.SkeletonPose(
                    "moving-test", ((1001.0, 0.0, 0.0),), (),
                    ((1.0, 0.0, 0.0),), found_actor)
                self.root_x = 1100.0
                return pose

        old_root = esp.decode_ftransform(pack_ftransform(
            translation=(1000.0, 0.0, 0.0)))
        player = esp.PlayerRenderSnapshot(
            False, (1000.0, 0.0, 0.0), 1, actor, "survivor",
            None, None, old_root)
        now = esp.time.monotonic()
        frame = esp.FrameRenderSnapshot(
            1, now, now, 0.0,
            {"loc": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0),
             "fov": 90.0},
            "hunter", (player,), (), (), None)

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(skeleton_esp=True)
        overlay._viewport_size = (1920, 1080)
        overlay._refresh_skeleton_cache(frame)

        rebased = overlay._cached_skeleton_for_player(
            actor, (1100.0, 0.0, 0.0), None,
            esp.time.monotonic(), 1)
        self.assertIsNotNone(rebased)
        self.assertAlmostEqual(rebased.world_points[0][0], 1101.0)

    def test_stale_frame_never_draws_player_geometry(self):
        class Painter:
            Antialiasing = 1

            def __init__(self, target):
                target._test_painter = self
                self.texts = []
                self.lines = []

            def setRenderHint(self, *args):
                pass

            def setFont(self, *args):
                pass

            def setPen(self, *args):
                pass

            def drawText(self, *args):
                self.texts.append(str(args[-1]))

            def drawLine(self, *args):
                self.lines.append(args)

        class PaintTarget:
            STALE_AFTER_SECONDS = esp.Overlay.STALE_AFTER_SECONDS

            @staticmethod
            def width():
                return 1920

            @staticmethod
            def height():
                return 1080

        now = esp.time.monotonic()
        pose = esp.SkeletonPose(
            "test", ((1000.0, 0.0, 0.0), (1000.0, 0.0, 80.0)),
            ((0, 1),))
        player = esp.PlayerRenderSnapshot(
            False, (1000.0, 0.0, 0.0), 1, 0x12340, "hunter",
            esp.CapsuleGeometry((1000.0, 0.0, 0.0), 90.0, 40.0), pose)
        stale_cases = (
            # Collection-duration guard, with a newly published frame.
            (now - 1.906, now, 1906.0),
            # Finished-frame age guard, with a quick collection.
            (now - 1.114, now - 1.109, 5.0),
            # Neither term alone exceeds 250 ms, but the oldest data does.
            (now - 0.300, now - 0.100, 200.0),
        )
        for started_at, finished_at, collection_ms in stale_cases:
            with self.subTest(
                    started_at=started_at, finished_at=finished_at,
                    collection_ms=collection_ms):
                frame = esp.FrameRenderSnapshot(
                    1, started_at, finished_at, collection_ms,
                    {"loc": (0.0, 0.0, 0.0),
                     "rot": (0.0, 0.0, 0.0), "fov": 90.0},
                    "survivor", (player,),
                    (("camera_ms", 6.0), ("players_ms", 1880.0),
                     ("capsule_ms", 20.0),
                     ("skeleton_refresh_ms", 0.0)),
                    (), None)
                target = PaintTarget()
                target.config = esp.Config(show_debug=True)
                target._snapshots = esp.LatestSnapshotStore(frame)
                with mock.patch.multiple(
                        esp, QPainter=Painter, QPen=lambda *args: None,
                        QColor=lambda *args: None, QFont=lambda *args: None):
                    esp.Overlay.paintEvent(target, None)

                self.assertTrue(any(
                    "ESP DATA STALE" in text
                    for text in target._test_painter.texts))
                self.assertTrue(any(
                    "STALE T:C6.0 P1880.0 B20.0 S0.0ms" in text
                    for text in target._test_painter.texts))
                self.assertEqual(target._test_painter.lines, [])

    def test_pose_ttl_is_rechecked_at_paint_time(self):
        class Metrics:
            @staticmethod
            def horizontalAdvance(text):
                return len(text) * 8

            @staticmethod
            def ascent():
                return 10

            @staticmethod
            def descent():
                return 3

        class Painter:
            Antialiasing = 1

            def __init__(self, target):
                target._test_painter = self
                self.lines = []

            def setRenderHint(self, *args):
                pass

            def setFont(self, *args):
                pass

            def setPen(self, *args):
                pass

            def setBrush(self, *args):
                pass

            def drawText(self, *args):
                pass

            def drawLine(self, *args):
                self.lines.append(args)

            def drawEllipse(self, *args):
                pass

            @staticmethod
            def fontMetrics():
                return Metrics()

        class PaintTarget:
            STALE_AFTER_SECONDS = esp.Overlay.STALE_AFTER_SECONDS
            SKELETON_CACHE_TTL_SECONDS = \
                esp.Overlay.SKELETON_CACHE_TTL_SECONDS
            _project_box = esp.Overlay._project_box
            _draw_box = esp.Overlay._draw_box
            _draw_skeleton = esp.Overlay._draw_skeleton

            @staticmethod
            def width():
                return 1920

            @staticmethod
            def height():
                return 1080

        now = esp.time.monotonic()
        pose = esp.SkeletonPose(
            "test", ((1000.0, 0.0, 0.0), (1000.0, 0.0, 80.0)),
            ((0, 1),))
        config = esp.Config(
            box_esp=False, skeleton_esp=True, snap_lines=False,
            show_names=False, show_distance=False)
        for pose_age, expected_lines in ((0.100, 1), (0.300, 0)):
            with self.subTest(pose_age=pose_age):
                player = esp.PlayerRenderSnapshot(
                    False, (1000.0, 0.0, 0.0), 1, 0x12340,
                    "hunter", None, pose, None, now - pose_age)
                frame = esp.FrameRenderSnapshot(
                    1, now, now, 0.0,
                    {"loc": (0.0, 0.0, 0.0),
                     "rot": (0.0, 0.0, 0.0), "fov": 90.0},
                    "survivor", (player,), (("local_pawn", True),),
                    (), None)
                target = PaintTarget()
                target.config = config
                target._snapshots = esp.LatestSnapshotStore(frame)
                target._layout_warnings = ()
                with mock.patch.multiple(
                        esp, QPainter=Painter, QPen=lambda *args: None,
                        QColor=lambda *args: None, QFont=lambda *args: None):
                    esp.Overlay.paintEvent(target, None)
                self.assertEqual(
                    len(target._test_painter.lines), expected_lines)

    def test_pose_frame_repaints_during_slow_reader_gap(self):
        now = 500.0
        pose = esp.SkeletonPose(
            "test", ((1000.0, 0.0, 0.0),), ())
        player = esp.PlayerRenderSnapshot(
            False, (1000.0, 0.0, 0.0), 1, 0x12340, "hunter",
            None, pose, None, now - 0.24)
        frame = esp.FrameRenderSnapshot(
            1, now - 0.01, now - 0.005, 5.0,
            {"loc": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0),
             "fov": 90.0},
            "survivor", (player,), (), (), None)
        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay._last_window_sync = now
        overlay._last_repaint = now - 0.020
        overlay._last_rendered_sequence = frame.sequence
        overlay._last_rendered_frame = frame
        overlay._snapshots = esp.LatestSnapshotStore(frame)
        updates = []
        overlay.update = lambda: updates.append(True)

        with mock.patch.object(esp.time, "monotonic", return_value=now):
            overlay.update_overlay()
        self.assertEqual(updates, [True])

    def test_world_change_during_collection_discards_mixed_frame(self):
        actor = 0x12340

        class FakeESP:
            def __init__(self):
                self._runtime_context_identity = (0x10000, 0x20000)
                self._world_epoch = 1
                self.camera_reads = 0
                self._last_actor_roles = {actor: "survivor"}
                self._last_local_role = "hunter"
                self._last_iter_stats = {"local_pawn": True, "rendered": 1}
                self._last_actor_transforms = {}
                self._skeleton_failure_counts = {}

            def get_camera(self):
                self.camera_reads += 1
                if self.camera_reads == 2:
                    self._runtime_context_identity = (0x30000, 0x40000)
                    self._world_epoch += 1
                return {"loc": (0.0, 0.0, 0.0),
                        "rot": (0.0, 0.0, 0.0), "fov": 90.0}

            @staticmethod
            def iter_players(**kwargs):
                yield False, (1000.0, 0.0, 0.0), 1, actor

            @staticmethod
            def character_role(found_actor):
                return "survivor"

        overlay = esp.Overlay.__new__(esp.Overlay)
        overlay.esp = FakeESP()
        overlay.config = esp.Config(box_esp=False, skeleton_esp=False)
        overlay._snapshot_sequence = 0
        overlay._viewport_size = (1920, 1080)
        frame = overlay._collect_snapshot()
        self.assertIsNone(frame.error)
        self.assertIsNone(frame.camera)
        self.assertEqual(frame.players, ())

class RemovedTargetingFeatureTests(unittest.TestCase):
    def test_process_access_is_read_only(self):
        source = inspect.getsource(esp)
        tree = ast.parse(source)
        blocked_calls = {
            "write" + "_bytes",
            "write" + "_float",
            "write" + "_double",
            "write" + "_int",
        }
        found = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in blocked_calls
        }
        self.assertEqual(found, set())

    def test_targeting_configuration_and_methods_are_absent(self):
        removed_word = "aim" + "bot"
        self.assertNotIn(removed_word, inspect.getsource(esp).casefold())
        self.assertFalse(any(
            name.casefold().startswith("aim")
            for name in esp.Config.__dataclass_fields__))
        self.assertFalse(any(
            name.casefold().startswith("_aim")
            for name in dir(esp.Overlay)))
        self.assertNotIn(
            "AController::" + "Control" + "Rotation", esp.MecchaESP.OFFSET_MAP)
        self.assertFalse(hasattr(esp, "w" + "float"))


class MenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = esp.QApplication.instance() or esp.QApplication([])

    def test_menu_renders_and_binds_every_setting(self):
        config = esp.Config(
            enabled=False,
            box_esp=False,
            skeleton_esp=False,
            show_local=False,
            show_names=False,
            show_distance=False,
            snap_lines=False,
            show_debug=True,
            box_style="2D",
            box_width_ratio=0.60,
            box_height_world=125.0,
            box_y_offset=7,
            box_line_width=3,
            skeleton_line_width=4,
            box_corner_fraction=0.30,
        )
        menu = esp.Menu(config)
        self.addCleanup(menu.close)
        menu.show()
        self.app.processEvents()

        self.assertEqual((menu.width(), menu.height()), menu.EXPANDED_SIZE)
        self.assertFalse(menu.cb_enabled.isChecked())
        self.assertEqual(menu.status_badge.text(), "●  OVERLAY PAUSED")
        self.assertEqual(menu.cmb_box_style.currentText(), "2D")
        self.assertAlmostEqual(menu.spn_box_width.value(), 0.60)
        self.assertEqual(menu.spn_height.value(), 125)
        self.assertEqual(menu.spn_yoff.value(), 7)
        self.assertEqual(menu.spn_box_line.value(), 3)
        self.assertEqual(menu.spn_skeleton_line.value(), 4)
        self.assertAlmostEqual(menu.spn_corner.value(), 0.30)

        menu.cb_enabled.setChecked(True)
        menu.cb_box.setChecked(True)
        menu.cb_skeleton.setChecked(True)
        menu.cb_local.setChecked(True)
        menu.cb_names.setChecked(True)
        menu.cb_dist.setChecked(True)
        menu.cb_snap.setChecked(True)
        menu.cb_debug.setChecked(False)
        menu.cmb_box_style.setCurrentText("Corner")
        menu.spn_box_width.setValue(0.75)
        menu.spn_height.setValue(140)
        menu.spn_yoff.setValue(-3)
        menu.spn_box_line.setValue(5)
        menu.spn_skeleton_line.setValue(6)
        menu.spn_corner.setValue(0.40)
        self.app.processEvents()

        self.assertTrue(config.enabled)
        self.assertTrue(config.box_esp)
        self.assertTrue(config.skeleton_esp)
        self.assertTrue(config.show_local)
        self.assertTrue(config.show_names)
        self.assertTrue(config.show_distance)
        self.assertTrue(config.snap_lines)
        self.assertFalse(config.show_debug)
        self.assertEqual(config.box_style, "Corner")
        self.assertAlmostEqual(config.box_width_ratio, 0.75)
        self.assertEqual(config.box_height_world, 140.0)
        self.assertEqual(config.box_y_offset, -3)
        self.assertEqual(config.box_line_width, 5)
        self.assertEqual(config.skeleton_line_width, 6)
        self.assertAlmostEqual(config.box_corner_fraction, 0.40)
        self.assertEqual(menu.status_badge.text(), "●  OVERLAY ON")
        self.assertFalse(menu.grab().isNull())

    def test_palette_preview_collapse_and_removed_controls(self):
        config = esp.Config()
        menu = esp.Menu(config)
        self.addCleanup(menu.close)
        menu.show()
        self.app.processEvents()

        with mock.patch.object(
                esp.QColorDialog, "getColor",
                return_value=esp.QColor(12, 34, 56)):
            menu.btn_enemy_color.click()
        self.assertEqual(config.enemy_color, (12, 34, 56))
        self.assertIn("#0C2238", menu.btn_enemy_color.text())

        menu.btn_collapse.click()
        self.app.processEvents()
        self.assertEqual((menu.width(), menu.height()), menu.COMPACT_SIZE)
        self.assertFalse(menu.body.isVisible())
        menu.btn_collapse.click()
        self.app.processEvents()
        self.assertEqual((menu.width(), menu.height()), menu.EXPANDED_SIZE)
        self.assertTrue(menu.body.isVisible())

        removed_word = "aim" + "bot"
        visible_text = " ".join(
            widget.text()
            for widget in menu.findChildren(esp.QWidget)
            if callable(getattr(widget, "text", None)))
        self.assertNotIn(removed_word, visible_text.casefold())
        old_controls = (
            "cb_" + removed_word,
            "cb_" + "aim" + "_fov",
            "lbl_" + "aim" + "_key",
            "btn_record_" + "key",
            "spn_" + "aim" + "_fov",
            "spn_" + "aim" + "_smooth",
            "spn_" + "aim" + "_off",
        )
        for attr in old_controls:
            self.assertFalse(hasattr(menu, attr))


class PlayerFilterTests(unittest.TestCase):
    def _reader_for_cleon_role(self, local_role, dead_in_hunter_roster=False):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {
            "UWorld::GameState": 0x10,
            "AController::PlayerState": 0x20,
            "APlayerController::AcknowledgedPawn": 0x28,
            "APlayerState::PawnPrivate": 0x30,
            "AGameStateBase::PlayerArray": 0x40,
        }

        world, gamestate, controller = 0x10000, 0x20000, 0x30000
        local_ps, hunter_ps = 0x40000, 0x41000
        survivor_ps, dead_ps = 0x42000, 0x43000
        local_pawn, hunter_pawn = 0x50000, 0x51000
        survivor_pawn, dead_pawn = 0x52000, 0x53000
        player_array = 0x60000

        memory.put(world + 0x10, struct.pack("<Q", gamestate))
        memory.put(controller + 0x20, struct.pack("<Q", local_ps))
        memory.put(controller + 0x28, struct.pack("<Q", local_pawn))
        playerstates = (local_ps, hunter_ps, survivor_ps, dead_ps)
        memory.put(gamestate + 0x40,
                   struct.pack("<Qii", player_array, len(playerstates),
                               len(playerstates)))
        memory.put(player_array, struct.pack("<4Q", *playerstates))
        for playerstate, pawn in zip(
                playerstates,
                (local_pawn, hunter_pawn, survivor_pawn, dead_pawn)):
            memory.put(playerstate + 0x30, struct.pack("<Q", pawn))

        if local_role == "hunter":
            hunters = frozenset((local_ps, hunter_ps))
            survivors = frozenset((survivor_ps,))
        else:
            hunters = frozenset((hunter_ps,))
            survivors = frozenset((local_ps, survivor_ps))
        if dead_in_hunter_roster:
            hunters = hunters | frozenset((dead_ps,))

        pawns = {local_pawn, hunter_pawn, survivor_pawn, dead_pawn}
        roles = {
            local_pawn: local_role,
            hunter_pawn: "hunter",
            survivor_pawn: "survivor",
            dead_pawn: "hunter" if dead_in_hunter_roster else "survivor",
        }
        positions = {
            local_pawn: (50.0, 5.0, 5.0),
            hunter_pawn: (100.0, 10.0, 10.0),
            survivor_pawn: (200.0, 20.0, 20.0),
            dead_pawn: (300.0, 30.0, 30.0),
        }
        reader._get_world = lambda: world
        reader._get_local_controller = lambda found_world: controller
        reader._cleon_live_rosters = lambda found_gamestate: (
            hunters, survivors, 1)
        reader.character_role = lambda pawn: roles.get(pawn)
        reader.character_dead_state = (
            lambda pawn, assume_character=False: pawn == dead_pawn)
        reader._object_is_a = lambda obj, class_name: (
            (class_name == "BP_GameState_cLeon_C" and obj == gamestate)
            or (class_name == "BP_FirstPersonCharacter_Main_C" and obj in pawns))
        reader._actor_position = lambda pawn: positions.get(pawn)
        reader._test_local_pawn = local_pawn
        return reader, hunter_pawn, survivor_pawn, dead_pawn

    def test_survivor_sees_live_hunter_role_and_not_dead_player(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role("survivor")
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {hunter_pawn, survivor_pawn})
        self.assertNotIn(dead_pawn, actors)
        self.assertEqual(reader._last_actor_roles[hunter_pawn], "hunter")
        self.assertEqual(reader._last_iter_stats["dead_filtered"], 1)

    def test_survivor_hides_dead_hunter_still_present_in_hunter_roster(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role(
                "survivor", dead_in_hunter_roster=True)
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {hunter_pawn, survivor_pawn})
        self.assertNotIn(dead_pawn, actors)
        self.assertEqual(reader._last_iter_stats["dead_filtered"], 1)

    def test_unreadable_dead_state_fails_closed_before_geometry(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role("survivor")
        reader.character_dead_state = lambda pawn, assume_character=False: (
            None if pawn == hunter_pawn else pawn == dead_pawn)
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {survivor_pawn})
        self.assertEqual(reader._last_iter_stats["state_unreadable"], 1)

    def test_live_survivor_roster_does_not_override_dead_flag(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role("survivor")
        reader.character_dead_state = lambda pawn, assume_character=False: pawn in {
            survivor_pawn, dead_pawn}
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {hunter_pawn})
        self.assertEqual(reader._last_iter_stats["dead_filtered"], 2)

    def test_dead_or_unreadable_local_player_is_not_emitted(self):
        live_reader, _, _, _ = self._reader_for_cleon_role("survivor")
        live_rows = list(live_reader.iter_players(
            include_local=True, include_actor=True))
        self.assertIn(live_reader._test_local_pawn,
                      {row[3] for row in live_rows})
        for local_state in (True, None):
            with self.subTest(local_state=local_state):
                reader, hunter_pawn, survivor_pawn, dead_pawn = \
                    self._reader_for_cleon_role("survivor")
                local_pawn = reader._test_local_pawn
                reader.character_dead_state = (
                    lambda pawn, assume_character=False, state=local_state:
                    state if pawn == local_pawn else pawn == dead_pawn)
                rows = list(reader.iter_players(
                    include_local=True, include_actor=True))
                actors = {row[3] for row in rows}
                self.assertNotIn(local_pawn, actors)
                self.assertEqual(actors, {hunter_pawn, survivor_pawn})

    def test_hunter_hides_other_hunters_and_dead_players(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role("hunter")
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {survivor_pawn})
        self.assertNotIn(hunter_pawn, actors)
        self.assertNotIn(dead_pawn, actors)
        self.assertEqual(reader._last_iter_stats["role_filtered"], 1)
        self.assertEqual(reader._last_iter_stats["dead_filtered"], 1)

    def test_unavailable_cleon_roster_falls_back_without_hiding_everyone(self):
        reader, hunter_pawn, survivor_pawn, dead_pawn = \
            self._reader_for_cleon_role("survivor")
        reader._cleon_live_rosters = lambda found_gamestate: None
        reader.character_dead_state = (
            lambda pawn, assume_character=False: pawn == dead_pawn)
        rows = list(reader.iter_players(include_actor=True))
        actors = {row[3] for row in rows}
        self.assertEqual(actors, {hunter_pawn, survivor_pawn})
        self.assertEqual(reader._last_iter_stats["roster_mode"], "fallback")

    def test_missing_cleon_roster_fields_are_not_resolved_every_frame(self):
        class Resolver:
            def __init__(self):
                self.calls = []

            def resolve(self, class_name, field_name):
                self.calls.append((class_name, field_name))
                return None

        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader._object_is_a = lambda obj, class_name: True
        reader._cleon_roster_offsets = None
        reader._last_cleon_roster_snapshot = None
        reader.resolver = Resolver()

        self.assertIsNone(reader._cleon_live_rosters(0x20000))
        self.assertEqual(len(reader.resolver.calls), 3)
        self.assertIs(reader._cleon_roster_offsets, False)
        self.assertEqual(reader._cleon_roster_source, "resolver-unavailable")

        self.assertIsNone(reader._cleon_live_rosters(0x20000))
        self.assertEqual(len(reader.resolver.calls), 3)

    def test_dead_flag_is_independent_stable_byte(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {"BP_FirstPersonCharacter_Main_C::Dead": 0x5AA}
        reader._object_is_a = lambda actor, class_name: True
        actor = 0x70000

        memory.put(actor + 0x5AA, b"\x00")
        self.assertFalse(reader.character_dead_state(actor))
        memory.put(actor + 0x5AA, b"\x01")
        self.assertTrue(reader.character_dead_state(actor))
        memory.put(actor + 0x5AA, b"\x02")
        self.assertIsNone(reader.character_dead_state(actor))

    def test_role_classifier_uses_cooked_class_family_names(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader._isa_cache = {}
        hunter_actor, survivor_actor = 0x80000, 0x81000
        hunter_class, survivor_class = 0x90000, 0x91000
        hunter_base, survivor_base = 0x92000, 0x93000
        memory.put(hunter_actor + esp.OFFSETS["UObjectBase::ClassPrivate"],
                   struct.pack("<Q", hunter_class))
        memory.put(survivor_actor + esp.OFFSETS["UObjectBase::ClassPrivate"],
                   struct.pack("<Q", survivor_class))
        memory.put(hunter_class + esp.OFFSETS["UStruct::SuperStruct"],
                   struct.pack("<Q", hunter_base))
        memory.put(survivor_class + esp.OFFSETS["UStruct::SuperStruct"],
                   struct.pack("<Q", survivor_base))
        memory.put(hunter_base + esp.OFFSETS["UStruct::SuperStruct"],
                   struct.pack("<Q", 0))
        memory.put(survivor_base + esp.OFFSETS["UStruct::SuperStruct"],
                   struct.pack("<Q", 0))

        class Objects:
            names = {
                hunter_class:
                    "BP_FirstPersonCharacter_cLeon_Character_Hunter_Default_C",
                survivor_class:
                    "BP_FirstPersonCharacter_cLeon_Character_Survivor_Default_C",
                hunter_base:
                    "BP_FirstPersonCharacter_cLeon_Character_Hunter_C",
                survivor_base:
                    "BP_FirstPersonCharacter_cLeon_Character_Survivor_C",
            }

            def _obj_name(self, obj):
                return self.names.get(obj, "")

        reader.objects = Objects()
        self.assertEqual(reader.character_role(hunter_actor), "hunter")
        self.assertEqual(reader.character_role(survivor_actor), "survivor")


class CapsuleReaderTests(unittest.TestCase):
    def test_capsule_uses_component_world_center_and_rejects_scaled_shape(self):
        memory = Memory()
        reader = esp.MecchaESP.__new__(esp.MecchaESP)
        reader.pm = memory
        reader.offsets = {
            "UCapsuleComponent::CapsuleHalfHeight": 0x540,
            "UCapsuleComponent::CapsuleRadius": 0x544,
        }
        capsule = 0x20000
        reader._character_capsule = lambda actor: capsule
        current_raw = [pack_ftransform(translation=(100.0, 210.0, 305.0))]
        reader._component_world_transform_snapshot = lambda component: (
            current_raw[0], esp.decode_ftransform(current_raw[0]))
        memory.put(capsule + 0x540, struct.pack("<f", 96.0))
        memory.put(capsule + 0x544, struct.pack("<f", 42.0))

        center, half_height, radius = reader.capsule_bounds(0x30000)
        self.assertAlmostEqual(center[0], 100.0)
        self.assertAlmostEqual(center[1], 210.0)
        self.assertAlmostEqual(center[2], 305.0)
        self.assertEqual((half_height, radius), (96.0, 42.0))

        current_raw[0] = pack_ftransform(
            translation=(100.0, 210.0, 305.0), scale=(1.005, 1.0, 1.0))
        self.assertIsNone(reader.capsule_bounds(0x30000))
        self.assertIsNotNone(reader.capsule_center(0x30000))


if __name__ == "__main__":
    unittest.main()
