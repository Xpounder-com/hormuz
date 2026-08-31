"""Strict internal observations and unchanged, dependency-closed public output."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from hormuz.outcome_wire import OutcomeKeys, decode_source_body, observation_from_mapping
from hormuz.portfolio_config import PortfolioConnectorBinding
from hormuz.portfolio_wire import PortfolioError, outcome_catalogue, route, validate


BINDING = PortfolioConnectorBinding("acme", "github-one", "github", "123", None, ("456", "789"))
DELIVERY = "12345678-1234-4234-8234-123456789abc"


def observation(**changes):
    return {
        "schema_id": "hormuz.source-outcome-observation", "schema_version": 1,
        "source_event_id": "11111111-1111-4111-8111-111111111111",
        "external_object_id": "101", "container_id": "456", "source_revision": "4",
        "ordering_domain": "source_revision_counter_v1", "revision_order": "4",
        "object_type": "issue", "event_type": "completed", "quality_state": "unknown",
        "duration_ms": None, "state": "observed", "supersedes_source_event_id": None,
        "reason_code": "observed", "event_at": "2026-08-30T12:00:00Z", **changes,
    }


class OutcomeContractTests(unittest.TestCase):
    def error(self, action, code="invalid_request"):
        with self.assertRaises(PortfolioError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_public_catalogue_is_exact_closed_approved_subset(self):
        root = Path(__file__).resolve().parents[1]
        planned = json.loads((root / "docs/portfolio-intelligence-wire-v1.json").read_text())
        installed = outcome_catalogue()
        self.assertEqual(set(installed["schema_ids"]), {
            "hormuz.work-outcome-event", "hormuz.work-outcome-page", "hormuz.connector-ingest-receipt",
        })
        for name, schema in installed["$defs"].items():
            self.assertEqual(schema, planned["$defs"][name])
        cases = json.loads((root / "tests/fixtures/portfolio_intelligence/wire-v1-examples.json").read_text())["cases"]
        for case in cases:
            if case["schema_id"] in installed["schema_ids"]:
                validate(case["value"], case["schema_id"])
        self.assertEqual(route("GET", "/v1/admin/portfolio/outcomes"), ("list_outcomes", None))
        self.error(lambda: route("POST", "/v1/admin/portfolio/outcomes"), "not_found")

    def test_observation_is_closed_versioned_and_content_free(self):
        value = observation_from_mapping(observation(), BINDING)
        self.assertEqual(asdict(value), observation())
        for field in ("title", "body", "comment", "prompt", "response", "path", "actor_name", "organization_id", "evidence_level", "work_scope"):
            self.error(lambda field=field: observation_from_mapping({**observation(), field: "SYNTHETIC_EXCLUDED"}, BINDING))
        for changes in ({"schema_version": True}, {"schema_version": 2}, {"event_type": "SYNTHETIC_EXCLUDED"},
                        {"external_object_id": "SYNTHETIC_EXCLUDED"}, {"duration_ms": 1.5},
                        {"source_event_id": "SYNTHETIC_EXCLUDED"}, {"source_revision": "SYNTHETIC_EXCLUDED"},
                        {"event_at": "2026-02-30T00:00:00Z"}, {"state": "tombstoned"},
                        {"supersedes_source_event_id": "11111111-1111-4111-8111-111111111111"}):
            self.error(lambda changes=changes: observation_from_mapping(observation(**changes), BINDING))
        self.error(lambda: observation_from_mapping(observation(container_id="654"), BINDING), "forbidden")

    def test_unknown_and_incomparable_source_order_are_explicit(self):
        unknown = observation(source_revision=None, ordering_domain=None, revision_order=None, event_at=None)
        self.assertEqual(asdict(observation_from_mapping(unknown, BINDING)), unknown)
        for changes in ({"source_revision": None}, {"ordering_domain": None}, {"revision_order": None},
                        {"revision_order": "-1"}, {"revision_order": "9223372036854775808"},
                        {"revision_order": "4.5"}, {"ordering_domain": "SYNTHETIC_EXCLUDED"}):
            self.error(lambda changes=changes: observation_from_mapping(observation(**changes), BINDING))

    def test_linear_projection_requires_native_uuid_scope_without_connector_claim(self):
        binding = PortfolioConnectorBinding("acme", "linear-one", "linear", None,
                                            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",))
        value = observation(external_object_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", container_id=binding.external_object_ids[0],
                            source_revision=None, ordering_domain=None, revision_order=None)
        self.assertEqual(asdict(observation_from_mapping(value, binding)), value)
        self.error(lambda: observation_from_mapping({**value, "container_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"}, binding), "forbidden")
        self.error(lambda: observation_from_mapping({**value, "external_object_id": "101"}, binding))

    def test_wrong_primitive_types_have_only_fixed_diagnostics(self):
        for name in observation():
            for invalid in ({}, []):
                with self.subTest(field=name, invalid=type(invalid).__name__):
                    self.error(lambda name=name, invalid=invalid: observation_from_mapping(observation(**{name: invalid}), BINDING))
        zero = observation(source_revision="0", revision_order="0")
        self.assertEqual(asdict(observation_from_mapping(zero, BINDING)), zero)

    def test_bounded_parser_counts_members_depth_and_rejects_ambiguous_json(self):
        self.assertEqual(decode_source_body(b'{"verified":true}'), {"verified": True})
        for raw in (b'{"id":1,"id":2}', b'{"x":NaN}', b'{"x":1e999}', b'"not-object"',
                    b'{"x":"\\ud800"}', b'\xff', b'{', b' ' * (1048576 + 1),
                    ("[" * 20 + "0" + "]" * 20).encode()):
            self.error(lambda raw=raw: decode_source_body(raw))
        self.error(lambda: decode_source_body(json.dumps({"x": list(range(4097))}).encode()))
        self.error(lambda: decode_source_body(json.dumps({str(i): 0 for i in range(4097)}).encode()))
        accepted = {"x": list(range(4095))}
        self.assertEqual(decode_source_body(json.dumps(accepted).encode()), accepted)

    def test_provenance_is_tenant_domain_and_version_bound(self):
        keys = OutcomeKeys("v2", {"v1": b"a" * 32, "v2": b"b" * 32})
        args = ("acme", "github-one", DELIVERY, b"exact verified bytes")
        first = keys.delivery_digest("v1", *args)
        self.assertEqual(first, keys.delivery_digest("v1", *args))
        self.assertNotEqual(first, keys.delivery_digest("v2", *args))
        self.assertNotEqual(first, keys.delivery_digest("v1", "beta", *args[1:]))
        self.assertNotEqual(first, keys.delivery_digest("v1", *args[:-1], b"exact verified bytes "))
        self.assertNotIn("aaaaaaaa", repr(keys))
        self.error(lambda: keys.delivery_digest("missing", *args), "unavailable")
        for current, material in (("v1", {"v1": b"short"}), ("v2", {"v1": b"a" * 32})):
            self.error(lambda current=current, material=material: OutcomeKeys(current, material))


if __name__ == "__main__":
    unittest.main()
