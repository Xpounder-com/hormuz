"""Strict, content-free admission authority before any storage or egress."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from hormuz.config import ConfigError, GatewayConfig
from hormuz.attribution_admission import AdmissionError, select_admission
from hormuz.attribution_config import build_attribution_config

if __package__:
    from ._portfolio_fixture import ADMIN, OTHER, registry_config
else:
    from _portfolio_fixture import ADMIN, OTHER, registry_config


def control_document():
    reference = {"work_scope_id": "registered-use-case", "version": 1}
    return {"schema_id": "hormuz.attribution-control", "schema_version": 1, "bindings": [
        {"organization_id": "acme", "actor_id": "alice", "client": "codex",
         "allowed_work_scopes": [reference], "default_work_scopes": [], "require_scope": False},
    ]}


class AttributionAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.config = registry_config(Path("/unused/synthetic-attribution"))
        self.identity = self.config.identities_by_token[ADMIN]
        self.identities = tuple(self.config.identities_by_token.values())
        self.document = control_document()

    def configure(self):
        return replace(self.config, attribution_control=build_attribution_config(self.document, self.identities))

    def select(self, headers=(), *, config=None, identity=None, client="codex", accounted=True):
        return select_admission(config or self.configure(), identity or self.identity, client,
                                headers, account_usage=accounted)

    def rejects(self, reason, operation, status=400):
        with self.assertRaises(AdmissionError) as caught:
            operation()
        self.assertEqual((caught.exception.reason, caught.exception.status), (reason, status))
        self.assertNotIn("registered-use-case", str(caught.exception))
        self.assertNotIn("SYNTHETIC_EXCLUDED", caught.exception.result_header)

    def test_config_default_off_and_existing_identity_client_authority(self):
        self.assertIsNone(build_attribution_config(None, self.identities))
        self.assertIsNone(self.select(config=self.config))
        self.assertIsNone(self.select(identity=self.config.identities_by_token[OTHER]))
        self.assertIsNone(self.select(client="claude-code"))
        selected = self.select()
        self.assertIsNone(selected.work_scope)
        self.assertEqual((selected.confidence, selected.reason), ("unattributed", "missing_evidence"))

    def test_real_configuration_loader_retains_only_explicit_attribution_control(self):
        if __package__:
            from .test_portfolio_api_cli import configuration_document
        else:
            from test_portfolio_api_cli import configuration_document
        document, environment = configuration_document(self.config)
        document["attribution_control"] = self.document
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(document))
            loaded = GatewayConfig.load(path, environ=environment)
            self.assertEqual(loaded.attribution_control, self.configure().attribution_control)

    def test_installed_attribution_definitions_are_exact_approved_subset(self):
        from hormuz.portfolio_wire import attribution_catalogue, validate
        root = Path(__file__).resolve().parents[1]
        approved = json.loads((root / "docs/portfolio-intelligence-wire-v1.json").read_text())
        installed = attribution_catalogue()
        self.assertEqual(set(installed["schema_ids"]), {"hormuz.governed-run-attribution-request", "hormuz.governed-run-attribution-event", "hormuz.governed-run-attribution-page"})
        for name, definition in installed["$defs"].items():
            self.assertEqual(definition, approved["$defs"][name])
        cases = json.loads((root / "tests/fixtures/portfolio_intelligence/wire-v1-examples.json").read_text())["cases"]
        for case in cases:
            if case["schema_id"] in installed["schema_ids"]:
                validate(case["value"], case["schema_id"])

    def test_config_strict_bounded_and_non_echoing(self):
        for mutate in (
            lambda x: x.update(schema_version=True),
            lambda x: x.update(prompt="SYNTHETIC_EXCLUDED"),
            lambda x: x.update(bindings=x["bindings"] * 1001),
            lambda x: x["bindings"].append(copy.deepcopy(x["bindings"][0])),
            lambda x: x["bindings"][0].update(actor_id="unknown"),
            lambda x: x["bindings"][0].update(organization_id="beta"),
            lambda x: x["bindings"][0].update(client="user-agent-label"),
            lambda x: x["bindings"][0].update(require_scope=1),
            lambda x: x["bindings"][0].update(allowed_work_scopes=[{"work_scope_id": "x", "version": True}]),
            lambda x: x["bindings"][0].update(default_work_scopes=[{"work_scope_id": "unauthorized", "version": 1}]),
            lambda x: x["bindings"][0].update(allowed_work_scopes=x["bindings"][0]["allowed_work_scopes"] * 129),
        ):
            value = copy.deepcopy(self.document)
            mutate(value)
            with self.assertRaisesRegex(ConfigError, "^attribution_configuration_invalid$"):
                build_attribution_config(value, self.identities)
        restricted = tuple(replace(identity, allowed_clients=("claude-code",)) for identity in self.identities)
        with self.assertRaisesRegex(ConfigError, "^attribution_configuration_invalid$"):
            build_attribution_config(self.document, restricted)

    def test_explicit_precedence_defaults_missing_and_ambiguity(self):
        binding = self.document["bindings"][0]
        first = binding["allowed_work_scopes"][0]
        second = {"work_scope_id": "second-registered", "version": 2}
        binding["allowed_work_scopes"].append(second)
        binding["default_work_scopes"] = [second]
        selected = self.select()
        self.assertEqual((selected.work_scope.work_scope_id, selected.confidence), ("second-registered", "server_side_default"))
        selected = self.select(["v1;work_scope_id=registered-use-case;version=1"])
        self.assertEqual((selected.work_scope.work_scope_id, selected.confidence), ("registered-use-case", "explicit_authorized"))
        binding["default_work_scopes"].append(first)
        selected = self.select()
        self.assertEqual((selected.work_scope, selected.confidence, selected.reason), (None, "ambiguous", "ambiguous"))
        binding["require_scope"] = True
        self.rejects("ambiguous", self.select, 403)
        binding["default_work_scopes"] = []
        self.rejects("missing_evidence", self.select, 403)

    def test_explicit_duplicate_invalid_unsupported_and_foreign_authority(self):
        valid = "v1;work_scope_id=registered-use-case;version=1"
        self.rejects("ambiguous", lambda: self.select([valid, valid]))
        for value in ("", valid + ";actor_id=alice", valid + "\n", valid.replace("=1", "=01"),
                      valid.replace("=1", "=2147483648"), valid.replace("=1", "=0"),
                      "v1;work_scope_id=SYNTHETIC_EXCLUDED/filename;version=1", "é" * 20, "x" * 193):
            self.rejects("invalid_reference", lambda value=value: self.select([value]))
        self.rejects("unsupported", lambda: self.select([valid.replace("v1;", "v2;")]))
        self.rejects("unsupported", lambda: self.select([valid], config=self.config), 403)
        self.rejects("unauthorized_scope", lambda: self.select([valid], identity=self.config.identities_by_token[OTHER]), 403)
        self.rejects("unauthorized_scope", lambda: self.select([valid.replace("=1", "=2")]), 403)
        self.rejects("unsupported", lambda: self.select([valid], accounted=False))
        self.assertIsNone(self.select(accounted=False))


if __name__ == "__main__":
    unittest.main()
