"""Schema-13/18 finance account registration transaction proof."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from hormuz._portfolio_sql import PortfolioSQL
from hormuz._finance_account_binding_schema import QUERY_AUDIT_TABLE, TABLE_DDL
from hormuz.audit_chain import AuditChainSource
from hormuz.config import UsageStorageConfig
from hormuz.finance_account_binding import (
    parse_finance_account_bindings, parse_finance_identity, select_finance_account,
)
from hormuz.finance_account_registration import parse_account_binding_registration_request
from hormuz.finance_account_registration_store import (
    AccountBindingStorageError, REGISTRATION_TABLE, _receipt_from_row,
    append_active_account_registration, create_finance_account_registration_repository,
)
from hormuz.finance_account_capture import FinanceAccountCaptureError
from hormuz.finance_account_evidence import (
    QUERY_AUDIT_SCHEMA_ID,
    canonical_evidence_text,
    validate_finance_attempt_account_binding_event,
    validate_finance_query_audit_event,
)
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import UsageStore
from hormuz.store import ReservationScope, StorageSchemaError

if __package__:
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
    from ._sqlite import managed_sqlite_connection
else:
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase
    from _sqlite import managed_sqlite_connection


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
KEY = b"synthetic-finance-fingerprint-key"
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


def request(*, expected=None, source_version=1, source_digest="", binding_id="primary-account",
            state="active"):
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
        "state": state,
        "reason_code": (
            "created" if expected is None else "revoked" if state == "revoked" else "replaced"
        ),
    }
    return parse_account_binding_registration_request(
        json.dumps(value).encode(), organization_id="acme",
    )


def request_payload(value):
    return json.dumps({
        "schema_id": "hormuz.finance-account-binding-request",
        "schema_version": 1,
        "binding_id": value.binding_id,
        "expected_version": value.expected_version,
        "upstream_reference_id": value.upstream_reference_id,
        "upstream_reference_version": value.upstream_reference_version,
        "transport_profile": value.transport_profile,
        "inference_credential_reference_id": value.inference_credential_reference_id,
        "inference_credential_reference_version": (
            value.inference_credential_reference_version
        ),
        "source_binding": {
            "binding_id": value.source_binding_id,
            "version": value.source_binding_version,
            "content_digest": value.source_binding_digest,
        },
        "state": value.state,
        "reason_code": value.reason_code,
    }).encode()


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
               fail_reauthorize_at=None, path=None, actor_id="alice"):
        registration_request = registration_request or request(source_digest=self.source.content_digest)
        connection = sqlite3.connect(path or self.config.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        calls = []
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                sql = PortfolioSQL(connection, postgres=False, tables=TABLE_DDL)

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
                    actor_id=actor_id, reauthorize=reauthorize, append_audit=audit,
                )
            return result, calls
        finally:
            connection.close()

    def rows(self, table=REGISTRATION_TABLE, *, path=None):
        with managed_sqlite_connection(path or self.config.database_path) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY 1,2,3").fetchall()

    def test_create_replay_replace_and_stale_cas(self):
        first, calls = self.append()
        self.assertEqual((first.organization_id, first.version, first.binding_state),
                         ("acme", 1, "active"))
        self.assertEqual(calls, ["auth", "audit", "auth"])
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))
        replay, calls = self.append()
        self.assertEqual(replay, first)
        self.assertEqual(calls, ["auth", "auth"])
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

    def test_repository_historical_replay_precedes_current_configuration_lookup(self):
        historical = request(source_digest=self.source.content_digest)
        first, _ = self.append(historical)
        self.append(
            request(expected=1, source_digest=self.source.content_digest),
            config_version=2,
        )
        # The fixture configuration intentionally has no account candidate.
        # An exact historical request must still resolve from durable state.
        repository = create_finance_account_registration_repository(self.config)
        self.assertEqual(repository.register(ADMIN, request_payload(historical)), first)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (2, 2))

    def test_registered_actor_preserves_established_identity_text(self):
        receipt, _ = self.append(actor_id="Álice Example")
        self.assertEqual(receipt.version, 1)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT registered_by FROM {REGISTRATION_TABLE}"
            ).fetchone()
        self.assertEqual(row[0], "Álice Example")

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

    def test_repository_authorization_precedes_request_parsing_and_storage(self):
        repository = create_finance_account_registration_repository(self.config)
        unauthorized = PortfolioPrincipal("acme", "mallory", ("finance_viewer",))
        with self.assertRaisesRegex(AccountBindingStorageError, "^forbidden$"):
            repository.register(unauthorized, b"not-json")
        read_only = create_finance_account_registration_repository(
            self.config,
            read_only=True,
        )
        with self.assertRaisesRegex(AccountBindingStorageError, "^forbidden$"):
            read_only.register(ADMIN, b"not-json")
        self.assertEqual(self.rows(), [])

    def test_historical_replay_reauthorizes_before_return(self):
        original, _ = self.append()
        with self.assertRaisesRegex(AccountBindingStorageError, "^unavailable$"):
            self.append(fail_reauthorize_at=2)
        self.assertEqual(self.append()[0], original)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_forged_parsed_request_cannot_alias_replay_or_bypass_cas(self):
        original = request(source_digest=self.source.content_digest)
        forged = (
            replace(original, request_digest="f" * 64),
            replace(original, upstream_reference_id="different-upstream"),
            replace(original, expected_version=True),
            replace(original, source_binding_digest="f" * 64),
        )
        for invalid in forged:
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(AccountBindingStorageError, "^binding_conflict$"):
                    self.append(invalid)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (0, 0))

        first, _ = self.append(original)
        for invalid in forged:
            with self.subTest(replay=invalid):
                with self.assertRaisesRegex(AccountBindingStorageError, "^binding_conflict$"):
                    self.append(invalid)
        self.assertEqual(self.append(original)[0], first)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_concurrent_exact_replays_append_one_version_and_one_audit(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.append) for _ in range(2)]
            receipts = [future.result()[0] for future in futures]
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_registration_rows_are_append_only(self):
        self.append()
        with self.connect() as connection:
            for statement in (
                f"UPDATE {REGISTRATION_TABLE} SET content_digest='{'f' * 64}'",
                f"DELETE FROM {REGISTRATION_TABLE}",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_canonical_boolean_version_in_historical_evidence_fails_closed(self):
        self.append()
        # JSON true compares equal to Python integer 1. An exact row-to-evidence
        # type check is required even when the evidence remains canonical JSON.
        with self.connect() as connection:
            row = dict(connection.execute(f"SELECT * FROM {REGISTRATION_TABLE}").fetchone())
        evidence = json.loads(row["evidence_json"])
        evidence["version"] = True
        row["evidence_json"] = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            _receipt_from_row(row)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (1, 1))

    def test_canonical_boolean_previous_version_in_successor_fails_closed(self):
        self.append()
        successor = request(expected=1, source_digest=self.source.content_digest)
        self.append(successor, config_version=2)
        with self.connect() as connection:
            row = dict(connection.execute(
                f"SELECT * FROM {REGISTRATION_TABLE} WHERE version=2"
            ).fetchone())
        evidence = json.loads(row["evidence_json"])
        evidence["previous_version"] = True
        row["evidence_json"] = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(AccountBindingStorageError, "unavailable"):
            _receipt_from_row(row)
        self.assertEqual((len(self.rows()), len(self.rows("test_registration_audit"))), (2, 2))

    def test_revocation_copies_prior_coordinates_without_current_source_authority(self):
        first, _ = self.append()
        revoked_source = self.bind_source(expected=1, state="revoked")
        self.assertEqual(revoked_source.binding_state, "revoked")
        revoked, _ = self.append(
            request(
                expected=1,
                source_digest=self.source.content_digest,
                state="revoked",
            ),
            config_version=2,
        )
        self.assertEqual((revoked.version, revoked.binding_state), (2, "revoked"))
        rows = self.rows()
        self.assertEqual(rows[0][12:20], rows[1][12:20])
        self.assertEqual(first.binding_state, "active")

    def test_attempt_capture_is_bound_audited_and_atomic_before_egress(self):
        self.append()
        store = UsageStore(self.config.database_path)
        identity = next(
            identity
            for identity in self.config.identities_by_token.values()
            if identity.actor_id == "alice"
        )
        arguments = {
            "identity": identity,
            "client": "codex",
            "protocol": "openai",
            "requested_model": "synthetic",
            "resolved_alias": "synthetic",
            "upstream_model": "synthetic-provider",
            "policy_version": "test",
            "policy_action": "allowed",
            "redaction_count": 0,
            "redaction_rules": (),
            "scopes": (ReservationScope(name="organization"),),
            "reserved_tokens": 1,
            "reserved_cost_microusd": 1,
            "ttl_seconds": 60,
            "work_budget": None,
            "finance_account": selected(),
        }
        attempt = store._begin_request_attempt_with_work_budget(**arguments)
        with self.connect() as connection:
            row = dict(connection.execute(
                "SELECT * FROM gateway_finance_attempt_account_bindings "
                "WHERE request_attempt_id=?",
                (attempt.attempt_id,),
            ).fetchone())
            event = json.loads(row.pop("evidence_json"))
            self.assertEqual(row, {
                key: value
                for key, value in event.items()
                if key not in {"schema_id", "schema_version"}
            })
            self.assertEqual(event["state"], "bound")
            validate_finance_attempt_account_binding_event(event)
            audit = connection.execute(
                "SELECT source_event_id FROM gateway_audit_chain_entries "
                "WHERE source_schema_id='hormuz.finance-attempt-account-binding' "
                "AND source_event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertIsNotNone(audit)
        store.verify_audit_chain(organization_id="acme")

        with self.connect() as connection:
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "gateway_request_attempts",
                    "gateway_budget_reservations",
                    "gateway_finance_attempt_account_bindings",
                    "gateway_audit_chain_entries",
                )
            }

        from hormuz import store as store_module

        real_capture = store_module.append_finance_attempt_account_binding

        def capture_then_fail(*args, **kwargs):
            real_capture(*args, **kwargs)
            raise FinanceAccountCaptureError()

        with mock.patch.object(
            store_module,
            "append_finance_attempt_account_binding",
            side_effect=capture_then_fail,
        ), self.assertRaisesRegex(StorageSchemaError, "finance_account_binding_unavailable"):
            store._begin_request_attempt_with_work_budget(**arguments)
        with self.connect() as connection:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(after, before)

    def test_query_audit_source_must_exist_and_match_exact_canonical_evidence(self):
        store = UsageStore(self.config.database_path)
        event = {
            "schema_id": QUERY_AUDIT_SCHEMA_ID,
            "schema_version": 1,
            "organization_id": "acme",
            "query_event_id": str(uuid4()),
            "actor_id": "alice",
            "query_class": "finance_coverage_report_v1",
            "binding_id": self.source.binding_id,
            "binding_version": self.source.version,
            "collection_profile": "openai.organization-usage-completions.v1",
            "query_start_at": "2026-09-01T00:00:00.000000Z",
            "query_end_at": "2026-09-02T00:00:00.000000Z",
            "as_of_commit_sequence": 0,
            "currency": "USD",
            "selected_snapshot_count": 0,
            "coverage_bucket_count": 0,
            "provider_observation_count": 0,
            "terminal_attempt_count": 0,
            "terminal_attempts_missing_sidecar_count": 0,
            "occurred_at": "2026-09-02T00:00:00.000000Z",
        }
        validate_finance_query_audit_event(event)
        source = AuditChainSource(
            QUERY_AUDIT_SCHEMA_ID,
            1,
            str(event["query_event_id"]),
        )
        with store._connection() as connection, self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "finance_account_audit_source_missing",
        ):
            store._append_audit_chain_entry_in_connection(
                connection,
                event=event,
                source=source,
            )

        row = {
            key: value
            for key, value in event.items()
            if key not in {"schema_id", "schema_version"}
        }
        row["evidence_json"] = canonical_evidence_text(event)
        with store._connection() as connection:
            columns = tuple(row)
            connection.execute(
                f"INSERT INTO {QUERY_AUDIT_TABLE} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(row.values()),
            )

        tampered = {**event, "selected_snapshot_count": 1}
        with store._connection() as connection, self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "finance_account_audit_source_missing",
        ):
            store._append_audit_chain_entry_in_connection(
                connection,
                event=tampered,
                source=source,
            )
        with store._connection() as connection:
            store._append_audit_chain_entry_in_connection(
                connection,
                event=event,
                source=source,
            )
        store.verify_audit_chain(organization_id="acme")

    def test_old_backup_restores_separately_and_forward_pair_retains_successor(self):
        first, _ = self.append()
        restored = self.root / "restored.sqlite3"
        with managed_sqlite_connection(self.config.database_path) as source, managed_sqlite_connection(restored) as target:
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
    def test_new_tables_have_only_runtime_read_append_and_owner_cannot_mutate(self):
        tables = (
            "portfolio_finance_account_binding_versions",
            "gateway_finance_attempt_account_bindings",
            "portfolio_finance_query_audit_events",
        )
        with self.store._transaction("acme") as connection:
            for table in tables:
                qualified = f'"{self.schema}".{table}'
                privileges = connection.execute(
                    "SELECT has_table_privilege(current_user, %s, 'SELECT') AS can_select, "
                    "has_table_privilege(current_user, %s, 'INSERT') AS can_insert, "
                    "has_table_privilege(current_user, %s, 'UPDATE') AS can_update, "
                    "has_table_privilege(current_user, %s, 'DELETE') AS can_delete, "
                    "has_table_privilege(current_user, %s, 'TRUNCATE') AS can_truncate",
                    (qualified, qualified, qualified, qualified, qualified),
                ).fetchone()
                self.assertEqual(
                    dict(privileges),
                    {
                        "can_select": True,
                        "can_insert": True,
                        "can_update": False,
                        "can_delete": False,
                        "can_truncate": False,
                    },
                )
        with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
            for table in tables:
                qualified = self.sql.SQL("{}.{}").format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(table),
                )
                for statement in (
                    self.sql.SQL("UPDATE {} SET organization_id=organization_id").format(qualified),
                    self.sql.SQL("DELETE FROM {}").format(qualified),
                    self.sql.SQL("TRUNCATE TABLE {}").format(qualified),
                ):
                    with self.subTest(table=table, statement=statement.as_string(connection)):
                        try:
                            connection.execute(statement)
                        except self.psycopg.Error as error:
                            self.assertIn(error.sqlstate, {"23514", "0A000"})
                        else:
                            self.fail("append-only table accepted a mutation")

    def test_runtime_registration_and_attempt_capture_are_audited_and_tenant_scoped(self):
        base = registry_config(Path("/unused/synthetic-registration-runtime"))
        upstream = replace(
            base.upstreams["openai"],
            base_url="https://api.openai.com/v1",
            finance_identity=parse_finance_identity({
                "upstream_reference_id": "openai-primary",
                "upstream_reference_version": 1,
                "transport_profile": "openai.first-party.v1",
                "inference_credential_reference_id": "inference-primary",
                "inference_credential_reference_version": 2,
            }),
        )
        config = replace(
            base,
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
            upstreams={**base.upstreams, "openai": upstream},
            finance_account_bindings=parse_finance_account_bindings([{
                "organization_id": "acme",
                "upstream_reference_id": "openai-primary",
                "binding_id": "primary-account",
                "binding_version": 1,
            }]),
        )
        collection = create_finance_collection_repository(
            config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        source = collection.bind_source(ADMIN, {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1,
            "binding_id": "source-primary",
            "expected_version": None,
            "provider": "openai",
            "provider_account_reference_id": "raw-provider-account",
            "scope": {"kind": "projects", "ids": ["synthetic-project"]},
            "credential_reference_version": 1,
            "fingerprint_key_version": 1,
            "state": "active",
            "reason_code": "created",
        }, fingerprint_key=KEY)
        payload = json.dumps({
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
                "binding_id": source.binding_id,
                "version": source.version,
                "content_digest": source.content_digest,
            },
            "state": "active",
            "reason_code": "created",
        }).encode()
        account_repository = create_finance_account_registration_repository(
            config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        receipt = account_repository.register(ADMIN, payload)
        self.assertEqual((receipt.binding_id, receipt.version), ("primary-account", 1))

        identity = next(
            item
            for item in config.identities_by_token.values()
            if item.actor_id == "alice"
        )
        attempt = self.store._begin_request_attempt_with_work_budget(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="synthetic",
            resolved_alias="synthetic",
            upstream_model="synthetic-provider",
            policy_version="test",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(ReservationScope(name="organization"),),
            reserved_tokens=1,
            reserved_cost_microusd=1,
            ttl_seconds=60,
            work_budget=None,
            finance_account=selected(),
        )
        with self.psycopg.connect(
            self.owner_dsn,
            row_factory=self.psycopg.rows.dict_row,
        ) as connection:
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(
                    self.sql.Identifier(self.schema)
                )
            )
            sidecar = connection.execute(
                "SELECT state, binding_id, binding_version "
                "FROM gateway_finance_attempt_account_bindings "
                "WHERE request_attempt_id=%s",
                (attempt.attempt_id,),
            ).fetchone()
            self.assertEqual(
                dict(sidecar),
                {
                    "state": "bound",
                    "binding_id": "primary-account",
                    "binding_version": 1,
                },
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM gateway_audit_chain_entries "
                    "WHERE source_schema_id IN (%s, %s)",
                    (
                        "hormuz.finance-account-binding-version",
                        "hormuz.finance-attempt-account-binding",
                    ),
                ).fetchone()["count"],
                2,
            )
        with self.store._transaction("beta") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS count FROM "
                    f"{self.store._table('gateway_finance_attempt_account_bindings')}"
                )
                self.assertEqual(cursor.fetchone()["count"], 0)
        self.store.verify_audit_chain(organization_id="acme")

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
                sql = PortfolioSQL(connection, postgres=True, tables=TABLE_DDL)

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
    def test_exact_six_grants_match_implemented_migration(self):
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "hormuz/migrations/postgresql/0018_finance_account_binding_and_query_audit.sql"
        ).read_text()
        statements = [line.strip() for line in migration.splitlines()
                      if line.strip().startswith("GRANT SELECT, INSERT ON")]
        self.assertEqual(statements, [
            "GRANT SELECT, INSERT ON {schema}.portfolio_finance_account_binding_versions TO {runtime_role};",
            "GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_account_bindings TO {runtime_role};",
            "GRANT SELECT, INSERT ON {schema}.portfolio_finance_query_audit_events TO {runtime_role};",
        ])

    def test_runtime_table_mapping_is_the_real_schema(self):
        self.assertEqual(
            set(TABLE_DDL),
            {
                "portfolio_finance_account_binding_versions",
                "gateway_finance_attempt_account_bindings",
                "portfolio_finance_query_audit_events",
            },
        )


if __name__ == "__main__":
    unittest.main()
