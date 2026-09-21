#!/usr/bin/env python3
"""Run the bounded #214 source/wheel transition matrix against published predecessors.

This is a development-tree checkpoint. It does not qualify a v1.3.0 release
candidate, an unimplemented migration, OCI, Compose, or live provider data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import unquote, urlsplit
import zipfile


V1_ARCHIVE_SHA256 = "2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a"
V1_MANIFEST_SHA256 = "85774aa45a8b30be88d1cb1a7b543222cc1396523aec31c17de07470b09d56b2"
V12_SOURCE_SHA256 = "257c99b99af891838a1a0f1ba77bcc9c6baa74fba586d99381adab9f7b1af909"
V12_WHEEL_SHA256 = "5519df553d4a9c330e6cf1fa822d6f8efc956e8eb5a7ac178eee01a89ffaa2bb"

SQLITE_CASES = (
    "test_sqlite_registry_transition.SQLiteRegistryTransitionTests.test_released_sqlite_binary_preserves_old_state_and_refuses_newer_or_partial_state",
    "test_sqlite_registry_transition.SQLiteRegistryTransitionTests.test_sqlite_quiesced_verified_pair_restore_keeps_unknown_holds",
    "test_sqlite_registry_transition.SQLiteRegistryTransitionTests.test_sqlite_candidate_writes_remain_present_for_forward_recovery",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_published_predecessor_populates_finance_and_replays_original_receipts",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_real_missing_successor_preserves_published_predecessor",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_test_only_ddl_failure_rolls_back_and_retry_is_idempotent",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_published_and_current_binaries_refuse_partial_and_newer_without_repair",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts",
    "test_finance_account_binding_transition_preflight.SQLitePublishedAccountBindingPreflightTests.test_post_checkpoint_witness_write_requires_retained_forward_recovery",
)
POSTGRES_CASES = (
    "test_postgres_registry_transition.PostgresRegistryTransitionTests.test_released_postgres_binary_preserves_old_state_and_refuses_newer_or_partial_state",
    "test_postgres_registry_transition.PostgresRegistryTransitionTests.test_postgres_quiesced_verified_pair_restore_keeps_unknown_holds",
    "test_postgres_registry_transition.PostgresRegistryTransitionTests.test_postgres_candidate_writes_remain_present_for_forward_recovery",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_published_predecessor_populates_finance_and_replays_original_receipts",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_real_missing_successor_preserves_published_predecessor",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_test_only_ddl_failure_rolls_back_and_retry_is_idempotent",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_published_and_current_binaries_refuse_partial_and_newer_without_repair",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_quiesced_old_pair_restore_is_separate_and_preserves_original_receipts",
    "test_finance_account_binding_transition_preflight.PostgresPublishedAccountBindingPreflightTests.test_post_checkpoint_witness_write_requires_retained_forward_recovery",
)


class MatrixRefusal(RuntimeError):
    """A required matrix input or case is missing or unbound."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_published_artifacts(paths: dict[str, Path]) -> None:
    expected = {
        "v1_archive": V1_ARCHIVE_SHA256,
        "v1_manifest": V1_MANIFEST_SHA256,
        "v12_source": V12_SOURCE_SHA256,
        "v12_wheel": V12_WHEEL_SHA256,
    }
    for name, digest in expected.items():
        try:
            actual = sha256(paths[name])
        except (KeyError, OSError) as error:
            raise MatrixRefusal(f"{name}_missing") from error
        if actual != digest:
            raise MatrixRefusal(f"{name}_digest_mismatch")


