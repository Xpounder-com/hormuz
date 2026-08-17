#!/usr/bin/env python3
"""Validate the bounded, integrity-locked official-client test fixture."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
from typing import Any


EXPECTED_NAME = "hormuz-pinned-official-clients"
EXPECTED_NODE = "24.19.0"
EXPECTED_NPM = "npm@11.17.0"
EXPECTED_LOCK_SHA256 = (
    "46c78e86875212e56fab397915d9e9caf93813809b6049787b5784a2408f1d0c"
)
EXPECTED_DIRECT = {
    "@anthropic-ai/claude-code": "2.1.233",
    "@openai/codex": "0.147.0",
}
EXPECTED_DIRECT_INTEGRITY = {
    "node_modules/@anthropic-ai/claude-code": (
        "sha512-WS0ZSsNu2zkQonC+rW7HdByMCkPQ2l+hO1G0LdvWTj40kiYr0qAiSJjCBNRIbi0f"
        "oBol4IFTCKwLHAN83qxxUQ=="
    ),
    "node_modules/@openai/codex": (
        "sha512-EQLEXecAG2ptxI7UpBMo2TR/ga5596/c/OsYF/0LoUDh5JANZ7IoGqlzBEWbuEVQ76J"
        "ePIbtTW/ihCkp1a7Z3w=="
    ),
}
EXPECTED_PACKAGES = {
    "node_modules/@anthropic-ai/claude-code": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-darwin-arm64": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-darwin-x64": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-linux-arm64": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-linux-arm64-musl": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-linux-x64": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-linux-x64-musl": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-win32-arm64": "2.1.233",
    "node_modules/@anthropic-ai/claude-code-win32-x64": "2.1.233",
    "node_modules/@openai/codex": "0.147.0",
    "node_modules/@openai/codex-darwin-arm64": "0.147.0-darwin-arm64",
    "node_modules/@openai/codex-darwin-x64": "0.147.0-darwin-x64",
    "node_modules/@openai/codex-linux-arm64": "0.147.0-linux-arm64",
    "node_modules/@openai/codex-linux-x64": "0.147.0-linux-x64",
    "node_modules/@openai/codex-win32-arm64": "0.147.0-win32-arm64",
    "node_modules/@openai/codex-win32-x64": "0.147.0-win32-x64",
}
EXPECTED_PLATFORM = {
    "node_modules/@anthropic-ai/claude-code-darwin-arm64": ("darwin", "arm64"),
    "node_modules/@anthropic-ai/claude-code-darwin-x64": ("darwin", "x64"),
    "node_modules/@anthropic-ai/claude-code-linux-arm64": ("linux", "arm64"),
    "node_modules/@anthropic-ai/claude-code-linux-arm64-musl": ("linux", "arm64"),
    "node_modules/@anthropic-ai/claude-code-linux-x64": ("linux", "x64"),
    "node_modules/@anthropic-ai/claude-code-linux-x64-musl": ("linux", "x64"),
    "node_modules/@anthropic-ai/claude-code-win32-arm64": ("win32", "arm64"),
    "node_modules/@anthropic-ai/claude-code-win32-x64": ("win32", "x64"),
    "node_modules/@openai/codex-darwin-arm64": ("darwin", "arm64"),
    "node_modules/@openai/codex-darwin-x64": ("darwin", "x64"),
    "node_modules/@openai/codex-linux-arm64": ("linux", "arm64"),
    "node_modules/@openai/codex-linux-x64": ("linux", "x64"),
    "node_modules/@openai/codex-win32-arm64": ("win32", "arm64"),
    "node_modules/@openai/codex-win32-x64": ("win32", "x64"),
}
EXPECTED_INSTALL_SCRIPT_PACKAGE = "node_modules/@anthropic-ai/claude-code"
REGISTRY_TARBALL = re.compile(
    r"^https://registry\.npmjs\.org/@(?:anthropic-ai|openai)/.+\.tgz$"
)
MAX_MANIFEST_BYTES = 1024 * 1024


class ClientLockContractError(RuntimeError):
    """Raised when the pinned official-client fixture violates its contract."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ClientLockContractError("client lock JSON contains a duplicate key")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ClientLockContractError("client lock input is unavailable") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ClientLockContractError("client lock input exceeds the size limit")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClientLockContractError("client lock input is not strict JSON") from error
    if not isinstance(value, dict):
        raise ClientLockContractError("client lock input must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ClientLockContractError("client lock input is unavailable") from error
    return digest.hexdigest()


def _validate_integrity(value: Any) -> None:
    if not isinstance(value, str) or not value.startswith("sha512-"):
        raise ClientLockContractError("client package integrity is not SHA-512")
    try:
        decoded = base64.b64decode(value.removeprefix("sha512-"), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ClientLockContractError("client package integrity is invalid") from error
    if len(decoded) != 64:
        raise ClientLockContractError("client package integrity has the wrong length")


def validate_client_lock(package_file: Path, lock_file: Path) -> dict[str, Any]:
    package = _read_json(package_file)
    lock = _read_json(lock_file)
    expected_package_keys = {
        "name",
        "version",
        "private",
        "description",
        "packageManager",
        "engines",
        "dependencies",
    }
    if set(package) != expected_package_keys:
        raise ClientLockContractError("client package manifest has unexpected fields")
    if (
        package.get("name") != EXPECTED_NAME
        or package.get("version") != "0.0.0"
        or package.get("private") is not True
        or package.get("description")
        != "Integrity-locked official client fixture for Hormuz compatibility verification."
        or package.get("packageManager") != EXPECTED_NPM
        or package.get("engines") != {"node": EXPECTED_NODE}
        or package.get("dependencies") != EXPECTED_DIRECT
    ):
        raise ClientLockContractError("client package manifest is not the approved fixture")

    lock_digest = _sha256(lock_file)
    if lock_digest != EXPECTED_LOCK_SHA256:
        raise ClientLockContractError("client lock digest is not approved")
    if set(lock) != {"name", "version", "lockfileVersion", "requires", "packages"}:
        raise ClientLockContractError("client lock has unexpected top-level fields")
    if (
        lock.get("name") != EXPECTED_NAME
        or lock.get("version") != "0.0.0"
        or lock.get("lockfileVersion") != 3
        or lock.get("requires") is not True
    ):
        raise ClientLockContractError("client lock header is invalid")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise ClientLockContractError("client lock packages must be an object")
    if set(packages) != {"", *EXPECTED_PACKAGES}:
        raise ClientLockContractError("client lock package closure is not exact")
    root = packages.get("")
    if not isinstance(root, dict) or root != {
        "name": EXPECTED_NAME,
        "version": "0.0.0",
        "dependencies": EXPECTED_DIRECT,
        "engines": {"node": EXPECTED_NODE},
    }:
        raise ClientLockContractError("client lock root does not match its manifest")

    install_script_packages: set[str] = set()
    for path, expected_version in EXPECTED_PACKAGES.items():
        record = packages.get(path)
        if not isinstance(record, dict) or record.get("version") != expected_version:
            raise ClientLockContractError("client package version is not approved")
        resolved = record.get("resolved")
        if not isinstance(resolved, str) or REGISTRY_TARBALL.fullmatch(resolved) is None:
            raise ClientLockContractError("client package is not from the approved registry")
        _validate_integrity(record.get("integrity"))
        if record.get("link") is not None or record.get("inBundle") is not None:
            raise ClientLockContractError("client lock contains a linked or bundled package")
        if record.get("dependencies"):
            raise ClientLockContractError("client lock contains an unexpected transitive edge")
        if record.get("hasInstallScript") is True:
            install_script_packages.add(path)

        platform = EXPECTED_PLATFORM.get(path)
        if platform is None:
            if record.get("optional") is True or "os" in record or "cpu" in record:
                raise ClientLockContractError("direct client package has platform constraints")
        elif (
            record.get("optional") is not True
            or record.get("os") != [platform[0]]
            or record.get("cpu") != [platform[1]]
        ):
            raise ClientLockContractError("client platform package constraints are invalid")

    if install_script_packages != {EXPECTED_INSTALL_SCRIPT_PACKAGE}:
        raise ClientLockContractError("client lifecycle script set is not approved")
    for path, integrity in EXPECTED_DIRECT_INTEGRITY.items():
        if packages[path].get("integrity") != integrity:
            raise ClientLockContractError("direct client integrity changed")

    return {
        "schema": "hormuz.pinned-client-lock.v1",
        "node_version": EXPECTED_NODE,
        "npm_version": EXPECTED_NPM.removeprefix("npm@"),
        "registry": "https://registry.npmjs.org",
        "package_count": len(EXPECTED_PACKAGES),
        "package_lock_sha256": lock_digest,
        "clients": [
            {
                "name": name,
                "version": version,
                "integrity": packages[f"node_modules/{name}"]["integrity"],
            }
            for name, version in sorted(EXPECTED_DIRECT.items())
        ],
        "explicit_lifecycle_script": {
            "package": "@anthropic-ai/claude-code",
            "path": "install.cjs",
        },
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        raise ClientLockContractError("cannot write client lock evidence") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the pinned official-client npm lock contract."
    )
    parser.add_argument(
        "--package-file",
        type=Path,
        default=Path("deploy/clients/package.json"),
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("deploy/clients/package-lock.json"),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = validate_client_lock(arguments.package_file, arguments.lock_file)
        if arguments.output is not None:
            _write_json(arguments.output, result)
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    except ClientLockContractError as error:
        print(f"client lock validation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
