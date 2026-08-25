#!/usr/bin/env python3
"""Validate public CycloneDX and SLSA v1 payloads before transparency logging."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    from tools._verification_runtime import (
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )
    from tools.create_oci_release_provenance import (
        BASE_DIGEST,
        BASE_IMAGE,
        BUILD_TYPE,
        EXPECTED_IMAGE,
        EXPECTED_REPOSITORY,
        FRONTEND_DIGEST,
        FRONTEND_IMAGE,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )
    from create_oci_release_provenance import (  # type: ignore[no-redef]
        BASE_DIGEST,
        BASE_IMAGE,
        BUILD_TYPE,
        EXPECTED_IMAGE,
        EXPECTED_REPOSITORY,
        FRONTEND_DIGEST,
        FRONTEND_IMAGE,
    )


SCHEMA_ID = "hormuz.oci-public-metadata-validation"
SCHEMA_VERSION = 1
CYCLONEDX_SCHEMA = "http://cyclonedx.org/schema/bom-1.7.schema.json"
WORKFLOW_PATH = ".github/workflows/release-oci.yml"
HEX_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
STRICT_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
UUID_URN = re.compile(
    r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
MAX_IDENTITY_TEXT_LENGTH = 512
MAX_PUBLIC_METADATA_BYTES = 32 * 1024 * 1024
ALLOWED_COMPONENT_KEYS = {
    "bom-ref",
    "hashes",
    "licenses",
    "name",
    "properties",
    "purl",
    "supplier",
    "type",
    "version",
}
ALLOWED_COMPONENT_PROPERTIES = {
    "aquasecurity:trivy:Class",
    "aquasecurity:trivy:FilePath",
    "aquasecurity:trivy:LayerDiffID",
    "aquasecurity:trivy:LayerDigest",
    "aquasecurity:trivy:PkgID",
    "aquasecurity:trivy:PkgType",
    "aquasecurity:trivy:SrcEpoch",
    "aquasecurity:trivy:SrcName",
    "aquasecurity:trivy:SrcRelease",
    "aquasecurity:trivy:SrcVersion",
    "aquasecurity:trivy:Type",
}
ALLOWED_ROOT_PROPERTIES = {
    "aquasecurity:trivy:DiffID",
    "aquasecurity:trivy:ImageID",
    "aquasecurity:trivy:Labels:org.opencontainers.image.description",
    "aquasecurity:trivy:Labels:org.opencontainers.image.licenses",
    "aquasecurity:trivy:Labels:org.opencontainers.image.revision",
    "aquasecurity:trivy:Labels:org.opencontainers.image.source",
    "aquasecurity:trivy:Labels:org.opencontainers.image.title",
    "aquasecurity:trivy:Labels:org.opencontainers.image.vendor",
    "aquasecurity:trivy:Labels:org.opencontainers.image.version",
    "aquasecurity:trivy:Reference",
    "aquasecurity:trivy:RepoDigest",
    "aquasecurity:trivy:RepoTag",
    "aquasecurity:trivy:SchemaVersion",
    "aquasecurity:trivy:Size",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|secret|access[_-]?token|api[_-]?key)\s*[:=]\s*[^\s,;]{6,}"),
)
FORBIDDEN_DISCLOSURES = (
    "/Users/",
    "/home/runner/",
    "/private/tmp/",
    "C:\\Users\\",
    ".env",
    "hormuz.json",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "HORMUZ_INGRESS_CREDENTIAL",
)


class PublicMetadataError(RuntimeError):
    """Raised when release metadata is unsafe, ambiguous, or outside its schema."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--reproducibility", required=True, type=Path)
    parser.add_argument("--supply-chain", required=True, type=Path)
    parser.add_argument("--vulnerabilities", required=True, type=Path)
    parser.add_argument("--build-lock", required=True, type=Path)
    parser.add_argument("--runtime-lock", required=True, type=Path)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--invocation-uri", required=True)
    parser.add_argument("--repository-visibility", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        sbom = _load_json(args.sbom)
        provenance = _load_json(args.provenance)
        summary = validate_public_metadata(
            sbom=sbom,
            provenance=provenance,
            image_reference=args.image_reference,
            image_digest=args.image_digest,
            release_tag=args.release_tag,
            commit=args.commit,
            invocation_uri=args.invocation_uri,
            repository_visibility=args.repository_visibility,
            sbom_sha256=file_sha256(args.sbom),
            provenance_sha256=file_sha256(args.provenance),
            byproduct_sha256={
                "preflight": file_sha256(args.preflight),
                "reproducibility": file_sha256(args.reproducibility),
                "supply-chain-summary": file_sha256(args.supply_chain),
                "cyclonedx-sbom": file_sha256(args.sbom),
                "vulnerability-report": file_sha256(args.vulnerabilities),
            },
            dependency_sha256={
                "build-lock": file_sha256(args.build_lock),
                "runtime-lock": file_sha256(args.runtime_lock),
            },
        )
        write_private_json_evidence(args.output, summary, indent=2)
    except (
        OSError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        PublicMetadataError,
    ) as error:
        print(f"public OCI metadata validation failed: {error}", file=sys.stderr)
        return 1

    print(
        "validated public OCI metadata: "
        f"components={summary['sbom']['component_count']} "
        f"digest={summary['artifact']['digest']}"
    )
    return 0


def validate_public_metadata(
    *,
    sbom: dict[str, Any],
    provenance: dict[str, Any],
    image_reference: str,
    image_digest: str,
    release_tag: str,
    commit: str,
    invocation_uri: str,
    repository_visibility: str,
    sbom_sha256: str,
    provenance_sha256: str,
    byproduct_sha256: dict[str, str],
    dependency_sha256: dict[str, str],
) -> dict[str, Any]:
    if repository_visibility != "public":
        raise PublicMetadataError("public_metadata_repository_not_public")
    if image_reference != EXPECTED_IMAGE or not is_sha256_digest(image_digest):
        raise PublicMetadataError("public_metadata_subject_invalid")
    if STRICT_TAG.fullmatch(release_tag) is None or HEX_COMMIT.fullmatch(commit) is None:
        raise PublicMetadataError("public_metadata_release_identity_invalid")
    expected_invocation = (
        f"https://github.com/{EXPECTED_REPOSITORY}/actions/runs/"
        r"[1-9][0-9]*/attempts/[1-9][0-9]*"
    )
    if re.fullmatch(expected_invocation, invocation_uri) is None:
        raise PublicMetadataError("public_metadata_invocation_invalid")
    if not is_sha256_digest(sbom_sha256) or not is_sha256_digest(provenance_sha256):
        raise PublicMetadataError("public_metadata_evidence_hash_invalid")
    if set(byproduct_sha256) != {
        "preflight",
        "reproducibility",
        "supply-chain-summary",
        "cyclonedx-sbom",
        "vulnerability-report",
    } or not all(is_sha256_digest(item) for item in byproduct_sha256.values()):
        raise PublicMetadataError("public_metadata_byproduct_hashes_invalid")
    if set(dependency_sha256) != {"build-lock", "runtime-lock"} or not all(
        is_sha256_digest(item) for item in dependency_sha256.values()
    ):
        raise PublicMetadataError("public_metadata_dependency_hashes_invalid")

    provenance_context = _validate_provenance(
        provenance,
        image_reference=image_reference,
        image_digest=image_digest,
        release_tag=release_tag,
        commit=commit,
        invocation_uri=invocation_uri,
        byproduct_sha256=byproduct_sha256,
        dependency_sha256=dependency_sha256,
    )
    component_count = _validate_sbom(
        sbom,
        image_reference=image_reference,
        image_digest=image_digest,
        version=provenance_context["version"],
        commit=provenance_context["commit"],
    )
    _scan_public_strings(sbom, "sbom")
    _scan_public_strings(provenance, "provenance")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "digest": image_digest,
            "image_reference": image_reference,
            "platform": "linux/amd64",
            "repository_visibility": repository_visibility,
        },
        "provenance": {
            "predicate_type": "https://slsa.dev/provenance/v1",
            "sha256": provenance_sha256,
            "strict_schema": True,
        },
        "release": {
            "commit": commit,
            "tag": release_tag,
        },
        "sbom": {
            "component_count": component_count,
            "format": "CycloneDX",
            "sha256": sbom_sha256,
            "spec_version": "1.7",
            "strict_schema": True,
        },
        "secret_leak_validation": {
            "forbidden_pattern_matches": 0,
            "unexpected_fields": 0,
            "verdict": "pass",
        },
        "verdict": "pass",
    }


