from __future__ import annotations

import copy
import json
import unittest

from hormuz.compaction import (
    CompactionConfigError,
    Selection,
    compact_text,
    optimize_request,
)
from hormuz.compaction_formats import (
    CompactionFormatError,
    LINE_RUNS,
    PATH_LIST,
    decode_text,
    restore_text,
)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


COUNTERS = {"characters": len, "bytes": lambda value: len(value.encode("utf-8"))}


class FormatTests(unittest.TestCase):
    def assert_compacts_exactly(self, value: str, format: str) -> str:
        candidate = compact_text(value, format)  # type: ignore[arg-type]
        self.assertNotEqual(candidate, value)
        self.assertLess(len(candidate.encode()), len(value.encode()))
        self.assertEqual(restore_text(candidate), value)
        return candidate

    def test_json_table_preserves_rows_types_unicode_and_key_order(self) -> None:
        value = dump(
            [
                {"record_id": i, "value": "保留原文", "active": i % 2 == 0, "nothing": None}
                for i in range(80)
            ]
        )
        candidate = self.assert_compacts_exactly(value, "json_table")
        self.assertIn("hormuz-json-table-v1", candidate)

    def test_json_table_rejects_noncanonical_heterogeneous_and_duplicate_keys(self) -> None:
        cases = [
            '[ {"id": 1}, {"id": 2} ]',
            '[{"id":1},{"id":2,"error":"keep"}]',
            '[{"id":1,"id":2},{"id":3}]',
            '[{"id":NaN},{"id":1}]',
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(compact_text(value, "json_table"), value)

    def test_line_runs_preserve_error_position_count_and_final_newline(self) -> None:
        value = "OK\n" * 80 + "ERROR permission denied\n" + "OK\n" * 40
        candidate = self.assert_compacts_exactly(value, "line_runs")
        self.assertIn("ERROR permission denied", candidate)

    def test_search_lines_preserve_leading_zero_colons_and_newline(self) -> None:
        value = "".join(f"src/request.py:{i:03}:value: {i}\n" for i in range(1, 90))
        candidate = self.assert_compacts_exactly(value, "search_lines")
        self.assertIn('"001"', candidate)

    def test_path_list_preserves_order_duplicates_and_final_newline(self) -> None:
        value = "".join(f"src/generated/file_{i % 20}.py\n" for i in range(100))
        self.assert_compacts_exactly(value, "path_list")

    def test_path_list_preserves_real_codex_tool_wrapper(self) -> None:
        paths = "".join(f"generated/file_{i:03}.py\n" for i in range(180))
        value = (
            "Chunk ID: 72e35e\n"
            "Wall time: 0.01806 seconds\n"
            "Process exited with code 0\n"
            "Final output:\n"
            + paths
        )
        candidate = self.assert_compacts_exactly(value, "path_list")
        parsed = json.loads(candidate)
        self.assertEqual(parsed["before"], value[: value.index("generated/")])
        self.assertEqual(parsed["after"], "\n")

    def test_search_lines_preserve_tool_wrapper_and_following_text(self) -> None:
        matches = "".join(f"src/request.py:{i}:value: {i}\n" for i in range(1, 140))
        value = "Output:\n" + matches + "Notice: results complete"
        candidate = self.assert_compacts_exactly(value, "search_lines")
        parsed = json.loads(candidate)
        self.assertEqual(parsed["before"], "Output:\n")
        self.assertEqual(parsed["after"], "\nNotice: results complete")

    def test_unsupported_text_is_unchanged(self) -> None:
        for format, value in [
            ("line_runs", "a\nb\nc"),
            ("search_lines", "a.py:1:x\nb.py:2:y\n"),
            ("path_list", "src/a.py\ntests/b.py\n"),
            ("json_table", dump([{"a": 1}, {"b": 2}])),
        ]:
            with self.subTest(format=format):
                self.assertEqual(compact_text(value, format), value)  # type: ignore[arg-type]

    def test_crlf_text_is_preserved_by_passthrough(self) -> None:
        value = "src/generated/a.py\r\nsrc/generated/b.py\r\n" * 20
        for format_name in ("path_list", "search_lines"):
            with self.subTest(format=format_name):
                self.assertEqual(compact_text(value, format_name), value)  # type: ignore[arg-type]

    def test_decoder_rejects_invalid_counts_widths_unknown_keys_and_expansion(self) -> None:
        cases = [
            {"format": LINE_RUNS, "runs": [["x", True]]},
            {"format": LINE_RUNS, "runs": [["x", -1]]},
            {"format": LINE_RUNS, "runs": [["x", 4097]]},
            {"format": "hormuz-json-table-v1", "columns": ["a", "b"], "rows": [[1], [2]]},
            {"format": LINE_RUNS, "runs": [["x", 2]], "unexpected": 1},
            {
                "format": PATH_LIST,
                "before": "",
                "prefix": "src/",
                "suffixes": ["a.py", "b.py"],
                "after": "",
            },
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(CompactionFormatError):
                    restore_text(dump(value))
        with self.assertRaises(CompactionFormatError):
            decode_text(dump({"format": LINE_RUNS, "runs": [["x" * 100, 100]]}), max_output_bytes=1000)

    def test_existing_envelope_and_literal_marker_are_not_nested(self) -> None:
        original = "same\n" * 100
        compact = compact_text(original, "line_runs")
        self.assertEqual(compact_text(compact, "line_runs"), compact)
        literal = "The literal marker hormuz-line-runs-v1 must remain text.\n" * 20
        candidate = compact_text(literal, "line_runs")
        self.assertEqual(restore_text(candidate), literal)
        self.assertEqual(compact_text(candidate, "line_runs"), candidate)


class ProtocolTests(unittest.TestCase):
    def _content(self) -> str:
        return dump([{"record_id": i, "status": "ok", "amount": i} for i in range(120)])

    def _payload(self, protocol: str) -> dict[str, object]:
        content = self._content()
        if protocol == "responses":
            return {"model": "approved", "store": False, "input": [
                {"type": "message", "role": "user", "content": "keep"},
                {"type": "function_call", "call_id": "call-1", "name": "list_records", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call-1", "output": content},
            ]}
        if protocol == "chat":
            return {"model": "approved", "messages": [
                {"role": "system", "content": "keep"},
                {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "list_records", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call-1", "content": content, "custom": True},
            ]}
        return {"model": "approved", "max_tokens": 100, "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "list_records", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": content, "is_error": False}]},
        ]}

    def test_three_protocols_change_only_selected_string_result(self) -> None:
        for protocol in ("responses", "chat", "anthropic"):
            with self.subTest(protocol=protocol):
                payload = self._payload(protocol)
                original = copy.deepcopy(payload)
                result = optimize_request(
                    payload,
                    protocol,  # type: ignore[arg-type]
                    [Selection("call-1", "json_table")],
                    COUNTERS,
                    enabled=True,
                )
                self.assertTrue(result.changed)
                self.assertEqual(result.reason, "compacted")
                self.assertEqual(result.changed_blocks, 1)
                self.assertEqual(payload, original)
                restored = optimize_request(
                    result.payload,
                    protocol,  # type: ignore[arg-type]
                    [Selection("call-1", "json_table")],
                    COUNTERS,
                    enabled=True,
                )
                self.assertFalse(restored.changed)

    def test_disabled_does_not_call_counter_and_returns_independent_payload(self) -> None:
        payload = self._payload("responses")
        result = optimize_request(
            payload,
            "responses",
            [Selection("call-1", "json_table")],
            {"bad": lambda _value: (_ for _ in ()).throw(AssertionError("called"))},
            enabled=False,
        )
        self.assertEqual(result.reason, "disabled")
        self.assertEqual(result.before_tokens, {})
        self.assertIsNot(result.payload, payload)

    def test_counter_failure_rolls_back_all_changes(self) -> None:
        payload = self._payload("responses")
        result = optimize_request(
            payload,
            "responses",
            [Selection("call-1", "json_table")],
            {"broken": lambda _value: (_ for _ in ()).throw(RuntimeError("sensitive input"))},
            enabled=True,
        )
        self.assertEqual(result.reason, "counter_unavailable")
        self.assertEqual(result.payload, payload)

    def test_duplicate_result_or_call_is_not_selected(self) -> None:
        for duplicate in ("result", "call"):
            payload = self._payload("responses")
            items = payload["input"]
            assert isinstance(items, list)
            source = items[2 if duplicate == "result" else 1]
            items.append(copy.deepcopy(source))
            result = optimize_request(
                payload, "responses", [Selection("call-1", "json_table")], COUNTERS, enabled=True
            )
            self.assertFalse(result.changed)
            self.assertEqual(result.reason, "no_eligible_result")

    def test_opaque_history_and_malformed_shapes_pass_through(self) -> None:
        opaque = {"model": "approved", "previous_response_id": "resp_1", "input": []}
        self.assertEqual(
            optimize_request(opaque, "responses", [], COUNTERS, enabled=True).reason,
            "unsupported_history",
        )
        malformed = {"model": "approved", "input": ["wrong"]}
        self.assertEqual(
            optimize_request(malformed, "responses", [], COUNTERS, enabled=True).reason,
            "unsupported_shape",
        )

    def test_selection_validation_is_closed(self) -> None:
        with self.assertRaises(CompactionConfigError):
            optimize_request({}, "responses", [Selection("x", "json_table"), Selection("x", "line_runs")], COUNTERS, enabled=True)
        with self.assertRaises(CompactionConfigError):
            optimize_request({}, "responses", [], COUNTERS, enabled=1)  # type: ignore[arg-type]

    def test_append_only_history_does_not_change_an_earlier_representation(self) -> None:
        for protocol in ("responses", "chat", "anthropic"):
            with self.subTest(protocol=protocol):
                original = self._payload(protocol)
                first = optimize_request(
                    original,
                    protocol,  # type: ignore[arg-type]
                    [Selection("call-1", "json_table")],
                    COUNTERS,
                    enabled=True,
                )
                extended = copy.deepcopy(original)
                key = "input" if protocol == "responses" else "messages"
                items = extended[key]
                assert isinstance(items, list)
                if protocol == "responses":
                    items.append({"type": "message", "role": "user", "content": "later turn"})
                    index, field = 2, "output"
                elif protocol == "chat":
                    items.append({"role": "user", "content": "later turn"})
                    index, field = 2, "content"
                else:
                    items.append({"role": "user", "content": [{"type": "text", "text": "later turn"}]})
                    index, field = 0, "content"
                second = optimize_request(
                    extended,
                    protocol,  # type: ignore[arg-type]
                    [Selection("call-1", "json_table")],
                    COUNTERS,
                    enabled=True,
                )
                first_items = first.payload[key]
                second_items = second.payload[key]
                assert isinstance(first_items, list) and isinstance(second_items, list)
                if protocol == "anthropic":
                    first_block = first_items[1]["content"][index]  # type: ignore[index]
                    second_block = second_items[1]["content"][index]  # type: ignore[index]
                    self.assertEqual(first_block[field], second_block[field])
                else:
                    self.assertEqual(first_items[index][field], second_items[index][field])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
