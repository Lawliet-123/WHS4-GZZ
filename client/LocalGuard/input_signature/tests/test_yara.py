"""YARA 규칙·프로세스 식별·검사 실패 처리의 회귀 테스트.

임시 프로세스/합성 marker는 탐지 파이프라인을 재현하기 위한 것이며 실제
게임에서의 정상·핵 분류 정확도를 측정하지 않는다.
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_events import ReplaySession
from yara_scanner import (load_rules, scan_once, scan_loaded_autopaint_bridge_once,
                          scan_external_python_candidates,
                          select_external_python_candidates, candidate_batch,
                          main, ROOT, scanner_stale_window_ms)
from tests.fixture_sessions import ControlledTarget, generate, MARKER

HAS_YARA = importlib.util.find_spec('yara') is not None


@unittest.skipUnless(HAS_YARA,'yara-python not installed')
class Tests(unittest.TestCase):
    """0점의 의미, 민감 바이트 비공개, 후보 범위를 일관되게 검증한다."""
    def setUp(self):
        """각 테스트에 격리된 합성 세션과 가짜 프로세스를 준비한다."""
        self.temp = tempfile.TemporaryDirectory()
        self.session = ReplaySession(self.temp.name,'test',data_origin='controlled_fixture',modules=['localguard_yara'])
        self.forwarded = []
        self.session.event_sink = self.forwarded.append
        self.process = SimpleNamespace(pid=123,check=lambda:None)
        self.raw = io.StringIO()
    def tearDown(self): self.session.finish(); self.temp.cleanup()
    def test_success_no_match_emits_zero(self):
        """검사가 성공했을 때만 범위를 명시한 0점 Event를 남긴다."""
        rules = SimpleNamespace(match=lambda **kw:[])
        event = scan_once(rules,self.process,self.session,self.raw)
        self.assertEqual(event['raw_score'],0)
        self.assertFalse(event['evidence']['active_cheat_proven'])
        transmitted = dict(event)
        transmitted.pop('_matched_strings_for_console')
        self.assertEqual(self.forwarded, [transmitted])
    def test_failure_no_zero(self):
        """접근 거부는 검사 실패로 기록하고 정상 0점으로 바꾸지 않는다."""
        def fail(**kw): raise PermissionError('denied')
        event = scan_once(SimpleNamespace(match=fail),self.process,self.session,self.raw)
        self.assertIsNone(event)
        self.assertEqual(self.session.counts,{})
        self.assertIn('scan_error',self.raw.getvalue())
        self.assertEqual(self.forwarded, [])
    def test_timeout_no_zero(self):
        """YARA 시간 제한에 걸린 검사는 불완전하므로 점수를 내지 않는다."""
        import yara
        def fail(**kw): raise yara.TimeoutError('timeout')
        self.assertIsNone(scan_once(SimpleNamespace(match=fail),self.process,self.session,self.raw))
        self.assertEqual(self.forwarded, [])
    def test_yara_warning_not_success(self):
        """YARA 부분 검사 경고가 있으면 겉보기 빈 일치도 정상으로 취급하지 않는다."""
        def partial(**kw):
            kw['warnings_callback'](1,'too many matches')
            return []
        self.assertIsNone(scan_once(SimpleNamespace(match=partial),self.process,self.session,self.raw))
        self.assertEqual(self.forwarded, [])
    def test_identity_change_no_score(self):
        """스캔 중 PID의 대상이 바뀌면 해당 결과를 버린다."""
        calls = []
        def check():
            calls.append(1)
            if len(calls)>1: raise RuntimeError('process changed')
        self.process.check=check
        self.assertIsNone(scan_once(SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw))
    def test_no_matched_bytes_in_log(self):
        """매칭한 원시 메모리 문자열은 raw 로그나 공통 Event에 노출되지 않는다."""
        import yara
        rules = yara.compile(source='rule r { meta: score=3 strings: $a="TOP_SECRET" condition: $a }')
        matches = rules.match(data=b'TOP_SECRET')
        event = scan_once(SimpleNamespace(match=lambda **kw:matches),self.process,self.session,self.raw)
        self.assertEqual(event['raw_score'],3)
        self.assertNotIn('TOP_SECRET',self.raw.getvalue())
        self.assertNotIn('TOP_SECRET',json.dumps(event))
    def test_known_autopaint_literals_are_reported_without_arbitrary_memory(self):
        """허용된 고정 문자열만 표시하고 주변의 사적 메모리는 출력하지 않는다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        sample = b'|'.join([
            b'mesh_first_pipeline', 'mesh-first-uv-color-'.encode('utf-16le'),
            b'ServerCompactPaintBatch', b'PaintAtUVWithBrush',
            b'mesh-first paint requires the async queued dispatcher',
            b'mesh-first paint completed',
            b'mesh-first single-stroke ServerPaintBatch stream prepared',
            b'PRIVATE_UNRELATED_MEMORY',
        ])
        matches = rules.match(data=sample,fast=True)
        event = scan_once(SimpleNamespace(match=lambda **kw:matches),
                          self.process,self.session,self.raw)
        record = json.loads(self.raw.getvalue())
        displayed = {item['text'] for hit in record['matches']
                     for item in hit['matched_strings']}
        self.assertEqual(len(displayed),7)
        self.assertIn('mesh-first-uv-color-',displayed)
        self.assertIn('ServerCompactPaintBatch',displayed)
        self.assertIn('mesh_first_pipeline',' '.join(event['_matched_strings_for_console']))
        self.assertNotIn('PRIVATE_UNRELATED_MEMORY',self.raw.getvalue())
        self.assertNotIn('matched_strings',json.dumps(event['evidence']))
    def test_same_rule_name_with_different_literal_is_not_disclosed(self):
        """규칙 이름만 같고 내용이 다른 문자열은 허용 목록 출력에서 제외한다."""
        import yara
        rules = yara.compile(source='rule MECCHA_PRReady_AutoPaint_Bridge { '
                             'meta: score=3 strings: $pipeline="PRIVATE_VALUE" '
                             'condition: $pipeline }')
        matches = rules.match(data=b'PRIVATE_VALUE')
        scan_once(SimpleNamespace(match=lambda **kw:matches),
                  self.process,self.session,self.raw)
        record = json.loads(self.raw.getvalue())
        self.assertEqual(record['matches'][0]['matched_strings'],[])
        self.assertNotIn('PRIVATE_VALUE',self.raw.getvalue())
    def test_loaded_bridge_memory_positive(self):
        """로드된 bridge의 매핑 메모리에서 실제 규칙 일치를 찾아낸다."""
        import yara
        rules = yara.compile(source='rule bridge { meta: score=3 strings: $a="SECRET_BRIDGE_PATTERN" condition: $a }')
        module = {'name':'meccha-direct-bridge-v1-test.dll','path':'C:\\test.dll',
                  'base_address':0x1000,'size':64}
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',return_value=b'SECRET_BRIDGE_PATTERN') as read:
            event = scan_loaded_autopaint_bridge_once(rules,self.process,self.session,self.raw)
        self.assertEqual(event['raw_score'],3)
        self.assertEqual(event['evidence']['scope'],'loaded_autopaint_bridge_module_memory')
        self.assertEqual(event['evidence']['module_name'],module['name'])
        self.assertNotIn('SECRET_BRIDGE_PATTERN',self.raw.getvalue())
        read.assert_called_once_with(self.process.pid,module)
    def test_loaded_bridge_no_hit_defers_to_full_scan(self):
        """기본 모드의 bridge 불일치는 게임 전체 검사로 넘어간다."""
        module = {'name':'runtime-bridge.dll','path':'C:\\test.dll',
                  'base_address':0x1000,'size':64}
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',return_value=b'no match'):
            event = scan_loaded_autopaint_bridge_once(
                SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw)
        self.assertIsNone(event)
        self.assertEqual(self.session.counts,{})
        self.assertEqual(self.raw.getvalue(),'')
    def test_loaded_bridge_read_error_defers_to_full_scan(self):
        """bridge 읽기 실패도 기본 모드에서 전체 검사 기회를 남긴다."""
        module = {'name':'runtime-bridge.dll','path':'C:\\test.dll',
                  'base_address':0x1000,'size':64}
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',side_effect=PermissionError('denied')):
            event = scan_loaded_autopaint_bridge_once(
                SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw)
        self.assertIsNone(event)
        self.assertEqual(self.session.counts,{})
        self.assertIn('module_scan_error',self.raw.getvalue())
    def test_bridge_only_absent_is_narrow_inventory_zero(self):
        """bridge-only의 DLL 부재 0점은 모듈 목록 범위에만 한정된다."""
        with patch('yara_scanner.list_process_modules',return_value=[
                {'name':'game.dll','path':'C:\\game.dll','base_address':0x1000,'size':64}]), \
             patch('yara_scanner.read_process_module') as read:
            event = scan_loaded_autopaint_bridge_once(
                SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw,
                bridge_only=True)
        read.assert_not_called()
        self.assertEqual(event['raw_score'],0)
        self.assertEqual(event['evidence']['scope'],'known_autopaint_bridge_module_inventory')
        self.assertEqual(event['evidence']['scan_method'],'module_inventory')
        self.assertIn('not_proven_clean',event['evidence']['zero_means'])
        self.assertEqual(json.loads(self.raw.getvalue())['module_inventory_count'],1)
    def test_bridge_only_loaded_no_match_is_module_memory_zero(self):
        """bridge-only 불일치 0점이 게임 전체 정상처럼 기록되지 않는다."""
        module = {'name':'runtime-bridge.dll','path':'C:\\test.dll',
                  'base_address':0x1000,'size':64}
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',return_value=b'no match'):
            event = scan_loaded_autopaint_bridge_once(
                SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw,
                bridge_only=True)
        self.assertEqual(event['raw_score'],0)
        self.assertEqual(event['evidence']['scope'],'loaded_autopaint_bridge_module_memory')
        self.assertIn('not_proven_clean',event['evidence']['zero_means'])
    def test_bridge_only_loaded_known_literals_are_positive(self):
        """bridge-only에서도 알려진 DLL 메모리 조합은 양성으로 기록한다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        module = {'name':'meccha-direct-bridge-v1-test.dll','path':'C:\\bridge.dll',
                  'base_address':0x1000,'size':64}
        data = b'|'.join([
            b'mesh_first_pipeline', 'mesh-first-uv-color-'.encode('utf-16le'),
            b'ServerCompactPaintBatch', b'PaintAtUVWithBrush',
        ])
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',return_value=data):
            event = scan_loaded_autopaint_bridge_once(
                rules,self.process,self.session,self.raw,bridge_only=True)
        self.assertEqual(event['raw_score'],3)
        self.assertEqual(event['evidence']['scope'],'loaded_autopaint_bridge_module_memory')
        self.assertIn('MECCHA_PRReady_AutoPaint_Bridge',event['evidence']['matched_rules'])
    def test_bridge_only_read_error_is_not_zero(self):
        """bridge-only의 부분 읽기·오류는 정상 0점이 아니다."""
        module = {'name':'runtime-bridge.dll','path':'C:\\test.dll',
                  'base_address':0x1000,'size':64}
        with patch('yara_scanner.list_process_modules',return_value=[module]), \
             patch('yara_scanner.read_process_module',side_effect=PermissionError('denied')):
            event = scan_loaded_autopaint_bridge_once(
                SimpleNamespace(match=lambda **kw:[]),self.process,self.session,self.raw,
                bridge_only=True)
        self.assertIsNone(event)
        self.assertEqual(self.session.counts,{})
        self.assertIn('scan_error',self.raw.getvalue())
        self.assertIn('yara_scan_failed',self.session.errors)
    def test_repository_rules_match_each_known_family_sample(self):
        """팀 저장소에서 뽑은 각 핵 계열의 알려진 조합에 규칙이 일치한다."""
        rules,info = load_rules([ROOT/'rules/repository_cheats.yar'])
        wide = lambda value: value.encode('utf-16le')
        samples = {
            'MECCHA_Repo_AutoPaint_Bridge': b'|'.join([
                b'mesh-first paint requires the async queued dispatcher',
                b'mesh-first paint completed',
                b'mesh-first single-stroke ServerPaintBatch stream prepared',
            ]),
            'MECCHA_PRReady_AutoPaint_Bridge': b'|'.join([
                b'mesh_first_pipeline',
                wide('mesh-first-uv-color-'),
                b'ServerCompactPaintBatch',
                b'PaintAtUVWithBrush',
            ]),
            'MECCHA_Repo_AutoPaint_Direct_Injector': b'|'.join([
                b'direct_injector_abi_ready', b'bridge_hash_mismatch',
                b'bridge_start_export_not_found',
                b'usage: runtime-injector.exe --direct <pid> <creation-filetime-utc> <expected-exe-path> <bridge-path>',
            ]),
            'MECCHA_Repo_Simple_LoadLibrary_Injector': b'|'.join([
                wide('usage: runtime-injector.exe <process.exe> <bridge.dll>'),
                wide('injected pid='),
                wide('LoadLibraryW failed in target process'),
            ]),
            'MECCHA_Repo_AutoPaint_Controller': b'|'.join([
                b'Meccha Auto Paint', b'f10_mesh_first_paint',
                b'mesh_first_paint', b'meccha-direct-bridge-v1-',
            ]),
            'MECCHA_Repo_GodMode_Host_DLL': b'|'.join([
                b'GodModeHost402 CreateThread failed; win32=',
                b'ProcessEvent hook installed; total=',
                b'blocked server death call; total=',
                wide('GodModeHost402.on'),
            ]),
            'MECCHA_Repo_GodMode_UE4SS_Lua': b'|'.join([
                b'[GodMode] %s\\n', b'ON - local pawn protected: ',
                b'usage: godmode [on|off|status]',
                b"loaded; F6=toggle, F7=status, or use 'godmode on|off|status'",
            ]),
            'MECCHA_Repo_NoClip_UE4SS_Lua': b'|'.join([
                b'[MecchaNoclip] main.lua loaded', b'nocliptest',
                b'[MecchaNoclip] Original Collision = ',
                b'[MecchaNoclip] NOCLIP ON',
            ]),
            'MECCHA_Repo_Whistle_Spoofing_DLL': b'|'.join([
                b'whistle.dll - ProcessEvent Logger',
                b'C:/Dumper-7/whistle-pe-log.txt', b'Provoaction_HIKAKIN',
            ]),
            'MECCHA_Repo_Hide_Anywhere_Source': b'|'.join([
                b'namespace features::hide_anywhere',
                b'offsets::cleon_survivor::FilledValue',
                b'IsInViewCheckLate', b'EnableDistanceGimmick',
            ]),
            'MECCHA_Repo_Aimbot_Python': b'|'.join([
                b'Activation: hold F8; release it to stop writes',
                b'Synthetic maximum angular speed (deg/s)',
                b'Verification settings: radius=', b'readback_error_pitch',
            ]),
            'MECCHA_Repo_ESP_Python': b'|'.join([
                b'MECCHA // VISION',
                b'Pause every visual layer without changing its settings',
                b'MecchaESPReader', b'MecchaESPSkeleton',
                b'Start the game, wait until the lobby has loaded, then run esp.py again.',
            ]),
        }
        self.assertEqual(info['rule_count'], len(samples))
        self.assertFalse(info['test_rules_present'])
        for expected,data in samples.items():
            with self.subTest(rule=expected):
                self.assertEqual({m.rule for m in rules.match(data=data)}, {expected})

    def test_ruleset_id_is_stable_for_same_rule_content(self):
        """절대 경로가 달라도 같은 basename+내용이면 같은 ruleset ID다."""
        first_dir = Path(self.temp.name) / 'first'
        second_dir = Path(self.temp.name) / 'second'
        first_dir.mkdir()
        second_dir.mkdir()

        source = (
            'rule stable_rule { '
            'meta: score=2 '
            'strings: $a="stable-marker" '
            'condition: $a }'
        )

        first = first_dir / 'same.yar'
        second = second_dir / 'same.yar'
        first.write_text(source, encoding='utf-8')
        second.write_text(source, encoding='utf-8')

        _, first_info = load_rules([first])
        _, second_info = load_rules([second])

        self.assertEqual(
            first_info['ruleset_id'],
            second_info['ruleset_id'],
        )
        self.assertTrue(
            first_info['ruleset_id'].startswith('sha256:')
        )

    def test_success_event_carries_ruleset_identity_when_provided(self):
        """중앙 Event에 raw score와 함께 실제 ruleset identity가 전달된다."""
        rules, info = load_rules(
            [ROOT / 'rules' / 'repository_cheats.yar']
        )
        info = dict(info)
        info['source'] = 'repository_default'

        event = scan_once(
            SimpleNamespace(match=lambda **kw: []),
            self.process,
            self.session,
            self.raw,
            ruleset=info,
        )

        ruleset = event['evidence']['ruleset']

        self.assertEqual(
            ruleset['id'],
            info['ruleset_id'],
        )
        self.assertEqual(
            ruleset['source'],
            'repository_default',
        )
        self.assertEqual(
            ruleset['rule_count'],
            info['rule_count'],
        )
        self.assertFalse(
            ruleset['test_rules_present']
        )
        self.assertEqual(
            ruleset['files'],
            info['files'],
        )

    def test_event_without_ruleset_keeps_legacy_compatibility(self):
        """직접 호출하는 과거/테스트 경로는 ruleset 필드 없이도 동작한다."""
        event = scan_once(
            SimpleNamespace(match=lambda **kw: []),
            self.process,
            self.session,
            self.raw,
        )

        self.assertNotIn(
            'ruleset',
            event['evidence'],
        )

    def test_autopaint_bridge_requires_full_combination(self):
        """Auto Paint 단어 하나만으로 양성 처리되지 않도록 조합을 요구한다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        a = b'mesh-first paint requires the async queued dispatcher'
        b = b'mesh-first paint completed'
        c = b'mesh-first single-stroke ServerPaintBatch stream prepared'
        self.assertFalse(rules.match(data=a))
        self.assertFalse(rules.match(data=a+b))
        self.assertEqual({m.rule for m in rules.match(data=a+b+c)},
                         {'MECCHA_Repo_AutoPaint_Bridge'})

    def test_pr_ready_bridge_requires_full_combination(self):
        """PR-ready Auto Paint 규칙도 모든 핵심 리터럴이 함께 있어야 일치한다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        parts = [b'mesh_first_pipeline', 'mesh-first-uv-color-'.encode('utf-16le'),
                 b'ServerCompactPaintBatch', b'PaintAtUVWithBrush']
        expected = {'MECCHA_PRReady_AutoPaint_Bridge'}
        for index in range(len(parts)):
            with self.subTest(missing=index):
                self.assertNotIn('MECCHA_PRReady_AutoPaint_Bridge',
                                 {m.rule for m in rules.match(data=b'|'.join(
                                     part for offset,part in enumerate(parts) if offset != index))})
        self.assertEqual({m.rule for m in rules.match(data=b'|'.join(parts))}, expected)
    @unittest.skipUnless(sys.platform=='win32','Windows process test')
    def test_pr_ready_bridge_matches_controlled_process_memory(self):
        """통제된 프로세스 메모리에 올린 bridge marker를 PID 스캔으로 찾는다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        marker = b'|'.join([
            b'mesh_first_pipeline', 'mesh-first-uv-color-'.encode('utf-16le'),
            b'ServerCompactPaintBatch', b'PaintAtUVWithBrush',
        ])
        with ControlledTarget() as target:
            target.command('on', marker=marker)
            positive = scan_once(rules, target.process, self.session, self.raw, timeout=10)
            self.assertIsNotNone(positive)
            self.assertIn('MECCHA_PRReady_AutoPaint_Bridge',
                          positive['evidence']['matched_rules'])
            target.command('off')
            negative = scan_once(rules, target.process, self.session, self.raw, timeout=10)
            self.assertIsNotNone(negative)
            self.assertNotIn('MECCHA_PRReady_AutoPaint_Bridge',
                             negative['evidence']['matched_rules'])

    def test_generic_injection_api_names_do_not_match(self):
        """일반적인 주입 API 이름만으로 알려진 핵 규칙이 맞지 않는다."""
        rules,_ = load_rules([ROOT/'rules/repository_cheats.yar'])
        generic = b'OpenProcess VirtualAllocEx WriteProcessMemory CreateRemoteThread LoadLibraryW ProcessEvent'
        self.assertFalse(rules.match(data=generic))
    def test_invalid_score_rejected(self):
        """규칙 점수가 허용 범위를 벗어나면 파일을 컴파일해도 사용하지 않는다."""
        path = Path(self.temp.name)/'bad.yar'
        path.write_text('rule bad { meta: score=99 condition: true }',encoding='utf-8')
        with self.assertRaises(ValueError): load_rules([path])
    def test_includes_disabled(self):
        """기록되지 않은 외부 YARA include가 규칙에 끼어들지 못한다."""
        path = Path(self.temp.name)/'bad.yar'
        path.write_text('include "elsewhere.yar"',encoding='utf-8')
        with self.assertRaises(Exception): load_rules([path])
    def test_cli_missing_game_emits_no_false_clean_result(self):
        """게임이 없으면 검사를 실패로 남기고 정상 0점 Event를 만들지 않는다."""
        with patch('yara_scanner.find_processes',return_value=[]), redirect_stdout(io.StringIO()):
            code = main(['--log-root',self.temp.name,'--session-id','no_game','--label','normal'])
        self.assertEqual(code,1)
        manifest = json.loads((Path(self.temp.name)/'no_game/manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(manifest['comparison_ready'])
        self.assertEqual(manifest['event_counts'],{})
        self.assertTrue(manifest['collection_errors'])
        self.assertEqual(manifest['scan_interval_seconds'], 60)
        self.assertEqual(manifest['scan_timeout_seconds'], 45)
        heartbeat_rows = [json.loads(line) for line in
                          (Path(self.temp.name)/'no_game/raw/heartbeat.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(heartbeat_rows[-1]['components']['localguard_input_signature']['stale_after_ms'],
                         110000)
    def test_scanner_heartbeat_freshness_covers_full_scan_schedule(self):
        """YARA 제한 시간과 주기를 포함하는 stale 한도를 사용한다."""
        self.assertEqual(scanner_stale_window_ms(60, 45, 5), 110000)
        self.assertEqual(scanner_stale_window_ms(0.1, 3, 5), 15000)
    def test_external_candidates_are_same_session_python_only(self):
        """게임과 같은 Windows 세션의 Python 후보만 자동 선택한다."""
        rows = [
            {'pid': 10, 'name': 'PenguinHotel-Win64-Shipping.exe', 'parent_pid': 1},
            {'pid': 19, 'name': 'python.exe', 'parent_pid': 1},
            {'pid': 20, 'name': 'python.exe', 'parent_pid': 19},
            {'pid': 21, 'name': 'pythonw.exe', 'parent_pid': 20},
            {'pid': 22, 'name': 'python.exe', 'parent_pid': 1},
            {'pid': 30, 'name': 'other.exe', 'parent_pid': 1},
            {'pid': 40, 'name': 'python.exe', 'parent_pid': 1},
        ]
        def lookup(pid):
            if pid == 40: raise OSError('process exited')
            return 2 if pid == 22 else 1
        candidates, skipped = select_external_python_candidates(
            rows, game_pid=10, game_session_id=1, scanner_pid=20,
            session_lookup=lookup)
        self.assertEqual([row['pid'] for row in candidates], [21])
        self.assertEqual(skipped, [{'pid': 40, 'error_type': 'OSError'}])
    def test_external_candidate_batch_rotates(self):
        """한 주기의 후보 수를 제한하면서 다음 주기에는 이어서 검사한다."""
        rows = [{'pid': pid} for pid in (1, 2, 3, 4, 5)]
        first, cursor = candidate_batch(rows, 0, 2)
        second, cursor = candidate_batch(rows, cursor, 2)
        third, cursor = candidate_batch(rows, cursor, 2)
        self.assertEqual([row['pid'] for row in first + second + third],
                         [1, 2, 3, 4, 5, 1])
    def test_no_external_candidate_is_not_a_clean_scan(self):
        """Python 후보가 없다는 사실을 외부 핵이 없다는 0점 증거로 만들지 않는다."""
        game = SimpleNamespace(pid=10, check=lambda: None)
        output = io.StringIO()
        with patch('yara_scanner.list_processes', return_value=[]), \
             patch('yara_scanner.process_session_id', return_value=1), \
             redirect_stdout(output):
            cursor, failures = scan_external_python_candidates(
                SimpleNamespace(), game, self.session, self.raw,
                timeout=1, max_targets=4, cursor=0)
        records = [json.loads(line) for line in self.raw.getvalue().splitlines()]
        self.assertEqual((cursor, failures), (0, 0))
        self.assertEqual(records[0]['type'], 'candidate_discovery')
        self.assertFalse(records[0]['score_evaluated'])
        self.assertNotIn('scan_result', [record['type'] for record in records])
        self.assertIn('Python PID (0개): 없음', output.getvalue())
    def test_unreadable_external_candidate_is_not_zero(self):
        """후보 접근 실패는 기록하되 정상 0점으로 처리하지 않는다."""
        game = SimpleNamespace(pid=10, check=lambda: None)
        row = {'pid': 22, 'name': 'python.exe', 'parent_pid': 1}
        output = io.StringIO()
        with patch('yara_scanner.list_processes', return_value=[row]), \
             patch('yara_scanner.process_session_id', return_value=1), \
             patch('yara_scanner.ProcessIdentity', side_effect=PermissionError('denied')), \
             redirect_stdout(output):
            _, failures = scan_external_python_candidates(
                SimpleNamespace(), game, self.session, self.raw,
                timeout=1, max_targets=4, cursor=0)
        records = [json.loads(line) for line in self.raw.getvalue().splitlines()]
        self.assertEqual(failures, 1)
        self.assertIn('external_candidate_error', [record['type'] for record in records])
        self.assertNotIn('scan_result', [record['type'] for record in records])
        self.assertIn('이번 주기 검사 PID: 22', output.getvalue())
    @unittest.skipUnless(sys.platform == 'win32', 'Windows process test')
    def test_auto_external_scans_live_python_memory(self):
        """통제된 실제 Python 자식 프로세스 메모리를 자동 발견해 검사한다."""
        rules, _ = load_rules([ROOT/'rules/repository_cheats.yar'])
        marker = b'|'.join([
            b'MECCHA // VISION',
            b'Pause every visual layer without changing its settings',
            b'MecchaESPReader', b'MecchaESPSkeleton',
            b'Start the game, wait until the lobby has loaded, then run esp.py again.',
        ])
        with ControlledTarget() as target:
            target.command('on', marker=marker)
            row = {'pid': target.process.pid, 'name': 'python.exe',
                   'parent_pid': 1}
            game = SimpleNamespace(pid=999999, check=lambda: None)
            output = io.StringIO()
            with patch('yara_scanner.list_processes', return_value=[row]), \
                 patch('yara_scanner.process_session_id', return_value=1), \
                 redirect_stdout(output):
                cursor, failures = scan_external_python_candidates(
                    rules, game, self.session, self.raw,
                    timeout=10, max_targets=4, cursor=0)
        records = [json.loads(line) for line in self.raw.getvalue().splitlines()]
        scans = [row for row in records if row['type'] == 'scan_result']
        self.assertEqual(failures, 0)
        self.assertEqual(cursor, 1)
        self.assertEqual(scans[0]['pid'], row['pid'])
        self.assertEqual(scans[0]['scope'], 'same_session_external_python_memory')
        self.assertIn('MECCHA_Repo_ESP_Python',
                      [match['rule'] for match in scans[0]['matches']])
        self.assertIn(f"Python PID (1개): {row['pid']}", output.getvalue())
    def test_cli_preserves_existing_folder(self):
        """같은 세션 이름으로 다시 실행해도 기존 증거 파일을 덮지 않는다."""
        with redirect_stdout(io.StringIO()):
            code = main(['--log-root',self.temp.name,'--session-id','test'])
        self.assertEqual(code,1)
        self.assertTrue(self.session.path.is_dir())
    @unittest.skipUnless(sys.platform=='win32','Windows process test')
    def test_actual_process_memory_normal_on_off(self):
        """무해한 marker ON/OFF와 YARA 양성·음성 구간이 일치하는지 본다."""
        results = generate(Path(self.temp.name)/'fixtures')
        self.assertEqual([r['event_count'] for r in results],[10,10])
        self.assertEqual([r['zero_score_count'] for r in results],[10,6])
        self.assertTrue(all(r['valid_format'] for r in results))
        self.assertTrue(all(not r['comparison_ready'] for r in results))


if __name__ == '__main__': unittest.main()
