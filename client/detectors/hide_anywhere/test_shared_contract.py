"""Real Shared contract test; HTTP transport is mocked, no network traffic."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from mecha_detector_v9 import EXPECTED, Rule, make_common_event
from server_bridge import ServerBridge
try:
    import shared.logger as logger
    from shared.transport import DeliveryOutcome
    from shared.schema import encode_event
except ImportError:
    logger = None

@unittest.skipIf(logger is None, 'Shared package unavailable')
class SharedContract(unittest.TestCase):
    def test_actual_env_config_outbox_uuid_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = {
                'GZZ_TELEMETRY_URL': 'https://telemetry.example.test',
                'GZZ_TELEMETRY_TOKEN': 'synthetic-test-only',
                'GZZ_TELEMETRY_OUTBOX': str(Path(tmp) / 'hide-anywhere.sqlite3'),
                'GZZ_TELEMETRY_RETRY_MODE': 'persistent',
            }
            transport = Mock()
            transport.send.return_value = DeliveryOutcome('accepted', 'stored')
            with patch.dict('os.environ', environment), patch('shared.client.HttpTransport', return_value=transport):
                bridge = ServerBridge(Mock())
                try:
                    rule = Rule()
                    events = [make_common_event('test', 'p', 'hide_anywhere', 1000 + i,
                                                EXPECTED, False, False, rule=rule, identity='a')
                              for i in range(4)]
                    events.append(make_common_event('test', 'p', 'hide_anywhere', 2000,
                                                    {}, None, None, rule=rule, identity='a'))
                    for event in events:
                        encode_event(event)
                        bridge.send(event)
                    self.assertTrue(logger.flush_client(timeout=3))
                    self.assertEqual(logger.get_client_status().acknowledged_this_run, len(events))
                    self.assertEqual(len(transport.send.call_args_list), len(events))
                    ids = []
                    for call, event in zip(transport.send.call_args_list, events):
                        payload, event_id = call.args
                        self.assertEqual(json.loads(payload), event)
                        self.assertEqual(str(uuid.UUID(event_id)), event_id)
                        ids.append(event_id)
                    self.assertEqual(len(set(ids)), len(events))
                finally:
                    bridge.close()
