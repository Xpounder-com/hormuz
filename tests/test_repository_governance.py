from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.verify_repository_governance import (
    RepositoryGovernanceError,
    validate_repository_governance,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class RepositoryGovernanceTests(unittest.TestCase):
    def _copy_contract(self, target: Path) -> None:
        shutil.copytree(REPOSITORY_ROOT / ".github", target / ".github")

    def test_repository_governance_contract_passes(self) -> None:
        result = validate_repository_governance(REPOSITORY_ROOT)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["repository"], "Xpounder-com/hormuz")
        self.assertEqual(result["ruleset_count"], 4)
        self.assertEqual(result["required_check_count"], 11)
        self.assertGreaterEqual(result["workflow_count"], 4)
        self.assertGreater(result["pinned_action_use_count"], 0)
        self.assertEqual(result["public_transition_check_count"], 10)

        immutable_ruleset = json.loads(
            (
                REPOSITORY_ROOT
                / ".github/rulesets/version-tag-immutability.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            immutable_ruleset["conditions"]["ref_name"]["include"],
            ["refs/tags/v*", "refs/tags/candidate-v1.0.0-*"],
        )

        creation_ruleset = json.loads(
            (
                REPOSITORY_ROOT / ".github/rulesets/version-tag-creation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            creation_ruleset["conditions"]["ref_name"]["include"],
            ["refs/tags/v*"],
        )

        candidate_creation_ruleset = json.loads(
            (
                REPOSITORY_ROOT
                / ".github/rulesets/candidate-tag-creation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            candidate_creation_ruleset["conditions"]["ref_name"]["include"],
            ["refs/tags/candidate-v1.0.0-*"],
        )
        self.assertEqual(
            candidate_creation_ruleset["bypass_actors"],
            [
                {
                    "actor_id": 15368,
                    "actor_type": "Integration",
                    "bypass_mode": "always",
                }
            ],
        )

    def test_unpinned_action_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/ci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                    "actions/checkout@v7",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "unpinned external Action"
            ):
                validate_repository_governance(root)

    def test_pull_request_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/ci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace("  pull_request:\n", "  pull_request_target:\n", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "public-fork-unsafe"
            ):
                validate_repository_governance(root)

    def test_duplicate_feature_branch_ci_trigger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/ci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "  push:\n    branches:\n      - main\n",
                    "  push:\n",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "once for pull requests"
            ):
                validate_repository_governance(root)

    def test_required_check_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            manifest_path = root / ".github/repository-governance-v1.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["required_checks"].pop()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "required CI check set changed"
            ):
                validate_repository_governance(root)

    def test_version_tag_immutability_cannot_gain_a_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            ruleset_path = (
                root / ".github/rulesets/version-tag-immutability.json"
            )
            ruleset = json.loads(ruleset_path.read_text(encoding="utf-8"))
            ruleset["bypass_actors"] = [
                {
                    "actor_id": None,
                    "actor_type": "OrganizationAdmin",
                    "bypass_mode": "always",
                }
            ]
            ruleset_path.write_text(json.dumps(ruleset), encoding="utf-8")
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "ruleset contract changed"
            ):
                validate_repository_governance(root)

    def test_candidate_tag_creation_cannot_gain_a_human_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            ruleset_path = root / ".github/rulesets/candidate-tag-creation.json"
            ruleset = json.loads(ruleset_path.read_text(encoding="utf-8"))
            ruleset["bypass_actors"] = [
                {
                    "actor_id": None,
                    "actor_type": "OrganizationAdmin",
                    "bypass_mode": "always",
                }
            ]
            ruleset_path.write_text(json.dumps(ruleset), encoding="utf-8")
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "ruleset contract changed"
            ):
                validate_repository_governance(root)

    def test_only_candidate_freeze_workflow_can_write_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/release-oci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace("  contents: read\n", "  contents: write\n", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "only the steward-gated candidate freeze job",
            ):
                validate_repository_governance(root)

    def test_non_freeze_job_cannot_gain_contents_write_via_write_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/release-oci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "  release:\n",
                    "  release:\n    permissions: write-all\n",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "only the steward-gated candidate freeze job",
            ):
                validate_repository_governance(root)

    def test_non_freeze_workflow_cannot_inherit_top_level_write_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/upstream-canary.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "permissions:\n  contents: read\n",
                    "permissions: write-all\n",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "only the steward-gated candidate freeze job",
            ):
                validate_repository_governance(root)

    def test_flow_style_permission_grant_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/upstream-canary.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "permissions:\n  contents: read\n",
                    "permissions: {contents: write}\n",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "unsupported YAML syntax",
            ):
                validate_repository_governance(root)

    def test_escaped_job_permission_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/release-oci.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "  release:\n",
                    '  release:\n    "permis\\u0073ions": write-all\n',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "unsupported mapping syntax",
            ):
                validate_repository_governance(root)


if __name__ == "__main__":
    unittest.main()
