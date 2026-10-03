import os
import re
import struct
import unittest
import zipfile
from pathlib import Path

try:
    from mecha_detector_v9 import Rule, EXPECTED, make_common_event
except ImportError:  # detector module is not next to this file
    Rule = EXPECTED = make_common_event = None


@unittest.skipIf(Rule is None, 'mecha_detector_v9 not importable')
class Rules(unittest.TestCase):
    def test_normal_hiding(self):
        r=Rule()
        for fill in [0,1,2,3,4,5,0]:
            self.assertFalse(r.evaluate('pawn',dict(SearchRadius=150.,InteractLength=250.,Angle=20.,AngleBias=2.,FilledValue=fill,PreStencil=int(fill==5))))

    def test_three_samples_and_no_spam(self):
        r=Rule()
        self.assertEqual([r.evaluate('pawn',EXPECTED) for _ in range(5)],[False,False,True,False,False])

    def test_respawn_and_missing_values(self):
        r=Rule()
        r.evaluate('a',EXPECTED);r.evaluate('a',EXPECTED)
        self.assertFalse(r.evaluate('b',EXPECTED))
        self.assertFalse(r.evaluate('b',{}))
        self.assertEqual(r.count,0)

    def test_nonfinite_and_partial_match(self):
        r=Rule()
        for bad in [float('nan'),float('inf'),150.,None]:
            values=dict(EXPECTED,SearchRadius=bad)
            self.assertFalse(r.evaluate('a',values))

    def test_rearm_and_read_failure_reset(self):
        r=Rule()
        for _ in range(3):r.evaluate('a',EXPECTED)
        r.evaluate('a',{})
        self.assertEqual([r.evaluate('a',EXPECTED) for _ in range(3)],[False,False,True])
        r.reset()
        self.assertFalse(r.evaluate('a',EXPECTED))

    def test_common_event_schema_and_scores(self):
        normal = make_common_event('normal_001', 'player_042', 'hide_anywhere', 1000,
                                   dict(EXPECTED, SearchRadius=150.), False, False,
                                   rule=Rule(), identity='a')
        self.assertEqual(list(normal), ['session_id', 'player_id', 'module', 'timestamp_ms',
                                       'evidence', 'reasons', 'raw_score'])
        self.assertEqual(normal['raw_score'], 0)
        rule = Rule()
        events = [make_common_event('hide_anywhere_002', 'player_042', 'hide_anywhere',
                                   52781 + i, EXPECTED, True, True, rule=rule, identity='a')
                  for i in range(4)]
        self.assertEqual([e['raw_score'] for e in events], [2, 2, 3, 3])
        suspicious = events[2]
        self.assertEqual(suspicious['raw_score'], 3)
        self.assertEqual(suspicious['evidence']['hide_value_confirmed'], 1)
        self.assertEqual(normal['evidence']['status'], 'NORMAL')

    def test_missing_resets_confirmation_and_is_not_normal(self):
        rule = Rule()
        def event(values, identity='a'):
            return make_common_event('test', 'p', 'hide_anywhere', 1000, values,
                                     False, False, rule=rule, identity=identity)
        event(EXPECTED); event(EXPECTED)
        missing = event({})
        self.assertEqual(missing['evidence']['status'], 'ERROR')
        self.assertIsNone(missing['evidence']['hide_value_pattern'])
        self.assertEqual(event(EXPECTED)['raw_score'], 0)
        event(EXPECTED)
        self.assertEqual(event(EXPECTED)['raw_score'], 3)
        self.assertEqual(event(EXPECTED, 'b')['raw_score'], 0)


# ---------------------------------------------------------------------------
# Logger build checks: mecha_logger.py constants vs. the 5.6.1-0+UE5-Chameleon
# SDK dump and the matching PenguinHotel-Win64-Shipping.exe.
# Files are looked up next to this file, or via MECHA_SDK_ZIP / MECHA_GAME_EXE.
# Tests that need a missing file are skipped, not failed.
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
SDK_ZIP = Path(os.environ.get('MECHA_SDK_ZIP', HERE / '5_6_1-0_UE5-Chameleon.zip'))
GAME_EXE = Path(os.environ.get('MECHA_GAME_EXE', HERE / 'PenguinHotel-Win64-Shipping.exe'))
MECCHA_DLL = Path(os.environ.get('MECHA_DLL', HERE / 'meccha.dll'))

