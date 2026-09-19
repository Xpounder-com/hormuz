"""Cross-language reference vectors exercised by the existing gateway validators."""

from __future__ import annotations

import json
import re
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from pathlib import Path

from hormuz.contracts import ContractValidationError, validate_contract
from hormuz.compaction_runtime import ContextRuntimeError, parse_context_preference
from hormuz.session_client import SessionClientError, validate_session_gateway
from hormuz.credential_store import StoredSession
from hormuz.session_client import access_token

FIXTURES = Path(__file__).parent / "fixtures" / "native_client" / "v1" / "gateway.json"


class NativeClientContractTests(unittest.TestCase):
    def test_shared_session_vectors_preserve_python_credential_and_refresh_thresholds(self) -> None:
        fixture = json.loads(FIXTURES.with_name("sessions.json").read_text())
        now = datetime.fromtimestamp(fixture["now"], timezone.utc)
        record = fixture["record"]
        for case in fixture["cases"]:
            # Python CLI has its own v1 snake-case record with no pending state.
            # Pending-state parity is deliberately Swift/Rust only.
            if not case["python_applicable"]:
                continue
            with self.subTest(case=case["id"]):
                session = StoredSession.from_dict({
                    "version": 1, "gateway": record["profile"]["gateway"],
                    "client": record["profile"]["client"],
                    "access_token": record["accessToken"], "refresh_token": record["refreshToken"],
                    "access_expires_at": (now + timedelta(seconds=case["access_remaining"])).isoformat(),
                    "session_expires_at": (now + timedelta(seconds=case["session_remaining"])).isoformat(),
                })
                store = Mock()
                store.get.return_value = session
                response = json.loads(session.to_json())
                response.update(access_token="hox_a_" + "b" * 43, refresh_token="hox_r_" + "b" * 43,
                                access_expires_at=(now + timedelta(seconds=600)).isoformat())
                with patch("hormuz.session_client.datetime") as clock, patch("hormuz.session_client.SessionGatewayClient") as gateway:
                    clock.now.return_value = now
                    gateway.return_value.post.return_value = (200, response)
                    kwargs = dict(gateway=session.gateway, profile="synthetic", allow_insecure_http=False,
                                  store=store, lock_factory=lambda _: nullcontext())
                    if case["result"] == "credential":
                        self.assertTrue(access_token(**kwargs).startswith("hox_a_"))
                    else:
                        with self.assertRaises(SessionClientError) as caught:
                            access_token(**kwargs)
                        self.assertEqual(caught.exception.code, "login_required")
                    self.assertEqual(gateway.return_value.post.call_count, case["refreshes"])

    def test_profile_gateway_vectors_record_python_cli_differences(self) -> None:
        fixture = json.loads(FIXTURES.with_name("profiles.json").read_text())
        self.assertEqual(len(fixture["cases"]), 49)
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                profile = case["input"]
                try:
                    actual = validate_session_gateway(
                        profile["gateway"],
                        allow_insecure_http=profile.get("allowLoopbackHTTP", False),
                    )
                except (SessionClientError, ValueError):
                    actual = None
                self.assertEqual(actual, case["python_gateway"])

    def test_context_setting_bytes_match_the_existing_relay_parser(self) -> None:
        fixture = json.loads(FIXTURES.with_name("context-settings.json").read_text())
        self.assertEqual(len(fixture["cases"]), 20)
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                raw = case["file_bytes"]
                data = raw.encode("utf-8") if raw is not None else None
                if case["expected_enabled"] is None:
                    with self.assertRaises(ContextRuntimeError) as caught:
                        parse_context_preference(data)
                    self.assertEqual(caught.exception.code, "settings_invalid")
                else:
                    result = parse_context_preference(data)
                    self.assertIs(result.enabled, case["expected_enabled"])
                    self.assertEqual(result.schema_version, 1)
        with self.assertRaises(ContextRuntimeError):
            parse_context_preference(b"\xff")

    def test_fixture_provenance_identifiers_and_complete_native_error_catalog(self) -> None:
        for path in FIXTURES.parent.glob("*.json"):
            fixture = json.loads(path.read_text(encoding="utf-8"))
            schema_id = (
                "hormuz.native-client-raw-number-fixtures"
                if path.name == "raw-numbers.json"
                else "hormuz.native-client-fixtures"
            )
            self.assertEqual(fixture["schema_id"], schema_id, path.name)
            self.assertEqual(fixture["schema_version"], 1, path.name)
            self.assertRegex(fixture["source_revision"], r"^[0-9a-f]{40}$")
            cases = fixture.get("cases", [])
            ids = [case.get("id", case.get("code")) for case in cases]
            self.assertEqual(len(ids), len(set(ids)), path.name)
        # Catalog additions or wording changes must update the shared expectations.
        root = Path(__file__).resolve().parents[1]
        swift = (root / "clients/macos/Sources/HormuzClientCore/ClientError.swift").read_text()
        current = dict(re.findall(r'case \.(\w+): return "([^\"]*)"', swift))
        recorded = json.loads(FIXTURES.with_name("errors.json").read_text())
        self.assertEqual(current, {case["code"]: case["swift_message"] for case in recorded["cases"]})

    def test_raw_number_vectors_preserve_gateway_expectations(self) -> None:
        fixtures = json.loads(FIXTURES.with_name("raw-numbers.json").read_text())
        self.assertEqual(len(fixtures["cases"]), 10)
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                response = json.loads(case["response_json"])
                if case["gateway_valid"]:
                    validate_contract(response)
                else:
                    with self.assertRaises(ContractValidationError):
                        validate_contract(response)

    def test_shared_vectors_preserve_gateway_schema_expectations(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(fixtures["schema_id"], "hormuz.native-client-fixtures")
        self.assertEqual(fixtures["schema_version"], 1)
        self.assertRegex(fixtures["source_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(len(fixtures["cases"]), 41)
        identifiers = [case["id"] for case in fixtures["cases"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                self.assertIsInstance(case["gateway_valid"], bool)
                self.assertIsInstance(case["client_valid"], bool)
                if case["gateway_valid"]:
                    validate_contract(case["response"])
                else:
                    with self.assertRaises(ContractValidationError):
                        validate_contract(case["response"])
                if case["gateway_valid"] != case["client_valid"]:
                    self.assertTrue(case["note"], "Document client/server responsibility differences")
                self.assertEqual(case["expected"] is not None, case["client_valid"])

    def test_usage_baseline_is_the_existing_frozen_gateway_fixture(self) -> None:
        existing = json.loads(
            (Path(__file__).parent / "fixtures" / "contracts" / "valid-v1.json").read_text()
        )
        shared = json.loads(FIXTURES.read_text(encoding="utf-8"))
        usage = next(case for case in shared["cases"] if case["id"] == "usage_current")
        self.assertEqual(usage["response"], existing["usage_summary"])


if __name__ == "__main__":
    unittest.main()
