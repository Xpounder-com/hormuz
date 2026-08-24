from __future__ import annotations

import unittest

from tools import verify_oci_release_preflight as preflight


COMMIT = "a" * 40
TAG = "v0.1.0"
REF = f"refs/tags/{TAG}"
WORKFLOW_REF = (
    "Xpounder-com/hormuz/.github/workflows/release-oci.yml@"
    f"{REF}"
)


class OciReleasePreflightTests(unittest.TestCase):
    def _validate(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "repository": "Xpounder-com/hormuz",
            "repository_visibility": "public",
            "ref": REF,
            "workflow_ref": WORKFLOW_REF,
            "commit": COMMIT,
            "ref_protected": "true",
            "tag": TAG,
            "package_version": "0.1.0",
            "tag_object_type": "tag",
            "tag_commit": COMMIT,
            "main_contains_commit": True,
            "source_date_epoch": "1787562000",
        }
        values.update(overrides)
        return preflight.validate_release_context(**values)  # type: ignore[arg-type]

    def test_approved_release_identity_is_content_free_and_registry_portable(self) -> None:
        summary = self._validate()

        self.assertEqual(summary["schema_id"], "hormuz.oci-release-preflight")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["artifact"], {
            "contract": "signed_oci_digest",
            "first_publication_registry": "ghcr.io/xpounder-com/hormuz",
            "platform": "linux/amd64",
            "registry_is_product_contract": False,
        })
        self.assertEqual(summary["signing"]["key_management"], "keyless_github_oidc")
        self.assertEqual(summary["signing"]["transparency_log"], "public_rekor")
        self.assertEqual(
            summary["signing"]["workflow_identity"],
            f"https://github.com/{WORKFLOW_REF}",
        )

    def test_repository_workflow_and_ref_are_exact(self) -> None:
        invalid = {
            "repository": "fork/hormuz",
            "ref": "refs/heads/main",
            "workflow_ref": WORKFLOW_REF.replace("release-oci.yml", "other.yml"),
        }
        for field, value in invalid.items():
            with self.subTest(field=field), self.assertRaises(preflight.PreflightError):
                self._validate(**{field: value})

    def test_private_repository_fails_before_public_transparency(self) -> None:
        with self.assertRaisesRegex(preflight.PreflightError, "not_public"):
            self._validate(repository_visibility="private")

    def test_only_protected_annotated_strict_version_tags_are_admitted(self) -> None:
        invalid = {
            "ref_protected": "false",
            "tag_object_type": "commit",
            "tag": "v0.1.0-rc1",
            "package_version": "0.2.0",
        }
        for field, value in invalid.items():
            with self.subTest(field=field), self.assertRaises(preflight.PreflightError):
                self._validate(**{field: value})

    def test_tag_must_resolve_to_the_exact_main_commit(self) -> None:
        with self.assertRaisesRegex(preflight.PreflightError, "tag_commit_mismatch"):
            self._validate(tag_commit="b" * 40)
        with self.assertRaisesRegex(preflight.PreflightError, "not_reachable_from_main"):
            self._validate(main_contains_commit=False)


if __name__ == "__main__":
    unittest.main()
