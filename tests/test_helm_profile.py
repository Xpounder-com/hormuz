from __future__ import annotations

import copy
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError

from tools import verify_helm_profile as helm_profile
from tools import verify_core_wheel as core_distribution


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "hormuz"
CONFIGURATION = "hormuz-config-v1"
RUNTIME_SECRET = "hormuz-runtime-v1"
LABELS = {
    "app.kubernetes.io/name": "hormuz",
    "app.kubernetes.io/instance": "proof",
    "app.kubernetes.io/component": "gateway",
}


def _selector() -> dict[str, object]:
    return {"matchLabels": dict(LABELS)}


def _container(name: str, argument: str) -> dict[str, object]:
    value: dict[str, object] = {
        "name": name,
        "image": helm_profile.HORMUZ_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["hormuz"],
        "args": [argument],
        "env": [
            {"name": "HORMUZ_CONFIG", "value": "/etc/hormuz/hormuz.json"},
            {
                "name": "HORMUZ_POSTGRES_DSN",
                "valueFrom": {
                    "secretKeyRef": {"name": RUNTIME_SECRET, "key": "postgres-runtime-dsn"}
                },
            },
            {
                "name": "HORMUZ_INGRESS_CREDENTIAL",
                "valueFrom": {
                    "secretKeyRef": {"name": RUNTIME_SECRET, "key": "ingress-credential"}
                },
            },
        ],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "250m", "memory": "256Mi"},
            "limits": {"cpu": "2", "memory": "2Gi"},
        },
        "volumeMounts": [
            {"name": "configuration", "mountPath": "/etc/hormuz", "readOnly": True},
            {"name": "temporary", "mountPath": "/tmp"},
        ],
    }
    if name == "gateway":
        probe_code = "credential=os.environ['HORMUZ_INGRESS_CREDENTIAL']"
        value.update(
            {
                "ports": [{"name": "http-private", "containerPort": 8787, "protocol": "TCP"}],
                "livenessProbe": {
                    "exec": {"command": ["/opt/hormuz/bin/python", "-I", "-c", probe_code]}
                },
                "readinessProbe": {
                    "exec": {"command": ["/opt/hormuz/bin/python", "-I", "-c", probe_code]}
                },
                "lifecycle": {
                    "preStop": {
                        "exec": {
                            "command": [
                                "/opt/hormuz/bin/python",
                                "-I",
                                "-c",
                                "import sys, time; time.sleep(int(sys.argv[1]))",
                                "10",
                            ]
                        }
                    }
                },
            }
        )
    return value


def _network_policy(role: str, spec: dict[str, object]) -> dict[str, object]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"proof-hormuz-{role}",
            "annotations": {"io.hormuz/network-policy-role": role},
        },
        "spec": {"podSelector": _selector(), **spec},
    }


def valid_manifest() -> dict[str, object]:
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "proof-hormuz",
            "annotations": {"io.hormuz/contract": helm_profile.CONTRACT_SCHEMA},
        },
        "spec": {
            "replicas": 2,
            "minReadySeconds": 5,
            "progressDeadlineSeconds": 600,
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "selector": _selector(),
            "template": {
                "metadata": {
                    "labels": dict(LABELS),
                    "annotations": {
                        "io.hormuz/config-sha256": "sha256:" + "a" * 64,
                        "io.hormuz/runtime-secret-revision": "conformance-generation-v1",
                        "io.hormuz/image-digest": helm_profile.HORMUZ_IMAGE.partition("@")[2],
                    },
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": 660,
                    "nodeSelector": {
                        "kubernetes.io/os": "linux",
                        "kubernetes.io/arch": "amd64",
                    },
                    "topologySpreadConstraints": [
                        {
                            "maxSkew": 1,
                            "topologyKey": "kubernetes.io/hostname",
                            "whenUnsatisfiable": "DoNotSchedule",
                            "nodeAffinityPolicy": "Honor",
                            "nodeTaintsPolicy": "Honor",
                            "labelSelector": _selector(),
                        }
                    ],
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "runAsGroup": 65532,
                        "fsGroup": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "initContainers": [_container("configuration-preflight", "doctor")],
                    "containers": [_container("gateway", "serve")],
                    "volumes": [
                        {
                            "name": "configuration",
                            "configMap": {
                                "name": CONFIGURATION,
                                "defaultMode": 288,
                                "items": [{"key": "hormuz.json", "path": "hormuz.json"}],
                            },
                        },
                        {
                            "name": "temporary",
                            "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"},
                        },
                    ],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "proof-hormuz"},
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.96.1.2",
            "selector": dict(LABELS),
            "ports": [
                {
                    "name": "http-private",
                    "port": 8787,
                    "targetPort": "http-private",
                    "protocol": "TCP",
                }
            ],
        },
    }
    pdb = {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": "proof-hormuz"},
        "spec": {"minAvailable": 1, "selector": _selector()},
    }
    policies = [
        _network_policy(
            "default-deny",
            {"policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []},
        ),
        _network_policy(
            "dns-egress",
            {
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                },
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    }
                ],
            },
        ),
        _network_policy(
            "customer-ingress",
            {
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "hormuz-ingress"
                                    }
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 8787}],
                    }
                ],
            },
        ),
        _network_policy(
            "customer-egress",
            {
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "hormuz-dependencies"
                                    }
                                },
                                "podSelector": {"matchLabels": {"app": "postgres"}},
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 5432}],
                    }
                ],
            },
        ),
    ]
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [deployment, service, pdb, *policies],
    }


