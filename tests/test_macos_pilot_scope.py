from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from tools import macos_pilot_scope as scope


class MacPilotScopeTests(unittest.TestCase):
    def test_request_defaults_to_full_and_accepts_only_exact_names(self) -> None:
        self.assertEqual(scope.resolve_requested_scope().name, "full_dual_provider")
        self.assertEqual(
            scope.resolve_requested_scope("codex_openai").name, "codex_openai"
        )
        for value in (
            None,
            True,
            False,
            0,
            2,
            [],
            {},
            "",
            "CODEX_OPENAI",
            "codex-openai",
            "external_pilot_openai",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    scope.MacPilotScopeError, "qualification_scope_invalid"
                ):
                    scope.resolve_requested_scope(value)

    def test_evidence_version_and_scope_matrix_is_strict(self) -> None:
        self.assertEqual(
            scope.resolve_evidence_scope(
                1, qualification_scope_present=False
            ).name,
            "full_dual_provider",
        )
        self.assertEqual(
            scope.resolve_evidence_scope(
                2,
                qualification_scope_present=True,
                qualification_scope="codex_openai",
            ).name,
            "codex_openai",
        )
        invalid = (
            (1, True, None),
            (1, True, "full_dual_provider"),
            (1, True, "codex_openai"),
            (2, False, None),
            (2, True, None),
            (2, True, "full_dual_provider"),
            (2, True, "unknown"),
            (True, False, None),
            (False, False, None),
            (0, False, None),
            (3, False, None),
            ("2", True, "codex_openai"),
        )
        for version, present, selected in invalid:
            with self.subTest(
                version=version, present=present, selected=selected
            ):
                with self.assertRaises(scope.MacPilotScopeError):
                    scope.resolve_evidence_scope(
                        version,
                        qualification_scope_present=present,
                        qualification_scope=selected,
                    )

    def test_unhashable_scope_values_fail_as_contract_errors(self) -> None:
        for value in ([], {}):
            with self.subTest(value=value):
                with self.assertRaises(scope.MacPilotScopeError):
                    scope.resolve_evidence_scope(
                        2,
                        qualification_scope_present=True,
                        qualification_scope=value,
                    )

    def test_requested_and_evidence_scopes_must_match(self) -> None:
        full = scope.resolve_requested_scope()
        narrow = scope.resolve_requested_scope("codex_openai")
        self.assertIs(scope.require_matching_scope(full, full), full)
        self.assertIs(scope.require_matching_scope(narrow, narrow), narrow)
        with self.assertRaisesRegex(
            scope.MacPilotScopeError, "qualification_scope_mismatch"
        ):
            scope.require_matching_scope(full, narrow)
        with self.assertRaisesRegex(
            scope.MacPilotScopeError, "qualification_scope_mismatch"
        ):
            scope.require_matching_scope(narrow, full)

    def test_contracts_and_nested_values_are_immutable(self) -> None:
        full = scope.resolve_requested_scope()
        with self.assertRaises(FrozenInstanceError):
            full.name = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            scope.SCOPE_CONTRACTS["changed"] = full  # type: ignore[index]
        self.assertIsInstance(full.provider_protocols, tuple)
        self.assertEqual(
            scope.resolve_requested_scope().provider_protocols,
            ("anthropic", "openai"),
        )


if __name__ == "__main__":
    unittest.main()
