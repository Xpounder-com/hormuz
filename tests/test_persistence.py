from __future__ import annotations

import ast
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

import hormuz._audit_verifier as audit_verifier
import hormuz._persistence as persistence
import hormuz.audit_chain as audit_chain
from hormuz.audit_chain import build_audit_chain_entry, build_custody_audit_chain_entry
from hormuz.config import Identity
from hormuz.store import (
    MonthlyTotals,
    RequestAttempt,
    RequestAttemptStateError,
    ReservationDenied,
    ReservationScope,
    SecretTotals,
    UsageRepository,
)


class _StorageError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PersistenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = Identity(
            token_env="HORMUZ_TOKEN",
            token="secret",
            actor_id="alice",
            actor_name="Alice",
            team_id="engineering",
            team_name="Engineering",
            organization_id="acme",
            identity_type="human",
            authentication_source="oidc",
        )
        self.now = datetime(2026, 8, 23, 12, 30, tzinfo=timezone.utc)

    def test_store_remains_the_public_compatibility_facade(self) -> None:
        self.assertIs(MonthlyTotals, persistence.MonthlyTotals)
        self.assertIs(SecretTotals, persistence.SecretTotals)
        self.assertIs(ReservationScope, persistence.ReservationScope)
        self.assertIs(RequestAttempt, persistence.RequestAttempt)
        self.assertIs(RequestAttemptStateError, persistence.RequestAttemptStateError)
        self.assertIs(ReservationDenied, persistence.ReservationDenied)
        self.assertIs(UsageRepository, persistence.UsageRepository)

    def test_request_attempt_builders_preserve_canonical_evidence(self) -> None:
        root = persistence.build_request_attempt_root(
            attempt_id="264f8281-8322-4d2f-84be-c94405fa87d5",
            created_at=self.now,
            identity=self.identity,
            organization_id="acme",
            client="codex",
            protocol="openai",
            requested_model="engineering-fast",
            resolved_alias="engineering-fast",
            upstream_model="gpt-5.4",
            policy_version="engineering-v1",
            policy_action="allowed+redacted",
            redaction_count=-1,
            redaction_rules=("openai_api_key", "anthropic_api_key", "openai_api_key"),
            reserved_tokens=-5,
            reserved_cost_microusd=-10,
        )
        self.assertEqual(root["created_at"], "2026-08-23T12:30:00+00:00")
        self.assertEqual(root["redaction_count"], 0)
        self.assertEqual(root["redaction_rules"], ["anthropic_api_key", "openai_api_key"])
        self.assertEqual(root["reserved_tokens"], 0)
        self.assertEqual(root["reserved_cost_microusd"], 0)

        event = persistence.build_request_attempt_event(
            event_id="1c4b36b3-ea41-49e7-954b-e2d6aac9d8ea",
            attempt_id=str(root["attempt_id"]),
            organization_id="acme",
            occurred_at=self.now,
            sequence=1,
            state="pending",
            reason_code=None,
            usage_event_id=None,
        )
        self.assertEqual(event["occurred_at"], root["created_at"])
        self.assertEqual(event["state"], "pending")

    def test_request_attempt_result_normalizes_sqlite_and_postgres_row_shape(self) -> None:
        row: dict[str, object] = {
            "actor_id": "alice",
            "actor_name": "Alice",
            "team_id": "engineering",
            "team_name": "Engineering",
            "organization_id": "acme",
            "identity_type": "human",
            "authentication_source": "oidc",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "engineering-fast",
            "resolved_alias": None,
            "upstream_model": "gpt-5.4",
            "policy_version": "engineering-v1",
            "policy_action": "allowed",
            "redaction_count": 2,
            "redaction_rules": '["anthropic_api_key","openai_api_key"]',
        }
        result = persistence.normalize_request_attempt_result(row, error_factory=_StorageError)
        self.assertEqual(result.identity.organization_id, "acme")
        self.assertEqual(result.identity.token_env, "REQUEST_ATTEMPT_LEDGER")
        self.assertEqual(result.resolved_alias, None)
        self.assertEqual(result.redaction_rules, ("anthropic_api_key", "openai_api_key"))

        row["redaction_rules"] = "not-json"
        with self.assertRaisesRegex(_StorageError, "request_attempt_evidence_malformed"):
            persistence.normalize_request_attempt_result(row, error_factory=_StorageError)

    def test_shared_state_and_audit_normalizers_preserve_stable_failures(self) -> None:
        latest = persistence.normalize_request_attempt_state({"sequence": 2, "state": "succeeded"})
        self.assertEqual((latest.sequence, latest.state), (2, "succeeded"))
        persistence.require_terminal_request_attempt_state("rate_limited")
        persistence.require_pending_request_attempt_state("pending")
        self.assertFalse(persistence.should_mark_request_attempt_unknown("outcome_unknown"))
        with self.assertRaisesRegex(RequestAttemptStateError, "request_attempt_not_pending"):
            persistence.should_mark_request_attempt_unknown("failed")

        head = persistence.normalize_audit_chain_head(
            {
                "organization_id": "acme",
                "chain_version": 1,
                "chain_epoch": 3,
                "sequence": 12,
                "head_digest": "a" * 64,
            },
            error_factory=_StorageError,
        )
        self.assertEqual((head.organization_id, head.chain_epoch, head.sequence), ("acme", 3, 12))
        with self.assertRaisesRegex(_StorageError, "audit_chain_head_malformed"):
            persistence.normalize_audit_chain_head(
                {
                    "organization_id": "acme",
                    "chain_version": 1,
                    "chain_epoch": 1,
                    "sequence": 0,
                    "head_digest": b"not-text",
                },
                error_factory=_StorageError,
            )

    def test_audit_verification_inputs_normalize_v1_v2_and_source_identity(self) -> None:
        fixtures = json.loads(
            (Path(__file__).parent / "fixtures" / "contracts" / "valid-v1.json").read_text()
        )
        v1_event = fixtures["audit_usage_v2"]
        v1_entry = build_audit_chain_entry(
            v1_event,
            chain_version=1,
            chain_epoch=1,
            sequence=1,
            previous_digest=None,
        )
        v1_input = persistence.normalize_audit_chain_entry_input(
            {
                "entry_schema_id": v1_entry["schema_id"],
                "entry_schema_version": v1_entry["schema_version"],
                "chain_version": v1_entry["chain_version"],
                "chain_epoch": v1_entry["chain_epoch"],
                "sequence": v1_entry["sequence"],
                "event_id": v1_event["id"],
                "previous_digest": v1_entry["previous_digest"],
                "event_digest": v1_entry["event_digest"],
                "event_json": json.dumps(v1_entry["event"]),
                "source_schema_id": None,
                "source_schema_version": None,
                "source_event_id": None,
            },
            organization_id="xpounder",
            error_factory=_StorageError,
        )
        self.assertEqual(v1_input.source.schema_id, "hormuz.audit-event")
        self.assertEqual(v1_input.source.event_id, v1_event["id"])

        epoch = persistence.normalize_audit_chain_epoch_input(
            {
                "chain_version": 1,
                "chain_epoch": 1,
                "reason_code": "initial_adoption",
                "predecessor_chain_epoch": None,
                "predecessor_sequence": None,
                "predecessor_head_digest": None,
            },
            error_factory=_StorageError,
        )
        checkpoint = persistence.normalize_audit_chain_checkpoint_input(
            fixtures["audit_chain_checkpoint_v1"],
            organization_id="xpounder",
            error_factory=_StorageError,
        )
        self.assertEqual((epoch.chain_version, epoch.chain_epoch), (1, 1))
        self.assertIsNotNone(checkpoint)
        self.assertEqual((checkpoint.chain_epoch, checkpoint.sequence), (1, 1))

        source_event_id = "01234567-89ab-4def-8123-456789abcdef"
        v2_event = fixtures["custody_control_event"]
        v2_entry = build_custody_audit_chain_entry(
            v2_event,
            source_schema_id="hormuz.custody-control-event",
            source_schema_version=1,
            source_event_id=source_event_id,
            chain_version=1,
            chain_epoch=1,
            sequence=2,
            previous_digest=str(v1_entry["event_digest"]),
        )
        v2_input = persistence.normalize_audit_chain_entry_input(
            {
                "entry_schema_id": v2_entry["schema_id"],
                "entry_schema_version": v2_entry["schema_version"],
                "chain_version": v2_entry["chain_version"],
                "chain_epoch": v2_entry["chain_epoch"],
                "sequence": v2_entry["sequence"],
                "event_id": source_event_id,
                "previous_digest": v2_entry["previous_digest"],
                "event_digest": v2_entry["event_digest"],
                "event_json": json.dumps(v2_entry["event"]),
                "source_schema_id": v2_entry["source_schema_id"],
                "source_schema_version": v2_entry["source_schema_version"],
                "source_event_id": v2_entry["source_event_id"],
            },
            organization_id="xpounder",
            error_factory=_StorageError,
        )
        v2_source = persistence.normalize_audit_chain_source_event_input(
            v2_event,
            source=v2_input.source,
            error_factory=_StorageError,
        )
        self.assertEqual(v2_input.source.schema_id, "hormuz.custody-control-event")
        self.assertEqual(
            persistence.audit_chain_source_event_map(
                (v2_source,),
                error_factory=_StorageError,
            )[v2_input.source],
            v2_event,
        )

    def test_sqlite_v2_claim_without_source_columns_fails_with_stable_storage_error(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT 1 AS chain_version, 1 AS chain_epoch, 1 AS sequence,
                   'hormuz.audit-chain-entry' AS entry_schema_id,
                   2 AS entry_schema_version, 'event-id' AS event_id,
                   NULL AS previous_digest, ? AS event_digest, ? AS event_json
            """,
            ("a" * 64, json.dumps({"id": "event-id"})),
        ).fetchone()
        connection.close()

        with self.assertRaises(_StorageError) as raised:
            persistence.normalize_audit_chain_entry_input(
                row,
                organization_id="acme",
                error_factory=_StorageError,
            )
        self.assertEqual(raised.exception.code, "audit_chain_entry_malformed")

    def test_backend_neutral_module_cannot_import_database_adapters(self) -> None:
        forbidden = {"sqlite3", "psycopg", "psycopg_pool", "store", "postgres_usage_store", "postgres"}
        for module in (persistence, audit_chain, audit_verifier):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                tree = ast.parse(source)
                imported = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                imported.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertTrue(imported.isdisjoint(forbidden), imported.intersection(forbidden))

        postgres_adapter = Path(persistence.__file__).with_name("postgres_usage_store.py")
        postgres_tree = ast.parse(postgres_adapter.read_text(encoding="utf-8"))
        postgres_imports = {
            node.module or ""
            for node in ast.walk(postgres_tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("store", postgres_imports)

    def test_shared_verifier_and_storage_adapters_keep_distinct_ownership(self) -> None:
        verifier_source = Path(audit_verifier.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".execute(", verifier_source)
        self.assertNotIn("_transaction(", verifier_source)

        sqlite_source = Path(persistence.__file__).with_name("store.py").read_text(encoding="utf-8")
        self.assertIn('connection.execute("BEGIN")', sqlite_source)
        self.assertIn("_audit_chain_source_events_in_connection", sqlite_source)
        self.assertIn("return verify_audit_chain_inputs(inputs)", sqlite_source)
        self.assertIn("raise StorageSchemaError(error.code) from None", sqlite_source)

        postgres_source = Path(persistence.__file__).with_name("postgres_usage_store.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("with self._transaction(organization) as connection", postgres_source)
        self.assertIn("for_share=True", postgres_source)
        self.assertIn("_custody_audit_chain_source_events_in_cursor", postgres_source)
        self.assertIn("return verify_audit_chain_inputs(inputs)", postgres_source)
        self.assertIn("raise PostgresStorageError(error.code) from None", postgres_source)


if __name__ == "__main__":
    unittest.main()
