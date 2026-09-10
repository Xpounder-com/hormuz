"""Focused data/protocol invariants; no model calls or gateway changes."""
import copy
import os
from pathlib import Path
import unittest

from evaluate import (
    counts, dump, exact_fold, fixtures, guarded_payload, load_upstream, pack_table,
    schema_probes, token_guard, transform_payload, unpack_table,
)


class NativeBaselineTests(unittest.TestCase):
    def test_table_preserves_types_unicode_nested_values_and_order(self):
        rows = [{"id": i, "literal": "quote\"\n<|endoftext|>保留", "value": [None, False, 1, 1.0, {"title": "a  b"}]} for i in range(40)]
        original = dump(rows)
        packed = pack_table(original)
        self.assertNotEqual(original, packed)
        self.assertEqual(original, unpack_table(packed))

    def test_noncanonical_ambiguous_and_heterogeneous_json_stays_verbatim(self):
        for text in ('[{"x": 1}, {"x": 1}]', '[{"x":1,"x":2},{"x":2}]',
                     '[{"x":1},{"x":null,"y":2}]', '[NaN,NaN]', '[{},{}]',
                     '[1,2]', '{"rows":[]}', 'not json', '[{"x":1e0},{"x":1e0}]'):
            with self.subTest(text=text):
                self.assertEqual(pack_table(text), text)

    def test_small_table_is_not_inflated(self):
        text = '[{"x":1},{"x":2}]'
        self.assertEqual(text, pack_table(text))

    def test_table_requires_same_key_order(self):
        text = '[{"a":1,"b":2},{"b":2,"a":1}]'
        self.assertEqual(text, pack_table(text))