def _validate_provenance(
    value: dict[str, Any],
    *,
    image_reference: str,
    image_digest: str,
    release_tag: str,
    commit: str,
    invocation_uri: str,
    byproduct_sha256: dict[str, str],
    dependency_sha256: dict[str, str],
) -> dict[str, str]:
    _keys(value, {"buildDefinition", "runDetails"}, label="provenance")
    definition = _object(value["buildDefinition"], "provenance_build_definition")
    _keys(
        definition,
        {"buildType", "externalParameters", "internalParameters", "resolvedDependencies"},
        label="provenance_build_definition",
    )
    if definition["buildType"] != BUILD_TYPE:
        raise PublicMetadataError("provenance_build_type_invalid")
    internal = _object(definition["internalParameters"], "provenance_internal_parameters")
    if internal:
        raise PublicMetadataError("provenance_internal_parameters_not_empty")

    external = _object(definition["externalParameters"], "provenance_external_parameters")
    _keys(
        external,
        {
            "artifactContract",
            "firstPublicationRegistry",
            "platform",
            "ref",
            "repository",
            "tag",
            "version",
        },
        label="provenance_external_parameters",
    )
    tag = _string(external["tag"], "provenance_tag")
    version = _string(external["version"], "provenance_version")
    ref = _string(external["ref"], "provenance_ref")
    if (
        STRICT_TAG.fullmatch(tag) is None
        or tag != release_tag
        or tag != f"v{version}"
        or ref != f"refs/tags/{tag}"
    ):
        raise PublicMetadataError("provenance_release_identity_invalid")
    if external != {
        "artifactContract": "signed_oci_digest",
        "firstPublicationRegistry": image_reference,
        "platform": "linux/amd64",
        "ref": ref,
        "repository": EXPECTED_REPOSITORY,
        "tag": tag,
        "version": version,
    }:
        raise PublicMetadataError("provenance_external_parameters_invalid")

    dependencies = _array(definition["resolvedDependencies"], "provenance_dependencies")
    if len(dependencies) != 5:
        raise PublicMetadataError("provenance_dependency_count_invalid")
    normalized: dict[str, dict[str, str]] = {}
    for index, dependency_value in enumerate(dependencies):
        dependency = _object(dependency_value, f"provenance_dependency_{index}")
        _keys(dependency, {"digest", "uri"}, label=f"provenance_dependency_{index}")
        uri = _string(dependency["uri"], f"provenance_dependency_uri_{index}")
        digest = _object(dependency["digest"], f"provenance_dependency_digest_{index}")
        if uri in normalized:
            raise PublicMetadataError("provenance_dependency_duplicate")
        normalized[uri] = {str(key): _string(item, "provenance_dependency_hash") for key, item in digest.items()}

    source_uri = f"git+https://github.com/{EXPECTED_REPOSITORY}@{ref}"
    build_lock_uri = f"git+https://github.com/{EXPECTED_REPOSITORY}#requirements/oci-build-linux-amd64.lock"
    runtime_lock_uri = f"git+https://github.com/{EXPECTED_REPOSITORY}#requirements/oci-runtime-linux-amd64.lock"
    expected_uris = {source_uri, BASE_IMAGE, FRONTEND_IMAGE, build_lock_uri, runtime_lock_uri}
    if set(normalized) != expected_uris:
        raise PublicMetadataError("provenance_dependency_set_invalid")
    source_digest = normalized[source_uri]
    if set(source_digest) != {"gitCommit"} or HEX_COMMIT.fullmatch(source_digest["gitCommit"]) is None:
        raise PublicMetadataError("provenance_source_commit_invalid")
    provenance_commit = source_digest["gitCommit"]
    if provenance_commit != commit:
        raise PublicMetadataError("provenance_source_commit_mismatch")
    _exact_sha_dependency(normalized[BASE_IMAGE], BASE_DIGEST, "provenance_base_digest")
    _exact_sha_dependency(normalized[FRONTEND_IMAGE], FRONTEND_DIGEST, "provenance_frontend_digest")
    _exact_sha_dependency(
        normalized[build_lock_uri],
        dependency_sha256["build-lock"],
        "provenance_build_lock_digest",
    )
    _exact_sha_dependency(
        normalized[runtime_lock_uri],
        dependency_sha256["runtime-lock"],
        "provenance_runtime_lock_digest",
    )

    details = _object(value["runDetails"], "provenance_run_details")
    _keys(details, {"builder", "byproducts", "metadata"}, label="provenance_run_details")
    builder = _object(details["builder"], "provenance_builder")
    _keys(builder, {"id"}, label="provenance_builder")
    expected_identity = (
        f"https://github.com/{EXPECTED_REPOSITORY}/{WORKFLOW_PATH}@{ref}"
    )
    if builder["id"] != expected_identity:
        raise PublicMetadataError("provenance_builder_identity_invalid")

    byproducts = _array(details["byproducts"], "provenance_byproducts")
    expected_names = {
        "preflight",
        "reproducibility",
        "supply-chain-summary",
        "cyclonedx-sbom",
        "vulnerability-report",
    }
    actual_names: set[str] = set()
    for index, byproduct_value in enumerate(byproducts):
        byproduct = _object(byproduct_value, f"provenance_byproduct_{index}")
        _keys(byproduct, {"digest", "name"}, label=f"provenance_byproduct_{index}")
        name = _string(byproduct["name"], "provenance_byproduct_name")
        if name in actual_names:
            raise PublicMetadataError("provenance_byproduct_duplicate")
        actual_names.add(name)
        _exact_sha_dependency(
            _object(byproduct["digest"], "provenance_byproduct_digest"),
            byproduct_sha256.get(name, ""),
            "provenance_byproduct_digest",
        )
    if actual_names != expected_names:
        raise PublicMetadataError("provenance_byproduct_set_invalid")

    metadata = _object(details["metadata"], "provenance_metadata")
    _keys(metadata, {"invocationId"}, label="provenance_metadata")
    invocation = _string(metadata["invocationId"], "provenance_invocation")
    if invocation != invocation_uri:
        raise PublicMetadataError("provenance_invocation_invalid")

    if not is_sha256_digest(image_digest) or image_reference != EXPECTED_IMAGE:
        raise PublicMetadataError("provenance_subject_invalid")
    return {"commit": provenance_commit, "tag": tag, "version": version}


