from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from hormuz.cli import main
from hormuz.context_benchmark import (
    ContextBenchmarkError,
    load_benchmark,
    run_benchmark,
    write_benchmark_result,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "hormuz" / "benchmark_data" / "context-corpus.v1.json"
REFERENCES = ROOT / "hormuz" / "benchmark_data" / "context-references.v1.json"
CATEGORIES = {
    "bug_fix",
    "feature",
    "refactor",
    "incident",
    "onboarding",
    "policy_question",
}
CHALLENGES = {
    "authorization_cross_scope",
    "stale_relevant",
    "superseded_decision",
    "contradiction",
    "changed_dependency",
    "malicious_context",
}


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ContextBenchmarkTests(unittest.TestCase):
    def test_frozen_corpus_has_stratified_tasks_and_separate_outcomes(self) -> None:
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        references = json.loads(REFERENCES.read_text(encoding="utf-8"))

        self.assertEqual(len(corpus["tasks"]), 60)
        self.assertEqual(
            {category: 10 for category in CATEGORIES},
            {
                category: sum(task["category"] == category for task in corpus["tasks"])
                for category in CATEGORIES
            },
        )
        self.assertEqual(
            {challenge: 10 for challenge in CHALLENGES},
            {
                challenge: sum(task["challenge"] == challenge for task in corpus["tasks"])
                for challenge in CHALLENGES
            },
        )
        self.assertEqual(sum(task["ci"] for task in corpus["tasks"]), 12)
        self.assertEqual(references["corpus_sha256"], canonical_sha256(corpus))
        self.assertNotIn("reference_outcome", CORPUS.read_text(encoding="utf-8"))

        loaded = load_benchmark(CORPUS, REFERENCES)
        self.assertEqual(loaded.leakage_failures, ())
        self.assertEqual(len(loaded.tasks), 60)

    def test_report_exposes_safety_guarantees_and_current_release_gaps(self) -> None:
        result, exit_code = run_benchmark(CORPUS, REFERENCES, profile="report", iterations=2)
        governed = result["baselines"]["hormuz_governed"]

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "reported")
        self.assertEqual(governed["authorization_leak_task_rate"], 0)
        self.assertEqual(governed["lifecycle_stale_task_rate"], 0)
        self.assertEqual(governed["token_budget_violations"], 0)
        self.assertEqual(governed["determinism_failures"], 0)
        self.assertEqual(governed["recall"], 1)
        self.assertGreater(result["baselines"]["full_history"]["authorization_leak_task_rate"], 0)
        self.assertGreater(result["baselines"]["simple_lexical"]["lifecycle_stale_task_rate"], 0)
        self.assertFalse(result["contract_observations"]["dependency_invalidation_automatic"])
        self.assertFalse(result["contract_observations"]["malicious_context_quarantine"])
        self.assertFalse(result["contract_observations"]["contradiction_outcome_explicit"])

    def test_regression_subset_passes_and_release_profile_fails_known_gaps(self) -> None:
        regression, regression_exit = run_benchmark(
            CORPUS,
            REFERENCES,
            profile="regression",
            ci_subset=True,
            iterations=3,
        )
        release, release_exit = run_benchmark(CORPUS, REFERENCES, profile="release")

        self.assertEqual(regression_exit, 0)
        self.assertEqual(regression["status"], "passed")
        self.assertEqual(regression["corpus"]["task_count"], 12)
        self.assertEqual(release_exit, 2)
        self.assertEqual(release["status"], "failed")
        failed = {item["metric"] for item in release["thresholds"] if not item["passed"]}
        self.assertEqual(
            failed,
            {
                "hormuz_governed.precision",
                "hormuz_governed.useful_pack_rate",
                "hormuz_governed.stale_selection_task_rate",
                "hormuz_governed.dependency_stale_challenge_rate",
                "hormuz_governed.malicious_challenge_selection_rate",
                "hormuz_governed.contradiction_challenge_selection_rate",
            },
        )

    def test_tampered_frozen_snapshot_and_unknown_fields_fail_closed(self) -> None:
        original_corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        original_references = json.loads(REFERENCES.read_text(encoding="utf-8"))
        mutations = (
            ("memory snapshot", lambda value: value["tasks"][0].__setitem__("memory_snapshot_sha256", "0" * 64)),
            ("unknown field", lambda value: value["tasks"][0].__setitem__("unexpected", True)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                corpus = copy.deepcopy(original_corpus)
                references = copy.deepcopy(original_references)
                mutate(corpus)
                references["corpus_sha256"] = canonical_sha256(corpus)
                corpus_path = Path(temporary) / "corpus.json"
                references_path = Path(temporary) / "references.json"
                corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
                references_path.write_text(json.dumps(references), encoding="utf-8")

                with self.assertRaises(ContextBenchmarkError):
                    load_benchmark(corpus_path, references_path)

    def test_solution_leakage_is_detected_and_fails_regression(self) -> None:
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        references = json.loads(REFERENCES.read_text(encoding="utf-8"))
        leaked_content = corpus["tasks"][0]["records"][0]["content"]
        references["outcomes"][0]["reference_outcome"] = leaked_content
        references["outcomes"][0]["reference_outcome_sha256"] = hashlib.sha256(
            leaked_content.encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            corpus_path = Path(temporary) / "corpus.json"
            references_path = Path(temporary) / "references.json"
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
            references_path.write_text(json.dumps(references), encoding="utf-8")

            loaded = load_benchmark(corpus_path, references_path)
            result, exit_code = run_benchmark(
                corpus_path,
                references_path,
                profile="regression",
                ci_subset=True,
            )

        self.assertEqual(loaded.leakage_failures, (corpus["tasks"][0]["task_id"],))
        self.assertEqual(exit_code, 2)
        failure = next(
            item for item in result["thresholds"] if item["metric"] == "corpus.leakage_review_failures"
        )
        self.assertFalse(failure["passed"])

    def test_evidence_output_is_private_and_protected_from_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            write_benchmark_result({"status": "test"}, str(output))

            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "test"})
            with self.assertRaises(ContextBenchmarkError):
                write_benchmark_result({"status": "replaced"}, str(output))
            output.chmod(0o644)
            write_benchmark_result({"status": "replaced"}, str(output), force=True)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "replaced"})
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_cli_benchmark_does_not_require_gateway_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, redirect_stderr(io.StringIO()):
            output = Path(temporary) / "evidence.json"
            exit_code = main(
                [
                    "--config",
                    str(Path(temporary) / "missing-hormuz.json"),
                    "context-benchmark",
                    "--corpus",
                    str(CORPUS),
                    "--references",
                    str(REFERENCES),
                    "--profile",
                    "regression",
                    "--ci-subset",
                    "--output",
                    str(output),
                ]
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
