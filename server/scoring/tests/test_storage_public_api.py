from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import server.scoring as scoring
from server.scoring.main import (
    backfill_external_access_history_from_writer,
    get_external_access_history,
    get_external_access_scoped_state,
    get_whistle_window_conflicts,
    get_whistle_window_history,
)


class StoragePublicApiTests(unittest.TestCase):

    @patch("server.scoring.main._get_store")
    def test_external_access_wrapper_uses_scoped_storage(self, mocked_get_store):
        store = Mock()
        expected = object()
        store.get_scoped_module_state.return_value = expected
        mocked_get_store.return_value = store

        result = get_external_access_scoped_state(
            "session_1",
            "player_1",
            "external_process",
        )

        self.assertIs(result, expected)
        store.get_scoped_module_state.assert_called_once_with(
            "session_1",
            "player_1",
            "external_access",
            "external_process",
        )

    @patch("server.scoring.main._get_store")
    def test_external_access_history_backfill_replays_only_external_access(
        self,
        mocked_get_store,
    ):
        store = Mock()
        mocked_get_store.return_value = store

        external = Mock(
            result={
                "module": "external_access",
                "raw_score": 2,
            },
            event_id="external-event",
            sequence=10,
        )
        noclip = Mock(
            result={
                "module": "noclip",
                "raw_score": 3,
            },
            event_id="noclip-event",
            sequence=11,
        )

        writer = Mock()
        writer.iter_stored.return_value = [external, noclip]

        result = backfill_external_access_history_from_writer(
            writer,
            batch_size=100,
        )

        self.assertEqual(result, 11)

        store.process_event.assert_called_once_with(
            external.result,
            event_id="external-event",
            sequence=10,
        )

        writer.iter_stored.assert_called_once_with(
            after_sequence=0,
            limit=100,
        )

    @patch("server.scoring.main._get_store")
    def test_external_access_history_wrapper_delegates(
        self,
        mocked_get_store,
    ):
        store = Mock()
        expected = [object(), object()]
        store.get_external_access_history.return_value = expected
        mocked_get_store.return_value = store

        result = get_external_access_history(
            "session_1",
            "player_1",
            "module_integrity",
            after_sequence=10,
            limit=25,
        )

        self.assertIs(result, expected)
        store.get_external_access_history.assert_called_once_with(
            "session_1",
            "player_1",
            "module_integrity",
            after_sequence=10,
            limit=25,
        )

    @patch("server.scoring.main._get_store")
    def test_external_access_wrapper_rejects_unknown_submodule(
        self,
        mocked_get_store,
    ):
        with self.assertRaises(ValueError):
            get_external_access_scoped_state(
                "session_1",
                "player_1",
                "unknown_channel",
            )

        mocked_get_store.assert_not_called()

    @patch("server.scoring.main._get_store")
    def test_whistle_window_history_wrapper_delegates(self, mocked_get_store):
        store = Mock()
        expected = [object()]
        store.get_window_history.return_value = expected
        mocked_get_store.return_value = store

        result = get_whistle_window_history(
            "session_1",
            "player_1",
            after_sequence=20,
            limit=50,
        )

        self.assertIs(result, expected)
        store.get_window_history.assert_called_once_with(
            "session_1",
            "player_1",
            after_sequence=20,
            limit=50,
        )

    @patch("server.scoring.main._get_store")
    def test_whistle_conflict_wrapper_delegates(self, mocked_get_store):
        store = Mock()
        expected = [object()]
        store.get_window_conflicts.return_value = expected
        mocked_get_store.return_value = store

        result = get_whistle_window_conflicts(
            "session_1",
            "player_1",
            after_sequence=30,
            limit=10,
        )

        self.assertIs(result, expected)
        store.get_window_conflicts.assert_called_once_with(
            "session_1",
            "player_1",
            after_sequence=30,
            limit=10,
        )

    def test_package_exports_new_storage_interfaces(self):
        self.assertIs(
            scoring.backfill_external_access_history_from_writer,
            backfill_external_access_history_from_writer,
        )
        self.assertIs(
            scoring.get_external_access_history,
            get_external_access_history,
        )
        self.assertIs(
            scoring.get_external_access_scoped_state,
            get_external_access_scoped_state,
        )
        self.assertIs(
            scoring.get_whistle_window_history,
            get_whistle_window_history,
        )
        self.assertIs(
            scoring.get_whistle_window_conflicts,
            get_whistle_window_conflicts,
        )

        self.assertTrue(hasattr(scoring, "ExternalAccessEvent"))
        self.assertTrue(hasattr(scoring, "WindowEvent"))
        self.assertTrue(hasattr(scoring, "WindowConflict"))


if __name__ == "__main__":
    unittest.main()
