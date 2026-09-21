"""Adversarial, provider-free request intake for future account registration."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from hormuz.finance_account_registration import (
    AccountBindingMatchError,
    AccountBindingRequestError,
    match_active_account_binding_source,
    parse_account_binding_registration_request,
)
from hormuz.finance_account_binding import (
    UnavailableFinance,
    parse_finance_account_bindings,
    parse_finance_identity,
    select_finance_account,
)
from hormuz.finance_collection_repository import SourceBindingVersion


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


def selected_account(*, tenant="tenant-a", binding_id="primary-account", version=1,
                     credential_version=2, upstream_version=1):
    return select_finance_account(
        organization_id=tenant,
        protocol="openai",
        base_url="https://api.openai.com/v1",
        identity=parse_finance_identity({
            "upstream_reference_id": "openai-primary",
            "upstream_reference_version": upstream_version,
            "transport_profile": "openai.first-party.v1",
            "inference_credential_reference_id": "inference-primary",
            "inference_credential_reference_version": credential_version,
        }),
        bindings=parse_finance_account_bindings([{
            "organization_id": tenant,
            "upstream_reference_id": "openai-primary",
            "binding_id": binding_id,
            "binding_version": version,
        }]),
    )


def source_account(**changes):
    value = SourceBindingVersion(
        organization_id="tenant-a",
        binding_id="source-primary",
        version=3,
        binding_event_id="00000000-0000-4000-8000-000000000001",
        provider="openai",
        provider_account_fingerprint="b" * 64,
        scope_kind="projects",
        scope_fingerprints=("c" * 64,),
        credential_reference_id="billing-admin-reference",
        credential_reference_version=4,
        fingerprint_key_version=5,
        binding_state="active",
        previous_version=2,
        content_digest="a" * 64,
        bound_by="operator-a",
        bound_at="2026-09-21T00:00:00+00:00",
        reason_code="replaced",
    )
    return replace(value, **changes)


_DEFAULT_SOURCE = object()


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


class ActiveAccountBindingSourceMatchTests(unittest.TestCase):
    def match(self, *, request=None, selected=None, source=_DEFAULT_SOURCE,
              current_source=_DEFAULT_SOURCE):
        source = source_account() if source is _DEFAULT_SOURCE else source
        return match_active_account_binding_source(
            parse_account_binding_registration_request(payload(), organization_id="tenant-a")
            if request is None else request,
            selected=selected_account() if selected is None else selected,
            source=source,
            current_source=source if current_source is _DEFAULT_SOURCE else current_source,
        )

    def assert_conflict(self, **kwargs):
        with self.assertRaises(AccountBindingMatchError) as captured:
            self.match(**kwargs)
        self.assertEqual(captured.exception.code, "binding_conflict")
        self.assertEqual(str(captured.exception), "binding_conflict")

    def test_matching_copies_only_exact_source_coordinates_without_registration(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("provider I/O")), \
             mock.patch.object(sqlite3, "connect", side_effect=AssertionError("database I/O")):
            matched = self.match()
        self.assertEqual((matched.organization_id, matched.binding_id, matched.version),
                         ("tenant-a", "primary-account", 1))
        self.assertEqual((matched.provider, matched.provider_account_fingerprint,
                          matched.scope_kind, matched.scope_fingerprints,
                          matched.fingerprint_key_version),
                         ("openai", "b" * 64, "projects", ("c" * 64,), 5))
        self.assertEqual(matched.source_binding_digest, "a" * 64)
        self.assertFalse(hasattr(matched, "credential_reference_id"))
        self.assertFalse(hasattr(matched, "receipt_id"))
        self.assertFalse(hasattr(matched, "registered_at"))
        self.assertNotIn("primary-account", repr(matched))
        with self.assertRaises(FrozenInstanceError):
            matched.version = 2

    def test_absent_or_mismatched_binding_never_infers_account_from_current_context(self):
        request = parse_account_binding_registration_request(payload(), organization_id="tenant-a")
        source = source_account()
        for kwargs in (
            {"selected": UnavailableFinance("not_configured")},
            {"selected": selected_account(binding_id="another-account")},
            {"selected": selected_account(tenant="tenant-b")},
            {"selected": selected_account(version=2)},
            {"selected": selected_account(credential_version=3)},
            {"selected": selected_account(upstream_version=2)},
            {"source": None},
            {"source": replace(source, organization_id="tenant-b")},
            {"source": replace(source, binding_id="other-source")},
            {"source": replace(source, content_digest="d" * 64)},
            {"source": replace(source, provider="anthropic")},
            {"current_source": replace(source, version=4, previous_version=3)},
            {"current_source": replace(source, binding_state="revoked")},
        ):
            with self.subTest(kwargs=kwargs):
                self.assert_conflict(request=request, **kwargs)

    def test_replacement_must_match_configured_next_version_and_current_source(self):
        request = parse_account_binding_registration_request(
            payload(dict(REQUEST, expected_version=1, reason_code="replaced")),
            organization_id="tenant-a",
        )
        self.assertEqual(self.match(request=request, selected=selected_account(version=2)).version, 2)
        self.assert_conflict(request=request)
        self.assert_conflict(request=request, selected=selected_account(version=2),
                             current_source=replace(source_account(), binding_state="revoked"))

    def test_revocation_requires_prior_registration_and_never_uses_active_source_match(self):
        request = parse_account_binding_registration_request(
            payload(dict(REQUEST, expected_version=1, state="revoked", reason_code="revoked")),
            organization_id="tenant-a",
        )
        self.assert_conflict(request=request, selected=selected_account(version=2))


if __name__ == "__main__":
    unittest.main()