def _validate_sbom(
    value: dict[str, Any],
    *,
    image_reference: str,
    image_digest: str,
    version: str,
    commit: str,
) -> int:
    _keys(
        value,
        {
            "$schema",
            "bomFormat",
            "components",
            "dependencies",
            "metadata",
            "serialNumber",
            "specVersion",
            "version",
            "vulnerabilities",
        },
        label="sbom",
    )
    if value["$schema"] != CYCLONEDX_SCHEMA or value["bomFormat"] != "CycloneDX":
        raise PublicMetadataError("sbom_schema_invalid")
    if value["specVersion"] != "1.7" or value["version"] != 1:
        raise PublicMetadataError("sbom_version_invalid")
    if UUID_URN.fullmatch(_string(value["serialNumber"], "sbom_serial")) is None:
        raise PublicMetadataError("sbom_serial_invalid")
    if _array(value["vulnerabilities"], "sbom_vulnerabilities"):
        raise PublicMetadataError("sbom_embedded_vulnerabilities_not_empty")

    metadata = _object(value["metadata"], "sbom_metadata")
    _keys(metadata, {"component", "timestamp", "tools"}, label="sbom_metadata")
    _timestamp(metadata["timestamp"])
    tools = _object(metadata["tools"], "sbom_tools")
    _keys(tools, {"components"}, label="sbom_tools")
    tool_components = _array(tools["components"], "sbom_tool_components")
    if len(tool_components) != 1:
        raise PublicMetadataError("sbom_tool_count_invalid")
    tool = _object(tool_components[0], "sbom_tool")
    _keys(tool, {"group", "manufacturer", "name", "type", "version"}, label="sbom_tool")
    manufacturer = _object(tool["manufacturer"], "sbom_tool_manufacturer")
    _keys(manufacturer, {"name"}, label="sbom_tool_manufacturer")
    if tool != {
        "type": "application",
        "manufacturer": {"name": "Aqua Security Software Ltd."},
        "group": "aquasecurity",
        "name": "trivy",
        "version": "0.74.0",
    }:
        raise PublicMetadataError("sbom_tool_identity_invalid")

    root = _object(metadata["component"], "sbom_root_component")
    _keys(root, {"bom-ref", "name", "properties", "purl", "type"}, label="sbom_root_component")
    subject = f"{image_reference}@{image_digest}"
    if root["type"] != "container" or root["name"] != subject:
        raise PublicMetadataError("sbom_root_subject_invalid")
    root_properties = _properties(root["properties"], ALLOWED_ROOT_PROPERTIES, "sbom_root")
    required_root_properties = {
        "aquasecurity:trivy:ImageID",
        "aquasecurity:trivy:Labels:org.opencontainers.image.description",
        "aquasecurity:trivy:Labels:org.opencontainers.image.licenses",
        "aquasecurity:trivy:Labels:org.opencontainers.image.revision",
        "aquasecurity:trivy:Labels:org.opencontainers.image.source",
        "aquasecurity:trivy:Labels:org.opencontainers.image.title",
        "aquasecurity:trivy:Labels:org.opencontainers.image.vendor",
        "aquasecurity:trivy:Labels:org.opencontainers.image.version",
        "aquasecurity:trivy:Reference",
        "aquasecurity:trivy:RepoDigest",
        "aquasecurity:trivy:SchemaVersion",
        "aquasecurity:trivy:Size",
    }
    if not required_root_properties.issubset(root_properties):
        raise PublicMetadataError("sbom_root_properties_incomplete")
    _require_single_property(root_properties, "aquasecurity:trivy:Labels:org.opencontainers.image.revision", commit)
    _require_single_property(
        root_properties,
        "aquasecurity:trivy:Labels:org.opencontainers.image.description",
        "Non-root reference runtime for the Hormuz enterprise AI policy gateway",
    )
    _require_single_property(
        root_properties,
        "aquasecurity:trivy:Labels:org.opencontainers.image.licenses",
        "Apache-2.0",
    )
    _require_single_property(root_properties, "aquasecurity:trivy:Labels:org.opencontainers.image.source", f"https://github.com/{EXPECTED_REPOSITORY}")
    _require_single_property(root_properties, "aquasecurity:trivy:Labels:org.opencontainers.image.title", "Hormuz")
    _require_single_property(
        root_properties,
        "aquasecurity:trivy:Labels:org.opencontainers.image.vendor",
        "NeuralInt",
    )
    _require_single_property(root_properties, "aquasecurity:trivy:Labels:org.opencontainers.image.version", version)
    _require_single_property(root_properties, "aquasecurity:trivy:Reference", subject)
    _require_single_property(root_properties, "aquasecurity:trivy:RepoDigest", subject)
    _require_single_property(root_properties, "aquasecurity:trivy:SchemaVersion", "2")
    if (
        len(root_properties["aquasecurity:trivy:ImageID"]) != 1
        or not is_sha256_digest(root_properties["aquasecurity:trivy:ImageID"][0])
    ):
        raise PublicMetadataError("sbom_image_id_invalid")
    image_id = root_properties["aquasecurity:trivy:ImageID"][0]
    for label in ("bom-ref", "purl"):
        item = _string(root[label], f"sbom_root_{label}")
        try:
            parsed = urlsplit(item)
            qualifiers = parse_qs(parsed.query, strict_parsing=True)
        except ValueError as error:
            raise PublicMetadataError("sbom_root_purl_invalid") from error
        if (
            parsed.scheme != "pkg"
            or parsed.netloc
            # Trivy derives the OCI PURL version from RepoDigest, which is the
            # registry manifest digest. ImageID is a separate daemon-local
            # identity and can differ on a pulled registry image.
            or parsed.path != f"oci/hormuz@{image_digest}"
            or parsed.fragment
            or qualifiers
            != {
                "arch": ["amd64"],
                "repository_url": [image_reference],
            }
        ):
            raise PublicMetadataError("sbom_root_purl_invalid")
    root_ref = _string(root["bom-ref"], "sbom_root_ref")
    if root_ref != root["purl"]:
        raise PublicMetadataError("sbom_root_reference_mismatch")
    size_values = root_properties["aquasecurity:trivy:Size"]
    if len(size_values) != 1 or re.fullmatch(r"[1-9][0-9]*", size_values[0]) is None:
        raise PublicMetadataError("sbom_image_size_invalid")
    repository_tags = root_properties.get("aquasecurity:trivy:RepoTag", [])
    allowed_repository_tags = {
        f"{image_reference}:sha-{commit}",
        f"{image_reference}:v{version}",
    }
    if (
        len(repository_tags) != len(set(repository_tags))
        or any(item not in allowed_repository_tags for item in repository_tags)
    ):
        raise PublicMetadataError("sbom_repository_tag_invalid")
    for item in root_properties.get("aquasecurity:trivy:DiffID", []):
        if not is_sha256_digest(item):
            raise PublicMetadataError("sbom_layer_diff_id_invalid")

    components = _array(value["components"], "sbom_components")
    if not components or len(components) > 10_000:
        raise PublicMetadataError("sbom_component_count_invalid")
    refs = {root_ref}
    hormuz_versions: list[str] = []
    for index, component_value in enumerate(components):
        component = _object(component_value, f"sbom_component_{index}")
        _keys(
            component,
            {"bom-ref", "name", "type", "version"},
            optional=ALLOWED_COMPONENT_KEYS - {"bom-ref", "name", "type", "version"},
            label=f"sbom_component_{index}",
        )
        component_ref = _string(component["bom-ref"], "sbom_component_ref")
        if component_ref in refs:
            raise PublicMetadataError("sbom_component_ref_duplicate")
        refs.add(component_ref)
        name = _string(component["name"], "sbom_component_name")
        component_version = _string(component["version"], "sbom_component_version")
        _safe_name(name, "sbom_component_name")
        _safe_name(component_version, "sbom_component_version")
        if component["type"] not in {"library", "operating-system"}:
            raise PublicMetadataError("sbom_component_type_invalid")
        if name == "hormuz":
            hormuz_versions.append(component_version)
        if "purl" in component and not _string(component["purl"], "sbom_component_purl").startswith("pkg:"):
            raise PublicMetadataError("sbom_component_purl_invalid")
        if "supplier" in component:
            supplier = _object(component["supplier"], "sbom_component_supplier")
            _keys(supplier, {"name"}, label="sbom_component_supplier")
            _safe_name(supplier["name"], "sbom_component_supplier_name")
        if "licenses" in component:
            _licenses(component["licenses"])
        if "hashes" in component:
            _hashes(component["hashes"])
        if "properties" in component:
            properties = _properties(
                component["properties"],
                ALLOWED_COMPONENT_PROPERTIES,
                "sbom_component",
            )
            for path in properties.get("aquasecurity:trivy:FilePath", []):
                if re.fullmatch(
                    r"opt/hormuz/lib/python3\.14/site-packages/[A-Za-z0-9_.-]+\.dist-info/METADATA",
                    path,
                ) is None:
                    raise PublicMetadataError("sbom_component_path_invalid")
            for property_name in ("aquasecurity:trivy:LayerDiffID", "aquasecurity:trivy:LayerDigest"):
                if not all(is_sha256_digest(item) for item in properties.get(property_name, [])):
                    raise PublicMetadataError("sbom_component_layer_digest_invalid")
    if hormuz_versions != [version]:
        raise PublicMetadataError("sbom_hormuz_version_invalid")

    dependencies = _array(value["dependencies"], "sbom_dependencies")
    seen_dependencies: set[str] = set()
    for index, dependency_value in enumerate(dependencies):
        dependency = _object(dependency_value, f"sbom_dependency_{index}")
        _keys(dependency, {"dependsOn", "ref"}, label=f"sbom_dependency_{index}")
        dependency_ref = _string(dependency["ref"], "sbom_dependency_ref")
        if dependency_ref not in refs or dependency_ref in seen_dependencies:
            raise PublicMetadataError("sbom_dependency_ref_invalid")
        seen_dependencies.add(dependency_ref)
        depends_on = _array(dependency["dependsOn"], "sbom_dependency_edges")
        if len(depends_on) != len(set(depends_on)):
            raise PublicMetadataError("sbom_dependency_edge_duplicate")
        if any(not isinstance(item, str) or item not in refs for item in depends_on):
            raise PublicMetadataError("sbom_dependency_edge_invalid")
    if seen_dependencies != refs:
        raise PublicMetadataError("sbom_dependency_coverage_invalid")
    return len(components)


