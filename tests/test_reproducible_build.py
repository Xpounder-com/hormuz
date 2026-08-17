import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.reproducible_build import (
    LockedDistribution,
    ReproducibleBuildError,
    canonicalize_sdist,
    parse_build_lock,
    render_manifest,
    validate_build_toolchain,
    validate_installed_build_toolchain,
)


SHA = "7" * 40
EPOCH = 1_786_930_600
LOCKED = (
    LockedDistribution("build", "1.3.0", "a" * 64),
    LockedDistribution("colorama", "0.4.6", "b" * 64),
    LockedDistribution("packaging", "26.3", "c" * 64),
    LockedDistribution("pyproject-hooks", "1.2.0", "d" * 64),
    LockedDistribution("setuptools", "84.0.0", "e" * 64),
    LockedDistribution("wheel", "0.48.0", "f" * 64),
)


def _write_sdist(
    path: Path,
    *,
    members: list[tuple[str, bytes | None, int]],
    archive_mtime: int,
    uid: int,
) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(
            filename="source-name.tar",
            mode="wb",
            fileobj=raw,
            mtime=archive_mtime,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w|",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for name, content, mode in members:
                    member = tarfile.TarInfo(name)
                    member.mtime = archive_mtime
                    member.uid = uid
                    member.gid = uid
                    member.uname = f"user-{uid}"
                    member.gname = f"group-{uid}"
                    member.mode = mode
                    if content is None:
                        member.type = tarfile.DIRTYPE
                        archive.addfile(member)
                    else:
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))


