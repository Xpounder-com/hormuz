#!/usr/bin/env python3
"""Strict aggregate gate for the path-scoped CI workflow."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

if __package__:
    from .classify_ci_scope import FULL_CLASSIFICATIONS
else:
    from classify_ci_scope import FULL_CLASSIFICATIONS

ALWAYS_REQUIRED_JOB_IDS = (
    "test",
    "package",
    "render-https-preflight",
    "render-authentication-staging",
    "compose-reference",
    "client-compatibility",
)
PATH_SCOPED_JOB_IDS = (
    "postgres-compatibility",
    "postgres-backup-restore",
    "oci-reference-runtime",
    "kubernetes-reference",
    "postgres-ha-reference",
    "disaster-recovery-reference",
    "oci-supply-chain",
    "oci-reproducibility",
)
EXPECTED_JOB_IDS = frozenset(ALWAYS_REQUIRED_JOB_IDS + PATH_SCOPED_JOB_IDS)


class CIRequiredError(ValueError):
    """Raised when an applicable CI job did not reach its required result."""


def _parse_results(values: Sequence[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for value in values:
        job_id, separator, result = value.partition("=")
        if not separator or not job_id or not result or job_id in results:
            raise CIRequiredError("CI job results are malformed or duplicated")
        results[job_id] = result
    if frozenset(results) != EXPECTED_JOB_IDS:
        raise CIRequiredError("CI job result set changed")
    return results


def validate_required_results(
    *,
    scope_result: str,
    run_full: str,
    classification: str,
    result_values: Sequence[str],
) -> dict[str, object]:
    """Require success for every applicable job and only intentional skips."""

    if scope_result != "success":
        raise CIRequiredError("CI scope detection did not succeed")
    if run_full not in {"true", "false"}:
        raise CIRequiredError("CI scope output is missing or invalid")
    if run_full == "false":
        if classification != "website_only":
            raise CIRequiredError("reduced CI lacks the website-only classification")
    elif classification not in FULL_CLASSIFICATIONS:
        raise CIRequiredError("full CI classification is missing or invalid")

    results = _parse_results(result_values)
    for job_id in ALWAYS_REQUIRED_JOB_IDS:
        if results[job_id] != "success":
            raise CIRequiredError(f"always-applicable CI job did not succeed: {job_id}")

    expected_scoped_result = "success" if run_full == "true" else "skipped"
    for job_id in PATH_SCOPED_JOB_IDS:
        if results[job_id] != expected_scoped_result:
            raise CIRequiredError(
                "path-scoped CI job had an unexpected result: "
                f"{job_id}={results[job_id]}"
            )

    return {
        "classification": classification,
        "job_count": len(results),
        "run_full": run_full == "true",
        "status": "passed",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope-result", required=True)
    parser.add_argument("--run-full", required=True)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--result", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = validate_required_results(
            scope_result=args.scope_result,
            run_full=args.run_full,
            classification=args.classification,
            result_values=args.result,
        )
    except CIRequiredError as exc:
        print(f"CI required gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
