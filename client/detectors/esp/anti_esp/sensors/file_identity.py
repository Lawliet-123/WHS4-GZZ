"""Factual file hash and Authenticode enrichment for module observations."""

from __future__ import annotations

from typing import Any

from ..policy import FileFingerprintCache
from .signature import SignatureVerifier


class FileIdentityEnricher:
    """Collect cached SHA-256 and Windows trust facts without classifying them."""

    def __init__(
        self,
        *,
        fingerprints: FileFingerprintCache | None = None,
        signatures: SignatureVerifier | None = None,
    ) -> None:
        self._fingerprints = fingerprints or FileFingerprintCache()
        self._signatures = signatures or SignatureVerifier()

    def inspect(self, path: str) -> dict[str, Any]:
        digest = self._fingerprints.sha256(path)
        signature = self._signatures.verify(path)
        return {
            "sha256": digest,
            "signature_status": signature.status,
            "signature_native_code": signature.native_code,
            "signature_native_code_hex": (
                None
                if signature.native_code is None
                else f"0x{signature.native_code:08X}"
            ),
            "signature_backend": signature.backend,
            "signature_checked_at": signature.checked_at,
            "signature_message": signature.message,
            "signature_cache_hit": signature.cache_hit,
        }

    def __call__(self, path: str) -> dict[str, Any]:
        return self.inspect(path)


__all__ = ["FileIdentityEnricher"]