SDK_TYPES = {'<d': 'double', '<B': 'bool', '<i': 'int32'}
# class -> SDK header holding it
SDK_FILES = {
    'ABP_FirstPersonCharacter_Main_C': 'BP_FirstPersonCharacter_Main_classes.hpp',
    'UBPC_NearInteract_C': 'BPC_NearInteract_classes.hpp',
    'ABP_FirstPersonCharacter_cLeon_Character_Survivor_C':
        'BP_FirstPersonCharacter_cLeon_Character_Survivor_classes.hpp',
    'ABP_Camera_Base_C': 'BP_Camera_Base_classes.hpp',
    'UObject': 'CoreUObject_classes.hpp', 'UStruct': 'CoreUObject_classes.hpp',
    'UWorld': 'Engine_classes.hpp', 'UGameInstance': 'Engine_classes.hpp',
    'UPlayer': 'Engine_classes.hpp', 'ULocalPlayer': 'Engine_classes.hpp',
    'APlayerController': 'Engine_classes.hpp',
}
# Engine chain offsets that are literals in AutoObserver.context()/ancestry()/poll_viewport().
ENGINE_CHAIN = [
    ('UObject', 'Index', 0xC), ('UObject', 'Class', 0x10), ('UObject', 'Name', 0x18),
    ('UStruct', 'SuperStruct', 0x40), ('UWorld', 'OwningGameInstance', 0x228),
    ('UGameInstance', 'LocalPlayers', 0x38), ('UPlayer', 'PlayerController', 0x30),
    ('ULocalPlayer', 'ViewportClient', 0x78), ('APlayerController', 'AcknowledgedPawn', 0x350),
]


def sdk_text(name):
    with zipfile.ZipFile(SDK_ZIP) as z:
        return z.read('CppSDK/SDK/' + name).decode('utf-8', 'replace')


def sdk_field(cls, field):
    """(type, offset, size) of `field` inside `cls` from the dump, or None."""
    text = sdk_text(SDK_FILES[cls]).replace('\r', '')
    m = re.search(r'\bclass (?:alignas\(\w+\) )?' + cls + r'\b[^\n]*\n\{(.*?)\n\};', text, re.S)
    if not m:
        return None
    row = re.search(r'^\s*(?:class |struct )?([\w:<>* ]+?)\s+' + field + r'\s*;\s*//\s*0x([0-9A-Fa-f]+)\(0x([0-9A-Fa-f]+)\)',
                    m.group(1), re.M)
    return (row.group(1).strip(), int(row.group(2), 16), int(row.group(3), 16)) if row else None


