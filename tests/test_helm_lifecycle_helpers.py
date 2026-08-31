"""Run the real shell helpers with a synthetic kubectl, never a cluster."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/verify_helm_profile.sh"


class HelmLifecycleHelperTests(unittest.TestCase):
    def helper(self, name, following):
        source = SCRIPT.read_text(encoding="utf-8")
        return source[source.index(name + "() {"):source.index("\n" + following + "() {")]

    def run_shell(self, helper, invocation, stub):
        with tempfile.TemporaryDirectory() as temporary:
            script = (
                "set -euo pipefail\n"
                + "ARTIFACT_ROOT=" + shlex.quote(temporary) + "\n"
                + "python3() { " + shlex.quote(sys.executable) + ' "$@"; }\n'
                + "fail() { printf '%s\\n' \"$1\" >&2; exit 1; }\n"
                + "sleep() { :; }\nmonotonic_ms() { printf '2000\\n'; }\n"
                + "pod_calls=0\n" + stub + "\n" + helper + "\n" + invocation + "\n"
            )
            return subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=15)

    def test_pod_disappearance_is_one_atomic_read_not_check_then_fetch(self):
        helper = self.helper("wait_for_service_exclusion", "request_attempt_uncertainty")
        result = self.run_shell(helper, "wait_for_service_exclusion gone-pod 10.0.0.9 1000", r'''
kubectl() {
  if [[ "$*" == *"get pod "* ]]; then
    if [[ "$*" == *"--ignore-not-found"* ]]; then return 0; fi
    pod_calls=$((pod_calls + 1))
    if [[ "${pod_calls}" == 1 ]]; then return 0; fi
    printf 'Error from server (NotFound)\n' >&2
    return 1
  fi
  printf '{"items":[]}\n'
}
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1000")

    def test_api_failure_must_not_be_treated_as_deleted_pod(self):
        helper = self.helper("wait_for_service_exclusion", "request_attempt_uncertainty")
        result = self.run_shell(helper, "wait_for_service_exclusion gone-pod 10.0.0.9 1000", r'''
kubectl() {
  if [[ "$*" == *"get pod "* ]]; then return 1; fi
  printf '{"items":[]}\n'
}
''')
        self.assertNotEqual(result.returncode, 0)

    def test_deleted_pod_with_ready_service_endpoint_is_not_accepted(self):
        helper = self.helper("wait_for_service_exclusion", "request_attempt_uncertainty")
        result = self.run_shell(helper, "wait_for_service_exclusion gone-pod 10.0.0.9 1000", r'''
kubectl() {
  if [[ "$*" == *"get pod "* ]]; then return 0; fi
  printf '{"items":[{"endpoints":[{"conditions":{"ready":true},"addresses":["10.0.0.9"]}]}]}\n'
}
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("service-addressable", result.stderr)

    def test_ready_or_ambiguous_pod_readiness_is_not_exclusion_proof(self):
        helper = self.helper("wait_for_service_exclusion", "request_attempt_uncertainty")
        for readiness in ("True", "True False"):
            stub = "kubectl() { if [[ \"$*\" == *\"get pod \"* ]]; then printf '%s\\n' " + shlex.quote(readiness) + "; else printf '{\"items\":[]}\\n'; fi; }"
            with self.subTest(readiness=readiness):
                result = self.run_shell(helper, "wait_for_service_exclusion selected-pod 10.0.0.9 1000", stub)
                self.assertNotEqual(result.returncode, 0)

    def test_replacement_target_requires_two_ready_non_terminating_replicas(self):
        helper = self.helper("select_gateway_replacement_target", "wait_for_gateway_replacement")
        result = self.run_shell(helper, "select_gateway_replacement_target", "kubectl() { printf '{\"items\":[]}\\n'; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gateway_ready_replacement_target_invalid", result.stderr)

    def test_replacement_target_skips_terminating_pod_and_pins_uid_in_same_read(self):
        def pod(name, uid, *, deleting=False):
            return {"metadata": {"name": name, "uid": uid, **({"deletionTimestamp": "2026-08-31T00:00:00Z"} if deleting else {})},
                    "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}

        value = {"items": [
            pod("a-terminating", "00000000-0000-0000-0000-000000000001", deleting=True),
            pod("b-ready", "00000000-0000-0000-0000-000000000002"),
            pod("c-ready", "00000000-0000-0000-0000-000000000003"),
        ]}
        helper = self.helper("select_gateway_replacement_target", "wait_for_gateway_replacement")
        stub = "kubectl() { printf '%s\\n' " + shlex.quote(json.dumps(value)) + "; }"
        result = self.run_shell(helper, "select_gateway_replacement_target", stub)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "b-ready|00000000-0000-0000-0000-000000000002")


if __name__ == "__main__":
    unittest.main()
