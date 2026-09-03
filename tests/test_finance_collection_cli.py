"""CLI ordering proofs for authenticated provider finance collection."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import UsageStore

if __package__:
    from ._portfolio_fixture import ADMIN as ADMIN_TOKEN, registry_config
    from ._sqlite import managed_sqlite_connection
    from .test_finance_collection_runtime import (
        ADMIN,
        KEY,
        MIDDLE,
        START,
        openai_bucket,
        openai_page,
        openai_usage,
    )
else:
    from _portfolio_fixture import ADMIN as ADMIN_TOKEN, registry_config
    from _sqlite import managed_sqlite_connection
    from test_finance_collection_runtime import (
        ADMIN,
        KEY,
        MIDDLE,
        START,
        openai_bucket,
        openai_page,
        openai_usage,
    )


class FinanceCollectionCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = registry_config(self.root)
        UsageStore(self.config.database_path)
        self.environment = {
            "HORMUZ_PORTFOLIO_TOKEN": ADMIN_TOKEN,
            "HORMUZ_FINANCE_FINGERPRINT_KEY": KEY.decode(),
            "SYNTHETIC_PROVIDER_KEY": "provider-secret-value",
        }
        self.parser = build_parser()

    def invoke(self, argv, *, dependencies=None, environment=None):
        args = self.parser.parse_args(argv)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = finance_commands.run(
                self.config,
                args,
                dependencies,
                environ=self.environment if environment is None else environment,
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def binding_request(self):
        return {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1,
            "binding_id": "provider-account",
            "expected_version": None,
            "provider": "openai",
            "provider_account_reference_id": "raw-provider-account",
            "scope": {"kind": "organization", "ids": []},
            "credential_reference_version": 1,
            "fingerprint_key_version": 1,
            "state": "active",
            "reason_code": "created",
        }

    def bind(self):
        return create_finance_collection_repository(self.config).bind_source(
            ADMIN,
            self.binding_request(),
            fingerprint_key=KEY,
        )

    def test_unauthorized_source_bind_cannot_open_file_key_or_database(self):
        missing = self.root / "must-not-open.json"
        dependencies = finance_commands.FinanceCommandDependencies(
            create_repository=mock.Mock(side_effect=AssertionError("database opened")),
        )
        status, stdout, stderr = self.invoke(
            ["finance", "source", "bind", str(missing)],
            dependencies=dependencies,
            environment={
                "HORMUZ_PORTFOLIO_TOKEN": "invalid",
                "HORMUZ_FINANCE_FINGERPRINT_KEY": "must-not-read",
            },
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {"code": "unauthenticated"}})
        dependencies.create_repository.assert_not_called()

    def test_source_binding_discards_raw_provider_identifiers(self):
        request = self.root / "binding.json"
        request.write_text(json.dumps(self.binding_request()), encoding="utf-8")
        status, stdout, stderr = self.invoke(
            ["finance", "source", "bind", str(request)]
        )
        self.assertEqual((status, stderr), (0, ""))
        output = json.loads(stdout)
        self.assertEqual(output["binding_id"], "provider-account")
        self.assertNotIn("raw-provider-account", stdout)
        with self.config.database_path.open("rb") as database:
            self.assertNotIn(b"raw-provider-account", database.read())

    def test_import_commits_pending_before_file_and_retry_never_reopens_it(self):
        self.bind()
        bundle = self.root / "bundle.json"
        page = json.loads(
            openai_page([openai_bucket(START, MIDDLE, [openai_usage()])])
        )
        bundle.write_text(
            json.dumps(
                {
                    "schema_id": "hormuz.finance-collection-file-bundle",
                    "schema_version": 1,
                    "collection_profile": "openai.organization-usage-completions.v1",
                    "query_start_at": START,
                    "query_end_at": MIDDLE,
                    "bucket_width": "1d",
                    "requested_page_size": 1,
                    "pages": [page],
                }
            ),
            encoding="utf-8",
        )
        argv = [
            "finance",
            "import",
            str(bundle),
            "provider-account",
            "1",
            "openai.organization-usage-completions.v1",
            START,
            MIDDLE,
            "--page-size",
            "1",
            "--idempotency-key",
            "stable-import",
            "--fingerprint-key-version",
            "1",
        ]
        original_read = finance_commands._read_bounded
        observed_pending = []

        def read_after_prepare(path, maximum):
            with self.config.database_path.open("rb"):
                pass
            with managed_sqlite_connection(self.config.database_path) as connection:
                observed_pending.append(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_attempts"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_events"
                    ).fetchone()[0],
                    0,
                )
            return original_read(path, maximum)

        with mock.patch.object(finance_commands, "_read_bounded", side_effect=read_after_prepare):
            status, first, stderr = self.invoke(argv)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(observed_pending, [1])
        bundle.unlink()
        status, retry, stderr = self.invoke(argv)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(first), json.loads(retry))

    def test_collect_resolves_only_selected_credential_after_pending_commit(self):
        self.bind()
        calls = []

        def resolve(config, *, environ, selection_allowed):
            calls.append(
                {
                    provider: selection_allowed(provider)
                    for provider in ("openai", "anthropic")
                }
            )
            return {"openai": environ["SYNTHETIC_PROVIDER_KEY"], "anthropic": ""}

        def fetch(value, *, credential, base_url):
            with managed_sqlite_connection(self.config.database_path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_attempts"
                    ).fetchone()[0],
                    1,
                )
            self.assertEqual(credential, "provider-secret-value")
            self.assertEqual(base_url, "https://api.openai.com")
            return (openai_page([openai_bucket(START, MIDDLE, [openai_usage()])]),)

        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=resolve,
            fetch_pages=fetch,
        )
        status, stdout, stderr = self.invoke(
            [
                "finance",
                "collect",
                "provider-account",
                "1",
                "openai.organization-usage-completions.v1",
                START,
                MIDDLE,
                "--page-size",
                "1",
                "--idempotency-key",
                "live-read",
                "--fingerprint-key-version",
                "1",
            ],
            dependencies=dependencies,
        )
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(calls, [{"openai": True, "anthropic": False}])
        self.assertIn("snapshot_id", json.loads(stdout))

    def test_provider_failure_commits_only_content_free_terminal(self):
        self.bind()

        def fail(*_args, **_kwargs):
            from hormuz.finance_collection import FinanceCollectionError

            raise FinanceCollectionError("provider_rate_limited")

        dependencies = finance_commands.FinanceCommandDependencies(fetch_pages=fail)
        status, stdout, stderr = self.invoke(
            [
                "finance",
                "collect",
                "provider-account",
                "1",
                "openai.organization-usage-completions.v1",
                START,
                MIDDLE,
                "--page-size",
                "1",
                "--idempotency-key",
                "failed-live-read",
                "--fingerprint-key-version",
                "1",
            ],
            dependencies=dependencies,
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            json.loads(stderr), {"error": {"code": "provider_rate_limited"}}
        )
        with managed_sqlite_connection(self.config.database_path) as connection:
            event = connection.execute(
                "SELECT state,reason_code,receipt_id,snapshot_id,evidence_json "
                "FROM portfolio_finance_collection_events"
            ).fetchone()
            self.assertEqual(event[:4], ("failed", "provider_rate_limited", None, None))
            self.assertNotIn("provider-secret-value", event[4])
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM portfolio_finance_snapshots"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
