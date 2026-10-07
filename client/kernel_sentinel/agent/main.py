import argparse
import json
from pathlib import Path
import queue
import threading
import time
import sys
import os
from . import protocol as P
from .analysis import CrossView, findings, validate_config
from .files import FileWorker
from .sensors import ThreadOrigins, CallbackHealth
from .feature_status import FeatureStatus, render_table
from .session_clock import SessionClock
from .telemetry import Telemetry
from .startup import StartupDiagnostics

class Log:
    def __init__(self, directory, session, player, config, *, t0=None, telemetry='off', cycle_events=False, access_classifier=None):
        self.clock = SessionClock(t0)
        self.telemetry = None
        self.cycle_events = cycle_events
        self.cycle_findings = []
        self.cycle_errors = []
        self.cycle_observed = False
        self.access_classifier = access_classifier
        self.cycle_access = []
        self.cycle_access_counts = {'approved_observation': 0, 'unresolved': 0}
        directory.mkdir(parents=True, exist_ok=False)
        self.raw = (directory/'raw_events.jsonl').open('x', encoding='utf-8')
        self.common = (directory/'common_events.jsonl').open('x', encoding='utf-8')
        self.session, self.player, self.config = session, player, config
        self.start_unix = time.time_ns() // 1_000_000
        self.last = {}
        self.count = 0
        self.directory = directory
        self.feature_status = FeatureStatus()
        try:
            if telemetry == 'managed':
                self.telemetry = Telemetry()
        except Exception:
            self.raw.close()
            self.common.close()
            raise
    def publish(self, record):
        self.common.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
        self.common.flush()
        self.count += 1
        if self.telemetry:
            try:
                receipt = self.telemetry.send(record)
                row = {'type': 'server_queued', 'event_id': receipt.event_id,
                       'delivery': 'local_outbox_only', 'timestamp_ms': record['timestamp_ms']}
            except Exception as exc:
                row = {'type': 'server_enqueue_error', 'error_type': type(exc).__name__,
                       'message': 'Event remains in common_events.jsonl'}
            self.raw.write(json.dumps(row)+'\n')
            self.raw.flush()
    def common_event(self, event, reasons, score, valid=True):
        return {'session_id': self.session, 'player_id': self.player,
                'module': 'kernel_sentinel', 'timestamp_ms': self.clock.event_ms(event),
                'evidence': {**event, **({'source_status': event['status']} if 'status' in event else {}),
                             **self.clock.evidence(), 'measurement_valid': valid,
                             'status': 'DETECTED' if score else 'NORMAL' if valid else 'ERROR'},
                'reasons': reasons, 'raw_score': score}
    def write(self, event):
        event.setdefault('collected_unix_ms', time.time_ns() // 1_000_000)
        self.raw.write(json.dumps(event, ensure_ascii=False)+'\n')
        self.raw.flush()
        self.feature_status.observe(event)
        observed = findings(event, self.config)
        # Preserve driver raw first. Apply approval only to this event's access finding.
        scoring_event = event
        access = None
        if event['type'] == 'handle_post' and not event.get('flags', 0) & 3 and observed:
            access = (self.access_classifier.classify(event) if self.access_classifier else
                      {'classification': 'unresolved', 'reason': 'approval_classifier_unavailable',
                       'actor_pid': event.get('actor_pid')})
            scoring_event = {**event, 'access_approval': access}
            if access['classification'] == 'approved_observation':
                self.cycle_observed = True
                observed = [(reason, score) for reason, score in observed
                            if reason != 'sensitive_handle_rights_granted']
                observed.append(('approved_defense_module_access', 0))
            elif access['reason'] in ('approval_validation_unavailable', 'approval_classifier_unavailable'):
                observed.append(('access_approval_validation_unavailable', 0))
            if self.cycle_events:
                self.cycle_access_counts[access['classification']] += 1
                # Bounded summary; every original event remains in raw.
                if len(self.cycle_access) < 64:
                    self.cycle_access.append(access)
        if self.cycle_events:
            self.cycle_findings.extend(observed)
        if event['type'] == 'kernel_diagnostics':
            self.cycle_observed = True
        errors = [reason for reason, _ in observed if reason.endswith('incomplete') or reason in
                  ('coverage_gap', 'object_callback_probe_inconclusive', 'handle_post_context_unavailable', 'target_exit',
                   'access_approval_validation_unavailable')]
        if event['type'] == 'driver_cross_view_status' and event.get('outcome') == 'inconclusive':
            errors.append('driver_cross_view_inconclusive')
        if event['type'] == 'kernel_thread_snapshot' and (event.get('status') != 0 or event.get('lookup_failed', 0)):
            errors.append('kernel_thread_snapshot_incomplete')
        if self.cycle_events:
            self.cycle_errors.extend(errors)
        for reason, score in observed:
            # Rate-limit common events, retaining every raw record. No cumulative ban score.
            key = (reason, event.get('actor_pid'), event.get('target_pid'), event.get('path'),
                   event.get('base'), event.get('tid'), event.get('create_time'), event.get('start'),
                   event.get('actor_create_time'))
            now = time.monotonic()
            if now - self.last.get(key, -1000) < 5:
                continue
            if len(self.last) > 10000:
                self.last = {k:v for k,v in self.last.items() if now-v < 5}
            self.last[key] = now
            record = self.common_event(scoring_event, [reason], score, valid=not bool(errors))
            record['evidence']['measurement_errors'] = errors
            if access and access['classification'] == 'unresolved' and (score > 0 or not errors):
                record['evidence']['status'] = 'SUSPICIOUS'
                record['evidence']['access_conclusion'] = 'sensitive_access_observed_approval_unresolved'
            self.publish(record)
            print(f'[{score}] {reason}', flush=True)
        if self.cycle_events and event['type'] == 'sensor_cycle':
            positive = [(reason, score) for reason, score in self.cycle_findings if score > 0]
            valid = self.cycle_observed and not self.cycle_errors
            score = max((score for _, score in positive), default=0)
            record = self.common_event(event, sorted({reason for reason, _ in positive}), score, valid)
            record['evidence'].update(sample_kind='sensor_cycle', measurement_errors=sorted(set(self.cycle_errors)),
                                      scope='configured_sensors_observed_this_cycle',
                                      access_approvals=list(self.cycle_access),
                                      access_approval_counts=dict(self.cycle_access_counts))
            if all(reason.startswith('sensitive_handle_') for reason, _ in positive) and \
                    self.cycle_access_counts['unresolved'] and (score > 0 or valid):
                record['evidence']['status'] = 'SUSPICIOUS'
            if self.cycle_errors:
                record['reasons'] = sorted(set(record['reasons']) | set(self.cycle_errors))
            if not valid and not record['reasons']:
                record['reasons'] = ['Observation Unavailable']
            self.publish(record)
            self.cycle_findings.clear()
            self.cycle_access.clear()
            self.cycle_access_counts = {'approved_observation': 0, 'unresolved': 0}
            self.cycle_errors.clear()
            self.cycle_observed = False
    def close(self):
        if self.telemetry:
            try:
                flushed, closed = self.telemetry.close()
                self.raw.write(json.dumps({'type': 'server_flush', 'complete': flushed})+'\n')
                self.raw.write(json.dumps({'type': 'server_shutdown', 'complete': closed})+'\n')
            except Exception as exc:
                self.raw.write(json.dumps({'type': 'server_close_error', 'error_type': type(exc).__name__})+'\n')
        self.raw.close()
        self.common.close()
        self.status_report = self.feature_status.report()
        (self.directory/'feature_status.json').write_text(json.dumps(self.status_report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (self.directory/'feature_status.md').write_text(render_table(self.status_report),encoding='utf-8')

def watch(args):
    from .windows import Windows, Driver
    diag = StartupDiagnostics(args.out)
    config = diag.call('config_read', lambda: validate_config(json.loads(Path(args.config).read_text(encoding='utf-8-sig'))))
    api = diag.call('windows_api_init', Windows)
    privilege = diag.call('debug_privilege', api.enable_debug_privilege)
    kernel_only = args.kernel_only
    if kernel_only and (args.mode != 'observe' or args.expected_image):
        raise ValueError('--kernel-only supports observe without --expected-image')
    process, created, image = (None, 0, None) if kernel_only else diag.call('target_process_query', api.process, args.pid)
    driver = worker = log = beat_thread = None
    excluded_user_events = 0
    stop = threading.Event()
    beat_errors = queue.Queue()
    def heartbeat():
        while not stop.wait(1):
            try:
                driver.call(P.IO_HEARTBEAT)
            except Exception as ex:
                beat_errors.put(str(ex))
                return
    try:
        if args.expected_image and image.casefold() != args.expected_image.casefold():
            raise ValueError(f'image mismatch: actual {image}')
        if args.mode == 'enforce' and not args.expected_image:
            raise ValueError('enforce requires --expected-image with the full executable path')
        if process is not None and api.wait(process, 0) != 258:
            raise RuntimeError('target is not running')
        driver = diag.call('driver_device_open', Driver, api)
        initial = diag.call('driver_status_query', driver.status)
        if initial['capabilities'] & 31 != 31:
            raise RuntimeError('required callbacks/diagnostics unavailable')
        if args.thread_interval and not initial['capabilities'] & 32:
            raise RuntimeError('kernel thread sensor unavailable: load v0.2 SYS on Windows 10 build 19045; '
                               '--thread-interval 0 explicitly disables this sensor')
        # Discard pre-session queue explicitly, retaining its count in metadata.
        stale = dropped = 0
        for _ in range(64):
            events, lost = driver.events()
            stale += len(events)
            dropped += lost
            if not events:
                break
        classifier = None
        if not kernel_only:
            from .approved_access import ApprovedAccess, WindowsInspector, digest
            inspector = WindowsInspector(api)
            try:
                runtime = inspector(os.getpid(), command=False)
                runtime['sha256'] = digest(runtime['image'])
                runtime['registered_interpreters'] = [sys.executable, getattr(sys, '_base_executable', sys.executable)]
                classifier = ApprovedAccess(args.session_id, args.player_id, args.pid, created,
                                            inspector, runtime)
            except Exception:
                # Sensor still runs; missing approval verification must never exempt accesses.
                classifier = None
        log = diag.call('local_log_and_shared_init', Log, Path(args.out), args.session_id, args.player_id, config,
                  t0=getattr(args, 't0', None), telemetry=getattr(args, 'telemetry', 'off'),
                  cycle_events=getattr(args, 'cycle_events', False), access_classifier=classifier)
        log.write({'type': 'session_start', 'pid': args.pid, 'image': image, 'create_time': created,
            'mode': args.mode, 'debug_privilege': privilege, 'config': config,
            'agent_version': '0.2.3', 'capabilities': initial['capabilities'],
            'diagnostics_interval': args.interval, 'code_per_cycle': args.code_per_cycle,
            'verify_signatures': args.verify_signatures, 'cross_view_status_events': True,
            'direct_kernel_memory_read_sensor': 'not_implemented',
            'scope': 'kernel_only' if kernel_only else 'kernel_and_game',
            'thread_interval': args.thread_interval,
            'callback_health_enabled': not kernel_only and not args.no_callback_health,
            'discarded_pre_session_events': stale, 'pre_session_dropped': dropped,
            'notes': ['load-time baseline is not trusted-clean', 'scores do not establish cheating',
                      'kernel-only has no game handle protection or OB health probe' if kernel_only else
                      'game image/thread events begin after attachment; no preexisting DLL inventory']})
        if kernel_only:
            driver.policy()  # Explicitly clear game policy on this exclusive device session.
        else:
            driver.policy(args.pid, 2 if args.mode == 'enforce' else 1, created)
        worker = FileWorker(api, args.verify_signatures)
        if not kernel_only:
            beat_thread = threading.Thread(target=heartbeat, daemon=True)
            beat_thread.start()
        if kernel_only:
            print(f'Kernel-only monitoring | No game PID required | Output: {args.out}', flush=True)
        else:
            print(f'Attached: PID {args.pid} | {image}\nMode: {args.mode} | Output: {args.out}', flush=True)
        cross = CrossView()
        origins = ThreadOrigins()
        health = CallbackHealth(os.getpid(), args.pid)
        status = driver.status()
        next_thread = next_probe = 0
        next_diag, next_status, last_skipped = 0, 0, 0
        seen_drivers = set()
        while True:
            if not beat_errors.empty():
                raise RuntimeError('heartbeat failed: '+beat_errors.get_nowait())
            wait = api.wait(process, 0) if process is not None else 258
            if wait == 0:
                log.write({'type': 'target_exit', 'target_pid': args.pid, 'source': 'retained_process_handle'})
                break
            if wait != 258:
                raise RuntimeError(f'process wait failed: {wait}')
            drained = False
            for _ in range(16):
                events, lost = driver.events()
                if lost:
                    health.invalidate()
                    log.write({'type': 'coverage_gap', 'reason': 'kernel_ring_overflow', 'dropped': lost})
                for event in events:
                    if kernel_only and event['type'] != 'kernel_image':
                        excluded_user_events += 1
                        continue
                    health.observe(event)
                    log.write(event)
                    if event['type'] in ('game_image', 'kernel_image') and not event['flags'] & 16:
                        worker.submit(event)
                if len(events) < 64:
                    drained = True
                    break
            health_result = health.finish(time.monotonic(), drained)
            if health_result:
                log.write(health_result)
            while True:
                try:
                    log.write(worker.results.get_nowait())
                except queue.Empty:
                    break
            if worker.skipped != last_skipped:
                log.write({'type': 'coverage_gap', 'reason': 'file_analysis_queue_full', 'dropped': worker.skipped-last_skipped})
                last_skipped = worker.skipped
            now = time.monotonic()
            if now >= next_status:
                status = driver.status()
                log.write({'type': 'driver_status', **status})
                if kernel_only and (status['pid'] or status['mode']):
                    raise RuntimeError('unexpected game protection state in kernel-only mode')
                if not kernel_only and (status['pid'] != args.pid or status['create_time'] != created or not status['lease_active']):
                    raise RuntimeError('protection state changed or heartbeat lease expired')
                next_status = now + 2
            if now >= next_diag:
                cycle_started = time.monotonic()
                before = driver.diagnostics()
                threads = None
                if args.thread_interval and now >= next_thread:
                    threads = driver.thread_scan()
                    next_thread = now + args.thread_interval
                user = None
                try:
                    user = api.drivers()
                except (OSError, RuntimeError) as ex:
                    log.write({'type': 'coverage_gap', 'reason': 'psapi_unavailable', 'error': str(ex)})
                after = driver.diagnostics()
                log.write(before)
                log.write({'type': 'psapi_snapshot', 'modules': user})
                log.write(after)
                if threads is not None:
                    log.write(threads)
                    for event in origins.compare(before, threads, after):
                        log.write(event)
                for event in cross.compare(before, user, after):
                    log.write(event)
                log.write(dict(cross.last_status))
                for _ in range(args.code_per_cycle):
                    log.write(driver.code_scan())
                current = set()
                if after['module_status'] == 0:
                    for module in after['modules']:
                        key = (module['base'], module['path'])
                        current.add(key)
                        if key not in seen_drivers:
                            event = {'type': 'driver_snapshot', **module}
                            log.write(event)
                            worker.submit(event)
                    seen_drivers = current
                next_diag = now + args.interval
                log.write({'type': 'sensor_cycle', 'duration_ms': round((time.monotonic()-cycle_started)*1000, 3)})
            now = time.monotonic()
            if not kernel_only and not args.no_callback_health and now >= next_probe and health.pending is None:
                stamp = time.time_ns() // 100 + 116444736000000000
                health.begin(now, stamp, status['generation'], driver.probe_callback(args.pid))
                next_probe = now + 15
            time.sleep(.1)
    except KeyboardInterrupt:
        print('Stopping...', flush=True)
    except Exception as ex:
        if log:
            log.write({'type': 'coverage_gap', 'reason': 'agent_error', 'error': str(ex)})
        raise
    finally:
        try:
            import signal
            signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        except (AttributeError, ValueError, OSError):
            pass
        stop.set()
        if beat_thread:
            beat_thread.join(timeout=3)
        if driver:
            try:
                driver.policy()
            except OSError as ex:
                print(f'Disarm IOCTL failed; closing device: {ex}', file=sys.stderr)
            # Cleanup IRP clears policy; unloaded service is a separate explicit operation.
            driver.close()
        if worker:
            pending = worker.close()
            if log and pending:
                log.write({'type': 'coverage_gap', 'reason': 'file_analysis_pending_on_stop', 'pending': pending})
            if log:
                while not worker.results.empty():
                    log.write(worker.results.get_nowait())
        if process is not None:
            api.close(process)
        if log:
            log.write({'type': 'session_end', 'common_event_count': log.count,
                       'excluded_user_events': excluded_user_events})
            log.close()
            print(render_table(log.status_report),flush=True)

def replay(args):
    config = validate_config(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
    counts = {}
    with Path(args.raw).open(encoding='utf-8') as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                for reason, score in findings(json.loads(line), config):
                    counts[reason] = counts.get(reason, 0) + 1
            except (ValueError, KeyError, TypeError) as ex:
                raise ValueError(f'line {line_number}: {ex}') from ex
    print(json.dumps({'raw_finding_counts_without_rate_limit': counts}, ensure_ascii=False, indent=2))

def main():
    import signal
    try:
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    except (AttributeError, ValueError, OSError):
        pass
    parser = argparse.ArgumentParser(description='KernelSentinel research anti-cheat')
    sub = parser.add_subparsers(dest='command', required=True)
    w = sub.add_parser('watch')
    target = w.add_mutually_exclusive_group(required=True)
    target.add_argument('--pid', type=int)
    target.add_argument('--kernel-only', action='store_true',
                        help='kernel sensors without a game PID; no game policy or OB health probe')
    w.add_argument('--mode', choices=('observe', 'enforce'), default='observe')
    w.add_argument('--expected-image')
    w.add_argument('--config', default='config/policy.json')
    w.add_argument('--out', required=True, help='new output directory; never overwrites previous sessions')
    w.add_argument('--session-id', required=True)
    w.add_argument('--player-id', default='player_local')
    w.add_argument('--t0', help='Launcher common Unix epoch seconds')
    w.add_argument('--telemetry', choices=('managed', 'off'), default='off')
    w.add_argument('--install-driver', action='store_true', help='Install/start the signed approved SYS before attachment')
    w.add_argument('--prepare-test-environment', action='store_true',
                   help='TEST ONLY: trust the pinned test certificate and enable TESTSIGNING; may require reboot')
    w.add_argument('--driver-path', help='Approved SYS path (or GZZ_KERNEL_DRIVER_PATH)')
    w.add_argument('--driver-sha256', help='Pinned approved hash (or GZZ_KERNEL_DRIVER_SHA256)')
    w.set_defaults(cycle_events=True)
    w.add_argument('--interval', type=float, default=5)
    w.add_argument('--verify-signatures', action='store_true')
    w.add_argument('--thread-interval', type=float, default=15,
                   help='kernel thread origin scan interval (seconds); 0 explicitly disables')
    w.add_argument('--no-callback-health', action='store_true', help='disable read-only OB callback health probes')
    w.add_argument('--code-per-cycle', type=int, default=2, help='0 disables; default 2 kernel modules per diagnostics cycle')
    r = sub.add_parser('replay')
    r.add_argument('--raw', required=True)
    r.add_argument('--config', default='config/policy.json')
    args = parser.parse_args()
    try:
        if args.command == 'watch':
            if not 1 <= args.interval <= 3600:
                raise ValueError('--interval must be between 1 and 3600 seconds')
            if not 0 <= args.code_per_cycle <= 8:
                raise ValueError('--code-per-cycle must be between 0 and 8')
            if args.thread_interval != 0 and not 5 <= args.thread_interval <= 3600:
                raise ValueError('--thread-interval must be 0 or between 5 and 3600 seconds')
            SessionClock(args.t0)  # Fail before installing a driver on invalid clock input.
            if args.install_driver:
                from .driver_setup import ensure_driver
                StartupDiagnostics(args.out).call('approved_driver_setup', ensure_driver, args.driver_path, args.driver_sha256,
                                                 prepare_test=args.prepare_test_environment)
            elif args.prepare_test_environment:
                raise ValueError('--prepare-test-environment requires --install-driver')
            watch(args)
        else:
            replay(args)
        return 0
    except (OSError, RuntimeError, ValueError) as ex:
        print(f'KernelSentinel error: {ex}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
