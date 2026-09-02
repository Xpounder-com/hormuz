from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.verify_external_pilot_deployment import (
    DeploymentEvidenceError,
    EXPECTED_CONTRACT,
    build_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
SERVICE_ID = "srv-" + "b" * 20
ORIGIN = "https://hormuz-test.onrender.com"
RUN_URL = "https://github.com/Xpounder-com/hormuz/actions/runs/123"


def _health(*, commit: str = COMMIT) -> dict[str, object]:
    return {
        "schema_id": "hormuz.hosted-provider-pilot",
        "schema_version": 1,
        "status": "provider_pilot",
        "inference_enabled": True,
        "contract": dict(EXPECTED_CONTRACT),
        "deployment": {
            "platform": "render",
            "source_commit": commit,
            "source_branch": "main",
            "repository": "Xpounder-com/hormuz",
            "cpu_count": "0.5",
            "web_concurrency": "1",
            "external_origin": ORIGIN,
            "service_id": SERVICE_ID,
            "instance_fingerprint": "c" * 16,
        },
    }


class ExternalPilotDeploymentTests(unittest.TestCase):
    def test_live_observations_create_only_strict_content_free_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SUPPORT.md").write_text("# Support\n")
            headers = {
                "content-type": "application/json",
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            }
            with (
                patch(
                    "tools.verify_external_pilot_deployment._json_response",
                    side_effect=[(_health(), headers), (_health(), headers)],
                ) as fetch,
                patch(
                    "tools.verify_external_pilot_deployment._request",
                    return_value=(401, {"cache-control": "no-store"}, b"{}"),
                ) as unauthorized,
            ):
                evidence = build_evidence(
                    origin=ORIGIN,
                    expected_commit=COMMIT,
                    expected_service_id=SERVICE_ID,
                    workflow_run_url=RUN_URL,
                    root=root,
                )
        self.assertEqual(fetch.call_args_list[0].args, (ORIGIN, "/health"))
        self.assertEqual(fetch.call_args_list[1].args, (ORIGIN, "/ready"))
        unauthorized.assert_called_once_with(ORIGIN, "/v1/responses", method="POST")
        self.assertEqual(evidence["schema_id"], "hormuz.external-pilot-deployment-evidence")
        self.assertEqual(evidence["source_commit"], COMMIT)
        self.assertEqual(evidence["gateway_origin"], ORIGIN)
        self.assertEqual(evidence["render_service_id"], SERVICE_ID)
        self.assertTrue(evidence["support_path_published"])
        rendered = repr(evidence).lower()
        for forbidden in ("prompt", "response_body", "credential", "token_value"):
            if forbidden == "credential":
                self.assertIn("provider_credentials_server_only", rendered)
            else:
                self.assertNotIn(forbidden, rendered)

    def test_source_or_contract_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SUPPORT.md").write_text("# Support\n")
            headers = {
                "content-type": "application/json",
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            }
            for health in (_health(commit="d" * 40), {**_health(), "contract": {}}):
                with self.subTest(health=health), patch(
                    "tools.verify_external_pilot_deployment._json_response",
                    side_effect=[(health, headers), (health, headers)],
                ), self.assertRaises(DeploymentEvidenceError):
                    build_evidence(
                        origin=ORIGIN,
                        expected_commit=COMMIT,
                        expected_service_id=SERVICE_ID,
                        workflow_run_url=RUN_URL,
                        root=root,
                    )

    def test_workflow_is_manual_protected_and_exact_main_only(self) -> None:
        workflow = (ROOT / ".github/workflows/external-pilot-qualification.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment: external-pilot-qualification", workflow)
        self.assertIn('test "$GITHUB_REF" = "$HORMUZ_EXPECTED_REF"', workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', workflow)
        self.assertIn("verify_external_pilot_deployment.py", workflow)
        self.assertIn("hormuz-external-pilot-deployment-${{ github.run_number }}-${{ github.run_attempt }}", workflow)
        deployment_job = workflow.split("\n  qualification:\n", 1)[0]
        self.assertNotIn("${{ secrets.", deployment_job)


if __name__ == "__main__":
    unittest.main()
