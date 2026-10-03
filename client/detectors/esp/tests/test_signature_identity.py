from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from anti_esp.sensors.identity import (
    FileInstallationSecret,
    PseudonymousIdentity,
    normalize_identifier,
)
from anti_esp.sensors.signature import (
    TRUST_E_NOSIGNATURE,
    SignatureBackendUnavailable,
    SignatureVerifier,
)


class FakeTrustBackend:
    name = "fake-wintrust"

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[str] = []

    def verify(self, path: str) -> int:
        self.calls.append(path)
        return self.result


class SignatureVerifierTests(unittest.TestCase):
    def test_trusted_result_is_cached_until_file_fingerprint_changes(self) -> None:
        backend = FakeTrustBackend(0)
        fingerprint = [10, 100]
        verifier = SignatureVerifier(
            backend=backend,
            fingerprint_provider=lambda _path: tuple(fingerprint),
            clock=lambda: 12.5,
        )

        first = verifier.verify("module.dll")
        second = verifier.verify("module.dll")
        fingerprint[1] = 101
        third = verifier.verify("module.dll")

        self.assertEqual(first.status, "trusted")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertFalse(third.cache_hit)
        self.assertEqual(len(backend.calls), 2)

    def test_no_signature_has_unsigned_factual_status(self) -> None:
        backend = FakeTrustBackend(TRUST_E_NOSIGNATURE)
        verifier = SignatureVerifier(
            backend=backend,
            fingerprint_provider=lambda _path: (1, 2),
        )
        result = verifier.verify("unsigned.dll")

        self.assertEqual(result.status, "unsigned")
        self.assertEqual(result.native_code, TRUST_E_NOSIGNATURE)
        self.assertNotIn("cheat", json.dumps(result.to_dict()).casefold())

    def test_native_policy_rejection_is_distinct_from_backend_error(self) -> None:
        verifier = SignatureVerifier(
            backend=FakeTrustBackend(0x800B0109),
            fingerprint_provider=lambda _path: (1, 2),
        )
        result = verifier.verify("bad-chain.dll")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.to_dict()["native_code_hex"], "0x800B0109")
        self.assertNotIn("malware", json.dumps(result.to_dict()).casefold())

    def test_provider_unknown_is_indeterminate_not_signature_rejection(self) -> None:
        verifier = SignatureVerifier(
            backend=FakeTrustBackend(0x800B0001),
            fingerprint_provider=lambda _path: (1, 2),
        )
        result = verifier.verify("provider-unknown.dll")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.native_code, 0x800B0001)
        self.assertIn("indeterminate", result.message.casefold())

    def test_backend_unavailable_is_reported_without_throwing(self) -> None:
        class Unavailable:
            name = "missing"

            def verify(self, _path: str) -> int:
                raise SignatureBackendUnavailable("not installed")

        result = SignatureVerifier(
            backend=Unavailable(),
            fingerprint_provider=lambda _path: (1, 2),
        ).verify("module.dll")
        self.assertEqual(result.status, "unavailable")
        self.assertIn("not installed", result.message)

    def test_bad_fingerprint_does_not_call_backend(self) -> None:
        backend = FakeTrustBackend()
        result = SignatureVerifier(
            backend=backend,
            fingerprint_provider=lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
        ).verify("missing.dll")
        self.assertEqual(result.status, "error")
        self.assertEqual(backend.calls, [])


