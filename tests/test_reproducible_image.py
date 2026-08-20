from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.reproducible_image import (
    BUILDKIT_IMAGE,
    BUILDKIT_VERSION,
    OCILayoutSummary,
    OCIReproducibilityError,
    build_command,
    canonicalize_oci_layout,
    compare_oci_layouts,
    _dependency_locks_sha256,
    render_manifest,
    validate_builder,
    validate_oci_layout,
    validate_source_sha,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "7" * 40
EPOCH = 1_786_934_370
VERSION = "0.1.0"
PLATFORM = "linux/amd64"
PLATFORMS = ("linux/amd64", "linux/arm64")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_blob(root: Path, value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()
    path = root / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return digest


def _write_layout(
    root: Path,
    *,
    revision: str = SHA,
    version: str = VERSION,
    layer: bytes = b"deterministic-layer",
    platform: str = PLATFORM,
) -> OCILayoutSummary:
    root.mkdir(parents=True, exist_ok=True)
    architecture = platform.split("/", 1)[1]
    created = datetime.fromtimestamp(EPOCH, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    layer_digest = _write_blob(root, layer)
    config = _canonical(
        {
            "architecture": architecture,
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.version": version,
                },
                "User": "65532:65532",
            },
            "created": created,
            "os": "linux",
            "rootfs": {"diff_ids": ["sha256:" + ("a" * 64)], "type": "layers"},
        }
    )
    config_digest = _write_blob(root, config)
    manifest = _canonical(
        {
            "config": {
                "digest": "sha256:" + config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [
                {
                    "digest": "sha256:" + layer_digest,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "size": len(layer),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    manifest_digest = _write_blob(root, manifest)
    index = _canonical(
        {
            "manifests": [
                {
                    "digest": "sha256:" + manifest_digest,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": architecture, "os": "linux"},
                    "size": len(manifest),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    (root / "index.json").write_bytes(index)
    (root / "oci-layout").write_bytes(b'{"imageLayoutVersion":"1.0.0"}\n')
    return validate_oci_layout(
        root,
        source_sha=revision,
        source_date_epoch=EPOCH,
        version=version,
        platform=platform,
    )


class ReproducibleImageTests(unittest.TestCase):
    def test_dependency_digest_binds_both_runtime_locks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "deploy/container").mkdir(parents=True)
            (root / "deploy/postgres").mkdir(parents=True)
            (root / "deploy/container/requirements.lock").write_text("core\n")
            postgres = root / "deploy/postgres/requirements.lock"
            postgres.write_text("postgres-a\n")

            first = _dependency_locks_sha256(root)
            postgres.write_text("postgres-b\n")
            second = _dependency_locks_sha256(root)

            self.assertRegex(first, r"^[0-9a-f]{64}$")
            self.assertNotEqual(first, second)

    def test_source_and_build_command_are_exact_and_nonpublishing(self) -> None:
        self.assertEqual(validate_source_sha(SHA), SHA)
        for invalid in ("7" * 39, "G" * 40, "../" + SHA, "7" * 41):
            with self.subTest(invalid=invalid), self.assertRaises(
                OCIReproducibilityError
            ):
                validate_source_sha(invalid)

        command = build_command(
            context=Path("/tmp/exact-source"),
            destination=Path("/tmp/image-layout"),
            source_sha=SHA,
            source_date_epoch=EPOCH,
            version=VERSION,
            platform=PLATFORM,
        )
        rendered = " ".join(command)
        self.assertEqual(command[:3], ["docker", "buildx", "build"])
        self.assertIn("--no-cache", command)
        self.assertIn("--pull=false", command)
        self.assertIn("--provenance=false", command)
        self.assertIn("--sbom=false", command)
        self.assertIn("HORMUZ_REVISION=" + SHA, command)
        self.assertIn("SOURCE_DATE_EPOCH=" + str(EPOCH), command)
        self.assertIn("--platform " + PLATFORM, rendered)
        self.assertIn("tar=false,rewrite-timestamp=true", rendered)
        self.assertNotIn("--push", command)
        self.assertNotIn("--tag", command)

    def test_builder_is_pinned_and_fail_closed(self) -> None:
        self.assertEqual(BUILDKIT_VERSION, "v0.32.2")
        self.assertEqual(
            BUILDKIT_IMAGE,
            "moby/buildkit:v0.32.2@sha256:"
            "28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8",
        )
        valid = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "Name: hormuz-reproducer\n"
                "Driver: docker-container\n"
                "BuildKit version: v0.32.2\n"
            ),
            stderr="",
        )
        with patch("scripts.reproducible_image.subprocess.run", return_value=valid):
            validate_builder()

        for output in (
            "Driver: docker\nBuildKit version: v0.32.2\n",
            "Driver: docker-container\nBuildKit version: v0.29.0\n",
        ):
            with self.subTest(output=output), patch(
                "scripts.reproducible_image.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=output, stderr=""
                ),
            ), self.assertRaises(OCIReproducibilityError):
                validate_builder()

    def test_layout_validation_binds_every_blob_and_image_identity(self) -> None:
        for platform in PLATFORMS:
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary) / "layout"
                expected = _write_layout(root, platform=platform)
                actual = validate_oci_layout(
                    root,
                    source_sha=SHA,
                    source_date_epoch=EPOCH,
                    version=VERSION,
                    platform=platform,
                )
                self.assertEqual(actual, expected)
                self.assertRegex(actual.index_sha256, r"^[0-9a-f]{64}$")
                self.assertRegex(actual.manifest_digest, r"^sha256:[0-9a-f]{64}$")
                self.assertRegex(actual.config_digest, r"^sha256:[0-9a-f]{64}$")
                self.assertEqual(len(actual.layer_digests), 1)

        with self.assertRaises(OCIReproducibilityError):
            build_command(
                context=Path("/tmp/exact-source"),
                destination=Path("/tmp/image-layout"),
                source_sha=SHA,
                source_date_epoch=EPOCH,
                version=VERSION,
                platform="linux/s390x",
            )

    def test_layout_validation_rejects_mutation_ambiguity_and_unsafe_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            mutated = base / "mutated"
            summary = _write_layout(mutated)
            layer = mutated / "blobs" / "sha256" / summary.layer_digests[0][7:]
            layer.write_bytes(b"hormuz-sentinel")
            with self.assertRaises(OCIReproducibilityError) as raised:
                validate_oci_layout(
                    mutated,
                    source_sha=SHA,
                    source_date_epoch=EPOCH,
                    version=VERSION,
                    platform=PLATFORM,
                )
            self.assertNotIn("hormuz-sentinel", str(raised.exception))

            ambiguous = base / "ambiguous"
            _write_layout(ambiguous)
            (ambiguous / "index.json").write_bytes(
                b'{"schemaVersion":2,"schemaVersion":2}\n'
            )
            with self.assertRaises(OCIReproducibilityError):
                validate_oci_layout(
                    ambiguous,
                    source_sha=SHA,
                    source_date_epoch=EPOCH,
                    version=VERSION,
                    platform=PLATFORM,
                )

            unexpected = base / "unexpected"
            _write_layout(unexpected)
            (unexpected / "hormuz-sentinel").write_text("x")
            with self.assertRaises(OCIReproducibilityError) as raised:
                validate_oci_layout(
                    unexpected,
                    source_sha=SHA,
                    source_date_epoch=EPOCH,
                    version=VERSION,
                    platform=PLATFORM,
                )
            self.assertNotIn("hormuz-sentinel", str(raised.exception))

            if hasattr(Path, "symlink_to"):
                linked = base / "linked"
                _write_layout(linked)
                (linked / "unsafe").symlink_to(linked / "index.json")
                with self.assertRaises(OCIReproducibilityError):
                    validate_oci_layout(
                        linked,
                        source_sha=SHA,
                        source_date_epoch=EPOCH,
                        version=VERSION,
                        platform=PLATFORM,
                    )

    def test_comparison_and_canonical_archive_are_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            _write_layout(first)
            _write_layout(second)
            compare_oci_layouts(first, second)

            first_tar = root / "first.oci.tar"
            second_tar = root / "second.oci.tar"
            canonicalize_oci_layout(first, first_tar, source_date_epoch=EPOCH)
            canonicalize_oci_layout(second, second_tar, source_date_epoch=EPOCH)
            self.assertEqual(first_tar.read_bytes(), second_tar.read_bytes())
            with tarfile.open(first_tar, "r:") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    [member.name for member in members],
                    sorted(member.name for member in members),
                )
                self.assertTrue(all(member.isfile() for member in members))
                for member in members:
                    self.assertEqual(member.mtime, EPOCH)
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    self.assertEqual((member.uname, member.gname), ("", ""))
                    self.assertEqual(member.mode, 0o644)

            changed = root / "changed"
            summary = _write_layout(changed)
            layer = changed / "blobs" / "sha256" / summary.layer_digests[0][7:]
            layer.write_bytes(layer.read_bytes() + b"x")
            with self.assertRaises(OCIReproducibilityError):
                compare_oci_layouts(first, changed)

    def test_manifest_is_deterministic_content_free_and_input_bound(self) -> None:
        summary = OCILayoutSummary(
            index_sha256="1" * 64,
            manifest_digest="sha256:" + ("2" * 64),
            config_digest="sha256:" + ("3" * 64),
            layer_digests=("sha256:" + ("4" * 64),),
            file_count=5,
            total_bytes=123,
        )
        values = dict(
            source_sha=SHA,
            source_date_epoch=EPOCH,
            version=VERSION,
            platform=PLATFORM,
            dockerfile_sha256="5" * 64,
            dependency_lock_sha256="6" * 64,
            base_image_digest="sha256:" + ("7" * 64),
            summary=summary,
            artifact_filename="hormuz-0.1.0-linux-amd64.oci.tar",
            artifact_sha256="8" * 64,
            artifact_size=456,
        )
        first = render_manifest(**values)
        second = render_manifest(**values)
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        self.assertEqual(first["schema"], "hormuz.reproducible-oci.v1")
        self.assertEqual(first["independent_builds"], 2)
        self.assertEqual(first["source_sha"], SHA)
        self.assertEqual(first["builder"]["version"], "v0.32.2")
        self.assertEqual(first["builder"]["driver"], "docker-container")
        self.assertNotIn("/tmp", encoded)
        self.assertNotIn("builder_host", encoded)
        self.assertNotIn("username", encoded)

    def test_dockerfile_and_ci_bind_the_reproducibility_contract(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        release = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("ARG SOURCE_DATE_EPOCH", dockerfile)
        self.assertIn('SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}"', dockerfile)
        self.assertEqual(dockerfile.count("rm -rf /root/.cache"), 2)
        self.assertIn("python scripts/reproducible_image.py", workflow)
        self.assertIn('--source-sha "$GITHUB_SHA"', workflow)
        self.assertIn("hormuz-reproducible-oci", workflow)
        self.assertEqual(workflow.count("python scripts/reproducible_image.py"), 2)
        self.assertIn("--platform linux/amd64", workflow)
        self.assertIn("--platform linux/arm64", workflow)
        self.assertIn("hormuz-reproducible-oci/linux-amd64", workflow)
        self.assertIn("hormuz-reproducible-oci/linux-arm64", workflow)
        self.assertIn("moby/buildkit:v0.32.2@sha256:", workflow)
        self.assertIn('SOURCE_DATE_EPOCH=$HORMUZ_SOURCE_DATE_EPOCH', workflow)
        self.assertIn("python scripts/reproducible_image.py", release)
        self.assertIn('--source-sha "$GITHUB_SHA"', release)
        self.assertIn("hormuz-reproducible-oci", release)
        self.assertEqual(release.count("python scripts/reproducible_image.py"), 2)
        self.assertIn("--platform linux/amd64", release)
        self.assertIn("--platform linux/arm64", release)
        self.assertIn("hormuz-reproducible-oci/linux-amd64", release)
        self.assertIn("hormuz-reproducible-oci/linux-arm64", release)
        self.assertGreaterEqual(release.count("moby/buildkit:v0.32.2@sha256:"), 2)
        self.assertIn("SOURCE_DATE_EPOCH=${{ steps.source_epoch.outputs.value }}", release)
        self.assertIn("provenance: mode=max", release)
        self.assertIn("sbom: true", release)


if __name__ == "__main__":
    unittest.main()
