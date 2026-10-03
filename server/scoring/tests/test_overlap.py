from __future__ import annotations

import unittest

from server.scoring.correlation import CorrelationCandidate
from server.scoring.overlap import build_overlap_groups


def candidate(
    left: str,
    right: str,
    *,
    tag: str,
    left_event: str,
    right_event: str,
) -> CorrelationCandidate:
    return CorrelationCandidate(
        session_id="s",
        player_id="p",
        modules=(left, right),
        event_ids=(left_event, right_event),
        sequences=(1, 2),
        overlap_tags=(tag,),
        entity_keys=(None, None),
        time_distance_ms=100,
        reasons=("candidate",),
    )


class OverlapGroupTests(unittest.TestCase):
    def test_single_pair_forms_group(self):
        result = build_overlap_groups(
            [
                candidate(
                    "autopaint",
                    "injection",
                    tag="process_injection",
                    left_event="a",
                    right_event="b",
                )
            ],
            active_modules=("autopaint", "injection"),
        )

        self.assertEqual(len(result), 1)

        group = result[0]

        self.assertEqual(
            group.modules,
            ("autopaint", "injection"),
        )
        self.assertEqual(
            group.overlap_tags,
            ("process_injection",),
        )
        self.assertEqual(group.candidate_count, 1)

    def test_transitive_candidates_form_one_group(self):
        result = build_overlap_groups(
            [
                candidate(
                    "hide_anywhere",
                    "injection",
                    tag="hide_injection",
                    left_event="a",
                    right_event="b",
                ),
                candidate(
                    "injection",
                    "value_tamper",
                    tag="hide_config",
                    left_event="b",
                    right_event="c",
                ),
            ],
            active_modules=(
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
        )

        self.assertEqual(len(result), 1)

        self.assertEqual(
            result[0].modules,
            (
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
        )

        self.assertEqual(
            result[0].overlap_tags,
            ("hide_config", "hide_injection"),
        )

    def test_inactive_module_is_not_grouped(self):
        result = build_overlap_groups(
            [
                candidate(
                    "autopaint",
                    "injection",
                    tag="process_injection",
                    left_event="a",
                    right_event="b",
                )
            ],
            active_modules=("autopaint",),
        )

        self.assertEqual(result, ())

    def test_candidate_without_common_tag_is_ignored(self):
        item = CorrelationCandidate(
            session_id="s",
            player_id="p",
            modules=("a", "b"),
            event_ids=("1", "2"),
            sequences=(1, 2),
            overlap_tags=(),
            entity_keys=(None, None),
            time_distance_ms=10,
            reasons=(),
        )

        result = build_overlap_groups(
            [item],
            active_modules=("a", "b"),
        )

        self.assertEqual(result, ())

    def test_no_candidates_returns_empty(self):
        self.assertEqual(
            build_overlap_groups(
                [],
                active_modules=("aimbot",),
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