def valid_serving_generation() -> dict[str, object]:
    deployment = copy.deepcopy(valid_manifest()["items"][0])
    deployment["metadata"]["generation"] = 2
    deployment["status"] = {
        "observedGeneration": 2,
        "replicas": 2,
        "updatedReplicas": 2,
        "readyReplicas": 2,
        "availableReplicas": 2,
    }
    template = deployment["spec"]["template"]
    pods = []
    for index in range(2):
        pods.append(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": f"proof-hormuz-{index}",
                    "labels": copy.deepcopy(template["metadata"]["labels"]),
                    "annotations": copy.deepcopy(
                        template["metadata"]["annotations"]
                    ),
                },
                "spec": copy.deepcopy(template["spec"]),
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        )
    return {"apiVersion": "v1", "kind": "List", "items": [deployment, *pods]}


class HelmChartContractTests(unittest.TestCase):
    def test_repository_chart_satisfies_the_static_contract(self) -> None:
        digest = helm_profile.validate_chart(CHART)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_account_free_reference_pins_kind_kubernetes_and_cilium(self) -> None:
        fixture = ROOT / "deploy" / "kubernetes" / "conformance"
        kind = (fixture / "kind.yaml").read_text(encoding="utf-8")
        cilium = (fixture / "cilium-values.yaml").read_text(encoding="utf-8")
        postgres = (fixture / "postgres.yaml").read_text(encoding="utf-8")
        proof_values = (fixture / "helm-values.yaml").read_text(encoding="utf-8")
        runner = (ROOT / "tools" / "verify_helm_profile.sh").read_text(encoding="utf-8")
        chart_values = (CHART / "values.yaml").read_text(encoding="utf-8")
        deployment_template = (CHART / "templates" / "deployment.yaml").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "deploy" / "kubernetes" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(kind.count(helm_profile.KIND_NODE_IMAGE), 3)
        self.assertIn("disableDefaultCNI: true", kind)
        self.assertIn(helm_profile.CILIUM_AGENT_IMAGE.partition("@")[2], cilium)
        self.assertIn(helm_profile.CILIUM_OPERATOR_IMAGE.partition("@")[2], cilium)
        self.assertIn(helm_profile.POSTGRES_IMAGE, postgres)
        self.assertIn(f'KIND_VERSION="{helm_profile.KIND_VERSION}"', runner)
        self.assertIn(f'CILIUM_VERSION="{helm_profile.CILIUM_VERSION}"', runner)
        self.assertIn("kind delete cluster", runner)
        self.assertNotIn("kind load docker-image", runner)
        self.assertNotIn("docker pull", runner)
        self.assertIn("write_random_hex_secret", runner)
        self.assertIn("emit_job_diagnostics", runner)
        self.assertIn("capture_gateway_logs v1-before-upgrade", runner)
        self.assertIn("capture_gateway_logs v2-before-rollback", runner)
        self.assertIn("capture_gateway_logs v1-before-replica-deletion", runner)
        self.assertIn("capture_gateway_logs v1-after-replica-replacement", runner)
        self.assertIn("wait_for_job_log_marker", runner)
        self.assertIn("wait_for_serving_generation", runner)
        self.assertIn("validate-serving-generation", runner)
        self.assertIn("kubectl --namespace \"${namespace}\" rollout status", runner)
        self.assertIn("--from=cronjob/replacement-traffic", runner)
        self.assertIn("--from=cronjob/blocking-request", runner)
        self.assertIn("--grace-period=0 --force", runner)
        self.assertIn("wait_for_provider_disconnect", runner)
        self.assertIn("get endpointslices", runner)
        self.assertNotIn("get endpoints hormuz-hormuz", runner)
        self.assertIn("write-operation-proof", runner)
        self.assertIn(
            "REQUEST_ATTEMPT_STALE_SECONDS=$((UPSTREAM_TIMEOUT_SECONDS + 60))",
            runner,
        )
        self.assertIn('sleep "$((REQUEST_ATTEMPT_STALE_SECONDS + 2))"', runner)
        self.assertIn("synthetic traffic did not remain active", runner)
        self.assertIn("rendered-yaml-keywords.yaml", runner)
        self.assertIn('runtimeSecret.env.NO=true', runner)
        self.assertNotIn('openssl rand -hex 32 >', runner)
        self.assertNotIn("    HORMUZ_TOKEN:", chart_values)
        self.assertNotIn("    OPENAI_API_KEY:", chart_values)
        self.assertNotIn("    ANTHROPIC_API_KEY:", chart_values)
        self.assertIn("    HORMUZ_TOKEN: hormuz-identity-token", proof_values)
        self.assertIn("    OPENAI_API_KEY: openai-api-key", proof_values)
        self.assertIn("    ANTHROPIC_API_KEY: anthropic-api-key", proof_values)
        self.assertIn("name: {{ $name | quote }}", deployment_template)
        self.assertIn("key: {{ $key | quote }}", deployment_template)
        self.assertIn("endpointDrainSeconds: 10", chart_values)
        self.assertIn(".Values.endpointDrainSeconds | quote", deployment_template)
        self.assertIn("preStop:", deployment_template)
        for runner_name in (
            "verify_helm_profile.sh",
            "verify_postgres_ha_reference.sh",
            "verify_disaster_recovery_reference.sh",
        ):
            runner_source = (ROOT / "tools" / runner_name).read_text(encoding="utf-8")
            self.assertIn(f"/chart/hormuz-{helm_profile.CHART_VERSION}.tgz", runner_source)
        self.assertLess(
            readme.index("kubectl create namespace hormuz-system"),
            readme.index("kubectl --namespace hormuz-system create configmap"),
        )
        self.assertNotIn("apiVersion: cilium.io/", (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Kubernetes + Helm multi-replica reference", workflow)
        self.assertIn("HORMUZ_KUBERNETES_PROOF_ACK", workflow)
        self.assertNotIn("aws ", runner)
        self.assertNotIn("az ", runner)
        self.assertNotIn("gcloud ", runner)

    def test_probe_accepts_projected_secret_links_but_rejects_mount_escape(self) -> None:
        probe = runpy.run_path(
            str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
        )
        read_projected_secret = probe["_read_projected_secret"]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            mount = temporary_root / "secret"
            revision = mount / "..2026_08_25_00_00_00"
            revision.mkdir(parents=True)
            (revision / "identity-token").write_text("synthetic-token", encoding="utf-8")
            (mount / "..data").symlink_to(revision.name, target_is_directory=True)
            (mount / "identity-token").symlink_to("..data/identity-token")
            self.assertEqual(
                read_projected_secret(mount, "identity-token"),
                "synthetic-token",
            )

            outside = temporary_root / "outside"
            outside.write_text("must-not-read", encoding="utf-8")
            (mount / "escaped").symlink_to(outside)
            with self.assertRaisesRegex(SystemExit, "proof_secret_unavailable"):
                read_projected_secret(mount, "escaped")

    def test_replacement_probe_emits_a_start_barrier_and_strict_summary(self) -> None:
        probe = runpy.run_path(
            str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
        )
        replacement = probe["_run_replacement_traffic"]
        request = mock.Mock(return_value={"status": 200})
        original_request = replacement.__globals__["_governed_request"]
        replacement.__globals__["_governed_request"] = request
        output = io.StringIO()
        try:
            with (
                mock.patch.object(
                    probe["time"], "monotonic", side_effect=(0, 0, 1, 15)
                ),
                mock.patch.object(probe["time"], "sleep"),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    replacement(
                        target="http://hormuz.invalid",
                        headers={"Authorization": "synthetic"},
                        expected_policy="fallback+capped+redacted",
                        duration_seconds=15,
                    ),
                    0,
                )
        finally:
            replacement.__globals__["_governed_request"] = original_request
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0], {"event": "traffic_started"})
        self.assertEqual(
            lines[-1],
            {
                "command": "replacement-traffic",
                "failed_requests": 0,
                "successful_requests": 2,
            },
        )
        self.assertEqual(request.call_count, 2)

    def test_replacement_probe_reports_transport_failures_without_retry_or_secrets(self) -> None:
        cases = (
            (URLError(ConnectionRefusedError("private-target-and-credential")), "connection_refused"),
            (ConnectionResetError("private-target-and-credential"), "connection_reset"),
            (TimeoutError("private-target-and-credential"), "timeout"),
            (URLError("private-target-and-credential"), "transport"),
        )
        for error, failure_kind in cases:
            with self.subTest(failure_kind=failure_kind):
                probe = runpy.run_path(
                    str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
                )
                replacement = probe["_run_replacement_traffic"]
                request = mock.Mock(side_effect=({"status": 200}, error, {"status": 200}))
                output = io.StringIO()
                with (
                    mock.patch.dict(replacement.__globals__, {"_governed_request": request}),
                    mock.patch.object(probe["time"], "monotonic", side_effect=(0, 0, 1, 2, 15)),
                    mock.patch.object(probe["time"], "sleep"),
                    contextlib.redirect_stdout(output),
                    self.assertRaisesRegex(SystemExit, "^replacement_traffic_failed$"),
                ):
                    replacement(
                        target="http://private-target.invalid",
                        headers={"Authorization": "private-target-and-credential"},
                        expected_policy="fallback+capped+redacted",
                        duration_seconds=15,
                    )
                lines = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(lines[0], {"event": "traffic_started"})
                self.assertEqual(
                    lines[-1],
                    {
                        "command": "replacement-traffic",
                        "successful_requests": 2,
                        "failed_requests": 1,
                        "failure_counts": {failure_kind: 1},
                    },
                )
                self.assertEqual(request.call_count, 3)
                self.assertNotIn("private-target", output.getvalue())

    def test_replacement_probe_reports_contract_failure_and_stops_immediately(self) -> None:
        for error, failure_kind in (
            (SystemExit("unexpected_status:503"), "unexpected_status"),
            (SystemExit("policy_decision_invalid"), "policy_decision"),
            (SystemExit("private-response-body"), "response_contract"),
            (ValueError("private-response-body"), "response_contract"),
        ):
            with self.subTest(failure_kind=failure_kind):
                probe = runpy.run_path(
                    str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
                )
                replacement = probe["_run_replacement_traffic"]
                request = mock.Mock(side_effect=error)
                output = io.StringIO()
                with (
                    mock.patch.dict(replacement.__globals__, {"_governed_request": request}),
                    mock.patch.object(probe["time"], "monotonic", side_effect=(0, 0)),
                    mock.patch.object(probe["time"], "sleep") as sleep,
                    contextlib.redirect_stdout(output),
                    self.assertRaisesRegex(SystemExit, "^replacement_traffic_failed$"),
                ):
                    replacement(
                        target="http://hormuz.invalid",
                        headers={},
                        expected_policy="fallback+capped+redacted",
                        duration_seconds=15,
                    )
                self.assertEqual(
                    json.loads(output.getvalue()),
                    {
                        "command": "replacement-traffic",
                        "successful_requests": 0,
                        "failed_requests": 1,
                        "failure_counts": {failure_kind: 1},
                    },
                )
                request.assert_called_once()
                sleep.assert_not_called()
                self.assertNotIn("private-response", output.getvalue())

    def test_blocking_probe_distinguishes_graceful_completion_from_ambiguous_loss(self) -> None:
        probe = runpy.run_path(
            str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
        )
        run_blocking = probe["_run_blocking_request"]
        request = mock.Mock(return_value={"command": "request", "status": 200})
        original_request = run_blocking.__globals__["_governed_request"]
        run_blocking.__globals__["_governed_request"] = request
        graceful_output = io.StringIO()
        try:
            with contextlib.redirect_stdout(graceful_output):
                self.assertEqual(
                    run_blocking(
                        command="blocking-request",
                        target="http://hormuz.invalid",
                        headers={"Authorization": "synthetic"},
                        expected_policy="fallback+capped+redacted",
                    ),
                    0,
                )
            request.side_effect = OSError("synthetic connection loss")
            ambiguous_output = io.StringIO()
            with contextlib.redirect_stdout(ambiguous_output):
                self.assertEqual(
                    run_blocking(
                        command="ambiguous-request",
                        target="http://hormuz.invalid",
                        headers={"Authorization": "synthetic"},
                        expected_policy="fallback+capped+redacted",
                    ),
                    0,
                )
        finally:
            run_blocking.__globals__["_governed_request"] = original_request
        self.assertEqual(
            [json.loads(line) for line in graceful_output.getvalue().splitlines()],
            [
                {"event": "blocking_request_started"},
                {"command": "blocking-request", "status": 200},
            ],
        )
        self.assertEqual(
            [json.loads(line) for line in ambiguous_output.getvalue().splitlines()],
            [
                {"event": "blocking_request_started"},
                {"command": "ambiguous-request", "transport_outcome": "ambiguous"},
            ],
        )

    def test_governed_probe_classifies_status_before_validating_success_policy(self) -> None:
        probe = runpy.run_path(
            str(ROOT / "deploy" / "kubernetes" / "conformance" / "probe.py")
        )
        governed_request = probe["_governed_request"]
        request = mock.Mock(return_value=(502, {}, b""))
        original_request = governed_request.__globals__["_request"]
        governed_request.__globals__["_request"] = request
        try:
            with self.assertRaisesRegex(SystemExit, "^unexpected_status:502$"):
                governed_request(
                    target="http://hormuz.invalid",
                    headers={"Authorization": "synthetic"},
                    expected_status=200,
                    expected_policy="fallback+capped+redacted",
                )

            request.return_value = (200, {}, b'{"model":"gpt-kubernetes-proof"}')
            with self.assertRaisesRegex(SystemExit, "^policy_decision_invalid$"):
                governed_request(
                    target="http://hormuz.invalid",
                    headers={"Authorization": "synthetic"},
                    expected_status=200,
                    expected_policy="fallback+capped+redacted",
                )
        finally:
            governed_request.__globals__["_request"] = original_request

    def test_source_distribution_contract_includes_the_chart_and_live_proof(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("recursive-include deploy/helm", manifest)
        self.assertIn("recursive-include deploy/kubernetes", manifest)
        self.assertIn("tools/verify_helm_profile.py", manifest)
        self.assertIn("tools/verify_multi_replica_operation.py", manifest)
        self.assertIn("deploy/helm/hormuz/Chart.yaml", core_distribution.REQUIRED_HELM_SDIST_PATHS)
        self.assertIn(
            "deploy/kubernetes/conformance/kind.yaml",
            core_distribution.REQUIRED_HELM_SDIST_PATHS,
        )

    def test_rendered_resource_model_satisfies_the_runtime_contract(self) -> None:
        helm_profile.validate_manifest(
            valid_manifest(),
            expected_configuration=CONFIGURATION,
            expected_runtime_secret=RUNTIME_SECRET,
        )

    def test_endpoint_drain_window_is_required_and_preserves_shutdown_budget(self) -> None:
        cases = (
            (None, 660),
            ("0", 660),
            ("4", 660),
            ("-1", 660),
            ("301", 660),
            ("10; echo private-data", 660),
            ("10", 69),
        )
        for delay, grace in cases:
            with self.subTest(delay=delay, grace=grace):
                manifest = valid_manifest()
                pod = manifest["items"][0]["spec"]["template"]["spec"]
                gateway = pod["containers"][0]
                pod["terminationGracePeriodSeconds"] = grace
                if delay is None:
                    gateway.pop("lifecycle")
                else:
                    gateway["lifecycle"]["preStop"]["exec"]["command"][-1] = delay
                with self.assertRaisesRegex(helm_profile.HelmProfileError, "endpoint_drain"):
                    helm_profile.validate_manifest(
                        manifest,
                        expected_configuration=CONFIGURATION,
                        expected_runtime_secret=RUNTIME_SECRET,
                    )

        manifest = valid_manifest()
        manifest["items"][0]["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] = 70
        helm_profile.validate_manifest(
            manifest,
            expected_configuration=CONFIGURATION,
            expected_runtime_secret=RUNTIME_SECRET,
        )

    def test_endpoint_drain_hook_uses_the_pinned_python_without_a_shell(self) -> None:
        gateway = valid_manifest()["items"][0]["spec"]["template"]["spec"]["containers"][0]
        command = gateway["lifecycle"]["preStop"]["exec"]["command"]
        self.assertEqual(command[:3], ["/opt/hormuz/bin/python", "-I", "-c"])
        self.assertIn(command[3], (CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8"))
        with (
            mock.patch.object(sys, "argv", ["-c", command[4]]),
            mock.patch("time.sleep") as sleep,
        ):
            exec(command[3], {})
        sleep.assert_called_once_with(10)

    def test_mutable_or_privileged_runtime_fails_closed(self) -> None:
        mutable = valid_manifest()
        mutable["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "ghcr.io/xpounder-com/hormuz:v0.1.1"
        )
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "gateway_image"):
            helm_profile.validate_manifest(
                mutable,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

        privileged = valid_manifest()
        privileged["items"][0]["spec"]["template"]["spec"]["containers"][0][
            "securityContext"
        ]["privileged"] = True
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "gateway_security"):
            helm_profile.validate_manifest(
                privileged,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

    def test_secret_values_and_customer_dependencies_fail_closed(self) -> None:
        literal = valid_manifest()
        literal["items"][0]["spec"]["template"]["spec"]["containers"][0]["env"][1] = {
            "name": "HORMUZ_POSTGRES_DSN",
            "value": "not-allowed-in-a-rendered-manifest",
        }
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "gateway_literal_secret"):
            helm_profile.validate_manifest(
                literal,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

        owned_secret = valid_manifest()
        owned_secret["items"].append(
            {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "forbidden"}}
        )
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "chart_owns_customer_dependency"):
            helm_profile.validate_manifest(
                owned_secret,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

    def test_privileged_control_credentials_fail_closed(self) -> None:
        privileged = valid_manifest()
        for container in privileged["items"][0]["spec"]["template"]["spec"][
            "initContainers"
        ] + privileged["items"][0]["spec"]["template"]["spec"]["containers"]:
            container["env"].append(
                {
                    "name": "HORMUZ_POSTGRES_MIGRATION_DSN",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": RUNTIME_SECRET,
                            "key": "postgres-migration-dsn",
                        }
                    },
                }
            )
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "privileged_env"):
            helm_profile.validate_manifest(
                privileged,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

    def test_rollout_generation_annotations_fail_closed(self) -> None:
        wrong_configuration = valid_manifest()
        with self.assertRaisesRegex(
            helm_profile.HelmProfileError, "configuration_sha256"
        ):
            helm_profile.validate_manifest(
                wrong_configuration,
                expected_configuration=CONFIGURATION,
                expected_configuration_sha256="sha256:" + "b" * 64,
                expected_runtime_secret=RUNTIME_SECRET,
                expected_runtime_secret_revision="conformance-generation-v1",
            )

        wrong_secret = valid_manifest()
        with self.assertRaisesRegex(
            helm_profile.HelmProfileError, "runtime_input_annotations"
        ):
            helm_profile.validate_manifest(
                wrong_secret,
                expected_configuration=CONFIGURATION,
                expected_configuration_sha256="sha256:" + "a" * 64,
                expected_runtime_secret=RUNTIME_SECRET,
                expected_runtime_secret_revision="conformance-generation-v2",
            )

    def test_serving_generation_rejects_partial_or_mixed_rollouts(self) -> None:
        expected_sha256 = "sha256:" + "a" * 64
        rollout = valid_serving_generation()
        helm_profile.validate_serving_generation(
            rollout,
            expected_configuration=CONFIGURATION,
            expected_configuration_sha256=expected_sha256,
            expected_runtime_secret=RUNTIME_SECRET,
            expected_runtime_secret_revision="conformance-generation-v1",
        )

        partial = copy.deepcopy(rollout)
        partial["items"][0]["status"]["updatedReplicas"] = 1
        with self.assertRaisesRegex(
            helm_profile.HelmProfileError, "rollout_updated_replicas"
        ):
            helm_profile.validate_serving_generation(
                partial,
                expected_configuration=CONFIGURATION,
                expected_configuration_sha256=expected_sha256,
                expected_runtime_secret=RUNTIME_SECRET,
                expected_runtime_secret_revision="conformance-generation-v1",
            )

        mixed = copy.deepcopy(rollout)
        mixed["items"][1]["metadata"]["annotations"][
            "io.hormuz/config-sha256"
        ] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(
            helm_profile.HelmProfileError, "pod_configuration_sha256"
        ):
            helm_profile.validate_serving_generation(
                mixed,
                expected_configuration=CONFIGURATION,
                expected_configuration_sha256=expected_sha256,
                expected_runtime_secret=RUNTIME_SECRET,
                expected_runtime_secret_revision="conformance-generation-v1",
            )

    def test_topology_spread_excludes_unschedulable_tainted_domains(self) -> None:
        unsafe_spread = valid_manifest()
        del unsafe_spread["items"][0]["spec"]["template"]["spec"][
            "topologySpreadConstraints"
        ][0]["nodeTaintsPolicy"]
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "topology_spread"):
            helm_profile.validate_manifest(
                unsafe_spread,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

    def test_public_service_and_cni_specific_api_fail_closed(self) -> None:
        public = valid_manifest()
        public["items"][1]["spec"]["type"] = "LoadBalancer"
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "service_exposure"):
            helm_profile.validate_manifest(
                public,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

        cilium = valid_manifest()
        cilium["items"][3]["apiVersion"] = "cilium.io/v2"
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "cni_specific_resource"):
            helm_profile.validate_manifest(
                cilium,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

    def test_default_deny_and_source_restriction_fail_closed(self) -> None:
        normalized = valid_manifest()
        del normalized["items"][3]["spec"]["ingress"]
        del normalized["items"][3]["spec"]["egress"]
        helm_profile.validate_manifest(
            normalized,
            expected_configuration=CONFIGURATION,
            expected_runtime_secret=RUNTIME_SECRET,
        )

        opened = valid_manifest()
        opened["items"][3]["spec"]["egress"] = [{}]
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "default_deny"):
            helm_profile.validate_manifest(
                opened,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )

        broad = valid_manifest()
        broad["items"][-1]["spec"]["egress"][0]["to"] = [
            {"ipBlock": {"cidr": "0.0.0.0/0"}}
        ]
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "egress_broad_cidr"):
            helm_profile.validate_manifest(
                broad,
                expected_configuration=CONFIGURATION,
                expected_runtime_secret=RUNTIME_SECRET,
            )


