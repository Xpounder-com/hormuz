"""Benchmark comparison and suite rules without a router, processes or sockets."""
import unittest

from hormuz.model_benchmark import BenchmarkError, check_answer, validate_suite


class ModelBenchmarkFastTests(unittest.TestCase):
    def suite(self):
        return {"schema_id": "hormuz.benchmark-suite", "schema_version": 1, "name": "my-work",
                "targets": {"objective": "quality", "min_pass_rate": 1, "min_cases": 2},
                "cases": [
                    {"id": "seven", "task_type": "summarization", "partition": "held_out",
                     "request": {"input": "Summarize the count as a digit: seven boxes.",
                                 "max_output_tokens": 8},
                     "checks": [{"metric": "correctness", "kind": "text_equals", "value": "7"}]},
                    {"id": "nine", "task_type": "summarization", "partition": "held_out",
                     "request": {"input": "Summarize the count as a digit: nine boxes.",
                                 "max_output_tokens": 8},
                     "checks": [{"metric": "correctness", "kind": "text_equals", "value": "9"}]},
                ]}

    def test_json_checks_compare_numeric_values_without_equating_booleans(self):
        examples = (("numeric", {"count": 1}, '{"count":1.0}', True),
                    ("boolean", {"count": True}, '{"count":1}', False),
                    ("nested", {"items": [1, {"ok": True}]}, '{"items":[1.0,{"ok":true}]}', True))
        for kind in ("json_equals", "tool_call"):
            for name, expected, answer, matches in examples:
                with self.subTest(kind=kind, example=name):
                    reference = expected if kind == "json_equals" else {"name": "lookup_count", "arguments": expected}
                    check = {"metric": "json_accuracy", "kind": kind, "value": reference}
                    output = ([{"type": "message", "content": [{"type": "output_text", "text": answer}]}]
                              if kind == "json_equals" else [{"type": "function_call", "call_id": "fixture-call",
                                  "name": "lookup_count", "arguments": answer}])
                    self.assertIs(check_answer(check, {"output": output}), matches)

    def test_invalid_targets_and_unbounded_or_stateful_tasks_are_rejected(self):
        for mutation in ("target", "target_overflow", "output_limit", "continuation", "unknown_field", "duplicate_metric"):
            with self.subTest(mutation=mutation):
                suite = self.suite()
                if mutation == "target":
                    suite["targets"]["min_pass_rate"] = -1
                elif mutation == "target_overflow":
                    suite["targets"]["min_pass_rate"] = 10**400
                elif mutation == "output_limit":
                    suite["cases"][0]["request"]["max_output_tokens"] = 10**9
                elif mutation == "continuation":
                    suite["cases"][0]["request"]["previous_response_id"] = "opaque-history"
                elif mutation == "unknown_field":
                    suite["unexpected"] = True
                else:
                    suite["cases"][0]["checks"] *= 2
                with self.assertRaises(BenchmarkError) as failure:
                    validate_suite(suite)
                self.assertEqual(failure.exception.code, "benchmark_suite_invalid")

    def test_task_labels_cannot_certify_a_different_classified_workload(self):
        suite = self.suite()
        for case in suite["cases"]:
            case["task_type"] = "translation"
        with self.assertRaises(BenchmarkError) as failure:
            validate_suite(suite)
        self.assertEqual(failure.exception.code, "benchmark_suite_invalid")

    def test_native_coding_tasks_are_rejected_before_direct_model_benchmarking(self):
        for task, prompt in (("code_change", "Fix the Python function to return seven."),
                             ("code_review", "Review this code diff for bugs.")):
            with self.subTest(task=task):
                suite = self.suite()
                suite["cases"] = suite["cases"][:1]
                suite["cases"][0]["task_type"] = task
                suite["cases"][0]["request"]["input"] = prompt
                with self.assertRaises(BenchmarkError) as failure:
                    validate_suite(suite)
                self.assertEqual(failure.exception.code, "benchmark_suite_invalid")


if __name__ == "__main__":
    unittest.main()
