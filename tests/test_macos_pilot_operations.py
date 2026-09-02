from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import macos_pilot_operations as operations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/macos_pilot/complete-synthetic-v1.json"
SOURCE = "a" * 40
PREVIOUS_SOURCE = "b" * 40
GATEWAY_RUN_URL = "https://github.com/Xpounder-com/hormuz/actions/runs/3"
OPERATIONS_RUN_URL = "https://github.com/Xpounder-com/hormuz/actions/runs/5"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


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
            x86_64_record=fixture["clean_machine_runs"][1],  # type: ignore[index]
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
                    x86_64_record=fixture["clean_machine_runs"][1],  # type: ignore[index]
                    lifecycle=fixture["lifecycle"],
                    codex_record=fixture["client_auth_recovery"][0],  # type: ignore[index]
                    claude_record=fixture["client_auth_recovery"][1],  # type: ignore[index]
                    source_commit=SOURCE,
                    workflow_run_url=OPERATIONS_RUN_URL,
                )

        wrong_arch = copy.deepcopy(fixture["clean_machine_runs"][1])  # type: ignore[index]
        wrong_arch["architecture"] = "arm64"
        with self.assertRaisesRegex(
            operations.MacPilotOperationsError, "operations_records_incomplete"
        ):
            operations.assemble(
                inputs=_inputs(),
                arm64_record=fixture["clean_machine_runs"][0],  # type: ignore[index]
                x86_64_record=wrong_arch,
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
                x86_64_record=fixture["clean_machine_runs"][1],  # type: ignore[index]
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