@unittest.skipUnless(SDK_ZIP.exists(), 'SDK dump zip not found')
class LoggerBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mecha_logger
        cls.m = mecha_logger
        cls.A = mecha_logger.AutoObserver

    def test_rvas_match_sdk_offsets(self):
        text = sdk_text('Basic.hpp')
        for name, actual in [('GWorld', self.A.GWORLD), ('AppendString', self.A.APPEND_STRING)]:
            sdk = int(re.search(name + r'\s*=\s*0x([0-9A-Fa-f]+)', text).group(1), 16)
            self.assertEqual(actual, sdk, name)
        # The logger deliberately does NOT use the dump's GNames (fallback only).
        gnames = int(re.search(r'GNames\s*=\s*0x([0-9A-Fa-f]+)', text).group(1), 16)
        self.assertNotEqual(self.A.GNAMES, gnames)

    def test_field_tables_match_sdk(self):
        tables = [('ABP_FirstPersonCharacter_Main_C', self.m.PAWN_FIELDS),
                  ('UBPC_NearInteract_C', self.m.NEAR_FIELDS),
                  ('ABP_FirstPersonCharacter_cLeon_Character_Survivor_C', self.m.SURVIVOR_FIELDS),
                  ('ABP_Camera_Base_C', self.m.CAMERA_FIELDS)]
        for cls, table in tables:
            for name, offset, fmt in table:
                with self.subTest(cls=cls, field=name):
                    found = sdk_field(cls, name.removeprefix('Camera.'))
                    self.assertIsNotNone(found, 'not in SDK')
                    self.assertEqual((found[1], found[2]), (offset, struct.calcsize(fmt)))
                    self.assertEqual(found[0], SDK_TYPES[fmt])
        found = sdk_field('ABP_FirstPersonCharacter_Main_C', 'BPC_NearInteract')
        self.assertEqual(found[1], self.m.NEAR_INTERACT_PTR_OFFSET)
        found = sdk_field('ABP_FirstPersonCharacter_Main_C', 'HaveActor_R')
        self.assertEqual(found[1], self.m.HELD_ACTOR_OFFSET)

    def test_engine_chain_offsets(self):
        for cls, field, offset in ENGINE_CHAIN:
            with self.subTest(cls=cls, field=field):
                found = sdk_field(cls, field)
                self.assertIsNotNone(found, 'not in SDK')
                self.assertEqual(found[1], offset)

    def test_class_names_exist_in_sdk(self):
        objects = SDK_ZIP and zipfile.ZipFile(SDK_ZIP).read('GObjects-Dump.txt').decode('utf-8', 'replace')
        for name in (self.A.MAIN, self.A.SURVIVOR, 'BPC_NearInteract_C'):
            self.assertIn(name, objects)


class FakeTarget:
    path = r'C:\Game\PenguinHotel-Win64-Shipping.exe'
    BASE = 0x140000000

    MECCHA_BASE = 0x7FF812340000

    def modules(self):
        rows = [dict(name='PenguinHotel-Win64-Shipping.exe', path=self.path, base=hex(self.BASE), size=0xA3FA000),
                dict(name='meccha.dll', path=r'C:\Users\x\meccha.dll', base=hex(self.MECCHA_BASE), size=0xC0000)]
        return {(r['path'].casefold(), r['base'], r['size']): r for r in rows}


def observer(regions):
    """AutoObserver over fake memory: regions = {address: bytes}."""
    import mecha_logger
    obs = mecha_logger.AutoObserver.__new__(mecha_logger.AutoObserver)
    obs.target, obs.base, obs.events, obs.log = FakeTarget(), None, [], []
    obs.values, obs.module_cache = {}, []
    obs.identity = obs.name_mode = obs.last_status = obs.viewport_identity = None
    obs.last_chain = obs.held_camera = obs.slot_state = None

    def emit(kind, **data):
        obs.events.append(kind)
        obs.log.append((kind, data))
    obs.emit = emit

    def read(address, size):
        for start, data in regions.items():
            if start <= address and address + size <= start + len(data):
                return data[address - start:address - start + size]
        raise OSError(f'unmapped {address:#x}')
    obs.read = read
    return obs


class LoggerFailClosed(unittest.TestCase):
    def ref(self, disp):
        import mecha_logger
        return {FakeTarget.BASE + mecha_logger.AutoObserver.POOL_REF: b'\x4c\x8d\x05' + struct.pack('<i', disp) + b'\0'}

    def test_reference_resolving_to_expected_pool_is_accepted(self):
        import mecha_logger
        A = mecha_logger.AutoObserver
        obs = observer(self.ref(A.GNAMES - (A.POOL_REF + 7)))
        self.assertEqual(obs.main_base(), FakeTarget.BASE)
        self.assertEqual(obs.events, ['dump_profile'])

    def test_previous_build_reference_is_rejected(self):
        # Previous build: disp32 0x08424F69 -> NamePool RVA 0x097B7400.
        obs = observer(self.ref(0x08424F69))
        with self.assertRaisesRegex(ValueError, r'resolves NamePool RVA 0x97b7400'):
            obs.main_base()
        self.assertIsNone(obs.base)

    def test_wrong_instruction_is_rejected(self):
        import mecha_logger
        obs = observer({FakeTarget.BASE + mecha_logger.AutoObserver.POOL_REF: b'\x90' * 8})
        with self.assertRaisesRegex(ValueError, 'differs from analyzed EXE'):
            obs.main_base()

    @unittest.skipUnless(GAME_EXE.exists(), 'game EXE not found')
    def test_real_exe_reference(self):
        """The reference bytes in the actual EXE resolve to the logger's NamePool RVA."""
        import mecha_logger
        A = mecha_logger.AutoObserver
        with GAME_EXE.open('rb') as f:
            head = f.read(4096)
            pe = struct.unpack_from('<I', head, 60)[0]
            n_sec, opt = struct.unpack_from('<H', head, pe + 6)[0], struct.unpack_from('<H', head, pe + 20)[0]
            sec = pe + 24 + opt
            for i in range(n_sec):
                vsize, va, rsize, raw = struct.unpack_from('<IIII', head, sec + i * 40 + 8)
                if va <= A.POOL_REF < va + max(vsize, rsize):
                    f.seek(raw + A.POOL_REF - va)
                    code = f.read(7)
                    break
            else:
                self.fail('POOL_REF not inside any section')
        self.assertEqual(code[:3], b'\x4c\x8d\x05')
        self.assertEqual(A.POOL_REF + 7 + struct.unpack('<i', code[3:])[0], A.GNAMES)


