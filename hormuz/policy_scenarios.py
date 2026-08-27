"""Portable, bounded request suites for repeatable policy evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - unavailable only on non-POSIX platforms
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - add fails closed without locking
    _fcntl = None

from .contracts import (
    ContractValidationError,
    POLICY_SCENARIO_MAX_COUNT,
    POLICY_SCENARIO_SUITE_SCHEMA_ID,
    POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
    validate_contract,
)


_MAX_POLICY_SCENARIO_BYTES = 1024 * 1024
_MAX_POLICY_SCENARIO_UPDATE_RETRIES = 8


class PolicyScenarioError(ValueError):
    """Stable, content-safe failure for scenario files and result artifacts."""

    def __init__(self, code: str, reason: str, *, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.hint = hint


@dataclass(frozen=True)
class PolicyScenario:
    scenario_id: str
    actor_id: str
    client: str
    protocol: str
    requested_model: str
    requested_output_tokens: int | None

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.scenario_id,
            "actor_id": self.actor_id,
            "client": self.client,
            "protocol": self.protocol,
            "requested_model": self.requested_model,
            "requested_output_tokens": self.requested_output_tokens,
        }


@dataclass(frozen=True)
class PolicyScenarioSuite:
    organization_id: str
    scenarios: tuple[PolicyScenario, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PolicyScenarioSuite":
        try:
            validate_contract(value)
        except ContractValidationError as error:
            raise PolicyScenarioError(
                "policy_scenario_suite_invalid",
                str(error),
                hint="Use `hormuz policy scenarios create` or correct the reported field.",
            ) from None
        scenarios = tuple(
            sorted(
                (
                    PolicyScenario(
                        scenario_id=item["id"],
                        actor_id=item["actor_id"],
                        client=item["client"],
                        protocol=item["protocol"],
                        requested_model=item["requested_model"],
                        requested_output_tokens=item["requested_output_tokens"],
                    )
                    for item in value["scenarios"]
                ),
                key=lambda item: item.scenario_id,
            )
        )
        return cls(organization_id=value["organization_id"], scenarios=scenarios)

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "PolicyScenarioSuite":
        try:
            decoded = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            raise PolicyScenarioError(
                "policy_scenario_suite_invalid",
                "the scenario suite must be strict UTF-8 JSON with unique object keys",
                hint="Use `hormuz policy scenarios create` to produce a valid starting file.",
            ) from None
        if not isinstance(decoded, Mapping):
            raise PolicyScenarioError(
                "policy_scenario_suite_invalid",
                "the scenario suite must be a JSON object",
                hint="Use `hormuz policy scenarios create` to produce a valid starting file.",
            )
        return cls.from_mapping(decoded)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_id": POLICY_SCENARIO_SUITE_SCHEMA_ID,
            "schema_version": POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
            "organization_id": self.organization_id,
            "scenarios": [scenario.to_mapping() for scenario in self.scenarios],
        }

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_mapping())).hexdigest()

    @property
    def suite_id(self) -> str:
        return f"sha256:{self.content_sha256}"

    def with_scenario(self, scenario: PolicyScenario) -> "PolicyScenarioSuite":
        if any(item.scenario_id == scenario.scenario_id for item in self.scenarios):
            raise PolicyScenarioError(
                "policy_scenario_id_exists",
                "the scenario ID already exists in this suite",
                hint="Choose a new --id or edit the existing scenario explicitly.",
            )
        if len(self.scenarios) >= POLICY_SCENARIO_MAX_COUNT:
            raise PolicyScenarioError(
                "policy_scenario_limit_reached",
                f"a scenario suite may contain at most {POLICY_SCENARIO_MAX_COUNT} entries",
                hint="Split unrelated requests into another bounded suite.",
            )
        return PolicyScenarioSuite.from_mapping(
            {
                "schema_id": POLICY_SCENARIO_SUITE_SCHEMA_ID,
                "schema_version": POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
                "organization_id": self.organization_id,
                "scenarios": [
                    *(item.to_mapping() for item in self.scenarios),
                    scenario.to_mapping(),
                ],
            }
        )


def create_policy_scenario_suite(
    *,
    organization_id: str,
    scenario_id: str,
    actor_id: str,
    client: str,
    protocol: str,
    requested_model: str,
    requested_output_tokens: int | None,
) -> PolicyScenarioSuite:
    """Create and validate a canonical suite containing one explicit request."""

    return PolicyScenarioSuite.from_mapping(
        {
            "schema_id": POLICY_SCENARIO_SUITE_SCHEMA_ID,
            "schema_version": POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
            "organization_id": organization_id,
            "scenarios": [
                {
                    "id": scenario_id,
                    "actor_id": actor_id,
                    "client": client,
                    "protocol": protocol,
                    "requested_model": requested_model,
                    "requested_output_tokens": requested_output_tokens,
                }
            ],
        }
    )


def create_policy_scenario(
    *,
    organization_id: str,
    scenario_id: str,
    actor_id: str,
    client: str,
    protocol: str,
    requested_model: str,
    requested_output_tokens: int | None,
) -> PolicyScenario:
    """Validate one scenario through the complete portable suite contract."""

    return create_policy_scenario_suite(
        organization_id=organization_id,
        scenario_id=scenario_id,
        actor_id=actor_id,
        client=client,
        protocol=protocol,
        requested_model=requested_model,
        requested_output_tokens=requested_output_tokens,
    ).scenarios[0]


def add_policy_scenario_to_suite(
    path: str | Path,
    *,
    scenario_id: str,
    actor_id: str,
    client: str,
    protocol: str,
    requested_model: str,
    requested_output_tokens: int | None,
) -> PolicyScenarioSuite:
    """Serialize a complete read-modify-write add and reject outside edits."""

    selected = Path(path).expanduser()
    if _fcntl is None:
        raise PolicyScenarioError(
            "policy_scenario_lock_unavailable",
            "this platform cannot safely serialize scenario-suite additions",
            hint="Edit a copy explicitly and validate it before replacing the suite.",
        )
    for _ in range(_MAX_POLICY_SCENARIO_UPDATE_RETRIES):
        descriptor: int | None = None
        try:
            _validate_input_path(selected)
            descriptor = os.open(
                selected,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise PolicyScenarioError(
                    "policy_scenario_suite_not_regular",
                    "the scenario suite path is not a regular file",
                    hint="Choose a regular JSON file.",
                )
            _fcntl.flock(descriptor, _fcntl.LOCK_EX)
            if not _descriptor_matches_path(descriptor, selected):
                continue
            suite = PolicyScenarioSuite.from_json_bytes(_read_policy_scenario_bytes(descriptor))
            scenario = create_policy_scenario(
                organization_id=suite.organization_id,
                scenario_id=scenario_id,
                actor_id=actor_id,
                client=client,
                protocol=protocol,
                requested_model=requested_model,
                requested_output_tokens=requested_output_tokens,
            )
            updated = suite.with_scenario(scenario)
            replace_policy_scenario_suite(
                selected,
                updated,
                expected_content_sha256=suite.content_sha256,
            )
            return updated
        except PolicyScenarioError:
            raise
        except OSError:
            raise PolicyScenarioError(
                "policy_scenario_suite_unavailable",
                "the scenario suite could not be updated",
                hint="Check that the path and its directory are writable, then retry.",
            ) from None
        finally:
            if descriptor is not None:
                try:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    raise PolicyScenarioError(
        "policy_scenario_concurrent_update",
        "the scenario suite changed repeatedly during the add operation",
        hint="Retry after other scenario editors have finished.",
    )


def load_policy_scenario_suite(path: str | Path) -> PolicyScenarioSuite:
    """Read one bounded regular file without following a symbolic link."""

    selected = Path(path).expanduser()
    _validate_input_path(selected)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            selected,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PolicyScenarioError(
                "policy_scenario_suite_not_regular",
                "the scenario suite path is not a regular file",
                hint="Choose a regular JSON file.",
            )
        content = _read_policy_scenario_bytes(descriptor)
    except PolicyScenarioError:
        raise
    except OSError:
        raise PolicyScenarioError(
            "policy_scenario_suite_unavailable",
            "the scenario suite could not be read",
            hint="Check that the path exists and the current user can read it.",
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return PolicyScenarioSuite.from_json_bytes(content)


def write_policy_scenario_suite(
    path: Path,
    suite: PolicyScenarioSuite,
    *,
    force: bool,
) -> None:
    """Create or explicitly replace one canonical owner-only suite file."""

    _atomic_publish_json(
        path,
        suite.to_mapping(),
        overwrite=force,
        require_existing=False,
        maximum_bytes=_MAX_POLICY_SCENARIO_BYTES,
    )


def replace_policy_scenario_suite(
    path: Path,
    suite: PolicyScenarioSuite,
    *,
    expected_content_sha256: str,
) -> None:
    """Atomically replace the regular suite file that was explicitly edited."""

    _atomic_publish_json(
        path,
        suite.to_mapping(),
        overwrite=True,
        require_existing=True,
        maximum_bytes=_MAX_POLICY_SCENARIO_BYTES,
        expected_content_sha256=expected_content_sha256,
    )


def write_policy_evaluation(path: Path, value: Mapping[str, Any], *, force: bool) -> None:
    """Validate and atomically publish an owner-only evaluation contract."""

    try:
        validate_contract(value)
    except ContractValidationError as error:
        raise PolicyScenarioError(
            "policy_evaluation_invalid",
            "the generated policy evaluation failed contract validation",
            hint=str(error),
        ) from None
    _atomic_publish_json(path, value, overwrite=force, require_existing=False)


def _read_policy_scenario_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    content = bytearray()
    while len(content) <= _MAX_POLICY_SCENARIO_BYTES:
        chunk = os.read(
            descriptor,
            min(64 * 1024, _MAX_POLICY_SCENARIO_BYTES + 1 - len(content)),
        )
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > _MAX_POLICY_SCENARIO_BYTES:
        raise PolicyScenarioError(
            "policy_scenario_suite_too_large",
            "the scenario suite exceeds the 1 MiB limit",
            hint=f"Keep no more than {POLICY_SCENARIO_MAX_COUNT} explicit request scenarios.",
        )
    return bytes(content)


def _descriptor_matches_path(descriptor: int, path: Path) -> bool:
    """Return whether a locked descriptor is still the path's current inode."""

    try:
        descriptor_target = os.fstat(descriptor)
        path_target = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(path_target.st_mode)
        and descriptor_target.st_dev == path_target.st_dev
        and descriptor_target.st_ino == path_target.st_ino
    )


