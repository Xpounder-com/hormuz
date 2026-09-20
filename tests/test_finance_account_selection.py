"""Provider-free configuration candidates, never durable account-binding proof."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.config import GatewayConfig
from hormuz.finance_account_binding import (
    FinanceAccountCandidate,
    UnavailableFinance,
    parse_finance_account_bindings,
    parse_finance_identity,
    select_finance_account,
)


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {
    "upstream_reference_id": "openai-primary",
    "upstream_reference_version": 1,
    "transport_profile": "openai.first-party.v1",
    "inference_credential_reference_id": "inference-primary",
    "inference_credential_reference_version": 2,
}
BINDING = {
    "organization_id": "tenant-a",
    "upstream_reference_id": "openai-primary",
    "binding_id": "account-a",
    "binding_version": 3,
}


def select(*, identity=IDENTITY, bindings=(BINDING,), tenant="tenant-a",
           protocol="openai", base_url="https://api.openai.com/v1"):
    return select_finance_account(
        organization_id=tenant,
        protocol=protocol,
        base_url=base_url,
        identity=parse_finance_identity(copy.deepcopy(identity)),
        bindings=parse_finance_account_bindings(copy.deepcopy(list(bindings))),
    )


class FinanceAccountSelectionTests(unittest.TestCase):
    def test_valid_references_are_an_immutable_candidate_not_an_account_binding(self):
        raw_identity = copy.deepcopy(IDENTITY)
        raw_bindings = [copy.deepcopy(BINDING)]
        parsed_identity = parse_finance_identity(raw_identity)
        parsed_bindings = parse_finance_account_bindings(raw_bindings)
        raw_identity["inference_credential_reference_version"] = 99
        raw_bindings[0]["binding_version"] = 99
        candidate = select_finance_account(
            organization_id="tenant-a", protocol="openai", base_url="https://api.openai.com/v1",
            identity=parsed_identity, bindings=parsed_bindings,
        )
        self.assertIsInstance(candidate, FinanceAccountCandidate)
        self.assertEqual(candidate.identity.inference_credential_reference_version, 2)
        self.assertEqual(candidate.binding.binding_version, 3)
        self.assertFalse(hasattr(candidate, "provider_account_fingerprint"))
        self.assertFalse(hasattr(candidate, "bound"))
        with self.assertRaises(FrozenInstanceError):
            candidate.binding.binding_version = 99
        with self.assertRaises(FrozenInstanceError):
            candidate.identity.transport_profile = "anthropic.first-party.v1"
        with self.assertRaises(FrozenInstanceError):
            parsed_bindings.invalid = True

    def test_same_upstream_selects_only_the_authenticated_tenant_reference(self):
        second = dict(BINDING, organization_id="tenant-b", binding_id="account-b", binding_version=4)
        entries = [BINDING, second]
        self.assertEqual(select(bindings=entries).binding.binding_id, "account-a")
        self.assertEqual(select(bindings=entries, tenant="tenant-b").binding.binding_id, "account-b")
        self.assertEqual(select(bindings=entries, tenant="tenant-c"), UnavailableFinance("binding_missing"))
        # Matching account names still do not make a different tenant selectable.
        self.assertEqual(select(bindings=[second]), UnavailableFinance("binding_missing"))

    def test_only_exact_upstream_reference_matches_without_fallback(self):
        other = dict(BINDING, upstream_reference_id="other", binding_id="other-account")
        self.assertEqual(select(bindings=[other]), UnavailableFinance("binding_missing"))
        self.assertEqual(select(bindings=[other, BINDING]).binding.binding_id, "account-a")

    def test_duplicate_tenant_upstream_is_ambiguous_even_when_identical_or_invalid(self):
        for duplicate in (BINDING, dict(BINDING, binding_id="other"), dict(BINDING, binding_version=True)):
            for entries in ([BINDING, duplicate], [duplicate, BINDING]):
                with self.subTest(entries=entries):
                    self.assertEqual(select(bindings=entries), UnavailableFinance("binding_ambiguous"))

    def test_invalid_mapping_is_scoped_when_its_tenant_and_upstream_are_identifiable(self):
        invalid = dict(BINDING, organization_id="tenant-b", binding_version=False)
        self.assertIsInstance(select(bindings=[BINDING, invalid]), FinanceAccountCandidate)
        self.assertEqual(select(bindings=[BINDING, invalid], tenant="tenant-b"), UnavailableFinance("binding_invalid"))
        # An unidentifiable row cannot safely be ignored in favor of another row.
        for invalid in ({}, None, 7, dict(BINDING, organization_id="")):
            with self.subTest(invalid=invalid):
                self.assertEqual(select(bindings=[BINDING, invalid]), UnavailableFinance("binding_invalid"))

    def test_invalid_optional_metadata_has_only_fixed_content_free_results(self):
        for value in (None, [], "untrusted-payload", 1, True, {}, dict(IDENTITY, secret="untrusted-payload")):
            with self.subTest(value=value):
                self.assertEqual(parse_finance_identity(value), UnavailableFinance("binding_invalid"))
        for value in (None, {}, "untrusted-payload", 1, True):
            with self.subTest(value=value):
                parsed = parse_finance_account_bindings(value)
                self.assertTrue(parsed.invalid)
                self.assertNotIn("untrusted-payload", repr(parsed))
        self.assertEqual(select(bindings=[dict(BINDING, secret="untrusted-payload")]),
                         UnavailableFinance("binding_invalid"))

    def test_versions_are_exact_bounded_integers_never_booleans_or_coerced(self):
        for name in ("upstream_reference_version", "inference_credential_reference_version"):
            for value in (True, False, 0, -1, 2_147_483_648, "1", 1.0, None):
                with self.subTest(name=name, value=value):
                    self.assertEqual(select(identity=dict(IDENTITY, **{name: value})), UnavailableFinance("binding_invalid"))
            self.assertIsInstance(select(identity=dict(IDENTITY, **{name: 2_147_483_647})), FinanceAccountCandidate)
        for value in (True, 0, -1, 2_147_483_648, "1", 1.0, None):
            with self.subTest(binding_version=value):
                self.assertEqual(select(bindings=[dict(BINDING, binding_version=value)]), UnavailableFinance("binding_invalid"))

    def test_identifiers_are_exact_ascii_not_trimmed_normalized_or_secret_values(self):
        for value in ("", " a", "a ", "a\n", "a/b", "é", "a" * 129, 1, None):
            with self.subTest(value=value):
                self.assertEqual(select(identity=dict(IDENTITY, upstream_reference_id=value)), UnavailableFinance("binding_invalid"))
                self.assertEqual(select(identity=dict(IDENTITY, inference_credential_reference_id=value)), UnavailableFinance("binding_invalid"))
                self.assertEqual(select(bindings=[dict(BINDING, binding_id=value)]), UnavailableFinance("binding_invalid"))
        self.assertIsInstance(select(identity=dict(IDENTITY, inference_credential_reference_id="a_.:-0")), FinanceAccountCandidate)

    def test_mapping_limit_is_bounded_without_truncating_away_conflicts(self):
        entries = [dict(BINDING, organization_id=f"tenant-{i}") for i in range(1023)] + [BINDING]
        self.assertIsInstance(select(bindings=entries), FinanceAccountCandidate)
        self.assertEqual(select(bindings=entries + [BINDING]), UnavailableFinance("binding_invalid"))

    def test_transport_requires_exact_first_party_https_origin_and_profile(self):
        for base_url in (
            "http://api.openai.com/v1", "https://api.openai.com.attacker.invalid/v1",
            "https://api.openai.com@proxy.invalid/v1", "https://user@api.openai.com/v1",
            "https://api.openai.com:444/v1", "https://api.openai.com./v1",
            "https://api.openai.com/v1?account=other", "https://api.openai.com/v1#fragment",
            "https://proxy.invalid/v1", "https://api.anthropic.com/v1", "https://api.openai.com:bad/v1",
            "https://api.openai.com\\@proxy.invalid/v1", "https://api.\nopenai.com/v1",
        ):
            with self.subTest(base_url=base_url):
                self.assertEqual(select(base_url=base_url), UnavailableFinance("unsupported_transport"))
        for base_url in ("https://api.openai.com", "https://api.openai.com:443/v1"):
            self.assertIsInstance(select(base_url=base_url), FinanceAccountCandidate)
        self.assertEqual(select(identity=dict(IDENTITY, transport_profile="compatible.openai")),
                         UnavailableFinance("unsupported_transport"))
        self.assertEqual(select(identity=dict(IDENTITY, transport_profile="anthropic.first-party.v1")),
                         UnavailableFinance("unsupported_transport"))
        self.assertIsInstance(select(protocol="anthropic", base_url="https://api.anthropic.com",
                                     identity=dict(IDENTITY, transport_profile="anthropic.first-party.v1")),
                              FinanceAccountCandidate)

    def test_missing_configuration_never_synthesizes_account_identity(self):
        self.assertEqual(select_finance_account(
            organization_id="tenant-a", protocol="openai", base_url="https://api.openai.com",
            identity=UnavailableFinance("not_configured"), bindings=None,
        ), UnavailableFinance("not_configured"))
        self.assertEqual(select_finance_account(
            organization_id="tenant-a", protocol="openai", base_url="https://api.openai.com",
            identity=parse_finance_identity(IDENTITY), bindings=None,
        ), UnavailableFinance("not_configured"))
        self.assertEqual(select(bindings=[]), UnavailableFinance("binding_missing"))


class FinanceAccountConfigurationTests(unittest.TestCase):
    def load(self, identity=IDENTITY, bindings=(BINDING,)):
        value = json.loads((ROOT / "config.example.json").read_bytes())
        value["upstreams"]["openai"]["finance_identity"] = identity
        value["finance_account_bindings"] = list(bindings) if isinstance(bindings, tuple) else bindings
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(value))
            # Credential-free configuration construction must not inspect even
            # the names of environment entries, open envelopes or call providers.
            with mock.patch("hormuz._config_builder.resolve_static_identity_tokens", side_effect=AssertionError("secret read")), \
                 mock.patch("hormuz.custody_runtime.resolve_upstream_credentials", side_effect=AssertionError("credential read")), \
                 mock.patch("urllib.request.urlopen", side_effect=AssertionError("provider call")):
                from hormuz._config_builder import _build_gateway_config
                return _build_gateway_config(GatewayConfig, path, environ={}, resolve_credentials=False)

    def test_optional_metadata_loads_as_typed_values_without_credential_access(self):
        config = self.load()
        result = select_finance_account(
            organization_id="tenant-a", protocol="openai", base_url=config.upstreams["openai"].base_url,
            identity=config.upstreams["openai"].finance_identity, bindings=config.finance_account_bindings,
        )
        self.assertIsInstance(result, FinanceAccountCandidate)
        self.assertEqual(result.binding.binding_id, "account-a")

    def test_malformed_optional_metadata_does_not_reject_ordinary_configuration(self):
        for identity, bindings in ((None, None), ([], {}), (dict(IDENTITY, extra=True), [BINDING]),
                                   (IDENTITY, [dict(BINDING, binding_version=True)])):
            with self.subTest(identity=identity, bindings=bindings):
                config = self.load(identity, bindings)
                result = select_finance_account(
                    organization_id="tenant-a", protocol="openai", base_url=config.upstreams["openai"].base_url,
                    identity=config.upstreams["openai"].finance_identity, bindings=config.finance_account_bindings,
                )
                self.assertEqual(result, UnavailableFinance("binding_invalid"))


if __name__ == "__main__":
    unittest.main()
