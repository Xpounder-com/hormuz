"""Use the actual accepted finance-history binary and digest-bound fixtures.

The archive is an intermediate Git checkpoint, not a published release. No
fixture source is taken from the evolving budget checkout inside the driver.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import ModuleType
from urllib.parse import unquote, urlsplit


SOURCE_COMMIT = "1dd6c9f561ee70880d6e68b7aa2d2ab17852d207"
ARCHIVE_SHA256 = "31622ab69ee74daa4be92b4f1a8d57808304d1c4916600a17f7afe499a721910"


def verify_installed_runtime(source_tar, package_root):
    """Do not trust install metadata if source/data files drift afterwards."""
    prefix = "hormuz-finance-baseline/hormuz/"
    try:
        expected = {}
        for member in source_tar.getmembers():
            if not member.name.startswith(prefix):
                continue
            relative = Path(member.name[len(prefix):])
            if relative.suffix not in {".py", ".json", ".sql"}:
                continue
            if (not member.isfile() or member.size > 2 * 1024 * 1024
                    or relative.is_absolute() or ".." in relative.parts or relative in expected):
                raise RuntimeError("budget_predecessor_runtime_mismatch")
            expected[relative] = member
        actual = {path.relative_to(package_root) for path in package_root.rglob("*")
                  if path.is_file() and path.suffix in {".py", ".json", ".sql", ".so", ".pyd"}}
        if not expected or actual != set(expected):
            raise RuntimeError("budget_predecessor_runtime_mismatch")
        for relative, member in expected.items():
            path = package_root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(package_root.resolve()):
                raise RuntimeError("budget_predecessor_runtime_mismatch")
            with path.open("rb") as installed:
                payload = installed.read(member.size + 1)
            if payload != source_tar.extractfile(member).read(member.size + 1):
                raise RuntimeError("budget_predecessor_runtime_mismatch")
        return len(expected)
    except (OSError, ValueError):
        raise RuntimeError("budget_predecessor_runtime_mismatch") from None


def finance_predecessor_call(request):
    executable = os.environ["HORMUZ_TEST_FINANCE_PYTHON"]
    result = subprocess.run(
        [executable, "-I", str(Path(__file__).resolve())], input=json.dumps(request),
        text=True, capture_output=True, timeout=60, cwd=Path(executable).parent,
    )
    if result.returncode:
        raise AssertionError("budget_predecessor_driver_failed")
    return json.loads(result.stdout)


def _driver():
    from dataclasses import replace
    import hormuz
    from hormuz.config import UsageStorageConfig
    from hormuz.finance_rate_cards import rate_card_from_mapping
    from hormuz.finance_repository import create_finance_repository
    from hormuz.portfolio_repository import create_portfolio_repository
    from hormuz.portfolio_service import PortfolioService
    from hormuz.portfolio_wire import ATTRIBUTIONS, OUTCOMES, SCOPES, canonical
    from hormuz.postgres import POSTGRES_SCHEMA_VERSION, PostgresStorageError, migrate_postgres
    from hormuz.postgres_usage_store import PostgresUsageStore
    from hormuz.store import StorageSchemaError, UsageStore

    distribution = importlib.metadata.distribution("hormuz")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(source.get("url", ""))
    archive = Path(unquote(url.path))
    if (distribution.version != "1.0.0" or UsageStore.schema_version != 8 or POSTGRES_SCHEMA_VERSION != 12
            or not Path(hormuz.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            or source.get("archive_info", {}).get("hashes", {}).get("sha256") != ARCHIVE_SHA256
            or url.scheme != "file" or url.netloc not in {"", "localhost"}):
        raise RuntimeError("budget_predecessor_binding_invalid")
    with archive.open("rb") as source:
        archive_bytes = source.read(32 * 1024 * 1024 + 1)
    if len(archive_bytes) > 32 * 1024 * 1024 or hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("budget_predecessor_binding_invalid")
    # Verify and execute from the same bytes, without reopening a mutable path.
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as source_tar:
        runtime_files_verified = verify_installed_runtime(source_tar, Path(hormuz.__file__).resolve().parent)
        for name in ("_registry_transition_fixture", "_portfolio_fixture", "_attribution_fixture",
                     "test_outcome_contract", "_outcome_fixture", "_finance_values_fixture", "_finance_fixture"):
            member = source_tar.getmember(f"hormuz-finance-baseline/tests/{name}.py")
            if not member.isfile() or member.size > 128 * 1024:
                raise RuntimeError("budget_predecessor_fixture_invalid")
            stream = source_tar.extractfile(member)
            module = ModuleType(name)
            module.__file__ = str(archive) + "/" + name + ".py"
            module.__package__ = ""
            sys.modules[name] = module
            # Only exact trusted archive bytes can execute in this isolated driver.
            exec(compile(stream.read(), module.__file__, "exec"), module.__dict__)
    ledger = sys.modules["_registry_transition_fixture"]
    registry = sys.modules["_portfolio_fixture"]
    attribution = sys.modules["_attribution_fixture"]
    outcome = sys.modules["_outcome_fixture"]
    finance = sys.modules["_finance_fixture"]

    def finance_receipt(value):
        return {
            "card": value.card.as_mapping(), "receipt_id": value.receipt_id,
            "registered_by": value.registered_by, "registered_at": value.registered_at,
            "sequence": value.sequence,
        }
    request = json.load(sys.stdin)
    try:
        config = registry.registry_config(Path(request.get("path", "/unused/predecessor/usage.sqlite3")).parent)
        if request["backend"] == "sqlite":
            config = replace(config, database_path=Path(request["path"]))
            store = UsageStore(config.database_path, read_only=request["mode"] != "seed")
            environment = None
        elif request["backend"] == "postgresql":
            config = replace(config, usage_storage=UsageStorageConfig(
                backend="postgresql", postgres_schema=request["schema"], postgres_runtime_role=request["runtime_role"],
            ))
            if request["mode"] == "seed":
                migrate_postgres(
                    request["owner_dsn"], schema=request["schema"], runtime_role=request["runtime_role"],
                    policy_control_role=request["policy_control_role"], custody_control_role=request["custody_control_role"],
                    custody_executor_role=request["custody_executor_role"],
                )
            store = PostgresUsageStore(request["runtime_dsn"], schema=request["schema"], runtime_role=request["runtime_role"], organization_ids=("acme", "beta"))
            environment = {"HORMUZ_POSTGRES_DSN": request["runtime_dsn"]}
        else:
            raise RuntimeError("budget_predecessor_backend_invalid")
        result = {"status": "ready", "runtime_files_verified": runtime_files_verified}
        if request["mode"] == "seed":
            ledger.seed_registry_ledger(store)
            result["registry_writes"], result["registry_page"] = registry.seed_registry_metadata(config, environ=environment)
            _, result["attribution_write"], result["attribution_page"], result["attempt_id"] = attribution.seed_attribution_metadata(config, environ=environment)
            result["outcome_seed"] = outcome.seed_outcome_metadata(config, environ=environment)
            result["outcome_page"] = result["outcome_seed"]["page"]
            result["finance_registration"] = finance_receipt(finance.seed_finance(config, environ=environment))
        elif request["mode"] in {"replay", "page", "facts"}:
            repositories = create_portfolio_repository(config, environ=environment)
            service = PortfolioService(config, repositories)
            if request["mode"] == "replay":
                result["registry_replays"] = [service.dispatch(registry.ADMIN, "POST", path, body=canonical(body).encode(), idempotency_key=key)
                                                for path, body, key, _expected in request["registry_writes"]]
                body, key, _expected = request["attribution_write"]
                result["attribution_replay"] = service.dispatch(registry.ADMIN, "POST", ATTRIBUTIONS, body=canonical(body).encode(), idempotency_key=key)
                result["outcome_replays"], result["outcome_retention"] = outcome.replay_outcome_metadata(config, request["outcome_seed"], environ=environment)
                repository = create_finance_repository(config, environ=environment)
                result["finance_replay"] = finance_receipt(repository.register_rate_card(
                    finance.ADMIN, rate_card_from_mapping(request["finance_registration"]["card"]),
                ))
            elif request["mode"] == "page":
                path = {"registry": SCOPES, "attribution": ATTRIBUTIONS, "outcome": OUTCOMES}[request["resource"]]
                result["page"] = service.dispatch(registry.ADMIN, "GET", path, query="cursor=" + request["cursor"])[1]
            else:
                result["facts"] = repositories.attributions.attempt_facts(service.authenticate(registry.ADMIN), request["attempt_id"])
        elif request["mode"] != "verify":
            raise RuntimeError("budget_predecessor_mode_invalid")
        result.update(ledger.ledger_observation(store))
    except (StorageSchemaError, PostgresStorageError) as error:
        result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _driver()