class LoggerNameDecode(unittest.TestCase):
    """decode_name() against a synthetic pool laid out like the EXE's FNamePool:
    Blocks[] at pool+0x10, current block/cursor at pool+8, stride 2, header >> 6 = length."""
    def test_decode(self):
        import mecha_logger
        A = mecha_logger.AutoObserver
        pool, block = FakeTarget.BASE + A.GNAMES, 0x200000000
        entries = b''.join(struct.pack('<H', len(n) << 6) + n.encode() for n in ('None', 'Pawn'))
        entries += struct.pack('<H', (4 << 6) | 1) + 'Wide'.encode('utf-16le')
        image = FakeTarget.BASE + A.POOL_REF  # main_base() needs a valid reference to run
        import struct as s
        regions = {
            image: b'\x4c\x8d\x05' + s.pack('<i', A.GNAMES - (A.POOL_REF + 7)) + b'\0',
            pool: b'\0' * 8 + s.pack('<II', 0, len(entries)) + s.pack('<Q', block),
            block: entries + b'\0' * 64,
        }
        obs = observer(regions)
        mode = (False, 6)
        self.assertEqual(obs.decode_name(0, mode), 'None')
        self.assertEqual(obs.decode_name(3, mode), 'Pawn')        # byte offset 6 -> index 3
        self.assertEqual(obs.decode_name(6, mode), 'Wide')        # byte offset 12, wide entry
        with self.assertRaises(ValueError):
            obs.decode_name(0x10000, mode)                        # block beyond current block


# ---------------------------------------------------------------------------
# Synthetic process: World -> GameInstance -> LocalPlayer -> Controller -> Pawn,
# laid out with the offsets of the 5.6.1 dump, so poll_data() runs end to end.
# ---------------------------------------------------------------------------
CLEAN = dict(InteractLength=250., IsTalkNow=1, EnableInteract=0, IsInViewCheckLate=0.,
             SearchRadius=150., Angle=20., AngleBias=2., IgnoreUpVector=0,
             FilledValue=3., PreStencil=1)
CLEAN_CAMERA = {'Camera.EnableDistance': 300., 'Camera.EnableDistanceGimmick': 300.,
                'Camera.Is_in_View_Check_Late': 0.}
# What meccha.dll's Hide_anywhere routine writes (RVA 0x60540; see MecchaDll below).
HIDE_ANYWHERE = dict(InteractLength=5000., IsTalkNow=0, EnableInteract=1, IsInViewCheckLate=1e9,
                     SearchRadius=5000., Angle=360., AngleBias=360., IgnoreUpVector=1,
                     FilledValue=0., PreStencil=0)
HIDE_ANYWHERE_CAMERA = {'Camera.EnableDistance': 5000., 'Camera.EnableDistanceGimmick': 5000.,
                        'Camera.Is_in_View_Check_Late': 1e9}


