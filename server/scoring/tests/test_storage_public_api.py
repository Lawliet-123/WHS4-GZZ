from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import server.scoring as scoring
from server.scoring.main import (
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

        self.assertTrue(hasattr(scoring, "WindowEvent"))
        self.assertTrue(hasattr(scoring, "WindowConflict"))


if __name__ == "__main__":
    unittest.main()
