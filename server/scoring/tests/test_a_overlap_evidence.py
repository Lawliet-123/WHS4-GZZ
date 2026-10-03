"""Overlap 후보의 양성/음성 근거. 태그 자체는 합산/감산 정책이 아니다."""
from copy import deepcopy
import unittest

from server.scoring.policies.localguard import evaluate
from server.scoring.policy import inspect_event


def event(module, evidence=None, reasons=None, score=1):
    return {"session_id": "overlap_test", "player_id": "player_1", "module": module,
            "timestamp_ms": 1000, "evidence": evidence or {}, "reasons": reasons or [], "raw_score": score}


class AOverlapEvidenceTests(unittest.TestCase):
    def tags(self, payload):
        original = deepcopy(payload)
        result = evaluate(payload, inspect_event(payload))
        self.assertEqual(payload, original)
        return result.overlap_tags

    def test_positive_and_pid_alone_are_never_overlap_evidence(self):
        for module in ("injection", "value_tamper", "filesystem", "godmode_runtime", "aimbot_runtime", "noclip_runtime"):
            self.assertEqual(self.tags(event(module, {"pid": 500, "status": "DETECTED"})), ())

    def test_injection_requires_matching_known_dll_and_reason(self):
        for name, tag in (("meccha.dll", "hide_anywhere_injection"), ("runtime-bridge.dll", "process_injection")):
            payload = event("injection", {"module_2": "C:/Game/" + name}, ["untrusted_module"])
            self.assertEqual(self.tags(payload), (tag,))
            payload["reasons"] = ["unrelated_reason"]
            self.assertEqual(self.tags(payload), ())
        for name in ("not-meccha.dll", "meccha.dll.backup", "other.dll"):
            self.assertEqual(self.tags(event("injection", {"module": name}, ["untrusted_module"])), ())
        self.assertEqual(self.tags(event("injection", {"meta": {"note": "meccha.dll"}}, ["untrusted_module"])), ())

    def test_hide_field_reason_must_match_observed_field(self):
        self.assertEqual(self.tags(event("value_tamper", {"value_2": "Angle=360 (BPC_NearInteract / default 20)"},
                                         ["config_changed_angle"])), ("hide_anywhere_value_tamper",))
        for field in ("Health", "SearchRadius"):
            self.assertEqual(self.tags(event("value_tamper", {"value": field + "=5000"}, ["config_changed_angle"])), ())

    def test_yara_requires_real_known_autopaint_rule_not_shared_loader(self):
        evidence = {"measurement_valid": True, "test_rule_match": False,
                    "matched_rules": ["MECCHA_Repo_AutoPaint_Bridge"], "pid": 500}
        self.assertEqual(self.tags(event("localguard_yara", evidence, ["YARA match"], 3)), ("autopaint_artifact",))
        for changes in ({"test_rule_match": True}, {"matched_rules": ["Simple_LoadLibrary"]}, {"measurement_valid": False}):
            self.assertEqual(self.tags(event("localguard_yara", dict(evidence, **changes), ["YARA match"], 3)), ())

    def test_executable_hash_requires_autopaint_catalogue_match(self):
        evidence = {"measurement_valid": True, "matched_executables": [{"catalogue_ids": ["whs4_gzz_auto_paint_injector_v1"]}]}
        self.assertEqual(self.tags(event("localguard_executable_hash", evidence)), ("autopaint_artifact",))
        evidence["matched_executables"] = [{"catalogue_ids": ["whs4_gzz_godmode_v1"]}]
        self.assertEqual(self.tags(event("localguard_executable_hash", evidence)), ())

    def test_filesystem_does_not_classify_every_injected_dll_as_autopaint(self):
        for name, tags in (("runtime-bridge.dll", ("autopaint_artifact",)), ("meccha.dll", ())):
            self.assertEqual(self.tags(event("filesystem", {"file": name}, ["unexpected_binary_in_game_dir"])), tags)

    def test_runtime_requires_reason_and_actual_read_source(self):
        for module, reason, tag in (("godmode_runtime", "invincible_enabled", "godmode_behavior"),
                                    ("aimbot_runtime", "control_rotation_pattern", "aimbot_behavior"),
                                    ("noclip_runtime", "collision_bit_cleared", "noclip_behavior")):
            evidence = {"value": "target=fixture; observed=1; expected=0", "meta": {"source": "ReadProcessMemory"}}
            self.assertEqual(self.tags(event(module, evidence, [reason])), (tag,))
            self.assertEqual(self.tags(event(module, {"value": evidence["value"]}, [reason])), ())

    def test_unavailable_zero_or_out_of_range_never_emits_tags(self):
        evidence = {"module": "meccha.dll"}
        for changes in ({"status": "ERROR"}, {"status": "OFFLINE"}, {"measurement_valid": False}):
            self.assertEqual(self.tags(event("injection", dict(evidence, **changes), ["untrusted_module"])), ())
        for score in (0, 101):
            self.assertEqual(self.tags(event("injection", evidence, ["untrusted_module"], score)), ())


if __name__ == "__main__":
    unittest.main()
