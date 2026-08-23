"""Mechanical helpers shared by independently executable security proofs.

This module deliberately does not know any proof schema, required check, or
security assertion. Callers must validate their own evidence before using the
serialization helper and must interpret command results in their own domain.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def is_sha256_digest(value: object) -> bool:
    """Return whether ``value`` is one complete lowercase SHA-256 digest."""

    return isinstance(value, str) and _SHA256_DIGEST.fullmatch(value) is not None


def is_pinned_image_reference(value: object, *, image_name: str | None = None) -> bool:
    """Return whether an image reference has a complete immutable digest."""

    if not isinstance(value, str):
        return False
    name, separator, digest = value.rpartition("@")
    if not name or not separator or not is_sha256_digest(digest):
        return False
    return image_name is None or name == image_name


def file_sha256(path: Path) -> str:
    """Hash one artifact without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def canonical_json_sha256(value: object) -> str:
    """Hash an already normalized JSON value using the stable compact form."""

    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def run_container_command(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    capture_stderr: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute one explicit Docker command and return its uninterpreted result."""

    arguments = tuple(command)
    if not arguments or arguments[0] != "docker":
        raise ValueError("container_command_must_use_docker")
    if capture_stderr:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    return subprocess.run(
        arguments,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
    )


def write_private_json_evidence(
    path: Path,
    value: Mapping[str, Any],
    *,
    indent: int | None = None,
    temporary_prefix: str | None = None,
    parent_mode: int = 0o777,
) -> None:
    """Atomically serialize caller-validated JSON evidence as an owner-only file.

    Existing-output policy and schema/content validation intentionally remain
    with each proof. This helper owns only canonical encoding, temporary-file
    cleanup, flush/fsync, atomic replacement, and the final file mode.
    """

    encoded = (
        json.dumps(
            dict(value),
            sort_keys=True,
            indent=indent,
            separators=(",", ":") if indent is None else None,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=parent_mode)
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=temporary_prefix or f".{path.name}.",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
