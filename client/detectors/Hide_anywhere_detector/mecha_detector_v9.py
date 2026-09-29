#!/usr/bin/env python3
"""Hide Anywhere scoring and common-event helpers.

This module contains no process access.  It only evaluates values already
collected by ``mecha_logger.py`` and returns the team-wide event schema.
"""
from __future__ import annotations

import math


EXPECTED = {
    'InteractLength': 5000.0,
    'IsInViewCheckLate': 1_000_000_000.0,
    'SearchRadius': 5000.0,
    'Angle': 360.0,
    'AngleBias': 360.0,
    'IgnoreUpVector': 1,
}


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def values_match(values):
    """True only when every pinned Hide Anywhere value is present and valid."""
    for name, expected in EXPECTED.items():
        value = values.get(name)
        if not _finite_number(value):
            return False
        if not math.isclose(float(value), float(expected), rel_tol=0.0, abs_tol=1e-6):
            return False
    return True


class Rule:
    """Three-sample confirmation with re-arm on mismatch or Pawn change."""
    def __init__(self, required=3):
        self.required = required
        self.identity = None
        self.count = 0
        self.latched = False

    def reset(self):
        self.identity = None
        self.count = 0
        self.latched = False

    def evaluate(self, identity, values):
        if identity != self.identity:
            self.identity = identity
            self.count = 0
            self.latched = False
        if not values_match(values):
            self.count = 0
            self.latched = False
            return False
        self.count += 1
        if self.count >= self.required and not self.latched:
            self.latched = True
            return True
        return False


def make_common_event(session_id, player_id, module, timestamp_ms, values,
                      injected_module=False, viewport_hook=False):
    """Return exactly the shared ReplayAnalyzer/Dashboard event schema."""
    pattern = values_match(values)
    evidence = {
        'hide_value_pattern': int(pattern),
        'injected_module': int(bool(injected_module)),
        'viewport_hook': int(bool(viewport_hook)),
    }
    reasons = []
    if pattern:
        reasons.append('Hide Anywhere Value Pattern Matched')
    if injected_module:
        reasons.append('Injected Module Loaded')
    if viewport_hook:
        reasons.append('Viewport VTable Outside Main Image')

    # A complete data pattern is decisive. Module/vtable observations provide
    # incremental evidence when the values are temporarily unavailable.
    raw_score = 3 if pattern else min(2, evidence['injected_module'] + evidence['viewport_hook'])
    return {
        'session_id': session_id,
        'player_id': player_id,
        'module': module,
        'timestamp_ms': max(0, int(timestamp_ms)),
        'evidence': evidence,
        'reasons': reasons,
        'raw_score': raw_score,
    }
