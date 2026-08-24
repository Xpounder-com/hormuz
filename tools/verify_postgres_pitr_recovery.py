#!/usr/bin/env python3
"""Write strict evidence for Hormuz's disposable PostgreSQL PITR drill.

The shell runner performs the deliberately isolated database exercise.  This
tool accepts only its fixed, content-free result shape and writes the sole
retained evidence artifact after every positive and negative check has passed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

try:
    from tools._verification_runtime import (
        is_pinned_image_reference,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        is_pinned_image_reference,
        write_private_json_evidence,
    )


SUMMARY_SCHEMA_ID = "hormuz.postgresql-pitr-recovery"
SUMMARY_SCHEMA_VERSION = 1
SUMMARY_COVERAGE = "ephemeral_postgresql_wal_pitr_only"
_POSTGRES_VERSION_PATTERN = re.compile(r"\d+\.\d+(?:\.\d+)?\Z")
_RECOVERY_CONTAINER_PATTERN = re.compile(
    r"hormuz-postgres-pitr-recovery-[0-9]+\Z"
)
_RECOVERY_DATABASE = "hormuz_pitr"
_DEFAULT_PROMOTION_ATTEMPTS = 45
_DEFAULT_PROMOTION_INTERVAL_MS = 1000
_PROMOTION_PROBE_TIMEOUT_SECONDS = 5
_CHECK_KEYS = (
    "base_backup_created",
    "pre_target_wal_replayed",
    "post_target_mutation_excluded",
    "hormuz_restricted_state_verified",
    "missing_wal_not_promoted",
    "unreachable_target_not_promoted",
)
_DURATION_KEYS = (
    "seed",
    "base_backup",
    "wal_archive",
    "restore",
    "verify",
    "total",
)


class PITRRecoveryError(RuntimeError):
    """A stable, content-free failure from the disposable PITR drill."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summary", help="validate and write the retained PITR evidence")
    summary.add_argument("--database-image", required=True)
    summary.add_argument("--database-version", required=True)
    for key in _CHECK_KEYS:
        summary.add_argument(f"--{key.replace('_', '-')}", action="store_true")
    for key in _DURATION_KEYS:
        summary.add_argument(f"--{key.replace('_', '-')}-ms", type=int, required=True)
    summary.add_argument("--output", required=True, type=Path)
    promotion_wait = commands.add_parser(
        "promotion-wait",
        help="wait for the positive disposable recovery target to promote",
    )
    promotion_wait.add_argument("--container", required=True)
    promotion_wait.add_argument(
        "--attempts", type=int, default=_DEFAULT_PROMOTION_ATTEMPTS
    )
    promotion_wait.add_argument(
        "--interval-ms", type=int, default=_DEFAULT_PROMOTION_INTERVAL_MS
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "summary":
            checks = {key: getattr(args, key) for key in _CHECK_KEYS}
            durations = {key: getattr(args, f"{key}_ms") for key in _DURATION_KEYS}
            evidence = build_summary(
                database_image=args.database_image,
                database_version=args.database_version,
                checks=checks,
                durations_ms=durations,
            )
            write_summary(args.output, evidence)
            print("wrote content-free PostgreSQL PITR recovery summary")
        elif args.command == "promotion-wait":
            wait_for_promotion(
                lambda: _postgres_is_promoted(args.container),
                attempts=args.attempts,
                interval_ms=args.interval_ms,
            )
            print("disposable PostgreSQL recovery target promoted")
        else:  # pragma: no cover - argparse owns this boundary
            raise AssertionError(f"unsupported PITR command: {args.command}")
        return 0
    except PITRRecoveryError as error:
        print(f"PostgreSQL PITR recovery failed: {error}", file=sys.stderr)
        return 1


def build_summary(
    *,
    database_image: object,
    database_version: object,
    checks: Mapping[str, object],
    durations_ms: Mapping[str, object],
) -> dict[str, object]:
    """Return the strict schema written only after the complete drill succeeds."""

    _validate_database_identity(database_image=database_image, database_version=database_version)
    _validate_checks(checks)
    _validate_durations(durations_ms)
    summary: dict[str, object] = {
        "schema_id": SUMMARY_SCHEMA_ID,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "coverage": SUMMARY_COVERAGE,
        "database": {"image": database_image, "version": database_version},
        "recovery_target": "named_restore_point",
        "checks": dict(checks),
        "durations_ms": dict(durations_ms),
    }
    validate_summary(summary)
    return summary


def validate_summary(value: Mapping[str, object]) -> None:
    """Reject incomplete, unpinned, or non-content-free evidence shapes."""

    if set(value) != {
        "schema_id",
        "schema_version",
        "coverage",
        "database",
        "recovery_target",
        "checks",
        "durations_ms",
    }:
        raise PITRRecoveryError("summary_schema_invalid")
    if value.get("schema_id") != SUMMARY_SCHEMA_ID or value.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        raise PITRRecoveryError("summary_schema_invalid")
    if value.get("coverage") != SUMMARY_COVERAGE or value.get("recovery_target") != "named_restore_point":
        raise PITRRecoveryError("summary_schema_invalid")
    database = _mapping(value.get("database"), "summary_schema_invalid")
    if set(database) != {"image", "version"}:
        raise PITRRecoveryError("summary_schema_invalid")
    _validate_database_identity(database_image=database.get("image"), database_version=database.get("version"))
    _validate_checks(_mapping(value.get("checks"), "summary_schema_invalid"))
    _validate_durations(_mapping(value.get("durations_ms"), "summary_schema_invalid"))


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    """Atomically publish one owner-readable evidence file without partial output."""

    validate_summary(summary)
    if path.exists() or path.is_symlink():
        raise PITRRecoveryError("summary_output_exists")
    try:
        write_private_json_evidence(
            path,
            summary,
            temporary_prefix=".hormuz-postgresql-pitr-",
        )
    except OSError as error:
        raise PITRRecoveryError("summary_write_failed") from error


def wait_for_promotion(
    probe: Callable[[], bool],
    *,
    attempts: int = _DEFAULT_PROMOTION_ATTEMPTS,
    interval_ms: int = _DEFAULT_PROMOTION_INTERVAL_MS,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Wait a bounded time for the positive recovery target to leave recovery."""

    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or not 1 <= attempts <= 300
        or not isinstance(interval_ms, int)
        or isinstance(interval_ms, bool)
        or not 0 <= interval_ms <= 10_000
    ):
        raise PITRRecoveryError("promotion_wait_configuration_invalid")
    for attempt in range(attempts):
        if probe() is True:
            return
        if attempt + 1 < attempts:
            sleeper(interval_ms / 1000)
    raise PITRRecoveryError("recovery_target_promotion_timeout")


def _postgres_is_promoted(container: object) -> bool:
    """Return only whether the fixed disposable target reports recovery=false."""

    if (
        not isinstance(container, str)
        or _RECOVERY_CONTAINER_PATTERN.fullmatch(container) is None
    ):
        raise PITRRecoveryError("promotion_target_invalid")
    try:
        completed = subprocess.run(
            (
                "docker",
                "exec",
                container,
                "psql",
                "--username=postgres",
                f"--dbname={_RECOVERY_DATABASE}",
                "--set=ON_ERROR_STOP=on",
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT pg_is_in_recovery()",
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_PROMOTION_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "f"


def _validate_database_identity(*, database_image: object, database_version: object) -> None:
    if not is_pinned_image_reference(database_image, image_name="postgres"):
        raise PITRRecoveryError("database_image_not_pinned")
    if not isinstance(database_version, str) or _POSTGRES_VERSION_PATTERN.fullmatch(database_version) is None:
        raise PITRRecoveryError("database_version_invalid")


def _validate_checks(checks: Mapping[str, object]) -> None:
    if set(checks) != set(_CHECK_KEYS) or any(checks[key] is not True for key in checks):
        raise PITRRecoveryError("summary_checks_invalid")


def _validate_durations(durations_ms: Mapping[str, object]) -> None:
    if set(durations_ms) != set(_DURATION_KEYS):
        raise PITRRecoveryError("summary_durations_invalid")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in durations_ms.values()
    ):
        raise PITRRecoveryError("summary_durations_invalid")


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PITRRecoveryError(code)
    return value


if __name__ == "__main__":  # pragma: no cover - exercised by the shell/CI gate
    raise SystemExit(main())