class IdentityTests(unittest.TestCase):
    def test_disabled_by_default_and_does_not_collect(self) -> None:
        calls = 0

        def identifiers():
            nonlocal calls
            calls += 1
            return {"board": "secret-board"}

        result = PseudonymousIdentity(identifier_provider=identifiers).generate()
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.scope, "none")
        self.assertEqual(calls, 0)

    def test_unpeppered_result_is_install_scoped_and_contains_no_raw_values(self) -> None:
        raw_board = "BOARD-SERIAL-VERY-SECRET"
        raw_disk = "DISK-SERIAL-VERY-SECRET"
        generator = PseudonymousIdentity(
            enabled=True,
            app_namespace="test.localguard",
            identifier_provider=lambda: {
                "board": raw_board,
                "disk": raw_disk,
            },
            installation_secret_provider=lambda: b"A" * 32,
            clock=lambda: 7.0,
        )

        result = generator.generate()
        serialized = json.dumps(result.to_dict())

        self.assertEqual(result.status, "generated")
        self.assertEqual(result.scope, "installation")
        self.assertTrue(result.pseudonym.startswith("ep1_"))
        self.assertNotIn(raw_board, serialized)
        self.assertNotIn(raw_disk, serialized)
        self.assertFalse(result.stability_guaranteed)
        self.assertIn("not a guaranteed HWID", result.message)

    def test_normalization_makes_equivalent_inputs_match(self) -> None:
        common = dict(
            enabled=True,
            app_namespace="test.localguard",
            installation_secret_provider=lambda: b"B" * 32,
        )
        first = PseudonymousIdentity(
            identifier_provider=lambda: {"Board": "  ABC   123  "},
            **common,
        ).generate()
        second = PseudonymousIdentity(
            identifier_provider=lambda: {"board": "abc 123"},
            **common,
        ).generate()
        self.assertEqual(first.pseudonym, second.pseudonym)

    def test_installation_secret_changes_install_scoped_pseudonym(self) -> None:
        def build(secret: bytes):
            return PseudonymousIdentity(
                enabled=True,
                identifier_provider=lambda: {"board": "same"},
                installation_secret_provider=lambda: secret,
            ).generate()

        self.assertNotEqual(build(b"A" * 32).pseudonym, build(b"B" * 32).pseudonym)

    def test_pepper_produces_same_endpoint_pseudonym_across_local_secrets(self) -> None:
        def build(secret: bytes):
            return PseudonymousIdentity(
                enabled=True,
                identifier_provider=lambda: {"board": "same"},
                installation_secret_provider=lambda: secret,
                pepper=b"server-provided-pepper",
            ).generate()

        first = build(b"A" * 32)
        second = build(b"B" * 32)
        self.assertEqual(first.scope, "endpoint")
        self.assertEqual(first.pseudonym, second.pseudonym)
        self.assertFalse(first.stability_guaranteed)

    def test_placeholder_only_source_is_unavailable(self) -> None:
        result = PseudonymousIdentity(
            enabled=True,
            identifier_provider=lambda: {
                "board": "To Be Filled By O.E.M.",
                "disk": "00000000",
            },
            installation_secret_provider=lambda: b"A" * 32,
        ).generate()
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.pseudonym)

    def test_provider_error_and_source_name_cannot_leak_raw_identifier(self) -> None:
        raw = "RAW-DEVICE-SERIAL-DO-NOT-EMIT"

        generated = PseudonymousIdentity(
            enabled=True,
            identifier_provider=lambda: {raw: "value"},
            installation_secret_provider=lambda: b"A" * 32,
        ).generate()
        self.assertEqual(generated.source_kinds, ("custom",))
        self.assertNotIn(raw, json.dumps(generated.to_dict()))

        def failed_provider():
            raise KeyError(raw)

        failed = PseudonymousIdentity(
            enabled=True,
            identifier_provider=failed_provider,
            installation_secret_provider=lambda: b"A" * 32,
        ).generate()
        self.assertEqual(failed.status, "error")
        self.assertNotIn(raw, json.dumps(failed.to_dict()))

    def test_file_installation_secret_is_persistent_and_not_an_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guard" / "secret.bin"
            store = FileInstallationSecret(path)
            first = store.get()
            second = FileInstallationSecret(path).get()

            self.assertEqual(len(first), 32)
            self.assertEqual(first, second)
            self.assertEqual(path.read_bytes(), first)

    def test_normalizer_rejects_placeholders(self) -> None:
        self.assertIsNone(normalize_identifier("  UNKNOWN "))
        self.assertEqual(normalize_identifier("  AbC   123 "), "abc 123")


if __name__ == "__main__":
    unittest.main()
