from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools import verify_oci_reproducibility as reproducibility


class OciReproducibilityTests(unittest.TestCase):
    def test_identical_single_platform_archives_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar"
            second = root / "second.tar"
            self._write_archive(first)
            self._write_archive(second)

            summary = reproducibility.verify_archives(
                first=first,
                second=second,
                expected_reference="v0.1.0",
                expected_version="0.1.0",
                expected_commit="a" * 40,
                source_date_epoch=1787562000,
            )

        self.assertEqual(summary["schema_id"], "hormuz.oci-reproducibility")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["artifact"]["platform"], "linux/amd64")
        self.assertTrue(summary["artifact"]["digest"].startswith("sha256:"))
        self.assertEqual(summary["build"]["independent_no_cache_builds"], 2)
        self.assertEqual(summary["verdict"], "pass")

    def test_different_archives_fail_before_a_reproducibility_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar"
            second = root / "second.tar"
            self._write_archive(first)
            self._write_archive(second, version="0.2.2")

            with self.assertRaisesRegex(
                reproducibility.ReproducibilityError,
                "independent_oci_archives_differ",
            ):
                reproducibility.verify_archives(
                    first=first,
                    second=second,
                    expected_reference="v0.1.0",
                    expected_version="0.1.0",
                    expected_commit="a" * 40,
                    source_date_epoch=1787562000,
                )

    def test_wrong_platform_or_revision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar"
            second = root / "second.tar"
            self._write_archive(first, architecture="arm64")
            self._write_archive(second, architecture="arm64")
            with self.assertRaisesRegex(reproducibility.ReproducibilityError, "platform"):
                reproducibility.verify_archives(
                    first=first,
                    second=second,
                    expected_reference="v0.1.0",
                    expected_version="0.1.0",
                    expected_commit="a" * 40,
                    source_date_epoch=1787562000,
                )

            self._write_archive(first)
            self._write_archive(second)
            with self.assertRaisesRegex(reproducibility.ReproducibilityError, "revision"):
                reproducibility.verify_archives(
                    first=first,
                    second=second,
                    expected_reference="v0.1.0",
                    expected_version="0.1.0",
                    expected_commit="b" * 40,
                    source_date_epoch=1787562000,
                )

    def _write_archive(
        self,
        path: Path,
        *,
        version: str = "0.1.0",
        architecture: str = "amd64",
    ) -> None:
        commit = "a" * 40
        config = self._json_bytes({
            "architecture": architecture,
            "os": "linux",
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": commit,
                    "org.opencontainers.image.version": version,
                }
            },
        })
        config_digest = self._digest(config)
        layer = b"fixed-layer"
        layer_digest = self._digest(layer)
        manifest = self._json_bytes({
            "schemaVersion": 2,
            "mediaType": reproducibility.OCI_MANIFEST,
            "config": {
                "mediaType": reproducibility.OCI_CONFIG,
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [{
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": layer_digest,
                "size": len(layer),
            }],
        })
        manifest_digest = self._digest(manifest)
        index = self._json_bytes({
            "schemaVersion": 2,
            "mediaType": reproducibility.OCI_INDEX,
            "manifests": [{
                "mediaType": reproducibility.OCI_MANIFEST,
                "digest": manifest_digest,
                "size": len(manifest),
                "annotations": {"org.opencontainers.image.ref.name": "v0.1.0"},
                "platform": {"architecture": architecture, "os": "linux"},
            }],
        })
        members = {
            "oci-layout": self._json_bytes({"imageLayoutVersion": "1.0.0"}),
            "index.json": index,
            self._blob_name(config_digest): config,
            self._blob_name(layer_digest): layer,
            self._blob_name(manifest_digest): manifest,
        }
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
            for name in sorted(members):
                value = members[name]
                info = tarfile.TarInfo(name)
                info.size = len(value)
                info.mtime = 0
                archive.addfile(info, io.BytesIO(value))

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _digest(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    @staticmethod
    def _blob_name(digest: str) -> str:
        algorithm, value = digest.split(":", 1)
        return f"blobs/{algorithm}/{value}"


if __name__ == "__main__":
    unittest.main()
