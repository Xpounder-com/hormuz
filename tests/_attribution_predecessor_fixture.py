"""Run the real pinned registry predecessor in its isolated interpreter.

The driver loads synthetic fixtures from the same digest-checked Git archive
that installed that interpreter, never from evolving current fixtures. This
is an intermediate source checkpoint, not a published release artifact. The
immutable released-v1 driver remains separate and unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit


SOURCE_COMMIT = "b8cec8faba8d8e48d515dfcc3ec8eeaa78fc7926"
ARCHIVE_SHA256 = "f8cb9c0493aa54e04e4706eddd111a90b54f2c70bf9f0e6af38911ba1d03995c"


def registry_predecessor_call(request):
    executable = os.environ["HORMUZ_TEST_REGISTRY_PYTHON"]
    result = subprocess.run([executable, "-I", str(Path(__file__).resolve())], input=json.dumps(request),
                            text=True, capture_output=True, timeout=60, cwd=Path(executable).parent)
    if result.returncode:
        raise AssertionError("registry_predecessor_driver_failed")
    return json.loads(result.stdout)


def _driver():
    from dataclasses import replace
    import hormuz
    from hormuz.config import UsageStorageConfig
    from hormuz.portfolio_repository import RegistryRepository
    from hormuz.portfolio_service import PortfolioService
    from hormuz.portfolio_wire import SCOPES
    from hormuz.postgres import POSTGRES_SCHEMA_VERSION, PostgresStorageError, migrate_postgres
    from hormuz.postgres_usage_store import PostgresUsageStore
    from hormuz.store import StorageSchemaError, UsageStore

    distribution = importlib.metadata.distribution("hormuz")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(source.get("url", ""))
    archive = Path(unquote(url.path))
    if (distribution.version != "1.0.0" or UsageStore.schema_version != 5 or POSTGRES_SCHEMA_VERSION != 9
            or not Path(hormuz.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            or source.get("archive_info", {}).get("hashes", {}).get("sha256") != ARCHIVE_SHA256
            or url.scheme != "file" or url.netloc not in {"", "localhost"}
            or archive.stat().st_size > 32 * 1024 * 1024
            or hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256):
        raise RuntimeError("registry_predecessor_binding_invalid")
    fixtures = {}
    with tarfile.open(archive) as source_tar:
        for name in ("_registry_transition_fixture", "_portfolio_fixture"):
            member = source_tar.getmember(f"hormuz-registry-baseline/tests/{name}.py")
            if not member.isfile() or member.size > 128 * 1024:
                raise RuntimeError("registry_predecessor_fixture_invalid")
            stream = source_tar.extractfile(member)
            namespace = {"__name__": "registry_predecessor_" + name, "__file__": str(archive) + "/" + name + ".py"}
            # These are the exact trusted, digest-bound predecessor fixtures.
            exec(compile(stream.read(), namespace["__file__"], "exec"), namespace)
            fixtures[name] = namespace
    ledger, registry = fixtures["_registry_transition_fixture"], fixtures["_portfolio_fixture"]
    request = json.load(sys.stdin)
    try:
        config = registry["registry_config"](Path(request.get("path", "/unused/predecessor/usage.sqlite3")).parent)
        if request["backend"] == "sqlite":
            config = replace(config, database_path=Path(request["path"]))
            store = UsageStore(config.database_path, read_only=request["mode"] != "seed")
            environment = None
        else:
            config = replace(config, usage_storage=UsageStorageConfig(backend="postgresql", postgres_schema=request["schema"], postgres_runtime_role=request["runtime_role"]))
            if request["mode"] == "seed":
                migrate_postgres(request["owner_dsn"], schema=request["schema"], runtime_role=request["runtime_role"],
                                 policy_control_role=request["policy_control_role"], custody_control_role=request["custody_control_role"], custody_executor_role=request["custody_executor_role"])
            store = PostgresUsageStore(request["runtime_dsn"], schema=request["schema"], runtime_role=request["runtime_role"], organization_ids=("acme", "beta"))
            environment = {"HORMUZ_POSTGRES_DSN": request["runtime_dsn"]}
        result = {"status": "ready"}
        if request["mode"] == "seed":
            ledger["seed_registry_ledger"](store)
            result["writes"], result["page"] = registry["seed_registry_metadata"](config, environ=environment)
        elif request["mode"] == "replay":
            service = PortfolioService(config, RegistryRepository(config, environ=environment))
            result["replays"] = [service.dispatch(registry["ADMIN"], "POST", path, body=json.dumps(body).encode(), idempotency_key=key)
                                 for path, body, key, _expected in request["writes"]]
            # Return after replays so the caller can assert no idempotency writes.
        elif request["mode"] == "page":
            service = PortfolioService(config, RegistryRepository(config, environ=environment))
            result["page"] = service.dispatch(registry["ADMIN"], "GET", SCOPES, query="cursor=" + request["cursor"])[1]
        result.update(ledger["ledger_observation"](store))
    except (StorageSchemaError, PostgresStorageError) as error:
        result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _driver()
