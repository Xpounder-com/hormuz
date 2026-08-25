from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

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


class HelmChartContractTests(unittest.TestCase):
    def test_repository_chart_satisfies_the_static_contract(self) -> None:
        digest = helm_profile.validate_chart(CHART)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_account_free_reference_pins_kind_kubernetes_and_cilium(self) -> None:
        fixture = ROOT / "deploy" / "kubernetes" / "conformance"
        kind = (fixture / "kind.yaml").read_text(encoding="utf-8")
        cilium = (fixture / "cilium-values.yaml").read_text(encoding="utf-8")
        postgres = (fixture / "postgres.yaml").read_text(encoding="utf-8")
        runner = (ROOT / "tools" / "verify_helm_profile.sh").read_text(encoding="utf-8")
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
        self.assertNotIn('openssl rand -hex 32 >', runner)
        self.assertNotIn("apiVersion: cilium.io/", (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Kubernetes + Helm multi-replica reference", workflow)
        self.assertIn("HORMUZ_KUBERNETES_PROOF_ACK", workflow)
        self.assertNotIn("aws ", runner)
        self.assertNotIn("az ", runner)
        self.assertNotIn("gcloud ", runner)

    def test_source_distribution_contract_includes_the_chart_and_live_proof(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("recursive-include deploy/helm", manifest)
        self.assertIn("recursive-include deploy/kubernetes", manifest)
        self.assertIn("tools/verify_helm_profile.py", manifest)
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
