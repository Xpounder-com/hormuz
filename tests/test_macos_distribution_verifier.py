from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.verify_macos_distribution import (
    BASE_EXPECTED_FILES,
    EXPECTED_DIRECTORIES,
    STAPLED_TICKET,
    VerificationError,
    signing_details,
    verify_archive,
    verify_reported_version,
)


class MacOSDistributionArchiveTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
