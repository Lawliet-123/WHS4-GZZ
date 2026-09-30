"""Explicit Windows wire layouts; works offline on non-Windows systems."""
import struct

VERSION = 1
EVENT = struct.Struct('<6Q12I520s')
BATCH = struct.Struct('<IIQ')
STATUS = struct.Struct('<4I3Q2I')
POLICY = struct.Struct('<4IQ')
DIAG_SIZE = 278928
PROBES = ('NtOpenProcess', 'NtQuerySystemInformation', 'MmCopyVirtualMemory', 'PsLookupProcessByProcessId')
KINDS = dict(enumerate(('', 'process_create', 'process_exit', 'thread_create', 'thread_exit',
    'game_image', 'kernel_image', 'handle_pre', 'handle_post', 'policy', 'target_exit')))
FIELDS = ('sequence timestamp_100ns generation operation_id image_base image_size '
    'kind actor_pid target_pid thread_id source_pid recipient_pid original_access '
    'before_access after_access flags status granted_access path').split()

def ioctl(function, access):
    return (0x8337 << 16) | (access << 14) | (function << 2)

IO_STATUS = ioctl(0x800, 1)
IO_POLICY = ioctl(0x801, 3)
IO_EVENTS = ioctl(0x802, 1)
IO_HEARTBEAT = ioctl(0x803, 2)
IO_DIAGNOSTICS = ioctl(0x804, 1)
IO_CODE = ioctl(0x805, 1)
IO_THREADS = ioctl(0x806, 1)
THREAD_HEADER = struct.Struct('<8I')
THREAD_RECORD = struct.Struct('<4I3Q')
THREAD_SIZE = THREAD_HEADER.size + 4096 * THREAD_RECORD.size

def parse_threads(data):
    if len(data) != THREAD_SIZE:
        raise ValueError('thread snapshot size mismatch')
    d = dict(zip(('version', 'status', 'count', 'enumerated', 'lookup_failed', 'os_build',
                  'duration_ms', 'flags'), THREAD_HEADER.unpack_from(data)))
    if d['version'] != VERSION or d['count'] > 4096 or d['count'] > d['enumerated']:
        raise ValueError('thread snapshot version/count mismatch')
    d['type'] = 'kernel_thread_snapshot'
    d['threads'] = []
    for i in range(d['count']):
        d['threads'].append(dict(zip(('pid', 'tid', 'flags', 'status', 'create_time', 'start',
                                     'snapshot_start'), THREAD_RECORD.unpack_from(data, 32+i*40))))
    return d

CODE = struct.Struct('<4IQ2I32s32s256s')

def parse_code(data):
    if len(data) != CODE.size:
        raise ValueError('code scan size mismatch')
    d = dict(zip(('version', 'status', 'flags', 'module_count', 'base', 'size',
                  'bytes_hashed', 'baseline', 'current', 'path'), CODE.unpack(data)))
    if d['version'] != VERSION:
        raise ValueError('code scan version mismatch')
    d.update(type='kernel_code_scan', baseline=d['baseline'].hex(), current=d['current'].hex(),
             path=d['path'].split(b'\0')[0].decode('utf-8', 'replace'))
    return d

def parse_status(data):
    if len(data) != STATUS.size:
        raise ValueError('status length mismatch')
    d = dict(zip(('version', 'pid', 'mode', 'lease_active', 'generation', 'create_time',
                  'dropped_total', 'queue_count', 'capabilities'), STATUS.unpack(data)))
    if d['version'] != VERSION:
        raise ValueError('driver protocol version mismatch')
    return d

def parse_events(data):
    if len(data) < BATCH.size:
        raise ValueError('truncated batch')
    version, count, dropped = BATCH.unpack_from(data)
    if version != VERSION or count > 1024 or len(data) != 16 + count * EVENT.size:
        raise ValueError('invalid event batch/version/size')
    events = []
    for offset in range(16, len(data), EVENT.size):
        e = dict(zip(FIELDS, EVENT.unpack_from(data, offset)))
        e['path'] = e['path'].decode('utf-16-le', errors='replace').split('\0', 1)[0]
        e['type'] = KINDS.get(e.pop('kind'), 'unknown')
        e['unix_ms'] = (e['timestamp_100ns'] - 116444736000000000) // 10000
        if e['type'] in ('process_create', 'process_exit', 'target_exit'):
            e['process_create_time'] = e.pop('image_base')
        if e['type'] in ('handle_pre', 'handle_post') and e['flags'] & 256:
            e['actor_tid'] = e.pop('image_base')
            e['actor_create_time'] = e.pop('image_size')
            e['caller_identity_scope'] = 'current_callback_execution_context_not_call_stack'
        events.append(e)
    return events, dropped

def parse_diagnostics(data):
    if len(data) != DIAG_SIZE:
        raise ValueError('diagnostics size mismatch')
    keys = ('version', 'count', 'module_status', 'valid', 'changed', 'failed', 'dispatch', 'reserved')
    d = dict(zip(keys, struct.unpack_from('<8I', data)))
    if d['version'] != VERSION or d['count'] > 1024:
        raise ValueError('diagnostics version/count mismatch')
    d['type'] = 'kernel_diagnostics'
    d['probes'] = []
    for i, name in enumerate(PROBES):
        address, status, _, baseline, current = struct.unpack_from('<QII32s32s', data, 32 + i * 80)
        p = {'name': name, 'address': address, 'read_status': status,
             'baseline': baseline.hex(), 'current': current.hex()}
        # Decode only an entry E9 rel32 instruction, never treat a jump alone as malicious.
        if status == 0 and current[0] == 0xe9:
            p['entry_e9_target'] = (address + 5 + struct.unpack_from('<i', current, 1)[0]) & ((1 << 64)-1)
        d['probes'].append(p)
    d['dispatch_pointers'] = [dict(zip(('baseline', 'current'), struct.unpack_from('<QQ', data, 352+i*16))) for i in range(3)]
    d['modules'] = []
    for i in range(d['count']):
        base, size, path, _ = struct.unpack_from('<QI256sI', data, 400 + i*272)
        d['modules'].append({'base': base, 'size': size, 'path': path.split(b'\0')[0].decode('utf-8', 'replace')})
    return d