class EnvelopeTests(unittest.TestCase):
    def assert_only_expected_change(self, payload, protocol, expected):
        original = copy.deepcopy(payload)
        actual = transform_payload(payload, protocol, {"selected"}, lambda text: "compact:" + text)
        self.assertEqual(actual, expected)
        self.assertEqual(payload, original)
        self.assertEqual(transform_payload(payload, protocol, set(), lambda _: "bad"), payload)

    def test_chat_keeps_instructions_calls_and_unselected_outputs(self):
        payload = {"model": "approved", "tools": [{"function": {"description": "a  b"}}], "messages": [
            {"role": "system", "content": "Never deploy."},
            {"role": "assistant", "tool_calls": [{"id": "selected", "function": {"arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "selected", "content": "result"},
            {"role": "tool", "tool_call_id": "other", "content": "result"},
        ]}
        expected = copy.deepcopy(payload)
        expected["messages"][2]["content"] = "compact:result"
        self.assert_only_expected_change(payload, "chat", expected)

    def test_responses_keeps_call_ids_reasoning_storage_and_limits(self):
        payload = {"instructions": "Preserve all facts", "store": False, "max_output_tokens": 123,
                   "input": [{"type": "reasoning", "encrypted_content": "opaque"},
                             {"type": "function_call_output", "call_id": "selected", "output": "result"},
                             {"type": "function_call_output", "call_id": "other", "output": "result"}]}
        expected = copy.deepcopy(payload)
        expected["input"][1]["output"] = "compact:result"
        self.assert_only_expected_change(payload, "responses", expected)

    def test_anthropic_keeps_text_multimodal_and_cache_fields(self):
        payload = {"system": "Preserve all facts", "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "result"},
                {"type": "tool_result", "tool_use_id": "selected", "content": "result", "is_error": True, "cache_control": {"type": "ephemeral"}},
                {"type": "tool_result", "tool_use_id": "selected", "content": [{"type": "image", "source": {"type": "base64", "data": "opaque"}}]},
            ]}]}
        expected = copy.deepcopy(payload)
        expected["messages"][0]["content"][1]["content"] = "compact:result"
        self.assert_only_expected_change(payload, "anthropic", expected)

    def test_unknown_protocol_and_responses_string_input_pass_through(self):
        for protocol, payload in (("unknown", {"content": "result"}), ("responses", {"input": "result"})):
            self.assertEqual(transform_payload(payload, protocol, {"selected"}, lambda _: "bad"), payload)

    def test_full_envelope_guard_and_reconstruction(self):
        import tiktoken
        encoders = {n: tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")}
        payload = {"store": False, "input": [{"type": "function_call_output", "call_id": "selected",
                    "output": dump([{"id": i, "name": 'quoted"value', "active": True} for i in range(50)])}]}
        changed = guarded_payload(payload, "responses", {"selected"}, pack_table, encoders)
        self.assertNotEqual(changed, payload)
        self.assertEqual(transform_payload(changed, "responses", {"selected"}, unpack_table), payload)
        before, after = counts(dump(payload), encoders), counts(dump(changed), encoders)
        self.assertTrue(all(after[n] < before[n] for n in encoders))
        self.assertEqual(guarded_payload(payload, "responses", {"selected"}, lambda _: "expansion " * 10000, encoders), payload)


class UpstreamProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Required rather than skip: a green run must actually execute upstream probes.
        cls.modules = load_upstream(Path(os.environ["HEADROOM_SOURCE"]))
        os.environ["HEADROOM_LOSSLESS_COMPACTION"] = "1"

    def test_search_can_be_reconstructed_with_paths_and_line_numbers(self):
        folds = self.modules["folds"]
        original = dict((name, text) for name, _, text in fixtures())["search_repeated_file"]
        candidate = exact_fold(folds, original, "search")
        self.assertNotEqual(candidate, original)
        self.assertTrue(any(fn(candidate) == original for fn in (folds.search_unheading, folds.search_dir_unheading)))

    def test_log_preserves_rare_error_and_repeat_count(self):
        folds = self.modules["folds"]
        original = "OK\n" * 30 + "ERROR no approval\n" + "OK\n" * 40
        candidate = exact_fold(folds, original, "log")
        self.assertNotEqual(candidate, original)
        self.assertIn("ERROR no approval", candidate)
        self.assertEqual(folds.expand_runs(candidate), original)

    def test_nonadjacent_config_blocks_roundtrip(self):
        folds = self.modules["folds"]
        original = ("[common]\nmode = read_only\nretries = 0\n" + "[unique]\nnumber = 1\n" + "[common]\nmode = read_only\nretries = 0\n") * 10
        candidate = exact_fold(folds, original, "config")
        self.assertNotEqual(candidate, original)
        self.assertEqual(folds.expand_runs(folds.unfold_repeated_blocks(candidate)), original)

    def test_strict_guard_preserves_ansi_diff_and_literal_marker(self):
        names = {"ansi_log", "diff_with_index", "literal_marker_collision"}
        for name, kind, text in fixtures():
            if name in names:
                with self.subTest(name=name):
                    self.assertEqual(exact_fold(self.modules["folds"], text, kind), text)

    def test_schema_counterexamples_reproduce(self):
        probes = schema_probes(self.modules["schemas"])
        observed = {p["probe"]: p["preserved"] for p in probes}
        self.assertEqual(observed, {"property_named_title": True, "const_object_keys": False,
                                  "enum_object_keys": False, "default_object_keys": False,
                                  "defs_named_title": False, "instruction_spacing": False})

    def test_guard_avoids_token_inflation_and_handles_token_special_literals(self):
        import tiktoken
        encoders = {n: tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")}
        self.assertEqual(token_guard("a", "a long replacement", encoders), "a")
        self.assertEqual(token_guard("same", "same", encoders), "same")
        self.assertTrue(all(v > 0 for v in counts("<|endoftext|>", encoders).values()))
        for name, kind, text in fixtures():
            candidate = pack_table(text) if kind == "json" else exact_fold(self.modules["folds"], text, kind)
            final = token_guard(text, candidate, encoders)
            with self.subTest(name=name):
                before, after = counts(text, encoders), counts(final, encoders)
                self.assertTrue(all(after[n] <= before[n] for n in encoders))


if __name__ == "__main__":
    unittest.main()
