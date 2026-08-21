import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts.release_contract import (
    EXPECTED_IMAGE,
    EXPECTED_OIDC_ISSUER,
    EXPECTED_REPOSITORY,
    ReleaseContractError,
    parse_digest,
    render_evidence,
    render_notes,
    render_slsa_predicate,
    validate_image_inspection,
    validate_package,
    validate_release,
    validate_slsa_verification,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "7" * 40
DIGEST = "sha256:" + "a" * 64


class ReleaseContractTests(unittest.TestCase):
    def test_release_contract_binds_tag_version_repository_and_identity(self) -> None:
        contract = validate_release(
            tag="v0.1.0",
            ref="refs/tags/v0.1.0",
            sha=SHA,
            repository=EXPECTED_REPOSITORY,
            event="push",
            project_file=ROOT / "pyproject.toml",
        )
        self.assertEqual(contract["schema"], "hormuz.release-contract.v1")
        self.assertEqual(contract["version"], "0.1.0")
        self.assertEqual(contract["image"], EXPECTED_IMAGE)
        self.assertEqual(contract["version_tag"], f"{EXPECTED_IMAGE}:0.1.0")
        self.assertEqual(contract["revision_tag"], f"{EXPECTED_IMAGE}:sha-{SHA}")
        self.assertEqual(contract["oidc_issuer"], EXPECTED_OIDC_ISSUER)
        self.assertEqual(
            contract["signing_identity"],
            "https://github.com/Xpounder-com/hormuz/"
            ".github/workflows/release.yml@refs/tags/v0.1.0",
        )

    def test_release_contract_fails_closed_on_untrusted_trigger_inputs(self) -> None:
        valid = {
            "tag": "v0.1.0",
            "ref": "refs/tags/v0.1.0",
            "sha": SHA,
            "repository": EXPECTED_REPOSITORY,
            "event": "push",
            "project_file": ROOT / "pyproject.toml",
        }
        cases = {
            "loose tag": {"tag": "v0.1"},
            "prerelease tag": {"tag": "v0.1.0-rc.1"},
            "mismatched ref": {"ref": "refs/heads/main"},
            "short sha": {"sha": "7be6ce7"},
            "other repository": {"repository": "personal/hormuz"},
            "manual event": {"event": "workflow_dispatch"},
        }
        for name, changes in cases.items():
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                validate_release(**(valid | changes))

        with self.assertRaisesRegex(ReleaseContractError, "GitHub-hosted"):
            validate_release(
                **valid,
                runner_environment="self-hosted",
                deny_self_hosted_runners=True,
            )

    def test_release_contract_rejects_tag_package_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_file = Path(temporary) / "pyproject.toml"
            project_file.write_text('[project]\nversion = "0.2.0"\n')
            with self.assertRaisesRegex(ReleaseContractError, "does not match"):
                validate_release(
                    tag="v0.1.0",
                    ref="refs/tags/v0.1.0",
                    sha=SHA,
                    repository=EXPECTED_REPOSITORY,
                    event="push",
                    project_file=project_file,
                )

    def test_git_release_requires_annotated_tag_on_main_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*arguments: str) -> str:
                result = subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip()

            git("init", "--initial-branch=main")
            git("config", "user.name", "Hormuz Release Test")
            git("config", "user.email", "release-test@invalid.example")
            project_file = root / "pyproject.toml"
            project_file.write_text('[project]\nversion = "0.1.0"\n')
            git("add", "pyproject.toml")
            git("commit", "-m", "release source")
            sha = git("rev-parse", "HEAD")
            git("tag", "-a", "v0.1.0", "-m", "Hormuz 0.1.0")

            contract = validate_release(
                tag="v0.1.0",
                ref="refs/tags/v0.1.0",
                sha=sha,
                repository=EXPECTED_REPOSITORY,
                event="push",
                project_file=project_file,
                main_ref="main",
            )
            self.assertEqual(contract["commit_sha"], sha)

            git("tag", "--delete", "v0.1.0")
            git("tag", "v0.1.0")
            with self.assertRaisesRegex(ReleaseContractError, "must be annotated"):
                validate_release(
                    tag="v0.1.0",
                    ref="refs/tags/v0.1.0",
                    sha=sha,
                    repository=EXPECTED_REPOSITORY,
                    event="push",
                    project_file=project_file,
                    main_ref="main",
                )

            git("tag", "--delete", "v0.1.0")
            git("switch", "--orphan", "unmerged-release")
            project_file.write_text('[project]\nversion = "0.1.0"\n')
            git("add", "pyproject.toml")
            git("commit", "-m", "unmerged release source")
            unmerged_sha = git("rev-parse", "HEAD")
            git("tag", "-a", "v0.1.0", "-m", "Unmerged Hormuz 0.1.0")
            with self.assertRaisesRegex(ReleaseContractError, "not reachable"):
                validate_release(
                    tag="v0.1.0",
                    ref="refs/tags/v0.1.0",
                    sha=unmerged_sha,
                    repository=EXPECTED_REPOSITORY,
                    event="push",
                    project_file=project_file,
                    main_ref="main",
                )

    def test_package_must_be_private_and_linked_to_the_repository(self) -> None:
        value = {
            "name": "hormuz",
            "package_type": "container",
            "visibility": "private",
            "repository": {"full_name": EXPECTED_REPOSITORY},
        }
        self.assertEqual(validate_package(value)["visibility"], "private")
        for mutation in (
            {"visibility": "public"},
            {"package_type": "npm"},
            {"name": "other"},
            {"repository": None},
            {"repository": {"full_name": "personal/hormuz"}},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(
                ReleaseContractError
            ):
                validate_package(value | mutation)

    def test_registry_digest_parser_requires_one_exact_digest(self) -> None:
        self.assertEqual(parse_digest(f'"{DIGEST}"\n'), DIGEST)
        self.assertEqual(parse_digest(f"Digest: {DIGEST}\nDigest: {DIGEST}"), DIGEST)
        with self.assertRaises(ReleaseContractError):
            parse_digest("no digest")
        with self.assertRaises(ReleaseContractError):
            parse_digest(f"{DIGEST}\nsha256:{'b' * 64}")

    def test_release_image_metadata_is_bound_to_source_version_and_revision(self) -> None:
        inspection = [
            {
                "Architecture": "amd64",
                "Os": "linux",
                "Config": {
                    "User": "65532:65532",
                    "Labels": {
                        "org.opencontainers.image.source": (
                            "https://github.com/Xpounder-com/hormuz"
                        ),
                        "org.opencontainers.image.revision": SHA,
                        "org.opencontainers.image.version": "0.1.0",
                    },
                },
            }
        ]
        result = validate_image_inspection(
            inspection,
            version="0.1.0",
            sha=SHA,
        )
        self.assertEqual(result["platform"], "linux/amd64")
        for mutation in (
            {"Architecture": "arm64"},
            {"Config": inspection[0]["Config"] | {"User": "0:0"}},
            {
                "Config": inspection[0]["Config"]
                | {
                    "Labels": inspection[0]["Config"]["Labels"]
                    | {"org.opencontainers.image.revision": "8" * 40}
                }
            },
        ):
            with self.subTest(mutation=mutation), self.assertRaises(
                ReleaseContractError
            ):
                validate_image_inspection(
                    [inspection[0] | mutation],
                    version="0.1.0",
                    sha=SHA,
                )

    def test_content_free_release_evidence_uses_the_immutable_digest(self) -> None:
        contract = validate_release(
            tag="v0.1.0",
            ref="refs/tags/v0.1.0",
            sha=SHA,
            repository=EXPECTED_REPOSITORY,
            event="push",
            project_file=ROOT / "pyproject.toml",
        )
        package = validate_package(
            {
                "name": "hormuz",
                "package_type": "container",
                "visibility": "private",
                "repository": {"full_name": EXPECTED_REPOSITORY},
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cosign = root / "cosign.json"
            provenance = root / "provenance.json"
            provenance_validation = root / "provenance-validation.json"
            cosign.write_text(json.dumps([{"verified": True}]))
            provenance.write_text(json.dumps([{"verified": True}]))
            provenance_validation.write_text(
                json.dumps(
                    {
                        "schema": "hormuz.slsa-verification.v1",
                        "predicate_type": "https://slsa.dev/provenance/v1",
                        "subject": f"{EXPECTED_IMAGE}@{DIGEST}",
                        "source_ref": "refs/tags/v0.1.0",
                        "source_commit": SHA,
                        "builder": contract["signing_identity"],
                    }
                )
            )
            evidence = render_evidence(
                contract=contract,
                package=package,
                digest=DIGEST,
                workflow_run_url=(
                    "https://github.com/Xpounder-com/hormuz/actions/runs/123"
                ),
                workflow_run_id="123",
                workflow_run_attempt="2",
                cosign_version="GitVersion: v3.0.6",
                cosign_verification=cosign,
                provenance_verification=provenance,
                provenance_validation=provenance_validation,
            )
        self.assertEqual(evidence["immutable_image"], f"{EXPECTED_IMAGE}@{DIGEST}")
        self.assertTrue(evidence["signature"]["verified"])
        self.assertTrue(evidence["provenance"]["verified"])
        serialized = json.dumps(evidence)
        for forbidden_value in (
            "sk-live-example",
            "employee@example.com",
            "customer source text",
        ):
            self.assertNotIn(forbidden_value, serialized.lower())

        notes = render_notes(evidence)
        self.assertIn(f"'{EXPECTED_IMAGE}@{DIGEST}'", notes)
        self.assertIn("verify-attestation", notes)
        self.assertIn("single-node SQLite", notes)

    def test_slsa_predicate_and_verified_statement_bind_release_source(self) -> None:
        contract = validate_release(
            tag="v0.1.0",
            ref="refs/tags/v0.1.0",
            sha=SHA,
            repository=EXPECTED_REPOSITORY,
            event="push",
            project_file=ROOT / "pyproject.toml",
        )
        predicate = render_slsa_predicate(
            contract=contract,
            dockerfile=ROOT / "Dockerfile",
            dependency_locks=(
                ROOT / "deploy/container/requirements.lock",
                ROOT / "deploy/postgres/requirements.lock",
            ),
            workflow_run_url=(
                "https://github.com/Xpounder-com/hormuz/actions/runs/123"
            ),
            workflow_run_id="123",
            workflow_run_attempt="1",
        )
        parameters = predicate["buildDefinition"]["externalParameters"]
        self.assertEqual(parameters["ref"], "refs/tags/v0.1.0")
        self.assertEqual(parameters["commit"], SHA)
        self.assertEqual(
            parameters["platforms"], ["linux/amd64", "linux/arm64"]
        )
        self.assertEqual(
            parameters["dependencyLocks"],
            [
                "deploy/container/requirements.lock",
                "deploy/postgres/requirements.lock",
            ],
        )
        dependencies = predicate["buildDefinition"]["resolvedDependencies"]
        self.assertEqual(dependencies[0]["digest"], {"gitCommit": SHA})
        self.assertRegex(dependencies[1]["digest"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(dependencies[2]["digest"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(dependencies[3]["digest"]["sha256"], r"^[0-9a-f]{64}$")

        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": EXPECTED_IMAGE, "digest": {"sha256": "a" * 64}}
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": predicate,
        }
        record = {
            "payload": base64.b64encode(
                json.dumps(statement, separators=(",", ":")).encode()
            ).decode()
        }
        verified = validate_slsa_verification(
            [record],
            predicate=predicate,
            image=EXPECTED_IMAGE,
            digest=DIGEST,
        )
        self.assertEqual(verified["source_commit"], SHA)
        self.assertEqual(verified["source_ref"], "refs/tags/v0.1.0")

        changed = json.loads(json.dumps(predicate))
        changed["buildDefinition"]["externalParameters"]["commit"] = "8" * 40
        with self.assertRaises(ReleaseContractError):
            validate_slsa_verification(
                [record],
                predicate=changed,
                image=EXPECTED_IMAGE,
                digest=DIGEST,
            )

    def test_release_workflow_is_tag_only_pinned_and_least_privilege(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertRegex(workflow, r'(?m)^\s+tags:\n\s+- "v\*"$')
        self.assertNotRegex(workflow, r"(?m)^\s+branches:")
        self.assertIn("permissions: {}", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertNotIn("attestations: write", workflow)
        self.assertIn("--main-ref origin/main", workflow)
        self.assertIn("--deny-self-hosted-runners", workflow)
        self.assertIn("validate-package", workflow)
        self.assertIn("cosign sign --yes", workflow)
        self.assertIn("cosign attest --yes", workflow)
        self.assertIn("cosign verify-attestation", workflow)
        self.assertIn("push-by-digest=true", workflow)
        self.assertLess(
            workflow.index("validate-package"),
            workflow.index("Promote verified digest"),
        )
        self.assertIn("HORMUZ_SIGSTORE_RELEASE_APPROVAL", workflow)
        self.assertIn("sigstore-public-transparency-v1", workflow)
        self.assertIn("python scripts/reproducible_build.py", workflow)
        self.assertIn('--source-sha "$GITHUB_SHA"', workflow)
        self.assertIn("deploy/build/requirements.lock", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("--only-binary=:all:", workflow)
        self.assertNotRegex(
            workflow,
            r"(?m)^\s*run:\s+.*--only-binary=:all:",
        )
        self.assertIn("--no-build-isolation --no-deps --editable .", workflow)
        self.assertNotIn("build==1.3.0", workflow)
        self.assertNotIn("run: python -m build", workflow)
        self.assertNotIn(f"{EXPECTED_IMAGE}:latest", workflow)
        action_uses = re.findall(r"(?m)^\s+uses:\s+([^\s#]+)", workflow)
        self.assertTrue(action_uses)
        for action in action_uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_ci_package_job_uses_the_exact_source_reproducibility_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        builder = (ROOT / "scripts/reproducible_build.py").read_text()
        self.assertIn("python scripts/reproducible_build.py", workflow)
        self.assertIn('--source-sha "$GITHUB_SHA"', workflow)
        self.assertIn("--outdir dist", workflow)
        self.assertGreaterEqual(workflow.count("deploy/build/requirements.lock"), 4)
        self.assertGreaterEqual(workflow.count("--require-hashes"), 4)
        self.assertGreaterEqual(workflow.count("--only-binary=:all:"), 4)
        self.assertNotRegex(
            workflow,
            r"(?m)^\s*run:\s+.*--only-binary=:all:",
        )
        self.assertEqual(
            workflow.count("--no-build-isolation --no-deps --editable ."),
            3,
        )
        self.assertNotIn("pip install build==", workflow)
        self.assertNotIn("run: python -m build", workflow)
        self.assertIn("path: dist/*", workflow)
        self.assertIn('"--no-isolation"', builder)
        self.assertIn("sys.executable", builder)
        self.assertNotIn('add_argument("--python"', builder)
        self.assertIn("source revision is not the checked-out commit", builder)

    def test_ci_uses_the_hash_locked_runtime_closure_without_resolution(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertEqual(
            workflow.count("Install hash-locked runtime dependencies"),
            2,
        )
        self.assertEqual(
            workflow.count("--requirement deploy/container/requirements.lock"),
            4,
        )
        self.assertEqual(
            workflow.count("--no-build-isolation --no-deps --editable ."),
            3,
        )
        self.assertEqual(
            workflow.count("--requirement deploy/postgres/requirements.lock"),
            1,
        )
        self.assertNotIn("--no-build-isolation --editable .", workflow)
        self.assertIn(
            '"$RUNNER_TEMP/hormuz-wheel/bin/pip" install '
            "--require-hashes --only-binary=:all: \\",
            workflow,
        )
        self.assertIn(
            '"$RUNNER_TEMP/hormuz-wheel/bin/pip" install --no-deps '
            "dist/hormuz-*.whl",
            workflow,
        )
        self.assertEqual(workflow.count("python -m pip check"), 2)
        self.assertIn('"$RUNNER_TEMP/hormuz-wheel/bin/pip" check', workflow)

    def test_release_and_canary_use_the_hash_locked_runtime_closure(self) -> None:
        paths = {
            ROOT / ".github/workflows/release.yml": 2,
            ROOT / ".github/workflows/upstream-canary.yml": 1,
        }
        for path, expected_count in paths.items():
            with self.subTest(path=path.name):
                workflow = path.read_text()
                self.assertEqual(
                    workflow.count("Install hash-locked runtime dependencies"),
                    expected_count,
                )
                self.assertEqual(
                    workflow.count(
                        "--requirement deploy/container/requirements.lock"
                    ),
                    expected_count,
                )
                self.assertIn(
                    "--no-build-isolation --no-deps --editable .",
                    workflow,
                )
                self.assertNotIn("--no-build-isolation --editable .", workflow)
                self.assertIn("python -m pip check", workflow)

    def test_pinned_official_clients_have_an_integrity_locked_closure(self) -> None:
        package_file = ROOT / "deploy/clients/package.json"
        lock_file = ROOT / "deploy/clients/package-lock.json"
        self.assertTrue(package_file.is_file(), "pinned client package manifest is missing")
        self.assertTrue(lock_file.is_file(), "pinned client lockfile is missing")

        package = json.loads(package_file.read_text())
        lock = json.loads(lock_file.read_text())
        expected = {
            "@anthropic-ai/claude-code": "2.1.233",
            "@openai/codex": "0.147.0",
        }
        self.assertIs(package.get("private"), True)
        self.assertEqual(package.get("packageManager"), "npm@11.17.0")
        self.assertEqual(package.get("engines"), {"node": "24.19.0"})
        self.assertEqual(package.get("dependencies"), expected)
        self.assertEqual(lock.get("lockfileVersion"), 3)
        self.assertEqual(lock["packages"][""]["dependencies"], expected)
        self.assertIn("node_modules/@openai/codex-linux-x64", lock["packages"])
        self.assertIn(
            "node_modules/@anthropic-ai/claude-code-linux-x64",
            lock["packages"],
        )
        for path, record in lock["packages"].items():
            if not path:
                continue
            with self.subTest(path=path):
                self.assertRegex(record["integrity"], r"^sha512-[A-Za-z0-9+/]+={0,2}$")
                self.assertRegex(
                    record["resolved"],
                    r"^https://registry\.npmjs\.org/.+\.tgz$",
                )
        self.assertEqual(
            (ROOT / "deploy/clients/.npmrc").read_text().splitlines(),
            [
                "audit=false",
                "engine-strict=true",
                "fund=false",
                "ignore-scripts=true",
                "package-lock=true",
            ],
        )
        dependabot = (ROOT / ".github/dependabot.yml").read_text()
        self.assertIn("package-ecosystem: npm", dependabot)
        self.assertIn('directory: "/deploy/clients"', dependabot)

    def test_ci_and_release_use_the_pinned_client_lock(self) -> None:
        for path in (
            ROOT / ".github/workflows/ci.yml",
            ROOT / ".github/workflows/release.yml",
        ):
            with self.subTest(path=path.name):
                workflow = path.read_text()
                self.assertIn("npm ci --prefix deploy/clients", workflow)
                self.assertIn("--ignore-scripts --no-audit --no-fund", workflow)
                self.assertIn("deploy/clients/package-lock.json", workflow)
                self.assertIn("deploy/clients/node_modules/.bin", workflow)
                self.assertIn("python scripts/client_lock_contract.py", workflow)
                self.assertIn(
                    "node deploy/clients/node_modules/"
                    "@anthropic-ai/claude-code/install.cjs",
                    workflow,
                )
                self.assertIn("cache: npm", workflow)
                self.assertIn("24.19.0", workflow)
                self.assertIn('test "$(node --version)" = "v24.19.0"', workflow)
                self.assertIn('test "$(npm --version)" = "11.17.0"', workflow)
                self.assertNotIn("--no-package-lock", workflow)
                self.assertNotIn("@openai/codex@$CODEX_VERSION", workflow)
                self.assertNotIn(
                    "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION",
                    workflow,
                )
        release = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("client-compatibility:", release)
        self.assertIn("needs: [verify, client-compatibility]", release)
        for path in (
            ROOT / ".github/workflows/ci.yml",
            ROOT / ".github/workflows/release.yml",
            ROOT / ".github/workflows/upstream-canary.yml",
        ):
            with self.subTest(path=path.name):
                workflow = path.read_text()
                self.assertIn("test_installed_codex_reloads_auth_helper_after_401", workflow)
                self.assertIn("test_official_claude_code_reloads_auth_helper_after_401", workflow)

    def test_latest_upstream_client_canary_remains_explicitly_dynamic(self) -> None:
        workflow = (ROOT / ".github/workflows/upstream-canary.yml").read_text()
        self.assertIn("@openai/codex@latest", workflow)
        self.assertIn("@anthropic-ai/claude-code@latest", workflow)
        self.assertIn("--no-package-lock --no-save", workflow)
        self.assertNotIn("npm ci --prefix deploy/clients", workflow)

    def test_upstream_canary_uses_the_hash_locked_build_toolchain(self) -> None:
        workflow = (ROOT / ".github/workflows/upstream-canary.yml").read_text()
        self.assertIn("deploy/build/requirements.lock", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("--only-binary=:all:", workflow)
        self.assertNotRegex(
            workflow,
            r"(?m)^\s*run:\s+.*--only-binary=:all:",
        )
        self.assertIn("--no-build-isolation --no-deps --editable .", workflow)


if __name__ == "__main__":
    unittest.main()
