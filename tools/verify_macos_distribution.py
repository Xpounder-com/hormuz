#!/usr/bin/env python3
"""Verify a distribution-shaped Hormuz Mac bundle without reading credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO


MODES = {"ad-hoc", "developer-id", "notarized"}
SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW_RUN_URL_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]*\Z"
)
BASE_EXPECTED_FILES = {
    "Hormuz.app/Contents/Info.plist",
    "Hormuz.app/Contents/Resources/ContextHelper/hormuz-context",
    "Hormuz.app/Contents/Helpers/hormuz-context-arm64",
    "Hormuz.app/Contents/MacOS/Hormuz",
    "Hormuz.app/Contents/Resources/Hormuz.icns",
    "Hormuz.app/Contents/Resources/HormuzMark.png",
    "Hormuz.app/Contents/Resources/HormuzMenuMark.png",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/Codenotch-LICENSE.txt",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/PyInstaller-COPYING.txt",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/Python-LICENSE.txt",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/regex-LICENSE.txt",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/tiktoken-LICENSE.txt",
    "Hormuz.app/Contents/Resources/ContextTokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
    "Hormuz.app/Contents/Resources/ContextTokenizers/fb374d419588a4632f3f557e76b4b70aebbca790",
    "Hormuz.app/Contents/_CodeSignature/CodeResources",
}
CONTEXT_LAUNCHER_SHA256 = "d85c56b7143b1a47d8640a35b15a6915757473030e3e2fcd18f7fb84b204e1aa"
TOKENIZER_SHA256 = {
    "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "fb374d419588a4632f3f557e76b4b70aebbca790": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}
STAPLED_TICKET = "Hormuz.app/Contents/CodeResources"
EXPECTED_DIRECTORIES = {
    "Hormuz.app/",
    "Hormuz.app/Contents/",
    "Hormuz.app/Contents/Helpers/",
    "Hormuz.app/Contents/MacOS/",
    "Hormuz.app/Contents/Resources/",
    "Hormuz.app/Contents/Resources/ContextHelper/",
    "Hormuz.app/Contents/Resources/ContextTokenizers/",
    "Hormuz.app/Contents/Resources/ThirdPartyNotices/",
    "Hormuz.app/Contents/_CodeSignature/",
}
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


class VerificationError(RuntimeError):
    pass


def run(*arguments: str) -> tuple[str, str]:
    result = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise VerificationError(f"command_failed:{Path(arguments[0]).name}")
    return result.stdout, result.stderr


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return stream_digest(source)


def stream_digest(source: BinaryIO) -> str:
    value = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        value.update(chunk)
    return value.hexdigest()


def expected_files(mode: str) -> set[str]:
    files = set(BASE_EXPECTED_FILES)
    if mode == "notarized":
        files.add(STAPLED_TICKET)
    return files


def signing_details(bundle: Path, mode: str, expected_identifier: str) -> dict[str, object]:
    run("codesign", "--verify", "--strict", "--verbose=4", str(bundle))
    _, details = run("codesign", "-dvvv", str(bundle))
    identifier = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    team = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    flags = re.search(r"^CodeDirectory .* flags=([^\n]+)$", details, re.MULTILINE)
    if identifier is None or identifier.group(1) != expected_identifier:
        raise VerificationError("wrong_signed_bundle_identifier")
    if flags is None or "runtime" not in flags.group(1):
        raise VerificationError("hardened_runtime_missing")

    entitlement_output, entitlement_diagnostic = run("codesign", "-d", "--entitlements", "-", str(bundle))
    if entitlement_output.strip() or "<plist" in entitlement_diagnostic:
        raise VerificationError("unexpected_distribution_entitlements")

    authorities = re.findall(r"^Authority=(.+)$", details, re.MULTILINE)
    team_id = team.group(1) if team is not None else ""
    if mode == "ad-hoc":
        if "Signature=adhoc" not in details or authorities or team_id not in {"", "not set"}:
            raise VerificationError("expected_ad_hoc_signature")
    else:
        if "Signature=adhoc" in details or len(authorities) < 2:
            raise VerificationError("developer_id_signature_missing")
        if not authorities[0].startswith("Developer ID Application:"):
            raise VerificationError("wrong_leaf_signing_authority")
        if re.fullmatch(r"[A-Z0-9]{10}", team_id) is None:
            raise VerificationError("developer_team_identifier_missing")
        if re.search(r"^Timestamp=.+$", details, re.MULTILINE) is None:
            raise VerificationError("secure_timestamp_missing")
        if mode == "notarized" and "Notarization Ticket=stapled" not in details:
            raise VerificationError("stapled_ticket_missing")
    return {"team_identifier": team_id or None, "authority": authorities[0] if authorities else None}


def verify_reported_version(executable: Path, expected_version: str) -> None:
    output, diagnostic = run(str(executable), "--version")
    if output != f"Hormuz Mac {expected_version}\n" or diagnostic:
        raise VerificationError("reported_release_version_mismatch")


def verify_archive(
    archive: Path,
    bundle: Path | None,
    mode: str,
    expected_identifier: str,
) -> dict[str, object]:
    required_files = expected_files(mode)
    try:
        with zipfile.ZipFile(archive) as packaged:
            files: set[str] = set()
            directories: set[str] = set()
            seen: set[str] = set()
            uncompressed_bytes = 0
            for entry in packaged.infolist():
                if entry.filename in seen:
                    raise VerificationError("duplicate_archive_member")
                seen.add(entry.filename)
                path = Path(entry.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise VerificationError("unsafe_archive_member")
                archived_mode = entry.external_attr >> 16
                archived_type = stat.S_IFMT(archived_mode)
                if archived_type == stat.S_IFLNK:
                    raise VerificationError("archive_symlink_not_allowed")
                if entry.flag_bits & 0x1:
                    raise VerificationError("encrypted_archive_member_not_allowed")
                if entry.file_size < 0:
                    raise VerificationError("invalid_archive_member_size")
                uncompressed_bytes += entry.file_size
                if uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise VerificationError("distribution_archive_too_large")
                if entry.is_dir():
                    if archived_type not in {0, stat.S_IFDIR} or entry.file_size != 0:
                        raise VerificationError("invalid_archive_directory")
                    directories.add(entry.filename)
                    continue
                if archived_type not in {0, stat.S_IFREG} or entry.file_size == 0:
                    raise VerificationError("invalid_archive_regular_file")
                files.add(entry.filename)
                if entry.filename not in required_files:
                    continue
                if bundle is not None:
                    relative = path.relative_to("Hormuz.app")
                    with packaged.open(entry) as archived_file:
                        if stream_digest(archived_file) != digest(bundle / relative):
                            raise VerificationError("archive_bundle_content_mismatch")
                if entry.filename in {
                    "Hormuz.app/Contents/MacOS/Hormuz",
                    "Hormuz.app/Contents/Resources/ContextHelper/hormuz-context",
                    "Hormuz.app/Contents/Helpers/hormuz-context-arm64",
                } and not archived_mode & 0o111:
                    raise VerificationError("archive_executable_mode_missing")
    except VerificationError:
        raise
    except (
        OSError,
        RuntimeError,
        EOFError,
        KeyError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as error:
        raise VerificationError("invalid_distribution_archive") from error

    if files != required_files or directories != EXPECTED_DIRECTORIES:
        expected = required_files | EXPECTED_DIRECTORIES
        missing = ",".join(sorted(expected - (files | directories))) or "none"
        extra = ",".join(sorted((files | directories) - expected)) or "none"
        raise VerificationError(f"unexpected_archive_contents:missing={missing}:extra={extra}")

    with tempfile.TemporaryDirectory(prefix="hormuz-macos-archive-") as temporary:
        run("ditto", "-x", "-k", str(archive), temporary)
        extracted_bundle = Path(temporary) / "Hormuz.app"
        signature = signing_details(extracted_bundle, mode, expected_identifier)
        if mode == "notarized":
            run("xcrun", "stapler", "validate", str(extracted_bundle))
            run("spctl", "--assess", "--type", "execute", "--verbose=4", str(extracted_bundle))
    return signature


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--expected-bundle-id", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-build", required=True)
    parser.add_argument(
        "--verify-executable-version",
        action="store_true",
        help="Execute the packaged binary to verify --version; use only without credentials",
    )
    parser.add_argument(
        "--verify-context-helper",
        action="store_true",
        help="Execute the packaged helper help path; use only without credentials",
    )
    parser.add_argument("--source-commit")
    parser.add_argument("--workflow-run-url")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    provenance_supplied = args.source_commit is not None or args.workflow_run_url is not None
    if (args.source_commit is None) != (args.workflow_run_url is None):
        raise VerificationError("incomplete_workflow_provenance")
    if provenance_supplied and (
        SOURCE_COMMIT_RE.fullmatch(args.source_commit) is None
        or WORKFLOW_RUN_URL_RE.fullmatch(args.workflow_run_url) is None
    ):
        raise VerificationError("invalid_workflow_provenance")

    bundle = args.bundle.resolve()
    archive = args.archive.resolve()
    executable = bundle / "Contents/MacOS/Hormuz"
    context_helper = bundle / "Contents/Resources/ContextHelper/hormuz-context"
    context_backends = {
        "arm64": bundle / "Contents/Helpers/hormuz-context-arm64",
    }
    information = bundle / "Contents/Info.plist"
    icon = bundle / "Contents/Resources/Hormuz.icns"
    tokenizer_directory = bundle / "Contents/Resources/ContextTokenizers"
    tokenizer_files = {tokenizer_directory / name for name in TOKENIZER_SHA256}
    if (
        not bundle.is_dir()
        or not executable.is_file()
        or not context_helper.is_file()
        or context_helper.is_symlink()
        or not all(path.is_file() and not path.is_symlink() for path in context_backends.values())
        or not information.is_file()
        or not icon.is_file()
        or not all(path.is_file() and path.stat().st_size > 0 for path in tokenizer_files)
    ):
        raise VerificationError("incomplete_app_bundle")
    if digest(context_helper) != CONTEXT_LAUNCHER_SHA256:
        raise VerificationError("unexpected_context_helper_launcher")
    for path in tokenizer_files:
        if digest(path) != TOKENIZER_SHA256[path.name]:
            raise VerificationError("context_tokenizer_digest_mismatch")
    if args.expected_bundle_id.endswith(".local"):
        raise VerificationError("local_bundle_identifier_not_distributable")

    with information.open("rb") as source:
        plist = plistlib.load(source)
    expected_plist = {
        "CFBundleExecutable": "Hormuz",
        "CFBundleIdentifier": args.expected_bundle_id,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": args.expected_version,
        "CFBundleVersion": args.expected_build,
        "CFBundleIconFile": "Hormuz",
        "LSMinimumSystemVersion": "14.0",
    }
    if any(plist.get(key) != value for key, value in expected_plist.items()):
        raise VerificationError("unexpected_distribution_plist")
    if plist.get("NSAppTransportSecurity") != {"NSAllowsLocalNetworking": True}:
        raise VerificationError("unexpected_transport_security_configuration")

    architecture_output, _ = run("lipo", "-archs", str(executable))
    architectures = sorted(architecture_output.split())
    if architectures != ["arm64"]:
        raise VerificationError("apple_silicon_binary_required")
    dependency_output, _ = run("otool", "-L", str(executable))
    dependencies = [line.strip().split(" (", 1)[0] for line in dependency_output.splitlines() if line.startswith("\t")]
    if not dependencies or any(not item.startswith(("/System/Library/", "/usr/lib/")) for item in dependencies):
        raise VerificationError("non_system_runtime_dependency")

    helper_architectures: dict[str, list[str]] = {}
    for expected_architecture, candidate in context_backends.items():
        architecture_output, _ = run("lipo", "-archs", str(candidate))
        candidate_architectures = sorted(architecture_output.split())
        if candidate_architectures != [expected_architecture]:
            raise VerificationError("context_helper_architecture_mismatch")
        dependency_output, _ = run("otool", "-L", str(candidate))
        dependencies = [line.strip().split(" (", 1)[0] for line in dependency_output.splitlines() if line.startswith("\t")]
        if not dependencies or any(not item.startswith(("/System/Library/", "/usr/lib/")) for item in dependencies):
            raise VerificationError("non_system_runtime_dependency")
        helper_architectures[expected_architecture] = candidate_architectures
    if args.verify_executable_version:
        verify_reported_version(executable, args.expected_version)
    if args.verify_context_helper:
        helper_output, helper_diagnostic = run(str(context_helper), "context", "--help")
        if "compact" not in helper_output or "run" not in helper_output or helper_diagnostic:
            raise VerificationError("context_helper_runtime_check_failed")

    signature = signing_details(bundle, args.mode, args.expected_bundle_id)
    helper_signatures: dict[str, dict[str, object]] = {}
    if args.mode != "ad-hoc":
        for architecture, candidate in context_backends.items():
            identifier_architecture = architecture.replace("_", "-")
            helper_signature = signing_details(
                candidate,
                "developer-id",
                f"{args.expected_bundle_id}.context-helper.{identifier_architecture}",
            )
            if helper_signature["team_identifier"] != signature["team_identifier"]:
                raise VerificationError("context_helper_team_mismatch")
            helper_signatures[architecture] = helper_signature
    if args.mode == "notarized":
        run("xcrun", "stapler", "validate", str(bundle))
        run("spctl", "--assess", "--type", "execute", "--verbose=4", str(bundle))
    verify_archive(archive, bundle, args.mode, args.expected_bundle_id)

    result = {
        "schema_id": "hormuz.macos-distribution-proof",
        "schema_version": 3,
        "passed": True,
        "mode": args.mode,
        "distribution_ready": args.mode == "notarized",
        "bundle_identifier": args.expected_bundle_id,
        "version": args.expected_version,
        "build": args.expected_build,
        "architectures": architectures,
        "context_helper_architectures": helper_architectures,
        "minimum_macos": "14.0",
        "hardened_runtime": True,
        "entitlements": [],
        "system_runtime_dependencies_only": True,
        "context_helper_packaged": True,
        "context_helper_runtime_verified": args.verify_context_helper,
        "context_helper_launcher_sealed_by_bundle": True,
        "context_helper_signatures": helper_signatures,
        "context_tokenizers_packaged": ["cl100k_base", "o200k_base"],
        "executable_version_verified": args.verify_executable_version,
        "notarization_ticket_stapled": args.mode == "notarized",
        "team_identifier": signature["team_identifier"],
        "signing_authority": signature["authority"],
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": digest(archive),
        "executable_sha256": digest(executable),
        "context_helper_sha256": digest(context_helper),
        "context_helper_backend_sha256": {
            architecture: digest(path)
            for architecture, path in sorted(context_backends.items())
        },
        "icon_sha256": digest(icon),
    }
    if provenance_supplied:
        result.update(
            {
                "source_commit": args.source_commit,
                "workflow_run_url": args.workflow_run_url,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    os.chmod(args.output, 0o600)
    print(json.dumps({key: result[key] for key in ("passed", "mode", "distribution_ready", "architectures")}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"macos_distribution_verification_failed:{error}", file=sys.stderr)
        raise SystemExit(1) from None
