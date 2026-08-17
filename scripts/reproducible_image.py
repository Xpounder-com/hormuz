#!/usr/bin/env python3
"""Build and compare two exact-source Hormuz OCI image layouts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Any


SCHEMA = "hormuz.reproducible-oci.v1"
DEFAULT_PLATFORM = "linux/amd64"
SUPPORTED_PLATFORMS = (DEFAULT_PLATFORM, "linux/arm64")
BUILDKIT_VERSION = "v0.32.2"
BUILDKIT_IMAGE = (
    "moby/buildkit:v0.32.2@sha256:"
    "28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8"
)
BUILDER_DRIVER = "docker-container"
OCI_LAYOUT_VERSION = "1.0.0"
OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.-]+)?$")
MAX_SOURCE_MEMBERS = 10_000
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_LAYOUT_FILES = 512
MAX_LAYOUT_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_LAYERS = 128
MIN_SOURCE_DATE_EPOCH = 315_532_800
MAX_SOURCE_DATE_EPOCH = 4_294_967_295


class OCIReproducibilityError(RuntimeError):
    """Raised when exact-source OCI byte reproducibility cannot be proven."""


class _InvalidJSON(ValueError):
    pass


@dataclass(frozen=True)
class OCILayoutSummary:
    index_sha256: str
    manifest_digest: str
    config_digest: str
    layer_digests: tuple[str, ...]
    file_count: int
    total_bytes: int


def validate_source_sha(value: str) -> str:
    if not isinstance(value, str) or not COMMIT_SHA.fullmatch(value):
        raise OCIReproducibilityError(
            "source revision is not a full lowercase commit SHA"
        )
    return value


def _validate_epoch(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_SOURCE_DATE_EPOCH
        or value > MAX_SOURCE_DATE_EPOCH
    ):
        raise OCIReproducibilityError("source commit timestamp is outside build bounds")
    return value


def _validate_version(value: str) -> str:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise OCIReproducibilityError("project version is invalid")
    return value


def _validate_platform(value: str) -> str:
    if value not in SUPPORTED_PLATFORMS:
        raise OCIReproducibilityError("OCI reproducibility platform is unsupported")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise OCIReproducibilityError("reproducibility input cannot be read") from error
    return digest.hexdigest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJSON("duplicate member")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> object:
    raise _InvalidJSON("nonstandard constant")


def _strict_json(value: bytes, *, label: str) -> dict[str, object]:
    if not value or len(value) > MAX_JSON_BYTES:
        raise OCIReproducibilityError(f"{label} size is invalid")
    try:
        decoded = value.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise OCIReproducibilityError(f"{label} is not strict JSON") from error
    if not isinstance(parsed, dict):
        raise OCIReproducibilityError(f"{label} is not a JSON object")
    return parsed


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or not SHA256.fullmatch(value[7:])
    ):
        raise OCIReproducibilityError("OCI descriptor digest is invalid")
    return value


def _descriptor(
    value: object,
    *,
    media_type: str,
) -> tuple[str, int, dict[str, object]]:
    if not isinstance(value, dict):
        raise OCIReproducibilityError("OCI descriptor is invalid")
    if value.get("mediaType") != media_type:
        raise OCIReproducibilityError("OCI descriptor media type is invalid")
    digest = _digest(value.get("digest"))
    size = value.get("size")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or size > MAX_LAYOUT_BYTES
    ):
        raise OCIReproducibilityError("OCI descriptor size is invalid")
    return digest, size, value


def _layout_files(root: Path) -> dict[str, tuple[Path, int, str]]:
    try:
        if root.is_symlink() or not root.is_dir():
            raise OCIReproducibilityError("OCI layout root is unsafe")
    except OSError as error:
        raise OCIReproducibilityError("OCI layout root is unsafe") from error
    files: dict[str, tuple[Path, int, str]] = {}
    directories: set[str] = set()
    total_bytes = 0
    try:
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                raise OCIReproducibilityError("OCI layout contains a symbolic link")
            if stat.S_ISDIR(details.st_mode):
                directories.add(relative)
                continue
            if not stat.S_ISREG(details.st_mode):
                raise OCIReproducibilityError("OCI layout contains a special file")
            if relative in files:
                raise OCIReproducibilityError("OCI layout contains duplicate files")
            total_bytes += details.st_size
            if (
                len(files) + 1 > MAX_LAYOUT_FILES
                or details.st_size < 0
                or total_bytes > MAX_LAYOUT_BYTES
            ):
                raise OCIReproducibilityError("OCI layout exceeds supported bounds")
            files[relative] = (path, details.st_size, _sha256_file(path))
    except OCIReproducibilityError:
        raise
    except OSError as error:
        raise OCIReproducibilityError("OCI layout cannot be inspected") from error

    required_directories = {"blobs", "blobs/sha256"}
    if (
        not required_directories.issubset(directories)
        or directories - required_directories - {"ingest"}
    ):
        raise OCIReproducibilityError("OCI layout directories are invalid")
    if "index.json" not in files or "oci-layout" not in files:
        raise OCIReproducibilityError("OCI layout control files are missing")
    for relative in files:
        if relative in {"index.json", "oci-layout"}:
            continue
        parts = PurePosixPath(relative).parts
        if (
            len(parts) != 3
            or parts[:2] != ("blobs", "sha256")
            or not SHA256.fullmatch(parts[2])
        ):
            raise OCIReproducibilityError("OCI layout contains an unexpected file")
    return files


def _read_layout_file(
    files: dict[str, tuple[Path, int, str]],
    relative: str,
) -> bytes:
    selected = files.get(relative)
    if selected is None:
        raise OCIReproducibilityError("OCI descriptor blob is missing")
    path, size, digest_value = selected
    if size > MAX_JSON_BYTES and relative in {"index.json", "oci-layout"}:
        raise OCIReproducibilityError("OCI control file exceeds supported bounds")
    try:
        value = path.read_bytes()
    except OSError as error:
        raise OCIReproducibilityError("OCI layout file cannot be read") from error
    if len(value) != size or _sha256_bytes(value) != digest_value:
        raise OCIReproducibilityError("OCI layout file changed during inspection")
    return value


def _read_blob(
    files: dict[str, tuple[Path, int, str]],
    digest: str,
    expected_size: int,
) -> bytes:
    path = _verify_blob(files, digest, expected_size)
    try:
        value = path.read_bytes()
    except OSError as error:
        raise OCIReproducibilityError("OCI descriptor blob cannot be read") from error
    if len(value) != expected_size or _sha256_bytes(value) != digest[7:]:
        raise OCIReproducibilityError("OCI descriptor blob changed during inspection")
    return value


def _verify_blob(
    files: dict[str, tuple[Path, int, str]],
    digest: str,
    expected_size: int,
) -> Path:
    hex_digest = digest[7:]
    relative = f"blobs/sha256/{hex_digest}"
    selected = files.get(relative)
    if selected is None:
        raise OCIReproducibilityError("OCI descriptor blob is missing")
    path, size, actual_digest = selected
    if size != expected_size or actual_digest != hex_digest:
        raise OCIReproducibilityError("OCI descriptor blob does not match its digest")
    return path


def _created_at(source_date_epoch: int) -> str:
    return datetime.fromtimestamp(
        _validate_epoch(source_date_epoch), timezone.utc
    ).isoformat().replace("+00:00", "Z")


def validate_oci_layout(
    root: Path,
    *,
    source_sha: str,
    source_date_epoch: int,
    version: str,
    platform: str,
) -> OCILayoutSummary:
    """Validate a bounded single-platform OCI layout and every referenced blob."""

    sha = validate_source_sha(source_sha)
    epoch = _validate_epoch(source_date_epoch)
    release_version = _validate_version(version)
    selected_platform = _validate_platform(platform)
    architecture = selected_platform.split("/", 1)[1]
    files = _layout_files(root)

    layout_bytes = _read_layout_file(files, "oci-layout")
    layout = _strict_json(layout_bytes, label="OCI layout declaration")
    if layout != {"imageLayoutVersion": OCI_LAYOUT_VERSION}:
        raise OCIReproducibilityError("OCI layout version is unsupported")

    index_bytes = _read_layout_file(files, "index.json")
    index = _strict_json(index_bytes, label="OCI index")
    if (
        index.get("schemaVersion") != 2
        or index.get("mediaType") != OCI_INDEX_MEDIA_TYPE
    ):
        raise OCIReproducibilityError("OCI index contract is invalid")
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise OCIReproducibilityError("OCI index must contain one manifest")
    manifest_digest, manifest_size, manifest_descriptor = _descriptor(
        manifests[0], media_type=OCI_MANIFEST_MEDIA_TYPE
    )
    if manifest_descriptor.get("platform") != {
        "architecture": architecture,
        "os": "linux",
    }:
        raise OCIReproducibilityError("OCI manifest platform is invalid")
    manifest_bytes = _read_blob(files, manifest_digest, manifest_size)
    manifest = _strict_json(manifest_bytes, label="OCI image manifest")
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE
    ):
        raise OCIReproducibilityError("OCI image manifest contract is invalid")

    config_digest, config_size, _config_descriptor = _descriptor(
        manifest.get("config"), media_type=OCI_CONFIG_MEDIA_TYPE
    )
    layer_values = manifest.get("layers")
    if (
        not isinstance(layer_values, list)
        or not layer_values
        or len(layer_values) > MAX_LAYERS
    ):
        raise OCIReproducibilityError("OCI image layer count is invalid")
    layers: list[str] = []
    for layer_value in layer_values:
        layer_digest, layer_size, _layer_descriptor = _descriptor(
            layer_value, media_type=OCI_LAYER_MEDIA_TYPE
        )
        _verify_blob(files, layer_digest, layer_size)
        layers.append(layer_digest)

    config_bytes = _read_blob(files, config_digest, config_size)
    config = _strict_json(config_bytes, label="OCI image configuration")
    if config.get("architecture") != architecture or config.get("os") != "linux":
        raise OCIReproducibilityError("OCI image configuration platform is invalid")
    if config.get("created") != _created_at(epoch):
        raise OCIReproducibilityError("OCI image creation time is not source-bound")
    runtime = config.get("config")
    if not isinstance(runtime, dict) or runtime.get("User") != "65532:65532":
        raise OCIReproducibilityError("OCI image runtime user is invalid")
    labels = runtime.get("Labels")
    if (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != sha
        or labels.get("org.opencontainers.image.version") != release_version
    ):
        raise OCIReproducibilityError("OCI image identity labels are invalid")
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict) or rootfs.get("type") != "layers":
        raise OCIReproducibilityError("OCI image root filesystem is invalid")
    diff_ids = rootfs.get("diff_ids")
    if (
        not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or not all(
            isinstance(value, str)
            and value.startswith("sha256:")
            and SHA256.fullmatch(value[7:])
            for value in diff_ids
        )
    ):
        raise OCIReproducibilityError("OCI root filesystem digests are invalid")

    referenced = {manifest_digest[7:], config_digest[7:]}
    referenced.update(value[7:] for value in layers)
    stored = {
        PurePosixPath(relative).parts[2]
        for relative in files
        if relative.startswith("blobs/sha256/")
    }
    if stored != referenced:
        raise OCIReproducibilityError("OCI layout contains unreferenced blobs")

    return OCILayoutSummary(
        index_sha256=_sha256_bytes(index_bytes),
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        layer_digests=tuple(layers),
        file_count=len(files),
        total_bytes=sum(value[1] for value in files.values()),
    )


def compare_oci_layouts(first: Path, second: Path) -> None:
    """Require identical relative files, sizes, and bytes across two layouts."""

    first_files = _layout_files(first)
    second_files = _layout_files(second)
    first_values = {
        relative: (details[1], details[2])
        for relative, details in first_files.items()
    }
    second_values = {
        relative: (details[1], details[2])
        for relative, details in second_files.items()
    }
    if first_values != second_values:
        raise OCIReproducibilityError(
            "independent OCI builds are not byte-identical"
        )


def canonicalize_oci_layout(
    source: Path,
    destination: Path,
    *,
    source_date_epoch: int,
) -> None:
    """Write one deterministic tar transport for an already validated OCI layout."""

    epoch = _validate_epoch(source_date_epoch)
    files = _layout_files(source)
    if destination.exists():
        raise OCIReproducibilityError("OCI archive destination already exists")
    try:
        with destination.open("xb") as raw:
            with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for relative in sorted(files):
                    path, size, _digest_value = files[relative]
                    member = tarfile.TarInfo(relative)
                    member.size = size
                    member.mode = 0o644
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.mtime = epoch
                    member.pax_headers = {}
                    with path.open("rb") as content:
                        archive.addfile(member, content)
    except OCIReproducibilityError:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    except (OSError, tarfile.TarError) as error:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise OCIReproducibilityError("OCI archive cannot be written") from error


def build_command(
    *,
    context: Path,
    destination: Path,
    source_sha: str,
    source_date_epoch: int,
    version: str,
    platform: str,
) -> list[str]:
    sha = validate_source_sha(source_sha)
    epoch = _validate_epoch(source_date_epoch)
    release_version = _validate_version(version)
    selected_platform = _validate_platform(platform)
    return [
        "docker",
        "buildx",
        "build",
        "--progress=plain",
        "--no-cache",
        "--pull=false",
        "--platform",
        selected_platform,
        "--build-arg",
        f"HORMUZ_REVISION={sha}",
        "--build-arg",
        f"HORMUZ_VERSION={release_version}",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={epoch}",
        "--provenance=false",
        "--sbom=false",
        "--output",
        f"type=oci,dest={destination},tar=false,rewrite-timestamp=true",
        str(context),
    ]


def validate_builder() -> None:
    """Require the active builder to use the reviewed driver and BuildKit release."""

    try:
        result = subprocess.run(
            ["docker", "buildx", "inspect", "--bootstrap"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OCIReproducibilityError("OCI builder inspection failed") from error
    if result.returncode != 0:
        raise OCIReproducibilityError("OCI builder inspection returned failure")
    drivers = re.findall(r"(?m)^Driver:\s+(\S+)\s*$", result.stdout)
    versions = re.findall(
        r"(?m)^BuildKit(?: version)?:\s+(\S+)\s*$", result.stdout
    )
    if drivers != [BUILDER_DRIVER] or not versions or any(
        version != BUILDKIT_VERSION for version in versions
    ):
        raise OCIReproducibilityError("OCI builder does not match the pinned contract")


def _run_git(arguments: list[str], *, project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OCIReproducibilityError("git source operation failed") from error
    if result.returncode != 0:
        raise OCIReproducibilityError("git source operation returned failure")
    return result.stdout


def _source_epoch(project_root: Path, source_sha: str) -> int:
    sha = validate_source_sha(source_sha)
    resolved = _run_git(
        ["rev-parse", "--verify", f"{sha}^{{commit}}"], project_root=project_root
    ).strip()
    if resolved != sha:
        raise OCIReproducibilityError("source revision is not available as a commit")
    checked_out = _run_git(["rev-parse", "HEAD"], project_root=project_root).strip()
    if checked_out != sha:
        raise OCIReproducibilityError("source revision is not the checked-out commit")
    timestamp = _run_git(
        ["show", "-s", "--format=%ct", sha], project_root=project_root
    ).strip()
    try:
        return _validate_epoch(int(timestamp))
    except ValueError as error:
        raise OCIReproducibilityError("source commit timestamp is unavailable") from error


def _safe_source_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != name
    ):
        raise OCIReproducibilityError("source export contains an unsafe path")
    return path


def _export_source(project_root: Path, source_sha: str, destination: Path) -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={destination}",
                source_sha,
            ],
            cwd=project_root,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OCIReproducibilityError("exact source export failed") from error
    if result.returncode != 0 or not destination.is_file():
        raise OCIReproducibilityError("exact source export failed")


def _extract_source(source: Path, destination: Path) -> None:
    destination.mkdir()
    total_bytes = 0
    names: set[str] = set()
    try:
        with tarfile.open(source, "r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_SOURCE_MEMBERS:
                raise OCIReproducibilityError("source export member count is invalid")
            for member in members:
                path = _safe_source_path(member.name)
                if member.name in names:
                    raise OCIReproducibilityError("source export contains duplicate paths")
                names.add(member.name)
                if not member.isdir() and not member.isfile():
                    raise OCIReproducibilityError(
                        "source export contains an unsupported member type"
                    )
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    continue
                total_bytes += member.size
                if member.size < 0 or total_bytes > MAX_SOURCE_BYTES:
                    raise OCIReproducibilityError("source export exceeds supported bounds")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise OCIReproducibilityError(
                        "source export file content is unavailable"
                    )
                content = extracted.read(MAX_SOURCE_BYTES + 1)
                if len(content) != member.size:
                    raise OCIReproducibilityError("source export file size is invalid")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except OCIReproducibilityError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise OCIReproducibilityError("exact source export cannot be extracted") from error


def _project_version(source_root: Path) -> str:
    try:
        project = tomllib.loads((source_root / "pyproject.toml").read_text())
        version = project["project"]["version"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError) as error:
        raise OCIReproducibilityError("project version cannot be read") from error
    return _validate_version(version)


def _base_image_digest(dockerfile: Path) -> str:
    try:
        text = dockerfile.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise OCIReproducibilityError("Dockerfile cannot be read") from error
    match = re.search(
        r"(?m)^ARG PYTHON_IMAGE=[^\s@]+@(sha256:[0-9a-f]{64})$", text
    )
    if match is None:
        raise OCIReproducibilityError("Dockerfile base image is not digest pinned")
    return match.group(1)


def _run_build(command: list[str], *, source_root: Path) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=source_root,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OCIReproducibilityError("OCI build process failed") from error
    if result.returncode != 0:
        raise OCIReproducibilityError("OCI builder returned failure")


def render_manifest(
    *,
    source_sha: str,
    source_date_epoch: int,
    version: str,
    platform: str,
    dockerfile_sha256: str,
    dependency_lock_sha256: str,
    base_image_digest: str,
    summary: OCILayoutSummary,
    artifact_filename: str,
    artifact_sha256: str,
    artifact_size: int,
) -> dict[str, Any]:
    sha = validate_source_sha(source_sha)
    epoch = _validate_epoch(source_date_epoch)
    release_version = _validate_version(version)
    selected_platform = _validate_platform(platform)
    if not all(
        SHA256.fullmatch(value)
        for value in (
            dockerfile_sha256,
            dependency_lock_sha256,
            artifact_sha256,
            summary.index_sha256,
        )
    ):
        raise OCIReproducibilityError("reproducibility manifest digest is invalid")
    _digest(base_image_digest)
    _digest(summary.manifest_digest)
    _digest(summary.config_digest)
    for value in summary.layer_digests:
        _digest(value)
    if (
        not artifact_filename
        or "/" in artifact_filename
        or "\\" in artifact_filename
        or isinstance(artifact_size, bool)
        or not isinstance(artifact_size, int)
        or artifact_size <= 0
        or summary.file_count <= 0
        or summary.total_bytes <= 0
    ):
        raise OCIReproducibilityError("reproducibility manifest metadata is invalid")
    return {
        "schema": SCHEMA,
        "source_sha": sha,
        "source_date_epoch": epoch,
        "version": release_version,
        "platform": selected_platform,
        "independent_builds": 2,
        "comparison": {
            "format": "oci-image-layout-1.0.0",
            "source_date_epoch_rewrite": True,
            "provenance": "disabled_for_byte_comparison",
            "sbom": "disabled_for_byte_comparison",
        },
        "builder": {
            "driver": BUILDER_DRIVER,
            "image": BUILDKIT_IMAGE,
            "version": BUILDKIT_VERSION,
        },
        "inputs": {
            "base_image_digest": base_image_digest,
            "dockerfile_sha256": dockerfile_sha256,
            "dependency_lock_sha256": dependency_lock_sha256,
        },
        "image": {
            "index_sha256": summary.index_sha256,
            "manifest_digest": summary.manifest_digest,
            "config_digest": summary.config_digest,
            "layer_digests": list(summary.layer_digests),
            "layout_file_count": summary.file_count,
            "layout_size_bytes": summary.total_bytes,
        },
        "artifact": {
            "filename": artifact_filename,
            "sha256": artifact_sha256,
            "size_bytes": artifact_size,
        },
    }


def build_reproducible_image(
    *,
    project_root: Path,
    source_sha: str,
    output_dir: Path,
    platform: str = DEFAULT_PLATFORM,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    output_dir = output_dir.expanduser().absolute()
    sha = validate_source_sha(source_sha)
    selected_platform = _validate_platform(platform)
    epoch = _source_epoch(project_root, sha)
    validate_builder()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(output_dir) and output_dir.is_symlink():
        raise OCIReproducibilityError("OCI output directory is unsafe")
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise OCIReproducibilityError("OCI output directory is not empty")

    with tempfile.TemporaryDirectory(
        prefix=".hormuz-reproducible-oci-", dir=output_dir.parent
    ) as temporary:
        root = Path(temporary)
        source_archive = root / "source.tar"
        _export_source(project_root, sha, source_archive)
        builds: list[tuple[Path, OCILayoutSummary]] = []
        version: str | None = None
        dockerfile_digest: str | None = None
        dependency_digest: str | None = None
        base_digest: str | None = None
        for label in ("a", "b"):
            source_root = root / f"source-{label}"
            layout = root / f"layout-{label}"
            _extract_source(source_archive, source_root)
            selected_version = _project_version(source_root)
            selected_dockerfile_digest = _sha256_file(source_root / "Dockerfile")
            selected_dependency_digest = _sha256_file(
                source_root / "deploy/container/requirements.lock"
            )
            selected_base_digest = _base_image_digest(source_root / "Dockerfile")
            inputs = (
                selected_version,
                selected_dockerfile_digest,
                selected_dependency_digest,
                selected_base_digest,
            )
            if version is None:
                (
                    version,
                    dockerfile_digest,
                    dependency_digest,
                    base_digest,
                ) = inputs
            elif inputs != (
                version,
                dockerfile_digest,
                dependency_digest,
                base_digest,
            ):
                raise OCIReproducibilityError(
                    "independent source exports have different OCI inputs"
                )
            command = build_command(
                context=source_root,
                destination=layout,
                source_sha=sha,
                source_date_epoch=epoch,
                version=selected_version,
                platform=selected_platform,
            )
            _run_build(command, source_root=source_root)
            summary = validate_oci_layout(
                layout,
                source_sha=sha,
                source_date_epoch=epoch,
                version=selected_version,
                platform=selected_platform,
            )
            builds.append((layout, summary))

        if (
            version is None
            or dockerfile_digest is None
            or dependency_digest is None
            or base_digest is None
        ):
            raise OCIReproducibilityError("OCI build inputs are unavailable")
        if builds[0][1] != builds[1][1]:
            raise OCIReproducibilityError(
                "independent OCI builds have different image metadata"
            )
        compare_oci_layouts(builds[0][0], builds[1][0])

        publish = root / "publish"
        publish.mkdir()
        artifact_filename = (
            f"hormuz-{version}-{selected_platform.replace('/', '-')}.oci.tar"
        )
        artifact = publish / artifact_filename
        canonicalize_oci_layout(
            builds[0][0], artifact, source_date_epoch=epoch
        )
        manifest = render_manifest(
            source_sha=sha,
            source_date_epoch=epoch,
            version=version,
            platform=selected_platform,
            dockerfile_sha256=dockerfile_digest,
            dependency_lock_sha256=dependency_digest,
            base_image_digest=base_digest,
            summary=builds[0][1],
            artifact_filename=artifact_filename,
            artifact_sha256=_sha256_file(artifact),
            artifact_size=artifact.stat().st_size,
        )
        (publish / "hormuz-oci-reproducibility.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        if output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError as error:
                raise OCIReproducibilityError(
                    "OCI output directory changed during build"
                ) from error
        try:
            publish.replace(output_dir)
        except OSError as error:
            raise OCIReproducibilityError("OCI artifacts cannot be published") from error
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--platform", choices=SUPPORTED_PLATFORMS, default=DEFAULT_PLATFORM
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        manifest = build_reproducible_image(
            project_root=arguments.project_root,
            source_sha=arguments.source_sha,
            output_dir=arguments.outdir,
            platform=arguments.platform,
        )
    except OCIReproducibilityError as error:
        print(f"reproducible OCI build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
