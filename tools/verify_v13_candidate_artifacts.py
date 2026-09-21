#!/usr/bin/env python3
"""Bind a prospective v1.3 source/wheel pair to one exact Git commit.

This is a static packaging preflight for #214. It does not execute migrations,
verify a signed image, qualify Compose, or accept the final release candidate.
"""

from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import configparser
import csv
from email.parser import Parser
from fnmatch import fnmatchcase
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
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SOURCE_MEMBERS = 4096
MAX_WHEEL_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_WHEEL_TOTAL_BYTES = 64 * 1024 * 1024
MAX_WHEEL_MEMBERS = 4096
GENERATED_SOURCE_FILES = frozenset({
    "PKG-INFO",
    "setup.cfg",
    "hormuz.egg-info/PKG-INFO",
    "hormuz.egg-info/SOURCES.txt",
    "hormuz.egg-info/dependency_links.txt",
    "hormuz.egg-info/entry_points.txt",
    "hormuz.egg-info/requires.txt",
    "hormuz.egg-info/top_level.txt",
})
GENERATED_SETUP_CFG = b"[egg_info]\ntag_build = \ntag_date = 0\n\n"
REQUIRED_SOURCE_KIT = frozenset({
    "MANIFEST.in",
    "pyproject.toml",
    "hormuz/__init__.py",
    "README.md",
    "LICENSE",
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
    "tools/verify_finance_account_binding_preflight.py",
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


def _utf8(raw: bytes, reason: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeError as error:
        raise CandidateArtifactError(reason) from error


def _read_tar(archive: tarfile.TarFile, prefix: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    seen: set[str] = set()
    implied_directories: set[str] = set()
    total_bytes = 0
    for ordinal, member in enumerate(archive, 1):
        if ordinal > MAX_SOURCE_MEMBERS:
            raise CandidateArtifactError("candidate_source_member_bounds")
        if member.size < 0 or member.size > MAX_SOURCE_FILE_BYTES:
            raise CandidateArtifactError("candidate_source_file_bounds")
        if member.isfile():
            total_bytes += member.size
            if total_bytes > MAX_SOURCE_TOTAL_BYTES:
                raise CandidateArtifactError("candidate_source_total_bounds")
        raw_name = member.name
        name = raw_name.rstrip("/")
        if name == prefix.rstrip("/") and member.isdir() and raw_name in {name, name + "/"}:
            continue
        if raw_name not in {name, name + "/"} or not _safe_path(name) or not name.startswith(prefix):
            raise CandidateArtifactError("candidate_source_path_invalid")
        relative = name[len(prefix):]
        if not relative or not _safe_path(relative):
            raise CandidateArtifactError("candidate_source_path_invalid")
        if relative in seen:
            raise CandidateArtifactError("candidate_source_duplicate_member")
        parents = {"/".join(relative.split("/")[:n]) for n in range(1, len(relative.split("/")))}
        if parents & result.keys() or (member.isfile() and relative in implied_directories):
            raise CandidateArtifactError("candidate_source_file_directory_collision")
        if not (member.isdir() or member.isfile()):
            raise CandidateArtifactError("candidate_source_member_type_invalid")
        if member.isfile() and raw_name != name:
            raise CandidateArtifactError("candidate_source_path_invalid")
        seen.add(relative)
        implied_directories.update(parents)
        if member.isdir():
            continue
        source = archive.extractfile(member)
        if source is None:
            raise CandidateArtifactError("candidate_source_file_invalid")
        with source:
            result[relative] = source.read(MAX_SOURCE_FILE_BYTES + 1)
        if len(result[relative]) != member.size:
            raise CandidateArtifactError("candidate_source_file_invalid")
    return result


def _git_archive_files(root: Path, commit: str, paths: set[str]) -> dict[str, bytes]:
    try:
        payload = _git(root, "archive", "--format=tar", commit, *sorted(paths))
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            result = _read_tar(archive, "")
    except (tarfile.TarError, UnicodeError) as error:
        raise CandidateArtifactError("candidate_git_archive_invalid") from error
    if set(result) != paths:
        raise CandidateArtifactError("candidate_git_archive_incomplete")
    return result


def _manifest_expected(raw: bytes, names: set[str]) -> set[str]:
    expected: set[str] = set()
    pruned: set[str] = set()
    for line in _utf8(raw, "candidate_manifest_invalid").splitlines():
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        command, *values = parts
        if command == "include" and values:
            for path in values:
                if not _safe_path(path) or path not in names:
                    raise CandidateArtifactError("candidate_manifest_declared_file_missing")
                expected.add(path)
        elif command == "recursive-include" and len(values) >= 2:
            directory, *patterns = values
            if not _safe_path(directory) or any("/" in pattern or "\\" in pattern for pattern in patterns):
                raise CandidateArtifactError("candidate_manifest_invalid")
            expected.update(
                path for path in names
                if path.startswith(directory + "/")
                and any(fnmatchcase(path.rsplit("/", 1)[-1], pattern) for pattern in patterns)
            )
        elif command == "prune" and values:
            for directory in values:
                if not _safe_path(directory):
                    raise CandidateArtifactError("candidate_manifest_invalid")
                pruned.add(directory)
        else:
            # A new setuptools manifest directive needs a reviewed verifier
            # update; silently ignoring one can omit committed source files.
            raise CandidateArtifactError("candidate_manifest_unsupported_directive")
    return {path for path in expected if not any(
        path == directory or path.startswith(directory + "/") for directory in pruned
    )}


def _git_files(root: Path, commit: str) -> tuple[dict[str, bytes], set[str], set[str]]:
    if COMMIT.fullmatch(commit) is None:
        raise CandidateArtifactError("candidate_commit_invalid")
    if _git(root, "rev-parse", "HEAD").decode("ascii").strip() != commit:
        raise CandidateArtifactError("candidate_commit_not_head")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise CandidateArtifactError("candidate_checkout_dirty")
    names = set(_git(root, "ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8").split("\0")) - {""}
    if "MANIFEST.in" not in names:
        raise CandidateArtifactError("candidate_git_source_kit_incomplete")
    manifest = _git_archive_files(root, commit, {"MANIFEST.in"})["MANIFEST.in"]
    selected = {path for path in names if _selected(path)} | _manifest_expected(manifest, names)
    if not REQUIRED_SOURCE_KIT <= selected:
        raise CandidateArtifactError("candidate_git_source_kit_incomplete")
    return _git_archive_files(root, commit, selected), names, selected


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


def _source_files(payload: bytes) -> dict[str, bytes]:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            return _read_tar(archive, f"hormuz-{VERSION}/")
    except (OSError, tarfile.TarError) as error:
        raise CandidateArtifactError("candidate_source_invalid") from error


def _check_metadata(raw: bytes, *, label: str):
    try:
        metadata = Parser().parsestr(raw.decode("utf-8"))
    except UnicodeError as error:
        raise CandidateArtifactError(f"candidate_{label}_metadata_invalid") from error
    if metadata.get_all("Name") != ["hormuz"] or metadata.get_all("Version") != [VERSION]:
        raise CandidateArtifactError(f"candidate_{label}_metadata_version_mismatch")
    return metadata


def _requirement(value: str) -> tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]:
    compact = value.replace(" ", "")
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_.-]*)(?:\[([A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*)\])?(.*)", compact)
    if match is None:
        raise CandidateArtifactError("candidate_requirement_invalid")
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    extras = tuple(sorted(re.sub(r"[-_.]+", "-", extra).lower() for extra in (match.group(2) or "").split(",") if extra))
    raw_specs = match.group(3)
    specifications: list[tuple[str, str]] = []
    for raw_spec in raw_specs.split(",") if raw_specs else ():
        spec = re.fullmatch(r"(===|==|~=|!=|<=|>=|<|>)([A-Za-z0-9.*+!_-]+)", raw_spec)
        if spec is None:
            raise CandidateArtifactError("candidate_requirement_invalid")
        specifications.append((spec.group(1), spec.group(2)))
    if len(specifications) != len(set(specifications)) or len(extras) != len(set(extras)):
        raise CandidateArtifactError("candidate_requirement_invalid")
    return name, extras, tuple(sorted(specifications))


def _expected_requirements(project: dict[str, object]) -> Counter[tuple[object, ...]]:
    dependencies = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    if not isinstance(dependencies, list) or not isinstance(optional, dict):
        raise CandidateArtifactError("candidate_project_dependencies_invalid")
    expected: Counter[tuple[object, ...]] = Counter()
    for extra, values in [(None, dependencies), *optional.items()]:
        if extra is not None and (not isinstance(extra, str) or re.fullmatch(r"[a-z0-9-]+", extra) is None):
            raise CandidateArtifactError("candidate_project_dependencies_invalid")
        if not isinstance(values, list) or not all(isinstance(value, str) and ";" not in value for value in values):
            raise CandidateArtifactError("candidate_project_dependencies_invalid")
        for value in values:
            expected[(*_requirement(value), extra)] += 1
    return expected


def _metadata_requirements(metadata) -> Counter[tuple[object, ...]]:
    actual: Counter[tuple[object, ...]] = Counter()
    for raw in metadata.get_all("Requires-Dist", []):
        parts = raw.split(";")
        if len(parts) > 2:
            raise CandidateArtifactError("candidate_metadata_dependencies_mismatch")
        extra = None
        if len(parts) == 2:
            marker = re.fullmatch(r'\s*extra\s*==\s*"([a-z0-9-]+)"\s*', parts[1])
            if marker is None:
                raise CandidateArtifactError("candidate_metadata_dependencies_mismatch")
            extra = marker.group(1)
        actual[(*_requirement(parts[0].strip()), extra)] += 1
    return actual


def _check_package_metadata(raw: bytes, project: dict[str, object], readme: bytes) -> None:
    metadata = _check_metadata(raw, label="source")
    try:
        description = readme.decode("utf-8")
    except UnicodeError as error:
        raise CandidateArtifactError("candidate_readme_invalid") from error
    optional = project.get("optional-dependencies", {})
    authors = project.get("authors", [])
    classifiers = project.get("classifiers", [])
    if (not isinstance(authors, list) or len(authors) > 1
            or any(not isinstance(author, dict) or set(author) != {"name"}
                   or not isinstance(author["name"], str) or not author["name"]
                   for author in authors)
            or not isinstance(classifiers, list)
            or not all(isinstance(item, str) for item in classifiers)):
        raise CandidateArtifactError("candidate_project_metadata_unsupported")
    expected_author = [authors[0]["name"]] if authors else []
    allowed_headers = {
        "Metadata-Version", "Name", "Version", "Summary", "Author",
        "License-Expression", "Classifier", "Requires-Python",
        "Description-Content-Type", "License-File", "Requires-Dist",
        "Provides-Extra", "Dynamic",
    }
    if (
        set(metadata.keys()) - allowed_headers
        or metadata.get_all("Metadata-Version") != ["2.4"]
        or metadata.get_all("Summary") != [project.get("description")]
        or metadata.get_all("License-Expression") != [project.get("license")]
        or project.get("readme") != "README.md"
        or project.get("license-files") != ["LICENSE"]
        or metadata.get_all("Author", []) != expected_author
        or metadata.get_all("Classifier", []) != classifiers
        or metadata.get_all("Requires-Python") != [project.get("requires-python")]
        or metadata.get_all("Description-Content-Type") != ["text/markdown"]
        or metadata.get_all("License-File") != ["LICENSE"]
        or metadata.get_all("Dynamic", []) not in ([], ["license-file"])
        or metadata.get_payload() != description
        or not isinstance(optional, dict)
        or sorted(metadata.get_all("Provides-Extra", [])) != sorted(optional)
        or _metadata_requirements(metadata) != _expected_requirements(project)
    ):
        raise CandidateArtifactError("candidate_source_metadata_semantics_mismatch")


def _check_entry_points(raw: bytes, scripts: dict[str, str]) -> None:
    try:
        entries = configparser.ConfigParser(interpolation=None)
        entries.read_string(raw.decode("utf-8"))
    except (UnicodeError, configparser.Error) as error:
        raise CandidateArtifactError("candidate_entry_points_invalid") from error
    if set(entries.sections()) != {"console_scripts"} or dict(entries["console_scripts"]) != scripts:
        raise CandidateArtifactError("candidate_entry_points_mismatch")


def _check_egg_info(source_files: dict[str, bytes], project: dict[str, object], scripts: dict[str, str]) -> None:
    if not GENERATED_SOURCE_FILES <= source_files.keys():
        raise CandidateArtifactError("candidate_source_generated_files_missing")
    if source_files["setup.cfg"] != GENERATED_SETUP_CFG:
        raise CandidateArtifactError("candidate_source_setup_cfg_mismatch")
    if source_files["hormuz.egg-info/PKG-INFO"] != source_files["PKG-INFO"]:
        raise CandidateArtifactError("candidate_source_egg_metadata_mismatch")
    if source_files["hormuz.egg-info/dependency_links.txt"] != b"\n" or source_files["hormuz.egg-info/top_level.txt"] != b"hormuz\n":
        raise CandidateArtifactError("candidate_source_egg_identity_mismatch")
    _check_entry_points(source_files["hormuz.egg-info/entry_points.txt"], scripts)
    sources = _utf8(source_files["hormuz.egg-info/SOURCES.txt"], "candidate_source_manifest_invalid").splitlines()
    if len(sources) != len(set(sources)) or set(sources) != set(source_files) - {"PKG-INFO", "setup.cfg"}:
        raise CandidateArtifactError("candidate_source_manifest_mismatch")
    actual: Counter[tuple[object, ...]] = Counter()
    extra = None
    for line in _utf8(source_files["hormuz.egg-info/requires.txt"], "candidate_source_egg_requirements_invalid").splitlines():
        if not line:
            continue
        if line.startswith("["):
            if re.fullmatch(r"\[([a-z0-9-]+)\]", line) is None:
                raise CandidateArtifactError("candidate_source_egg_requirements_invalid")
            extra = line[1:-1]
        else:
            actual[(*_requirement(line), extra)] += 1
    if actual != _expected_requirements(project):
        raise CandidateArtifactError("candidate_source_egg_requirements_mismatch")


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


def _wheel_selected(
    payload: bytes, runtime: set[str], scripts: dict[str, str],
    source_metadata: bytes, license_bytes: bytes, source_entry_points: bytes,
) -> dict[str, bytes]:
    dist_info = f"hormuz-{VERSION}.dist-info/"
    result: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_WHEEL_MEMBERS:
                raise CandidateArtifactError("candidate_wheel_member_bounds")
            seen: set[str] = set()
            files: set[str] = set()
            directories: set[str] = set()
            implied_directories: set[str] = set()
            total_bytes = 0
            for member in members:
                raw_name = member.filename
                name = raw_name.rstrip("/")
                if (raw_name not in {name, name + "/"} or not _safe_path(name)
                        or name in seen):
                    raise CandidateArtifactError("candidate_wheel_path_invalid")
                if member.flag_bits & 1:
                    raise CandidateArtifactError("candidate_wheel_encrypted_entry")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise CandidateArtifactError("candidate_wheel_member_type_invalid")
                if not (name == "hormuz" or name.startswith("hormuz/")
                        or name == dist_info.rstrip("/") or name.startswith(dist_info)):
                    raise CandidateArtifactError("candidate_wheel_extra_top_level_file")
                if not member.is_dir() and name in {"hormuz", dist_info.rstrip("/")}:
                    raise CandidateArtifactError("candidate_wheel_file_directory_collision")
                parents = {"/".join(name.split("/")[:n]) for n in range(1, len(name.split("/")))}
                if parents & files or (not member.is_dir() and name in implied_directories | directories):
                    raise CandidateArtifactError("candidate_wheel_file_directory_collision")
                seen.add(name)
                implied_directories.update(parents)
                if member.is_dir():
                    directories.add(name)
                    continue
                if raw_name != name or member.file_size > MAX_SOURCE_FILE_BYTES:
                    raise CandidateArtifactError("candidate_wheel_file_bounds")
                total_bytes += member.file_size
                if total_bytes > MAX_WHEEL_TOTAL_BYTES:
                    raise CandidateArtifactError("candidate_wheel_total_bounds")
                files.add(name)
                if name.startswith("hormuz/"):
                    if name not in runtime:
                        raise CandidateArtifactError("candidate_wheel_extra_runtime_file")
                    result[name] = archive.read(member)
            if set(result) != runtime:
                raise CandidateArtifactError("candidate_wheel_runtime_inventory_mismatch")
            wheel_metadata = archive.read(f"{dist_info}METADATA")
            _check_metadata(wheel_metadata, label="wheel")
            if wheel_metadata != source_metadata:
                raise CandidateArtifactError("candidate_wheel_source_metadata_mismatch")
            if archive.read(f"{dist_info}licenses/LICENSE") != license_bytes:
                raise CandidateArtifactError("candidate_wheel_license_mismatch")
            if archive.read(f"{dist_info}top_level.txt") != b"hormuz\n":
                raise CandidateArtifactError("candidate_wheel_top_level_mismatch")
            wheel_metadata = Parser().parsestr(archive.read(f"{dist_info}WHEEL").decode("utf-8"))
            if (wheel_metadata.get_all("Wheel-Version") != ["1.0"]
                    or wheel_metadata.get_all("Tag") != ["py3-none-any"]
                    or wheel_metadata.get_all("Root-Is-Purelib") != ["true"]):
                raise CandidateArtifactError("candidate_wheel_tag_mismatch")
            wheel_entry_points = archive.read(f"{dist_info}entry_points.txt")
            _check_entry_points(wheel_entry_points, scripts)
            if wheel_entry_points != source_entry_points:
                raise CandidateArtifactError("candidate_wheel_entry_points_mismatch")
            _check_wheel_record(archive, files, dist_info)
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError, configparser.Error, RuntimeError) as error:
        raise CandidateArtifactError("candidate_wheel_invalid") from error
    return result


def _bounded_artifact(path: Path, maximum: int, reason: str) -> bytes:
    try:
        with path.open("rb") as source:
            payload = source.read(maximum + 1)
    except OSError as error:
        raise CandidateArtifactError(reason) from error
    if len(payload) > maximum:
        raise CandidateArtifactError(reason)
    return payload


def verify_candidate(root: Path, commit: str, source: Path, wheel: Path) -> dict[str, object]:
    git_files, git_names, expected_source = _git_files(root, commit)
    _package_version(git_files["pyproject.toml"], git_files["hormuz/__init__.py"])
    if source.name != f"hormuz-{VERSION}.tar.gz" or not source.is_file():
        raise CandidateArtifactError("candidate_source_missing_or_misnamed")
    source_payload = _bounded_artifact(source, MAX_SOURCE_ARCHIVE_BYTES, "candidate_source_archive_bounds")
    source_files = _source_files(source_payload)
    if not expected_source <= source_files.keys():
        raise CandidateArtifactError("candidate_source_git_bytes_mismatch")
    unexpected = set(source_files) - git_names - GENERATED_SOURCE_FILES
    if unexpected:
        raise CandidateArtifactError("candidate_source_untracked_file")
    tracked_source = set(source_files) & git_names
    if tracked_source != expected_source:
        raise CandidateArtifactError("candidate_source_inventory_mismatch")
    committed_source = _git_archive_files(root, commit, tracked_source)
    if any(source_files[path] != committed_source[path] for path in tracked_source):
        raise CandidateArtifactError("candidate_source_git_bytes_mismatch")
    try:
        project = tomllib.loads(git_files["pyproject.toml"].decode("utf-8"))["project"]
        scripts = project["scripts"]
    except (ValueError, KeyError, TypeError, UnicodeError) as error:
        raise CandidateArtifactError("candidate_project_unreadable") from error
    if not isinstance(project, dict) or not isinstance(scripts, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in scripts.items()
    ):
        raise CandidateArtifactError("candidate_scripts_invalid")
    try:
        source_metadata = source_files["PKG-INFO"]
    except KeyError as error:
        raise CandidateArtifactError("candidate_source_metadata_missing") from error
    _check_package_metadata(source_metadata, project, git_files["README.md"])
    _check_egg_info(source_files, project, scripts)
    runtime = {path for path in git_files if path.startswith("hormuz/")}
    if wheel.name != f"hormuz-{VERSION}-py3-none-any.whl" or not wheel.is_file():
        raise CandidateArtifactError("candidate_wheel_missing_or_misnamed")
    wheel_payload = _bounded_artifact(wheel, MAX_WHEEL_ARCHIVE_BYTES, "candidate_wheel_archive_bounds")
    wheel_files = _wheel_selected(
        wheel_payload, runtime, scripts, source_metadata, git_files["LICENSE"],
        source_files["hormuz.egg-info/entry_points.txt"],
    )
    if wheel_files != {path: git_files[path] for path in runtime}:
        raise CandidateArtifactError("candidate_wheel_git_bytes_mismatch")
    return {
        "schema_id": "hormuz.v13-candidate-artifact-identity",
        "schema_version": 1,
        "candidate_commit": commit,
        "candidate_version": VERSION,
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        "wheel_sha256": hashlib.sha256(wheel_payload).hexdigest(),
        "runtime_files_verified": len(runtime),
        "source_kit_files_verified": len(expected_source) - len(runtime),
        "source_files_verified": len(tracked_source),
        "proof_scope": "git_source_archive_and_wheel_runtime_identity_only",
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
    except UnicodeError:
        print(json.dumps({"status": "failed", "reason_code": "candidate_encoding_invalid"}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
