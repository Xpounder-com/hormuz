from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.classify_ci_scope import (
    NATIVE_COMPANION_DOCS,
    NATIVE_DIRECTORY_PREFIXES,
    SAFE_DIRECTORY_PREFIXES,
    SAFE_EXACT_PATHS,
    classify_changed_paths,
    determine_scope,
    main as classify_main,
)
from tools.verify_ci_required import (
    ALWAYS_REQUIRED_JOB_IDS,
    CIRequiredError,
    PATH_SCOPED_JOB_IDS,
    validate_required_results,
)


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _results(
    *, always: str = "success", scoped: str = "success", native: str = "success"
) -> list[str]:
    return [
        *(f"{job_id}={always}" for job_id in ALWAYS_REQUIRED_JOB_IDS),
        *(f"{job_id}={scoped}" for job_id in PATH_SCOPED_JOB_IDS),
        f"native-contracts={native}",
    ]


class CIScopeTests(unittest.TestCase):
    def test_allowlist_is_small_and_explicit(self) -> None:
        self.assertEqual(SAFE_DIRECTORY_PREFIXES, ("website/",))
        self.assertEqual(
            SAFE_EXACT_PATHS,
            {
                "marketing/COMMERCIAL_SETUP.md",
                "marketing/MEASUREMENT.md",
            },
        )
        self.assertEqual(NATIVE_DIRECTORY_PREFIXES, ("clients/rust/",))
        self.assertEqual(NATIVE_COMPANION_DOCS, {"docs/NATIVE_CLIENT_ROADMAP.md"})

    def test_native_source_and_accompanying_roadmap_use_reduced_ci(self) -> None:
        for paths in [
            ["clients/rust/session/src/scheduler.rs"],
            ["clients/rust/Cargo.lock", "clients/rust/rust-toolchain.toml"],
            [
                "clients/rust/session/src/scheduler.rs",
                "clients/rust/session/README.md",
                "docs/NATIVE_CLIENT_ROADMAP.md",
            ],
        ]:
            with self.subTest(paths=paths):
                decision = classify_changed_paths(paths)
                self.assertFalse(decision.run_full)
                self.assertEqual(decision.classification, "native_only")
                self.assertEqual(decision.changed_path_count, len(paths))

    def test_native_reduced_scope_excludes_shared_and_unknown_paths(self) -> None:
        for path in [
            "hormuz/contracts.py",
            "hormuz/session_client.py",
            "hormuz/_contract_schemas/client.json",
            "clients/contracts/README.md",
            "tests/fixtures/native_client/v1/snapshots.json",
            "tests/fixtures/contracts/valid-v1.json",
            "tests/test_native_client_contracts.py",
            "clients/macos/Sources/HormuzClientCore/ClientError.swift",
            "clients/windows/App.xaml",
            "tools/classify_ci_scope.py",
            "tools/verify_ci_required.py",
            ".github/workflows/ci.yml",
            ".github/workflows/native-client-contracts.yml",
            "website/app/page.tsx",
            "clients/rust-extra/README.md",
            "docs/VERIFICATION.md",
        ]:
            with self.subTest(path=path):
                decision = classify_changed_paths(["clients/rust/core/src/lib.rs", path])
                self.assertTrue(decision.run_full)
                self.assertEqual(decision.classification, "full_changed_paths")

    def test_native_roadmap_alone_is_not_a_native_change(self) -> None:
        self.assertTrue(classify_changed_paths(["docs/NATIVE_CLIENT_ROADMAP.md"]).run_full)

    def test_malformed_native_paths_cannot_enable_reduced_scope(self) -> None:
        for path in [
            "/clients/rust/core/src/lib.rs",
            "clients/rust/../contracts/README.md",
            "clients/rust//core/src/lib.rs",
            "clients/rust/./Cargo.toml",
            "clients/rust/Cargo.toml\x00hormuz/gateway.py",
            "clients/rust/",
        ]:
            with self.subTest(path=path):
                self.assertTrue(classify_changed_paths([path]).run_full)

    def test_website_only_paths_use_reduced_ci(self) -> None:
        decision = classify_changed_paths(
            [
                "website/app/page.tsx",
                "website/public/favicon.ico",
                "marketing/MEASUREMENT.md",
            ]
        )
        self.assertFalse(decision.run_full)
        self.assertEqual(decision.classification, "website_only")
        self.assertEqual(decision.changed_path_count, 3)

    def test_each_explicit_marketing_file_is_safe(self) -> None:
        for path in SAFE_EXACT_PATHS:
            with self.subTest(path=path):
                self.assertFalse(classify_changed_paths([path]).run_full)

    def test_unknown_documentation_or_marketing_path_runs_full_ci(self) -> None:
        for path in ["README.md", "docs/VERIFICATION.md", "marketing/OFFER.md"]:
            with self.subTest(path=path):
                self.assertTrue(classify_changed_paths([path]).run_full)

    def test_mixed_or_malformed_paths_run_full_ci(self) -> None:
        for paths in [
            ["website/app/page.tsx", "hormuz/gateway.py"],
            ["website"],
            ["website/../hormuz/gateway.py"],
            ["website//app/page.tsx"],
            [],
        ]:
            with self.subTest(paths=paths):
                self.assertTrue(classify_changed_paths(paths).run_full)

    def test_non_pull_request_events_always_run_full_ci(self) -> None:
        for event_name in ["push", "workflow_dispatch", "schedule", ""]:
            with self.subTest(event_name=event_name):
                decision = determine_scope(
                    event_name=event_name,
                    base_sha="",
                    head_sha="",
                    repository_root=Path.cwd(),
                )
                self.assertTrue(decision.run_full)
                self.assertEqual(decision.classification, "full_non_pull_request")

    @mock.patch("tools.classify_ci_scope.subprocess.run")
    def test_main_and_manual_runs_do_not_reduce_native_diffs(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, b"clients/rust/Cargo.lock\x00")
        for event_name in ["push", "workflow_dispatch"]:
            decision = determine_scope(
                event_name=event_name,
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                repository_root=Path.cwd(),
            )
            self.assertTrue(decision.run_full)
        run.assert_not_called()

    def test_invalid_pull_request_revision_runs_full_ci(self) -> None:
        decision = determine_scope(
            event_name="pull_request",
            base_sha="not-a-sha",
            head_sha=HEAD_SHA,
            repository_root=Path.cwd(),
        )
        self.assertTrue(decision.run_full)
        self.assertEqual(decision.classification, "full_invalid_revision")

    @mock.patch("tools.classify_ci_scope.subprocess.run")
    def test_git_detection_failure_runs_full_ci(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 128, b"")
        decision = determine_scope(
            event_name="pull_request",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            repository_root=Path.cwd(),
        )
        self.assertTrue(decision.run_full)
        self.assertEqual(decision.classification, "full_detection_failure")

    @mock.patch("tools.classify_ci_scope.subprocess.run")
    def test_git_paths_are_nul_delimited_and_classified(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            [], 0, b"website/app/page.tsx\x00marketing/COMMERCIAL_SETUP.md\x00"
        )
        decision = determine_scope(
            event_name="pull_request",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            repository_root=Path.cwd(),
        )
        self.assertFalse(decision.run_full)
        run.assert_called_once()
        self.assertIn("--no-renames", run.call_args.args[0])
        self.assertIn("-z", run.call_args.args[0])

    def test_cli_writes_only_bounded_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            summary = Path(temporary) / "summary"
            result = classify_main(
                [
                    "--event-name",
                    "workflow_dispatch",
                    "--github-output",
                    str(output),
                    "--step-summary",
                    str(summary),
                ]
            )
            self.assertEqual(result, 0)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "classification=full_non_pull_request\nchanged_path_count=0\nrun_full=true\n",
            )
            self.assertIn("Infrastructure suite: `full`", summary.read_text())


