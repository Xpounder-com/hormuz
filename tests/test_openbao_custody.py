from __future__ import annotations

import base64
import io
import json
import unittest
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request

from hormuz.custody import KEY_PURPOSE_DATA_ENCRYPTION, KEY_PURPOSE_PROVIDER_CREDENTIAL, CustodyError, EnvelopeCipher
from hormuz.openbao_custody import OpenBaoTransitDataKeyProvider, verify_openbao_transit_profile


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if amount >= 0:
            return self._payload[:amount]
        return self._payload

    def close(self) -> None:
        self.closed = True


class _OpenBaoTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._keys: dict[str, bytes] = {}
        self._ciphertexts: dict[str, tuple[str, bytes, str]] = {}
        self._next = 0
        self.status: int | None = None
        self.response: object | None = None

    def __call__(self, request: Request, timeout_seconds: float) -> _Response:
        if self.status is not None:
            raise HTTPError(request.full_url, self.status, "redacted", {}, io.BytesIO())
        if self.response is not None:
            return _Response(self.response)
        parsed = urlparse(request.full_url)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        operation = "/".join(parts[2:-1])
        key_reference = parts[-1]
        body = json.loads((request.data or b"{}").decode("utf-8"))
        self.calls.append(
            {
                "operation": operation,
                "key_reference": key_reference,
                "body": body,
                "timeout_seconds": timeout_seconds,
                "token": request.get_header("X-vault-token"),
            }
        )
        context = body.get("context")
        if not isinstance(context, str):
            return _Response({"errors": ["redacted"]})
        key = self._keys.setdefault(key_reference, bytes([65 + len(self._keys)]) * 32)
        if operation == "datakey/plaintext":
            ciphertext = self._new_ciphertext(key_reference, key, context)
            return _Response(
                {
                    "data": {
                        "plaintext": base64.b64encode(key).decode("ascii"),
                        "ciphertext": ciphertext,
                    }
                }
            )
        if operation == "decrypt":
            cipher = body.get("ciphertext")
            if not isinstance(cipher, str) or cipher not in self._ciphertexts:
                raise HTTPError(request.full_url, 404, "redacted", {}, io.BytesIO())
            encrypted_key, plaintext, expected_context = self._ciphertexts[cipher]
            if encrypted_key != key_reference or expected_context != context:
                raise HTTPError(request.full_url, 400, "redacted", {}, io.BytesIO())
            return _Response({"data": {"plaintext": base64.b64encode(plaintext).decode("ascii")}})
        if operation == "rewrap":
            cipher = body.get("ciphertext")
            if not isinstance(cipher, str) or cipher not in self._ciphertexts:
                raise HTTPError(request.full_url, 404, "redacted", {}, io.BytesIO())
            encrypted_key, plaintext, expected_context = self._ciphertexts[cipher]
            if encrypted_key != key_reference or expected_context != context:
                raise HTTPError(request.full_url, 400, "redacted", {}, io.BytesIO())
            return _Response({"data": {"ciphertext": self._new_ciphertext(key_reference, plaintext, context)}})
        if operation == "encrypt":
            plaintext = body.get("plaintext")
            if not isinstance(plaintext, str):
                raise HTTPError(request.full_url, 400, "redacted", {}, io.BytesIO())
            decoded = base64.b64decode(plaintext.encode("ascii"), validate=True)
            return _Response({"data": {"ciphertext": self._new_ciphertext(key_reference, decoded, context)}})
        raise AssertionError(f"unexpected operation: {operation}")

    def _new_ciphertext(self, key_reference: str, plaintext: bytes, context: str) -> str:
        self._next += 1
        value = f"vault:v1:{self._next}"
        self._ciphertexts[value] = (key_reference, plaintext, context)
        return value


class OpenBaoTransitDataKeyProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = _OpenBaoTransport()
        self.provider = OpenBaoTransitDataKeyProvider(
            endpoint_url="https://openbao.example.test:8200",
            token="test-transit-token",
            transport=self.transport,
        )

    def test_envelope_round_trip_binds_the_canonical_context(self) -> None:
        cipher = EnvelopeCipher(self.provider)
        envelope = cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="provider-current",
        )
        self.assertEqual(cipher.unseal(envelope), b"provider-secret-value")
        self.assertEqual([call["operation"] for call in self.transport.calls], ["datakey/plaintext", "decrypt"])
        first_context = self.transport.calls[0]["body"]["context"]  # type: ignore[index]
        decoded_context = json.loads(base64.b64decode(first_context).decode("utf-8"))
        self.assertEqual(decoded_context["hormuz:purpose"], KEY_PURPOSE_PROVIDER_CREDENTIAL)
        self.assertEqual(decoded_context["hormuz:schema"], "hormuz.encrypted-envelope")
        self.assertEqual(self.transport.calls[0]["token"], "test-transit-token")
        self.assertNotIn("provider-secret-value", repr(self.transport.calls))

    def test_same_key_rewrap_stays_in_transit_and_cross_key_rewrap_never_handles_the_secret(self) -> None:
        cipher = EnvelopeCipher(self.provider)
        original = cipher.seal(
            b"provider-secret-value",
            organization_id="xpounder",
            purpose=KEY_PURPOSE_PROVIDER_CREDENTIAL,
            key_reference="provider-current",
        )
        same_key = cipher.rewrap(original, destination_key_reference="provider-current")
        self.assertEqual(same_key.key_reference, "provider-current")
        self.assertEqual(self.transport.calls[-1]["operation"], "rewrap")

        next_key = cipher.rewrap(original, destination_key_reference="provider-next")
        self.assertEqual(next_key.key_reference, "provider-next")
        self.assertEqual([call["operation"] for call in self.transport.calls[-2:]], ["decrypt", "encrypt"])
        self.assertEqual(cipher.unseal(next_key), b"provider-secret-value")
        self.assertNotIn("provider-secret-value", repr(self.transport.calls))

    def test_profile_verification_exercises_each_declared_purpose(self) -> None:
        count = verify_openbao_transit_profile(
            self.provider,
            {
                KEY_PURPOSE_PROVIDER_CREDENTIAL: "provider-current",
                KEY_PURPOSE_DATA_ENCRYPTION: "audit-current",
            },
            organization_id="xpounder",
        )
        self.assertEqual(count, 2)
        contexts = [
            json.loads(base64.b64decode(call["body"]["context"]).decode("utf-8"))  # type: ignore[index]
            for call in self.transport.calls
        ]
        self.assertEqual({context["hormuz:purpose"] for context in contexts}, {
            KEY_PURPOSE_PROVIDER_CREDENTIAL,
            KEY_PURPOSE_DATA_ENCRYPTION,
        })

    def test_failures_are_content_free_and_do_not_follow_redirects(self) -> None:
        self.transport.status = 403
        with self.assertRaises(CustodyError) as raised:
            self.provider.generate_data_key(
                key_reference="provider-current",
                encryption_context={"hormuz:purpose": KEY_PURPOSE_PROVIDER_CREDENTIAL},
            )
        self.assertEqual(raised.exception.code, "openbao_custody_access_denied")

        self.transport.status = 302
        with self.assertRaises(CustodyError) as raised:
            self.provider.generate_data_key(
                key_reference="provider-current",
                encryption_context={"hormuz:purpose": KEY_PURPOSE_PROVIDER_CREDENTIAL},
            )
        self.assertEqual(raised.exception.code, "openbao_custody_unavailable")

    def test_malformed_responses_fail_closed(self) -> None:
        self.transport.response = {"data": {"plaintext": "not-base64", "ciphertext": "vault:v1:1"}}
        with self.assertRaises(CustodyError) as raised:
            self.provider.generate_data_key(
                key_reference="provider-current",
                encryption_context={"hormuz:purpose": KEY_PURPOSE_PROVIDER_CREDENTIAL},
            )
        self.assertEqual(raised.exception.code, "openbao_custody_response_invalid")

    def test_constructor_refuses_non_loopback_http_before_any_token_egress(self) -> None:
        with self.assertRaises(CustodyError) as raised:
            OpenBaoTransitDataKeyProvider(
                endpoint_url="http://openbao.internal.example:8200",
                token="test-transit-token",
                transport=self.transport,
            )
        self.assertEqual(raised.exception.code, "openbao_custody_endpoint_invalid")


if __name__ == "__main__":
    unittest.main()
