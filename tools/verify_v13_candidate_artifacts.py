#!/usr/bin/env python3
"""Bind a prospective v1.3 source/wheel pair to one exact Git commit.

This is a static packaging preflight for #214. It does not execute migrations,
verify a signed image, qualify Compose, or accept the final release candidate.
"""

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
from email.parser import Parser
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib
import zipfile


VERSION = "1.3.0"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
MAX_SELECTED_FILE_BYTES = 16 * 1024 * 1024
REQUIRED_SOURCE_KIT = frozenset({
    "MANIFEST.in",
    "pyproject.toml",
    "hormuz/__init__.py",
    "docs/V13_CANDIDATE_ARTIFACT_PREFLIGHT.md",
    "docs/REGISTRY_TRANSITION.md",
    "docs/ATTRIBUTION_TRANSITION.md",
    "docs/OUTCOME_TRANSITION.md",
    "docs/BUDGET_TRANSITION.md",
    "docs/FINANCE_TRANSITION.md",
    "docs/FINANCE_ACCOUNT_BINDING_TRANSITION.md",
    "docs/registry-transition-plan-v2.json",
    "docs/attribution-transition-plan-v2.json",
    "docs/outcome-transition-plan-v2.json",
    "docs/budget-transition-plan-v2.json",
    "docs/finance-transition-plan-v8.json",
    "tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json",
    "tests/test_v13_candidate_artifacts.py",
    "tools/verify_v13_candidate_artifacts.py",
})


class CandidateArtifactError(ValueError):
    """Content-free failure of the prospective candidate packaging boundary."""


def _selected(path: str) -> bool:
    if path in {"pyproject.toml", "MANIFEST.in", "setup.py"} or path.startswith("hormuz/"):
        return True
    if path in REQUIRED_SOURCE_KIT:
        return True
    if path.startswith("docs/"):
        name = path.rsplit("/", 1)[-1]
        return (
            name.endswith("_TRANSITION.md")
            or bool(re.fullmatch(r"[a-z0-9-]+-transition-plan-v[0-9]+\.json", name))
            or bool(re.fullmatch(r"[a-z0-9-]+-(?:contract|wire)-v[0-9]+\.json", name))
        )
    if path.startswith("tools/") or path.startswith("tests/"):
        name = path.rsplit("/", 1)[-1]
        return "transition" in name and name.endswith(".py")
    return False


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ("git", "-C", str(root), *args), check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise CandidateArtifactError("candidate_git_unavailable") from error


def _safe_path(name: str) -> bool:
    return (
        bool(name) and not name.startswith("/") and "\\" not in name
        and all(part not in ("", ".", "..") for part in name.split("/"))
    )


