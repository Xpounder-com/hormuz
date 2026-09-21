"""Exercise the trusted local reference fixtures without candidate execution."""

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
SOURCE = ROOT / "hormuz/compaction.py"


class CodeOptimizerWorkloadTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("hormuz_optimizer_workload", WORKLOAD)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workload)
        self.workload = workload

    def test_local_cli_accepts_own_checkout_and_rejects_foreign_root(self) -> None:
        own = subprocess.run(
            [sys.executable, str(WORKLOAD), "--root", ".", "--action", "validate"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        report = json.loads(own.stdout)
        self.assertEqual(report["source"], str(ROOT / "hormuz/compaction.py"))
        self.assertEqual(report["outputs"], self.workload.evaluate(ROOT, "validate")["outputs"])

        with tempfile.TemporaryDirectory() as temporary:
            foreign = Path(temporary) / "candidate"
            package = foreign / "hormuz"
            package.mkdir(parents=True)
            for name in ("compaction.py", "compaction_formats.py"):
                (package / name).write_bytes((ROOT / "hormuz" / name).read_bytes())
            command = [
                sys.executable, str(WORKLOAD), "--root", str(foreign),
                "--action", "validate",
            ]
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("benchmark_foreign_root", rejected.stderr)
            self.assertNotIn(str(foreign), rejected.stderr)
            with self.assertRaisesRegex(RuntimeError, "benchmark_foreign_root"):
                self.workload.evaluate(foreign, "validate")

    def test_local_cli_rejects_symlink_alias_and_child_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            alias = Path(temporary) / "checkout-alias"
            alias.symlink_to(ROOT, target_is_directory=True)
            ancestor_alias = Path(temporary) / "parent-alias"
            ancestor_alias.symlink_to(ROOT.parent, target_is_directory=True)
            for supplied in (alias, ancestor_alias / ROOT.name):
                with self.subTest(supplied=supplied):
                    rejected = subprocess.run(
                        [sys.executable, str(WORKLOAD), "--root", str(supplied),
                         "--action", "validate"],
                        capture_output=True, text=True,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("benchmark_foreign_root", rejected.stderr)
                    self.assertNotIn(str(supplied), rejected.stderr)
                    with self.assertRaisesRegex(RuntimeError, "benchmark_foreign_root"):
                        self.workload.evaluate(supplied, "validate")

        no_child = subprocess.run(
            [sys.executable, str(WORKLOAD), "--root", str(ROOT),
             "--action", "validate", "--child"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(no_child.returncode, 0)
        self.assertIn("unrecognized arguments", no_child.stderr)

    def test_local_loader_rejects_source_ancestor_alias_inside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True) / "checkout"
            foreign = root / "vendor" / "foreign" / "hormuz"
            foreign.mkdir(parents=True)
            for name in ("compaction.py", "compaction_formats.py"):
                (foreign / name).write_text('raise RuntimeError("foreign_source_executed")\n')
            (root / "hormuz").symlink_to(foreign, target_is_directory=True)
            with patch.object(self.workload, "_own_root", return_value=root):
                with self.assertRaisesRegex(RuntimeError, "benchmark_source_alias"):
                    self.workload.evaluate(root, "validate")

    def test_local_loader_rejects_junction_like_root_and_source_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True) / "checkout"
            package = root / "hormuz"
            package.mkdir(parents=True)
            for name in ("compaction.py", "compaction_formats.py"):
                (package / name).write_text('raise RuntimeError("foreign_source_executed")\n')
            for aliased, error in (
                (root, "benchmark_foreign_root"),
                (package, "benchmark_source_alias"),
            ):
                with self.subTest(aliased=aliased):
                    with patch.object(
                        Path, "is_junction", lambda path: path == aliased, create=True,
                    ):
                        with patch.object(self.workload, "_own_root", return_value=root):
                            with self.assertRaisesRegex(RuntimeError, error):
                                self.workload.evaluate(root, "validate")

    def test_local_loader_rejects_resolved_source_alias_without_junction_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True) / "checkout"
            package = root / "hormuz"
            package.mkdir(parents=True)
            source = package / "compaction.py"
            source.write_text('raise RuntimeError("foreign_source_executed")\n')
            foreign = root / "vendor" / "foreign" / "hormuz" / "compaction.py"
            foreign.parent.mkdir(parents=True)
            foreign.write_text('raise RuntimeError("foreign_source_executed")\n')
            original_resolve = Path.resolve

            def resolve_as_junction(path: Path, *args: object, **kwargs: object) -> Path:
                if path == source:
                    return foreign
                return original_resolve(path, *args, **kwargs)

            with patch.object(self.workload, "_own_root", return_value=root):
                with patch.object(self.workload, "_path_is_alias", return_value=False):
                    with patch.object(Path, "resolve", resolve_as_junction):
                        with self.assertRaisesRegex(RuntimeError, "benchmark_source_alias"):
                            self.workload._prepare_local_source("probe", "compaction")

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
        self.assertEqual(baseline["source"], str(ROOT / "hormuz/compaction.py"))
        self.assertTrue(all(len(record["sha256"]) == 64 for record in baseline["outputs"].values()))
        local_module = workload.load_local_compaction()
        self.assertEqual(Path(local_module.__file__).resolve(), ROOT / "hormuz/compaction.py")

    def test_report_keeps_validated_source_when_module_changes_file(self) -> None:
        module, validated_source = self.workload._load_local_compaction()
        self.assertEqual(validated_source, str(SOURCE))
        self.assertIsInstance(validated_source, str)
        module.__file__ = "synthetic-decoy.py"
        with patch.object(
            self.workload, "_load_local_compaction", return_value=(module, validated_source),
        ):
            self.assertEqual(self.workload.evaluate(ROOT, "validate")["source"], validated_source)
            del module.__file__
            self.assertEqual(self.workload.evaluate(ROOT, "validate")["source"], validated_source)

    def test_mixed_runs_and_framed_search_are_effective_heldout_cases(self) -> None:
        for name in ("lines_mixed", "search_framed"):
            with self.subTest(name=name):
                value, format_name = self.workload.heldout_cases()[name]
                compacted = compaction.compact_text(value, format_name)
                self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
                self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        module = self.workload.load_local_compaction()
        original_lines = module._compact_line_runs

        def disabled_mixed_lines(text: str) -> str:
            return text if len(set(text.splitlines())) > 1 else original_lines(text)

        with patch.object(module, "_compact_line_runs", side_effect=disabled_mixed_lines):
            with patch.object(self.workload, "_load_local_compaction", return_value=(module, str(SOURCE))):
                disabled = self.workload.evaluate(ROOT, "heldout")
        self.assertNotEqual(baseline["outputs"]["lines_mixed"], disabled["outputs"]["lines_mixed"])

        module = self.workload.load_local_compaction()
        original_framed = module._compact_framed_lines

        def disabled_search(text: str, *, format: str) -> str:
            return text if format == "search_lines" else original_framed(text, format=format)

        with patch.object(module, "_compact_framed_lines", side_effect=disabled_search):
            with patch.object(self.workload, "_load_local_compaction", return_value=(module, str(SOURCE))):
                disabled = self.workload.evaluate(ROOT, "heldout")
        self.assertNotEqual(baseline["outputs"]["search_framed"], disabled["outputs"]["search_framed"])

    def test_heldout_json_table_detects_disabled_compaction(self) -> None:
        value, format_name = self.workload.heldout_cases()["json_table"]
        compacted = compaction.compact_text(value, format_name)
        self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
        self.assertTrue(compaction.decode_text(compacted).recognized)
        self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        module = self.workload.load_local_compaction()
        with patch.object(module, "_compact_json_table", side_effect=lambda text: text):
            with patch.object(self.workload, "_load_local_compaction", return_value=(module, str(SOURCE))):
                disabled = self.workload.evaluate(ROOT, "heldout")
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

        module = self.workload.load_local_compaction()
        original = module.compact_text

        def mutate_one_timed_path(text: str, format_name: str) -> str:
            if "src/generated/000020/" in text:
                return text + "synthetic mutation"
            return original(text, format_name)

        with patch.object(module, "compact_text", side_effect=mutate_one_timed_path):
            with patch.object(self.workload, "_load_local_compaction", return_value=(module, str(SOURCE))):
                mutated = self.workload.evaluate(ROOT, "benchmark")
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

    def test_benchmark_without_resource_keeps_output_contract(self) -> None:
        with patch.object(self.workload, "_resource", None):
            report = self.workload.evaluate(ROOT, "benchmark")
        self.assertIsNone(report["peak_rss_bytes"])
        self.assertEqual(len(report["samples_ns"]), 10)
        self.assertEqual(sum(map(len, report["samples_ns"].values())), 170)
        self.assertTrue(all(
            len(report["outputs"][name]["timed_sha256"]) == 64
            for name in report["samples_ns"]
        ))

    def test_rss_units_are_explicit_by_platform(self) -> None:
        self.assertEqual(self.workload._rss_to_bytes(7, "darwin"), 7)
        self.assertEqual(self.workload._rss_to_bytes(7, "linux"), 7 * 1024)
        self.assertEqual(self.workload._rss_to_bytes(7, "freebsd15"), 7 * 1024)
        self.assertIsNone(self.workload._rss_to_bytes(7, "openbsd7"))

    def test_seed_stdin_contract_and_all_four_local_actions(self) -> None:
        seed = "ab" * 32
        for action in ("validate", "heldout", "profile", "benchmark"):
            for seeded in (False, True):
                command = [sys.executable, str(WORKLOAD), "--root", str(ROOT), "--action", action]
                if seeded:
                    command.append("--seed-stdin")
                completed = subprocess.run(
                    command, input=(seed + "\n") if seeded else None,
                    capture_output=True, text=True, check=True,
                )
                report = json.loads(completed.stdout)
                self.assertNotIn(seed, completed.stdout)
                self.assertNotIn("seed", report)
                self.assertEqual(len(report["outputs"]), 7 if action == "heldout" else 12)
                if action == "benchmark":
                    self.assertEqual(len(report["samples_ns"]), 10)
                    self.assertEqual(sum(map(len, report["samples_ns"].values())), 170)
                if action == "profile":
                    self.assertEqual(len(report["hotspots"]), 12)
                    self.assertTrue(all(
                        item["path"].startswith("hormuz/") for item in report["hotspots"]
                    ))
        invalid = subprocess.run(
            [sys.executable, str(WORKLOAD), "--root", str(ROOT),
             "--action", "benchmark", "--seed-stdin"],
            input="not-a-seed\n", capture_output=True, text=True,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("benchmark_invalid_seed", invalid.stderr)
        self.assertNotIn("not-a-seed", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
