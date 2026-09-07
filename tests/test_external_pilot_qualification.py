from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from tools.qualify_external_pilot import (
    QualificationError,
    _abort_gateway_response,
    _authenticate_deployment_evidence,
    _deploy_hook_url,
    _logout_sessions,
    _provider_request,
    _reliability,
    _session_binding,
    _wait_for_cancellation_evidence,
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
ROTATED_REFRESH_AFTER_RESTART = "hox_r_" + "t" * 43
ACCESS = "hox_a_" + "a" * 43
ACCESS_AFTER_RESTART = "hox_a_" + "b" * 43
CLAUDE_REFRESH = "hox_r_" + "u" * 43
CLAUDE_ROTATED_REFRESH = "hox_r_" + "v" * 43
CLAUDE_ROTATED_AFTER_RESTART = "hox_r_" + "w" * 43
CLAUDE_ACCESS = "hox_a_" + "c" * 43
CLAUDE_ACCESS_AFTER_RESTART = "hox_a_" + "d" * 43
BINDING = ("evaluation", "evaluation-member", "evaluation-eng")
REHEARSAL = "k" * 43


def _qualification_arguments():
    return {
        "origin": ORIGIN,
        "expected_commit": COMMIT,
        "service_id": SERVICE_ID,
        "deployment_evidence_url": DEPLOYMENT_RUN,
        "workflow_run_url": QUALIFICATION_RUN,
        "refresh_token": REFRESH,
        "claude_code_refresh_token": CLAUDE_REFRESH,
        "rehearsal_key": REHEARSAL,
        "deploy_hook": f"https://api.render.com/deploy/{SERVICE_ID}?key=exampleKey1",
    }


def _session_identity(client, **changes):
    value = {
        "schema_id": "hormuz.gateway-identity", "schema_version": 1,
        "organization_id": BINDING[0], "actor_id": BINDING[1], "team_id": BINDING[2],
        "identity_type": "human", "allowed_clients": [client],
        "authentication_source": "session:test-issuer",
    }
    return value | changes


def _deployment_artifact(*, service_id: str = SERVICE_ID, profile: str = "external_pilot") -> bytes:
    evidence = {
        "schema_id": "hormuz.external-pilot-deployment-evidence",
        "schema_version": 1,
        "evidence_kind": "live_external_pilot",
        "profile": profile,
        "source_commit": COMMIT,
        "workflow_run_url": DEPLOYMENT_RUN,
        "gateway_origin": ORIGIN,
        "render_service_id": service_id,
        "identity_provider": "okta",
        "provider_protocols": ["openai"] if profile == "external_pilot_openai" else ["anthropic", "openai"],
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
    def test_session_binding_requires_the_expected_single_client(self) -> None:
        for client, access in (("codex", ACCESS), ("claude-code", CLAUDE_ACCESS)):
            with self.subTest(client=client), patch(
                "tools.qualify_external_pilot._json_gateway",
                return_value=(_session_identity(client), {}),
            ) as gateway:
                self.assertEqual(_session_binding(ORIGIN, access, client), BINDING)
            gateway.assert_called_once_with(ORIGIN, "/v1/gateway/whoami", access_token=access)

    def test_session_binding_rejects_broad_wrong_or_non_session_identity(self) -> None:
        for changes in (
            {"allowed_clients": ["codex", "claude-code"]},
            {"allowed_clients": ["codex"]},
            {"allowed_clients": []},
            {"identity_type": "machine"},
            {"authentication_source": "static"},
            {"actor_id": ""},
            {"organization_id": None},
            {"schema_version": 2},
        ):
            with self.subTest(changes=changes), patch(
                "tools.qualify_external_pilot._json_gateway",
                return_value=(_session_identity("claude-code", **changes), {}),
            ), self.assertRaisesRegex(QualificationError, "^qualification_client_session_invalid$"):
                _session_binding(ORIGIN, CLAUDE_ACCESS, "claude-code")

    def test_different_members_are_rejected_before_provider_traffic(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._refresh", side_effect=[
                (ACCESS, ROTATED_REFRESH), (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
            ]),
            patch("tools.qualify_external_pilot._session_binding", side_effect=[
                BINDING, (BINDING[0], "another-member", BINDING[2]),
            ]),
            patch("tools.qualify_external_pilot._provider_request") as provider,
            patch("tools.qualify_external_pilot._restart_and_wait") as restart,
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(QualificationError, "^qualification_session_actor_mismatch$"),
        ):
            qualify(**_qualification_arguments())
        provider.assert_not_called()
        restart.assert_not_called()
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_REFRESH),
        ])

    def test_second_refresh_failure_revokes_rotated_and_original_families(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._refresh", side_effect=[
                (ACCESS, ROTATED_REFRESH), QualificationError("session_refresh_invalid"),
            ]),
            patch("tools.qualify_external_pilot._provider_request") as provider,
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(QualificationError, "^session_refresh_invalid$"),
        ):
            qualify(**_qualification_arguments())
        provider.assert_not_called()
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH),
            unittest.mock.call(ORIGIN, CLAUDE_REFRESH),
        ])

    def test_changed_member_after_restart_blocks_remaining_provider_traffic(self) -> None:
        for changed_client in ("codex", "claude-code"):
            changed = (BINDING[0], "another-member", BINDING[2])
            bindings = [BINDING, BINDING, changed if changed_client == "codex" else BINDING, changed]
            with (
                self.subTest(client=changed_client),
                patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
                patch("tools.qualify_external_pilot._refresh", side_effect=[
                    (ACCESS, ROTATED_REFRESH), (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
                    (ACCESS_AFTER_RESTART, ROTATED_REFRESH_AFTER_RESTART),
                    (CLAUDE_ACCESS_AFTER_RESTART, CLAUDE_ROTATED_AFTER_RESTART),
                ]),
                patch("tools.qualify_external_pilot._session_binding", side_effect=bindings),
                patch("tools.qualify_external_pilot._reliability", side_effect=[
                    _counters(live=0, attempts=0, first=0, failovers=0),
                    _counters(live=1, attempts=1, first=1, failovers=0),
                ]),
                patch("tools.qualify_external_pilot._provider_request") as provider,
                patch("tools.qualify_external_pilot._restart_and_wait") as restart,
                patch("tools.qualify_external_pilot._logout") as logout,
                self.assertRaisesRegex(QualificationError, "^qualification_session_actor_mismatch$"),
            ):
                qualify(**_qualification_arguments())
            provider.assert_called_once_with(
                ORIGIN, ACCESS, protocol="openai", alias="openai-secondary", stream=False,
            )
            restart.assert_called_once()
            self.assertEqual(logout.call_args_list, [
                unittest.mock.call(ORIGIN, ROTATED_REFRESH_AFTER_RESTART),
                unittest.mock.call(ORIGIN, CLAUDE_ROTATED_AFTER_RESTART),
            ])

    def test_revocation_failure_still_attempts_the_other_session(self) -> None:
        for errors in ((OSError("transport failed"), None), (None, OSError("transport failed"))):
            with self.subTest(first_fails=errors[0] is not None), patch(
                "tools.qualify_external_pilot._logout", side_effect=errors,
            ) as logout, self.assertRaisesRegex(QualificationError, "^qualification_session_cleanup_failed$"):
                _logout_sessions(ORIGIN, (ROTATED_REFRESH, CLAUDE_ROTATED_REFRESH))
            self.assertEqual(logout.call_args_list, [
                unittest.mock.call(ORIGIN, ROTATED_REFRESH),
                unittest.mock.call(ORIGIN, CLAUDE_ROTATED_REFRESH),
            ])

    def test_cleanup_failure_preserves_the_primary_error(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._refresh", side_effect=[
                (ACCESS, ROTATED_REFRESH), (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
            ]),
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
            patch("tools.qualify_external_pilot._reliability", side_effect=QualificationError("probe_failed")),
            patch("tools.qualify_external_pilot._logout", side_effect=[OSError("transport failed"), None]) as logout,
            self.assertRaisesRegex(QualificationError, "^probe_failed$"),
        ):
            qualify(**_qualification_arguments())
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_REFRESH),
        ])

    def test_abort_gateway_response_forces_the_underlying_socket_closed(self) -> None:
        class Response:
            def __init__(self) -> None:
                self.connection = socket.socket()
                self.fp = SimpleNamespace(
                    raw=SimpleNamespace(_sock=self.connection)
                )
                self.closed = False

            def close(self) -> None:
                self.closed = True

        response = Response()
        _abort_gateway_response(response)
        self.assertTrue(response.closed)
        self.assertEqual(response.connection.fileno(), -1)

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

        # Authenticated artifact bytes must bind the explicitly requested scope.
        for observed in ("external_pilot", "external_pilot_openai"):
            for requested in ("external_pilot", "external_pilot_openai"):
                payload = _deployment_artifact(profile=observed)
                metadata = {**artifact, "size_in_bytes": len(payload)}
                with self.subTest(observed=observed, requested=requested), patch(
                    "tools.qualify_external_pilot._github_api_json",
                    side_effect=[run, {"total_count": 1, "artifacts": [metadata]}],
                ), patch("tools.qualify_external_pilot._github_api_bytes", return_value=payload):
                    arguments = dict(expected_commit=COMMIT, service_id=SERVICE_ID,
                                     origin=ORIGIN, profile=requested)
                    if observed != requested:
                        with self.assertRaisesRegex(QualificationError, "deployment_evidence_binding_invalid"):
                            _authenticate_deployment_evidence(DEPLOYMENT_RUN, **arguments)
                    else:
                        result = _authenticate_deployment_evidence(DEPLOYMENT_RUN, **arguments)
                        self.assertEqual(result["profile"], requested)

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

    def test_provider_transport_failure_is_content_free(self) -> None:
        class Response:
            status = 200
            headers = {
                "X-Hormuz-Requested-Model": "openai-primary",
                "X-Hormuz-Routed-Model": "gpt-test",
                "Server-Timing": "hormuz_upstream_headers;dur=1.000",
            }

            def __init__(self):
                self.closed = False

            def read1(self, _size):
                raise http.client.IncompleteRead(b"sensitive-partial-response", 1)

            def close(self):
                self.closed = True

        response = Response()
        with (
            patch(
                "tools.qualify_external_pilot._open_gateway",
                return_value=response,
            ),
            self.assertRaisesRegex(
                QualificationError, "^provider_response_invalid$"
            ),
        ):
            _provider_request(
                ORIGIN,
                ACCESS,
                protocol="openai",
                alias="openai-primary",
                stream=True,
            )
        self.assertTrue(response.closed)

    def test_cancellation_closes_the_live_downstream_after_one_incomplete_chunk(self) -> None:
        class Response:
            status = 200
            headers = {
                "X-Hormuz-Requested-Model": "openai-primary",
                "X-Hormuz-Routed-Model": "gpt-test",
                "Server-Timing": "hormuz_upstream_headers;dur=1.000",
                "X-Hormuz-Cancellation-Rehearsal": "v1",
            }

            def __init__(self):
                self.reads = 0
                self.closed = False

            def read1(self, _size):
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError("cancellation must not read the stream to EOF")
                return b'event: response.output_text.delta\ndata: {"delta":"x"}\n\n'

            def close(self):
                self.closed = True

        response = Response()
        with (
            patch("tools.qualify_external_pilot._open_gateway", return_value=response),
            patch("tools.qualify_external_pilot._abort_gateway_response") as abort,
        ):
            headers, first_before_completion, terminal_observed = _provider_request(
                ORIGIN,
                ACCESS,
                protocol="openai",
                alias="openai-primary",
                stream=True,
                rehearsal_header="X-Hormuz-Cancellation-Rehearsal",
                rehearsal_key=REHEARSAL,
                disconnect_after_first_chunk=True,
            )

        abort.assert_called_once_with(response)
        self.assertEqual(response.reads, 1)
        self.assertEqual(headers["x-hormuz-cancellation-rehearsal"], "v1")
        self.assertTrue(first_before_completion)
        self.assertFalse(terminal_observed)

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

    def test_openai_only_qualifies_both_aliases_and_revokes_only_codex(self) -> None:
        results = [({}, False, False)]
        for _ in range(2):
            results.extend([({}, False, False), ({}, True, True)])
        results += [({"x-hormuz-cancellation-rehearsal": "v1"}, True, False),
                    ({"x-hormuz-failover": "v1;reason=provider_rate_limited",
                      "x-hormuz-failover-rehearsal": "v1"}, False, False)]
        snapshots = [
            _counters(live=0, attempts=0, first=0, failovers=0),
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=5, attempts=5, first=5, failovers=0),
            _counters(live=6, attempts=6, first=6, failovers=0, unknown=1, cancellations=1),
            _counters(live=7, attempts=8, first=7, failovers=1, unknown=1, cancellations=1),
        ]
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence") as deployment,
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING) as binding,
            patch("tools.qualify_external_pilot._restart_and_wait") as restart,
            patch("tools.qualify_external_pilot._refresh", side_effect=[
                (ACCESS, ROTATED_REFRESH), (ACCESS_AFTER_RESTART, ROTATED_REFRESH_AFTER_RESTART),
            ]) as refresh,
            patch("tools.qualify_external_pilot._reliability", side_effect=snapshots),
            patch("tools.qualify_external_pilot._provider_request", side_effect=results) as provider,
            patch("tools.qualify_external_pilot._logout") as logout,
        ):
            evidence = qualify(
                origin=ORIGIN, expected_commit=COMMIT, service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN, workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH, rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
                profile="external_pilot_openai",
            )
        self.assertEqual(evidence["profile"], "external_pilot_openai")
        self.assertEqual(evidence["provider_protocols"], ["openai"])
        self.assertEqual(provider.call_count, 7)
        self.assertEqual({call.kwargs["protocol"] for call in provider.call_args_list}, {"openai"})
        self.assertEqual({call.kwargs["alias"] for call in provider.call_args_list}, {"openai-primary", "openai-secondary"})
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual([call.args[2] for call in binding.call_args_list], ["codex", "codex"])
        self.assertEqual(deployment.call_args.kwargs["profile"], "external_pilot_openai")
        self.assertEqual(restart.call_args.kwargs["profile"], "external_pilot_openai")
        logout.assert_called_once_with(ORIGIN, ROTATED_REFRESH_AFTER_RESTART)
        self.assertTrue(evidence["cancellation_verified"])
        self.assertTrue(evidence["recovery_drill_passed"])
        self.assertTrue(evidence["policy_bounded_same_protocol_failover_verified"])
        self.assertFalse(evidence["availability_sla_claimed"])

    def test_openai_scope_rejects_unneeded_credentials_and_untrusted_deployment_before_token_use(self) -> None:
        arguments = dict(origin=ORIGIN, expected_commit=COMMIT, service_id=SERVICE_ID,
                         deployment_evidence_url=DEPLOYMENT_RUN, workflow_run_url=QUALIFICATION_RUN,
                         refresh_token=REFRESH, rehearsal_key=REHEARSAL,
                         deploy_hook="unused", profile="external_pilot_openai")
        with patch("tools.qualify_external_pilot._refresh") as refresh, patch(
            "tools.qualify_external_pilot._logout",
        ) as logout:
            with self.assertRaisesRegex(QualificationError, "qualification_input_invalid"):
                qualify(**arguments, claude_code_refresh_token=CLAUDE_REFRESH)
            with patch("tools.qualify_external_pilot._authenticate_deployment_evidence",
                       side_effect=QualificationError("deployment_evidence_binding_invalid")):
                with self.assertRaisesRegex(QualificationError, "deployment_evidence_binding_invalid"):
                    qualify(**arguments)
            refresh.assert_not_called()
            logout.assert_not_called()

    def test_restart_and_live_provider_observations_bind_strict_evidence(self) -> None:
        provider_results = [
            ({"server-timing": "hormuz_upstream_headers;dur=1.000"}, False, False)
        ]
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
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=9, attempts=9, first=9, failovers=0),
            _counters(live=9, attempts=9, first=9, failovers=0),
            _counters(live=10, attempts=10, first=10, failovers=0, unknown=1, cancellations=1),
            _counters(live=11, attempts=12, first=11, failovers=1, unknown=1, cancellations=1),
        ]
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence") as deployment,
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
            patch("tools.qualify_external_pilot._restart_and_wait", return_value="c" * 16) as restart,
            patch(
                "tools.qualify_external_pilot._refresh",
                side_effect=[
                    (ACCESS, ROTATED_REFRESH),
                    (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
                    (ACCESS_AFTER_RESTART, ROTATED_REFRESH_AFTER_RESTART),
                    (CLAUDE_ACCESS_AFTER_RESTART, CLAUDE_ROTATED_AFTER_RESTART),
                ],
            ) as refresh,
            patch("tools.qualify_external_pilot._reliability", side_effect=snapshots) as reliability,
            patch("tools.qualify_external_pilot._provider_request", side_effect=provider_results) as provider,
            patch("tools.qualify_external_pilot._logout") as logout,
            patch("tools.qualify_external_pilot.time.sleep") as sleep,
        ):
            evidence = qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                claude_code_refresh_token=CLAUDE_REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        restart.assert_called_once()
        deployment.assert_called_once_with(
            DEPLOYMENT_RUN,
            expected_commit=COMMIT,
            service_id=SERVICE_ID,
            origin=ORIGIN,
            profile="external_pilot",
        )
        self.assertEqual(
            refresh.call_args_list,
            [
                unittest.mock.call(ORIGIN, REFRESH),
                unittest.mock.call(ORIGIN, CLAUDE_REFRESH),
                unittest.mock.call(ORIGIN, ROTATED_REFRESH),
                unittest.mock.call(ORIGIN, CLAUDE_ROTATED_REFRESH),
            ],
        )
        self.assertEqual(reliability.call_count, 7)
        sleep.assert_called_once()
        self.assertEqual(provider.call_count, 11)
        self.assertEqual(provider.call_args_list[0].args[1], ACCESS)
        for call in provider.call_args_list[1:]:
            expected_access = CLAUDE_ACCESS_AFTER_RESTART if call.kwargs["protocol"] == "anthropic" else ACCESS_AFTER_RESTART
            self.assertEqual(call.args[1], expected_access)
        self.assertTrue(provider.call_args_list[-2].kwargs["disconnect_after_first_chunk"])
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH_AFTER_RESTART),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_AFTER_RESTART),
        ])
        self.assertEqual(evidence["schema_id"], "hormuz.external-pilot-qualification-evidence")
        self.assertEqual(evidence["deployment_evidence_url"], DEPLOYMENT_RUN)
        self.assertEqual(evidence["recovery_evidence_url"], QUALIFICATION_RUN)
        self.assertEqual(evidence["live_provider_request_count"], 11)
        self.assertEqual(evidence["provider_attempt_record_count"], 12)
        self.assertEqual(evidence["failover_link_record_count"], 1)
        self.assertEqual(evidence["cancellation_replay_count"], 0)
        rendered = repr(evidence)
        for secret in (
            REFRESH,
            ROTATED_REFRESH,
            ROTATED_REFRESH_AFTER_RESTART,
            ACCESS,
            ACCESS_AFTER_RESTART,
            REHEARSAL,
            CLAUDE_REFRESH, CLAUDE_ROTATED_REFRESH, CLAUDE_ROTATED_AFTER_RESTART,
            CLAUDE_ACCESS, CLAUDE_ACCESS_AFTER_RESTART,
        ):
            self.assertNotIn(secret, rendered)

    def test_restart_requires_the_seeded_postgres_counters_to_survive(self) -> None:
        snapshots = [
            _counters(live=0, attempts=0, first=0, failovers=0),
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=0, attempts=0, first=0, failovers=0),
        ]
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
            patch("tools.qualify_external_pilot._restart_and_wait"),
            patch(
                "tools.qualify_external_pilot._refresh",
                side_effect=[
                    (ACCESS, ROTATED_REFRESH),
                    (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
                    (ACCESS_AFTER_RESTART, ROTATED_REFRESH_AFTER_RESTART),
                    (CLAUDE_ACCESS_AFTER_RESTART, CLAUDE_ROTATED_AFTER_RESTART),
                ],
            ),
            patch("tools.qualify_external_pilot._reliability", side_effect=snapshots),
            patch(
                "tools.qualify_external_pilot._provider_request",
                return_value=(
                    {"server-timing": "hormuz_upstream_headers;dur=1.000"},
                    False,
                    False,
                ),
            ),
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(
                QualificationError,
                "^postgresql_recovery_evidence_missing$",
            ),
        ):
            qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                claude_code_refresh_token=CLAUDE_REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH_AFTER_RESTART),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_AFTER_RESTART),
        ])

    def test_cancellation_evidence_wait_is_bounded(self) -> None:
        snapshot = _counters(live=8, attempts=8, first=8, failovers=0)
        with (
            patch(
                "tools.qualify_external_pilot._reliability",
                return_value=snapshot,
            ) as reliability,
            self.assertRaisesRegex(
                QualificationError,
                "^cancellation_evidence_timed_out$",
            ),
        ):
            _wait_for_cancellation_evidence(
                ORIGIN,
                ACCESS,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                before=snapshot,
                timeout_seconds=0,
            )
        reliability.assert_called_once()

    def test_cancellation_wait_requires_both_durable_evidence_and_idle_provider(self) -> None:
        before = _counters(live=5, attempts=5, first=5, failovers=0)
        recorded = _counters(live=6, attempts=6, first=6, failovers=0, unknown=1, cancellations=1)
        snapshots = [
            before | {"provider_inflight": 1},
            recorded | {"provider_inflight": 1},
            recorded,
        ]
        with (
            patch("tools.qualify_external_pilot._json_gateway", side_effect=[
                (snapshot, {"cache-control": "no-store"}) for snapshot in snapshots
            ]) as gateway,
            patch("tools.qualify_external_pilot.time.monotonic", return_value=0),
            patch("tools.qualify_external_pilot.time.sleep") as sleep,
        ):
            result = _wait_for_cancellation_evidence(
                ORIGIN, ACCESS, expected_commit=COMMIT, service_id=SERVICE_ID, before=before,
            )
        self.assertEqual(result, recorded)
        self.assertEqual(gateway.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_recorded_cancellation_cannot_pass_while_provider_remains_inflight(self) -> None:
        before = _counters(live=5, attempts=5, first=5, failovers=0)
        snapshot = _counters(live=6, attempts=6, first=6, failovers=0, unknown=1, cancellations=1)
        snapshot["provider_inflight"] = 1
        with (
            patch("tools.qualify_external_pilot._json_gateway", return_value=(snapshot, {"cache-control": "no-store"})),
            patch("tools.qualify_external_pilot.time.monotonic", side_effect=[0, 0, 1]),
            patch("tools.qualify_external_pilot.time.sleep") as sleep,
            self.assertRaisesRegex(QualificationError, "^cancellation_evidence_timed_out$"),
        ):
            _wait_for_cancellation_evidence(
                ORIGIN, ACCESS, expected_commit=COMMIT, service_id=SERVICE_ID,
                before=before, timeout_seconds=1,
            )
        sleep.assert_called_once()

    def test_reliability_checkpoints_still_require_idle_provider(self) -> None:
        snapshot = _counters(live=5, attempts=5, first=5, failovers=0) | {"provider_inflight": 1}
        with (
            patch("tools.qualify_external_pilot._json_gateway", return_value=(snapshot, {"cache-control": "no-store"})),
            self.assertRaisesRegex(QualificationError, "^provider_reliability_summary_invalid$"),
        ):
            _reliability(ORIGIN, ACCESS, expected_commit=COMMIT, service_id=SERVICE_ID)

    def test_cancellation_poll_rejects_invalid_summaries_without_retrying(self) -> None:
        before = _counters(live=5, attempts=5, first=5, failovers=0)
        changes = (
            {"provider_inflight": 9},
            {"provider_inflight": -1},
            {"provider_inflight": True},
            {"provider_inflight": "1"},
            {"provider_capacity": 9},
            {"scope": "all_actors"},
            {"unexpected_field": 1},
            {"deployment": before["deployment"] | {"source_commit": "c" * 40}},
            {"deployment": before["deployment"] | {"service_id": "srv-other"}},
            {"deployment": before["deployment"] | {"external_origin": "https://other.example"}},
        )
        for change in changes:
            with (
                self.subTest(change=change),
                patch("tools.qualify_external_pilot._json_gateway", return_value=(before | change, {"cache-control": "no-store"})) as gateway,
                patch("tools.qualify_external_pilot.time.sleep") as sleep,
                self.assertRaisesRegex(QualificationError, "^provider_reliability_summary_invalid$"),
            ):
                _wait_for_cancellation_evidence(
                    ORIGIN, ACCESS, expected_commit=COMMIT, service_id=SERVICE_ID, before=before,
                )
            gateway.assert_called_once()
            sleep.assert_not_called()

    def test_unexpected_unknown_provider_outcome_rejects_qualification(self) -> None:
        provider_results = [
            ({"server-timing": "hormuz_upstream_headers;dur=1.000"}, False, False)
        ]
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
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=1, attempts=1, first=1, failovers=0),
            _counters(live=9, attempts=9, first=9, failovers=0, unknown=1),
            _counters(live=10, attempts=10, first=10, failovers=0, unknown=2, cancellations=1),
            _counters(live=11, attempts=12, first=11, failovers=1, unknown=2, cancellations=1),
        ]
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
            patch("tools.qualify_external_pilot._restart_and_wait"),
            patch(
                "tools.qualify_external_pilot._refresh",
                side_effect=[
                    (ACCESS, ROTATED_REFRESH),
                    (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
                    (ACCESS_AFTER_RESTART, ROTATED_REFRESH_AFTER_RESTART),
                    (CLAUDE_ACCESS_AFTER_RESTART, CLAUDE_ROTATED_AFTER_RESTART),
                ],
            ),
            patch(
                "tools.qualify_external_pilot._reliability",
                side_effect=snapshots,
            ),
            patch(
                "tools.qualify_external_pilot._provider_request",
                side_effect=provider_results,
            ),
            patch("tools.qualify_external_pilot._logout") as logout,
            self.assertRaisesRegex(
                QualificationError,
                "provider_reliability_evidence_incomplete",
            ),
        ):
            qualify(
                origin=ORIGIN,
                expected_commit=COMMIT,
                service_id=SERVICE_ID,
                deployment_evidence_url=DEPLOYMENT_RUN,
                workflow_run_url=QUALIFICATION_RUN,
                refresh_token=REFRESH,
                claude_code_refresh_token=CLAUDE_REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH_AFTER_RESTART),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_AFTER_RESTART),
        ])

    def test_rotated_qualification_session_is_revoked_after_failure(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
            patch("tools.qualify_external_pilot._restart_and_wait"),
            patch("tools.qualify_external_pilot._refresh", side_effect=[
                (ACCESS, ROTATED_REFRESH), (CLAUDE_ACCESS, CLAUDE_ROTATED_REFRESH),
            ]),
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
                claude_code_refresh_token=CLAUDE_REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, ROTATED_REFRESH),
            unittest.mock.call(ORIGIN, CLAUDE_ROTATED_REFRESH),
        ])

    def test_original_refresh_credential_revokes_family_when_rotation_response_is_lost(self) -> None:
        with (
            patch("tools.qualify_external_pilot._authenticate_deployment_evidence"),
            patch("tools.qualify_external_pilot._session_binding", return_value=BINDING),
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
                claude_code_refresh_token=CLAUDE_REFRESH,
                rehearsal_key=REHEARSAL,
                deploy_hook=f"https://api.render.com/deploy/{SERVICE_ID}?key={'z' * 32}",
            )
        self.assertEqual(logout.call_args_list, [
            unittest.mock.call(ORIGIN, REFRESH),
            unittest.mock.call(ORIGIN, CLAUDE_REFRESH),
        ])

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

    def test_render_issued_short_hook_key_keeps_exact_commit_binding(self) -> None:
        # Synthetic key with the length observed in the real Render dashboard.
        hook = f"https://api.render.com/deploy/{SERVICE_ID}?key=exampleKey1"
        self.assertEqual(_deploy_hook_url(hook, SERVICE_ID, COMMIT), hook + "&ref=" + COMMIT)

    def test_deploy_hook_rejects_ambiguous_or_unsafe_credentials(self) -> None:
        base = f"https://api.render.com/deploy/{SERVICE_ID}"
        for invalid in (
            base + "?key=",
            base + "?key=" + "z" * 513,
            base + "?key=example+key",
            base + "?key=example%0Akey",
            base + "?key=example%2Fkey",
            base + "?key=exampleKey1&key=exampleKey2",
            base + "?key=exampleKey1&ref=" + "c" * 40,
            base + "?key=exampleKey1#fragment",
            f"https://user@api.render.com/deploy/{SERVICE_ID}?key=exampleKey1",
            f"https://api.render.com:444/deploy/{SERVICE_ID}?key=exampleKey1",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(QualificationError):
                _deploy_hook_url(invalid, SERVICE_ID, COMMIT)

    def test_workflow_keeps_live_credentials_in_the_protected_job(self) -> None:
        workflow = (ROOT / ".github/workflows/external-pilot-qualification.yml").read_text()
        self.assertEqual(workflow.count("environment: external-pilot-qualification"), 2)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_EXTERNAL_PILOT_REFRESH_TOKEN }}"), 1)
        self.assertEqual(workflow.count("${{ inputs.profile == 'external_pilot' && secrets.HORMUZ_EXTERNAL_PILOT_CLAUDE_CODE_REFRESH_TOKEN || '' }}"), 1)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_FAILOVER_REHEARSAL_KEY }}"), 1)
        self.assertEqual(workflow.count("${{ secrets.HORMUZ_RENDER_DEPLOY_HOOK_URL }}"), 1)
        self.assertEqual(workflow.count("${{ vars.HORMUZ_GATEWAY_ORIGIN }}"), 1)
        self.assertEqual(workflow.count("${{ vars.HORMUZ_RENDER_SERVICE_ID }}"), 1)
        credential_step = workflow.split(
            "- name: Restart exact deployment and run content-free qualification",
            1,
        )[1]
        self.assertIn("${{ secrets.HORMUZ_EXTERNAL_PILOT_REFRESH_TOKEN }}", credential_step)
        self.assertIn("${{ inputs.profile == 'external_pilot' && secrets.HORMUZ_EXTERNAL_PILOT_CLAUDE_CODE_REFRESH_TOKEN || '' }}", credential_step)
        self.assertNotIn("inputs.gateway_origin", credential_step)
        self.assertNotIn("inputs.render_service_id", credential_step)
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("qualify_external_pilot.py", workflow)
        self.assertIn("external-pilot-qualification-evidence.json", workflow)


if __name__ == "__main__":
    unittest.main()
