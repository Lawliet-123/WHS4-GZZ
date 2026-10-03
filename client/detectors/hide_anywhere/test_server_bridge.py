import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server_bridge import ServerBridge


class ServerBridgeTests(unittest.TestCase):
    def bridge(self, api):
        with (patch('server_bridge.importlib.import_module', return_value=api) as importer,
              patch.dict(os.environ, {'GZZ_TELEMETRY_OUTBOX': 'test-only.sqlite3'})):
            bridge = ServerBridge(self.emit)
            importer.assert_called_once_with('shared.logger')
            return bridge

    def setUp(self):
        self.emit = Mock()
        self.api = SimpleNamespace(ClientConfig=Mock(), configure_client=Mock(),
                                   send_detection=Mock(), flush_client=Mock(return_value=True),
                                   shutdown_client=Mock(return_value=True))

    def test_events_unchanged_and_id_delegated_to_shared(self):
        bridge = self.bridge(self.api)
        event = {'timestamp_ms': 1000, 'raw_score': 0}
        bridge.send(event)
        bridge.send(event)
        calls = self.api.send_detection.call_args_list
        self.assertEqual(calls[0].args, (event,))
        self.assertEqual(calls[0].kwargs, {})
        self.assertEqual(calls[1].kwargs, {})
        self.api.ClientConfig.from_env.assert_called_once_with()
        self.api.configure_client.assert_called_once_with(self.api.ClientConfig.from_env.return_value)

    def test_enqueue_failure_does_not_stop_collector(self):
        bridge = self.bridge(self.api)
        self.api.send_detection.side_effect = RuntimeError('private config details')
        bridge.send({'timestamp_ms': 0, 'raw_score': 3})
        self.assertEqual(self.emit.call_args.args[0], 'server_enqueue_error')
        self.assertNotIn('private config details', str(self.emit.call_args))

    def test_shutdown_even_on_flush_failure(self):
        bridge = self.bridge(self.api)
        self.api.flush_client.side_effect = RuntimeError()
        bridge.close()
        self.api.shutdown_client.assert_called_once_with(timeout=5.0)


if __name__ == '__main__':
    unittest.main()
