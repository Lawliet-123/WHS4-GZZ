"""Meccha Anti-ESP Monitor.

The package observes evidence and produces a review score.  The score is a
rule-based triage value, not a probability that a player is cheating.
"""

from .models import EvidenceEvent, ScoreSnapshot
from .scoring import SuspicionEngine

__all__ = ["EvidenceEvent", "ScoreSnapshot", "SuspicionEngine"]
