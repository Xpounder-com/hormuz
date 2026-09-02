from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from tools.qualify_external_pilot import (
    QualificationError,
    _authenticate_deployment_evidence,
    _deploy_hook_url,
    _provider_request,
    _reliability,
    qualify,
)


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://hormuz-test.onrender.com"
COMMIT = "a" * 40
SERVICE_ID = "srv-" + "b" * 20
DEPLOYMENT_RUN = "https://github.com/Xpounder-com/hormuz/actions/runs/100"
QUALIFICATION_RUN = "https://github.com/Xpounder-com/hormuz/actions/runs/101"
REFRESH = "hox_r_" + "r" * 43
ROTATED_REFRESH = "hox_r_" + "s" * 43
ACCESS = "hox_a_" + "a" * 43
REHEARSAL = "k" * 43


def _deployment_artifact(*, service_id: str = SERVICE_ID) -> bytes:
    evidence = {
        "schema_id": "hormuz.external-pilot-deployment-evidence",
        "schema_version": 1,
        "evidence_kind": "live_external_pilot",
        "profile": "external_pilot",
        "source_commit": COMMIT,
        "workflow_run_url": DEPLOYMENT_RUN,
        "gateway_origin": ORIGIN,
        "render_service_id": service_id,
        "identity_provider": "okta",
        "provider_protocols": ["anthropic", "openai"],
        "https": True,
        "inference_enabled": True,
        "provider_credentials_server_only": True,
        "postgresql_durable": True,
        "tenant_rls": True,
        "durable_sessions": True,
        "monitoring_configured": True,
        "worker_saturation_monitoring": True,
        "postgresql_pool_wait_monitoring": True,
        "support_path_published": True,
        "single_region_acknowledged": True,
        "availability_sla_claimed": False,
        "max_inflight_streams": 8,
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "external-pilot-deployment-evidence.json",
            json.dumps(evidence).encode(),
        )
    return archive.getvalue()


def _counters(*, live: int, attempts: int, first: int, failovers: int, unknown: int = 0, cancellations: int = 0):
    return {
        "schema_id": "hormuz.provider-reliability-summary",
        "schema_version": 1,
        "scope": "current_actor",
        "live_provider_request_count": live,
        "provider_attempt_record_count": attempts,
        "latency_header_sample_count": attempts,
        "latency_first_body_byte_sample_count": first,
        "latency_total_sample_count": attempts,
        "failover_link_record_count": failovers,
        "outcome_unknown_count": unknown,
        "cancellation_outcome_unknown_count": cancellations,
        "provider_capacity": 8,
        "provider_inflight": 0,
        "provider_peak_inflight": 1,
        "provider_saturated_total": 0,
        "connection_capacity": 9,
        "connection_saturated_total": 0,
        "postgresql_pool_max_connections": 4,
        "postgresql_pool_requests_waiting": 0,
        "postgresql_pool_requests_queued_total": 0,
        "postgresql_pool_wait_milliseconds_total": 0,
        "postgresql_pool_error_total": 0,
        "deployment": {
            "platform": "render",
            "source_commit": COMMIT,
            "service_id": SERVICE_ID,
            "external_origin": ORIGIN,
        },
    }


