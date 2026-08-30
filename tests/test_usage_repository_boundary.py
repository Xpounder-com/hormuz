from __future__ import annotations

import inspect
import json
from pathlib import Path
import unittest

from hormuz._persistence import UsageRepository
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import UsageRepository as PublicUsageRepository, UsageStore


# This is the v1 ledger boundary, not a home for future portfolio operations.
V1_USAGE_OPERATIONS = frozenset({
    "verify_ready",
    "record",
    "record_secret_event",
    "reserve_budget",
    "begin_request_attempt",
    "finalize_request_attempt",
    "mark_request_attempt_outcome_unknown",
    "sweep_stale_request_attempts",
    "release_budget_reservation",
    "refresh_budget_reservation",
    "active_budget_reservations",
    "monthly_totals",
    "monthly_secret_totals",
    "summary_rows",
    "report_rows",
    "audit_events",
    "audit_chain_head",
    "audit_chain_anchor_status",
    "record_audit_chain_checkpoint",
    "begin_audit_chain_epoch",
    "verify_audit_chain",
})


def _public_operations(owner: type) -> set[str]:
    return {
        name for name, value in vars(owner).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }


def _signature_manifest(method) -> dict[str, object]:
    signature = inspect.signature(method)
    parameters = []
    for parameter in signature.parameters.values():
        row = [
            parameter.name, parameter.kind.name,
            None if parameter.annotation is inspect.Parameter.empty else parameter.annotation,
        ]
        if parameter.default is not inspect.Parameter.empty:
            row.append(parameter.default)
        parameters.append(row)
    return json.loads(json.dumps({"parameters": parameters, "return": signature.return_annotation}))


class UsageRepositoryBoundaryTests(unittest.TestCase):
    def test_concrete_adapters_keep_the_exact_v1_operation_boundary(self) -> None:
        frozen = json.loads((
            Path(__file__).parent / "fixtures" / "persistence" / "usage-repository-v1.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(frozen["baseline_commit"], "932024e5bb5d9250a20ef7c815bac2487746d086")
        self.assertEqual(set(frozen["operations"]), V1_USAGE_OPERATIONS)
        for adapter in (UsageStore, PostgresUsageStore):
            with self.subTest(adapter=adapter.__name__):
                self.assertEqual(_public_operations(adapter), V1_USAGE_OPERATIONS)
                for name in V1_USAGE_OPERATIONS:
                    self.assertEqual(
                        _signature_manifest(getattr(adapter, name)),
                        frozen["operations"][name],
                        name,
                    )

    def test_protocol_owns_every_operation_with_explicit_compatible_types(self) -> None:
        self.assertIs(PublicUsageRepository, UsageRepository)
        self.assertEqual(_public_operations(UsageRepository), V1_USAGE_OPERATIONS)
        for name in V1_USAGE_OPERATIONS:
            with self.subTest(operation=name):
                contract = inspect.signature(getattr(UsageRepository, name))
                self.assertEqual(contract, inspect.signature(getattr(UsageStore, name)))
                for parameter in contract.parameters.values():
                    self.assertNotIn(parameter.kind, {
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    })
                    if parameter.name != "self":
                        self.assertIsNot(parameter.annotation, inspect.Parameter.empty)
                self.assertIsNot(contract.return_annotation, inspect.Signature.empty)


if __name__ == "__main__":
    unittest.main()
