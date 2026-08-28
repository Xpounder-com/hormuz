#!/usr/bin/env python3
"""Run the exact Hormuz v1 offline workflow five times against one candidate.

The runner executes the archived source in five fresh virtual environments and
five fresh SQLite workspaces. It emits only the strict content-free evidence
accepted by ``verify_v1_internal_repeatability_evidence.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from tools import v1_candidate
    from tools import verify_v1_internal_repeatability_evidence as repeatability
except ImportError:
    import v1_candidate  # type: ignore[no-redef]
    import verify_v1_internal_repeatability_evidence as repeatability  # type: ignore[no-redef]


MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_FILE_BYTES = 32 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 120
_SETUP_IDENTITY_TOKEN = "v1-sandbox-setup-only"
_EXPECTED_RUNTIME_REQUIREMENTS = frozenset(
    {
        "PyJWT[crypto]>=2.13,<3",
        "cryptography>=42",
    }
)
_FINAL_RUNTIME_VERSION_RE = re.compile(
    r"(?P<release>[0-9]+(?:\.[0-9]+)*)(?:\.post[0-9]+)?"
    r"(?:\+[0-9A-Za-z]+(?:[._-][0-9A-Za-z]+)*)?\Z"
)

_DEPENDENCY_PATH_PROBE = r"""
import importlib.metadata
import importlib.util
import json
import pathlib

paths = []
versions = {}
for distribution_name, module_name in (("PyJWT", "jwt"), ("cryptography", "cryptography")):
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise SystemExit(2)
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit(2)
    origin = pathlib.Path(spec.origin).resolve()
    package_root = pathlib.Path(distribution.locate_file("")).resolve()
    try:
        origin.relative_to(package_root)
    except ValueError:
        raise SystemExit(2)
    if not package_root.is_dir():
        raise SystemExit(2)
    paths.append(str(package_root))
    versions[distribution_name] = distribution.version
print(json.dumps({"paths": sorted(set(paths)), "versions": versions}, sort_keys=True))
"""

_SOURCE_PROBE = r"""
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).resolve()
dependency_paths = json.loads(sys.argv[2])
sys.path[:0] = [str(source), *dependency_paths]
import hormuz

module_path = pathlib.Path(hormuz.__file__).resolve()
try:
    module_path.relative_to(source)
except ValueError:
    loaded_from_archive = False
else:
    loaded_from_archive = True
print(json.dumps({"version": hormuz.__version__, "loaded_from_archive": loaded_from_archive}))
"""

_OFFLINE_CLI_WRAPPER = r"""
import json
import runpy
import socket
import sys

source = sys.argv[1]
dependency_paths = json.loads(sys.argv[2])
arguments = sys.argv[3:]
sys.path[:0] = [source, *dependency_paths]

class OfflineSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise OSError("network_disabled")

    def connect_ex(self, *args, **kwargs):
        raise OSError("network_disabled")

def network_disabled(*args, **kwargs):
    raise OSError("network_disabled")