class ExternalPilotQualificationTests(unittest.TestCase):
    def test_deployment_run_and_artifact_are_authenticated_before_qualification(self) -> None:
        archive = _deployment_artifact()
        run = {
            "id": 100,
            "html_url": DEPLOYMENT_RUN,
            "head_sha": COMMIT,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/external-pilot-qualification.yml",
            "run_number": 7,
            "run_attempt": 1,
            "repository": {"full_name": "Xpounder-com/hormuz"},
        }
        artifact = {
            "id": 777,
            "name": "hormuz-external-pilot-deployment-7-1",
            "size_in_bytes": len(archive),
            "expired": False,
            "url": "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/777",
            "archive_download_url": "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/777/zip",
            "workflow_run": {"id": 100, "head_branch": "main", "head_sha": COMMIT},
        }
        with (
            patch(
                "tools.qualify_external_pilot._github_api_json",
                side_effect=[run, {"total_count": 1, "artifacts": [artifact]}],
            ),
            patch(
                "tools.qualify_external_pilot._github_api_bytes",
                return_value=archive,
            ),
        ):
            evidence = _authenticate_deployment_evidence(
                DEPLOYMENT_RUN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                origin=ORIGIN,
            )
        self.assertEqual(evidence["render_service_id"], SERVICE_ID)

        failed = {**run, "conclusion": "failure"}
        with (
            patch("tools.qualify_external_pilot._github_api_json", return_value=failed),
            self.assertRaisesRegex(QualificationError, "run_not_trusted"),
        ):
            _authenticate_deployment_evidence(
                DEPLOYMENT_RUN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                origin=ORIGIN,
            )

        mismatched = _deployment_artifact(service_id="srv-" + "c" * 20)
        artifact["size_in_bytes"] = len(mismatched)
        with (
            patch(
                "tools.qualify_external_pilot._github_api_json",
                side_effect=[run, {"total_count": 1, "artifacts": [artifact]}],
            ),
            patch(
                "tools.qualify_external_pilot._github_api_bytes",
                return_value=mismatched,
            ),
            self.assertRaisesRegex(QualificationError, "binding_invalid"),
        ):
            _authenticate_deployment_evidence(
                DEPLOYMENT_RUN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                origin=ORIGIN,
            )

    def test_streaming_evidence_requires_terminal_marker_after_the_first_read(self) -> None:
        class Response:
            status = 200
            headers = {
                "X-Hormuz-Requested-Model": "openai-primary",
                "X-Hormuz-Routed-Model": "gpt-test",
                "Server-Timing": "hormuz_upstream_headers;dur=1.000",
            }

            def __init__(self, chunks):
                self.chunks = list(chunks)

            def read1(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

            def close(self):
                pass

        terminal = b'event: response.completed\ndata: {"type":"response.completed"}\n\n'
        delta = b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta"}\n\n'
        for chunks, observed_times, expected in (
            ([delta, terminal], [1.0, 1.5], (True, True)),
            ([delta, terminal], [1.0, 1.1], (False, True)),
            ([delta + terminal], [1.0], (False, True)),
            ([delta], [1.0], (True, False)),
        ):
            with (
                self.subTest(expected=expected),
                patch(
                    "tools.qualify_external_pilot._open_gateway",
                    return_value=Response(chunks),
                ),
                patch(
                    "tools.qualify_external_pilot.time.monotonic",
                    side_effect=observed_times,
                ),
            ):
                _, first_before_completion, terminal_observed = _provider_request(
                    ORIGIN,
                    ACCESS,
                    protocol="openai",
                    alias="openai-primary",
                    stream=True,
                )
            self.assertEqual(
                (first_before_completion, terminal_observed),
                expected,
            )

    def test_reliability_summary_proves_bounded_worker_and_pool_monitoring(self) -> None:
        value = _counters(live=1, attempts=1, first=1, failovers=0)
        with patch(
            "tools.qualify_external_pilot._json_gateway",
            return_value=(value, {"cache-control": "no-store"}),
        ):
            self.assertEqual(
                _reliability(
                    ORIGIN,
                    ACCESS,
                    expected_commit=COMMIT,
                    service_id=SERVICE_ID,
                ),
                value,
            )
        unsafe = {**value, "provider_capacity": 9}
        with (
            patch(
                "tools.qualify_external_pilot._json_gateway",
                return_value=(unsafe, {"cache-control": "no-store"}),
            ),
            self.assertRaisesRegex(
                QualificationError, "provider_reliability_summary_invalid"
            ),
        ):
            _reliability(
                ORIGIN,
                ACCESS,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
            )

    def test_restart_and_live_provider_observations_bind_strict_evidence(self) -> None:
        provider_results = []
        for _protocol in ("anthropic", "openai"):
            for _suffix in ("primary", "secondary"):
                provider_results.extend([
                    ({"server-timing": "hormuz_upstream_headers;dur=1.000"}, False, False),
                    ({"server-timing": "hormuz_upstream_headers;dur=1.000"}, True, True),
                ])
        provider_results.extend([
            ({"x-hormuz-cancellation-rehearsal": "v1"}, True, False),
            ({
                "x-hormuz-failover": "v1;reason=provider_rate_limited",
                "x-hormuz-failover-rehearsal": "v1",
            }, False, False),
        ])
        snapshots = [
            _counters(live=0, attempts=0, first=0, failovers=0),
            _counters(live=8, attempts=8, first=8, failovers=0),
            _counters(live=9, attempts=9, first=9, failovers=0, unknown=1, cancellations=1),
            _counters(live=10, attempts=11, first=10, failovers=1, unknown=1, cancellations=1),
        ]
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence") as deployment,
            patch("tools.qualify_external_pilot._restart_and_wait", return_value="c" * 16) as restart,
            patch("tools.qualify_external_pilot._refresh", return_value=(ACCESS, ROTATED_REFRESH)) as refresh,
            patch("tools.qualify_external_pilot._reliability", side_effect=snapshots) as reliability,
            patch("tools.qualify_external_pilot._provider_request", side_effect=provider_results) as provider,
            patch("tools.qualify_external_pilot._logout") as logout,
        ):
            evidence = qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        restart.assert_called_once()
        deployment.assert_called_once_with(
            DEPLOYMENT_RUN,
            expected_commit=COMMIT,
            service_id=SERVICE_ID,
            origin=ORIGIN,
        )
        refresh.assert_called_once_with(ORIGIN, REFRESH)
        self.assertEqual(reliability.call_count, 4)
        self.assertEqual(provider.call_count, 10)
        logout.assert_called_once_with(ORIGIN, ROTATED_REFRESH)
        self.assertEqual(evidence["schema_id"], "hormuz.external-pilot-qualification-evidence")
        self.assertEqual(evidence["deployment_evidence_url"], DEPLOYMENT_RUN)
        self.assertEqual(evidence["recovery_evidence_url"], QUALIFICATION_RUN)
        self.assertEqual(evidence["live_provider_request_count"], 10)
        self.assertEqual(evidence["provider_attempt_record_count"], 11)
        self.assertEqual(evidence["failover_link_record_count"], 1)
        self.assertEqual(evidence["cancellation_replay_count"], 0)
        rendered = repr(evidence)
        for secret in (REFRESH, ROTATED_REFRESH, ACCESS, REHEARSAL):
            self.assertNotIn(secret, rendered)

    def test_rotated_qualification_session_is_revoked_after_failure(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._restart_and_wait"),
            patch("tools.qualify_external_pilot._refresh", return_value=(ACCESS, ROTATED_REFRESH)),
            patch("tools.qualify_external_pilot._reliability", side_effect=QualificationError("probe_failed")),
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(QualificationError, "probe_failed"),
        ):
            qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        logout.assert_called_once_with(ORIGIN, ROTATED_REFRESH)

    def test_original_refresh_credential_revokes_family_when_rotation_response_is_lost(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._restart_and_wait"),
            patch(
                "tools.qualify_external_pilot._refresh",
                side_effect=QualificationError("refresh_response_invalid"),
            ),
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(QualificationError, "refresh_response_invalid"),
        ):
            qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        logout.assert_called_once_with(ORIGIN, REFRESH)

    def test_deploy_hook_is_bound_to_the_expected_service_and_commit(self) -> None:
        hook = f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}"
        qualified = _deploy_hook_url(hook, SERVICE_ID, COMMIT)
        self.assertEqual(qualified, hook + "&ref=" + COMMIT)
        for invalid in (
            f"http://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            f"https://api.render.com/deploy/srv-{'c' * 20}?key={'z' * 32}",
            f"https://example.test/deploy/{SERVICE_ID}?key={'z' * 32}",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(QualificationError):
                _deploy_hook_url(invalid, SERVICE_ID, COMMIT)

    def test_workflow_keeps_live_credentials_in_the_protected_job(self) -> None:
        workflow = (ROOT / ".github/workflows/external-pilot-qualification.yml").read_text()
        self.assertEqual(workflow.count("environment: external-pilot-qualification"), 2)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_EXTERNAL_PILOT_REFRESH_TOKEN }}"), 1)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_FAILOVER_REHEARSAL_KEY }}"), 1)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_RENDER_DEPLOY_HOOK_URL }}"), 1)
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("qualify_external_pilot.py", workflow)
        self.assertIn("external-pilot-qualification-evidence.json", workflow)


if __name__ == "__main__":
    unittest.main()