def _read_selected_tar(archive: tarfile.TarFile, prefix: str, selected: set[str]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    seen: set[str] = set()
    for member in archive.getmembers():
        name = member.name.rstrip("/")
        if name == prefix.rstrip("/") and member.isdir():
            continue
        if not _safe_path(name) or not name.startswith(prefix):
            raise CandidateArtifactError("candidate_source_path_invalid")
        relative = name[len(prefix):]
        if not relative or not _safe_path(relative):
            raise CandidateArtifactError("candidate_source_path_invalid")
        if member.isdir():
            continue
        if relative in seen:
            raise CandidateArtifactError("candidate_source_duplicate_file")
        seen.add(relative)
        if relative not in selected:
            if _selected(relative):
                raise CandidateArtifactError("candidate_source_extra_selected_file")
            continue
        if not member.isfile() or member.size > MAX_SELECTED_FILE_BYTES:
            raise CandidateArtifactError("candidate_source_selected_file_invalid")
        source = archive.extractfile(member)
        if source is None:
            raise CandidateArtifactError("candidate_source_selected_file_invalid")
        with source:
            result[relative] = source.read(MAX_SELECTED_FILE_BYTES + 1)
        if len(result[relative]) != member.size:
            raise CandidateArtifactError("candidate_source_selected_file_invalid")
    return result


def _git_files(root: Path, commit: str) -> dict[str, bytes]:
    if COMMIT.fullmatch(commit) is None:
        raise CandidateArtifactError("candidate_commit_invalid")
    if _git(root, "rev-parse", "HEAD").decode("ascii").strip() != commit:
        raise CandidateArtifactError("candidate_commit_not_head")
    names = _git(root, "ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8").split("\0")
    selected = sorted(path for path in names if path and _selected(path))
    if not REQUIRED_SOURCE_KIT <= set(selected) or "hormuz/__init__.py" not in selected:
        raise CandidateArtifactError("candidate_git_source_kit_incomplete")
    try:
        payload = _git(root, "archive", "--format=tar", commit, *selected)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            result = _read_selected_tar(archive, "", set(selected))
    except (tarfile.TarError, UnicodeError) as error:
        raise CandidateArtifactError("candidate_git_archive_invalid") from error
    if set(result) != set(selected):
        raise CandidateArtifactError("candidate_git_archive_incomplete")
    return result


def _package_version(pyproject: bytes, init: bytes) -> str:
    try:
        project = tomllib.loads(pyproject.decode("utf-8"))["project"]
        version = project["version"]
        name = project["name"]
        assignments = [
            node.value.value
            for node in ast.parse(init.decode("utf-8")).body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__version__"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ]
    except (KeyError, TypeError, ValueError, UnicodeError, SyntaxError) as error:
        raise CandidateArtifactError("candidate_version_unreadable") from error
    if name != "hormuz" or version != VERSION or assignments != [VERSION]:
        raise CandidateArtifactError("candidate_version_mismatch")
    return version


def _source_selected(path: Path, selected: set[str]) -> dict[str, bytes]:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            return _read_selected_tar(archive, f"hormuz-{VERSION}/", selected)
    except (OSError, tarfile.TarError) as error:
        raise CandidateArtifactError("candidate_source_invalid") from error


def _check_metadata(raw: bytes, *, label: str) -> None:
    try:
        metadata = Parser().parsestr(raw.decode("utf-8"))
    except UnicodeError as error:
        raise CandidateArtifactError(f"candidate_{label}_metadata_invalid") from error
    if metadata.get_all("Name") != ["hormuz"] or metadata.get_all("Version") != [VERSION]:
        raise CandidateArtifactError(f"candidate_{label}_metadata_version_mismatch")


def _check_wheel_record(archive: zipfile.ZipFile, names: set[str], dist_info: str) -> None:
    record = f"{dist_info}RECORD"
    if record not in names:
        raise CandidateArtifactError("candidate_wheel_record_missing")
    try:
        rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
    except (UnicodeError, csv.Error) as error:
        raise CandidateArtifactError("candidate_wheel_record_invalid") from error
    by_name: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in by_name:
            raise CandidateArtifactError("candidate_wheel_record_invalid")
        by_name[row[0]] = (row[1], row[2])
    if set(by_name) != names or by_name[record] != ("", ""):
        raise CandidateArtifactError("candidate_wheel_record_inventory_mismatch")
    for name in names - {record}:
        payload = archive.read(name)
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
        if by_name[name] != (f"sha256={digest}", str(len(payload))):
            raise CandidateArtifactError("candidate_wheel_record_digest_mismatch")


def _wheel_selected(path: Path, runtime: set[str], scripts: dict[str, str]) -> dict[str, bytes]:
    if path.name != f"hormuz-{VERSION}-py3-none-any.whl" or not path.is_file():
        raise CandidateArtifactError("candidate_wheel_missing_or_misnamed")
    dist_info = f"hormuz-{VERSION}.dist-info/"
    result: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            seen: set[str] = set()
            for member in archive.infolist():
                name = member.filename.rstrip("/")
                if not _safe_path(name):
                    raise CandidateArtifactError("candidate_wheel_path_invalid")
                if member.is_dir():
                    continue
                if name in seen:
                    raise CandidateArtifactError("candidate_wheel_duplicate_file")
                seen.add(name)
                if not (name.startswith("hormuz/") or name.startswith(dist_info)):
                    raise CandidateArtifactError("candidate_wheel_extra_top_level_file")
                if name.startswith("hormuz/"):
                    if name not in runtime:
                        raise CandidateArtifactError("candidate_wheel_extra_runtime_file")
                    if member.file_size > MAX_SELECTED_FILE_BYTES:
                        raise CandidateArtifactError("candidate_wheel_runtime_file_invalid")
                    result[name] = archive.read(member)
            if set(result) != runtime:
                raise CandidateArtifactError("candidate_wheel_runtime_inventory_mismatch")
            _check_metadata(archive.read(f"{dist_info}METADATA"), label="wheel")
            wheel_metadata = Parser().parsestr(archive.read(f"{dist_info}WHEEL").decode("utf-8"))
            if (wheel_metadata.get_all("Tag") != ["py3-none-any"]
                    or wheel_metadata.get_all("Root-Is-Purelib") != ["true"]):
                raise CandidateArtifactError("candidate_wheel_tag_mismatch")
            entries = configparser.ConfigParser(interpolation=None)
            entries.read_string(archive.read(f"{dist_info}entry_points.txt").decode("utf-8"))
            if set(entries.sections()) != {"console_scripts"} or dict(entries["console_scripts"]) != scripts:
                raise CandidateArtifactError("candidate_wheel_entry_points_mismatch")
            _check_wheel_record(archive, seen, dist_info)
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError, configparser.Error) as error:
        raise CandidateArtifactError("candidate_wheel_invalid") from error
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_candidate(root: Path, commit: str, source: Path, wheel: Path) -> dict[str, object]:
    git_files = _git_files(root, commit)
    _package_version(git_files["pyproject.toml"], git_files["hormuz/__init__.py"])
    if source.name != f"hormuz-{VERSION}.tar.gz" or not source.is_file():
        raise CandidateArtifactError("candidate_source_missing_or_misnamed")
    selected = set(git_files)
    source_files = _source_selected(source, selected | {"PKG-INFO"})
    try:
        _check_metadata(source_files.pop("PKG-INFO"), label="source")
    except KeyError as error:
        raise CandidateArtifactError("candidate_source_metadata_missing") from error
    if source_files != git_files:
        raise CandidateArtifactError("candidate_source_git_bytes_mismatch")
    runtime = {path for path in git_files if path.startswith("hormuz/")}
    try:
        scripts = tomllib.loads(git_files["pyproject.toml"].decode("utf-8"))["project"]["scripts"]
    except (ValueError, KeyError, TypeError, UnicodeError) as error:
        raise CandidateArtifactError("candidate_scripts_unreadable") from error
    if not isinstance(scripts, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in scripts.items()):
        raise CandidateArtifactError("candidate_scripts_invalid")
    wheel_files = _wheel_selected(wheel, runtime, scripts)
    if wheel_files != {path: git_files[path] for path in runtime}:
        raise CandidateArtifactError("candidate_wheel_git_bytes_mismatch")
    return {
        "schema_id": "hormuz.v13-candidate-artifact-identity",
        "schema_version": 1,
        "candidate_commit": commit,
        "candidate_version": VERSION,
        "source_sha256": _file_sha256(source),
        "wheel_sha256": _file_sha256(wheel),
        "runtime_files_verified": len(runtime),
        "source_kit_files_verified": len(selected) - len(runtime),
        "proof_scope": "git_runtime_and_transition_kit_source_wheel_byte_identity_only",
        "final_candidate_accepted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary = verify_candidate(args.repo_root, args.commit, args.source, args.wheel)
    except CandidateArtifactError as error:
        print(json.dumps({"status": "failed", "reason_code": str(error)}, sort_keys=True))
        return 1
    except OSError:
        print(json.dumps({"status": "failed", "reason_code": "candidate_io_error"}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