def wheel_runtime_files(wheel: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            entries = [item for item in archive.infolist()
                       if item.filename.startswith("hormuz/") and not item.is_dir()]
            names = [item.filename for item in entries]
            if not names or len(names) != len(set(names)):
                raise MatrixRefusal("candidate_wheel_runtime_invalid")
            if any(".." in Path(name).parts for name in names):
                raise MatrixRefusal("candidate_wheel_runtime_invalid")
            if any((item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries):
                raise MatrixRefusal("candidate_wheel_runtime_invalid")
            return {item.filename: archive.read(item) for item in entries}
    except MatrixRefusal:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise MatrixRefusal("candidate_wheel_invalid") from error


def verify_candidate_runtime_pair(source_root: Path, wheel: Path) -> dict[str, bytes]:
    root = source_root / "hormuz"
    if not root.is_dir():
        raise MatrixRefusal("candidate_source_missing")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise MatrixRefusal("candidate_source_symlink_invalid")
    source = {
        path.relative_to(source_root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    archived = wheel_runtime_files(wheel)
    if source != archived:
        raise MatrixRefusal("candidate_source_wheel_runtime_mismatch")
    return archived


def verify_candidate_import(mode: str, source_root: Path, wheel: Path,
                            runtime_files: dict[str, bytes]) -> None:
    import hormuz

    package_root = Path(hormuz.__file__).resolve().parent
    if mode == "source":
        if package_root != (source_root / "hormuz").resolve():
            raise MatrixRefusal("candidate_source_import_mismatch")
        return
    if not package_root.is_relative_to(Path(sys.prefix).resolve()) or package_root == (source_root / "hormuz").resolve():
        raise MatrixRefusal("candidate_wheel_import_mismatch")
    distribution = importlib.metadata.distribution("hormuz")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(direct.get("url", ""))
    digest = sha256(wheel)
    if (url.scheme != "file" or url.netloc not in {"", "localhost"}
            or Path(unquote(url.path)).resolve() != wheel.resolve()
            or direct.get("archive_info", {}).get("hashes", {}).get("sha256") != digest):
        raise MatrixRefusal("candidate_wheel_install_binding_invalid")
    installed = {
        "hormuz/" + path.relative_to(package_root).as_posix(): path.read_bytes()
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if installed != runtime_files:
        raise MatrixRefusal("candidate_wheel_install_runtime_mismatch")


def selected_cases(postgres: bool) -> tuple[str, ...]:
    return SQLITE_CASES + (POSTGRES_CASES if postgres else ())


def run_cases(source_root: Path, *, postgres: bool) -> dict[str, object]:
    sys.path.insert(0, str(source_root / "tests"))
    selected = selected_cases(postgres)
    suite = unittest.defaultTestLoader.loadTestsFromNames(selected)
    # Errors may contain local DSNs or fixture contents; emit only case IDs.
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    failed = [case.id() for case, _ in (*result.failures, *result.errors)]
    skipped = [case.id() for case, _ in result.skipped]
    if result.testsRun != len(selected) or failed or skipped or not result.wasSuccessful():
        raise MatrixRefusal(json.dumps({
            "matrix_cases_failed": failed,
            "matrix_cases_skipped": skipped,
            "expected": len(selected),
            "ran": result.testsRun,
        }, sort_keys=True))
    return {"sqlite_cases": len(SQLITE_CASES),
            "postgres_cases": len(POSTGRES_CASES) if postgres else 0,
            "skipped_cases": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("source", "wheel"))
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--candidate-wheel", required=True, type=Path)
    parser.add_argument("--v1-archive", required=True, type=Path)
    parser.add_argument("--v1-manifest", required=True, type=Path)
    parser.add_argument("--v1-python", required=True, type=Path)
    parser.add_argument("--v12-source", required=True, type=Path)
    parser.add_argument("--v12-wheel", required=True, type=Path)
    parser.add_argument("--v12-python", required=True, type=Path)
    parser.add_argument("--postgres", action="store_true",
                        help="require a disposable DSN and matching backup container")
    args = parser.parse_args(argv)
    try:
        source_root = args.source_root.resolve(strict=True)
        for name in ("v1_python", "v12_python"):
            executable = getattr(args, name)
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise MatrixRefusal(f"{name}_missing")
        if args.postgres and not (os.environ.get("HORMUZ_TEST_POSTGRES_DSN")
                                  and os.environ.get("HORMUZ_TEST_PG_CONTAINER")):
            raise MatrixRefusal("disposable_postgres_pair_missing")
        verify_published_artifacts({
            "v1_archive": args.v1_archive,
            "v1_manifest": args.v1_manifest,
            "v12_source": args.v12_source,
            "v12_wheel": args.v12_wheel,
        })
        runtime = verify_candidate_runtime_pair(source_root, args.candidate_wheel)
        verify_candidate_import(args.mode, source_root, args.candidate_wheel, runtime)
        # A venv's ``bin/python`` is normally a symlink. Resolving it jumps to
        # the base interpreter and silently discards the pinned installation.
        os.environ["HORMUZ_TEST_V1_PYTHON"] = str(args.v1_python.absolute())
        os.environ["HORMUZ_TEST_ACCOUNT_BINDING_PYTHON"] = str(args.v12_python.absolute())
        os.environ["HORMUZ_TEST_ACCOUNT_BINDING_SOURCE"] = str(args.v12_source.resolve())
        result = run_cases(source_root, postgres=args.postgres)
        commit = subprocess.run(["git", "-C", str(source_root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        print(json.dumps({"status": "passed", "checkpoint": "development-only",
                          "source_head": commit, "source_tree_clean": not bool(dirty),
                          "candidate_wheel_sha256": sha256(args.candidate_wheel),
                          "v1_archive_sha256": V1_ARCHIVE_SHA256,
                          "v12_source_sha256": V12_SOURCE_SHA256,
                          "v12_wheel_sha256": V12_WHEEL_SHA256,
                          "mode": args.mode, **result}, sort_keys=True))
        return 0
    except MatrixRefusal as error:
        # MatrixRefusal carries only fixed codes or selected test IDs.
        print(json.dumps({"status": "refused", "reason": str(error)}, sort_keys=True))
        return 1
    except Exception:
        # Other exceptions may contain local paths, DSNs or row contents.
        print(json.dumps({"status": "refused", "reason": "matrix_execution_error"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
