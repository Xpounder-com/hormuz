"""Additive source authorization must retain every frozen legacy SHA guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools import _ai_work_source_successor as successor
from tools import verify_recommendation_runtime as verifier


class AIWorkSourceSuccessorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        plan = json.loads((verifier.ROOT / verifier.PLAN_PATH).read_text())
        for name in set(verifier.REQUIRED_FILES) | set(plan["source_sha256"]):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(verifier.ROOT / name, target)
        self.plan = plan

    def tearDown(self):
        self.directory.cleanup()

    def test_approved_additions_project_to_original_sha_without_repinning_plan(self):
        authorization = successor.contract(self.root)
        for name in authorization["source_edits"]:
            with self.subTest(source=name):
                expected = self.plan["source_sha256"][name]
                self.assertEqual(expected, successor.projected_sha256(self.root, name, expected))
        self.assertEqual(verifier.PLAN_SHA256, verifier.canonical_digest(self.plan))
        self.assertEqual("recommendation_runtime_candidate_verified", verifier.verify(self.root)["status"])

    def test_legacy_edits_fail_in_every_authorized_source(self):
        for name in successor.contract(self.root)["source_edits"]:
            with self.subTest(source=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\nlegacy scope changed\n")
                try:
                    with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
                        verifier.verify(self.root)
                finally:
                    path.write_bytes(original)

    def test_unreviewed_package_data_addition_is_not_hidden(self):
        path = self.root / "pyproject.toml"
        path.write_text(path.read_text().replace('"policy.css"', '"policy.css", "unreviewed.css"'))
        with self.assertRaises(ValueError):
            verifier.verify(self.root)

    def test_legacy_wire_and_schema_sources_remain_guarded(self):
        for name in ("hormuz/portfolio-intelligence-wire-v1.json", "hormuz/_sqlite_schema.py"):
            with self.subTest(source=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\nlegacy wire changed\n")
                try:
                    with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
                        verifier.verify(self.root)
                finally:
                    path.write_bytes(original)

    def test_authorization_cannot_expand_its_own_scope(self):
        path = self.root / successor.CONTRACT_PATH
        authorization = json.loads(path.read_text())
        authorization["addition_counts"]["database_classes"] = 3
        path.write_text(json.dumps(authorization, indent=2) + "\n")
        with self.assertRaisesRegex(ValueError, "ai_work_source_authorization_invalid"):
            successor.contract(self.root)

    def test_new_ai_work_boundary_must_equal_approved_addition(self):
        path = self.root / "docs/durable-data-v1.json"
        value = json.loads(path.read_text())
        classes = next(item for item in value["database_classes"] if item["id"] == "ai_work_metadata")
        classes["contains_prompt_or_response_body"] = True
        path.write_text(json.dumps(value, indent=2) + "\n")
        with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
            verifier.verify(self.root)

    def test_wrong_parent_digest_is_not_an_authorized_successor(self):
        with self.assertRaisesRegex(ValueError, "ai_work_source_authorization_invalid"):
            successor.projected_sha256(self.root, "MANIFEST.in", "0" * 64)

    def test_approved_source_document_is_required(self):
        (self.root / successor.CONTRACT_PATH).unlink()
        with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_kit_incomplete"):
            verifier.verify(self.root)


if __name__ == "__main__":
    unittest.main()
