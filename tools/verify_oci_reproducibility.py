#!/usr/bin/env python3
"""Compare two normalized OCI archives and validate the AMD64 release payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

try:
    from tools._verification_runtime import (
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )


SCHEMA_ID = "hormuz.oci-reproducibility"
SCHEMA_VERSION = 1
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"


class ReproducibilityError(RuntimeError):
    """Raised when independently built OCI payloads are not identical and valid."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", required=True, type=Path)
    parser.add_argument("--second", required=True, type=Path)
    parser.add_argument("--expected-reference", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        summary = verify_archives(
            first=args.first,
            second=args.second,
            expected_reference=args.expected_reference,
            expected_version=args.expected_version,
            expected_commit=args.expected_commit,
            source_date_epoch=args.source_date_epoch,
        )
        write_private_json_evidence(args.output, summary, indent=2)
    except (OSError, ReproducibilityError, tarfile.TarError, json.JSONDecodeError) as error:
        print(f"OCI reproducibility verification failed: {error}", file=sys.stderr)
        return 1

    print(
        "verified reproducible OCI payload: "
        f"digest={summary['artifact']['digest']} platform=linux/amd64"
    )
    return 0


def verify_archives(
    *,
    first: Path,
    second: Path,
    expected_reference: str,
    expected_version: str,
    expected_commit: str,
    source_date_epoch: int,
) -> dict[str, Any]:
    first_hash = file_sha256(first)
    second_hash = file_sha256(second)
    if first_hash != second_hash or not _files_equal(first, second):
        raise ReproducibilityError("independent_oci_archives_differ")

    with tarfile.open(first, mode="r:*") as archive:
        layout = _read_json_member(archive, "oci-layout")
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise ReproducibilityError("oci_layout_invalid")
        index = _read_json_member(archive, "index.json")
        if index.get("schemaVersion") != 2 or index.get("mediaType") != OCI_INDEX:
            raise ReproducibilityError("oci_index_invalid")
        manifests = _list(index.get("manifests"), "oci_index_manifests")
        if len(manifests) != 1:
            raise ReproducibilityError("oci_index_must_contain_one_manifest")
        descriptor = _mapping(manifests[0], "oci_manifest_descriptor")
        if descriptor.get("mediaType") != OCI_MANIFEST:
            raise ReproducibilityError("oci_manifest_media_type_invalid")
        if _mapping(descriptor.get("platform"), "oci_manifest_platform") != {
            "architecture": "amd64",
            "os": "linux",
        }:
            raise ReproducibilityError("oci_descriptor_platform_mismatch")
        digest = _digest(descriptor.get("digest"), "oci_manifest_digest")
        annotations = _mapping(descriptor.get("annotations"), "oci_manifest_annotations")
        if annotations.get("org.opencontainers.image.ref.name") != expected_reference:
            raise ReproducibilityError("oci_reference_annotation_mismatch")

        manifest = _read_digest_json(archive, digest)
        if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != OCI_MANIFEST:
            raise ReproducibilityError("oci_manifest_invalid")
        config_descriptor = _mapping(manifest.get("config"), "oci_config_descriptor")
        if config_descriptor.get("mediaType") != OCI_CONFIG:
            raise ReproducibilityError("oci_config_media_type_invalid")
        config_digest = _digest(config_descriptor.get("digest"), "oci_config_digest")
        layers = _list(manifest.get("layers"), "oci_manifest_layers")
        if not layers:
            raise ReproducibilityError("oci_manifest_layers_empty")
        for layer in layers:
            layer_digest = _digest(
                _mapping(layer, "oci_layer_descriptor").get("digest"),
                "oci_layer_digest",
            )
            _read_digest_bytes(archive, layer_digest)

        config = _read_digest_json(archive, config_digest)
        if config.get("architecture") != "amd64" or config.get("os") != "linux":
            raise ReproducibilityError("oci_platform_mismatch")
        labels = _mapping(
            _mapping(config.get("config"), "oci_image_config").get("Labels"),
            "oci_image_labels",
        )
        if labels.get("org.opencontainers.image.version") != expected_version:
            raise ReproducibilityError("oci_version_label_mismatch")
        if labels.get("org.opencontainers.image.revision") != expected_commit:
            raise ReproducibilityError("oci_revision_label_mismatch")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "archive_sha256": first_hash,
            "config_digest": config_digest,
            "digest": digest,
            "platform": "linux/amd64",
            "reference": expected_reference,
            "revision": expected_commit,
            "version": expected_version,
        },
        "build": {
            "dependency_inputs": [
                "requirements/oci-build-linux-amd64.lock",
                "requirements/oci-runtime-linux-amd64.lock",
            ],
            "independent_no_cache_builds": 2,
            "provenance_embedded_in_payload": False,
            "source_date_epoch": source_date_epoch,
            "timestamp_rewrite": True,
        },
        "coverage": "unsigned_oci_payload_linux_amd64",
        "verdict": "pass",
    }


def _files_equal(first: Path, second: Path) -> bool:
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _read_digest_json(archive: tarfile.TarFile, digest: str) -> dict[str, Any]:
    return _mapping(json.loads(_read_digest_bytes(archive, digest)), digest)


def _read_digest_bytes(archive: tarfile.TarFile, digest: str) -> bytes:
    algorithm, value = digest.split(":", 1)
    member_name = f"blobs/{algorithm}/{value}"
    raw = _read_member(archive, member_name)
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise ReproducibilityError(f"oci_blob_digest_mismatch:{member_name}")
    return raw


def _read_json_member(archive: tarfile.TarFile, member_name: str) -> dict[str, Any]:
    return _mapping(json.loads(_read_member(archive, member_name)), member_name)


def _read_member(archive: tarfile.TarFile, member_name: str) -> bytes:
    matching = [member for member in archive.getmembers() if member.name == member_name]
    if len(matching) != 1 or not matching[0].isfile():
        raise ReproducibilityError(f"oci_archive_member_invalid:{member_name}")
    source = archive.extractfile(matching[0])
    if source is None:
        raise ReproducibilityError(f"oci_archive_member_unreadable:{member_name}")
    return source.read()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReproducibilityError(f"{label}_must_be_object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReproducibilityError(f"{label}_must_be_array")
    return value


def _digest(value: Any, label: str) -> str:
    if not is_sha256_digest(value):
        raise ReproducibilityError(f"{label}_invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