class CIRequiredGateTests(unittest.TestCase):
    def test_full_ci_requires_every_job_to_succeed(self) -> None:
        result = validate_required_results(
            scope_result="success",
            run_full="true",
            classification="full_changed_paths",
            result_values=_results(),
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["run_full"])

    def test_website_only_ci_requires_only_declared_scoped_jobs_to_skip(self) -> None:
        result = validate_required_results(
            scope_result="success",
            run_full="false",
            classification="website_only",
            result_values=_results(scoped="skipped", native="skipped"),
        )
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["run_full"])

    def test_native_only_ci_requires_successful_native_contracts(self) -> None:
        result = validate_required_results(
            scope_result="success",
            run_full="false",
            classification="native_only",
            result_values=_results(scoped="skipped"),
        )
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["run_full"])

    def test_each_native_only_applicable_job_blocks_on_failure_cancel_or_skip(self) -> None:
        for job_id in (*ALWAYS_REQUIRED_JOB_IDS, "native-contracts"):
            for outcome in ["failure", "cancelled", "skipped"]:
                with self.subTest(job_id=job_id, outcome=outcome):
                    results = [
                        f"{job_id}={outcome}" if value.startswith(f"{job_id}=") else value
                        for value in _results(scoped="skipped")
                    ]
                    with self.assertRaises(CIRequiredError):
                        validate_required_results(
                            scope_result="success",
                            run_full="false",
                            classification="native_only",
                            result_values=results,
                        )

    def test_failed_canceled_or_unexpectedly_skipped_jobs_block(self) -> None:
        cases = [
            _results(always="failure"),
            _results(always="cancelled"),
            _results(always="skipped"),
            _results(scoped="failure"),
            _results(scoped="cancelled"),
            _results(scoped="skipped"),
            _results(native="failure"),
            _results(native="cancelled"),
            _results(native="skipped"),
        ]
        for results in cases:
            with self.subTest(results=results):
                with self.assertRaises(CIRequiredError):
                    validate_required_results(
                        scope_result="success",
                        run_full="true",
                        classification="full_changed_paths",
                        result_values=results,
                    )

    def test_reduced_ci_rejects_a_scoped_job_that_ran_unexpectedly(self) -> None:
        for classification in ["website_only", "native_only"]:
            with self.subTest(classification=classification):
                with self.assertRaises(CIRequiredError):
                    validate_required_results(
                        scope_result="success",
                        run_full="false",
                        classification=classification,
                        result_values=_results(
                            scoped="success",
                            native="skipped" if classification == "website_only" else "success",
                        ),
                    )

    def test_website_only_ci_requires_native_job_to_skip(self) -> None:
        with self.assertRaises(CIRequiredError):
            validate_required_results(
                scope_result="success",
                run_full="false",
                classification="website_only",
                result_values=_results(scoped="skipped"),
            )

    def test_scope_failure_or_inconsistent_outputs_block(self) -> None:
        cases = [
            ("failure", "true", "full_detection_failure"),
            ("success", "", "full_detection_failure"),
            ("success", "false", "full_changed_paths"),
            ("success", "true", "website_only"),
            ("success", "true", "native_only"),
            ("success", "false", "unknown"),
        ]
        for scope_result, run_full, classification in cases:
            with self.subTest(
                scope_result=scope_result,
                run_full=run_full,
                classification=classification,
            ):
                with self.assertRaises(CIRequiredError):
                    validate_required_results(
                        scope_result=scope_result,
                        run_full=run_full,
                        classification=classification,
                        result_values=_results(),
                    )

    def test_missing_duplicate_or_unknown_job_results_block(self) -> None:
        complete = _results()
        cases = [
            complete[:-1],
            [*complete, complete[0]],
            [*complete, "unexpected=success"],
        ]
        for results in cases:
            with self.subTest(results=results):
                with self.assertRaises(CIRequiredError):
                    validate_required_results(
                        scope_result="success",
                        run_full="true",
                        classification="full_changed_paths",
                        result_values=results,
                    )


if __name__ == "__main__":
    unittest.main()