def _validate_input_path(path: Path) -> None:
    try:
        target = os.lstat(path)
    except FileNotFoundError:
        raise PolicyScenarioError(
            "policy_scenario_suite_unavailable",
            "the scenario suite could not be read",
            hint="Check that the path exists and the current user can read it.",
        ) from None
    except OSError:
        raise PolicyScenarioError(
            "policy_scenario_suite_unavailable",
            "the scenario suite path could not be inspected",
            hint="Check that the path exists and the current user can read it.",
        ) from None
    if stat.S_ISLNK(target.st_mode):
        raise PolicyScenarioError(
            "policy_scenario_suite_symlink_refused",
            "the scenario suite path is a symbolic link",
            hint="Choose a regular JSON file; Hormuz does not follow scenario links.",
        )
    if not stat.S_ISREG(target.st_mode):
        raise PolicyScenarioError(
            "policy_scenario_suite_not_regular",
            "the scenario suite path is not a regular file",
            hint="Choose a regular JSON file.",
        )


def _atomic_publish_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    overwrite: bool,
    require_existing: bool,
    maximum_bytes: int | None = None,
    expected_content_sha256: str | None = None,
) -> None:
    _validate_output_target(path, overwrite=overwrite, require_existing=require_existing)
    try:
        serialized = _pretty_json(value)
    except (OverflowError, RecursionError, ValueError):
        if maximum_bytes is not None:
            raise _policy_scenario_suite_too_large() from None
        raise PolicyScenarioError(
            "policy_scenario_output_unavailable",
            "the scenario artifact could not be serialized",
        ) from None
    if maximum_bytes is not None and len(serialized) > maximum_bytes:
        raise _policy_scenario_suite_too_large()
    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("scenario artifact write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if overwrite:
            _validate_output_target(path, overwrite=True, require_existing=require_existing)
            if expected_content_sha256 is not None:
                current = load_policy_scenario_suite(path)
                if current.content_sha256 != expected_content_sha256:
                    raise PolicyScenarioError(
                        "policy_scenario_concurrent_update",
                        "the scenario suite changed during the add operation",
                        hint="Reload the suite and retry after other editors have finished.",
                    )
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise PolicyScenarioError(
                    "policy_scenario_output_exists",
                    "the selected output path already exists",
                    hint="Choose another path or pass --force to replace a regular file.",
                ) from None
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        temporary_path = None
    except PolicyScenarioError:
        raise
    except OSError:
        raise PolicyScenarioError(
            "policy_scenario_output_unavailable",
            "the scenario artifact could not be written",
            hint="Check that the output directory exists and is writable.",
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _validate_output_target(path: Path, *, overwrite: bool, require_existing: bool) -> None:
    try:
        target = os.lstat(path)
    except FileNotFoundError:
        if require_existing:
            raise PolicyScenarioError(
                "policy_scenario_suite_unavailable",
                "the scenario suite disappeared before it could be updated",
                hint="Reload the suite and retry the explicit add operation.",
            ) from None
        return
    except OSError:
        raise PolicyScenarioError(
            "policy_scenario_output_unavailable",
            "the scenario output path could not be inspected",
            hint="Check that the output directory exists and is accessible.",
        ) from None
    if stat.S_ISLNK(target.st_mode):
        raise PolicyScenarioError(
            "policy_scenario_output_symlink_refused",
            "the selected output path is a symbolic link",
            hint="Choose a regular file path; Hormuz never follows scenario output links.",
        )
    if not stat.S_ISREG(target.st_mode):
        raise PolicyScenarioError(
            "policy_scenario_output_not_regular",
            "the selected output path is not a regular file",
            hint="Choose a regular file path; Hormuz never replaces special files or directories.",
        )
    if not overwrite:
        raise PolicyScenarioError(
            "policy_scenario_output_exists",
            "the selected output path already exists",
            hint="Choose another path or pass --force to replace a regular file.",
        )


def _policy_scenario_suite_too_large() -> PolicyScenarioError:
    return PolicyScenarioError(
        "policy_scenario_suite_too_large",
        "the scenario suite exceeds the 1 MiB limit",
        hint=f"Keep no more than {POLICY_SCENARIO_MAX_COUNT} explicit request scenarios.",
    )


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _pretty_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result
