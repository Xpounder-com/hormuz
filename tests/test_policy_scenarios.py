from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest import mock

import hormuz.policy_scenarios as policy_scenarios
from hormuz.policy_scenarios import (
    PolicyScenarioError,
    PolicyScenarioSuite,
    add_policy_scenario_to_suite,
    create_policy_scenario,
    create_policy_scenario_suite,
    load_policy_scenario_suite,
    replace_policy_scenario_suite,
    write_policy_scenario_suite,
)


def _scenario(scenario_id: str, *, actor_id: str = "alice") -> dict[str, object]:
    return {
        "id": scenario_id,
        "actor_id": actor_id,
        "client": "codex",
        "protocol": "openai",
        "requested_model": "gpt-5.4-mini",
        "requested_output_tokens": 1_000,
    }


def _suite(*scenarios: dict[str, object]) -> dict[str, object]:
    return {
        "schema_id": "hormuz.policy-scenario-suite",
        "schema_version": 1,
        "organization_id": "xpounder",
        "scenarios": list(scenarios),
    }


class PolicyScenarioSuiteTests(unittest.TestCase):
    def test_suite_canonicalizes_order_and_uses_a_stable_content_identity(self) -> None:
        first = PolicyScenarioSuite.from_mapping(_suite(_scenario("z-last"), _scenario("a-first")))
        second = PolicyScenarioSuite.from_mapping(_suite(_scenario("a-first"), _scenario("z-last")))

        self.assertEqual([item.scenario_id for item in first.scenarios], ["a-first", "z-last"])
        self.assertEqual(first.to_mapping(), second.to_mapping())
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.suite_id, f"sha256:{first.content_sha256}")

    def test_strict_contract_rejects_duplicates_unknown_fields_and_unbounded_suites(self) -> None:
        with self.assertRaises(PolicyScenarioError) as duplicate:
            PolicyScenarioSuite.from_mapping(_suite(_scenario("same"), _scenario("same")))
        self.assertEqual(duplicate.exception.code, "policy_scenario_suite_invalid")

        unknown = _scenario("unknown")
        unknown["prompt"] = "content must never enter this contract"
        with self.assertRaises(PolicyScenarioError) as unsupported:
            PolicyScenarioSuite.from_mapping(_suite(unknown))
        self.assertEqual(unsupported.exception.code, "policy_scenario_suite_invalid")
        self.assertNotIn("content must never enter this contract", unsupported.exception.reason)

        with self.assertRaises(PolicyScenarioError) as unbounded:
            PolicyScenarioSuite.from_mapping(
                _suite(*(_scenario(f"scenario-{index:03d}") for index in range(101)))
            )
        self.assertEqual(unbounded.exception.code, "policy_scenario_suite_invalid")

    def test_explicit_add_is_canonical_and_refuses_duplicate_ids(self) -> None:
        suite = create_policy_scenario_suite(
            organization_id="xpounder",
            scenario_id="baseline",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=None,
        )
        added = create_policy_scenario(
            organization_id="xpounder",
            scenario_id="candidate",
            actor_id="bob",
            client="claude-code",
            protocol="anthropic",
            requested_model="claude-sonnet",
            requested_output_tokens=2_000,
        )
        updated = suite.with_scenario(added)

        self.assertEqual([item.scenario_id for item in updated.scenarios], ["baseline", "candidate"])
        with self.assertRaises(PolicyScenarioError) as duplicate:
            updated.with_scenario(added)
        self.assertEqual(duplicate.exception.code, "policy_scenario_id_exists")

    def test_file_operations_are_owner_only_atomic_and_refuse_links(self) -> None:
        suite = create_policy_scenario_suite(
            organization_id="xpounder",
            scenario_id="baseline",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=1_000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "scenarios.json"
            write_policy_scenario_suite(path, suite, force=False)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(load_policy_scenario_suite(path), suite)
            original = path.read_bytes()
            with self.assertRaises(PolicyScenarioError) as exists:
                write_policy_scenario_suite(path, suite, force=False)
            self.assertEqual(exists.exception.code, "policy_scenario_output_exists")
            self.assertEqual(path.read_bytes(), original)

            updated = suite.with_scenario(
                create_policy_scenario(
                    organization_id="xpounder",
                    scenario_id="second",
                    actor_id="alice",
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-5.4-mini",
                    requested_output_tokens=2_000,
                )
            )
            replace_policy_scenario_suite(
                path,
                updated,
                expected_content_sha256=suite.content_sha256,
            )
            self.assertEqual(load_policy_scenario_suite(path), updated)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            link = root / "suite-link.json"
            os.symlink(path, link)
            with self.assertRaises(PolicyScenarioError) as input_link:
                load_policy_scenario_suite(link)
            self.assertEqual(input_link.exception.code, "policy_scenario_suite_symlink_refused")
            with self.assertRaises(PolicyScenarioError) as output_link:
                write_policy_scenario_suite(link, suite, force=True)
            self.assertEqual(output_link.exception.code, "policy_scenario_output_symlink_refused")

    @unittest.skipIf(policy_scenarios._fcntl is None, "POSIX file locks are unavailable")
    def test_concurrent_adds_are_serialized_without_lost_updates(self) -> None:
        suite = create_policy_scenario_suite(
            organization_id="xpounder",
            scenario_id="baseline",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=1_000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenarios.json"
            write_policy_scenario_suite(path, suite, force=False)
            workers = 6
            start = threading.Barrier(workers)
            real_publish = policy_scenarios._atomic_publish_json

            def slow_publish(*args, **kwargs):
                time.sleep(0.02)
                return real_publish(*args, **kwargs)

            def add(index: int) -> None:
                start.wait()
                add_policy_scenario_to_suite(
                    path,
                    scenario_id=f"concurrent-{index}",
                    actor_id="alice",
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-5.4-mini",
                    requested_output_tokens=1_000 + index,
                )

            with (
                mock.patch(
                    "hormuz.policy_scenarios._atomic_publish_json",
                    side_effect=slow_publish,
                ),
                ThreadPoolExecutor(max_workers=workers) as executor,
            ):
                list(executor.map(add, range(workers)))

            persisted = load_policy_scenario_suite(path)
            self.assertEqual(len(persisted.scenarios), workers + 1)
            self.assertEqual(
                {item.scenario_id for item in persisted.scenarios},
                {"baseline", *(f"concurrent-{index}" for index in range(workers))},
            )

    def test_add_fails_closed_when_platform_locking_is_unavailable(self) -> None:
        suite = create_policy_scenario_suite(
            organization_id="xpounder",
            scenario_id="baseline",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=1_000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenarios.json"
            write_policy_scenario_suite(path, suite, force=False)
            original = path.read_bytes()

            with (
                mock.patch.object(policy_scenarios, "_fcntl", None),
                self.assertRaises(PolicyScenarioError) as unavailable,
            ):
                add_policy_scenario_to_suite(
                    path,
                    scenario_id="unsafe-add",
                    actor_id="alice",
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-5.4-mini",
                    requested_output_tokens=2_000,
                )

            self.assertEqual(unavailable.exception.code, "policy_scenario_lock_unavailable")
            self.assertEqual(path.read_bytes(), original)

    def test_replace_rejects_changed_source_and_writer_enforces_loader_limit(self) -> None:
        suite = create_policy_scenario_suite(
            organization_id="xpounder",
            scenario_id="baseline",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="gpt-5.4-mini",
            requested_output_tokens=1_000,
        )
        second = suite.with_scenario(
            create_policy_scenario(
                organization_id="xpounder",
                scenario_id="second",
                actor_id="alice",
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                requested_output_tokens=2_000,
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "scenarios.json"
            write_policy_scenario_suite(path, suite, force=False)
            write_policy_scenario_suite(path, second, force=True)

            with self.assertRaises(PolicyScenarioError) as conflict:
                replace_policy_scenario_suite(
                    path,
                    suite,
                    expected_content_sha256=suite.content_sha256,
                )
            self.assertEqual(conflict.exception.code, "policy_scenario_concurrent_update")
            self.assertEqual(load_policy_scenario_suite(path), second)

            oversized = root / "oversized.json"
            with (
                mock.patch.object(policy_scenarios, "_MAX_POLICY_SCENARIO_BYTES", 64),
                self.assertRaises(PolicyScenarioError) as too_large,
            ):
                write_policy_scenario_suite(oversized, suite, force=False)
            self.assertEqual(too_large.exception.code, "policy_scenario_suite_too_large")
            self.assertFalse(oversized.exists())

    def test_loader_rejects_duplicate_json_keys_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text(
                '{"schema_id":"hormuz.policy-scenario-suite",'
                '"schema_id":"do-not-echo",'
                '"schema_version":1,"organization_id":"xpounder","scenarios":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(PolicyScenarioError) as raised:
                load_policy_scenario_suite(path)
        self.assertEqual(raised.exception.code, "policy_scenario_suite_invalid")
        self.assertNotIn("do-not-echo", raised.exception.reason)


if __name__ == "__main__":
    unittest.main()
