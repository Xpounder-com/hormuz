#!/usr/bin/env python3
"""Build byte-identical Hormuz wheel and source distributions twice."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Any


EXPECTED_BUILD_FRONTEND = "1.3.0"
EXPECTED_BUILD_REQUIREMENTS = (
    "setuptools==84.0.0",
    "wheel==0.48.0",
)
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MIN_SOURCE_DATE_EPOCH = 315_532_800  # 1980-01-01, the ZIP timestamp floor.
MAX_SOURCE_DATE_EPOCH = 4_294_967_295  # Maximum unsigned gzip timestamp.
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


class ReproducibleBuildError(RuntimeError):
    """Raised when a distribution build cannot prove byte reproducibility."""


@dataclass(frozen=True)
class _CanonicalMember:
    name: str
    is_directory: bool
    executable: bool
    content: bytes


def _validate_source_date_epoch(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_SOURCE_DATE_EPOCH
        or value > MAX_SOURCE_DATE_EPOCH
    ):
        raise ReproducibleBuildError("source commit timestamp is outside build bounds")
    return value


def _safe_archive_path(name: str) -> PurePosixPath:
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
        raise ReproducibleBuildError("distribution archive contains an unsafe path")
    return path


def _read_canonical_members(source: Path) -> list[_CanonicalMember]:
    try:
        archive = tarfile.open(source, "r:gz")
    except (OSError, tarfile.TarError) as error:
        raise ReproducibleBuildError("source distribution is not a valid tar archive") from error

    canonical: list[_CanonicalMember] = []
    names: set[str] = set()
    roots: set[str] = set()
    total_bytes = 0
    root_directory_found = False
    try:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ReproducibleBuildError("source distribution member count is invalid")
        for member in members:
            path = _safe_archive_path(member.name)
            if member.name in names:
                raise ReproducibleBuildError("source distribution contains duplicate paths")
            names.add(member.name)
            roots.add(path.parts[0])
            if len(path.parts) == 1 and member.isdir():
                root_directory_found = True
            if not member.isdir() and not member.isfile():
                raise ReproducibleBuildError(
                    "source distribution contains an unsupported member type"
                )
            if member.isdir():
                content = b""
            else:
                total_bytes += member.size
                if member.size < 0 or total_bytes > MAX_ARCHIVE_BYTES:
                    raise ReproducibleBuildError(
                        "source distribution exceeds canonicalization bounds"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReproducibleBuildError(
                        "source distribution file content is unavailable"
                    )
                content = extracted.read(MAX_ARCHIVE_BYTES + 1)
                if len(content) != member.size:
                    raise ReproducibleBuildError(
                        "source distribution file size is inconsistent"
                    )
            canonical.append(
                _CanonicalMember(
                    name=member.name,
                    is_directory=member.isdir(),
                    executable=bool(member.mode & 0o111),
                    content=content,
                )
            )
    except (OSError, tarfile.TarError) as error:
        raise ReproducibleBuildError("source distribution cannot be read safely") from error
    finally:
        archive.close()

    if len(roots) != 1 or not root_directory_found:
        raise ReproducibleBuildError("source distribution root is invalid")
    return sorted(canonical, key=lambda item: item.name)


def canonicalize_sdist(
    source: Path,
    destination: Path,
    *,
    source_date_epoch: int,
) -> None:
    """Rewrite a setuptools sdist with stable safe metadata and ordering."""

    epoch = _validate_source_date_epoch(source_date_epoch)
    members = _read_canonical_members(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as output:
                    for item in members:
                        member = tarfile.TarInfo(item.name)
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = epoch
                        member.pax_headers = {}
                        if item.is_directory:
                            member.type = tarfile.DIRTYPE
                            member.mode = 0o755
                            output.addfile(member)
                        else:
                            member.type = tarfile.REGTYPE
                            member.mode = 0o755 if item.executable else 0o644
                            member.size = len(item.content)
                            output.addfile(member, io.BytesIO(item.content))
    except FileExistsError as error:
        raise ReproducibleBuildError("canonical distribution output already exists") from error
    except OSError as error:
        raise ReproducibleBuildError("canonical distribution cannot be written") from error


def validate_build_toolchain(
    requirements: list[str] | tuple[str, ...],
    *,
    build_frontend_version: str,
) -> tuple[str, ...]:
    normalized = tuple(requirements)
    if normalized != EXPECTED_BUILD_REQUIREMENTS:
        raise ReproducibleBuildError("build backend requirements are not exact reviewed pins")
    if build_frontend_version != EXPECTED_BUILD_FRONTEND:
        raise ReproducibleBuildError("build frontend version is not the reviewed pin")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReproducibleBuildError("distribution artifact cannot be read") from error
    return digest.hexdigest()


def render_manifest(
    *,
    source_sha: str,
    source_date_epoch: int,
    build_requirements: tuple[str, ...],
    artifacts: dict[str, tuple[str, int]],
) -> dict[str, Any]:
    if not COMMIT_SHA.fullmatch(source_sha):
        raise ReproducibleBuildError("source revision is not a full lowercase commit SHA")
    epoch = _validate_source_date_epoch(source_date_epoch)
    requirements = validate_build_toolchain(
        build_requirements,
        build_frontend_version=EXPECTED_BUILD_FRONTEND,
    )
    if len(artifacts) != 2:
        raise ReproducibleBuildError("distribution artifact set is incomplete")
    rendered: list[dict[str, Any]] = []
    for filename in sorted(artifacts):
        digest, size = artifacts[filename]
        if (
            Path(filename).name != filename
            or not filename.startswith("hormuz-")
            or not (filename.endswith(".whl") or filename.endswith(".tar.gz"))
            or not SHA256.fullmatch(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
        ):
            raise ReproducibleBuildError("distribution artifact metadata is invalid")
        rendered.append(
            {
                "filename": filename,
                "sha256": digest,
                "size_bytes": size,
            }
        )
    if sum(item["filename"].endswith(".whl") for item in rendered) != 1:
        raise ReproducibleBuildError("distribution wheel set is invalid")
    if sum(item["filename"].endswith(".tar.gz") for item in rendered) != 1:
        raise ReproducibleBuildError("source distribution set is invalid")
    return {
        "schema": "hormuz.reproducible-distributions.v1",
        "source_sha": source_sha,
        "source_date_epoch": epoch,
        "build_frontend": f"build=={EXPECTED_BUILD_FRONTEND}",
        "build_requirements": list(requirements),
        "independent_builds": 2,
        "artifacts": rendered,
    }


def _run_git(
    arguments: list[str],
    *,
    project_root: Path,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReproducibleBuildError("git source export failed") from error


def _commit_epoch(project_root: Path, source_sha: str) -> int:
    if not COMMIT_SHA.fullmatch(source_sha):
        raise ReproducibleBuildError("source revision is not a full lowercase commit SHA")
    resolved = _run_git(
        ["rev-parse", "--verify", f"{source_sha}^{{commit}}"],
        project_root=project_root,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != source_sha:
        raise ReproducibleBuildError("source revision is not available as a commit")
    timestamp = _run_git(
        ["show", "-s", "--format=%ct", source_sha],
        project_root=project_root,
    )
    try:
        epoch = int(timestamp.stdout.strip())
    except ValueError as error:
        raise ReproducibleBuildError("source commit timestamp is unavailable") from error
    if timestamp.returncode != 0:
        raise ReproducibleBuildError("source commit timestamp is unavailable")
    return _validate_source_date_epoch(epoch)


def _read_build_requirements(project_root: Path) -> tuple[str, ...]:
    try:
        project = tomllib.loads((project_root / "pyproject.toml").read_text())
        requirements = project["build-system"]["requires"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError) as error:
        raise ReproducibleBuildError("project build requirements cannot be read") from error
    if not isinstance(requirements, list) or not all(
        isinstance(item, str) for item in requirements
    ):
        raise ReproducibleBuildError("project build requirements are invalid")
    try:
        frontend = importlib.metadata.version("build")
    except importlib.metadata.PackageNotFoundError as error:
        raise ReproducibleBuildError("reviewed build frontend is not installed") from error
    return validate_build_toolchain(
        requirements,
        build_frontend_version=frontend,
    )


def _export_source(
    project_root: Path,
    source_sha: str,
    archive_path: Path,
) -> None:
    exported = _run_git(
        ["archive", "--format=tar", f"--output={archive_path}", source_sha],
        project_root=project_root,
        timeout=60,
    )
    if exported.returncode != 0 or not archive_path.is_file():
        raise ReproducibleBuildError("exact source export failed")


def _extract_source(archive_path: Path, destination: Path) -> None:
    destination.mkdir()
    total_bytes = 0
    try:
        with tarfile.open(archive_path, "r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReproducibleBuildError("source export member count is invalid")
            for member in members:
                path = _safe_archive_path(member.name)
                if not member.isdir() and not member.isfile():
                    raise ReproducibleBuildError(
                        "source export contains an unsupported member type"
                    )
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReproducibleBuildError("source export file content is unavailable")
                total_bytes += member.size
                if member.size < 0 or total_bytes > MAX_ARCHIVE_BYTES:
                    raise ReproducibleBuildError("source export exceeds extraction bounds")
                content = extracted.read(MAX_ARCHIVE_BYTES + 1)
                if len(content) != member.size or len(content) > MAX_ARCHIVE_BYTES:
                    raise ReproducibleBuildError("source export file size is invalid")
                target.write_bytes(content)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, tarfile.TarError) as error:
        raise ReproducibleBuildError("exact source export cannot be extracted") from error


def _run_build(
    *,
    python_executable: str,
    source_root: Path,
    output_dir: Path,
    source_date_epoch: int,
) -> None:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    try:
        built = subprocess.run(
            [python_executable, "-m", "build", "--outdir", str(output_dir)],
            cwd=source_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReproducibleBuildError("distribution build process failed") from error
    if built.returncode != 0:
        raise ReproducibleBuildError("distribution build frontend returned failure")


def _artifact_paths(output_dir: Path) -> dict[str, Path]:
    try:
        paths = [path for path in output_dir.iterdir() if path.is_file()]
    except OSError as error:
        raise ReproducibleBuildError("distribution output cannot be inspected") from error
    wheels = [path for path in paths if path.name.endswith(".whl")]
    sdists = [path for path in paths if path.name.endswith(".tar.gz")]
    if len(paths) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ReproducibleBuildError("distribution build did not produce one wheel and one sdist")
    return {path.name: path for path in paths}


def _canonicalize_built_sdist(artifacts: dict[str, Path], epoch: int) -> None:
    sdist = next(path for name, path in artifacts.items() if name.endswith(".tar.gz"))
    canonical = sdist.with_name(f".{sdist.name}.canonical")
    canonicalize_sdist(sdist, canonical, source_date_epoch=epoch)
    try:
        canonical.replace(sdist)
    except OSError as error:
        raise ReproducibleBuildError("canonical source distribution cannot be selected") from error


def build_reproducible_distributions(
    *,
    project_root: Path,
    source_sha: str,
    output_dir: Path,
    python_executable: str,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    requirements = _read_build_requirements(project_root)
    epoch = _commit_epoch(project_root, source_sha)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ReproducibleBuildError("distribution output directory is not empty")

    with tempfile.TemporaryDirectory(
        prefix=".hormuz-reproducible-build-",
        dir=output_dir.parent,
    ) as temporary:
        root = Path(temporary)
        source_archive = root / "source.tar"
        _export_source(project_root, source_sha, source_archive)
        builds: list[dict[str, Path]] = []
        for label in ("a", "b"):
            source_root = root / f"source-{label}"
            build_output = root / f"build-{label}"
            build_output.mkdir()
            _extract_source(source_archive, source_root)
            _run_build(
                python_executable=python_executable,
                source_root=source_root,
                output_dir=build_output,
                source_date_epoch=epoch,
            )
            artifacts = _artifact_paths(build_output)
            _canonicalize_built_sdist(artifacts, epoch)
            builds.append(artifacts)

        if set(builds[0]) != set(builds[1]):
            raise ReproducibleBuildError("independent builds produced different filenames")
        first_metadata: dict[str, tuple[str, int]] = {}
        for filename in sorted(builds[0]):
            first = builds[0][filename]
            second = builds[1][filename]
            first_digest = _sha256(first)
            if first_digest != _sha256(second):
                raise ReproducibleBuildError(
                    "independent builds produced different artifact bytes"
                )
            first_metadata[filename] = (first_digest, first.stat().st_size)

        manifest = render_manifest(
            source_sha=source_sha,
            source_date_epoch=epoch,
            build_requirements=requirements,
            artifacts=first_metadata,
        )
        publish = root / "publish"
        publish.mkdir()
        for filename, source in builds[0].items():
            shutil.copyfile(source, publish / filename)
        (publish / "hormuz-distribution-reproducibility.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        if output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError as error:
                raise ReproducibleBuildError(
                    "distribution output directory changed during build"
                ) from error
        try:
            publish.replace(output_dir)
        except OSError as error:
            raise ReproducibleBuildError("distribution artifacts cannot be published") from error
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        manifest = build_reproducible_distributions(
            project_root=arguments.project_root,
            source_sha=arguments.source_sha,
            output_dir=arguments.outdir,
            python_executable=arguments.python,
        )
    except ReproducibleBuildError as error:
        print(f"reproducible build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
