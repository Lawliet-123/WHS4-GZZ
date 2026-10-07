"""Conservative local classification, independent of Launcher/shared implementation.

Registry entries are claims, not authorization. Approval also requires live process
identities, reviewed source hashes, the collector's runtime, and a bounded task.
This is deployment attestation, not proof that a Python process is uncompromised.
"""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = Path(__file__).resolve().parents[1] / 'artifacts/approved_access.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(path, cwd):
    path = Path(path)
    return os.path.normcase(str((path if path.is_absolute() else Path(cwd)/path).resolve()))


class ApprovedAccess:
    def __init__(self, session, player, target_pid, target_created, inspect, runtime,
                 *, root=ROOT, registry=None, policy=POLICY):
        self.session, self.player = session, player
        self.target_pid, self.target_created = target_pid, target_created
        self.inspect, self.runtime = inspect, runtime
        self.root = Path(root).resolve()
        self.registry = Path(registry) if registry else Path(
            os.environ.get('AC_LAUNCHER_LOG_DIR', self.root/'client/Launcher/logs'))/'anticheat_pids.json'
        self.policy = Path(policy)

    def classify(self, event):
        evidence = {'classification': 'unresolved', 'reason': 'not_checked',
                    'actor_pid': event.get('actor_pid'),
                    'actor_create_time': event.get('actor_create_time'),
                    'target_pid': event.get('target_pid'),
                    'expected_target_create_time': self.target_created,
                    'policy_version': 'reviewed_source_v1', 'approval_valid': False}
        def unknown(reason):
            evidence['reason'] = reason
            return evidence
        try:
            flags = event.get('flags', 0)
            if flags & (8 | 128 | 4 | 64):
                return unknown('thread_duplicate_restricted_or_incomplete_operation_not_approved')
            if not flags & 256 or not event.get('actor_create_time'):
                return unknown('driver_caller_identity_missing')
            if event.get('status', 0) & 0x80000000:
                return unknown('handle_operation_failed')
            if event.get('target_pid') != self.target_pid:
                return unknown('target_pid_mismatch')
            if any(event.get(key) != event.get('actor_pid') for key in ('source_pid', 'recipient_pid')):
                return unknown('handle_source_or_recipient_not_caller')
            game = self.inspect(self.target_pid, command=False)
            if game['create_time'] != self.target_created:
                return unknown('target_pid_reused')
            registry = json.loads(self.registry.read_text(encoding='utf-8-sig'))
            if registry.get('session_id') != self.session or registry.get('stopping') is not False:
                return unknown('registry_session_stale_or_stopping')
            claims = [(name, entry) for name, entry in registry['entries'].items()
                      if entry.get('pid') == event.get('actor_pid')]
            if len(claims) != 1:
                return unknown('actor_not_uniquely_registered')
            name, entry = claims[0]
            evidence['actor_module'] = name
            policy = json.loads(self.policy.read_text(encoding='utf-8'))
            spec = policy['modules'].get(name)
            if not spec:
                return unknown('module_has_no_reviewed_policy')
            if entry.get('create_time') != event['actor_create_time']:
                return unknown('registry_actor_generation_mismatch')
            actor = self.inspect(event['actor_pid'], command=True)
            if actor['create_time'] != event['actor_create_time']:
                return unknown('actor_pid_reused')
            launcher = self.inspect(registry['launcher_pid'], command=True)
            if launcher['create_time'] != registry.get('launcher_create_time'):
                return unknown('launcher_pid_reused_or_stale_registry')
            if launcher['create_time'] > actor['create_time']:
                return unknown('actor_predates_launcher')
            for process in (actor, launcher):
                if canonical(process['image'], self.root) != canonical(self.runtime['image'], self.root) or \
                        digest(process['image']) != self.runtime['sha256']:
                    return unknown('executable_not_attested_collector_runtime')
            evidence.update(actor_image=actor['image'], actor_argv=actor['argv'],
                            launcher_pid=registry['launcher_pid'],
                            launcher_create_time=launcher['create_time'],
                            runtime_sha256=self.runtime['sha256'])
            if any(canonical(cwd, self.root) != canonical(self.root, self.root)
                   for cwd in (entry['cwd'], actor['cwd'], launcher['cwd'])):
                return unknown('registered_work_directory_mismatch')
            # venv Launcher records its stub but runs sys._base_executable directly.
            # Only argv[0] may differ; both live images were independently verified.
            if canonical(entry['argv'][0], self.root) not in {canonical(p, self.root) for p in
                    self.runtime.get('registered_interpreters', [self.runtime['image']])}:
                return unknown('registered_interpreter_mismatch')
            if actor['argv'][1:] != entry['argv'][1:]:
                return unknown('live_command_differs_from_registry')
            if not self._entry_matches(actor['argv'], spec['entry']):
                return unknown('responsible_script_mismatch')
            if not self._entry_matches(launcher['argv'], ['client/Launcher/main.py']):
                return unknown('launcher_script_mismatch')
            for option, expected in (('--session', self.session), ('--player', self.player)):
                argv = launcher['argv']
                if argv.count(option) != 1 or argv[argv.index(option)+1] != expected:
                    return unknown('live_launcher_session_or_player_unverified')
            args = actor['argv']
            for option, expected in (('--session-id', self.session), ('--player-id', self.player)):
                if args.count(option) != 1 or args[args.index(option)+1] != expected:
                    return unknown('live_session_or_player_mismatch')
            if spec.get('target_option'):
                option = spec['target_option']
                if args.count(option) != 1 or int(args[args.index(option)+1]) != self.target_pid:
                    return unknown('live_target_argument_mismatch')
            # Pin the entire reviewed Python source set, including import surfaces.
            for tree in spec['trees'] + policy['launcher_trees']:
                source = self.root/tree
                paths = [source] if source.is_file() else list(source.rglob('*.py'))
                actual = {str(p.relative_to(self.root)).replace(os.sep, '/'): digest(p) for p in paths}
                expected = {p: value for p, value in policy['sha256'].items()
                            if p == tree or p.startswith(tree.rstrip('/')+'/')}
                if not actual or actual != expected:
                    return unknown('reviewed_source_hash_or_inventory_mismatch')
            evidence['source_revision'] = policy['source_revision']
            evidence['source_sha256_verified'] = True
            allowed = spec['allowed_process_access']
            evidence.update(allowed_process_access=allowed, approved_task=spec['task'])
            if allowed is None:
                return unknown('required_access_scope_not_verified')
            for field in ('original_access', 'before_access', 'granted_access'):
                value = event.get(field)
                if type(value) is not int or value < 0:
                    return unknown('access_measurement_missing_or_invalid')
                if value & ~allowed:
                    return unknown('access_outside_reviewed_scope')
            # Re-check generations after command/source inspection; never use old identity on failure.
            for pid, created in ((self.target_pid, self.target_created),
                                 (event['actor_pid'], event['actor_create_time']),
                                 (registry['launcher_pid'], launcher['create_time'])):
                if self.inspect(pid, command=False)['create_time'] != created:
                    return unknown('process_generation_changed_during_validation')
            if json.loads(self.registry.read_text(encoding='utf-8-sig')) != registry:
                return unknown('registry_changed_during_validation')
            evidence.update(classification='approved_observation', approval_valid=True,
                            reason='live_generations_runtime_script_source_and_access_scope_verified')
            return evidence
        except Exception as exc:
            evidence.update(reason='approval_validation_unavailable', error_type=type(exc).__name__)
            return evidence

    def _entry_matches(self, argv, entry):
        if entry[0] == '-m':
            return len(argv) >= 3 and argv[1:3] == entry
        return len(argv) >= 2 and not argv[1].startswith('-') and \
            canonical(argv[1], self.root) == canonical(entry[0], self.root)


class WindowsInspector:
    """Native exact FILETIME + optional psutil live argv/cwd; no cached fallbacks."""
    def __init__(self, api):
        self.api = api

    def __call__(self, pid, *, command):
        handle, created, image = self.api.process(pid)
        try:
            if self.api.wait(handle, 0) != 258:
                raise RuntimeError('process_not_running')
            result = dict(create_time=created, image=image)
            if command:
                import psutil
                process = psutil.Process(pid)
                result.update(argv=process.cmdline(), cwd=process.cwd())
                # Bracket psutil's PID-based reads with native generation queries.
                check, generation, _ = self.api.process(pid)
                try:
                    if generation != created or self.api.wait(check, 0) != 258:
                        raise RuntimeError('process_generation_changed')
                finally:
                    self.api.close(check)
            if self.api.wait(handle, 0) != 258:
                raise RuntimeError('process_exited_during_inspection')
            return result
        finally:
            self.api.close(handle)
