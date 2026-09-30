"""Evidence-based checks. Missing evidence never becomes a passing result."""
PROCESS_STRIP = 0x86B
THREAD_STRIP = 0x13
THREAD_SUSPEND_RESUME = 0x2
THREAD_RESUME = 0x1000


def request_matches_callback(requested, original, is_thread):
    """Account only for the observed thread suspend->resume access expansion.

    API input and OB OriginalDesiredAccess belong to different stages. Windows
    can add THREAD_RESUME for THREAD_SUSPEND_RESUME before calling OB callbacks.
    Do not mask arbitrary differences or apply thread normalization to processes.
    Actual post rights still have to match independent NtQueryObject evidence.
    """
    return original == requested or (is_thread and bool(requested & THREAD_SUSPEND_RESUME)
                                     and original == (requested | THREAD_RESUME))


def check_handle(truth, events, mode, generation, lost=0):
    if lost:
        return 'INCONCLUSIVE', 'Event queue overflow during this operation'
    if not truth['opened']:
        return 'INCONCLUSIVE', f"Windows refused the handle, error={truth['error']}"
    if truth.get('query_status', -1) != 0:
        return 'INCONCLUSIVE', 'Independent granted-access query failed'
    mask = 8 | 128
    expected_flags = (8 if truth['thread'] else 0) | (128 if truth['duplicate'] else 0)
    pre = [e for e in events if e['type'] == 'handle_pre'
           and e['actor_pid'] == truth['actor_pid'] and e['target_pid'] == truth['target_pid']
           and e['generation'] == generation and e['flags'] & mask == expected_flags
           and e['thread_id'] == truth['tid'] and not e['flags'] & 3
           and truth['begin'] <= e['timestamp_100ns'] <= truth['end']
           and request_matches_callback(truth['requested'], e['original_access'], truth['thread'])
           and e['source_pid'] == truth['actor_pid'] and e['recipient_pid'] == truth['actor_pid']]
    if len(pre) != 1:
        return ('FAIL' if not pre else 'INCONCLUSIVE'), f'Expected one pre event, got {len(pre)}'
    a = pre[0]
    if a['flags'] & 64:
        return 'INCONCLUSIVE', 'Driver could not allocate post context'
    post = [e for e in events if e['type'] == 'handle_post'
            and e['operation_id'] == a['operation_id'] and e['generation'] == generation
            and e['actor_pid'] == truth['actor_pid'] and e['target_pid'] == truth['target_pid']
            and truth['begin'] <= e['timestamp_100ns'] <= truth['end']]
    if len(post) != 1:
        return 'FAIL', f'Expected one matching post, got {len(post)}'
    b = post[0]
    if b['status'] & 0x80000000:
        return 'FAIL', 'OS returned a handle but driver logged a failed operation'
    if b['granted_access'] != truth['granted']:
        return 'FAIL', 'Post granted_access disagrees with independent NtQueryObject'
    strip = THREAD_STRIP if truth['thread'] else PROCESS_STRIP
    expected = a['before_access'] & ~strip if mode == 'enforce' else a['before_access']
    if a['after_access'] != expected:
        return 'FAIL', f'Wrong filtered access: expected {expected:#x}, got {a["after_access"]:#x}'
    if mode == 'enforce':
        if not a['flags'] & 32:
            return 'INCONCLUSIVE', 'Enforcement lease was not active'
        if truth['granted'] & strip:
            return 'FAIL', 'Forbidden rights remain in the actual handle'
        if bool(a['flags'] & 4) != bool(a['before_access'] & strip):
            return 'FAIL', 'Stripped flag disagrees with the access change'
    elif a['flags'] & (4 | 32):
        return 'FAIL', 'Observe mode unexpectedly changed rights or enforced'
    preserved = truth['requested'] & ~strip if mode == 'enforce' else truth['requested']
    if truth['granted'] & preserved != preserved:
        return 'FAIL', 'Requested allowed rights were not preserved'
    normalization = (f'; API request={truth["requested"]:#x}, callback original={a["original_access"]:#x}'
                     if a['original_access'] != truth['requested'] else '')
    return 'PASS', f'pre/post operation {a["operation_id"]}; actual access={truth["granted"]:#x}' + normalization
