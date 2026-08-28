from __future__ import annotations

import hashlib
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "Xpounder-com/hormuz"
TAG = "candidate-v1.0.0-" + "a" * 64
DRAFT_NAMESPACE = "untagged-c06969a150533862d8b2"


def _publisher_namespace() -> dict[str, object]:
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/freeze-v1-candidate.yml"
    ).read_text(encoding="utf-8")
    source = workflow.split("# BEGIN V1 CANDIDATE PUBLISHER", 1)[1].split(
        "# END V1 CANDIDATE PUBLISHER", 1
    )[0]
    namespace: dict[str, object] = {"__name__": "v1_candidate_publisher_test"}
    exec(
        compile(
            textwrap.dedent(source),
            ".github/workflows/freeze-v1-candidate.yml#publisher",
            "exec",
        ),
        namespace,
    )
    return namespace


class V1CandidatePublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publisher = _publisher_namespace()

    def _asset(
        self,
        *,
        asset_id: int,
        name: str,
        label: str,
        payload: bytes,
        namespace: str,
    ) -> dict[str, object]:
        return {
            "id": asset_id,
            "name": name,
            "label": label,
            "state": "uploaded",
            "size": len(payload),
            "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
            "url": (
                f"https://api.github.com/repos/{REPOSITORY}/"
                f"releases/assets/{asset_id}"
            ),
            "browser_download_url": (
                f"https://github.com/{REPOSITORY}/releases/download/"
                f"{namespace}/{name}"
            ),
        }

    def test_draft_asset_uses_exact_untagged_release_namespace(self) -> None:
        release_download_namespace = self.publisher["release_download_namespace"]
        validate_asset = self.publisher["validate_asset"]
        draft_release = {
            "html_url": (
                f"https://github.com/{REPOSITORY}/releases/tag/"
                f"{DRAFT_NAMESPACE}"
            )
        }
        namespace = release_download_namespace(
            draft_release,
            tag=TAG,
            draft=True,
        )
        self.assertEqual(namespace, DRAFT_NAMESPACE)

        payload = b"frozen source archive"
        asset = self._asset(
            asset_id=101,
            name="hormuz-1.0.0.tar.gz",
            label="Frozen Hormuz v1.0.0 source archive",
            payload=payload,
            namespace=DRAFT_NAMESPACE,
        )
        self.assertEqual(
            validate_asset(
                asset,
                name="hormuz-1.0.0.tar.gz",
                label="Frozen Hormuz v1.0.0 source archive",
                payload=payload,
                download_namespace=namespace,
            ),
            101,
        )

    def test_draft_asset_rejects_another_untagged_release_namespace(self) -> None:
        contract_error = self.publisher["ContractError"]
        validate_asset = self.publisher["validate_asset"]
        payload = b"frozen source archive"
        asset = self._asset(
            asset_id=102,
            name="hormuz-1.0.0.tar.gz",
            label="Frozen Hormuz v1.0.0 source archive",
            payload=payload,
            namespace="untagged-deadbeefdeadbeefdead",
        )
        with self.assertRaisesRegex(contract_error, "release_asset_invalid"):
            validate_asset(
                asset,
                name="hormuz-1.0.0.tar.gz",
                label="Frozen Hormuz v1.0.0 source archive",
                payload=payload,
                download_namespace=DRAFT_NAMESPACE,
            )

    def test_published_release_requires_digest_addressed_tag_namespace(self) -> None:
        contract_error = self.publisher["ContractError"]
        release_download_namespace = self.publisher["release_download_namespace"]
        published_release = {
            "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{TAG}"
        }
        self.assertEqual(
            release_download_namespace(
                published_release,
                tag=TAG,
                draft=False,
            ),
            TAG,
        )
        with self.assertRaisesRegex(contract_error, "published_release_url_invalid"):
            release_download_namespace(
                {
                    "html_url": (
                        f"https://github.com/{REPOSITORY}/releases/tag/"
                        f"{DRAFT_NAMESPACE}"
                    )
                },
                tag=TAG,
                draft=False,
            )


if __name__ == "__main__":
    unittest.main()
