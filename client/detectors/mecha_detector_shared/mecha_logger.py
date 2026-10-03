#!/usr/bin/env python3
"""Chameleon SDK read-only logger v7. Windows x64 / Python 3.10+.

Target build: SDK dump 5.6.1-0+UE5-Chameleon (Dumper-7, 2026-09-26) with the
matching PenguinHotel-Win64-Shipping.exe.

  python mecha_logger.py --pid 1234
  python mecha_logger.py --pid 1234 --code --sysmon --duration 120

Default: module changes + automatic local Pawn/data tracking + viewport
vtable slot 0x70 observations. --code adds main EXE .text scans (10 sec).
--sysmon reads existing configured Sysmon 7/8/10 events (5 sec).
--modules-only disables memory observation.

GWorld RVA 0x09613260 is from the SDK dump; the actual NamePool RVA
0x097B6400 is derived from the supplied EXE's FName::AppendString (RIP-relative
operand of the `lea r8` at RVA 0x01392490), not from the dump's GNames.
Both RVAs moved by -0x1000 versus the previous build; every SDK class field
offset used below was re-checked against the new dump and is unchanged.
Also observed (v7): the held item (Main_C::HaveActor_R, only when its class
ancestry contains exactly BP_Camera_Base_C) and, for the viewport vtable slot
0x70, which loaded module the slot points into (outside_main_image flag).
Slot 0x70 and the field set above were cross-checked by static analysis of the
supplied meccha.dll (Hide_anywhere): it overwrites ViewportClient vtable[0x70]
via VirtualProtect and rewrites these fields every frame. Nothing here executes,
loads or modifies that DLL.
Run against the matching game build. Unsupported name-pool layouts fail
closed with auto_status logs; no remote engine function is called.
FName decoding is runtime-probed, not guaranteed by the SDK (which uses
AppendString internally). Requires None and valid Pawn inheritance names.
Automatic binding follows World -> GameInstance -> LocalPlayers[0] ->
PlayerController -> AcknowledgedPawn. Main_C inheritance is required;
Survivor fields require exact Survivor ancestry. First observations are NOT
known-clean originals. No cheat verdicts, blocking, injection or writes.

Output: logs/<session_id>/manifest.json, events.jsonl and raw/mecha_log.jsonl.
The root events.jsonl contains only the shared common-event schema; the raw
collector records remain unchanged in raw/mecha_log.jsonl. Ctrl+C to stop.
v7 re-bases GWorld/NamePool for the new build and replaces the fixed byte
signature with a computed NamePool target check (diagnostic on mismatch).
Runtime class checks and bounded diagnostics remain enabled.
See README_ko.md for field coverage, gaps and validation limitations.
"""
import argparse
import base64
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

from mecha_detector_v9 import Rule, make_common_event
from server_bridge import ServerBridge


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def diff_modules(old, new):
    return ([new[k] for k in sorted(new.keys() - old.keys())],
            [old[k] for k in sorted(old.keys() - new.keys())])


class Windows:
    def __init__(self, pid):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        specs = {
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
            'CreateToolhelp32Snapshot': ([W.DWORD, W.DWORD], W.HANDLE),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        }
        for name, (args, ret) in specs.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, ret
        self.pid = pid
        self.handle = self.k.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not self.k.QueryFullProcessImageNameW(self.handle, 0, buf, C.byref(size)):
            err = C.get_last_error()
            self.close()
            raise C.WinError(err)
        self.path = buf.value

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def alive(self):
        result = self.k.WaitForSingleObject(self.handle, 0)
        if result == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        return result == 258

    def modules(self):
        class Entry(C.Structure):
            _fields_ = [('dwSize', W.DWORD), ('th32ModuleID', W.DWORD),
                        ('th32ProcessID', W.DWORD), ('GlblcntUsage', W.DWORD),
                        ('ProccntUsage', W.DWORD), ('modBaseAddr', C.c_void_p),
                        ('modBaseSize', W.DWORD), ('hModule', W.HMODULE),
                        ('szModule', W.WCHAR * 256), ('szExePath', W.WCHAR * 260)]
        for name in ('Module32FirstW', 'Module32NextW'):
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = [W.HANDLE, C.POINTER(Entry)], W.BOOL
        for _ in range(5):
            snap = self.k.CreateToolhelp32Snapshot(0x8 | 0x10, self.pid)
            if snap != C.c_void_p(-1).value:
                break
            err = C.get_last_error()
            if err != 24:  # ERROR_BAD_LENGTH: transient loader change
                raise C.WinError(err)
        else:
            raise C.WinError(err)
        try:
            entry = Entry()
            entry.dwSize = C.sizeof(entry)
            if not self.k.Module32FirstW(snap, C.byref(entry)):
                raise C.WinError(C.get_last_error())
            result = {}
            while True:
                row = dict(name=entry.szModule, path=entry.szExePath,
                           base=hex(entry.modBaseAddr or 0), size=entry.modBaseSize)
                result[(row['path'].casefold(), row['base'], row['size'])] = row
                if not self.k.Module32NextW(snap, C.byref(entry)):
                    err = C.get_last_error()
                    if err != 18:  # ERROR_NO_MORE_FILES
                        raise C.WinError(err)
                    return result
        finally:
            self.k.CloseHandle(snap)