class Process:
    SURVIVOR = 'BP_FirstPersonCharacter_cLeon_Character_Survivor_C'
    NAMES = ['None', 'Object', 'Actor', 'Pawn', 'BP_FirstPersonCharacter_Main_C', SURVIVOR,
             'BPC_NearInteract_C', 'BP_Camera_Base_C', 'BP_ItemBase_C']

    def __init__(self, held='BP_Camera_Base_C', slot_target=None):
        import mecha_logger as L
        A, B = L.AutoObserver, FakeTarget.BASE
        self.L, entries, idx = L, b'', {}
        for n in self.NAMES:
            idx[n] = len(entries) // 2
            e = struct.pack('<H', len(n) << 6) + n.encode()
            entries += e + (b'\0' if len(e) % 2 else b'')
        W, GI, ARR, LP, PC, PAWN, NEAR, VP, VT, HELD = [0x300000000 + i * 0x10000 for i in range(10)]
        C = {n: 0x400000000 + i * 0x1000 for i, n in enumerate(self.NAMES)}

        def klass(name, parent):
            b = bytearray(0x50)
            struct.pack_into('<I', b, 0x18, idx[name]); struct.pack_into('<Q', b, 0x40, parent)
            return b
        R = {B + A.POOL_REF: b'\x4c\x8d\x05' + struct.pack('<i', A.GNAMES - (A.POOL_REF + 7)) + b'\0',
             B + A.GNAMES: b'\0' * 8 + struct.pack('<II', 0, len(entries)) + struct.pack('<Q', 0x200000000),
             0x200000000: entries + b'\0' * 64, B + A.GWORLD: struct.pack('<Q', W),
             C['Object']: klass('Object', 0), C['Actor']: klass('Actor', C['Object']),
             C['Pawn']: klass('Pawn', C['Actor']),
             C['BP_FirstPersonCharacter_Main_C']: klass('BP_FirstPersonCharacter_Main_C', C['Pawn']),
             C[self.SURVIVOR]: klass(self.SURVIVOR, C['BP_FirstPersonCharacter_Main_C']),
             C['BPC_NearInteract_C']: klass('BPC_NearInteract_C', C['Object']),
             C['BP_Camera_Base_C']: klass('BP_Camera_Base_C', C['Actor']),
             C['BP_ItemBase_C']: klass('BP_ItemBase_C', C['Actor'])}
        self.world, gi, self.pawn, self.near = bytearray(0x300), bytearray(0x60), bytearray(0x1000), bytearray(0x200)
        self.held, self.vt = bytearray(0x600), bytearray(0x800)
        struct.pack_into('<Q', self.world, 0x228, GI); struct.pack_into('<Qii', gi, 0x38, ARR, 1, 4)
        lp = bytearray(0x100); struct.pack_into('<Q', lp, 0x30, PC); struct.pack_into('<Q', lp, 0x78, VP)
        pc = bytearray(0x400); struct.pack_into('<Q', pc, 0x350, PAWN)
        struct.pack_into('<Q', self.pawn, 0x10, C[self.SURVIVOR]); struct.pack_into('<Q', self.pawn, 0x400, NEAR)
        struct.pack_into('<Q', self.near, 0x10, C['BPC_NearInteract_C'])
        if held:
            struct.pack_into('<Q', self.pawn, 0x508, HELD); struct.pack_into('<Q', self.held, 0x10, C[held])
        struct.pack_into('<Q', self.vt, 0x70 * 8, slot_target or B + 0x1234560)
        R.update({W: self.world, GI: gi, ARR: struct.pack('<Q', LP), LP: lp, PC: pc, PAWN: self.pawn,
                  NEAR: self.near, VP: struct.pack('<Q', VT), VT: self.vt, HELD: self.held})
        self.obs = observer(R)
        self.set_pawn(CLEAN); self.set_camera(CLEAN_CAMERA)

    def set_pawn(self, v):
        for name, off, fmt in self.L.PAWN_FIELDS + self.L.SURVIVOR_FIELDS:
            if name in v: struct.pack_into(fmt, self.pawn, off, v[name])
        for name, off, fmt in self.L.NEAR_FIELDS:
            if name in v: struct.pack_into(fmt, self.near, off, v[name])

    def set_camera(self, v):
        for name, off, fmt in self.L.CAMERA_FIELDS:
            if name in v: struct.pack_into(fmt, self.held, off, v[name])

    def poll(self):
        self.obs.log.clear()
        self.obs.poll_data()
        return self.obs.log

    def kinds(self, log, kind):
        return [d for k, d in log if k == kind]


