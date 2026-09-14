"""Real OIDC/session/HTTP transport with an explicitly simulated policy controller.

PostgreSQL authority and atomicity are covered separately in the PG suite.
"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from datetime import timedelta

from hormuz.policy_document import PolicyDocument
from hormuz.policy_impact import ImpactStore, ImpactRecorder, impact_path, utcnow, routing_fingerprint
from hormuz.policy_impact_control import PolicyImpactControl
from hormuz.policy_repository import PolicyControlError
from tests._console_fixtures import ConsoleHTTPTestCase
from tests.test_policy_impact import observation


class SimulatedPolicyController:
    def __init__(self, document):
        self.document, self.generation = document, 1
        self.documents = {document.version_id: document}
        self.allowed = True

    def browser_baseline(self, caller):
        if not self.allowed or caller.subject != "admin-subject" or caller.organization_id != "customer-a":
            raise PolicyControlError("policy_administrator_required")
        return SimpleNamespace(document=self.document), self.generation

    def browser_history(self, caller):
        self.browser_baseline(caller)
        return SimpleNamespace(events=(), has_more=False)

    def browser_apply(self, caller, document, *, baseline_version, generation):
        self.browser_baseline(caller)
        if self.document.version_id != baseline_version or self.generation != generation:
            raise PolicyControlError("policy_active_version_mismatch")
        self.document, self.generation = document, generation + 1
        self.documents[document.version_id] = document

    def browser_rollback(self, caller, *, target_version, active_version, generation):
        self.browser_apply(caller, self.documents[target_version], baseline_version=active_version, generation=generation)


class PolicyImpactHTTPTests(ConsoleHTTPTestCase):
    def setUp(self):
        super().setUp()
        # This test's simulated policy tenant is the managed console tenant.
        config = replace(self.config, identities_by_subject={key: replace(identity, organization_id="customer-a")
                         for key, identity in self.config.identities_by_subject.items()})
        baseline = PolicyDocument.from_mapping({"schema_id": "hormuz.policy-document", "schema_version": 1,
            "organization_id": "customer-a", "policies": {"organization": {"max_output_tokens": 32}, "teams": {}, "actors": {}},
            "egress_controls": {"openai": {"allow_background": False, "allow_response_storage": False}, "secrets": {"mode": "off"}}}, config=config)
        self.controller = SimulatedPolicyController(baseline)
        self.impact_store = ImpactStore(impact_path(self.config.session_broker.database_path))
        self.impact = PolicyImpactControl(config=config, sessions=self.sessions, controller=self.controller, store=self.impact_store)
        self.gateway.policy_console = self.impact  # Explicit test-only injection; real gateway never exposes writes.
        self.gateway.impact_recorder = ImpactRecorder(self.impact_store)
        self.gateway.policy_engine.policy_runtime = SimpleNamespace(snapshot_for=lambda identity: self.controller.document.snapshot_for(identity))
        self.login_console()

    def capture(self):
        self.impact_store.record(observation(organization_id="customer-a", team_id="customer-a-eng", model_alias="safe-openai",
                    policy_version=self.controller.document.version_id, routing_fingerprint=routing_fingerprint(self.impact.config),
                    effective_limit=32, requested_limit=32, status="succeeded", output_tokens=24))

    def post_policy(self, action, values, **headers):
        return self.request("POST", "/v1/admin/policy/" + action, {"csrf_token": self.csrf, **values},
                            {"Origin": self.gateway_url, "Cookie": self.cookie, **headers})

    def preview(self):
        status, _, result = self.post_policy("preview", {"team_id": "customer-a-eng", "model_alias": "safe-openai", "proposed_limit": 16})
        self.assertEqual(status, 200, result)
        return result

    def test_preview_apply_actual_request_receipt_and_rollback(self):
        self.capture()
        p = self.preview()
        self.assertEqual(p["comparison"]["completions_above_limit"], 1)
        status, _, result = self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["candidate_active"])
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[2]["active_generation"], 2)
        status, _, response = self.request("POST", "/v1/responses", {"model": "safe-openai", "input": "synthetic-fixture", "max_output_tokens": 30},
                                           {"Authorization": "Bearer " + self.native.access_token, "X-Hormuz-Client": "codex"})
        self.assertEqual(status, 200, response)
        self.gateway.impact_recorder._queue.join()
        result = self.impact.results(self.cookie.split("=", 1)[1], p["preview_id"])
        self.assertEqual(result["captured_requests"], 1)
        self.assertEqual(result["receipts"][0]["effective_limit"], 16)
        self.assertEqual(result["receipts"][0]["policy_version"], p["candidate_version"])
        status, _, result = self.post_policy("rollback", {"preview_id": p["preview_id"], "acknowledged": True})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["active_version"], p["baseline_version"])
        self.assertFalse(result["rollback_available"])
        self.assertEqual(self.post_policy("rollback", {"preview_id": p["preview_id"], "acknowledged": True})[2]["active_generation"], 3)
        status, _, page = self.request("GET", "/console/policy/activity", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, page)
        self.assertIn(p["preview_id"], page)

    def test_no_data_stale_preview_and_aba_fail_closed(self):
        p = self.preview()
        self.assertFalse(p["can_apply"])
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 409)
        self.capture()
        p = self.preview()
        self.controller.generation += 2  # Same document after an intervening activation and rollback.
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 409)

    def test_root_grant_csrf_and_closed_fields_are_required(self):
        self.capture()
        p = self.preview()
        values = {"preview_id": p["preview_id"], "acknowledged": True}
        for extra, expected in (({"csrf_token": "wrong"}, 403), ({"organization_id": "customer-b"}, 400),
                                 ({"acknowledged": "true"}, 400)):
            self.assertEqual(self.post_policy("apply", values | extra)[0], expected)
        self.assertEqual(self.post_policy("apply", values, Origin="https://foreign.example")[0], 403)
        self.controller.allowed = False
        self.assertEqual(self.post_policy("apply", values)[0], 403)
        self.assertEqual(self.controller.generation, 1)

    def test_expiry_signature_membership_and_route_drift(self):
        self.capture()
        p = self.preview()
        credential = self.cookie.split("=", 1)[1]
        with patch("hormuz.policy_impact_control.utcnow", return_value=utcnow() + timedelta(minutes=11)):
            self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 409)
        restarted = PolicyImpactControl(config=self.impact.config, sessions=self.sessions, controller=self.controller, store=self.impact_store)
        self.assertEqual(restarted.review(credential, p["preview_id"])["preview_id"], p["preview_id"])
        routes = dict(self.impact.config.model_routes)
        routes["safe-openai"] = replace(routes["safe-openai"], upstream_model="changed")
        self.impact.config = replace(self.impact.config, model_routes=routes)
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 409)

    def test_inference_listener_does_not_expose_policy_writes(self):
        del self.gateway.policy_console
        self.assertEqual(self.post_policy("preview", {"team_id": "customer-a-eng", "model_alias": "safe-openai", "proposed_limit": 16})[0], 404)

    def test_tampered_proposal_and_revoked_browser_session_cannot_apply(self):
        import json
        self.capture()
        p = self.preview()
        with self.impact_store.connection() as db:
            value = json.loads(db.execute("SELECT value FROM previews WHERE id=?", (p["preview_id"],)).fetchone()[0])
            value["preview"]["proposed_limit"] = 1
            db.execute("UPDATE previews SET value=? WHERE id=?", (json.dumps(value), p["preview_id"]))
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 409)
        p = self.preview()
        self.sessions.logout(self.cookie.split("=", 1)[1])
        self.assertEqual(self.post_policy("apply", {"preview_id": p["preview_id"], "acknowledged": True})[0], 401)
        self.assertEqual(self.controller.generation, 1)
