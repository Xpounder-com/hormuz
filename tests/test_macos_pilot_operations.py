from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import macos_pilot_operations as operations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/macos_pilot/complete-synthetic-v1.json"
CODEX_FIXTURE = (
    ROOT / "tests/fixtures/macos_pilot/complete-synthetic-codex-openai-v2.json"
)
SOURCE = "a" * 40
PREVIOUS_SOURCE = "b" * 40
GATEWAY_RUN_URL = "https://github.com/Xpounder-com/hormuz/actions/runs/3"
OPERATIONS_RUN_URL = "https://github.com/Xpounder-com/hormuz/actions/runs/5"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _codex_fixture() -> dict[str, object]:
    return json.loads(CODEX_FIXTURE.read_text(encoding="utf-8"))


def _distribution_input(value: dict[str, object]) -> dict[str, object]:
    return {
        "source_commit": value["source_commit"],
        "workflow_run_url": value["workflow_run_url"],
        "archive_name": value["archive_name"],
        "archive_bytes": value["archive_bytes"],
        "archive_sha256": value["archive_sha256"],
        "version": value["version"],
        "build": value["build"],
    }


def _inputs() -> dict[str, object]:
    fixture = _fixture()
    candidate = _distribution_input(fixture["artifact"])  # type: ignore[arg-type]
    previous = _distribution_input(fixture["previous_artifact"])  # type: ignore[arg-type]
    candidate["source_commit"] = SOURCE
    return {
        "schema_id": operations.INPUT_SCHEMA_ID,
        "schema_version": operations.SCHEMA_VERSION,
        "source_commit": SOURCE,
        "candidate": candidate,
        "previous": previous,
        "gateway": {
            "source_commit": SOURCE,
            "deployment_evidence_url": GATEWAY_RUN_URL,
            "origin": "https://hormuz-pilot.onrender.com",
            "service_id": "srv-aaaaaaaaaaaaaaaaaaaa",
        },
    }


def _codex_inputs() -> dict[str, object]:
    value = _inputs()
    value["schema_version"] = 2
    value["qualification_scope"] = operations.scope.CODEX_OPENAI
    value["gateway"].update(  # type: ignore[union-attr]
        {
            "profile": "external_pilot_openai",
            "provider_protocols": ["openai"],
        }
    )
    return value


def _run(
    run_id: int,
    run_number: int,
    source: str,
    path: str,
    *,
    attempt: int = 1,
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_number": run_number,
        "run_attempt": attempt,
        "head_sha": source,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://github.com/Xpounder-com/hormuz/actions/runs/{run_id}",
        "path": path,
        "created_at": "2026-09-01T10:00:00Z",
        "run_started_at": "2026-09-01T10:01:00Z",
        "updated_at": "2026-09-01T10:10:00Z",
        "repository": {"full_name": "Xpounder-com/hormuz"},
    }