class LoggerObservesHideAnywhere(unittest.TestCase):
    def test_failed_read_drops_prior_value_and_breaks_confirmation(self):
        p = Process(); p.set_pawn(HIDE_ANYWHERE)
        rule = Rule()
        def event():
            p.poll()
            return make_common_event('test', 'p', 'hide_anywhere', 1,
                                     p.obs.sample_values, False, False, rule=rule,
                                     identity=p.obs.sample_identity, errors=p.obs.sample_errors)
        event(); event()
        original = p.obs.read
        def failed(address, size):
            if address == 0x300060000 + 0xC0:
                raise OSError('simulated failure')
            return original(address, size)
        p.obs.read = failed
        result = event()
        self.assertNotIn('SearchRadius', p.obs.values)
        self.assertNotIn('SearchRadius', p.obs.sample_values)
        self.assertEqual(result['evidence']['status'], 'ERROR')
        self.assertEqual(rule.count, 0)
        p.obs.read = original
        self.assertEqual(event()['raw_score'], 0)

    def test_failed_viewport_cannot_reuse_old_hook(self):
        p = Process(slot_target=FakeTarget.MECCHA_BASE + 0x2750); p.poll()
        original = p.obs.read
        def failed(address, size):
            if address == 0x300080000 + 0x70 * 8:
                raise OSError('simulated failure')
            return original(address, size)
        p.obs.read = failed
        p.poll()
        self.assertIsNone(p.obs.slot_state)
        self.assertNotIn('Viewport.PostRender_candidate', p.obs.values)
    def test_clean_baseline_and_slot_in_main_image(self):
        p = Process()
        log = p.poll()
        base = {d['field']: d['value'] for d in p.kinds(log, 'data_baseline')}
        for name, value in {**CLEAN, **CLEAN_CAMERA}.items():
            self.assertEqual(base[name], value, name)
        owner, = p.kinds(log, 'viewport_slot_owner')
        self.assertFalse(owner['outside_main_image'])
        self.assertEqual(owner['module'], 'PenguinHotel-Win64-Shipping.exe')
        self.assertEqual(p.kinds(p.poll(), 'data_changed'), [])

    def test_pinned_values_are_logged_as_changes(self):
        p = Process(); p.poll()
        p.set_pawn(HIDE_ANYWHERE); p.set_camera(HIDE_ANYWHERE_CAMERA)
        changed = {d['field']: d['after'] for d in p.kinds(p.poll(), 'data_changed')}
        self.assertEqual(changed, {**HIDE_ANYWHERE, **HIDE_ANYWHERE_CAMERA})

    def test_slot_redirected_into_another_module_is_reported(self):
        p = Process(); p.poll()
        hook = FakeTarget.MECCHA_BASE + 0x2750
        struct.pack_into('<Q', p.vt, 0x70 * 8, hook)
        log = p.poll()
        owner, = p.kinds(log, 'viewport_slot_owner')
        self.assertEqual((owner['module'], owner['outside_main_image'], owner['target']),
                         ('meccha.dll', True, hex(hook)))
        self.assertEqual(p.kinds(log, 'data_changed')[0]['field'], 'Viewport.PostRender_candidate')

    def test_already_hooked_when_logger_attaches(self):
        p = Process(slot_target=FakeTarget.MECCHA_BASE + 0x2750)
        owner, = p.kinds(p.poll(), 'viewport_slot_owner')  # no earlier baseline needed
        self.assertTrue(owner['outside_main_image'])
        self.assertEqual(owner['module'], 'meccha.dll')

    def test_unmapped_slot_target(self):
        p = Process(slot_target=0x7FF900000000)
        owner, = p.kinds(p.poll(), 'viewport_slot_owner')
        self.assertEqual((owner['module'], owner['outside_main_image']), (None, True))

    def test_held_item_must_be_exact_camera_class(self):
        for held in ('BP_ItemBase_C', None):
            with self.subTest(held=held):
                p = Process(held=held)
                log = p.poll()
                self.assertFalse([d for d in p.kinds(log, 'data_baseline') if d['field'].startswith('Camera.')])
                self.assertEqual(p.kinds(log, 'held_camera_binding'), [])

    def test_camera_put_away_drops_baseline(self):
        p = Process(); p.poll()
        struct.pack_into('<Q', p.pawn, 0x508, 0)
        self.assertEqual(len(p.kinds(p.poll(), 'held_camera_binding')), 1)
        struct.pack_into('<Q', p.pawn, 0x508, 0x300000000 + 9 * 0x10000)  # same camera again
        again = p.poll()
        self.assertEqual({d['field'] for d in p.kinds(again, 'data_baseline')} & set(CLEAN_CAMERA), set(CLEAN_CAMERA))


