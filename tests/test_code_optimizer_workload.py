"""Prove the optional external optimizer workload still describes Hormuz code."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hormuz import compaction


ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "benchmarks/code_optimizer/compaction.py"


class CodeOptimizerWorkloadTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("hormuz_optimizer_workload", WORKLOAD)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workload)
        self.workload = workload

    def candidate_source_with(self, original: str, replacement: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        package = root / "hormuz"
        package.mkdir()
        for name in ("compaction.py", "compaction_formats.py"):
            (package / name).write_bytes((ROOT / "hormuz" / name).read_bytes())
        source = package / "compaction.py"
        content = source.read_text(encoding="utf-8")
        self.assertEqual(content.count(original), 1)
        source.write_text(content.replace(original, replacement, 1), encoding="utf-8")
        return root

    def test_exact_compaction_file_bypasses_candidate_package_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "hormuz"
            package.mkdir()
            for name in ("compaction.py", "compaction_formats.py"):
                (package / name).write_bytes((ROOT / "hormuz" / name).read_bytes())
            (package / "decoy.py").write_text("# candidate decoy\n", encoding="utf-8")
            (package / "__init__.py").write_text(
                "from pathlib import Path\n"
                "from types import SimpleNamespace\n"
                "compaction = SimpleNamespace("
                "__file__=str(Path(__file__).with_name('decoy.py')), "
                "compact_text=lambda value, format_name: value)\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(WORKLOAD), "--root", str(root), "--action", "validate"],
                capture_output=True, text=True, check=True,
            )
            result = json.loads(completed.stdout)
            baseline = self.workload.evaluate(ROOT, "validate")
            self.assertEqual(Path(result["source"]).resolve(), (package / "compaction.py").resolve())
            self.assertEqual(result["outputs"], baseline["outputs"])

    def test_reference_workload_covers_primary_and_heldout_cases(self) -> None:
        workload = self.workload
        measured = workload.cases()
        heldout = workload.heldout_cases()
        self.assertIn("paths_typical", measured)
        self.assertIn("paths_large", measured)
        self.assertIn("paths_empty", measured)
        self.assertIn("paths_malformed_marker", measured)
        self.assertIn("paths_duplicate_unicode", measured)
        self.assertIn("json_typical", measured)
        self.assertIn("crlf_paths", heldout)
        self.assertIn("lines_mixed", heldout)
        self.assertIn("search_framed", heldout)
        self.assertIn("json_table", heldout)
        self.assertFalse(set(measured) & set(heldout))

        baseline = workload.evaluate(ROOT, "validate")
        holdout = workload.evaluate(ROOT, "heldout")
        self.assertEqual(len(baseline["outputs"]), len(measured))
        self.assertEqual(len(holdout["outputs"]), len(heldout))
        self.assertNotEqual(baseline["fixture_sha256"], holdout["fixture_sha256"])
        self.assertEqual(
            Path(baseline["source"]).resolve(), (ROOT / "hormuz/compaction.py").resolve()
        )
        self.assertTrue(all(len(record["sha256"]) == 64 for record in baseline["outputs"].values()))

    def test_mixed_runs_and_framed_search_are_effective_heldout_cases(self) -> None:
        for name in ("lines_mixed", "search_framed"):
            with self.subTest(name=name):
                value, format_name = self.workload.heldout_cases()[name]
                compacted = compaction.compact_text(value, format_name)
                self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
                self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        disabled_mixed_root = self.candidate_source_with(
            "def _compact_line_runs(text: str) -> str:\n",
            "def _compact_line_runs(text: str) -> str:\n"
            "    if len(set(text.splitlines())) > 1:\n"
            "        return text\n",
        )
        disabled_mixed = self.workload.evaluate(disabled_mixed_root, "heldout")
        self.assertNotEqual(baseline["outputs"]["lines_mixed"], disabled_mixed["outputs"]["lines_mixed"])
        disabled_framed_root = self.candidate_source_with(
            'def _compact_framed_lines(text: str, *, format: Literal["search_lines", "path_list"]) -> str:\n',
            'def _compact_framed_lines(text: str, *, format: Literal["search_lines", "path_list"]) -> str:\n'
            '    if format == "search_lines":\n'
            '        return text\n',
        )
        disabled_framed = self.workload.evaluate(disabled_framed_root, "heldout")
        self.assertNotEqual(baseline["outputs"]["search_framed"], disabled_framed["outputs"]["search_framed"])

    def test_heldout_json_table_detects_disabled_compaction(self) -> None:
        value, format_name = self.workload.heldout_cases()["json_table"]
        compacted = compaction.compact_text(value, format_name)
        self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
        self.assertTrue(compaction.decode_text(compacted).recognized)
        self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        disabled_root = self.candidate_source_with(
            "def _compact_json_table(text: str) -> str:\n",
            "def _compact_json_table(text: str) -> str:\n    return text\n",
        )
        disabled = self.workload.evaluate(disabled_root, "heldout")
        self.assertEqual(baseline["fixture_sha256"], disabled["fixture_sha256"])
        self.assertNotEqual(baseline["outputs"]["json_table"], disabled["outputs"]["json_table"])

    def test_benchmark_uses_distinct_inputs_and_fingerprints_their_outputs(self) -> None:
        measured = self.workload.cases()
        value, format_name = measured["json_typical"]
        self.assertLess(
            len(compaction.compact_text(value, format_name).encode("utf-8")),
            len(value.encode("utf-8")),
        )
        for seed in (None, bytes.fromhex("ab" * 32)):
            for name, (value, _) in measured.items():
                if name in {"paths_empty", "paths_malformed_marker"}:
                    continue
                count = 20 + 17 * (40 if name.endswith("large") else 100)
                variants = [
                    self.workload.timed_variant(name, value, index, seed)
                    for index in range(count)
                ]
                self.assertEqual(len(variants), len(set(variants)))
        benchmark = self.workload.evaluate(ROOT, "benchmark")
        samples = benchmark["samples_ns"]
        self.assertEqual(set(samples), set(measured) - {"paths_empty", "paths_malformed_marker"})
        self.assertIn("json_typical", samples)
        self.assertTrue(all(
            len(values) == 17 and all(math.isfinite(sample) and sample > 0 for sample in values)
            for values in samples.values()
        ))
        self.assertTrue(all(
            len(benchmark["outputs"][name]["timed_sha256"]) == 64 for name in samples
        ))

        mutation_root = self.candidate_source_with(
            "def compact_text(text: str, format: Format) -> str:\n",
            "def compact_text(text: str, format: Format) -> str:\n"
            '    if "src/generated/000020/" in text:\n'
            '        return text + "synthetic mutation"\n',
        )
        mutated = self.workload.evaluate(mutation_root, "benchmark")
        self.assertEqual(
            benchmark["outputs"]["paths_typical"]["sha256"],
            mutated["outputs"]["paths_typical"]["sha256"],
        )
        self.assertNotEqual(
            benchmark["outputs"]["paths_typical"]["timed_sha256"],
            mutated["outputs"]["paths_typical"]["timed_sha256"],
        )

    def test_job_seed_changes_timed_inputs_but_preserves_comparability(self) -> None:
        first_seed = bytes.fromhex("ab" * 32)
        second_seed = bytes.fromhex("cd" * 32)
        value, _ = self.workload.cases()["paths_typical"]
        first_variant = self.workload.timed_variant("paths_typical", value, 20, first_seed)
        self.assertNotIn(first_seed.hex(), first_variant)
        self.assertNotEqual(
            first_variant,
            self.workload.timed_variant("paths_typical", value, 20, second_seed),
        )
        self.assertEqual(
            first_variant,
            self.workload.timed_variant("paths_typical", value, 20, first_seed),
        )
        first = self.workload.evaluate(ROOT, "benchmark", seed=first_seed)
        repeat = self.workload.evaluate(ROOT, "benchmark", seed=first_seed)
        changed = self.workload.evaluate(ROOT, "benchmark", seed=second_seed)
        self.assertEqual(first["fixture_sha256"], repeat["fixture_sha256"])
        self.assertEqual(first["fixture_sha256"], changed["fixture_sha256"])
        self.assertEqual(first["outputs"], repeat["outputs"])
        for name in first["samples_ns"]:
            self.assertEqual(first["outputs"][name]["sha256"], changed["outputs"][name]["sha256"])
            self.assertNotEqual(
                first["outputs"][name]["timed_sha256"],
                changed["outputs"][name]["timed_sha256"],
            )

    def test_seed_stdin_contract_is_bounded_and_not_reported(self) -> None:
        seed = "ab" * 32
        command = [sys.executable, str(WORKLOAD), "--root", str(ROOT), "--action", "benchmark", "--seed-stdin"]
        completed = subprocess.run(command, input=seed + "\n", capture_output=True, text=True, check=True)
        report = json.loads(completed.stdout)
        self.assertNotIn(seed, completed.stdout)
        self.assertNotIn("seed", report)
        self.assertEqual(len(report["samples_ns"]), 10)
        invalid = subprocess.run(command, input="not-a-seed\n", capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("benchmark_invalid_seed", invalid.stderr)
        self.assertNotIn("not-a-seed", invalid.stderr)

    def test_candidate_module_cannot_patch_parent_hashes_or_clock(self) -> None:
        baseline = self.workload.evaluate(ROOT, "benchmark")
        attack_root = self.candidate_source_with(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n"
            "import hashlib\nimport time\n"
            "hashlib.sha256 = lambda *args, **kwargs: type('FakeHash', (), "
            "{'hexdigest': lambda self: '0' * 64, 'update': lambda self, value: None})()\n"
            "time.perf_counter_ns = lambda: 0\n",
        )
        attacked = self.workload.evaluate(attack_root, "benchmark")
        self.assertEqual(baseline["fixture_sha256"], attacked["fixture_sha256"])
        self.assertEqual(baseline["outputs"], attacked["outputs"])
        self.assertTrue(all(
            all(math.isfinite(sample) and sample > 0 for sample in values)
            for values in attacked["samples_ns"].values()
        ))

    @unittest.skipUnless(sys.platform == "darwin", "macOS RLIMIT_NPROC enforcement")
    def test_candidate_import_cannot_fork_or_posix_spawn(self) -> None:
        attack_root = self.candidate_source_with(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n"
            "import os\nimport sys\n"
            "try:\n    _fork_pid = os.fork()\n"
            "except OSError:\n    _fork_blocked = True\n"
            "else:\n"
            "    if _fork_pid == 0:\n        os._exit(0)\n"
            "    os.waitpid(_fork_pid, 0)\n    _fork_blocked = False\n"
            "try:\n"
            "    _spawn_pid = os.posix_spawn(sys.executable, "
            "[sys.executable, '-c', 'pass'], {})\n"
            "except OSError:\n    _spawn_blocked = True\n"
            "else:\n"
            "    os.waitpid(_spawn_pid, 0)\n    _spawn_blocked = False\n"
            "if not (_fork_blocked and _spawn_blocked):\n"
            "    raise RuntimeError('benchmark_child_process_cap_failed')\n",
        )
        self.assertEqual(
            self.workload.evaluate(ROOT, "validate")["outputs"],
            self.workload.evaluate(attack_root, "validate")["outputs"],
        )

    def test_candidate_output_and_roundtrip_limits_fail_closed(self) -> None:
        oversized_root = self.candidate_source_with(
            "def compact_text(text: str, format: Format) -> str:\n",
            "def compact_text(text: str, format: Format) -> str:\n"
            '    return "x" * 70000\n',
        )
        with self.assertRaisesRegex(RuntimeError, "benchmark_child_invalid_outputs"):
            self.workload.evaluate(oversized_root, "validate")
        slow_root = self.candidate_source_with(
            "def compact_text(text: str, format: Format) -> str:\n",
            "def compact_text(text: str, format: Format) -> str:\n"
            "    import time\n    time.sleep(2)\n",
        )
        with patch.object(self.workload, "_ROUNDTRIP_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(RuntimeError, "benchmark_child_timeout"):
                self.workload.evaluate(slow_root, "validate")


if __name__ == "__main__":
    unittest.main()
