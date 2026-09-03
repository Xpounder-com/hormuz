from __future__ import annotations

import json
import plistlib
import stat
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from tools.verify_macos_distribution import (
    BASE_EXPECTED_FILES,
    EXPECTED_DIRECTORIES,
    STAPLED_TICKET,
    VerificationError,
    main,
    signing_details,
    verify_archive,
    verify_reported_version,
)


class MacOSDistributionArchiveTests(unittest.TestCase):
    def _run_distribution_main(
        self,
        root: Path,
        *,
        verify_executable_version: bool,
        source_commit: str | None = None,
        workflow_run_url: str | None = None,
    ) -> tuple[dict[str, object], object]:
        bundle = root / "Hormuz.app"
        executable = bundle / "Contents/MacOS/Hormuz"
        information = bundle / "Contents/Info.plist"
        icon = bundle / "Contents/Resources/Hormuz.icns"
        executable.parent.mkdir(parents=True)
        icon.parent.mkdir(parents=True)
        executable.write_bytes(b"unexecuted-test-binary")
        icon.write_bytes(b"icon")
        with information.open("wb") as output:
            plistlib.dump(
                {
                    "CFBundleExecutable": "Hormuz",
                    "CFBundleIdentifier": "com.xpounder.hormuz",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "2.3.4",
                    "CFBundleVersion": "17",
                    "CFBundleIconFile": "Hormuz",
                    "LSMinimumSystemVersion": "14.0",
                    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
                },
                output,
            )
        archive = root / "Hormuz.zip"
        archive.write_bytes(b"archive")
        proof = root / "proof.json"
        arguments = [
            "verify_macos_distribution.py",
            "--bundle",
            str(bundle),
            "--archive",
            str(archive),
            "--mode",
            "ad-hoc",
            "--expected-bundle-id",
            "com.xpounder.hormuz",
            "--expected-version",
            "2.3.4",
            "--expected-build",
            "17",
            "--output",
            str(proof),
        ]
        if verify_executable_version:
            arguments.append("--verify-executable-version")
        if source_commit is not None:
            arguments.extend(("--source-commit", source_commit))
        if workflow_run_url is not None:
            arguments.extend(("--workflow-run-url", workflow_run_url))
        with (
            patch.object(sys, "argv", arguments),
            patch(
                "tools.verify_macos_distribution.run",
                side_effect=(
                    ("arm64 x86_64\n", ""),
                    (
                        (
                            f"{executable}:\n"
                            "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
                        ),
                        "",
                    ),
                ),
            ),
            patch(
                "tools.verify_macos_distribution.signing_details",
                return_value={"team_identifier": None, "authority": None},
            ),
            patch("tools.verify_macos_distribution.verify_archive"),
            patch("tools.verify_macos_distribution.verify_reported_version") as runtime,
        ):
            self.assertEqual(main(), 0)
        return json.loads(proof.read_text(encoding="utf-8")), runtime

    def _write_archive(self, root: Path, *, include_ticket: bool) -> tuple[Path, Path]:
        bundle = root / "Hormuz.app"
        files = set(BASE_EXPECTED_FILES)
        if include_ticket:
            files.add(STAPLED_TICKET)
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"content:{name}".encode())

        archive = root / "Hormuz.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name in sorted(EXPECTED_DIRECTORIES):
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (stat.S_IFDIR | 0o755) << 16
                output.writestr(entry, b"")
            for name in sorted(files):
                entry = zipfile.ZipInfo(name)
                mode = 0o755 if name.endswith("/MacOS/Hormuz") else 0o644
                entry.external_attr = (stat.S_IFREG | mode) << 16
                output.writestr(entry, (root / name).read_bytes())
        return archive, bundle

    def _verify(self, archive: Path, bundle: Path, mode: str) -> None:
        with (
            patch("tools.verify_macos_distribution.run", return_value=("", "")),
            patch("tools.verify_macos_distribution.signing_details", return_value={}),
        ):
            verify_archive(archive, bundle, mode, "com.xpounder.hormuz")

    def test_notarized_archive_requires_stapled_ticket_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, bundle = self._write_archive(Path(temporary), include_ticket=False)
            with self.assertRaisesRegex(VerificationError, "Contents/CodeResources"):
                self._verify(archive, bundle, "notarized")

    def test_notarized_archive_preserves_stapled_ticket_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, bundle = self._write_archive(Path(temporary), include_ticket=True)
            self._verify(archive, bundle, "notarized")

    def test_pre_notarization_archive_rejects_stapled_ticket_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, bundle = self._write_archive(Path(temporary), include_ticket=True)
            with self.assertRaisesRegex(VerificationError, "Contents/CodeResources"):
                self._verify(archive, bundle, "developer-id")

    def test_distribution_archive_uncompressed_size_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, bundle = self._write_archive(Path(temporary), include_ticket=True)
            with (
                patch(
                    "tools.verify_macos_distribution.MAX_ARCHIVE_UNCOMPRESSED_BYTES",
                    1,
                ),
                self.assertRaisesRegex(VerificationError, "distribution_archive_too_large"),
            ):
                self._verify(archive, bundle, "notarized")

    def test_corrupt_deflate_is_a_bounded_verification_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, bundle = self._write_archive(Path(temporary), include_ticket=True)
            with (
                patch.object(
                    zipfile.ZipExtFile,
                    "read",
                    side_effect=zlib.error("corrupt deflate"),
                ),
                self.assertRaisesRegex(
                    VerificationError, "invalid_distribution_archive"
                ),
            ):
                self._verify(archive, bundle, "notarized")

    def test_notarized_signature_requires_stapled_ticket_marker(self) -> None:
        details = "\n".join(
            (
                "Identifier=com.xpounder.hormuz",
                "CodeDirectory v=20500 flags=0x10000(runtime)",
                "Authority=Developer ID Application: Xpounder (ABCDEFGHIJ)",
                "Authority=Developer ID Certification Authority",
                "TeamIdentifier=ABCDEFGHIJ",
                "Timestamp=Sep 1, 2026 at 12:00:00 PM",
            )
        )
        with patch(
            "tools.verify_macos_distribution.run",
            side_effect=(("", ""), ("", details), ("", "")),
        ):
            with self.assertRaisesRegex(VerificationError, "stapled_ticket_missing"):
                signing_details(Path("Hormuz.app"), "notarized", "com.xpounder.hormuz")

    def test_packaged_executable_must_report_the_plist_release_version(self) -> None:
        executable = Path("Hormuz.app/Contents/MacOS/Hormuz")
        with patch(
            "tools.verify_macos_distribution.run",
            return_value=("Hormuz Mac 2.3.4\n", ""),
        ) as command:
            verify_reported_version(executable, "2.3.4")
        command.assert_called_once_with(str(executable), "--version")

        with patch(
            "tools.verify_macos_distribution.run",
            return_value=("Hormuz Mac 0.1.0-local\n", ""),
        ):
            with self.assertRaisesRegex(
                VerificationError, "reported_release_version_mismatch"
            ):
                verify_reported_version(executable, "2.3.4")

    def test_distribution_verifier_does_not_execute_payload_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proof, runtime = self._run_distribution_main(
                Path(temporary), verify_executable_version=False
            )
        runtime.assert_not_called()
        self.assertFalse(proof["executable_version_verified"])
        self.assertEqual(proof["schema_version"], 1)
        self.assertNotIn("source_commit", proof)

    def test_distribution_verifier_executes_payload_only_with_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proof, runtime = self._run_distribution_main(
                Path(temporary), verify_executable_version=True
            )
        runtime.assert_called_once()
        self.assertTrue(proof["executable_version_verified"])

    def test_distribution_proof_binds_protected_workflow_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proof, _runtime = self._run_distribution_main(
                Path(temporary),
                verify_executable_version=False,
                source_commit="a" * 40,
                workflow_run_url=(
                    "https://github.com/Xpounder-com/hormuz/actions/runs/12345"
                ),
            )
        self.assertEqual(proof["schema_version"], 2)
        self.assertEqual(proof["source_commit"], "a" * 40)
        self.assertEqual(
            proof["workflow_run_url"],
            "https://github.com/Xpounder-com/hormuz/actions/runs/12345",
        )

    def test_distribution_provenance_must_be_complete_and_repository_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(VerificationError, "incomplete_workflow_provenance"):
                self._run_distribution_main(
                    Path(temporary),
                    verify_executable_version=False,
                    source_commit="a" * 40,
                )

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(VerificationError, "invalid_workflow_provenance"):
                self._run_distribution_main(
                    Path(temporary),
                    verify_executable_version=False,
                    source_commit="a" * 40,
                    workflow_run_url="https://github.com/other/repo/actions/runs/1",
                )


if __name__ == "__main__":
    unittest.main()
