"""Execute the published v1.2.0 wheel with fixtures from its verified source kit.

This driver never loads fixture or runtime bytes from the candidate checkout.
Only synthetic data is used; subprocess diagnostics never expose DSNs or rows.
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
import tempfile
from urllib.parse import unquote, urlsplit

SOURCE_COMMIT = "d854a5a453fcbe20cb3f4c1e261e146f2da93855"
SOURCE_SHA256 = "257c99b99af891838a1a0f1ba77bcc9c6baa74fba586d99381adab9f7b1af909"
WHEEL_SHA256 = "5519df553d4a9c330e6cf1fa822d6f8efc956e8eb5a7ac178eee01a89ffaa2bb"
ARCHIVE_PREFIX = "hormuz-1.2.0/"
RUNTIME_FILE_COUNT = 161


def verified_source(path):
    with Path(path).open("rb") as stream:
        payload = stream.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("account_binding_predecessor_source_mismatch")
    return payload


def verify_installed_runtime(source_tar, package_root):
    prefix = ARCHIVE_PREFIX + "hormuz/"
    expected = {}
    for member in source_tar.getmembers():
        if not member.name.startswith(prefix) or member.isdir():
            continue
        relative = Path(member.name[len(prefix):])
        if (not member.isfile() or member.size > 2 * 1024 * 1024
                or relative.is_absolute() or ".." in relative.parts or relative in expected):
            raise RuntimeError("account_binding_predecessor_runtime_mismatch")
        expected[relative] = member
    actual = {p.relative_to(package_root) for p in package_root.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    if len(expected) != RUNTIME_FILE_COUNT or actual != set(expected):
        raise RuntimeError("account_binding_predecessor_runtime_mismatch")
    for relative, member in expected.items():
        path = package_root / relative
        archived = source_tar.extractfile(member)
        if (path.is_symlink() or not path.resolve().is_relative_to(package_root.resolve())
                or archived is None):
            raise RuntimeError("account_binding_predecessor_runtime_mismatch")
        with path.open("rb") as installed:
            if installed.read(member.size + 1) != archived.read(member.size + 1):
                raise RuntimeError("account_binding_predecessor_runtime_mismatch")
    return len(expected)


def predecessor_call(request):
    executable = os.environ["HORMUZ_TEST_ACCOUNT_BINDING_PYTHON"]
    result = subprocess.run(
        [executable, "-I", str(Path(__file__).resolve())],
        input=json.dumps(request), text=True, capture_output=True,
        timeout=60, cwd=Path(executable).parent,
    )
    if result.returncode:
        raise AssertionError("account_binding_predecessor_driver_failed")
    return json.loads(result.stdout)


def _extract_verified_fixtures(source_tar, destination):
    # Extract only regular fixture files, from the same already hash-verified
    # buffer used to check the installed runtime. No archive can choose a path.
    prefix = ARCHIVE_PREFIX + "tests/"
    for member in source_tar.getmembers():
        if not member.name.startswith(prefix) or member.isdir():
            continue
        relative = Path(member.name[len(prefix):])
        if (not member.isfile() or relative.is_absolute() or ".." in relative.parts
                or member.size > 2 * 1024 * 1024):
            raise RuntimeError("account_binding_predecessor_fixture_invalid")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        stream = source_tar.extractfile(member)
        if stream is None:
            raise RuntimeError("account_binding_predecessor_fixture_invalid")
        target.write_bytes(stream.read())


def _exercise(request, runtime_files_verified):
    from dataclasses import replace
    from unittest import mock
    from hormuz.config import UsageStorageConfig
    from hormuz.finance_collection import normalize_collection_pages
    from hormuz.finance_collection_repository import create_finance_collection_repository
    from hormuz.postgres import migrate_postgres
    from hormuz.postgres_usage_store import PostgresUsageStore
    from hormuz.store import UsageStore
    import _finance_collection_transition_fixture as predecessor
    import _portfolio_fixture as registry
    import test_finance_collection_runtime as collection

    mode = request.get("mode", "ready")
    config = registry.registry_config(Path(request.get("path", "/unused/predecessor/usage.sqlite3")).parent)
    environment = None
    if request["backend"] == "sqlite":
        config = replace(config, database_path=Path(request["path"]))
        store = UsageStore(config.database_path, read_only=mode != "seed")
    elif request["backend"] == "postgresql":
        if mode == "seed":
            migrate_postgres(request["owner_dsn"], **{k: request[k] for k in (
                "schema", "runtime_role", "policy_control_role", "custody_control_role", "custody_executor_role"
            )})
        config = replace(config, usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=request["schema"], postgres_runtime_role=request["runtime_role"],
        ))
        environment = {"HORMUZ_POSTGRES_DSN": request["runtime_dsn"]}
        store = PostgresUsageStore(request["runtime_dsn"], schema=request["schema"],
                                   runtime_role=request["runtime_role"], organization_ids=("acme", "beta"))
    else:
        raise RuntimeError("account_binding_predecessor_backend_invalid")
    store.verify_ready()
    result = {"status": "ready", "runtime_files_verified": runtime_files_verified}
    if mode == "ready":
        return result
    if mode not in {"seed", "replay"}:
        raise RuntimeError("account_binding_predecessor_mode_invalid")
    # Guard against a future helper accidentally changing offline fixture work
    # into provider collection. Database connections remain local test I/O.
    with mock.patch("hormuz.finance_collection.fetch_collection_pages",
                    side_effect=AssertionError("provider_io_forbidden")):
        if mode == "seed":
            predecessor._seed(config, store, environment)
        repository = create_finance_collection_repository(config, environ=environment)
        if mode == "seed":
            body = collection.FinanceCollectionSQLiteRepositoryTests.binding_request(None)
            repository.bind_source(collection.ADMIN, body, fingerprint_key=collection.KEY)
        for kind in ("usage", "costs"):
            profile = "openai.organization-usage-completions.v1" if kind == "usage" else "openai.organization-costs.v1"
            query = collection.query(profile, end=collection.END)
            prepared = repository.prepare_collection(collection.ADMIN, query,
                idempotency_key="published-v120-" + kind, evidence_origin="customer_file")
            if mode == "seed":
                if kind == "usage":
                    normalized = collection.normalized_usage(query, records_by_day=((collection.openai_usage(),), ()))
                else:
                    page = collection.openai_page([
                        collection.openai_bucket(collection.START, collection.MIDDLE, [collection.openai_cost()]),
                        collection.openai_bucket(collection.MIDDLE, collection.END, []),
                    ])
                    normalized = normalize_collection_pages(query, (page,), fingerprint_key=collection.KEY, fingerprint_key_version=1)
                receipt = repository.publish_collection(collection.ADMIN, prepared, normalized)
            else:
                if prepared.state != "succeeded":
                    raise RuntimeError("account_binding_predecessor_replay_changed")
                receipt = repository.receipt_for_prepared(collection.ADMIN, prepared)
            result[kind + "_receipt_id"] = receipt.receipt_id
        store.verify_audit_chain(organization_id="acme")
    return result


def _driver():
    import hormuz
    from hormuz.postgres import POSTGRES_SCHEMA_VERSION, PostgresStorageError
    from hormuz.store import StorageSchemaError, UsageStore
    distribution = importlib.metadata.distribution("hormuz")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(direct.get("url", ""))
    if (distribution.version != "1.2.0" or UsageStore.schema_version != 12
            or POSTGRES_SCHEMA_VERSION != 17 or url.scheme != "file" or url.netloc not in {"", "localhost"}
            or direct.get("archive_info", {}).get("hashes", {}).get("sha256") != WHEEL_SHA256
            or not Path(hormuz.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            or hashlib.sha256(Path(unquote(url.path)).read_bytes()).hexdigest() != WHEEL_SHA256):
        raise RuntimeError("account_binding_predecessor_install_binding_invalid")
    source = verified_source(os.environ["HORMUZ_TEST_ACCOUNT_BINDING_SOURCE"])
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive, tempfile.TemporaryDirectory() as temporary:
        count = verify_installed_runtime(archive, Path(hormuz.__file__).resolve().parent)
        fixture_root = Path(temporary) / "tests"
        _extract_verified_fixtures(archive, fixture_root)
        sys.path.insert(0, str(fixture_root))
        try:
            result = _exercise(json.load(sys.stdin), count)
        except (StorageSchemaError, PostgresStorageError) as error:
            result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _driver()