class MacPilotOperationsTests(unittest.TestCase):
    def test_assemble_emits_exact_content_free_record_groups(self) -> None:
        fixture = _fixture()
        inputs = _inputs()
        result = operations.assemble(
            inputs=inputs,
            arm64_record=fixture["clean_machine_runs"][0],  # type: ignore[index]
            lifecycle=fixture["lifecycle"],
            codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
            claude_record=fixture["client_auth_recovery"][1],  # type: ignore[index]
            source_commit=SOURCE,
            workflow_run_url=OPERATIONS_RUN_URL,
        )

        self.assertEqual(
            set(result),
            {
                "schema_id",
                "schema_version",
                "claim_scope",
                "source_commit",
                "workflow_run_url",
                "candidate_archive_sha256",
                "candidate_distribution_run_url",
                "previous_source_commit",
                "previous_archive_sha256",
                "previous_distribution_run_url",
                "gateway_source_commit",
                "gateway_deployment_evidence_url",
                "clean_machine_runs",
                "lifecycle",
                "client_auth_recovery",
            },
        )
        self.assertEqual(result["source_commit"], SOURCE)
        self.assertEqual(result["gateway_source_commit"], SOURCE)
        self.assertEqual(result["clean_machine_runs"], fixture["clean_machine_runs"])
        self.assertEqual(result["lifecycle"], fixture["lifecycle"])
        self.assertEqual(
            result["client_auth_recovery"], fixture["client_auth_recovery"]
        )

    def test_narrow_assemble_emits_only_codex_v2_evidence(self) -> None:
        fixture = _codex_fixture()
        result = operations.assemble(
            inputs=_codex_inputs(),
            arm64_record=fixture["clean_machine_runs"][0],  # type: ignore[index]
            lifecycle=fixture["lifecycle"],
            codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
            claude_record=None,
            source_commit=SOURCE,
            workflow_run_url=OPERATIONS_RUN_URL,
            qualification_scope=operations.scope.CODEX_OPENAI,
        )

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["qualification_scope"], "codex_openai")
        self.assertEqual(
            result["claim_scope"],
            "signed_macos_codex_openai_controlled_external_pilot_readiness",
        )
        self.assertEqual(
            [record["client"] for record in result["client_auth_recovery"]],
            ["codex"],
        )

    def test_assemble_enforces_selected_client_records(self) -> None:
        full = _fixture()
        narrow = _codex_fixture()
        common = {
            "arm64_record": full["clean_machine_runs"][0],  # type: ignore[index]
            "lifecycle": full["lifecycle"],
            "codex_record": full["client_auth_recovery"][0],  # type: ignore[index]
            "source_commit": SOURCE,
            "workflow_run_url": OPERATIONS_RUN_URL,
        }
        with self.assertRaisesRegex(
            operations.MacPilotOperationsError, "claude_record_required"
        ):
            operations.assemble(inputs=_inputs(), claude_record=None, **common)
        with self.assertRaisesRegex(
            operations.MacPilotOperationsError, "claude_record_unexpected"
        ):
            operations.assemble(
                inputs=_codex_inputs(),
                arm64_record=narrow["clean_machine_runs"][0],  # type: ignore[index]
                lifecycle=narrow["lifecycle"],
                codex_record=narrow["client_auth_recovery"][0],  # type: ignore[index]
                claude_record=full["client_auth_recovery"][1],  # type: ignore[index]
                source_commit=SOURCE,
                workflow_run_url=OPERATIONS_RUN_URL,
                qualification_scope=operations.scope.CODEX_OPENAI,
            )

    def test_validate_inputs_rejects_every_invalid_scope_matrix(self) -> None:
        cases: list[tuple[str, dict[str, object], str, str]] = []
        v1_with_scope = _inputs()
        v1_with_scope["qualification_scope"] = "full_dual_provider"
        cases.append(
            (
                "v1 scope",
                v1_with_scope,
                "full_dual_provider",
                "qualification_scope_unexpected",
            )
        )
        v2_missing_scope = _codex_inputs()
        del v2_missing_scope["qualification_scope"]
        cases.append(
            ("v2 missing", v2_missing_scope, "codex_openai", "qualification_scope_missing")
        )
        v2_unknown_scope = _codex_inputs()
        v2_unknown_scope["qualification_scope"] = "unknown"
        cases.append(
            ("v2 unknown", v2_unknown_scope, "codex_openai", "qualification_scope_invalid")
        )
        v2_full_scope = _codex_inputs()
        v2_full_scope["qualification_scope"] = "full_dual_provider"
        cases.append(
            ("v2 full", v2_full_scope, "codex_openai", "qualification_scope_invalid")
        )
        cases.append(
            ("requested mismatch", _codex_inputs(), "full_dual_provider", "qualification_scope_mismatch")
        )
        bad_profile = _codex_inputs()
        bad_profile["gateway"]["profile"] = "external_pilot"  # type: ignore[index]
        cases.append(("profile", bad_profile, "codex_openai", "gateway_scope_invalid"))
        bad_protocols = _codex_inputs()
        bad_protocols["gateway"]["provider_protocols"] = ["openai", "anthropic"]  # type: ignore[index]
        cases.append(("protocols", bad_protocols, "codex_openai", "gateway_scope_invalid"))

        for label, value, selected, expected in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                operations.MacPilotOperationsError, expected
            ):
                operations._validate_inputs(value, SOURCE, selected)

    def test_cli_rejects_forbidden_claude_path_before_any_json_read(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(operations, "_load_json") as load_json,
            redirect_stderr(io.StringIO()) as stderr,
            redirect_stdout(io.StringIO()),
        ):
            result = operations.main(
                [
                    "assemble",
                    "--inputs",
                    "/does/not/exist/inputs.json",
                    "--arm64-record",
                    "/does/not/exist/arm64.json",
                    "--lifecycle",
                    "/does/not/exist/lifecycle.json",
                    "--codex-record",
                    "/does/not/exist/codex.json",
                    "--claude-record",
                    "/does/not/exist/claude.json",
                    "--qualification-scope",
                    "codex_openai",
                    "--source-commit",
                    SOURCE,
                    "--workflow-run-url",
                    OPERATIONS_RUN_URL,
                    "--output",
                    str(Path(temporary) / "proof.json"),
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(stderr.getvalue().strip(), "claude_record_unexpected")
        load_json.assert_not_called()

    def test_gateway_authentication_is_bound_to_selected_profile(self) -> None:
        run = _run(10, 10, SOURCE, operations.pilot.EXTERNAL_PILOT_WORKFLOW)

        def evidence_for(profile: str) -> dict[str, object]:
            return {
                "schema_id": operations.deployment.SCHEMA_ID,
                "schema_version": operations.deployment.SCHEMA_VERSION,
                "evidence_kind": "live_external_pilot",
                **operations.deployment.PROFILE_CONTRACTS[profile],
                "source_commit": SOURCE,
                "workflow_run_url": run["html_url"],
                "gateway_origin": "https://hormuz-pilot.onrender.com",
                "render_service_id": "srv-aaaaaaaaaaaaaaaaaaaa",
                "support_path_published": True,
            }

        with patch.object(
            operations.pilot,
            "_authenticate_run_json_artifact",
            return_value=(evidence_for("external_pilot_openai"), datetime.now(timezone.utc)),
        ):
            value = operations._gateway(
                run, operations.scope.SCOPE_CONTRACTS[operations.scope.CODEX_OPENAI]
            )
        self.assertEqual(value["profile"], "external_pilot_openai")
        self.assertEqual(value["provider_protocols"], ["openai"])

        for selected, authenticated_profile in (
            ("full_dual_provider", "external_pilot_openai"),
            ("codex_openai", "external_pilot"),
        ):
            with (
                self.subTest(selected=selected),
                patch.object(
                    operations.pilot,
                    "_authenticate_run_json_artifact",
                    return_value=(
                        evidence_for(authenticated_profile),
                        datetime.now(timezone.utc),
                    ),
                ),
                self.assertRaisesRegex(
                    operations.MacPilotOperationsError,
                    "gateway_deployment_evidence_invalid",
                ),
            ):
                operations._gateway(
                    run, operations.scope.SCOPE_CONTRACTS[selected]
                )

        protocol_mismatch = evidence_for("external_pilot_openai")
        protocol_mismatch["provider_protocols"] = ["openai", "anthropic"]
        with (
            patch.object(
                operations.pilot,
                "_authenticate_run_json_artifact",
                return_value=(protocol_mismatch, datetime.now(timezone.utc)),
            ),
            self.assertRaisesRegex(
                operations.MacPilotOperationsError,
                "gateway_deployment_evidence_invalid",
            ),
        ):
            operations._gateway(
                run, operations.scope.SCOPE_CONTRACTS[operations.scope.CODEX_OPENAI]
            )

    def test_assemble_rejects_unbound_or_incomplete_inputs_and_records(self) -> None:
        fixture = _fixture()
        cases: list[tuple[str, object, str]] = []

        unknown = _inputs()
        unknown["candidate"]["unexpected"] = True  # type: ignore[index]
        cases.append(("unknown", unknown, "candidate_input_fields_invalid"))

        wrong_gateway = _inputs()
        wrong_gateway["gateway"]["source_commit"] = "c" * 40  # type: ignore[index]
        cases.append(("gateway", wrong_gateway, "operations_inputs_binding_invalid"))

        reused_archive = _inputs()
        reused_archive["previous"]["archive_sha256"] = reused_archive["candidate"][  # type: ignore[index]
            "archive_sha256"
        ]
        cases.append(("archive", reused_archive, "operations_inputs_binding_invalid"))

        reused_version = _inputs()
        reused_version["previous"]["version"] = reused_version["candidate"][  # type: ignore[index]
            "version"
        ]
        reused_version["previous"]["archive_name"] = reused_version["candidate"][  # type: ignore[index]
            "archive_name"
        ]
        cases.append(("version", reused_version, "operations_inputs_binding_invalid"))

        bad_origin = _inputs()
        bad_origin["gateway"]["origin"] = "http://example.invalid"  # type: ignore[index]
        cases.append(("origin", bad_origin, "gateway_origin_invalid"))

        for label, inputs, expected in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                operations.MacPilotOperationsError, expected
            ):
                operations.assemble(
                    inputs=inputs,
                    arm64_record=fixture["clean_machine_runs"][0],  # type: ignore[index]
                    lifecycle=fixture["lifecycle"],
                    codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
                    claude_record=fixture["client_auth_recovery"][1],  # type: ignore[index]
                    source_commit=SOURCE,
                    workflow_run_url=OPERATIONS_RUN_URL,
                )

        wrong_arch = copy.deepcopy(fixture["clean_machine_runs"][0])  # type: ignore[index]
        wrong_arch["architecture"] = "x86_64"
        with self.assertRaisesRegex(
            operations.MacPilotOperationsError,
            "clean_machine_run_0_architecture_invalid",
        ):
            operations.assemble(
                inputs=_inputs(),
                arm64_record=wrong_arch,
                lifecycle=fixture["lifecycle"],
                codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
                claude_record=fixture["client_auth_recovery"][1],  # type: ignore[index]
                source_commit=SOURCE,
                workflow_run_url=OPERATIONS_RUN_URL,
            )

        wrong_lifecycle = copy.deepcopy(fixture["lifecycle"])
        wrong_lifecycle["update_to_build"] = "3"  # type: ignore[index]
        with self.assertRaisesRegex(
            operations.MacPilotOperationsError, "operations_records_incomplete"
        ):
            operations.assemble(
                inputs=_inputs(),
                arm64_record=fixture["clean_machine_runs"][0],  # type: ignore[index]
                lifecycle=wrong_lifecycle,
                codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
                claude_record=fixture["client_auth_recovery"][1],  # type: ignore[index]
                source_commit=SOURCE,
                workflow_run_url=OPERATIONS_RUN_URL,
            )

    def test_prepare_requires_consecutive_first_attempts_and_same_gateway_source(self) -> None:
        candidate_run = _run(
            12, 12, SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW
        )
        previous_run = _run(
            11, 11, PREVIOUS_SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW
        )
        gateway_run = _run(
            10, 10, SOURCE, operations.pilot.EXTERNAL_PILOT_WORKFLOW
        )
        candidate = _inputs()["candidate"]
        previous = _inputs()["previous"]
        created = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        with (
            patch.object(
                operations,
                "_run",
                side_effect=[candidate_run, previous_run, gateway_run],
            ),
            patch.object(
                operations,
                "_distribution",
                side_effect=[
                    (candidate, created.replace(hour=12)),
                    (previous, created.replace(hour=11)),
                ],
            ),
            patch.object(
                operations,
                "_gateway",
                return_value={
                    "source_commit": SOURCE,
                    "deployment_evidence_url": GATEWAY_RUN_URL,
                    "origin": "https://hormuz-pilot.onrender.com",
                    "service_id": "srv-aaaaaaaaaaaaaaaaaaaa",
                },
            ),
            patch.object(
                operations,
                "_run_timeline",
                return_value=(created, created.replace(minute=10)),
            ),
        ):
            value = operations.prepare(
                candidate_url=candidate_run["html_url"],  # type: ignore[arg-type]
                previous_url=previous_run["html_url"],  # type: ignore[arg-type]
                gateway_url=gateway_run["html_url"],  # type: ignore[arg-type]
                expected_source_commit=SOURCE,
            )
        self.assertEqual(value["candidate"], candidate)
        self.assertEqual(value["previous"], previous)

        for label, changed_candidate, gateway_source, expected in (
            (
                "gap",
                {**candidate_run, "run_number": 13},
                SOURCE,
                "distribution_history_not_immediate",
            ),
            (
                "rerun",
                {**candidate_run, "run_attempt": 2},
                SOURCE,
                "distribution_history_not_immediate",
            ),
            (
                "gateway",
                candidate_run,
                "c" * 40,
                "gateway_source_commit_invalid",
            ),
        ):
            with (
                self.subTest(label=label),
                patch.object(
                    operations,
                    "_run",
                    side_effect=[changed_candidate, previous_run, gateway_run],
                ),
                patch.object(
                    operations,
                    "_distribution",
                    side_effect=[
                        (candidate, created.replace(hour=12)),
                        (previous, created.replace(hour=11)),
                    ],
                ),
                patch.object(
                    operations,
                    "_gateway",
                    return_value={
                        "source_commit": gateway_source,
                        "deployment_evidence_url": GATEWAY_RUN_URL,
                        "origin": "https://hormuz-pilot.onrender.com",
                        "service_id": "srv-aaaaaaaaaaaaaaaaaaaa",
                    },
                ),
                patch.object(
                    operations,
                    "_run_timeline",
                    return_value=(created, created.replace(minute=10)),
                ),
                self.assertRaisesRegex(operations.MacPilotOperationsError, expected),
            ):
                operations.prepare(
                    candidate_url=candidate_run["html_url"],  # type: ignore[arg-type]
                    previous_url=previous_run["html_url"],  # type: ignore[arg-type]
                    gateway_url=gateway_run["html_url"],  # type: ignore[arg-type]
                    expected_source_commit=SOURCE,
                )

    def test_distribution_binds_notarization_without_requiring_it_in_proof(self) -> None:
        run = _run(
            12, 12, SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW
        )
        signed_archive = b"authenticated signed archive fixture"
        proof = {
            "schema_id": "hormuz.macos-distribution-proof",
            "schema_version": 2,
            "passed": True,
            "mode": "notarized",
            "distribution_ready": True,
            "bundle_identifier": "com.xpounder.hormuz",
            "version": "1.2.3",
            "build": "12001",
            "architectures": ["arm64"],
            "minimum_macos": "14.0",
            "hardened_runtime": True,
            "entitlements": [],
            "system_runtime_dependencies_only": True,
            "executable_version_verified": True,
            "notarization_ticket_stapled": True,
            "team_identifier": "R267LZMUTY",
            "signing_authority": (
                "Developer ID Application: Test Operator (R267LZMUTY)"
            ),
            "archive_bytes": len(signed_archive),
            "archive_sha256": hashlib.sha256(signed_archive).hexdigest(),
            "executable_sha256": "d" * 64,
            "icon_sha256": "e" * 64,
            "source_commit": SOURCE,
            "workflow_run_url": run["html_url"],
        }
        notarization = {
            "schema_id": "hormuz.apple-notarization",
            "schema_version": 1,
            "submission_id": "12345678-1234-1234-8234-123456789abc",
            "status": "Accepted",
            "accepted": True,
            "issue_count": 0,
            "issue_severities": {},
            "ticket_entry_count": 1,
        }
        created = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as temporary:
            artifact_zip = Path(temporary) / "artifact.zip"
            with zipfile.ZipFile(artifact_zip, "w") as package:
                package.writestr("distribution-proof.json", json.dumps(proof))
                package.writestr("notarization.json", json.dumps(notarization))
                package.writestr("Hormuz-1.2.3-notarized.zip", signed_archive)

            def download(_artifact_id: int, output: Path, *_args: object) -> None:
                output.write_bytes(artifact_zip.read_bytes())

            with (
                patch.object(
                    operations,
                    "_artifacts",
                    return_value=[{"name": "hormuz-macos-1.2.3-12-1"}],
                ),
                patch.object(
                    operations,
                    "_trusted_artifact",
                    return_value=(7, artifact_zip.stat().st_size, created),
                ),
                patch.object(
                    operations.pilot,
                    "_download_github_artifact",
                    side_effect=download,
                ),
                patch.object(operations.pilot, "_verify_distribution_artifact_zip"),
            ):
                value, observed_created = operations._distribution(run, "candidate")

        self.assertNotIn("submission_id", proof)
        self.assertEqual(observed_created, created)
        self.assertEqual(value["source_commit"], SOURCE)
        self.assertEqual(value["archive_sha256"], proof["archive_sha256"])

    def test_prepare_emits_authenticated_narrow_input_shape_only_when_selected(self) -> None:
        candidate_run = _run(
            12, 12, SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW
        )
        previous_run = _run(
            11, 11, PREVIOUS_SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW
        )
        gateway_run = _run(
            10, 10, SOURCE, operations.pilot.EXTERNAL_PILOT_WORKFLOW
        )
        candidate = _inputs()["candidate"]
        previous = _inputs()["previous"]
        gateway = _codex_inputs()["gateway"]
        created = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        with (
            patch.object(
                operations,
                "_run",
                side_effect=[candidate_run, previous_run, gateway_run],
            ),
            patch.object(
                operations,
                "_distribution",
                side_effect=[
                    (candidate, created.replace(hour=12)),
                    (previous, created.replace(hour=11)),
                ],
            ),
            patch.object(operations, "_gateway", return_value=gateway) as authenticate,
            patch.object(
                operations,
                "_run_timeline",
                return_value=(created, created.replace(minute=10)),
            ),
        ):
            value = operations.prepare(
                candidate_url=candidate_run["html_url"],  # type: ignore[arg-type]
                previous_url=previous_run["html_url"],  # type: ignore[arg-type]
                gateway_url=gateway_run["html_url"],  # type: ignore[arg-type]
                expected_source_commit=SOURCE,
                qualification_scope=operations.scope.CODEX_OPENAI,
            )
        authenticate.assert_called_once_with(
            gateway_run,
            operations.scope.SCOPE_CONTRACTS[operations.scope.CODEX_OPENAI],
        )
        self.assertEqual(value, _codex_inputs())

    def test_run_authentication_rejects_non_default_or_failed_run(self) -> None:
        run = _run(12, 12, SOURCE, operations.pilot.MACOS_DISTRIBUTION_WORKFLOW)
        with patch.object(operations.pilot, "_github_api_json", return_value=run):
            self.assertEqual(
                operations._run(run["html_url"], "candidate"),  # type: ignore[arg-type]
                run,
            )
        for field, value in (("head_branch", "feature"), ("conclusion", "failure")):
            changed = {**run, field: value}
            with (
                self.subTest(field=field),
                patch.object(
                    operations.pilot, "_github_api_json", return_value=changed
                ),
                self.assertRaisesRegex(
                    operations.MacPilotOperationsError, "candidate_run_not_trusted"
                ),
            ):
                operations._run(run["html_url"], "candidate")  # type: ignore[arg-type]

    def test_isolated_cli_imports_only_its_reviewed_tools_directory(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "tools/macos_pilot_operations.py"),
                "--help",
            ],
            cwd="/",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("{prepare,assemble}", result.stdout)

    def test_collectors_are_system_tool_only_and_fail_closed(self) -> None:
        clean = ROOT / "tools/collect_macos_clean_machine.sh"
        session = ROOT / "tools/collect_macos_session_and_clients.sh"
        syntax = subprocess.run(
            ["/bin/bash", "-n", str(clean), str(session)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, msg=syntax.stderr)
        clean_text = clean.read_text(encoding="utf-8")
        session_text = session.read_text(encoding="utf-8")
        for executable in ("python", "node", "jq"):
            for suffix in (" ", '"', "'", "\n"):
                self.assertNotIn(f"/{executable}{suffix}", clean_text)
                self.assertNotIn(f"/{executable}{suffix}", session_text)
            self.assertNotIn(f" {executable} ", clean_text)
            self.assertNotIn(f" {executable} ", session_text)
        for marker in (
            "com.apple.quarantine",
            "codesign --verify --deep --strict",
            "spctl --assess --type execute",
            "developer_tools_present",
            "TeamIdentifier=R267LZMUTY",
            'diff -qr "$DOWNLOADED_APP" "$ARCHIVE_APP"',
            "archive_contents_mismatch",
            "PRELAUNCH_PIDS",
            '/bin/ps -ww -p "$candidate_pid" -o command=',
            '"$PRELAUNCH_PIDS" != *" $candidate_pid "*',
        ):
            self.assertIn(marker, clean_text)
        for marker in (
            "-extract IOConsoleLocked",
            "0.IOConsoleLocked",
            "0\\.147\\.0",
            "2\\.1\\.233",
            "pilot-evidence verify-denied",
            "pilot-evidence session-store-empty",
            "provider_egress_on_rejected_turn",
            "EXPECTED_GATEWAY",
            '"$gateway" == "$EXPECTED_GATEWAY"',
            "EXPECTED_SERVICE_ID",
            "EXPECTED_INSTANCE_FINGERPRINT",
            '"$instance_fingerprint" == "$EXPECTED_INSTANCE_FINGERPRINT"',
            "deployment.sourceCommit",
            "deployment.serviceId",
            "TeamIdentifier=R267LZMUTY",
            "134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477",
            "19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37",
            "bc466b6cde63edafc773f471a1fb98787fabb31f52240c8616ce7e1f587b212d",
        ):
            self.assertIn(marker, session_text)
        self.assertNotIn("command -v codex", session_text)
        self.assertNotIn("command -v claude", session_text)

        for text in (clean_text, session_text):
            self.assertIn('case "$SCHEMA_VERSION" in', text)
            self.assertIn("QUALIFICATION_SCOPE=full_dual_provider", text)
            self.assertIn("QUALIFICATION_SCOPE=codex_openai", text)
            self.assertIn('"$(input_value gateway.provider_protocols)" == "1"', text)
            self.assertIn(
                '"$(input_value gateway.provider_protocols.0)" == "openai"', text
            )
            self.assertLess(
                text.index('case "$SCHEMA_VERSION" in'),
                text.index("SOURCE_COMMIT="),
            )
        self.assertLess(
            session_text.index('case "$SCHEMA_VERSION" in'),
            session_text.index('/bin/chmod 700 "$OUTPUT_DIRECTORY"'),
        )
        claude_guard = session_text.rindex(
            'if [[ "$QUALIFICATION_SCOPE" == "full_dual_provider" ]]; then'
        )
        lifecycle_write = session_text.index("LIFECYCLE_TMP=", claude_guard)
        guarded_sequence = session_text[claude_guard:lifecycle_write]
        for marker in (
            "restart_app",
            "require_empty_session_store",
            "wait_for_active_profile claude-code",
            "verify_session",
            "run_claude_recovery",
            "pilot-evidence sign-out",
            "pilot-evidence session-absent",
            "credential_files_absent",
        ):
            self.assertIn(marker, guarded_sequence)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "collector execution requires the Apple plutil contract",
    )
    def test_collectors_reject_malformed_scope_before_mutation(self) -> None:
        clean = ROOT / "tools/collect_macos_clean_machine.sh"
        session = ROOT / "tools/collect_macos_session_and_clients.sh"
        cases: list[tuple[str, dict[str, object], str]] = []

        v1_scope = _inputs()
        v1_scope["qualification_scope"] = None
        cases.append(("v1 scope", v1_scope, "inputs_scope_invalid"))

        v2_missing = _codex_inputs()
        del v2_missing["qualification_scope"]
        cases.append(("v2 missing", v2_missing, "inputs_scope_invalid"))

        v2_null = _codex_inputs()
        v2_null["qualification_scope"] = None
        cases.append(("v2 null", v2_null, "inputs_scope_invalid"))

        v2_unknown = _codex_inputs()
        v2_unknown["qualification_scope"] = "unknown"
        cases.append(("v2 unknown", v2_unknown, "inputs_scope_invalid"))

        v2_full = _codex_inputs()
        v2_full["qualification_scope"] = "full_dual_provider"
        cases.append(("v2 full", v2_full, "inputs_scope_invalid"))

        wrong_profile = _codex_inputs()
        wrong_profile["gateway"]["profile"] = "external_pilot"  # type: ignore[index]
        cases.append(("profile", wrong_profile, "inputs_scope_invalid"))

        duplicate_protocol = _codex_inputs()
        duplicate_protocol["gateway"]["provider_protocols"] = ["openai", "openai"]  # type: ignore[index]
        cases.append(("duplicate", duplicate_protocol, "inputs_scope_invalid"))

        wrong_schema_type = _codex_inputs()
        wrong_schema_type["schema_version"] = True
        cases.append(("version type", wrong_schema_type, "inputs_schema_invalid"))

        for label, value, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inputs = root / "inputs.json"
                inputs.write_text(json.dumps(value), encoding="utf-8")
                clean_output = root / "clean.json"
                clean_result = subprocess.run(
                    ["/bin/bash", str(clean), str(inputs), "arm64", str(clean_output)],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertNotEqual(clean_result.returncode, 0)
                self.assertIn(expected, clean_result.stderr)
                self.assertFalse(clean_output.exists())

                session_output = root / "records"
                session_output.mkdir(mode=0o755)
                before_mode = session_output.stat().st_mode & 0o777
                session_result = subprocess.run(
                    ["/bin/bash", str(session), str(inputs), str(session_output)],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertNotEqual(session_result.returncode, 0)
                self.assertIn(expected, session_result.stderr)
                self.assertEqual(session_output.stat().st_mode & 0o777, before_mode)
                self.assertEqual(list(session_output.iterdir()), [])

    def test_exclusive_output_refuses_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "record.json"
            output.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(
                operations.MacPilotOperationsError, "output_path_unsafe"
            ):
                operations._write_exclusive(output, {"status": "passed"})


if __name__ == "__main__":
    unittest.main()
