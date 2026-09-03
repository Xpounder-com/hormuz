"""Verify and execute the exact merged native-finance predecessor runtime."""

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
from urllib.parse import unquote, urlsplit


SOURCE_COMMIT = "545598c052013bb406741e2a003971f8047082f5"
ARCHIVE_SHA256 = "7f4657439f91fa68bcd43860dfe6bb6f3c8b923dcd3c5ed9f1cd6acdcbe284c7"
ARCHIVE_PREFIX = "hormuz-finance-native-runtime-baseline/"
RUNTIME_FILE_COUNT = 145
RUNTIME_TREE_SHA256 = "6208bd386f6bd91aa395fe5d795e3112406b696d8675f169fb8f7dc288920123"


def verify_installed_runtime(source_tar, package_root):
    """Reject installed runtime/data bytes that differ from the archive."""

    prefix = ARCHIVE_PREFIX + "hormuz/"
    try:
        # Compare every package file, including non-code runtime assets.
        expected = {}
        for member in source_tar.getmembers():
            if not member.name.startswith(prefix):
                continue
            relative = Path(member.name[len(prefix):])
            if member.isdir():
                continue
            if (
                not member.isfile()
                or member.size > 2 * 1024 * 1024
                or relative.is_absolute()
                or ".." in relative.parts
                or relative in expected
            ):
                raise RuntimeError(
                    "finance_collection_predecessor_runtime_mismatch"
                )
            expected[relative] = member
        actual = {
            path.relative_to(package_root)
            for path in package_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
        }
        if len(expected) != RUNTIME_FILE_COUNT or actual != set(expected):
            raise RuntimeError("finance_collection_predecessor_runtime_mismatch")
        for relative, member in expected.items():
            path = package_root / relative
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(package_root.resolve())
            ):
                raise RuntimeError(
                    "finance_collection_predecessor_runtime_mismatch"
                )
            with path.open("rb") as installed:
                payload = installed.read(member.size + 1)
            archived = source_tar.extractfile(member)
            if archived is None or payload != archived.read(member.size + 1):
                raise RuntimeError(
                    "finance_collection_predecessor_runtime_mismatch"
                )
        return len(expected)
    except (OSError, ValueError):
        raise RuntimeError(
            "finance_collection_predecessor_runtime_mismatch"
        ) from None


def finance_collection_predecessor_call(request):
    executable = os.environ["HORMUZ_TEST_FINANCE_COLLECTION_PYTHON"]
    result = subprocess.run(
        [executable, "-I", str(Path(__file__).resolve())],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=60,
        cwd=Path(executable).parent,
    )
    if result.returncode:
        raise AssertionError("finance_collection_predecessor_driver_failed")
    return json.loads(result.stdout)


def _driver():
    import hormuz
    from hormuz.postgres import POSTGRES_SCHEMA_VERSION, PostgresStorageError
    from hormuz.postgres_usage_store import PostgresUsageStore
    from hormuz.store import StorageSchemaError, UsageStore

    distribution = importlib.metadata.distribution("hormuz")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(direct.get("url", ""))
    archive = Path(unquote(url.path))
    if (
        distribution.version != "1.0.0"
        or UsageStore.schema_version != 11
        or POSTGRES_SCHEMA_VERSION != 15
        or not Path(hormuz.__file__).resolve().is_relative_to(
            Path(sys.prefix).resolve()
        )
        or direct.get("archive_info", {}).get("hashes", {}).get("sha256")
        != ARCHIVE_SHA256
        or url.scheme != "file"
        or url.netloc not in {"", "localhost"}
    ):
        raise RuntimeError("finance_collection_predecessor_binding_invalid")
    with archive.open("rb") as source_file:
        archive_bytes = source_file.read(32 * 1024 * 1024 + 1)
    if (
        not archive_bytes
        or len(archive_bytes) > 32 * 1024 * 1024
        or hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256
    ):
        raise RuntimeError("finance_collection_predecessor_binding_invalid")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as source_tar:
        runtime_files_verified = verify_installed_runtime(
            source_tar,
            Path(hormuz.__file__).resolve().parent,
        )

    request = json.load(sys.stdin)
    result = {
        "status": "ready",
        "runtime_files_verified": runtime_files_verified,
    }
    try:
        if request["backend"] == "sqlite":
            UsageStore(Path(request["path"]), read_only=True).verify_ready()
        elif request["backend"] == "postgresql":
            PostgresUsageStore(
                request["runtime_dsn"],
                schema=request["schema"],
                runtime_role=request["runtime_role"],
                organization_ids=("acme", "beta"),
            ).verify_ready()
        else:
            raise RuntimeError(
                "finance_collection_predecessor_backend_invalid"
            )
    except (StorageSchemaError, PostgresStorageError) as error:
        result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _driver()
