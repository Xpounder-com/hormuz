#!/usr/bin/env python3
"""Classify a CI change set with a deliberately small, fail-closed allowlist."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SAFE_DIRECTORY_PREFIXES = ("website/",)
SAFE_EXACT_PATHS = frozenset(
    {
        "marketing/COMMERCIAL_SETUP.md",
        "marketing/MEASUREMENT.md",
    }
)
FULL_CLASSIFICATIONS = frozenset(
    {
        "full_changed_paths",
        "full_detection_failure",
        "full_empty_diff",
        "full_invalid_revision",
        "full_non_pull_request",
    }
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ScopeDecision:
    run_full: bool
    classification: str
    changed_path_count: int


def _is_safe_path(path: str) -> bool:
    """Return true only for an explicitly safe repository-relative path."""

    if not path or path.startswith("/") or "\x00" in path:
        return False
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if path in SAFE_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in SAFE_DIRECTORY_PREFIXES)


def classify_changed_paths(paths: Sequence[str]) -> ScopeDecision:
    """Classify already-detected paths; empty and unknown sets run everything."""

    if not paths:
        return ScopeDecision(True, "full_empty_diff", 0)
    if all(_is_safe_path(path) for path in paths):
        return ScopeDecision(False, "website_only", len(paths))
    return ScopeDecision(True, "full_changed_paths", len(paths))


def _changed_paths(root: Path, base_sha: str, head_sha: str) -> tuple[str, ...] | None:
    """Return NUL-delimited diff paths, or None when Git cannot prove the diff."""

    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                base_sha,
                head_sha,
                "--",
            ],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    if not completed.stdout:
        return ()
    if not completed.stdout.endswith(b"\x00"):
        return None
    return tuple(os.fsdecode(value) for value in completed.stdout[:-1].split(b"\x00"))


def determine_scope(
    *, event_name: str, base_sha: str, head_sha: str, repository_root: Path
) -> ScopeDecision:
    """Run the full suite unless a pull-request diff is proven website-only."""

    if event_name != "pull_request":
        return ScopeDecision(True, "full_non_pull_request", 0)
    if (
        not _SHA_PATTERN.fullmatch(base_sha)
        or not _SHA_PATTERN.fullmatch(head_sha)
        or base_sha == "0" * 40
        or head_sha == "0" * 40
    ):
        return ScopeDecision(True, "full_invalid_revision", 0)
    paths = _changed_paths(repository_root, base_sha, head_sha)
    if paths is None:
        return ScopeDecision(True, "full_detection_failure", 0)
    return classify_changed_paths(paths)


def _append_github_outputs(path: Path, decision: ScopeDecision) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"classification={decision.classification}\n")
        output.write(f"changed_path_count={decision.changed_path_count}\n")
        # Write the only skip-enabling output last. If classification fails
        # before this point, the workflow-level fallback remains full CI.
        output.write(f"run_full={'true' if decision.run_full else 'false'}\n")


def _append_step_summary(path: Path, decision: ScopeDecision) -> None:
    with path.open("a", encoding="utf-8") as summary:
        summary.write("## CI scope\n\n")
        summary.write(f"- Classification: `{decision.classification}`\n")
        summary.write(
            "- Infrastructure suite: "
            f"`{'full' if decision.run_full else 'website-only reduced'}`\n"
        )
        summary.write(f"- Changed paths detected: `{decision.changed_path_count}`\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--step-summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    decision = determine_scope(
        event_name=args.event_name,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        repository_root=args.repository_root.resolve(),
    )
    if args.step_summary is not None:
        _append_step_summary(args.step_summary, decision)
    print(
        json.dumps(
            {
                "changed_path_count": decision.changed_path_count,
                "classification": decision.classification,
                "run_full": decision.run_full,
            },
            sort_keys=True,
        )
    )
    _append_github_outputs(args.github_output, decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
