"""Dormant registration CAS proof; test DDL is not the schema-13/18 migration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hormuz._portfolio_sql import PortfolioSQL
from hormuz.config import UsageStorageConfig
from hormuz.finance_account_binding import (
    parse_finance_account_bindings, parse_finance_identity, select_finance_account,
)
from hormuz.finance_account_registration import parse_account_binding_registration_request
from hormuz.finance_account_registration_store import (
    AccountBindingStorageError, REGISTRATION_TABLE, append_active_account_registration,
)
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import UsageStore

if __package__:
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
KEY = b"synthetic-finance-fingerprint-key"
REGISTRATION_WITNESS = """
CREATE TABLE portfolio_finance_account_binding_versions (
    organization_id TEXT NOT NULL, binding_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
    binding_event_id TEXT NOT NULL, previous_version INTEGER,
    binding_state TEXT NOT NULL CHECK (binding_state='active'),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('created','replaced')),
    upstream_reference_id TEXT NOT NULL, upstream_reference_version INTEGER NOT NULL,
    transport_profile TEXT NOT NULL, inference_credential_reference_id TEXT NOT NULL,
    inference_credential_reference_version INTEGER NOT NULL,
    source_binding_id TEXT NOT NULL, source_binding_version INTEGER NOT NULL,
    source_binding_digest TEXT NOT NULL, provider TEXT NOT NULL,
    provider_account_fingerprint TEXT NOT NULL, scope_kind TEXT NOT NULL,
    scope_fingerprints_json TEXT NOT NULL, fingerprint_key_version INTEGER NOT NULL,
    content_digest TEXT NOT NULL, request_digest TEXT NOT NULL,
    registered_by TEXT NOT NULL, registered_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (organization_id,binding_id,version),
    UNIQUE (organization_id,binding_event_id),
    UNIQUE (organization_id,binding_id,request_digest),
    FOREIGN KEY (organization_id,source_binding_id,source_binding_version)
        REFERENCES portfolio_finance_source_binding_versions
            (organization_id,binding_id,version),
    CHECK ((version=1 AND previous_version IS NULL)
        OR (version>1 AND previous_version=version-1))
)
"""


def selected(*, version=1, tenant="acme"):
    return select_finance_account(
        organization_id=tenant, protocol="openai", base_url="https://api.openai.com/v1",
        identity=parse_finance_identity({
            "upstream_reference_id": "openai-primary",
            "upstream_reference_version": 1,
            "transport_profile": "openai.first-party.v1",
            "inference_credential_reference_id": "inference-primary",
            "inference_credential_reference_version": 2,
        }),
        bindings=parse_finance_account_bindings([{
            "organization_id": tenant,
            "upstream_reference_id": "openai-primary",
            "binding_id": "primary-account",
            "binding_version": version,
        }]),
    )


def request(*, expected=None, source_version=1, source_digest="", binding_id="primary-account"):
    value = {
        "schema_id": "hormuz.finance-account-binding-request",
        "schema_version": 1,
        "binding_id": binding_id,
        "expected_version": expected,
        "upstream_reference_id": "openai-primary",
        "upstream_reference_version": 1,
        "transport_profile": "openai.first-party.v1",
        "inference_credential_reference_id": "inference-primary",
        "inference_credential_reference_version": 2,
        "source_binding": {
            "binding_id": "source-primary", "version": source_version,
            "content_digest": source_digest,
        },
        "state": "active",
        "reason_code": "created" if expected is None else "replaced",
    }
    return parse_account_binding_registration_request(
        json.dumps(value).encode(), organization_id="acme",
    )


class SQLiteRegistrationStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        UsageStore(self.config.database_path)
        self.collection = create_finance_collection_repository(self.config)
        self.source = self.bind_source()
        with self.connect() as connection:
            connection.execute(REGISTRATION_WITNESS)
            connection.execute(
                "CREATE TABLE test_registration_audit "
                "(organization_id TEXT NOT NULL, event_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL)"
            )

    def connect(self):
        connection = sqlite3.connect(self.config.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(connection.close)
        return connection

    def bind_source(self, *, expected=None, account="raw-provider-account", state="active"):
        return self.collection.bind_source(ADMIN, {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1,
            "binding_id": "source-primary",
            "expected_version": expected,
            "provider": "openai",
            "provider_account_reference_id": account,
            "scope": {"kind": "projects", "ids": ["synthetic-project"]},
            "credential_reference_version": 1,
            "fingerprint_key_version": 1,
            "state": state,
            "reason_code": "created" if expected is None else ("revoked" if state == "revoked" else "replaced"),
        }, fingerprint_key=KEY)

    def append(self, registration_request=None, *, config_version=1, audit_fails=False,
               fail_reauthorize_at=None, path=None):
        registration_request = registration_request or request(source_digest=self.source.content_digest)
        connection = sqlite3.connect(path or self.config.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        calls = []
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                sql = PortfolioSQL(connection, postgres=False, tables={REGISTRATION_TABLE: REGISTRATION_WITNESS})

                def reauthorize():
                    calls.append("auth")
                    if len([call for call in calls if call == "auth"]) == fail_reauthorize_at:
                        raise AccountBindingStorageError("unavailable")

                def audit(event):
                    calls.append("audit")
                    if audit_fails:
                        raise AccountBindingStorageError("unavailable")
                    connection.execute(
                        "INSERT INTO test_registration_audit VALUES (?,?,?)",
                        (event["organization_id"], event["binding_event_id"],
                         json.dumps(event, sort_keys=True, separators=(",", ":"))),
                    )

                result = append_active_account_registration(
                    sql, registration_request, selected=selected(version=config_version),
                    actor_id="alice", reauthorize=reauthorize, append_audit=audit,
                )
            return result, calls
        finally:
            connection.close()

    def rows(self, table=REGISTRATION_TABLE, *, path=None):
        with sqlite3.connect(path or self.config.database_path) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY 1,2,3").fetchall()

    def test_create_replay_replace_and_stale_cas(self):
        first, calls = self.append()
        self.assertEqual((first.organization_id, first.version, first.binding_state),
                         ("acme", 1, "active"))
        self.assertEqual(calls, ["auth", "audit", "auth"])
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))
        replay, calls = self.append()
        self.assertEqual(replay, first)
        self.assertEqual(calls, ["auth"])
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

        second_request = request(expected=1, source_digest=self.source.content_digest)
        second, _ = self.append(second_request, config_version=2)
        self.assertEqual(second.version, 2)
        self.assertNotEqual(second.binding_event_id, first.binding_event_id)
        self.assertEqual(self.append()[0], first)  # historical replay survives successor
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (2, 2))
        stale = request(expected=1, source_digest="f" * 64)
        with self.assertRaisesRegex(AccountBindingStorageError, "binding_conflict"):
            self.append(stale, config_version=2)

    def test_absent_stale_or_revoked_source_never_registers(self):
        invalid = request(source_digest="f" * 64)
        with self.assertRaisesRegex(AccountBindingStorageError, "binding_conflict"):
            self.append(invalid)
        self.assertEqual(self.rows(), [])
        replacement = self.bind_source(expected=1, account="new-provider-account")
        self.assertEqual(replacement.version, 2)
        with self.assertRaisesRegex(AccountBindingStorageError, "binding_conflict"):
            self.append()
        self.assertEqual(self.rows(), [])
        revoked = self.bind_source(expected=2, state="revoked")
        self.assertEqual(revoked.version, 3)
        current_request = request(source_version=3, source_digest=revoked.content_digest)
        with self.assertRaisesRegex(AccountBindingStorageError, "binding_conflict"):
            self.append(current_request)
        self.assertEqual(self.rows(), [])

    def test_audit_and_authorization_failure_roll_back(self):
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append(audit_fails=True)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows("test_registration_audit"), [])
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append(fail_reauthorize_at=1)
        self.assertEqual(self.rows(), [])
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append(fail_reauthorize_at=2)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows("test_registration_audit"), [])

    def test_concurrent_exact_replays_append_one_version_and_one_audit(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.append) for _ in range(2)]
            receipts = [future.result()[0] for future in futures]
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_corrupted_historical_receipt_fails_closed(self):
        self.append()
        # Only the test witness permits mutation; the proposed production
        # successor must reject UPDATE/DELETE/REPLACE on this table.
        with self.connect() as connection:
            connection.execute(
                f"UPDATE {REGISTRATION_TABLE} SET content_digest=?",
                ("f" * 64,),
            )
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append()
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_canonical_boolean_version_in_historical_evidence_fails_closed(self):
        self.append()
        # JSON true compares equal to Python integer 1. An exact row-to-evidence
        # type check is required even when the evidence remains canonical JSON.
        with self.connect() as connection:
            evidence = json.loads(connection.execute(
                f"SELECT evidence_json FROM {REGISTRATION_TABLE}"
            ).fetchone()[0])
            evidence["version"] = True
            connection.execute(
                f"UPDATE {REGISTRATION_TABLE} SET evidence_json=?",
                (json.dumps(evidence, sort_keys=True, separators=(",", ":")),),
            )
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append()
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_canonical_boolean_previous_version_in_successor_fails_closed(self):
        self.append()
        successor = request(expected=1, source_digest=self.source.content_digest)
        self.append(successor, config_version=2)
        with self.connect() as connection:
            evidence = json.loads(connection.execute(
                f"SELECT evidence_json FROM {REGISTRATION_TABLE} WHERE version=2"
            ).fetchone()[0])
            evidence["previous_version"] = True
            connection.execute(
                f"UPDATE {REGISTRATION_TABLE} SET evidence_json=? WHERE version=2",
                (json.dumps(evidence, sort_keys=True, separators=(",", ":")),),
            )
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            self.append(successor, config_version=2)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (2, 2))

    def test_old_backup_restores_separately_and_forward_pair_retains_successor(self):
        first, _ = self.append()
        restored = self.root / "restored.sqlite3"
        with sqlite3.connect(self.config.database_path) as source, sqlite3.connect(restored) as target:
            source.backup(target)
        second, _ = self.append(request(expected=1, source_digest=self.source.content_digest),
                                config_version=2)
        self.assertEqual([row[2] for row in self.rows()], [1, 2])
        self.assertEqual([row[2] for row in self.rows(path=restored)], [1])
        self.assertEqual(self.append(path=restored)[0], first)
        self.assertEqual(self.append()[0], first)
        self.assertEqual([row[2] for row in self.rows()], [1, 2])
        self.assertEqual(second.version, 2)


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgreSQLRegistrationStoreTests(PostgresTestCase):
    def test_same_transaction_create_replay_and_audit_failure_rollback(self):
        config = replace(
            registry_config(Path("/unused/synthetic-registration")),
            usage_storage=UsageStorageConfig(
                backend="postgresql", postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        collection = create_finance_collection_repository(
            config, environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        source = collection.bind_source(ADMIN, {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1, "binding_id": "source-primary",
            "expected_version": None, "provider": "openai",
            "provider_account_reference_id": "raw-provider-account",
            "scope": {"kind": "projects", "ids": ["synthetic-project"]},
            "credential_reference_version": 1, "fingerprint_key_version": 1,
            "state": "active", "reason_code": "created",
        }, fingerprint_key=KEY)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema))
            )
            connection.execute(REGISTRATION_WITNESS)
            connection.execute(
                "CREATE TABLE test_registration_audit "
                "(organization_id TEXT NOT NULL, event_id TEXT PRIMARY KEY, evidence_json TEXT NOT NULL)"
            )

        def append(*, audit_fails=False):
            with self.psycopg.connect(
                self.owner_dsn, row_factory=self.psycopg.rows.dict_row,
            ) as connection:
                connection.execute(
                    self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema))
                )
                sql = PortfolioSQL(connection, postgres=True, tables={REGISTRATION_TABLE: REGISTRATION_WITNESS})

                def audit(event):
                    if audit_fails:
                        raise AccountBindingStorageError("unavailable")
                    sql.execute(
                        "INSERT INTO test_registration_audit VALUES (?,?,?)",
                        (event["organization_id"], event["binding_event_id"],
                         json.dumps(event, sort_keys=True, separators=(",", ":"))),
                    )

                return append_active_account_registration(
                    sql, request(source_digest=source.content_digest),
                    selected=selected(), actor_id="alice",
                    reauthorize=lambda: None, append_audit=audit,
                )

        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            append(audit_fails=True)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema))
            )
            self.assertEqual(connection.execute(
                f"SELECT COUNT(*) FROM {REGISTRATION_TABLE}"
            ).fetchone()[0], 0)
        first = append()
        self.assertEqual(append(), first)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema))
            )
            self.assertEqual(connection.execute(
                f"SELECT COUNT(*) FROM {REGISTRATION_TABLE}"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM test_registration_audit"
            ).fetchone()[0], 1)


class AccountBindingACLProposalTests(unittest.TestCase):
    def test_exact_four_grants_match_review_only_historical_plan(self):
        root = Path(__file__).resolve().parents[1]
        proposal = (root / "docs/finance-account-binding-acl-proposal.sql").read_text()
        statements = [line.strip() for line in proposal.splitlines()
                      if line.strip() and not line.lstrip().startswith("--")]
        self.assertEqual(statements, [
            "GRANT SELECT, INSERT ON {schema}.portfolio_finance_account_binding_versions TO {runtime_role};",
            "GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_account_bindings TO {runtime_role};",
        ])
        plan = json.loads((root / "docs/finance-transition-plan-v8.json").read_text())
        self.assertEqual(plan["postgresql_acl_proposal"]["proposed_schema18"], [
            203, "731d5b3bd66799555bf723adde3a9b19ac1922d1574bd48b7c79fca7752dc920",
        ])
        self.assertEqual(
            (plan["predecessor"]["postgresql_schema_version"],
             plan["predecessor"]["sqlite_schema_version"]),
            (17, 12),
        )
        self.assertTrue(plan["postgresql_acl_proposal"]["actual_implementation_grants_require_separate_approval"])

    def test_registration_witness_tracks_planned_column_names_only(self):
        root = Path(__file__).resolve().parents[1]
        plan = json.loads((root / "docs/finance-transition-plan-v8.json").read_text())
        with sqlite3.connect(":memory:") as connection:
            connection.execute(REGISTRATION_WITNESS)
            actual = [row[1] for row in connection.execute(f"PRAGMA table_info({REGISTRATION_TABLE})")]
        self.assertEqual(
            actual,
            list(plan["planned_storage"][REGISTRATION_TABLE]["columns"]),
        )


if __name__ == "__main__":
    unittest.main()