def _properties(value: Any, allowed: set[str], label: str) -> dict[str, list[str]]:
    properties = _array(value, f"{label}_properties")
    result: dict[str, list[str]] = {}
    for item_value in properties:
        item = _object(item_value, f"{label}_property")
        _keys(item, {"name", "value"}, label=f"{label}_property")
        name = _string(item["name"], f"{label}_property_name")
        property_value = _string(item["value"], f"{label}_property_value")
        if name not in allowed:
            raise PublicMetadataError(f"{label}_property_not_allowed")
        result.setdefault(name, []).append(property_value)
    return result


def _require_single_property(properties: dict[str, list[str]], name: str, expected: str) -> None:
    if properties.get(name) != [expected]:
        raise PublicMetadataError("sbom_root_property_mismatch")


def _licenses(value: Any) -> None:
    licenses = _array(value, "sbom_component_licenses")
    if not licenses:
        raise PublicMetadataError("sbom_component_licenses_empty")
    for item_value in licenses:
        item = _object(item_value, "sbom_component_license")
        if set(item) == {"expression"}:
            _safe_name(item["expression"], "sbom_license_expression")
        elif set(item) == {"license"}:
            license_value = _object(item["license"], "sbom_license")
            _keys(license_value, set(), optional={"id", "name"}, label="sbom_license")
            if not license_value or len(license_value) != 1:
                raise PublicMetadataError("sbom_license_identity_invalid")
            _safe_name(next(iter(license_value.values())), "sbom_license_identity")
        else:
            raise PublicMetadataError("sbom_license_schema_invalid")


