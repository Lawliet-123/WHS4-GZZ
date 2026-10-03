"""A 담당 overlap 후보 조건. PID/점수만으로 원인 동일성을 만들지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

HIDE_INJECTION = "hide_anywhere_injection"
HIDE_VALUE_TAMPER = "hide_anywhere_value_tamper"
_HIDE_FIELDS = frozenset((
    "interactlength", "isinviewchecklate", "searchradius", "angle", "anglebias", "ignoreupvector",
))
_AUTOPAINT_RULES = frozenset((
    "MECCHA_Repo_AutoPaint_Bridge", "MECCHA_PRReady_AutoPaint_Bridge",
    "MECCHA_Repo_AutoPaint_Direct_Injector", "MECCHA_Repo_AutoPaint_Controller",
))
_AUTOPAINT_CATALOGUE_IDS = frozenset((
    "whs4_gzz_auto_paint_injector_v1", "whs4_gzz_auto_paint_injector_v2",
    "whs4_gzz_auto_paint_lite_v2",
))
_INJECTION_REASONS = frozenset(("untrusted_module", "process_event_hooked", "exec_function_hooked"))


def _texts(evidence: Mapping[str, Any], prefix: str):
    """생산자 module[_N]/address[_N]/value[_N]/file[_N] 증거만 사용한다."""
    for key, value in evidence.items():
        if re.fullmatch(re.escape(prefix) + r"(?:_[2-9][0-9]*|_1[0-9]+)?", key) and isinstance(value, str):
            yield value


def _dll(evidence: Mapping[str, Any], name: str) -> bool:
    # 알려진 basename 토큰만 인정. not-meccha.dll / meccha.dll.backup은 제외한다.
    token = re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", re.IGNORECASE)
    return any(token.search(text) for prefix in ("module", "address", "file")
               for text in _texts(evidence, prefix))


def localguard_tags(event: Mapping[str, Any]) -> tuple[str, ...]:
    module, evidence, reasons = event["module"], event["evidence"], set(event["reasons"])
    if event["raw_score"] <= 0 or evidence.get("status") in ("ERROR", "OFFLINE") or evidence.get("measurement_valid") is False:
        return ()
    tags: list[str] = []
    if module == "injection" and reasons & _INJECTION_REASONS:
        if _dll(evidence, "meccha.dll"):
            tags.append(HIDE_INJECTION)
        if _dll(evidence, "runtime-bridge.dll"):
            tags.append("process_injection")
    elif module == "value_tamper":
        fields = {reason.removeprefix("config_changed_") for reason in reasons if reason.startswith("config_changed_")}
        observed = {text.split("=", 1)[0].strip().casefold() for text in _texts(evidence, "value") if "=" in text}
        if fields & observed & _HIDE_FIELDS:
            tags.append(HIDE_VALUE_TAMPER)
    elif module == "filesystem":
        if reasons & {"unexpected_binary_in_game_dir", "proxy_dll_in_game_dir"} and _dll(evidence, "runtime-bridge.dll"):
            tags.append("autopaint_artifact")
    elif module == "localguard_yara":
        matched = evidence.get("matched_rules")
        if (evidence.get("measurement_valid") is True and evidence.get("test_rule_match") is False
                and isinstance(matched, list) and any(isinstance(rule, str) and rule in _AUTOPAINT_RULES for rule in matched)):
            # 공유 LoadLibrary 규칙이나 테스트 규칙으로 AutoPaint 원인을 추정하지 않는다.
            tags.append("autopaint_artifact")
    elif module == "localguard_executable_hash":
        matches = evidence.get("matched_executables")
        if evidence.get("measurement_valid") is True and isinstance(matches, list):
            for item in matches:
                ids = item.get("catalogue_ids") if isinstance(item, Mapping) else None
                if isinstance(ids, list) and any(isinstance(key, str) and key in _AUTOPAINT_CATALOGUE_IDS for key in ids):
                    tags.append("autopaint_artifact")
                    break
    elif module in ("godmode_runtime", "aimbot_runtime", "noclip_runtime"):
        known = {
            "godmode_runtime": ({"invincible_enabled", "godmode_value_pattern"}, "godmode_behavior"),
            "aimbot_runtime": ({"control_rotation_pattern"}, "aimbot_behavior"),
            "noclip_runtime": ({"collision_bit_cleared"}, "noclip_behavior"),
        }
        codes, tag = known[module]
        meta = evidence.get("meta")
        if (reasons & codes and any(_texts(evidence, "value")) and isinstance(meta, Mapping)
                and meta.get("source") == "ReadProcessMemory"):
            tags.append(tag)
    return tuple(tags)
