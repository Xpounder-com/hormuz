from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools import verify_compose_profile as compose_profile
from tools import verify_core_wheel as core_distribution


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_ROOT = ROOT / "deploy" / "compose"


class ComposePreparationTests(unittest.TestCase):
    def test_operator_reload_recreates_file_backed_configuration(self) -> None:
        wrapper = (COMPOSE_ROOT / "hormuz-compose").read_text(encoding="utf-8")
        restart_block = wrapper.split("    restart)", 1)[1].split("        ;;", 1)[0]
        self.assertIn("--force-recreate --no-deps gateway", restart_block)
        self.assertNotIn("compose restart", restart_block)

    def test_logical_restore_keeps_grants_and_is_independent_of_live_counts(self) -> None:
        backup = (COMPOSE_ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
        restore = (COMPOSE_ROOT / "scripts" / "restore-verify.sh").read_text(encoding="utf-8")
        self.assertNotIn("--no-privileges", backup)
        self.assertNotIn("--no-privileges", restore)
        self.assertNotIn("source_events", restore)
        self.assertIn("has_table_privilege", restore)
        self.assertIn("SET ROLE hormuz_runtime", restore)

    def test_source_distribution_prunes_every_generated_runtime_file(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        include_index = manifest.index("recursive-include deploy/compose *.md *.yaml *.json *.sh *.py")
        self.assertIn("include deploy/compose/hormuz-compose", manifest)
        prune_index = manifest.index("prune deploy/compose/runtime")
        self.assertGreater(prune_index, include_index)
        self.assertTrue(
            core_distribution._is_forbidden_archive_path(
                "hormuz-0.1.1/deploy/compose/runtime/hormuz.json"
            )
        )

    def test_prepare_creates_protected_inputs_once_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "compose"
            shutil.copytree(
                COMPOSE_ROOT,
                target,
                ignore=shutil.ignore_patterns("runtime", "__pycache__", "*.pyc"),
            )
            prepare = target / "scripts" / "prepare.sh"
            first = subprocess.run(["/bin/sh", str(prepare)], check=True, capture_output=True, text=True)
            runtime = target / "runtime"
            secrets = runtime / "secrets"
            self.assertIn("prepared protected pilot inputs", first.stdout)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertEqual(secrets.stat().st_mode & 0o777, 0o700)
            self.assertEqual(runtime.stat().st_gid, os.getgid())
            expected = {
                "anthropic-api-key",
                "hormuz-identity-token",
                "hormuz-ingress-credential",
                "openai-api-key",
                "postgres-migration-dsn",
                "postgres-runtime-dsn",
                "postgres-runtime-password",
                "postgres-superuser-password",
            }
            self.assertEqual({path.name for path in secrets.iterdir()}, expected)
            before = {path.name: path.read_bytes() for path in secrets.iterdir()}
            self.assertTrue(all((path.stat().st_mode & 0o777) == 0o640 for path in secrets.iterdir()))
            self.assertTrue(all(path.stat().st_gid == os.getgid() for path in secrets.iterdir()))
            self.assertEqual((runtime / "hormuz.json").stat().st_mode & 0o777, 0o640)

            second = subprocess.run(["/bin/sh", str(prepare)], check=False, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("existing credentials were not changed", second.stderr)
            self.assertEqual({path.name: path.read_bytes() for path in secrets.iterdir()}, before)

    def test_live_proof_preflight_never_removes_an_existing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "tools").mkdir()
            shutil.copy2(ROOT / "tools/verify_compose_profile.sh", target / "tools")
            runtime = target / "deploy/compose/runtime"
            runtime.mkdir(parents=True)
            sentinel = runtime / "operator-owned-sentinel"
            sentinel.write_text("keep", encoding="utf-8")

            result = subprocess.run(
                ["/bin/bash", str(target / "tools/verify_compose_profile.sh")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("set HORMUZ_COMPOSE_PROOF_ACK", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is required to render Compose")
class ComposeProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        version = subprocess.run(
            ["docker", "compose", "version", "--short"],
            check=False,
            capture_output=True,
            text=True,
        )
        if version.returncode != 0:
            raise unittest.SkipTest("Docker Compose is required to render the profile")
        cls.bundled = cls._render("bundled")
        cls.external = cls._render("external")
        cls.bundled_operations = cls._render("bundled-operations")
        cls.external_operations = cls._render("external-operations")
        cls.verification = cls._render("verification")

    @classmethod
    def _render(cls, mode: str) -> dict[str, object]:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(COMPOSE_ROOT),
            "-f",
            str(COMPOSE_ROOT / "compose.yaml"),
        ]
        if mode.startswith("external"):
            command.extend(["-f", str(COMPOSE_ROOT / "compose.external-postgres.yaml")])
        if mode == "verification":
            command.extend(["-f", str(COMPOSE_ROOT / "compose.verify.yaml")])
        if mode.endswith("-operations"):
            command.extend(["--profile", "operations"])
        command.extend(["config", "--format", "json"])
        environment = {
            **os.environ,
            "HORMUZ_COMPOSE_PLATFORM": "linux/amd64",
            "HORMUZ_SECRET_GID": str(os.getgid()),
        }
        result = subprocess.run(command, check=True, capture_output=True, text=True, env=environment)
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise AssertionError("Compose did not render an object")
        return value

    def test_repository_models_satisfy_bundled_and_external_contracts(self) -> None:
        compose_profile.validate_compose_model(self.bundled, mode="bundled")
        compose_profile.validate_compose_model(self.external, mode="external")
        compose_profile.validate_compose_model(self.bundled_operations, mode="bundled-operations")
        compose_profile.validate_compose_model(self.external_operations, mode="external-operations")
        compose_profile.validate_compose_model(self.verification, mode="verification")

        self.assertEqual(set(self.bundled["services"]), {"gateway", "postgres"})
        self.assertEqual(set(self.external["services"]), {"gateway"})
        self.assertEqual(set(self.bundled_operations["services"]), {"gateway", "migrate", "postgres"})
        self.assertEqual(set(self.external_operations["services"]), {"gateway", "migrate"})
        self.assertEqual(set(self.verification["services"]), {"fake-provider", "gateway", "postgres"})
        self.assertEqual(self.bundled["services"]["gateway"]["image"], self.external["services"]["gateway"]["image"])

    def test_operator_has_database_credentials_only(self) -> None:
        migration = self.bundled_operations["services"]["migrate"]
        self.assertEqual(
            {item["source"] for item in migration["secrets"]},
            {"postgres_runtime_dsn", "postgres_migration_dsn"},
        )
        self.assertNotIn("postgres_migration_dsn", {
            item["source"] for item in self.bundled["services"]["gateway"]["secrets"]
        })

        provider_secret = copy.deepcopy(self.bundled_operations)
        provider_secret["services"]["migrate"]["secrets"].append(
            {"source": "openai_api_key", "target": "/run/secrets/openai_api_key"}
        )
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "migration_secret_mount"):
            compose_profile.validate_compose_model(provider_secret, mode="bundled-operations")

    def test_mutable_images_and_public_database_ports_fail_closed(self) -> None:
        mutable = copy.deepcopy(self.bundled)
        mutable["services"]["gateway"]["image"] = "ghcr.io/xpounder-com/hormuz:v0.1.1"
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "gateway_image"):
            compose_profile.validate_compose_model(mutable, mode="bundled")

        published_database = copy.deepcopy(self.bundled)
        published_database["services"]["postgres"]["ports"] = [
            {"target": 5432, "published": "5432", "host_ip": "0.0.0.0"}
        ]
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_port"):
            compose_profile.validate_compose_model(published_database, mode="bundled")

    def test_gateway_privilege_and_secret_boundaries_fail_closed(self) -> None:
        privileged = copy.deepcopy(self.bundled)
        privileged["services"]["gateway"]["read_only"] = False
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "runtime_identity"):
            compose_profile.validate_compose_model(privileged, mode="bundled")

        ungrouped = copy.deepcopy(self.bundled)
        ungrouped["services"]["gateway"]["group_add"] = []
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "protected_file_group"):
            compose_profile.validate_compose_model(ungrouped, mode="bundled")

        added_capability = copy.deepcopy(self.bundled)
        added_capability["services"]["gateway"]["cap_add"] = ["SYS_ADMIN"]
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "cap_add"):
            compose_profile.validate_compose_model(added_capability, mode="bundled")

        migration_secret = copy.deepcopy(self.bundled)
        migration_secret["services"]["gateway"]["secrets"].append(
            {"source": "postgres_migration_dsn", "target": "/run/secrets/postgres_migration_dsn"}
        )
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "secret_mount"):
            compose_profile.validate_compose_model(migration_secret, mode="bundled")

    def test_postgres_private_bootstrap_and_completed_initialization_fail_closed(self) -> None:
        forced_user = copy.deepcopy(self.bundled)
        forced_user["services"]["postgres"]["user"] = "0:0"
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_startup_identity"):
            compose_profile.validate_compose_model(forced_user, mode="bundled")

        grouped = copy.deepcopy(self.bundled)
        grouped["services"]["postgres"]["group_add"] = [str(os.getgid())]
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_supplemental_group"):
            compose_profile.validate_compose_model(grouped, mode="bundled")

        direct_secret = copy.deepcopy(self.bundled)
        direct_secret["services"]["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"] = (
            "/run/secrets/postgres_superuser_password"
        )
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_environment"):
            compose_profile.validate_compose_model(direct_secret, mode="bundled")

        missing_command = copy.deepcopy(self.bundled)
        missing_command["services"]["postgres"]["command"] = None
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_command"):
            compose_profile.validate_compose_model(missing_command, mode="bundled")

        early_health = copy.deepcopy(self.bundled)
        early_health["services"]["postgres"]["healthcheck"]["test"] = [
            "CMD-SHELL",
            "pg_isready --username postgres --dbname hormuz",
        ]
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "postgres_healthcheck"):
            compose_profile.validate_compose_model(early_health, mode="bundled")

    def test_secret_sentinel_cannot_appear_in_rendered_model(self) -> None:
        leaked = copy.deepcopy(self.bundled)
        leaked["services"]["gateway"]["environment"]["OPENAI_API_KEY"] = "proof-secret-sentinel"
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "contains_secret"):
            compose_profile.validate_compose_model(
                leaked,
                mode="bundled",
                secret_values=("proof-secret-sentinel",),
            )

    def test_content_free_live_evidence_schema_is_strict(self) -> None:
        evidence = compose_profile.build_evidence(
            os_version="24.04",
            docker_engine="29.0.1",
            docker_compose="2.40.3",
            hormuz_repo_digest=compose_profile.HORMUZ_IMAGE.rsplit("@", 1)[1],
            hormuz_image_id="sha256:" + "2" * 64,
            postgres_repo_digest=compose_profile.POSTGRES_IMAGE.rsplit("@", 1)[1],
            postgres_image_id="sha256:" + "4" * 64,
            requests_before_restart=1,
            requests_after_restart=4,
            usage_events_before_restart=1,
            usage_events_at_backup=4,
            usage_events_after_backup=5,
            restored_usage_events=4,
        )
        compose_profile.validate_evidence(evidence)
        self.assertEqual(evidence["verdict"], "verified_single_vm_pilot_reference")
        self.assertIsNotNone(datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00")))

        incomplete = copy.deepcopy(evidence)
        incomplete["checks"]["backup_restore"] = False
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "checks_incomplete"):
            compose_profile.validate_evidence(incomplete)

        wrong_digest = copy.deepcopy(evidence)
        wrong_digest["images"]["hormuz"]["repo_digest"] = "sha256:" + "1" * 64
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "repo_digest_mismatch"):
            compose_profile.validate_evidence(wrong_digest)

        unsafe = copy.deepcopy(evidence)
        unsafe["runner"]["os_version"] = "/home/runner/private"
        with self.assertRaisesRegex(compose_profile.ComposeProfileError, "forbidden_content"):
            compose_profile.validate_evidence(unsafe)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            compose_profile.write_evidence(output, evidence)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            compose_profile.validate_evidence(compose_profile.load_json(output))
            with self.assertRaisesRegex(compose_profile.ComposeProfileError, "output_exists"):
                compose_profile.write_evidence(output, evidence)


if __name__ == "__main__":
    unittest.main()