class MecchaDll(unittest.TestCase):
    """Provenance of HIDE_ANYWHERE: static analysis of meccha.dll (never loaded or executed).
    Hide_anywhere routine at RVA 0x60540, called once per frame from its PostRender hook
    (vtable slot index read from .data RVA 0xB2000 = 0x70, patched with VirtualProtect)."""
    SHA256 = 'e3902fcbc1ae7a9ff11aa2906550e7f710d5cdb036bf9145ccf76dd5980e9eab'
    STORES = {  # instruction bytes -> (base object, offset)
        '4c89bf10050000': ('pawn', 0x510), '66c787750600000001': ('pawn', 0x675),   # word: 0x675=0, 0x676=1
        '4c89a768060000': ('pawn', 0x668), '48c787180d000000000000': ('pawn', 0xD18),
        'c787200d000000000000': ('pawn', 0xD20), '4c89b8c0000000': ('near', 0xC0),
        '488988c8000000': ('near', 0xC8), '488988f0000000': ('near', 0xF0),
        'c680e900000001': ('near', 0xE9), '4c89bd00050000': ('camera', 0x500),
        '4c89bd08050000': ('camera', 0x508), '4c89a540050000': ('camera', 0x540),
    }
    IMMEDIATES = {5000.0: '49bf000000000088b340', 1e9: '49bc0000000065cdcd41', 360.0: '48b90000000000807640'}

    @classmethod
    def setUpClass(cls):
        if not MECCHA_DLL.exists():
            raise unittest.SkipTest('meccha.dll not found')
        import hashlib
        cls.data = MECCHA_DLL.read_bytes()
        cls.same_build = hashlib.sha256(cls.data).hexdigest() == cls.SHA256

    def test_pinned_values_and_stores(self):
        if not self.same_build:
            self.skipTest('different meccha.dll build: re-derive HIDE_ANYWHERE from its Hide_anywhere routine')
        for value, code in self.IMMEDIATES.items():
            self.assertEqual(struct.unpack('<d', bytes.fromhex(code)[2:])[0], value)
            self.assertIn(bytes.fromhex(code), self.data)
        for code in self.STORES:
            self.assertIn(bytes.fromhex(code), self.data)

    def test_logger_observes_every_field_it_writes(self):
        import mecha_logger as L
        seen = {'pawn': {o for _, o, _ in L.PAWN_FIELDS + L.SURVIVOR_FIELDS} | {0x676},
                'near': {o for _, o, _ in L.NEAR_FIELDS},
                'camera': {o for _, o, _ in L.CAMERA_FIELDS}}
        for code, (obj, offset) in self.STORES.items():
            with self.subTest(obj=obj, offset=hex(offset)):
                self.assertIn(offset, seen[obj])
        # Also writes one byte at pawn+0xB00 (PreRotateVector.X low byte): a per-frame game
        # vector, deliberately not logged.

    @unittest.skipIf(Rule is None, 'mecha_detector_v9 not importable')
    def test_detector_expected_matches_dll(self):
        for key in ('SearchRadius', 'InteractLength', 'Angle', 'AngleBias'):
            self.assertIn(key, EXPECTED)
        for key, value in EXPECTED.items():
            if key in HIDE_ANYWHERE:
                self.assertEqual(value, HIDE_ANYWHERE[key], key)



if __name__=='__main__': unittest.main()
