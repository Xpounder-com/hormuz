"""Prevent unfrozen plans, changed predecessor bytes and incomplete source kits."""

import copy
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import verify_finance_account_binding_preflight as verifier
from tools.verify_finance_account_binding_contract import REQUIRED_FILES as SCOPE_FILES
from tests import _finance_account_binding_predecessor_fixture as predecessor

ROOT = Path(__file__).resolve().parents[1]


class FinanceAccountBindingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / verifier.PLAN_PATH).read_bytes())

    def test_complete_plan_preserves_runtime_and_does_not_claim_acceptance(self):
        result = verifier.verify(ROOT)
        self.assertEqual(result["runtime_files_verified"], 161)
        self.assertFalse(result["published_artifacts_verified"])
        self.assertTrue(result["gates"].pop("owner_scope_approved"))
        self.assertTrue(all(value is False for value in result["gates"].values()))

    def test_each_required_proof_and_unaccepted_gate_is_digest_bound(self):
        for field in ("required_preflight_evidence", "implementation_must_replace_witnesses"):
            for case in self.plan[field]:
                changed = copy.deepcopy(self.plan)
                changed[field].remove(case)
                with self.subTest(case=case), self.assertRaisesRegex(ValueError, "plan_changed"):
                    verifier.validate_plan(changed)
        for gate in self.plan["gates"]:
            changed = copy.deepcopy(self.plan)
            changed["gates"][gate] = not changed["gates"][gate]
            with self.subTest(gate=gate), self.assertRaisesRegex(ValueError, "plan_changed"):
                verifier.validate_plan(changed)

    def test_scope_acl_and_history_cannot_be_silently_weakened(self):
        for field in ("configuration", "registration", "planned_storage", "audit",
                      "storage_guards", "postgresql_acl_proposal", "rollout", "frozen_file_sha256"):
            changed = copy.deepcopy(self.plan)
            changed[field] = {}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "plan_changed"):
                verifier.validate_plan(changed)

    def test_plan_requires_exact_two_artifacts_before_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            corrupt = Path(temporary) / "corrupt"
            corrupt.write_bytes(b"not the published artifact")
            with self.assertRaisesRegex(ValueError, "source_mismatch"):
                verifier.verify_artifacts(corrupt, corrupt)
            with self.assertRaisesRegex(RuntimeError, "source_mismatch"):
                predecessor.verified_source(corrupt)
        with self.assertRaisesRegex(ValueError, "requires_both_artifacts"):
            verifier.verify(ROOT, predecessor_source="absent")

    def test_source_kit_requires_every_new_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in set(verifier.REQUIRED_FILES) | set(self.plan["frozen_file_sha256"]):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            for name in verifier.REQUIRED_FILES:
                target = root / name
                original = target.read_bytes()
                target.unlink()
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "source_kit_incomplete"):
                    verifier.verify(root)
                target.write_bytes(original)

    def test_frozen_predecessor_contract_bytes_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = set(verifier.REQUIRED_FILES) | set(SCOPE_FILES) | set(self.plan["frozen_file_sha256"])
            for name in files:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            shutil.copytree(ROOT / "hormuz", root / "hormuz", ignore=shutil.ignore_patterns("__pycache__"))
            verifier.verify(root)
            path = root / "docs/finance-transition-plan-v7.json"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "frozen_history_changed"):
                verifier.verify(root)

    def test_candidate_runtime_mismatch_is_rejected(self):
        runtime = verifier.runtime_tree(ROOT / "hormuz")
        for change in ("remove", "alter", "add"):
            changed = dict(runtime)
            name = next(iter(changed))
            if change == "remove":
                del changed[name]
            elif change == "alter":
                changed[name] = "0" * 64
            else:
                changed["hormuz/unexpected.py"] = "0" * 64
            with self.subTest(change=change), mock.patch.object(verifier, "runtime_tree", return_value=changed):
                with self.assertRaisesRegex(ValueError, "runtime_changed"):
                    verifier.verify(ROOT)

    def test_predecessor_verifier_rejects_missing_altered_and_extra_installed_files(self):
        # Use actual current runtime bytes, which the plan independently binds
        # to the published 161-file predecessor, in an in-memory source tar.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive:
            for name in verifier.runtime_tree(ROOT / "hormuz"):
                payload = (ROOT / name).read_bytes()
                info = tarfile.TarInfo(predecessor.ARCHIVE_PREFIX + name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as temporary, tarfile.open(fileobj=io.BytesIO(buffer.getvalue()), mode="r:") as archive:
            package = Path(temporary) / "hormuz"
            shutil.copytree(ROOT / "hormuz", package, ignore=shutil.ignore_patterns("__pycache__"))
            self.assertEqual(predecessor.verify_installed_runtime(archive, package), 161)
            path = package / "__init__.py"
            original = path.read_bytes()
            for change in ("alter", "remove", "extra"):
                if change == "alter":
                    path.write_bytes(b"changed")
                elif change == "remove":
                    path.unlink()
                else:
                    (package / "unexpected.py").write_bytes(b"extra")
                with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "runtime_mismatch"):
                    predecessor.verify_installed_runtime(archive, package)
                path.write_bytes(original)
                (package / "unexpected.py").unlink(missing_ok=True)

    def test_manifest_includes_plan_verifier_and_fixture_source(self):
        lines = (ROOT / "MANIFEST.in").read_text().splitlines()
        self.assertIn("include " + verifier.PLAN_PATH, lines)
        self.assertIn("include tools/verify_finance_account_binding_preflight.py", lines)
        self.assertIn("recursive-include tests *.py *.json", lines)


if __name__ == "__main__":
    unittest.main()