socket.socket = OfflineSocket
socket.create_connection = network_disabled
socket.getaddrinfo = network_disabled
sys.argv = ["hormuz", *arguments]
runpy.run_module("hormuz.cli", run_name="__main__")
"""


class V1InternalRepeatabilityRunError(RuntimeError):
    """A content-safe runner failure."""


class _StageFailure(RuntimeError):
    def __init__(self, exit_code: int):
        super().__init__("stage_failed")
        self.exit_code = exit_code


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ceiling_second_timestamp(moment: datetime | None = None) -> str:
    """Return a custody-compatible timestamp that cannot predate ``moment``."""

    value = moment or datetime.now(UTC)
    value = value.astimezone(UTC)
    if value.microsecond:
        value = value.replace(microsecond=0) + timedelta(seconds=1)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _absolute_without_resolving(path: Path) -> Path:
    """Make an executable path absolute without dereferencing a venv symlink."""

    return Path(os.path.abspath(path))


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_environment(temporary: Path, *, setup: bool = False) -> dict[str, str]:
    environment = {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": str(temporary),
    }
    if setup:
        environment["HORMUZ_TOKEN"] = _SETUP_IDENTITY_TOKEN
    return environment


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: int = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise V1InternalRepeatabilityRunError("subprocess_unavailable") from error


def _archived_runtime_requirements(source_root: Path) -> None:
    try:
        value = tomllib.loads(
            (source_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies = value["project"]["dependencies"]
    except (KeyError, OSError, TypeError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise V1InternalRepeatabilityRunError(
            "candidate_runtime_requirements_invalid"
        ) from error
    if (
        not isinstance(dependencies, list)
        or len(dependencies) != len(_EXPECTED_RUNTIME_REQUIREMENTS)
        or any(not isinstance(item, str) for item in dependencies)
        or set(dependencies) != _EXPECTED_RUNTIME_REQUIREMENTS
    ):
        raise V1InternalRepeatabilityRunError(
            "candidate_runtime_requirements_invalid"
        )


def _final_release(value: object) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise V1InternalRepeatabilityRunError("dependency_version_probe_invalid")
    match = _FINAL_RUNTIME_VERSION_RE.fullmatch(value)
    if match is None:
        raise V1InternalRepeatabilityRunError(
            "preprovisioned_dependency_versions_unsupported"
        )
    return tuple(int(part) for part in match.group("release").split("."))


def _validate_dependency_probe(value: object, source_root: Path) -> list[str]:
    _archived_runtime_requirements(source_root)
    if not isinstance(value, dict) or set(value) != {"paths", "versions"}:
        raise V1InternalRepeatabilityRunError("dependency_path_probe_invalid")
    paths = value["paths"]
    versions = value["versions"]
    if (
        not isinstance(paths, list)
        or not paths
        or any(not isinstance(path, str) or not Path(path).is_dir() for path in paths)
        or not isinstance(versions, dict)
        or set(versions) != {"PyJWT", "cryptography"}
    ):
        raise V1InternalRepeatabilityRunError("dependency_path_probe_invalid")
    jwt_version = _final_release(versions["PyJWT"])
    cryptography_version = _final_release(versions["cryptography"])
    if not (
        jwt_version >= (2, 13)
        and jwt_version < (3,)
        and cryptography_version >= (42,)
    ):
        raise V1InternalRepeatabilityRunError(
            "preprovisioned_dependency_versions_unsupported"
        )
    return paths


def _dependency_paths(
    python: Path, temporary: Path, source_root: Path
) -> list[str]:
    result = _run(
        [str(python), "-I", "-B", "-c", _DEPENDENCY_PATH_PROBE],
        cwd=temporary,
        environment=_safe_environment(temporary),
    )
    if result.returncode != 0:
        raise V1InternalRepeatabilityRunError("preprovisioned_dependencies_unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise V1InternalRepeatabilityRunError("dependency_path_probe_invalid") from error
    return _validate_dependency_probe(value, source_root)


def _load_candidate(
    manifest_path: Path, archive_path: Path
) -> tuple[dict[str, object], bytes]:
    manifest_payload = v1_candidate._safe_read(
        manifest_path,
        maximum=v1_candidate.MAX_JSON_BYTES,
        label="manifest",
    )
    manifest = v1_candidate.validate_manifest(
        v1_candidate._decode_json(manifest_payload, label="manifest")
    )
    archive_payload = v1_candidate._safe_read(
        archive_path,
        maximum=v1_candidate.MAX_ARCHIVE_BYTES,
        label="archive",
    )
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    if (
        archive_path.name != candidate["artifact_name"]
        or len(archive_payload) != candidate["artifact_size_bytes"]
        or _sha256(archive_payload) != candidate["artifact_digest"]
    ):
        raise V1InternalRepeatabilityRunError("candidate_archive_binding_invalid")
    v1_candidate.inspect_archive(archive_path)
    return manifest, archive_payload


def _extract_source(payload: bytes, destination: Path) -> Path:
    destination.mkdir(mode=0o700)
    expected_root = f"{v1_candidate.PACKAGE_NAME}-{v1_candidate.PACKAGE_VERSION}"
    total = 0
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > v1_candidate.MAX_ARCHIVE_MEMBERS:
                raise V1InternalRepeatabilityRunError("archive_member_count_invalid")
            for member in members:
                normalized = member.name.rstrip("/")
                parts = normalized.split("/")
                if (
                    not normalized
                    or member.name.startswith("/")
                    or parts[0] != expected_root
                    or any(part in {"", ".", ".."} for part in parts)
                    or normalized in seen
                    or not (member.isdir() or member.isfile())
                ):
                    raise V1InternalRepeatabilityRunError("archive_member_unsafe")
                seen.add(normalized)
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if member.size < 0 or member.size > MAX_EXTRACTED_FILE_BYTES:
                    raise V1InternalRepeatabilityRunError("archive_member_size_invalid")
                total += member.size
                if total > MAX_EXTRACTED_BYTES:
                    raise V1InternalRepeatabilityRunError("archive_expanded_size_invalid")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise V1InternalRepeatabilityRunError("archive_member_unavailable")
                encoded = source.read(member.size + 1)
                if len(encoded) != member.size:
                    raise V1InternalRepeatabilityRunError("archive_member_size_invalid")
                descriptor = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                )
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        output.write(encoded)
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    except V1InternalRepeatabilityRunError:
        raise
    except (OSError, tarfile.TarError, EOFError) as error:
        raise V1InternalRepeatabilityRunError("archive_extraction_failed") from error

    source_root = destination / expected_root
    if not source_root.is_dir():
        raise V1InternalRepeatabilityRunError("archive_root_invalid")
    for path in sorted(source_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path, 0o500 if path.is_dir() else 0o400)
    os.chmod(source_root, 0o500)
    return source_root


def _write_private_json(path: Path, value: object) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            destination.write(encoded)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _prepare_workspace(source_root: Path, workspace: Path) -> tuple[Path, Path, Path, Path]:
    workspace.mkdir(mode=0o700)
    config_path = workspace / "hormuz.json"
    baseline_path = workspace / "baseline.json"
    candidate_path = workspace / "candidate.json"
    scenarios_path = workspace / "scenarios.json"
    database_path = workspace / "usage.sqlite3"

    config = json.loads(
        (source_root / "config.example.json").read_text(encoding="utf-8"),
        object_pairs_hook=repeatability._strict_object,
        parse_constant=repeatability._reject_json_constant,
    )
    if not isinstance(config, dict):
        raise V1InternalRepeatabilityRunError("configuration_invalid")
    config["database"] = str(database_path)
    _write_private_json(config_path, config)
    for source, destination in (
        (source_root / "examples/policy-admin-usability-baseline.json", baseline_path),
        (source_root / "examples/policy-admin-usability-scenarios.json", scenarios_path),
    ):
        payload = source.read_bytes()
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                output.write(payload)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if (
        _sha256(baseline_path.read_bytes()) != repeatability.BASELINE_ASSET_SHA256
        or _sha256(scenarios_path.read_bytes())
        != repeatability.SCENARIO_SUITE_ASSET_SHA256
    ):
        raise V1InternalRepeatabilityRunError("task_asset_digest_invalid")
    return config_path, baseline_path, candidate_path, scenarios_path


def _cli_command(
    python: Path,
    source_root: Path,
    dependency_paths: list[str],
    arguments: list[str],
) -> list[str]:
    return [
        str(python),
        "-I",
        "-B",
        "-c",
        _OFFLINE_CLI_WRAPPER,
        str(source_root),
        json.dumps(dependency_paths),
        *arguments,
    ]


def _run_cli(
    python: Path,
    source_root: Path,
    dependency_paths: list[str],
    arguments: list[str],
    *,
    workspace: Path,
    setup: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        _cli_command(python, source_root, dependency_paths, arguments),
        cwd=workspace,
        environment=_safe_environment(workspace, setup=setup),
    )


def _create_virtual_environment(host_python: Path, path: Path, temporary: Path) -> Path:
    result = _run(
        [str(host_python), "-I", "-B", "-m", "venv", "--without-pip", str(path)],
        cwd=temporary,
        environment=_safe_environment(temporary),
    )
    if result.returncode != 0:
        raise V1InternalRepeatabilityRunError("virtual_environment_creation_failed")
    executable = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise V1InternalRepeatabilityRunError("virtual_environment_python_missing")
    return executable


def _probe_source(
    python: Path,
    source_root: Path,
    dependency_paths: list[str],
    workspace: Path,
) -> None:
    result = _run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            _SOURCE_PROBE,
            str(source_root),
            json.dumps(dependency_paths),
        ],
        cwd=workspace,
        environment=_safe_environment(workspace),
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise V1InternalRepeatabilityRunError("candidate_source_probe_invalid") from error
    if result.returncode != 0 or value != {
        "version": repeatability.PACKAGE_VERSION,
        "loaded_from_archive": True,
    }:
        raise V1InternalRepeatabilityRunError("candidate_source_probe_invalid")


def _initialize_zero_usage(
    python: Path,
    source_root: Path,
    dependency_paths: list[str],
    workspace: Path,
    config_path: Path,
) -> None:
    common = ["--config", str(config_path)]
    migrated = _run_cli(
        python,
        source_root,
        dependency_paths,
        [*common, "storage", "migrate"],
        workspace=workspace,
        setup=True,
    )
    if migrated.returncode != 0:
        raise V1InternalRepeatabilityRunError("sqlite_setup_failed")
    status = _run_cli(
        python,
        source_root,
        dependency_paths,
        [*common, "status", "--json"],
        workspace=workspace,
        setup=True,
    )
    try:
        report = json.loads(
            status.stdout,
            object_pairs_hook=repeatability._strict_object,
            parse_constant=repeatability._reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise V1InternalRepeatabilityRunError("sqlite_zero_usage_probe_invalid") from error
    if (
        status.returncode != 0
        or not isinstance(report, dict)
        or report.get("schema_id") != "hormuz.usage-report"
        or report.get("rows") != []
    ):
        raise V1InternalRepeatabilityRunError("sqlite_zero_usage_probe_invalid")
    database = workspace / "usage.sqlite3"
    if not database.is_file():
        raise V1InternalRepeatabilityRunError("sqlite_setup_failed")
    os.chmod(database, 0o600)


def _modify_candidate(path: Path) -> None:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise V1InternalRepeatabilityRunError("candidate_not_regular")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=repeatability._strict_object,
            parse_constant=repeatability._reject_json_constant,
        )
        policies = value["policies"]
        organization = policies["organization"]
        if organization["max_output_tokens"] != 16_000:
            raise V1InternalRepeatabilityRunError("candidate_baseline_unexpected")
        organization["max_output_tokens"] = 4_000
        encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                output.write(encoded)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        os.replace(temporary, path)
    except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V1InternalRepeatabilityRunError("candidate_modification_failed") from error
    if _sha256(path.read_bytes()) != repeatability.CANDIDATE_ASSET_SHA256:
        raise V1InternalRepeatabilityRunError("candidate_asset_digest_invalid")


def _decode_contract(output: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            output,
            object_pairs_hook=repeatability._strict_object,
            parse_constant=repeatability._reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise V1InternalRepeatabilityRunError(f"{label}_contract_invalid") from error
    if not isinstance(value, dict):
        raise V1InternalRepeatabilityRunError(f"{label}_contract_invalid")
    return value


def _validate_comparison(value: dict[str, Any]) -> None:
    expected_change = {
        "path": "policies.organization.max_output_tokens",
        "change_type": "changed",
        "before": 16_000,
        "after": 4_000,
    }
    if (
        value.get("schema_id") != "hormuz.policy-comparison"
        or value.get("schema_version") != 1
        or value.get("organization_id") != "xpounder"
        or value.get("identical") is not False
        or value.get("changes") != [expected_change]
        or value.get("baseline")
        != {
            "version_id": f"sha256:{repeatability.BASELINE_CONTENT_SHA256}",
            "content_sha256": repeatability.BASELINE_CONTENT_SHA256,
        }
        or value.get("candidate")
        != {
            "version_id": f"sha256:{repeatability.CANDIDATE_CONTENT_SHA256}",
            "content_sha256": repeatability.CANDIDATE_CONTENT_SHA256,
        }
    ):
        raise V1InternalRepeatabilityRunError("comparison_contract_invalid")


def _validate_evaluation(value: dict[str, Any]) -> None:
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 1:
        raise V1InternalRepeatabilityRunError("evaluation_contract_invalid")
    scenario = scenarios[0]
    try:
        baseline_decision = scenario["baseline"]["decision"]
        candidate_decision = scenario["candidate"]["decision"]
    except (KeyError, TypeError) as error:
        raise V1InternalRepeatabilityRunError("evaluation_contract_invalid") from error
    if (
        value.get("schema_id") != "hormuz.policy-evaluation"
        or value.get("schema_version") != 1
        or value.get("organization_id") != "xpounder"
        or value.get("usage_basis") != "current"
        or value.get("suite")
        != {
            "suite_id": f"sha256:{repeatability.SCENARIO_SUITE_CONTENT_SHA256}",
            "content_sha256": repeatability.SCENARIO_SUITE_CONTENT_SHA256,
            "scenario_count": 1,
        }
        or value.get("baseline")
        != {
            "version_id": f"sha256:{repeatability.BASELINE_CONTENT_SHA256}",
            "content_sha256": repeatability.BASELINE_CONTENT_SHA256,
        }
        or value.get("candidate")
        != {
            "version_id": f"sha256:{repeatability.CANDIDATE_CONTENT_SHA256}",
            "content_sha256": repeatability.CANDIDATE_CONTENT_SHA256,
        }
        or value.get("summary")
        != {
            "scenario_count": 1,
            "changed_count": 1,
            "unchanged_count": 0,
            "baseline_allowed_count": 1,
            "candidate_allowed_count": 1,
        }
        or not isinstance(scenario, dict)
        or scenario.get("scenario_id") != "output-cap"
        or scenario.get("changed") is not True
        or baseline_decision.get("allowed") is not True
        or candidate_decision.get("allowed") is not True
        or baseline_decision.get("max_output_tokens") != 16_000
        or candidate_decision.get("max_output_tokens") != 4_000
    ):
        raise V1InternalRepeatabilityRunError("evaluation_contract_invalid")


def _stage_records() -> list[dict[str, object]]:
    return [
        {"name": name, "status": "not_attempted", "exit_code": None}
        for name in repeatability.STAGES
    ]


def _complete_command_stage(
    records: list[dict[str, object]],
    index: int,
    result: subprocess.CompletedProcess[str],
) -> None:
    expected = repeatability.EXPECTED_EXIT_CODES[repeatability.STAGES[index]]
    if result.returncode != expected:
        exit_code = result.returncode if 0 <= result.returncode <= 255 else 255
        records[index].update(status="failed", exit_code=exit_code)
        raise _StageFailure(exit_code)
    records[index].update(status="completed", exit_code=expected)


def _fail_internal_stage(
    records: list[dict[str, object]], index: int
) -> NoReturn:
    records[index].update(status="failed", exit_code=255)
    raise _StageFailure(255)


def _run_command_stage(
    records: list[dict[str, object]],
    index: int,
    python: Path,
    source_root: Path,
    dependency_paths: list[str],
    arguments: list[str],
    *,
    workspace: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        result = _run_cli(
            python,
            source_root,
            dependency_paths,
            arguments,
            workspace=workspace,
        )
    except V1InternalRepeatabilityRunError:
        _fail_internal_stage(records, index)
    _complete_command_stage(records, index, result)
    return result


def _execute_one_run(
    *,
    run_index: int,
    invocation_root: Path,
    host_python: Path,
    source_root: Path,
    dependency_paths: list[str],
    candidate_digest: str,
) -> dict[str, object]:
    run_root = invocation_root / f"run-{run_index}"
    run_root.mkdir(mode=0o700)
    virtual_environment = _create_virtual_environment(
        host_python, run_root / "venv", run_root
    )
    workspace = run_root / "workspace"
    config_path, baseline_path, candidate_path, scenarios_path = _prepare_workspace(
        source_root, workspace
    )
    _probe_source(virtual_environment, source_root, dependency_paths, workspace)
    _initialize_zero_usage(
        virtual_environment,
        source_root,
        dependency_paths,
        workspace,
        config_path,
    )

    stages = _stage_records()
    started_at = _timestamp()
    started_monotonic = time.monotonic()
    observed: dict[str, object] | None = None
    common = ["--config", str(config_path)]
    try:
        _run_command_stage(
            stages,
            0,
            virtual_environment,
            source_root,
            dependency_paths,
            [
                *common,
                "policy",
                "create",
                "--template",
                "standard",
                "--organization",
                "xpounder",
                "--output",
                str(candidate_path),
            ],
            workspace=workspace,
        )

        try:
            _modify_candidate(candidate_path)
        except V1InternalRepeatabilityRunError:
            _fail_internal_stage(stages, 1)
        stages[1].update(status="completed", exit_code=0)

        _run_command_stage(
            stages,
            2,
            virtual_environment,
            source_root,
            dependency_paths,
            [*common, "policy", "validate", str(candidate_path)],
            workspace=workspace,
        )

        compared = _run_command_stage(
            stages,
            3,
            virtual_environment,
            source_root,
            dependency_paths,
            [
                *common,
                "policy",
                "compare",
                str(candidate_path),
                "--baseline",
                str(baseline_path),
                "--organization",
                "xpounder",
                "--json",
            ],
            workspace=workspace,
        )
        try:
            _validate_comparison(_decode_contract(compared.stdout, "comparison"))
        except V1InternalRepeatabilityRunError:
            stages[3].update(status="failed", exit_code=255)
            raise _StageFailure(255)

        _run_command_stage(
            stages,
            4,
            virtual_environment,
            source_root,
            dependency_paths,
            ["policy", "scenarios", "validate", str(scenarios_path)],
            workspace=workspace,
        )

        evaluated = _run_command_stage(
            stages,
            5,
            virtual_environment,
            source_root,
            dependency_paths,
            [
                *common,
                "policy",
                "evaluate",
                str(candidate_path),
                "--baseline",
                str(baseline_path),
                "--organization",
                "xpounder",
                "--scenarios",
                str(scenarios_path),
                "--json",
            ],
            workspace=workspace,
        )
        try:
            _validate_evaluation(_decode_contract(evaluated.stdout, "evaluation"))
        except V1InternalRepeatabilityRunError:
            stages[5].update(status="failed", exit_code=255)
            raise _StageFailure(255)
        observed = repeatability.expected_observation()
    except _StageFailure:
        pass

    finished_monotonic = time.monotonic()
    finished_at = _timestamp()
    passed = all(stage["status"] == "completed" for stage in stages)
    return {
        "run_id": "v1ir:" + str(uuid.uuid4()),
        "run_index": run_index,
        "candidate_artifact_digest": candidate_digest,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(finished_monotonic - started_monotonic, 6),
        "isolation": {
            "fresh_virtual_environment": True,
            "fresh_working_directory": True,
            "fresh_sqlite_database": True,
            "candidate_source_loaded_from_archive": True,
            "network_guard_enabled": True,
            "provider_credentials_unset": True,
            "policy_admin_credentials_unset": True,
            "setup_current_usage_zero": True,
        },
        "stages": stages,
        "outcome": "passed" if passed else "failed",
        "observed": observed if passed else None,
    }


def build_evidence(
    manifest: dict[str, object], runs: list[dict[str, object]]
) -> dict[str, object]:
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    return {
        "schema_id": repeatability.SCHEMA_ID,
        "schema_version": repeatability.SCHEMA_VERSION,
        "evidence_kind": "candidate_gate_evidence",
        "gate_issue": repeatability.GATE_ISSUE,
        "generated_at": _ceiling_second_timestamp(),
        "candidate": {
            "target_version": candidate["target_version"],
            "artifact_kind": candidate["artifact_kind"],
            "artifact_digest": candidate["artifact_digest"],
            "source_commit": candidate["source_commit"],
            "frozen_at": candidate["frozen_at"],
        },
        "execution_attestation": repeatability.execution_attestation(),
        "task": repeatability.task_contract(),
        "runs": runs,
    }


def run_checkpoint(
    *,
    manifest_path: Path,
    archive_path: Path,
    output_path: Path,
    python: Path,
) -> dict[str, object]:
    python = _absolute_without_resolving(python)
    if output_path.exists() or output_path.is_symlink():
        raise V1InternalRepeatabilityRunError("output_exists")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise V1InternalRepeatabilityRunError("python_unavailable")
    manifest, archive_payload = _load_candidate(manifest_path, archive_path)
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)

    with tempfile.TemporaryDirectory(prefix="hormuz-v1-repeatability-") as temporary:
        root = Path(temporary)
        source_root = _extract_source(archive_payload, root / "source")
        dependency_paths = _dependency_paths(python, root, source_root)
        runs = [
            _execute_one_run(
                run_index=index,
                invocation_root=root,
                host_python=python,
                source_root=source_root,
                dependency_paths=dependency_paths,
                candidate_digest=str(candidate["artifact_digest"]),
            )
            for index in range(1, repeatability.EXPECTED_RUN_COUNT + 1)
        ]
        evidence = build_evidence(manifest, runs)
        result = repeatability.validate_evidence(evidence)
        v1_candidate._write_new_private_json(output_path, evidence)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Preprovisioned Python with Hormuz runtime dependencies available offline",
    )
    args = parser.parse_args(argv)
    try:
        result = run_checkpoint(
            manifest_path=args.manifest,
            archive_path=args.archive,
            output_path=args.output,
            python=args.python,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        V1InternalRepeatabilityRunError,
    ) as error:
        code = str(error) or "repeatability_run_failed"
        print(f"v1 internal repeatability run failed: {code}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["eligible_for_v1_0_0_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
