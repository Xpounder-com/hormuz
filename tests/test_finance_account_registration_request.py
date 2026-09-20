"""Adversarial, provider-free request intake for future account registration."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from hormuz.finance_account_binding import (
    AccountBindingRequestError,
    parse_account_binding_registration_request,
)


REQUEST = {
    "schema_id": "hormuz.finance-account-binding-request",
    "schema_version": 1,
    "binding_id": "primary-account",
    "expected_version": None,
    "upstream_reference_id": "openai-primary",
    "upstream_reference_version": 1,
    "transport_profile": "openai.first-party.v1",
    "inference_credential_reference_id": "inference-primary",
    "inference_credential_reference_version": 2,
    "source_binding": {
        "binding_id": "source-primary",
        "version": 3,
        "content_digest": "a" * 64,
    },
    "state": "active",
    "reason_code": "created",
}


def payload(value=REQUEST, **json_options):
    return json.dumps(value, **json_options).encode("utf-8")


class AccountBindingRegistrationRequestTests(unittest.TestCase):
    def parse(self, value=REQUEST, *, tenant="tenant-a"):
        return parse_account_binding_registration_request(payload(value), organization_id=tenant)

    def assert_invalid(self, raw, *, tenant="tenant-a"):
        with self.assertRaises(AccountBindingRequestError) as captured:
            parse_account_binding_registration_request(raw, organization_id=tenant)
        self.assertEqual(captured.exception.code, "invalid_request")
        self.assertEqual(str(captured.exception), "invalid_request")

    def test_canonical_request_digest_includes_authenticated_tenant(self):
        first = parse_account_binding_registration_request(
            payload(REQUEST, indent=2), organization_id="tenant-a"
        )
        reordered = dict(reversed(list(REQUEST.items())))
        reordered["source_binding"] = dict(reversed(list(REQUEST["source_binding"].items())))
        second = self.parse(reordered)
        other_tenant = self.parse(REQUEST, tenant="tenant-b")
        canonical = json.dumps(
            {"organization_id": "tenant-a", "request": REQUEST},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(first.request_digest, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(first.request_digest, second.request_digest)
        self.assertNotEqual(first.request_digest, other_tenant.request_digest)
        changed_source = copy.deepcopy(REQUEST)
        changed_source["source_binding"]["content_digest"] = "b" * 64
        self.assertNotEqual(first.request_digest, self.parse(changed_source).request_digest)
        self.assertEqual(first.organization_id, "tenant-a")
        self.assertEqual(first.source_binding_digest, "a" * 64)

    def test_exact_metadata_shape_rejects_authority_and_secret_fields(self):
        for field in (
            "organization_id", "registered_by", "registered_at", "binding_event_id",
            "provider_account_fingerprint", "credential", "account_reference_id",
        ):
            with self.subTest(field=field):
                self.assert_invalid(payload(dict(REQUEST, **{field: "untrusted-secret"})))
        for field in ("organization_id", "provider_account_fingerprint", "credential"):
            with self.subTest(source_field=field):
                changed = copy.deepcopy(REQUEST)
                changed["source_binding"][field] = "untrusted-secret"
                self.assert_invalid(payload(changed))

    def test_strict_json_limits_and_duplicate_nested_members(self):
        exactly_maximum = payload() + b" " * (65_536 - len(payload()))
        self.assertEqual(
            parse_account_binding_registration_request(
                exactly_maximum, organization_id="tenant-a"
            ).request_digest,
            self.parse().request_digest,
        )
        for raw in (
            b"", b"[]", b"{", b"\xff", b"x" * (65_536 + 1),
            b'{"schema_id":"x","schema_id":"y"}',
            payload().replace(b'"version": 3', b'"version": 3, "version": 3'),
            payload().replace(b'"schema_version": 1', b'"schema_version": NaN'),
            payload().replace(b'"schema_version": 1', b'"schema_version": Infinity'),
            payload().replace(b'"schema_version": 1', b'"schema_version": 1e999'),
        ):
            with self.subTest(raw_prefix=raw[:32]):
                self.assert_invalid(raw)

    def test_identifier_version_digest_and_profile_bounds(self):
        for field in ("binding_id", "upstream_reference_id", "inference_credential_reference_id"):
            for invalid in ("", " a", "a ", "a/b", "é", "a" * 129, 1):
                with self.subTest(field=field, invalid=invalid):
                    self.assert_invalid(payload(dict(REQUEST, **{field: invalid})))
        for field in ("upstream_reference_version", "inference_credential_reference_version"):
            for invalid in (True, False, 0, -1, 2_147_483_648, 1.0, "1"):
                with self.subTest(field=field, invalid=invalid):
                    self.assert_invalid(payload(dict(REQUEST, **{field: invalid})))
        for invalid in (True, 0, -1, 2_147_483_648, 1.0, "1"):
            with self.subTest(expected_version=invalid):
                self.assert_invalid(payload(dict(REQUEST, expected_version=invalid)))
        for invalid in ("", "b" * 63, "A" * 64, "z" * 64):
            changed = copy.deepcopy(REQUEST)
            changed["source_binding"]["content_digest"] = invalid
            with self.subTest(digest=invalid):
                self.assert_invalid(payload(changed))
        for field, invalid in (("binding_id", "source/other"), ("version", True),
                               ("version", 0), ("version", 2_147_483_648)):
            changed = copy.deepcopy(REQUEST)
            changed["source_binding"][field] = invalid
            with self.subTest(source_field=field, invalid=invalid):
                self.assert_invalid(payload(changed))
        self.assert_invalid(payload(dict(REQUEST, schema_id="other.request")))
        self.assert_invalid(payload(dict(REQUEST, schema_version=True)))
        self.assert_invalid(payload(dict(REQUEST, transport_profile="openai.compatible.v1")))
        self.assert_invalid(payload(REQUEST), tenant="tenant/other")

    def test_state_reason_and_expected_version_are_consistent(self):
        for state, reason, expected in (
            ("active", "created", None),
            ("active", "replaced", 1),
            ("revoked", "revoked", 1),
        ):
            with self.subTest(state=state, reason=reason, expected=expected):
                result = self.parse(dict(REQUEST, state=state, reason_code=reason,
                                         expected_version=expected))
                self.assertEqual((result.state, result.reason_code, result.expected_version),
                                 (state, reason, expected))
        for state, reason, expected in (
            ("active", "created", 1),
            ("active", "replaced", None),
            ("active", "revoked", 1),
            ("revoked", "created", 1),
            ("revoked", "revoked", None),
        ):
            with self.subTest(state=state, reason=reason, expected=expected):
                self.assert_invalid(payload(dict(REQUEST, state=state, reason_code=reason,
                                                 expected_version=expected)))

    def test_result_is_immutable_redacted_and_has_no_io(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("provider I/O")), \
             mock.patch.object(sqlite3, "connect", side_effect=AssertionError("database I/O")):
            result = self.parse()
        with self.assertRaises(FrozenInstanceError):
            result.binding_id = "different"
        self.assertNotIn("primary-account", repr(result))
        self.assertNotIn("source-primary", repr(result))
        self.assertNotIn("inference-primary", repr(result))


if __name__ == "__main__":
    unittest.main()