def _hashes(value: Any) -> None:
    hashes = _array(value, "sbom_component_hashes")
    for item_value in hashes:
        item = _object(item_value, "sbom_component_hash")
        _keys(item, {"alg", "content"}, label="sbom_component_hash")
        algorithm = item["alg"]
        content = _string(item["content"], "sbom_component_hash_content")
        lengths = {"SHA-1": 40, "SHA-256": 64, "SHA-512": 128}
        if algorithm not in lengths or re.fullmatch(rf"[0-9a-f]{{{lengths[algorithm]}}}", content) is None:
            raise PublicMetadataError("sbom_component_hash_invalid")


def _scan_public_strings(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _scan_public_strings(item, label)
        return
    if isinstance(value, list):
        for item in value:
            _scan_public_strings(item, label)
        return
    if not isinstance(value, str):
        return
    if len(value) > 4096 or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        raise PublicMetadataError(f"{label}_string_invalid")
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise PublicMetadataError(f"{label}_secret_pattern_detected")
    if any(marker in value for marker in FORBIDDEN_DISCLOSURES):
        raise PublicMetadataError(f"{label}_private_context_detected")
    if re.search(r"[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", value, re.IGNORECASE):
        raise PublicMetadataError(f"{label}_credential_url_detected")


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PublicMetadataError("public_metadata_duplicate_json_member")
            result[key] = value
        return result

    size = path.stat().st_size
    if size <= 0 or size > MAX_PUBLIC_METADATA_BYTES:
        raise PublicMetadataError("public_metadata_file_size_invalid")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    return _object(value, path.name)


def _keys(
    value: dict[str, Any],
    required: set[str],
    *,
    optional: set[str] | None = None,
    label: str,
) -> None:
    optional = optional or set()
    if not required.issubset(value) or not set(value).issubset(required | optional):
        raise PublicMetadataError(f"{label}_schema_invalid")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicMetadataError(f"{label}_must_be_object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PublicMetadataError(f"{label}_must_be_array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublicMetadataError(f"{label}_invalid")
    return value


def _safe_name(value: Any, label: str) -> str:
    item = _string(value, label)
    if (
        len(item) > MAX_IDENTITY_TEXT_LENGTH
        or item != item.strip()
        or any(unicodedata.category(character).startswith("C") for character in item)
    ):
        raise PublicMetadataError(f"{label}_invalid")
    return item


def _timestamp(value: Any) -> None:
    item = _string(value, "sbom_timestamp")
    try:
        parsed = datetime.fromisoformat(item)
    except (ValueError, OverflowError) as error:
        raise PublicMetadataError("sbom_timestamp_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicMetadataError("sbom_timestamp_not_utc")
    if parsed.utcoffset().total_seconds() != 0:
        raise PublicMetadataError("sbom_timestamp_not_utc")


def _sha_dependency(value: dict[str, str], label: str) -> None:
    if set(value) != {"sha256"} or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None:
        raise PublicMetadataError(f"{label}_invalid")


def _exact_sha_dependency(value: dict[str, str], expected: str, label: str) -> None:
    _sha_dependency(value, label)
    if value["sha256"] != expected.removeprefix("sha256:"):
        raise PublicMetadataError(f"{label}_mismatch")


if __name__ == "__main__":
    raise SystemExit(main())