class ReproducibleBuildTests(unittest.TestCase):
    def test_canonical_sdist_normalizes_archive_and_member_metadata(self) -> None:
        first_members = [
            ("hormuz-0.1.0/", None, 0o775),
            ("hormuz-0.1.0/hormuz/", None, 0o700),
            ("hormuz-0.1.0/hormuz/__init__.py", b'VERSION = "0.1.0"\n', 0o600),
            ("hormuz-0.1.0/scripts/check.py", b"print('ok')\n", 0o755),
        ]
        second_members = [first_members[0], *reversed(first_members[1:])]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            canonical_first = root / "canonical-first.tar.gz"
            canonical_second = root / "canonical-second.tar.gz"
            _write_sdist(
                first,
                members=first_members,
                archive_mtime=EPOCH + 100,
                uid=501,
            )
            _write_sdist(
                second,
                members=second_members,
                archive_mtime=EPOCH + 200,
                uid=1001,
            )

            canonicalize_sdist(first, canonical_first, source_date_epoch=EPOCH)
            canonicalize_sdist(second, canonical_second, source_date_epoch=EPOCH)

            self.assertEqual(canonical_first.read_bytes(), canonical_second.read_bytes())
            with tarfile.open(canonical_first, "r:gz") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    [member.name for member in members],
                    sorted(member.name for member in members),
                )
                for member in members:
                    self.assertEqual(member.mtime, EPOCH)
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    self.assertEqual((member.uname, member.gname), ("", ""))
                regular = {member.name: member for member in members if member.isfile()}
                self.assertEqual(
                    regular["hormuz-0.1.0/hormuz/__init__.py"].mode,
                    0o644,
                )
                self.assertEqual(
                    regular["hormuz-0.1.0/scripts/check.py"].mode,
                    0o755,
                )

    def test_canonical_sdist_rejects_unsafe_or_ambiguous_members(self) -> None:
        cases = {
            "absolute": [("/tmp/hormuz-sentinel", b"x", 0o644)],
            "traversal": [("hormuz-0.1.0/../hormuz-sentinel", b"x", 0o644)],
            "noncanonical": [("hormuz-0.1.0//hormuz-sentinel", b"x", 0o644)],
            "backslash": [("hormuz-0.1.0\\hormuz-sentinel", b"x", 0o644)],
            "multiple roots": [
                ("hormuz-0.1.0/a", b"a", 0o644),
                ("hormuz-sentinel/b", b"b", 0o644),
            ],
            "duplicate": [
                ("hormuz-0.1.0/a", b"a", 0o644),
                ("hormuz-0.1.0/a", b"a", 0o644),
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (name, members) in enumerate(cases.items()):
                with self.subTest(name=name):
                    source = root / f"unsafe-{index}.tar.gz"
                    output = root / f"canonical-{index}.tar.gz"
                    _write_sdist(
                        source,
                        members=members,
                        archive_mtime=EPOCH,
                        uid=501,
                    )
                    with self.assertRaises(ReproducibleBuildError) as raised:
                        canonicalize_sdist(
                            source,
                            output,
                            source_date_epoch=EPOCH,
                        )
                    self.assertNotIn("hormuz-sentinel", str(raised.exception))
                    self.assertFalse(output.exists())

            linked = root / "linked.tar.gz"
            with linked.open("wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=EPOCH) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|") as archive:
                        member = tarfile.TarInfo("hormuz-0.1.0/link")
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/tmp/hormuz-sentinel"
                        archive.addfile(member)
            with self.assertRaises(ReproducibleBuildError) as raised:
                canonicalize_sdist(
                    linked,
                    root / "linked-canonical.tar.gz",
                    source_date_epoch=EPOCH,
                )
            self.assertNotIn("hormuz-sentinel", str(raised.exception))

    def test_build_toolchain_requires_exact_pins(self) -> None:
        self.assertEqual(
            validate_build_toolchain(
                ["setuptools==84.0.0", "wheel==0.48.0"],
                build_frontend_version="1.3.0",
            ),
            ("setuptools==84.0.0", "wheel==0.48.0"),
        )
        for requirements, frontend in (
            (["setuptools>=75", "wheel==0.48.0"], "1.3.0"),
            (["setuptools==84.0.0", "wheel>=0.48"], "1.3.0"),
            (["setuptools==84.0.0"], "1.3.0"),
            (["setuptools==84.0.0", "wheel==0.48.0"], "1.4.0"),
        ):
            with self.subTest(requirements=requirements, frontend=frontend):
                with self.assertRaises(ReproducibleBuildError):
                    validate_build_toolchain(
                        requirements,
                        build_frontend_version=frontend,
                    )

    def test_build_lock_requires_the_exact_complete_hashed_closure(self) -> None:
        text = "\n".join(
            f"{item.name}=={item.version} --hash=sha256:{item.sha256}"
            for item in LOCKED
        ) + "\n"
        self.assertEqual(parse_build_lock(text), LOCKED)
        self.assertEqual(
            parse_build_lock(
                (Path(__file__).resolve().parents[1]
                 / "deploy/build/requirements.lock").read_text()
            ),
            (
                LockedDistribution(
                    "build",
                    "1.3.0",
                    "7145f0b5061ba90a1500d60bd1b13ca0a8a4cebdd0cc16ed8adf1c0e739f43b4",
                ),
                LockedDistribution(
                    "colorama",
                    "0.4.6",
                    "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
                ),
                LockedDistribution(
                    "packaging",
                    "26.3",
                    "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
                ),
                LockedDistribution(
                    "pyproject-hooks",
                    "1.2.0",
                    "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913",
                ),
                LockedDistribution(
                    "setuptools",
                    "84.0.0",
                    "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670",
                ),
                LockedDistribution(
                    "wheel",
                    "0.48.0",
                    "3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab",
                ),
            ),
        )

        invalid = {
            "missing": "\n".join(text.splitlines()[:-1]),
            "duplicate": text + text.splitlines()[0] + "\n",
            "range": text.replace("build==1.3.0", "build>=1.3.0"),
            "bad hash": text.replace("a" * 64, "hormuz-sentinel"),
            "unknown": text.replace("build==1.3.0", "hormuz-sentinel==1.3.0"),
            "option": text + "--index-url https://hormuz-sentinel.invalid\n",
        }
        for name, value in invalid.items():
            with self.subTest(name=name), self.assertRaises(
                ReproducibleBuildError
            ) as raised:
                parse_build_lock(value)
            self.assertNotIn("hormuz-sentinel", str(raised.exception))

    def test_installed_build_toolchain_must_match_every_locked_version(self) -> None:
        versions = {item.name: item.version for item in LOCKED}
        validate_installed_build_toolchain(
            LOCKED,
            version_lookup=versions.__getitem__,
        )
        with self.assertRaises(ReproducibleBuildError):
            validate_installed_build_toolchain(
                LOCKED,
                version_lookup=lambda name: "9.9.9" if name == "packaging" else versions[name],
            )
        with self.assertRaises(ReproducibleBuildError):
            validate_installed_build_toolchain(
                LOCKED,
                version_lookup=lambda name: (_ for _ in ()).throw(KeyError(name)),
            )

    def test_manifest_is_deterministic_content_free_and_hash_bound(self) -> None:
        artifacts = {
            "hormuz-0.1.0-py3-none-any.whl": ("a" * 64, 1234),
            "hormuz-0.1.0.tar.gz": ("b" * 64, 5678),
        }
        manifest = render_manifest(
            source_sha=SHA,
            source_date_epoch=EPOCH,
            build_requirements=("setuptools==84.0.0", "wheel==0.48.0"),
            locked_distributions=LOCKED,
            build_lock_sha256="9" * 64,
            artifacts=artifacts,
        )
        self.assertEqual(manifest["schema"], "hormuz.reproducible-distributions.v2")
        self.assertEqual(manifest["source_sha"], SHA)
        self.assertEqual(manifest["build_lock_sha256"], "9" * 64)
        self.assertEqual(
            manifest["locked_distributions"],
            [f"{item.name}=={item.version}" for item in LOCKED],
        )
        self.assertEqual(
            [item["filename"] for item in manifest["artifacts"]],
            sorted(artifacts),
        )
        encoded = json.dumps(manifest, sort_keys=True)
        self.assertNotIn(str(Path.home()), encoded)
        self.assertNotIn("generated_at", encoded)


if __name__ == "__main__":
    unittest.main()
