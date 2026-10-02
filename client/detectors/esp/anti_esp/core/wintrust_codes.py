"""Pure classification of native WinVerifyTrust result codes.

Only documented trust-policy, certificate, or signature rejection results are
classified as ``rejected``. Unknown/provider failures remain indeterminate so
an inability to verify cannot become suspicion evidence.
"""

from __future__ import annotations

from typing import Literal


ERROR_SUCCESS = 0x00000000
TRUST_E_NOSIGNATURE = 0x800B0100

# Conservative allowlist of native results that describe an actual signature,
# certificate, or trust-policy rejection. Operational/provider failures such as
# TRUST_E_PROVIDER_UNKNOWN (0x800B0001), TRUST_E_ACTION_UNKNOWN (0x800B0002),
# TRUST_E_SUBJECT_FORM_UNKNOWN (0x800B0003), TRUST_E_SYSTEM_ERROR, and
# CERT_E_REVOCATION_FAILURE are intentionally absent.
WINTRUST_REJECTION_CODES = frozenset(
    {
        0x80092026,  # CRYPT_E_SECURITY_SETTINGS
        0x80096002,  # TRUST_E_NO_SIGNER_CERT
        0x80096003,  # TRUST_E_COUNTER_SIGNER
        0x80096004,  # TRUST_E_CERT_SIGNATURE
        0x80096005,  # TRUST_E_TIME_STAMP
        0x80096010,  # TRUST_E_BAD_DIGEST
        0x80096019,  # TRUST_E_BASIC_CONSTRAINTS
        0x800B0004,  # TRUST_E_SUBJECT_NOT_TRUSTED
        0x800B0101,  # CERT_E_EXPIRED
        0x800B0102,  # CERT_E_VALIDITYPERIODNESTING
        0x800B0103,  # CERT_E_ROLE
        0x800B0104,  # CERT_E_PATHLENCONST
        0x800B0105,  # CERT_E_CRITICAL
        0x800B0106,  # CERT_E_PURPOSE
        0x800B0107,  # CERT_E_ISSUERCHAINING
        0x800B0108,  # CERT_E_MALFORMED
        0x800B0109,  # CERT_E_UNTRUSTEDROOT
        0x800B010A,  # CERT_E_CHAINING
        0x800B010C,  # CERT_E_REVOKED
        0x800B010D,  # CERT_E_UNTRUSTEDTESTROOT
        0x800B010F,  # CERT_E_CN_NO_MATCH
        0x800B0110,  # CERT_E_WRONG_USAGE
        0x800B0111,  # TRUST_E_EXPLICIT_DISTRUST
        0x800B0112,  # CERT_E_UNTRUSTEDCA
        0x800B0113,  # CERT_E_INVALID_POLICY
        0x800B0114,  # CERT_E_INVALID_NAME
    }
)

NativeTrustOutcome = Literal["trusted", "unsigned", "rejected", "indeterminate"]


def classify_winverifytrust_code(code: int) -> NativeTrustOutcome:
    """Classify a raw HRESULT without treating unknown failures as rejection."""

    if isinstance(code, bool) or not isinstance(code, int):
        raise TypeError("WinVerifyTrust code must be an integer")
    normalized = code & 0xFFFFFFFF
    if normalized == ERROR_SUCCESS:
        return "trusted"
    if normalized == TRUST_E_NOSIGNATURE:
        return "unsigned"
    if normalized in WINTRUST_REJECTION_CODES:
        return "rejected"
    return "indeterminate"


__all__ = [
    "ERROR_SUCCESS",
    "NativeTrustOutcome",
    "TRUST_E_NOSIGNATURE",
    "WINTRUST_REJECTION_CODES",
    "classify_winverifytrust_code",
]
