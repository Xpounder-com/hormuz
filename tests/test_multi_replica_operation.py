from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.verify_helm_profile import build_evidence as build_kubernetes_evidence
from tools.verify_multi_replica_operation import (
    MultiReplicaProofError,
    OPERATION_EVENTS,
    OPERATION_SCHEMA_ID,
    STATE_LIMITATIONS,
    STATE_POSTGRES_IMAGE,
    STATE_SCHEMA_ID,
    STATE_TESTS,
    TIMING_LIMITS_MS,
    build_operation_evidence,
    validate_operation_evidence,
    validate_state_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


class MultiReplicaOperationProofTests(unittest.TestCase):
    def test_composite_proof_binds_exact_inputs_and_keeps_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kubernetes, state, events = self._inputs(root)
            evidence = build_operation_evidence(
                source_commit="a" * 40,
                kubernetes_evidence_path=kubernetes,
                state_evidence_path=state,
                event_log_path=events,
                timings_ms={name: 1 for name in TIMING_LIMITS_MS},
                successful_requests=3,
                policy_denials=1,
                provider_requests=4,
                usage_events=3,
                outcome_unknown_attempts=1,
                uncertain_reservations=1,
            )

        validate_operation_evidence(evidence)
        self.assertEqual(evidence["schema_id"], OPERATION_SCHEMA_ID)
        self.assertFalse(evidence["retry_and_sessions"]["automatic_provider_replay"])
        self.assertEqual(evidence["retry_and_sessions"]["browser_sessions"], "excluded")
        self.assertIn("no_postgresql_leader_failover_claim", evidence["limitations"])
        self.assertIn("no_customer_sla", evidence["limitations"])

    def test_operation_proof_rejects_broadened_retry_counts_and_timing(self) -> None:
        evidence = self._operation_evidence()
        for mutation in ("retry", "counts", "timing", "unknown_field"):
            with self.subTest(mutation=mutation):
                invalid = copy.deepcopy(evidence)
                if mutation == "retry":
                    invalid["retry_and_sessions"]["automatic_provider_replay"] = True
                elif mutation == "counts":
                    invalid["state"]["provider_requests"] = 3
                elif mutation == "timing":
                    invalid["timings_ms"]["graceful_inflight_drain"] = (
                        TIMING_LIMITS_MS["graceful_inflight_drain"] + 1
                    )
                else:
                    invalid["customer_sla"] = True
                with self.assertRaises(MultiReplicaProofError):
                    validate_operation_evidence(invalid)

    def test_operation_proof_requires_the_exact_observed_event_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kubernetes, state, events = self._inputs(root)
            lines = events.read_text(encoding="utf-8").splitlines()
            lines[5], lines[6] = lines[6], lines[5]
            events.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(MultiReplicaProofError) as raised:
                build_operation_evidence(
                    source_commit="a" * 40,
                    kubernetes_evidence_path=kubernetes,
                    state_evidence_path=state,
                    event_log_path=events,
                    timings_ms={name: 1 for name in TIMING_LIMITS_MS},
                    successful_requests=3,
                    policy_denials=1,
                    provider_requests=4,
                    usage_events=3,
                    outcome_unknown_attempts=1,
                    uncertain_reservations=1,
                )
        self.assertEqual(str(raised.exception), "operation_event_log_invalid")

    def test_state_proof_is_strict_about_test_identity_and_content(self) -> None:
        evidence = self._state_evidence()
        validate_state_evidence(evidence)
        invalid = copy.deepcopy(evidence)
        invalid["checks"]["atomic_budget_and_tenant_isolation"]["test_id"] = "different.test"
        with self.assertRaises(MultiReplicaProofError):
            validate_state_evidence(invalid)
        sensitive = copy.deepcopy(evidence)
        sensitive["limitations"] = [*STATE_LIMITATIONS, "postgresql://secret"]
        with self.assertRaises(MultiReplicaProofError):
            validate_state_evidence(sensitive)

    def test_composite_proof_requires_linux_amd64_shared_state_evidence(self) -> None:
        for os_name, architecture in (("darwin", "arm64"), ("linux", "arm64")):
            with self.subTest(os=os_name, architecture=architecture):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    kubernetes, state, events = self._inputs(root)
                    value = json.loads(state.read_text(encoding="utf-8"))
                    value["runner"] = {
                        "os": os_name,
                        "architecture": architecture,
                        "python": "3.12.0",
                    }
                    state.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(MultiReplicaProofError) as raised:
                        build_operation_evidence(
                            source_commit="a" * 40,
                            kubernetes_evidence_path=kubernetes,
                            state_evidence_path=state,
                            event_log_path=events,
                            timings_ms={name: 1 for name in TIMING_LIMITS_MS},
                            successful_requests=3,
                            policy_denials=1,
                            provider_requests=4,
                            usage_events=3,
                            outcome_unknown_attempts=1,
                            uncertain_reservations=1,
                        )
                self.assertEqual(str(raised.exception), "state_runner_not_linux_amd64")

    def test_state_proof_binds_the_exact_postgresql_image(self) -> None:
        evidence = self._state_evidence()
        evidence["database"] = {
            "backend": "postgresql",
            "image": "postgres@sha256:" + "0" * 64,
        }
        with self.assertRaises(MultiReplicaProofError) as raised:
            validate_state_evidence(evidence)
        self.assertEqual(str(raised.exception), "state_database")

    def test_ci_declares_the_same_exact_postgresql_image_as_the_state_proof(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count(STATE_POSTGRES_IMAGE), 2)
        self.assertIn("--postgres-image '" + STATE_POSTGRES_IMAGE + "'", workflow)

    def _operation_evidence(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kubernetes, state, events = self._inputs(root)
            return build_operation_evidence(
                source_commit="a" * 40,
                kubernetes_evidence_path=kubernetes,
                state_evidence_path=state,
                event_log_path=events,
                timings_ms={name: 1 for name in TIMING_LIMITS_MS},
                successful_requests=3,
                policy_denials=1,
                provider_requests=4,
                usage_events=3,
                outcome_unknown_attempts=1,
                uncertain_reservations=1,
            )

    def _inputs(self, root: Path) -> tuple[Path, Path, Path]:
        kubernetes = root / "kubernetes.json"
        kubernetes.write_text(
            json.dumps(
                build_kubernetes_evidence(
                    docker_engine="27.0.0",
                    chart_package_sha256="b" * 64,
                    gateway_replicas=2,
                    distinct_gateway_nodes=2,
                    successful_requests=3,
                    policy_denials=1,
                    provider_requests=3,
                    usage_events=3,
                )
            ),
            encoding="utf-8",
        )
        state = root / "state.json"
        state.write_text(json.dumps(self._state_evidence()), encoding="utf-8")
        events = root / "events.log"
        events.write_text(
            "".join(f"{sequence}|{name}\n" for sequence, name in enumerate(OPERATION_EVENTS, start=1)),
            encoding="utf-8",
        )
        return kubernetes, state, events

    @staticmethod
    def _state_evidence() -> dict[str, object]:
        return {
            "schema_id": STATE_SCHEMA_ID,
            "schema_version": 1,
            "observed_at": "2026-08-25T00:00:00Z",
            "source_commit": "a" * 40,
            "runner": {
                "os": "linux",
                "architecture": "amd64",
                "python": "3.12.0",
            },
            "database": {
                "backend": "postgresql",
                "image": STATE_POSTGRES_IMAGE,
            },
            "checks": {
                name: {"test_id": test_id, "status": "passed", "duration_ms": 1}
                for name, test_id in sorted(STATE_TESTS.items())
            },
            "limitations": STATE_LIMITATIONS,
            "verdict": "verified_shared_state_process_replicas",
        }


if __name__ == "__main__":
    unittest.main()
