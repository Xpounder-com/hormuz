from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from hormuz.config import DLPRuleConfig
from hormuz.dlp_evaluation import (
    DLPEvaluationCase,
    DLPEvaluationError,
    evaluate_dlp_rule,
    load_evaluation_corpus,
    write_evaluation_result,
)
from hormuz.redaction import MAX_JSON_DEPTH


class DLPEvaluationTests(unittest.TestCase):
    def test_report_is_aggregate_content_free_and_measures_confusion_matrix(self) -> None:
        marker = "ORGANIZATION-EVALUATION-CONTENT-NEVER-RETAIN"
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus.jsonl"
            corpus.write_text(
                "\n".join(
                    json.dumps(value)
                    for value in (
                        {
                            "payload": {"input": f"employee@example.com {marker}-tp"},
                            "expected_match": True,
                        },
                        {
                            "payload": {"input": f"ordinary prose {marker}-tn"},
                            "expected_match": False,
                        },
                        {
                            "payload": {"input": f"support@example.com {marker}-fp"},
                            "expected_match": False,
                        },
                        {
                            "payload": {"input": f"owner [at] example.com {marker}-fn"},
                            "expected_match": True,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            cases = load_evaluation_corpus(corpus)
            result = evaluate_dlp_rule(
                cases,
                rule=DLPRuleConfig(
                    rule_id="email_address",
                    category="pii",
                    confidence="low",
                    action="detect",
                ),
                policy_version="organization-dlp-v7",
                corpus_id="email-eval-2026-08-v1",
                protocol="openai",
                model="gpt-test",
            )

        self.assertEqual(
            result["confusion_matrix"],
            {
                "true_positive": 1,
                "true_negative": 1,
                "false_positive": 1,
                "false_negative": 1,
            },
        )
        self.assertEqual(result["metrics"]["precision"], 0.5)
        self.assertEqual(result["metrics"]["recall"], 0.5)
        self.assertEqual(result["metrics"]["false_positive_rate"], 0.5)
        self.assertEqual(result["metrics"]["false_negative_rate"], 0.5)
        self.assertEqual(result["metrics"]["accuracy"], 0.5)
        self.assertEqual(
            result["corpus"],
            {
                "corpus_id": "email-eval-2026-08-v1",
                "case_count": 4,
                "positive_count": 2,
                "negative_count": 2,
            },
        )
        self.assertEqual(result["rule"]["configured_action"], "detect")
        self.assertEqual(
            result["detector"]["version"],
            "hormuz-deterministic-v2",
        )
        self.assertFalse(result["privacy"]["payloads_retained"])
        self.assertFalse(result["promotion"]["automatic"])
        self.assertEqual(
            result["promotion"]["decision"],
            "manual_policy_decision_required",
        )
        self.assertNotIn(marker, json.dumps(result))
        self.assertNotIn('"payload":', json.dumps(result))

    def test_configured_detector_scope_and_values_are_not_disclosed(self) -> None:
        protected = "PROJECT-MAGENTA-INTERNAL"
        rule = DLPRuleConfig(
            rule_id="company.codename",
            category="company_dictionary",
            confidence="high",
            action="deny",
            providers=("anthropic",),
            models=("claude-company",),
            values_env="COMPANY_DLP_VALUES",
            exact_values=(protected,),
        )
        encoded = base64.b64encode(
            f"codename={protected}".encode("utf-8")
        ).decode("ascii")
        percent_encoded = "".join(
            f"%{byte:02X}" for byte in protected.encode("utf-8")
        )
        hex_encoded = protected.encode("utf-8").hex()
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus.jsonl"
            corpus.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "payload": {"messages": [{"content": item}]},
                            "expected_match": True,
                        }
                    )
                    for item in (encoded, percent_encoded, hex_encoded)
                )
                + "\n",
                encoding="utf-8",
            )
            cases = load_evaluation_corpus(corpus)

        result = evaluate_dlp_rule(
            cases,
            rule=rule,
            policy_version="dictionary-v1",
            corpus_id="dictionary-v1",
            protocol="anthropic",
            model="claude-company",
        )
        self.assertEqual(result["confusion_matrix"]["true_positive"], 3)
        self.assertEqual(result["finding_count"], 3)
        self.assertNotIn(protected, repr(result))
        self.assertNotIn(encoded, repr(result))
        self.assertNotIn(percent_encoded, repr(result))
        self.assertNotIn(hex_encoded, repr(result))
        self.assertNotIn("COMPANY_DLP_VALUES", repr(result))

        with self.assertRaisesRegex(DLPEvaluationError, "outside the configured scope"):
            evaluate_dlp_rule(
                cases,
                rule=rule,
                policy_version="dictionary-v1",
                corpus_id="dictionary-v1",
                protocol="openai",
                model="gpt-test",
            )

    def test_corpus_schema_is_strict_and_errors_do_not_reflect_content(self) -> None:
        marker = "DO-NOT-REFLECT-EVALUATION-CONTENT"
        invalid_lines = (
            (
                '{"payload":{"input":"first"},"payload":{"input":"duplicate"},'
                '"expected_match":true}',
                "duplicate JSON member",
            ),
            (
                json.dumps(
                    {
                        "payload": {"input": marker},
                        "expected_match": True,
                        "unknown": 1,
                    }
                ),
                "exactly payload and expected_match",
            ),
            (
                '{"payload":{"input":"safe"},"expected_match":NaN}',
                "non-standard JSON constant",
            ),
            (
                json.dumps({"payload": marker, "expected_match": True}),
                "payload must be an object",
            ),
            (
                json.dumps({"payload": {"input": marker}, "expected_match": 1}),
                "expected_match must be boolean",
            ),
        )
        for line, expected_error in invalid_lines:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as temporary:
                corpus = Path(temporary) / "invalid.jsonl"
                corpus.write_text(line + "\n", encoding="utf-8")
                with self.assertRaisesRegex(DLPEvaluationError, expected_error) as raised:
                    load_evaluation_corpus(corpus)
                self.assertNotIn(marker, str(raised.exception))

        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty.jsonl"
            empty.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(DLPEvaluationError, "at least one case"):
                load_evaluation_corpus(empty)

    def test_detector_failure_is_content_free_and_produces_no_partial_report(self) -> None:
        marker = "DETECTOR-FAILURE-CONTENT-NEVER-REFLECT"
        payload: dict = {"input": marker}
        for _ in range(MAX_JSON_DEPTH + 1):
            payload = {"nested": payload}

        with self.assertRaisesRegex(
            DLPEvaluationError,
            "rejected evaluation case 1 without producing a report",
        ) as raised:
            evaluate_dlp_rule(
                (
                    # The payload is intentionally content-bearing but hidden from repr/errors.
                    DLPEvaluationCase(payload=payload, expected_match=False),
                ),
                rule=DLPRuleConfig(
                    rule_id="email_address",
                    category="pii",
                    confidence="low",
                    action="detect",
                ),
                policy_version="organization-dlp-v7",
                corpus_id="failure-eval-v1",
                protocol="openai",
                model="gpt-test",
            )

        self.assertNotIn(marker, str(raised.exception))

    def test_metrics_are_null_when_a_denominator_has_no_cases(self) -> None:
        result = evaluate_dlp_rule(
            (
                DLPEvaluationCase(
                    payload={"input": "ordinary text"},
                    expected_match=False,
                ),
            ),
            rule=DLPRuleConfig(
                rule_id="email_address",
                category="pii",
                confidence="low",
                action="detect",
            ),
            policy_version="organization-dlp-v7",
            corpus_id="negative-only-v1",
            protocol="openai",
            model="gpt-test",
        )

        self.assertIsNone(result["metrics"]["precision"])
        self.assertIsNone(result["metrics"]["recall"])
        self.assertIsNone(result["metrics"]["false_negative_rate"])
        self.assertEqual(result["metrics"]["specificity"], 1.0)
        self.assertEqual(result["metrics"]["false_positive_rate"], 0.0)

    def test_output_is_private_content_free_and_refuses_overwrite(self) -> None:
        result = {
            "schema_version": "hormuz.dlp-evaluation.v1",
            "privacy": {"payloads_retained": False},
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evaluation.json"
            write_evaluation_result(result, str(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
            with self.assertRaises(DLPEvaluationError):
                write_evaluation_result(result, str(output))
            output.chmod(0o644)
            write_evaluation_result(result, str(output), force=True)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
