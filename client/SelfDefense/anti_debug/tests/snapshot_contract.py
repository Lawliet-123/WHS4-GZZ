"""Explicit test snapshot verification; not imported by the production monitor."""
import hashlib
import re


def verify_snapshot(source, expected_sha256, reference_commit):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("registry SHA-256 must be 64 lowercase hex characters")
    if not re.fullmatch(r"[0-9a-f]{40}", reference_commit):
        raise ValueError("reference commit must be the full 40-character commit SHA")
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError("registry snapshot hash mismatch; no code imported")
    return actual
