from dataclasses import replace
from importlib import resources
from pathlib import Path

from hormuz._finance_schema import TABLE_DDL, postgres_statements
from hormuz.config import UsageStorageConfig
from hormuz.finance_repository import FinanceRateCardRepository
from hormuz.postgres import POSTGRES_SCHEMA_VERSION, postgres_transaction

if __package__:
    from ._finance_fixture import ADMIN, OTHER, AUDIT, CARDS, FinanceAssertions
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _finance_fixture import ADMIN, OTHER, AUDIT, CARDS, FinanceAssertions
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresFinanceTests(FinanceAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(registry_config(Path("/unused/synthetic-finance")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.setup_finance()

    def _rows(self, names):
        with self.psycopg.connect(self.owner_dsn, row_factory=self.psycopg.rows.dict_row) as connection:
            return {table: sorted((dict(row) for row in connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()), key=lambda row: (row.get("organization_id", ""), row.get("sequence", 0), repr(row))) for table in names}

    def finance_rows(self):
        return self._rows(TABLE_DDL)

    def legacy_rows(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            names = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (self.schema,)).fetchall()]
        return self._rows([name for name in names if name not in TABLE_DDL])

    def test_postgres_finance_real_schema_and_checked_in_migration(self):
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 15)
        self.assertEqual(len(self.legacy_rows()) + len(TABLE_DDL), 61)
        self.assertEqual(resources.files("hormuz").joinpath("migrations/postgresql/0012_finance_rate_cards.sql").read_text(),
                         postgres_statements("{schema}", "{runtime_role}"))

    def test_postgres_finance_forced_rls_append_only_and_bounded_statement(self):
        self.register()
        with self.repository._transaction(ADMIN) as sql:
            self.assertEqual(sql.one("SHOW statement_timeout")["statement_timeout"], "5s")
        with self.psycopg.connect(self.runtime_dsn) as connection:
            for table in TABLE_DDL:
                self.assertEqual(connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                    self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall(), [])
        with postgres_transaction(self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role, organization_id="beta") as connection:
            self.assertEqual(connection.execute(f"SELECT * FROM {CARDS}").fetchall(), [])
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute(f"INSERT INTO {AUDIT} VALUES ('acme','forged',999,'bob','read','synthetic-rate-card',1,%s,'2026-08-31T00:00:00.000000Z')", ("a" * 64,))
        for table in TABLE_DDL:
            for operation in ("UPDATE", "DELETE", "TRUNCATE"):
                statement = f"UPDATE {{}}.{{}} SET organization_id=organization_id" if operation == "UPDATE" else f"{operation} " + ("FROM " if operation == "DELETE" else "") + "{}.{}"
                statement = self.sql.SQL(statement).format(self.sql.Identifier(self.schema), self.sql.Identifier(table))
                with self.psycopg.connect(self.runtime_dsn) as connection:
                    with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                        connection.execute(statement)
                # TRUNCATE all FK-related tables together to exercise the trigger.
                if operation == "TRUNCATE":
                    statement = self.sql.SQL("TRUNCATE {}.{}, {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(AUDIT), self.sql.Identifier(self.schema), self.sql.Identifier(CARDS))
                with self.psycopg.connect(self.owner_dsn) as connection:
                    with self.assertRaisesRegex(self.psycopg.errors.CheckViolation, "portfolio_append_only"):
                        connection.execute(statement)

    def test_postgres_finance_weakened_rls_or_excessive_grants_fail_closed(self):
        for weaken, restore in ((f"ALTER TABLE {{}}.{CARDS} NO FORCE ROW LEVEL SECURITY", f"ALTER TABLE {{}}.{CARDS} FORCE ROW LEVEL SECURITY"),
                                (f"GRANT UPDATE ON {{}}.{CARDS} TO {{}}", f"REVOKE UPDATE ON {{}}.{CARDS} FROM {{}}")):
            args = (self.sql.Identifier(self.schema), self.sql.Identifier(self.runtime_role))
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL(weaken).format(*args))
            try:
                self.error("unavailable", self.register)
                self.error("unavailable", self.get)
            finally:
                with self.psycopg.connect(self.owner_dsn) as connection:
                    connection.execute(self.sql.SQL(restore).format(*args))
        self.assertEqual(self.finance_rows(), {AUDIT: [], CARDS: []})

    def test_postgres_finance_storage_outage_has_fixed_errors(self):
        unavailable = FinanceRateCardRepository(self.config, dsn="host=/unused/SYNTHETIC_EXCLUDED dbname=synthetic user=synthetic connect_timeout=1")
        self.error("unavailable", lambda: self.register(repository=unavailable))
        self.error("unavailable", lambda: self.get(repository=unavailable))

    def test_postgres_finance_borrowed_pool_resets_tenant_and_remains_caller_owned(self):
        pool = self._runtime_pool(min_connections=1, max_connections=1)
        repository = FinanceRateCardRepository(self.config, dsn="host=/unused/never-opened", connection_pool=pool)
        first = self.register(repository=repository)
        second = self.register(repository=repository, principal=OTHER, organization_id="beta")
        self.assertEqual(self.get(repository=repository), first)
        self.assertEqual(self.get(repository=repository, principal=OTHER), second)
        self.error("not_found", lambda: self.get(repository=repository, version=99))
        self.assertFalse(pool.closed)
        with pool.connection() as connection:
            row = connection.execute("SELECT current_setting('hormuz.organization_id', true) AS organization").fetchone()
            self.assertIn(row["organization"], (None, ""))

    def test_postgres_finance_statement_timeout_rolls_back_without_receipt(self):
        from hormuz.finance_rate_cards import rate_card_from_mapping
        if __package__:
            from ._finance_values_fixture import rate_card
        else:
            from _finance_values_fixture import rate_card

        def timeout():
            with self.repository._transaction(ADMIN) as sql:
                self.repository._audit(sql, ADMIN, "register", rate_card_from_mapping(rate_card()), "a" * 32, sql.now())
                sql.execute("SELECT pg_sleep(6)")

        self.error("unavailable", timeout)
        self.assertEqual(self.finance_rows(), {AUDIT: [], CARDS: []})

    def test_postgres_finance_corrupt_card_or_receipt_refuses_without_repair(self):
        self.register()
        for column, replacement in (("content_digest", "a" * 64), ("card_json", "{}"),
                                    ("receipt_id", "b" * 32), ("registered_by", "forged")):
            def change(value):
                with self.psycopg.connect(self.owner_dsn) as connection:
                    connection.execute(self.sql.SQL("ALTER TABLE {}.{} DISABLE TRIGGER {}").format(
                        self.sql.Identifier(self.schema), self.sql.Identifier(CARDS), self.sql.Identifier(CARDS + "_immutable")))
                    connection.execute(self.sql.SQL("UPDATE {}.{} SET {}=%s").format(
                        self.sql.Identifier(self.schema), self.sql.Identifier(CARDS), self.sql.Identifier(column)), (value,))
                    connection.execute(self.sql.SQL("ALTER TABLE {}.{} ENABLE TRIGGER {}").format(
                        self.sql.Identifier(self.schema), self.sql.Identifier(CARDS), self.sql.Identifier(CARDS + "_immutable")))

            with self.subTest(column=column):
                original = self.finance_rows()[CARDS][0][column]
                change(replacement)
                try:
                    before = self.finance_rows()
                    self.error("unavailable", self.register)
                    self.error("unavailable", self.get)
                    self.assertEqual(self.finance_rows(), before)
                finally:
                    change(original)

    def test_postgres_finance_direct_repository_refuses_partial_missing_and_newer_ledger(self):
        self.register()
        before = self.finance_rows()
        changes = (
            ("UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=15",
             "UPDATE {}.hormuz_schema_migrations SET state='applied' WHERE version=15"),
            ("UPDATE {}.hormuz_schema_migrations SET version=16 WHERE version=15",
             "UPDATE {}.hormuz_schema_migrations SET version=15 WHERE version=16"),
        )
        for change, restore in changes:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL(change).format(self.sql.Identifier(self.schema)))
            try:
                self.error("unavailable", lambda: self.register(version=2))
                self.error("unavailable", self.get)
                self.assertEqual(self.finance_rows(), before)
            finally:
                with self.psycopg.connect(self.owner_dsn) as connection:
                    connection.execute(self.sql.SQL(restore).format(self.sql.Identifier(self.schema)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            saved = connection.execute(self.sql.SQL("DELETE FROM {}.hormuz_schema_migrations WHERE version=15 RETURNING version,state,applied_at").format(self.sql.Identifier(self.schema))).fetchone()
        try:
            self.error("unavailable", lambda: self.register(version=2))
            self.error("unavailable", self.get)
            self.assertEqual(self.finance_rows(), before)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("INSERT INTO {}.hormuz_schema_migrations VALUES (%s,%s,%s)").format(self.sql.Identifier(self.schema)), saved)