class HelmEvidenceTests(unittest.TestCase):
    def _evidence(self) -> dict[str, object]:
        return helm_profile.build_evidence(
            docker_engine="28.5.1",
            chart_package_sha256="a" * 64,
            gateway_replicas=2,
            distinct_gateway_nodes=2,
            successful_requests=3,
            policy_denials=1,
            provider_requests=3,
            usage_events=4,
        )

    def test_content_free_evidence_is_strict(self) -> None:
        evidence = self._evidence()
        helm_profile.validate_evidence(evidence)

        cni_claim = copy.deepcopy(evidence)
        cni_claim["cluster"]["cni"]["product_dependency"] = True
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "cluster_cni"):
            helm_profile.validate_evidence(cni_claim)

        credential = copy.deepcopy(evidence)
        credential["state"]["secret_value"] = "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA"
        with self.assertRaisesRegex(helm_profile.HelmProfileError, "state_keys"):
            helm_profile.validate_evidence(credential)

    def test_evidence_file_is_protected_and_never_overwritten(self) -> None:
        evidence = self._evidence()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            helm_profile.write_evidence(path, evidence)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            helm_profile.validate_evidence(json.loads(path.read_text(encoding="utf-8")))
            with self.assertRaisesRegex(helm_profile.HelmProfileError, "evidence_output_exists"):
                helm_profile.write_evidence(path, evidence)

    def test_secret_scanner_rejects_exact_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = root / "secrets"
            secrets.mkdir()
            secret = "synthetic-kubernetes-secret-value"
            (secrets / "credential").write_text(secret, encoding="utf-8")
            artifact = root / "rendered.json"
            artifact.write_text(json.dumps({"value": secret}), encoding="utf-8")
            with self.assertRaisesRegex(
                helm_profile.HelmProfileError, "artifact_contains_secret_value"
            ):
                helm_profile.assert_no_secrets(
                    [artifact], secret_values=helm_profile.read_secret_values(secrets)
                )


if __name__ == "__main__":
    unittest.main()
