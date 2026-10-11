"""Additive source authorization must retain every frozen legacy SHA guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

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

    def test_hosted_manifest_inputs_are_exact_additions_under_the_frozen_parent(self):
        expected_additions = (
            "include requirements/oci-build-linux-amd64.lock\ninclude requirements/oci-runtime-linux-amd64.lock\n",
            "include deploy/render/gateway/ai-work.Caddyfile\ninclude deploy/render/gateway/ai-work-profile.example.json\n",
        )
        item = successor.contract(self.root)["source_edits"]["MANIFEST.in"]
        expected = self.plan["source_sha256"]["MANIFEST.in"]
        self.assertEqual(item["predecessor_sha256"], expected)
        self.assertEqual(item["edits"][-2:], [{"predecessor": "", "addition": text} for text in expected_additions])
        self.assertEqual(expected, successor.projected_sha256(self.root, "MANIFEST.in", expected))
        self.assertEqual(verifier.PLAN_SHA256, verifier.canonical_digest(self.plan))

    def test_hosted_manifest_inputs_cannot_be_missing_duplicated_or_replaced(self):
        path = self.root / "MANIFEST.in"
        original = path.read_bytes()
        lines = (b"include requirements/oci-build-linux-amd64.lock\n",
                 b"include requirements/oci-runtime-linux-amd64.lock\n",
                 b"include deploy/render/gateway/ai-work.Caddyfile\n",
                 b"include deploy/render/gateway/ai-work-profile.example.json\n")
        for line in lines:
            for replacement in (b"", line + line, b"include unreviewed-hosted-input\n"):
                with self.subTest(line=line.decode().strip(), replacement=replacement.decode().strip()):
                    self.assertEqual(original.count(line), 1)
                    path.write_bytes(original.replace(line, replacement, 1))
                    try:
                        with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
                            verifier.verify(self.root)
                    finally:
                        path.write_bytes(original)

    def test_other_manifest_additions_are_still_visible_to_the_frozen_source_guard(self):
        path = self.root / "MANIFEST.in"
        path.write_bytes(path.read_bytes() + b"include deploy/render/gateway/unreviewed.Caddyfile\n")
        with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
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

    def test_ci_successor_requires_exact_reviewed_bytes_and_frozen_parent(self):
        authorization = successor.contract(self.root)["ci_source_successor"]
        expected = self.plan["source_sha256"][successor.CI_SOURCE_PATH]
        self.assertEqual(expected, authorization["predecessor_sha256"])
        self.assertEqual(
            authorization["successor_sha256"],
            hashlib.sha256((self.root / successor.CI_SOURCE_PATH).read_bytes()).hexdigest(),
        )
        self.assertEqual(expected, successor.projected_sha256(self.root, successor.CI_SOURCE_PATH, expected))
        with self.assertRaisesRegex(ValueError, "ai_work_source_authorization_invalid"):
            successor.projected_sha256(self.root, successor.CI_SOURCE_PATH, "0" * 64)
        self.assertEqual(verifier.PLAN_SHA256, verifier.canonical_digest(self.plan))

    def test_ci_successor_rejects_guard_cleanup_image_and_comment_changes(self):
        path = self.root / successor.CI_SOURCE_PATH
        original = path.read_bytes()
        mutations = {
            "repository_guard": original.replace(b"Xpounder-com/hormuz", b"unreviewed/hormuz", 1),
            "credential_cleanup": original.replace(b'rm -f -- "${RUNNER_TEMP:?}/hormuz-dockerhub-read-auth/config.json"', b'true # removed credential cleanup', 1),
            "buildkit_pin": original.replace(b"moby/buildkit@sha256:", b"moby/buildkit:latest #", 1),
            "comment_only": original + b"\n# unreviewed workflow edit\n",
        }
        for label, payload in mutations.items():
            with self.subTest(change=label):
                self.assertNotEqual(original, payload)
                path.write_bytes(payload)
                try:
                    with self.assertRaisesRegex(ValueError, "ai_work_ci_source_changed"):
                        successor.projected_sha256(self.root, successor.CI_SOURCE_PATH, self.plan["source_sha256"][successor.CI_SOURCE_PATH])
                    with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_changed"):
                        verifier.verify(self.root)
                finally:
                    path.write_bytes(original)

    def test_ci_successor_cannot_authorize_other_paths(self):
        for relative in (".github/workflows/native-clients.yml", "../ci.yml", "/tmp/ci.yml"):
            with self.subTest(path=relative):
                with self.assertRaisesRegex(ValueError, "ai_work_source_authorization_invalid"):
                    successor.projected_sha256(self.root, relative, self.plan["source_sha256"][successor.CI_SOURCE_PATH])

    def test_ci_successor_schema_cannot_expand_even_with_a_new_document_digest(self):
        path = self.root / successor.CONTRACT_PATH
        original = json.loads(path.read_text())
        mutations = {
            "path": lambda item: item["ci_source_successor"].update(path=".github/workflows/native-clients.yml"),
            "parent": lambda item: item["ci_source_successor"].update(predecessor_sha256="0" * 64),
            "child": lambda item: item["ci_source_successor"].update(successor_sha256="0" * 64),
            "extra_field": lambda item: item["ci_source_successor"].update(ignore=True),
            "field_type": lambda item: item.update(ci_source_successor=[]),
            "missing_field": lambda item: item.pop("ci_source_successor"),
            "schema_version": lambda item: item.update(schema_version=2),
        }
        for label, mutate in mutations.items():
            with self.subTest(change=label):
                authorization = json.loads(json.dumps(original))
                mutate(authorization)
                path.write_text(json.dumps(authorization, indent=2) + "\n")
                digest = hashlib.sha256(json.dumps(authorization, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                with mock.patch.object(successor, "CONTRACT_SHA256", digest):
                    with self.assertRaisesRegex(ValueError, "ai_work_source_authorization_invalid"):
                        successor.contract(self.root)

    def test_approved_source_document_is_required(self):
        (self.root / successor.CONTRACT_PATH).unlink()
        with self.assertRaisesRegex(verifier.RecommendationRuntimePlanError, "recommendation_runtime_source_kit_incomplete"):
            verifier.verify(self.root)


if __name__ == "__main__":
    unittest.main()