def sysmon_query(pid, start, cursor):
    # Values interpolated below are validated integer / internally generated UTC.
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$log = 'Microsoft-Windows-Sysmon/Operational'
try {
  $null = Get-WinEvent -ListLog $log -ErrorAction Stop
  $q = "*[System[(EventID=7 or EventID=8 or EventID=10) and EventRecordID > CURSOR and TimeCreated[@SystemTime >= 'START']]] and *[EventData[Data[@Name='ProcessId']='PIDVALUE' or Data[@Name='TargetProcessId']='PIDVALUE']]"
  try { $events = @(Get-WinEvent -LogName $log -FilterXPath $q -Oldest -ErrorAction Stop) }
  catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events = @() } else { throw } }
  $rows = @(foreach ($event in $events) {
    [xml]$xml = $event.ToXml()
    $data = @{}
    foreach ($d in $xml.Event.EventData.Data) { $data[[string]$d.Name] = [string]$d.'#text' }
    @{record_id=[long]$event.RecordId; event_id=[int]$event.Id; event_time=$event.TimeCreated.ToUniversalTime().ToString('o'); data=$data}
  })
  ConvertTo-Json -Depth 8 -Compress -InputObject @{rows=$rows}
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
'''.replace('PIDVALUE', str(pid)).replace('CURSOR', str(cursor)).replace('START', start)
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                           '-EncodedCommand', encoded], capture_output=True,
                          timeout=30, encoding='utf-8', errors='replace')
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'PowerShell query failed')
    return json.loads(proc.stdout.lstrip('\ufeff'))['rows']


# Memory observation extension. No memory writes or remote function calls.
import struct
import hashlib


def changed_spans(before, after):
    """Exact contiguous changed byte ranges (same-size buffers)."""
    if len(before) != len(after):
        raise ValueError('Buffer sizes differ')
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        yield start, before[start:i].hex(), after[start:i].hex()


# Field tables: (name, offset, struct format). Offsets verified against the
# 5.6.1-0+UE5-Chameleon SDK dump (see test_detector.py::LoggerBuild).
PAWN_FIELDS = [('InteractLength', 0x510, '<d'), ('IsTalkNow', 0x675, '<B'),
               ('EnableInteract', 0x676, '<B'), ('IsInViewCheckLate', 0x668, '<d'),
               ('UseNearInteract', 0xAF8, '<B')]
NEAR_INTERACT_PTR_OFFSET = 0x400  # Main_C::BPC_NearInteract
NEAR_FIELDS = [('SearchRadius', 0xC0, '<d'), ('Angle', 0xC8, '<d'),
               ('AngleBias', 0xF0, '<d'), ('IgnoreUpVector', 0xE9, '<B')]
SURVIVOR_FIELDS = [('FilledValue', 0xD18, '<d'), ('PreStencil', 0xD20, '<i')]
HELD_ACTOR_OFFSET = 0x508  # Main_C::HaveActor_R (AActor*)
HELD_CAMERA = 'BP_Camera_Base_C'
CAMERA_FIELDS = [('Camera.EnableDistance', 0x500, '<d'),
                 ('Camera.EnableDistanceGimmick', 0x508, '<d'),
                 ('Camera.Is_in_View_Check_Late', 0x540, '<d')]
VIEWPORT_SLOT = 0x70


class MemoryObserver:
    def __init__(self, target, args, emit):
        self.target, self.args, self.emit = target, args, emit
        self.pages, self.values = {}, {}
        self.section = None
        self.k = target.k
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.handle = self.k.OpenProcess(0x10 | 0x1000, False, target.pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        emit('memory_observer_start', pawn=hex(args.pawn) if args.pawn else None,
             code_enabled=args.code, profile=args.pawn_profile,
             message='First successful reads are observations, NOT verified clean originals. Offsets are build-specific.')

    def close(self):
        self.k.CloseHandle(self.handle)

    def read(self, address, size):
        if not 0x10000 <= address < 0x0000800000000000 or not 0 < size <= 1024 * 1024:
            raise ValueError('Invalid memory range')
        buf, got = C.create_string_buffer(size), C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, address, buf, size, C.byref(got)):
            raise C.WinError(C.get_last_error())
        if got.value != size:
            raise OSError('Partial memory read')
        return buf.raw

    def find_text(self):
        modules = self.target.modules()
        matches = [m for m in modules.values() if os.path.normcase(m['path']) == os.path.normcase(self.target.path)]
        if len(matches) != 1:
            raise ValueError('Cannot uniquely identify main executable module')
        m = matches[0]
        base, image_size = int(m['base'], 16), m['size']
        dos = self.read(base, 64)
        if dos[:2] != b'MZ':
            raise ValueError('Missing MZ header')
        nt = struct.unpack_from('<I', dos, 60)[0]
        if nt > image_size - 24:
            raise ValueError('Invalid PE header offset')
        head = self.read(base + nt, 24)
        if head[:4] != b'PE\x00\x00':
            raise ValueError('Missing PE signature')
        count = struct.unpack_from('<H', head, 6)[0]
        optional_size = struct.unpack_from('<H', head, 20)[0]
        table = nt + 24 + optional_size
        if not 0 < count <= 96 or table + count * 40 > image_size:
            raise ValueError('Invalid section table')
        for i in range(count):
            entry = self.read(base + table + i * 40, 40)
            if entry[:8].rstrip(b'\x00') == b'.text':
                size, rva = struct.unpack_from('<II', entry, 8)
                if size <= 0 or rva + size > image_size or size > 512 * 1024 * 1024:
                    raise ValueError('Invalid .text extent')
                self.emit('code_section', image=m['path'], module_base=hex(base), rva=hex(rva), size=size)
                return base, rva, size
        raise ValueError('Main executable has no .text section')

    def poll_code(self):
        try:
            if self.section is None:
                self.section = self.find_text()
            base, rva, size = self.section
            errors, first_error, changed, success = 0, None, 0, 0
            started = time.monotonic()
            for offset in range(0, size, 4096):
                if not self.target.alive():
                    break
                address = base + rva + offset
                try:
                    data = self.read(address, min(4096, size - offset))
                except (OSError, ValueError) as exc:
                    errors += 1
                    if first_error is None:
                        first_error = dict(address=hex(address), message=str(exc))
                    continue  # Failed reads never replace good baselines.
                success += 1
                old = self.pages.get(offset)
                if old is None:
                    self.emit('code_baseline_page', address=hex(address), rva=hex(rva + offset),
                              size=len(data), sha256=hashlib.sha256(data).hexdigest())
                elif old != data:
                    changed += 1
                    self.emit('code_changed', address=hex(address), rva=hex(rva + offset),
                              before_sha256=hashlib.sha256(old).hexdigest(),
                              after_sha256=hashlib.sha256(data).hexdigest(),
                              changes=[dict(offset=n, before_hex=b, after_hex=a) for n, b, a in changed_spans(old, data)])
                self.pages[offset] = data
            self.emit('code_scan', read_pages=success, failed_pages=errors, first_error=first_error,
                      changed_pages=changed, seconds=round(time.monotonic() - started, 3))
        except (OSError, ValueError) as exc:
            self.emit('code_error', message=str(exc))

    def field(self, name, address, fmt):
        try:
            data = self.read(address, struct.calcsize(fmt))
            value = struct.unpack(fmt, data)[0]
            if isinstance(value, float) and not math.isfinite(value):
                value = str(value)  # Standards-compliant JSON for NaN/Infinity.
            prior = self.values.get(name)
            if prior is None or prior[0] != address:
                self.emit('data_baseline', field=name, address=hex(address), value=value, raw_hex=data.hex(),
                          previous_address=hex(prior[0]) if prior else None)
            elif prior[1] != data:
                self.emit('data_changed', field=name, address=hex(address), before=prior[2], after=value,
                          before_hex=prior[1].hex(), after_hex=data.hex())
            self.values[name] = (address, data, value)
            if hasattr(self, 'sample_values'):
                self.sample_values[name] = value
        except (OSError, ValueError) as exc:
            self.values.pop(name, None)
            if hasattr(self, 'sample_errors'):
                self.sample_errors[name] = type(exc).__name__
            self.emit('data_error', field=name, address=hex(address), message=str(exc))

    def poll_data(self):
        pawn = self.args.pawn
        if not pawn:
            return
        # User explicitly selects a verified Main_C-derived hider object.
        for name, offset, fmt in PAWN_FIELDS:
            self.field(name, pawn + offset, fmt)
        self.field('BPC_NearInteract_pointer', pawn + NEAR_INTERACT_PTR_OFFSET, '<Q')
        try:
            near = struct.unpack('<Q', self.read(pawn + NEAR_INTERACT_PTR_OFFSET, 8))[0]
            if not near:
                self.emit('data_unavailable', field='BPC_NearInteract', message='Null component pointer')
                for name in ('SearchRadius', 'Angle', 'AngleBias', 'IgnoreUpVector'):
                    self.values.pop(name, None)
                return
            for name, offset, fmt in NEAR_FIELDS:
                self.field(name, near + offset, fmt)
        except (OSError, ValueError) as exc:
            self.emit('data_error', field='BPC_NearInteract', message=str(exc))
        if self.args.pawn_profile == 'survivor':
            for name, offset, fmt in SURVIVOR_FIELDS:
                self.field(name, pawn + offset, fmt)
        # Held camera fields omitted: the supplied files do not implement the
        # GetName/class check needed to distinguish cameras from other actors.


class AutoObserver(MemoryObserver):
    """Read-only observer for the supplied 5.6.1 dump. Name layout is probed,
    never assumed valid: require None + exact class ancestry before data reads.
    No engine function is called from the external collector.
    """
    MAIN = 'BP_FirstPersonCharacter_Main_C'
    SURVIVOR = 'BP_FirstPersonCharacter_cLeon_Character_Survivor_C'
    GWORLD = 0x09613260  # SDK dump 5.6.1-0+UE5-Chameleon (Basic.hpp Offsets::GWorld)
    GNAMES = 0x097B6400  # Actual NamePool referenced by supplied EXE AppendString
    APPEND_STRING = 0x01392470  # SDK dump Offsets::AppendString (FName::AppendString)
    POOL_REF = 0x01392490  # `lea r8,[rip+disp32]` inside AppendString: 4C 8D 05 <disp32>

    def __init__(self, target, args, emit):
        super().__init__(target, args, emit)
        self.base = None
        self.identity = None
        self.name_mode = None
        self.last_status = None
        self.viewport_identity = None
        self.last_chain = None
        self.held_camera = None
        self.slot_state = None
        self.module_cache = []

    def ptr(self, address, stage="pointer"):
        try:
            value = struct.unpack('<Q', self.read(address, 8))[0]
        except OSError as exc:
            raise OSError(f'{stage}: read_address={address:#x}; {exc}') from exc
        if not 0x10000 <= value < 0x800000000000 or value % 8:
            raise ValueError(f'{stage}: invalid pointer; read_address={address:#x}; raw_value={value:#018x}; reason=' + ('null' if value == 0 else 'range/alignment'))
        return value

    def main_base(self):
        if self.base is None:
            matches = [m for m in self.target.modules().values()
                       if os.path.normcase(m['path']) == os.path.normcase(self.target.path)]
            if len(matches) != 1:
                raise ValueError('Main executable is not uniquely identified')
            m = matches[0]
            if m['size'] <= max(self.GWORLD, self.GNAMES) + 8:
                raise ValueError('Executable image too small for dump RVAs')
            candidate_base = int(m['base'], 16)
            # Check the analyzed RIP-relative NamePool reference before using it.
            # Resolve the lea operand instead of matching fixed bytes, so a moved
            # NamePool is reported with the RVA the running build actually uses.
            ref = self.read(candidate_base + self.POOL_REF, 7)
            if ref[:3] != b'\x4c\x8d\x05':
                raise ValueError('AppendString NamePool reference differs from analyzed EXE; profile rejected '
                                 f'(expected lea r8,[rip+disp32] at RVA {self.POOL_REF:#x}, found {ref.hex()})')
            actual = self.POOL_REF + 7 + struct.unpack('<i', ref[3:])[0]
            if actual != self.GNAMES:
                raise ValueError('AppendString NamePool reference differs from analyzed EXE; profile rejected '
                                 f'(running build resolves NamePool RVA {actual:#x}, logger expects {self.GNAMES:#x})')
            self.base = candidate_base
            self.emit('dump_profile', gworld_rva=hex(self.GWORLD), gnames_rva=hex(self.GNAMES),
                      module_base=hex(self.base), image=m['path'],
                      message='GWorld from SDK dump 5.6.1-0+UE5-Chameleon; NamePool RVA from EXE AppendString. Reference operand resolved and checked; full build identity not proven.')
        return self.base

    def context(self):
        world = self.ptr(self.main_base() + self.GWORLD, "GWorld")
        gi = self.ptr(world + 0x228, "World.OwningGameInstance")
        array, count, capacity = struct.unpack('<Qii', self.read(gi + 0x38, 16))
        if not 1 <= count <= capacity <= 64:
            raise ValueError(f'GameInstance.LocalPlayers: data={array:#x}; count={count}; capacity={capacity}')
        local = self.ptr(array, "LocalPlayers[0]")  # local player 0; split-screen others excluded
        controller = self.ptr(local + 0x30, "LocalPlayer.PlayerController")
        pawn = self.ptr(controller + 0x350, "Controller.AcknowledgedPawn")
        cls = self.ptr(pawn + 0x10, "Pawn.Class")
        index = struct.unpack('<i', self.read(pawn + 0xC, 4))[0]
        return (world, gi, local, controller, pawn, cls, index)

    def decode_name(self, index, mode):
        indirect, shift = mode
        pool = self.main_base() + self.GNAMES
        if indirect:
            pool = self.ptr(pool)
        block, offset = index >> 16, index & 0xFFFF
        current, cursor = struct.unpack('<II', self.read(pool + 8, 8))
        if not 0 <= block <= current < 8192 or cursor > 0x20000:
            raise ValueError(f'Unsupported FName allocator layout: pool={pool:#x}, index={index:#x}, block={block}, current_block={current}, cursor={cursor:#x}')
        block_ptr = self.ptr(pool + 0x10 + block * 8)
        entry_offset = offset * 2
        limit = cursor if block == current else 0x20000
        if entry_offset + 2 > limit:
            raise ValueError('FName entry outside allocated block')
        entry = block_ptr + entry_offset
        header = struct.unpack('<H', self.read(entry, 2))[0]
        length, wide = header >> shift, header & 1
        size = length * (2 if wide else 1)
        if not 1 <= length <= 1023 or entry_offset + 2 + size > limit:
            raise ValueError(f'Invalid FName entry: address={entry:#x}, header={header:#06x}, length={length}, wide={wide}, block_limit={limit:#x}')
        text = self.read(entry + 2, size).decode('utf-16le' if wide else 'ascii')
        if not text.isprintable():
            raise ValueError('Invalid FName characters')
        return text

    def ancestry(self, cls, mode):
        names, seen = [], set()
        for _ in range(64):
            if cls in seen:
                raise ValueError('Class inheritance cycle')
            seen.add(cls)
            index = struct.unpack('<I', self.read(cls + 0x18, 4))[0]
            names.append(self.decode_name(index, mode))
            if hasattr(self, '_diagnostic_names'):
                self._diagnostic_names.append(dict(class_address=hex(cls), fname_index=index, name=names[-1]))
            parent = struct.unpack('<Q', self.read(cls + 0x40, 8))[0]
            if parent == 0:
                return names
            if not 0x10000 <= parent < 0x800000000000 or parent % 8:
                raise ValueError('Invalid superclass pointer')
            cls = parent
        raise ValueError('Class ancestry too deep')

    def verified_classes(self, cls):
        # Preserve class validation. Capture bounded raw evidence instead of
        # guessing another offset or calling code inside the target process.
        now = time.monotonic()
        diagnostic = (getattr(self, '_diag_class', None) != cls or
                      now >= getattr(self, '_diag_next', 0))
        if diagnostic:
            self._diag_class, self._diag_next = cls, now + 30
        all_modes = [(False, 6)]  # Confirmed direct pool, 6-bit header shift in supplied EXE
        candidates = ([self.name_mode] + [m for m in all_modes if m != self.name_mode]
                      if self.name_mode else all_modes)
        for mode in candidates:
            reads, names = [], []
            original_read = self.read
            stage, none_name = 'None sentinel', None
            self._diagnostic_names = []
            def traced_read(address, size):
                try:
                    raw = original_read(address, size)
                except (OSError, ValueError) as exc:
                    if len(reads) < 64:
                        reads.append(dict(address=hex(address), size=size, error=str(exc)))
                    raise
                if len(reads) < 64:
                    reads.append(dict(address=hex(address), size=size, raw_hex=raw[:32].hex(),
                                      truncated=len(raw) > 32))
                return raw
            if diagnostic:
                self.read = traced_read
            failure = None
            try:
                stage = 'Class FName header'
                class_name_raw = self.read(cls + 0x18, 8)
                stage = 'None sentinel'
                none_name = self.decode_name(0, mode)
                if none_name != 'None':
                    raise ValueError('Index 0 decoded as ' + repr(none_name) + ', expected None')
                stage = 'Class ancestry decode'
                names = self.ancestry(cls, mode)
                stage = 'Pawn inheritance validation'
                if not names or names[-1] != 'Object' or 'Pawn' not in names or 'Actor' not in names:
                    raise ValueError('Decoded ancestry does not satisfy Pawn/Actor/Object validation')
            except (OSError, ValueError, UnicodeError) as exc:
                failure = str(exc)
            finally:
                self.read = original_read
            if diagnostic:
                self.emit('fname_candidate_diagnostic', class_address=hex(cls),
                          gnames_address=hex(self.main_base() + self.GNAMES),
                          indirect=mode[0], length_shift=mode[1], stage=stage,
                          success=failure is None, failure=failure, none_name=none_name,
                          ancestry=names, decoded_steps=self._diagnostic_names,
                          reads=reads, read_limit=64, max_bytes_per_read=32)
            if failure is None:
                if self.name_mode != mode:
                    self.emit('name_layout_validated', indirect=mode[0], length_shift=mode[1], ancestry=names,
                              message='Runtime consistency checks passed; name layout was not specified by SDK.')
                self.name_mode = mode
                return names
        self.name_mode = None
        raise ValueError('FName layout/class validation failed; see fname_candidate_diagnostic; data sampling skipped')

    def status(self, message):
        if message != self.last_status:
            self.emit('auto_status', message=message)
            self.last_status = message

    def owner_module(self, address):
        """(module name or None, inside main executable image) for an address."""
        for attempt in (0, 1):
            for start, end, name, is_main in self.module_cache:
                if start <= address < end:
                    return name, is_main
            if attempt == 0:  # cache miss: refresh once (module list may have changed)
                self.module_cache = [
                    (int(m['base'], 16), int(m['base'], 16) + m['size'], m['name'],
                     os.path.normcase(m['path']) == os.path.normcase(self.target.path))
                    for m in self.target.modules().values()]
        return None, False

    def poll_viewport(self, local):
        self.slot_state = None
        try:
            viewport = self.ptr(local + 0x78)
            table = self.ptr(viewport)
            identity = (viewport, table)
            if identity != self.viewport_identity:
                for key in list(self.values):
                    if key.startswith('Viewport.'):
                        self.values.pop(key)
                self.emit('viewport_binding', viewport=hex(viewport), vtable=hex(table),
                          slot_index=VIEWPORT_SLOT,
                          message='Slot 0x70 is not in the SDK; it matches the hook slot in the supplied meccha.dll (static analysis).')
                self.viewport_identity = identity
                self.slot_state = None
            address = table + VIEWPORT_SLOT * 8
            self.field('Viewport.PostRender_candidate', address, '<Q')
            entry = self.values.get('Viewport.PostRender_candidate')
            if entry and entry[0] == address:
                target = entry[2]
                module, in_main = self.owner_module(target)
                state = (target, module, in_main)
                if state != self.slot_state:
                    self.slot_state = state
                    self.emit('viewport_slot_owner', slot_address=hex(address), target=hex(target),
                              module=module, outside_main_image=not in_main,
                              message='Slot target module: ' + (module or 'none (not in any loaded module)') +
                                      ('' if in_main else ' [outside main image]'))
        except (OSError, ValueError, KeyError) as exc:
            self.emit('viewport_error', message=str(exc))

    def poll_data(self):
        self.sample_values = {}
        self.sample_errors = {}
        self.sample_identity = None
        self.slot_state = None
        try:
            ctx = self.context()
            world, gi, local, controller, pawn, cls, index = ctx
            if ctx != self.last_chain:
                self.last_chain = ctx
                self.emit('pointer_chain', world=hex(world), game_instance=hex(gi), local_player=hex(local), controller=hex(controller), pawn=hex(pawn), class_address=hex(cls))
            self.poll_viewport(local)
            classes = self.verified_classes(cls)
            if self.MAIN not in classes:
                self.sample_errors['pawn'] = 'UnsupportedPawnClass'
                self.identity = self.held_camera = None
                self.values = {k: v for k, v in self.values.items() if k.startswith('Viewport.')}
                self.status('Current Pawn is not Main_C-derived: ' + classes[0])
                return
            if ctx != self.identity:
                self.held_camera = None
                self.values = {k: v for k, v in self.values.items() if k.startswith('Viewport.')}
                self.emit('pawn_binding', pawn=hex(pawn), class_address=hex(cls), object_index=index,
                          ancestry=classes, world=hex(world), controller=hex(controller))
                self.identity = ctx
            # Stage the field log entries, then verify the binding again.
            actual_emit = self.emit
            staged = []
            self.emit = lambda kind, **data: staged.append((kind, data))
            near = None
            try:
                for name, off, fmt in PAWN_FIELDS:
                    self.field(name, pawn + off, fmt)
                if self.SURVIVOR in classes:
                    for name, off, fmt in SURVIVOR_FIELDS:
                        self.field(name, pawn + off, fmt)
                try:
                    near = self.ptr(pawn + NEAR_INTERACT_PTR_OFFSET)
                    near_classes = self.ancestry(self.ptr(near + 0x10), self.name_mode)
                    if 'BPC_NearInteract_C' not in near_classes:
                        raise ValueError('Component class mismatch')
                    for name, off, fmt in NEAR_FIELDS:
                        self.field(name, near + off, fmt)
                except (OSError, ValueError, UnicodeError) as exc:
                    self.sample_errors['near_component'] = type(exc).__name__
                    self.emit('component_unavailable', message=str(exc))
                    for key in ('SearchRadius','Angle','AngleBias','IgnoreUpVector'):
                        self.values.pop(key, None)
                    near = None
                held = None
                try:  # No held item is normal; only an exact camera class is read.
                    held = self.ptr(pawn + HELD_ACTOR_OFFSET)
                    if HELD_CAMERA not in self.ancestry(self.ptr(held + 0x10), self.name_mode):
                        held = None
                except (OSError, ValueError, UnicodeError):
                    held = None
                if held != self.held_camera:
                    self.emit('held_camera_binding', held=hex(held) if held else None)
                    for name, _, _ in CAMERA_FIELDS:
                        self.values.pop(name, None)
                if held:
                    for name, off, fmt in CAMERA_FIELDS:
                        self.field(name, held + off, fmt)
                if self.context() != ctx or (near and self.ptr(pawn + NEAR_INTERACT_PTR_OFFSET) != near):
                    raise ValueError('Binding changed during sampling; sample discarded')
                if held and self.ptr(pawn + HELD_ACTOR_OFFSET) != held:
                    raise ValueError('Held item changed during sampling; sample discarded')
            finally:
                self.emit = actual_emit
            for kind, data in staged:
                self.emit(kind, **data)
            self.held_camera = held
            self.sample_identity = (ctx, near)
            self.status('Sampling verified Pawn: ' + classes[0])
        except (OSError, ValueError, UnicodeError) as exc:
            self.sample_values = {}
            self.sample_errors['binding'] = type(exc).__name__
            self.slot_state = None
            # Pause and clear data baselines; never compare unrelated lifetimes.
            self.values = {k: v for k, v in self.values.items() if k.startswith('Viewport.')}
            self.identity = self.held_camera = None
            self.status(str(exc))


def main():
    # The launcher stops modules with Ctrl+Break (CTRL_BREAK_EVENT). Map it to
    # KeyboardInterrupt so the finally block (shared flush/shutdown, collector_stop)
    # runs; Windows' default handler would terminate without cleanup.
    try:
        import signal
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    except (AttributeError, ValueError, OSError):
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--code', action='store_true', help='Observe main EXE .text changes')
    p.add_argument('--code-interval', type=float, default=10, help='Seconds between .text scans')
    p.add_argument('--modules-only', action='store_true', help='Disable memory/data/vtable observation')
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--interval', type=float, default=1, help='Module poll seconds (default 1)')
    p.add_argument('--duration', type=float, default=0, help='Seconds; 0 means until Ctrl+C')
    p.add_argument('--sysmon', action='store_true', help='Read existing Sysmon events every 5 seconds')
    p.add_argument('--label', default='observation', help='Legacy alias/fallback for --session-id')
    p.add_argument('--session-id', help='Test name, e.g. normal_001 or hide_anywhere_002')
    p.add_argument('--player-id', default='player_local')
    p.add_argument('--module', default='hide_anywhere', help='Common-event module name')
    p.add_argument('--play-label', choices=('NORMAL', 'CHEAT'), help='Manifest label; inferred when omitted')
    p.add_argument('--cheat-start-ms', type=int)
    p.add_argument('--cheat-end-ms', type=int)
    p.add_argument('--out', type=Path, default=Path('logs'))
    p.add_argument('--local-only', action='store_true', help='Disable shared telemetry (default: ClientConfig.from_env)')
    a = p.parse_args()
    if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
        p.error('Requires Windows and 64-bit Python 3.10+.')
    if not 0 < a.pid <= 0xFFFFFFFF:
        p.error('PID must be a positive DWORD.')
    if not math.isfinite(a.interval) or a.interval < 0.2:
        p.error('interval must be finite and >= 0.2 seconds')
    if not math.isfinite(a.duration) or a.duration < 0:
        p.error('duration must be finite and >= 0')
    if not math.isfinite(a.code_interval) or a.code_interval < 1:
        p.error('code-interval must be finite and >= 1')
    if a.modules_only and a.code:
        p.error('--modules-only cannot be combined with --code')
    if a.cheat_start_ms is not None and a.cheat_start_ms < 0:
        p.error('--cheat-start-ms must be >= 0')
    if a.cheat_end_ms is not None and a.cheat_end_ms < 0:
        p.error('--cheat-end-ms must be >= 0')
    if (a.cheat_start_ms is not None and a.cheat_end_ms is not None and
            a.cheat_end_ms < a.cheat_start_ms):
        p.error('--cheat-end-ms must be >= --cheat-start-ms')
    a.pawn, a.pawn_profile = None, 'auto'
    requested_session = a.session_id or a.label
    session_id = ''.join(c if c.isascii() and (c.isalnum() or c in '-_') else '_' for c in requested_session)[:60]
    if not session_id:
        p.error('session-id must contain at least one ASCII letter, number, - or _')
    directory = a.out / session_id
    raw_directory = directory / 'raw'
    raw_directory.mkdir(parents=True, exist_ok=False)
    play_label = a.play_label or ('NORMAL' if session_id.lower().startswith('normal_') else 'CHEAT')
    manifest = {
        'session_id': session_id,
        'player_id': a.player_id,
        'label': play_label,
        'cheat_type': None if play_label == 'NORMAL' else a.module.upper(),
        'cheat_start_ms': None if play_label == 'NORMAL' else a.cheat_start_ms,
        'cheat_end_ms': None if play_label == 'NORMAL' else a.cheat_end_ms,
    }
    (directory / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    target = None
    memory = None
    server = None
    rule = Rule(required=3)
    with ((raw_directory / 'mecha_log.jsonl').open('x', encoding='utf-8', buffering=1) as log,
          (directory / 'events.jsonl').open('x', encoding='utf-8', buffering=1) as common_log):
        def emit(kind, **data):
            row = dict(time=utc(), kind=kind, pid=a.pid, label=session_id, **data)
            log.write(json.dumps(row, ensure_ascii=False) + '\n')
            print(row['time'], kind, data.get('message', ''), flush=True)
        started = utc()
        emit('collector_start', collector_pid=os.getpid(), sysmon_requested=a.sysmon)
        print('Raw log:', (raw_directory / 'mecha_log.jsonl').resolve())
        print('Common events:', (directory / 'events.jsonl').resolve())
        print('Manifest:', (directory / 'manifest.json').resolve())
        previous = None
        cursor = 0
        next_sysmon = 0
        begin = time.monotonic()
        try:
            if not a.local_only:
                try:
                    server = ServerBridge(emit)
                    emit('server_client_ready')
                except Exception as exc:
                    emit('server_configuration_error', error_type=type(exc).__name__,
                         message='Check shared.logger and launcher environment; server forwarding did not start.')
                    return 1
            target = Windows(a.pid)
            emit('target_attached', image=target.path)
            next_code = 0
            if not a.modules_only:
                try:
                    memory = AutoObserver(target, a, emit)
                except OSError as exc:
                    emit('memory_disabled', message=str(exc))
            if a.sysmon:
                emit('coverage_note', message='Sysmon rules must already enable 7/8/10. Empty results do not prove coverage. Log clear/rollover may cause gaps; restart collector after clearing logs.')
            while target.alive():
                now = time.monotonic()
                if a.duration and now - begin >= a.duration:
                    break
                current = None
                try:
                    current = target.modules()
                    if previous is None:
                        emit('module_baseline', modules=list(current.values()))
                    else:
                        added, removed = diff_modules(previous, current)
                        for module in added:
                            emit('module_added', module=module)
                        for module in removed:
                            emit('module_removed', module=module)
                    previous = current
                except OSError as exc:
                    # Preserve previous successful snapshot: failure is not removal.
                    emit('module_error', message=str(exc))
                if a.sysmon and now >= next_sysmon and target.alive():
                    try:
                        rows = sysmon_query(a.pid, started, cursor)
                        for row in rows:
                            emit('sysmon', **row)
                            cursor = max(cursor, row['record_id'])
                        emit('sysmon_poll', matched=len(rows), cursor=cursor)
                    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                        emit('sysmon_error', message=str(exc))
                    next_sysmon = time.monotonic() + 5
                if not target.alive():
                    break
                if memory:
                    memory.poll_data()
                values = getattr(memory, 'sample_values', {})
                loaded = (any(m['name'].casefold() == 'meccha.dll'
                              for m in current.values()) if current is not None else None)
                slot = getattr(memory, 'slot_state', None)
                hooked = (not slot[2]) if slot else None
                common = make_common_event(
                        session_id=session_id,
                        player_id=a.player_id,
                        module=a.module,
                        timestamp_ms=(time.monotonic() - begin) * 1000,
                        values=values,
                        injected_module=loaded,
                        viewport_hook=hooked, rule=rule,
                        identity=getattr(memory, 'sample_identity', None),
                        errors=getattr(memory, 'sample_errors', {'observer': 'DisabledOrUnavailable'}))
                common_log.write(json.dumps(common, ensure_ascii=False, allow_nan=False) + '\n')
                common_log.flush()
                if server:
                    server.send(common)
                if memory and a.code and time.monotonic() >= next_code:
                    memory.poll_code()
                    next_code = time.monotonic() + a.code_interval
                time.sleep(a.interval)
            emit('collection_finished', message='Duration reached or target exited. Sysmon ingestion delays can leave trailing events uncollected.')
        except KeyboardInterrupt:
            emit('interrupted')
        except OSError as exc:
            emit('fatal_error', message=str(exc))
            return 1
        finally:
            # Already cleaning up (target exited or first Ctrl+Break). A later Ctrl+Break
            # from the launcher must not interrupt the flush/shutdown below.
            try:
                import signal
                signal.signal(signal.SIGBREAK, signal.SIG_IGN)
            except (AttributeError, ValueError, OSError):
                pass
            if server:
                server.close()
            if memory:
                memory.close()
            if target:
                target.close()
            emit('collector_stop')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
