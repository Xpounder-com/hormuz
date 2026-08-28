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
                    "actor_id": None,
                    "actor_type": "OrganizationAdmin",
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

    def test_candidate_tag_creation_remains_organization_admin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            ruleset_path = root / ".github/rulesets/candidate-tag-creation.json"
            ruleset = json.loads(ruleset_path.read_text(encoding="utf-8"))
            ruleset["bypass_actors"] = [
                {
                    "actor_id": 15368,
                    "actor_type": "Integration",
                    "bypass_mode": "always",
                }
            ]
            ruleset_path.write_text(json.dumps(ruleset), encoding="utf-8")
            with self.assertRaisesRegex(
                RepositoryGovernanceError, "ruleset contract changed"
            ):
                validate_repository_governance(root)

    def test_no_workflow_can_grant_contents_write(self) -> None:
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
                "workflow-issued contents write is forbidden",
            ):
                validate_repository_governance(root)

    def test_candidate_freeze_job_cannot_grant_contents_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "    permissions:\n      contents: read\n",
                    "    permissions:\n      contents: write\n",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow-issued contents write is forbidden",
            ):
                validate_repository_governance(root)

    def test_candidate_publication_cannot_fall_back_to_github_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "GH_TOKEN: ${{ secrets.V1_RELEASE_PUBLISH_TOKEN }}",
                    "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow secret expression contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_release_tokens_must_be_compared_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    (
                        "          RELEASE_TOKENS_SEPARATED: "
                        "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
                        "secrets.V1_RELEASE_PUBLISH_TOKEN }}\n"
                    ),
                    "",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow secret expression contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_secret_checks_cannot_hide_in_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "          PUBLISH_TOKEN_CONFIGURED: ${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}\n",
                    (
                        '          PUBLISH_TOKEN_CONFIGURED: "true"\n'
                        "          # PUBLISH_TOKEN_CONFIGURED: "
                        "${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze credential boundary changed",
            ):
                validate_repository_governance(root)

    def test_candidate_secret_checks_must_be_step_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            value = value.replace(
                (
                    "          PUBLISH_TOKEN_CONFIGURED: "
                    "${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}\n"
                ),
                '          PUBLISH_TOKEN_CONFIGURED: "true"\n',
                1,
            ).replace(
                (
                    "          RELEASE_TOKENS_SEPARATED: "
                    "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
                    "secrets.V1_RELEASE_PUBLISH_TOKEN }}\n"
                ),
                '          RELEASE_TOKENS_SEPARATED: "true"\n',
                1,
            )
            workflow.write_text(
                value.replace(
                    "        run: |\n          umask 077\n",
                    (
                        "        run: |\n"
                        "          cat <<'CREDENTIAL_CONTRACT' >/dev/null\n"
                        "          PUBLISH_TOKEN_CONFIGURED: "
                        "${{ secrets.V1_RELEASE_PUBLISH_TOKEN != '' }}\n"
                        "          RELEASE_TOKENS_SEPARATED: "
                        "${{ secrets.V1_RELEASE_ADMIN_TOKEN != "
                        "secrets.V1_RELEASE_PUBLISH_TOKEN }}\n"
                        "          CREDENTIAL_CONTRACT\n"
                        "          umask 077\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze credential boundary changed",
            ):
                validate_repository_governance(root)

    def test_candidate_credentials_must_remain_in_the_freeze_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8").replace(
                "    environment: v1-release-custody\n", "", 1
            )
            workflow.write_text(
                value.replace(
                    "  freeze:\n",
                    (
                        "  freeze:\n"
                        "    name: Hold the protected custody environment\n"
                        "    needs: authorize\n"
                        "    environment: v1-release-custody\n"
                        "    permissions:\n"
                        "      contents: read\n"
                        "    runs-on: ubuntu-24.04\n"
                        "    steps:\n"
                        "      - name: Preserve the environment boundary\n"
                        "        run: 'true'\n\n"
                        "  publish:\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze job contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_environment_secret_inventory_check_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    (
                        "/environments/v1-release-custody/"
                        "secrets?per_page=100"
                    ),
                    "/actions/secrets?per_page=100",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze credential boundary changed",
            ):
                validate_repository_governance(root)

    def test_candidate_credential_step_cannot_continue_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    (
                        "      - name: Verify distinct environment-scoped "
                        "release credentials\n"
                    ),
                    (
                        "      - name: Verify distinct environment-scoped "
                        "release credentials\n"
                        "        continue-on-error: true\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze credential boundary changed",
            ):
                validate_repository_governance(root)

    def test_candidate_authorization_step_cannot_continue_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    (
                        "      - name: Fail closed unless the configured "
                        "steward initiated this run\n"
                    ),
                    (
                        "      - name: Fail closed unless the configured "
                        "steward initiated this run\n"
                        "        continue-on-error: true\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze authorization changed",
            ):
                validate_repository_governance(root)

    def test_candidate_authorization_job_cannot_continue_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "    timeout-minutes: 2\n    steps:\n",
                    (
                        "    timeout-minutes: 2\n"
                        "    continue-on-error: true\n"
                        "    steps:\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze job contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_freeze_job_cannot_override_the_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "    timeout-minutes: 15\n    steps:\n",
                    (
                        "    timeout-minutes: 15\n"
                        "    defaults:\n"
                        "      run:\n"
                        "        shell: 'bash {0} || true'\n"
                        "    steps:\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "candidate freeze job contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_freeze_workflow_cannot_override_the_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "permissions:\n  contents: read\n\nconcurrency:\n",
                    (
                        "permissions:\n"
                        "  contents: read\n\n"
                        "defaults:\n"
                        "  run:\n"
                        "    shell: 'bash {0} || true'\n\n"
                        "concurrency:\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow top-level contract changed",
            ):
                validate_repository_governance(root)

    def test_candidate_secret_access_rejects_unvalidated_expression_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "      - name: Install the hash-locked source-build frontend and backend\n",
                    (
                        "      - name: Install the hash-locked source-build frontend and backend\n"
                        "        env:\n"
                        "          EXTRA_TOKEN: "
                        "${{ secrets['V1_RELEASE_PUBLISH_TOKEN'] }}\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow secret expression contract changed",
            ):
                validate_repository_governance(root)

    def test_yaml_escaped_secret_cannot_bypass_custody_boundary(self) -> None:
        escaped_expressions = (
            '"${{ sec\\u0072ets.V1_RELEASE_PUBLISH_TOK\\u0045N }}"',
            (
                '"\\u0024\\u007b\\u007b \\u0073ecrets.'
                'V1_RELEASE_PUBLISH_TOKEN \\u007d\\u007d"'
            ),
        )
        for expression in escaped_expressions:
            with self.subTest(expression=expression):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self._copy_contract(root)
                    workflow = root / ".github/workflows/freeze-v1-candidate.yml"
                    value = workflow.read_text(encoding="utf-8")
                    workflow.write_text(
                        value.replace(
                            "      - name: Check out the exact protected main candidate\n",
                            (
                                "      - name: Consume a YAML-escaped secret\n"
                                "        env:\n"
                                f"          EXTRA_TOKEN: {expression}\n"
                                "        run: 'true'\n"
                                "      - name: Check out the exact protected main candidate\n"
                            ),
                            1,
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        RepositoryGovernanceError,
                        "workflow YAML character escapes are unsupported",
                    ):
                        validate_repository_governance(root)

    def test_case_varied_computed_secret_cannot_bypass_custody_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/freeze-v1-candidate.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "      - name: Check out the exact protected main candidate\n",
                    (
                        "      - name: Consume a case-varied computed secret\n"
                        "        env:\n"
                        "          EXTRA_TOKEN: "
                        "${{ SeCrEtS[format('V1_RELEASE_{0}_TOKEN', "
                        "'PUBLISH')] }}\n"
                        "        run: 'true'\n"
                        "      - name: Check out the exact protected main candidate\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow secret expression contract changed",
            ):
                validate_repository_governance(root)

    def test_custody_secrets_cannot_be_referenced_by_another_workflow(self) -> None:
        for secret_name in (
            "V1_RELEASE_ADMIN_TOKEN",
            "V1_RELEASE_PUBLISH_TOKEN",
        ):
            with self.subTest(secret_name=secret_name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self._copy_contract(root)
                    (root / ".github/workflows/rogue-custody.yml").write_text(
                        f"""name: Rogue custody consumer

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  consume:
    permissions:
      contents: read
    environment: v1-release-custody
    runs-on: ubuntu-24.04
    steps:
      - name: Consume custody secret
        env:
          CUSTODY_TOKEN: ${{{{ secrets.{secret_name} }}}}
        run: 'true'
""",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        RepositoryGovernanceError,
                        "custody secret used outside candidate freeze workflow",
                    ):
                        validate_repository_governance(root)

    def test_custody_environment_cannot_be_targeted_by_another_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            (root / ".github/workflows/rogue-custody.yml").write_text(
                """name: Rogue custody environment

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  consume:
    permissions:
      contents: read
    environment: v1-release-custody
    runs-on: ubuntu-24.04
    steps:
      - name: Consume environment
        run: 'true'
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "custody environment used outside candidate freeze workflow",
            ):
                validate_repository_governance(root)

    def test_computed_secret_name_cannot_bypass_custody_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            (root / ".github/workflows/rogue-custody.yml").write_text(
                """name: Computed custody secret

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  consume:
    permissions:
      contents: read
    runs-on: ubuntu-24.04
    steps:
      - name: Consume computed secret
        env:
          CUSTODY_TOKEN: ${{ secrets[format('V1_RELEASE_{0}_TOKEN', 'PUBLISH')] }}
        run: 'true'
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow secret expression contract changed",
            ):
                validate_repository_governance(root)

    def test_computed_environment_name_cannot_bypass_custody_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            (root / ".github/workflows/rogue-custody.yml").write_text(
                """name: Computed custody environment

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  consume:
    permissions:
      contents: read
    environment: ${{ format('v1-release-{0}', 'custody') }}
    runs-on: ubuntu-24.04
    steps:
      - name: Enter computed environment
        run: 'true'
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow environment contract changed",
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
                "workflow-issued contents write is forbidden",
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
                "workflow-issued contents write is forbidden",
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

    def test_escaped_top_level_permission_override_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/upstream-canary.yml"
            value = workflow.read_text(encoding="utf-8")
            workflow.write_text(
                value.replace(
                    "permissions:\n  contents: read\n",
                    'permissions:\n  contents: read\n"permis\\u0073ions": write-all\n',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "unsupported top-level mapping syntax",
            ):
                validate_repository_governance(root)

    def test_nonstandard_job_indentation_cannot_hide_write_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_contract(root)
            workflow = root / ".github/workflows/rogue.yml"
            workflow.write_text(
                """name: Rogue candidate writer

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  poison:
     permissions: write-all
     runs-on: ubuntu-24.04
     steps:
       - run: 'true'
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RepositoryGovernanceError,
                "workflow-issued contents write is forbidden",
            ):
                validate_repository_governance(root)


if __name__ == "__main__":
    unittest.main()
