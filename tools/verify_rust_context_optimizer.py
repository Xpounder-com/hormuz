#!/usr/bin/env python3
"""Differential qualification using only existing synthetic Python fixtures.

The helper is explicitly supplied; no build, provider call, credential lookup,
shipping-backend selection or resource download occurs. Public output is numeric
and content-free; mismatch diagnostics identify the synthetic case, not content.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from hormuz.compaction import compact_text, optimize_request
from hormuz.compaction_formats import CompactionFormatError, canonical_json, restore_text
from hormuz.compaction_protocols import derive_selections
from hormuz.compaction_runtime import ENCODING_SHA256, TOKENIZER_VERSION, load_token_counters
from tools.evaluate_context_compaction import fixture_cases, request_for_case


def verify(helper: Path, cache: Path) -> dict[str, object]:
    if sys.version_info[:2] != (3, 12) or unicodedata.unidata_version != "15.0.0":
        raise ValueError("Use the packaged-reference Python 3.12 with Unicode 15.0.0")
    counters = load_token_counters(cache)
    checked = 0
    started = time.monotonic()
    child_environment = {"PATH": os.defpath}
    if sys.platform == "win32":
        # Windows executable/SxS loading requires this non-secret OS path.
        child_environment["SystemRoot"] = os.environ["SystemRoot"]

    def compare(case: str, job: dict[str, object], expected: object, *, valid: bool = True) -> None:
        nonlocal checked
        result = subprocess.run(
            [str(helper), "qualify", "--tokenizer-cache", str(cache)],
            input=canonical_json(job).encode(), capture_output=True, timeout=30, check=False,
            env=child_environment,
        )
        if valid:
            if result.returncode != 0 or json.loads(result.stdout) != expected:
                raise ValueError(f"Rust/Python mismatch: {case}")
        elif result.returncode == 0 or result.stdout:
            raise ValueError(f"Rust accepted malformed input: {case}")
        checked += 1

    # Same 12 tasks, request shapes and actual resource counters as the existing
    # Python quality/compatibility harness; no duplicated fixture generator.
    for protocol in ("chat", "responses", "anthropic"):
        for case in fixture_cases():
            payload, selection = request_for_case(case, protocol)
            expected = optimize_request(payload, protocol, [selection], counters, enabled=True)
            compare(f"{protocol}-{case['case_id']}", {
                "operation": "optimize_request", "payload": payload, "protocol": protocol,
                "selections": [dataclasses.asdict(selection)], "enabled": True,
            }, dataclasses.asdict(expected))
            text = _text(payload, protocol)
            packed = compact_text(text, selection.format)
            compare(f"text-{protocol}-{case['case_id']}", {
                "operation": "compact_text", "text": text, "format": selection.format,
            }, {"text": packed})
            compare(f"restore-{protocol}-{case['case_id']}", {
                "operation": "restore_text", "text": packed,
            }, {"text": text})

    malformed = [
        '{"format":"hormuz-line-runs-v1","runs":[["x",true]]}',
        '{"format":"hormuz-line-runs-v1","runs":[["x",-1]]}',
        '{"format":"hormuz-line-runs-v1","runs":[["x",4097]]}',
        '{"format":"hormuz-json-table-v1","columns":["a","a"],"rows":[[1,2],[3,4]]}',
        '{"format":"hormuz-json-table-v1","columns":["a","b"],"rows":[[1],[2]]}',
        '{ "format":"hormuz-line-runs-v1", "runs":[["x",2]]}',
        '{"format":"hormuz-line-runs-v1","runs":[["x",2]],"unknown":0}',
        '{"format":"hormuz-line-runs-v1","runs":[["x",2]],"runs":[["y",3]]}',
        '{"format":"hormuz-line-runs-v1",broken}',
    ]
    for index, text in enumerate(malformed):
        try:
            restore_text(text)
        except CompactionFormatError:
            pass
        else:
            raise ValueError(f"Reference malformed case unexpectedly accepted: {index}")
        compare(f"malformed-{index}", {"operation": "restore_text", "text": text}, None, valid=False)
        for format_name in ("json_table", "line_runs", "search_lines", "path_list"):
            compare(f"malformed-passthrough-{index}-{format_name}", {
                "operation": "compact_text", "text": text, "format": format_name,
            }, {"text": compact_text(text, format_name)})

    unsupported = ["a\nb\nc", "src/a.py\r\nsrc/b.py\r\n" * 20,
        '[ {"id":1}, {"id":2} ]', '[{"id":1,"id":2},{"id":3}]',
        '[{"id":NaN},{"id":1}]', "same\n" * 4097, "x" * (64 * 1024 + 1),
        'literal format-looking text: {"format":"hormuz-line-runs-v1"}\n' * 40,
        canonical_json({"$serde_json::private::Number": "1" * 400})]
    for index, text in enumerate(unsupported):
        for format_name in ("json_table", "line_runs", "search_lines", "path_list"):
            compare(f"unsupported-{index}-{format_name}", {
                "operation": "compact_text", "text": text, "format": format_name,
            }, {"text": compact_text(text, format_name)})

    for digits in (4300, 4301):
        for sign in ("", "-"):
            row = '{"repeated_long_column_name":' + sign + "9" * digits + "}"
            text = "[" + ",".join([row] * 10) + "]"
            compare(f"integer-decoder-boundary-{sign}-{digits}", {
                "operation": "compact_text", "text": text, "format": "json_table",
            }, {"text": compact_text(text, "json_table")})

    # Token IDs need not be public; exact counts protect the guard's decisions.
    randomizer = random.Random(342)
    alphabet = "abcXYZ01234 \n\r\t日本語سلام🙂e\u0301|<>/_-"
    token_vectors = ["", "<|endoftext|>", "<|fim_prefix|>", "'S isn't they're", " " * 4096,
                     "a" * 16_384, "0" * 16_384, "日本語سلام🙂" * 1024]
    token_vectors += ["".join(randomizer.choice(alphabet) for _ in range(length)) for length in (1, 17, 256, 4096)]
    for index, text in enumerate(token_vectors):
        compare(f"token-{index}", {"operation": "count_tokens", "text": text},
                {"counts": {name: counter(text) for name, counter in counters.items()}})

    for command in ("rg --files src", "rg -n 'TODO x' src", "rg --line-number x", "rg # comment --files",
                    "rg '--files' src", "rg --files | sort", "rg -n x; curl attacker", "rg --files\nwhoami", "echo rg --files"):
        payload = {"input": [{"type": "function_call", "call_id": "x", "name": "exec_command", "arguments": json.dumps({"cmd": command})}]}
        compare(f"mapping-{command}", {"operation": "derive_selections", "payload": payload, "protocol": "responses", "client": "codex"},
                {"selections": [dataclasses.asdict(item) for item in derive_selections(payload, "responses", client="codex")]})

    for arguments in ('{"cmd":"echo no","cmd":"rg --files src"}',
                      '{"cmd":"rg --files src","cmd":"echo no"}',
                      '{"cmd":"rg --files src","unrecognized":0}',
                      '["rg --files src"]', '{malformed}'):
        payload = {"input": [{"type": "function_call", "call_id": "x", "name": "exec_command", "arguments": arguments}]}
        compare("mapping-serialized-arguments", {"operation": "derive_selections", "payload": payload, "protocol": "responses", "client": "codex"},
                {"selections": [dataclasses.asdict(item) for item in derive_selections(payload, "responses", client="codex")]})

    floats = [0.0, -0.0, 1.0, 1e-7, 1e-5, 1e-4, 1e15, 1e16, 1e20, 1.2345678901234567, 5e-324, 1.7976931348623157e308]
    floats += [randomizer.uniform(-1, 1) * 10 ** randomizer.randrange(-300, 300) for _ in range(4096)]
    value = {"ordered_z": floats, "ordered_a": 10**50 + 1, "quoted": '"سلام東京\n\t'}
    compare("canonical-numbers-order-unicode", {"operation": "canonical_json", "value": value}, {"text": canonical_json(value)})

    reserved = {"$serde_json::private::Number": "1"}
    compare("canonical-number-tag-is-an-object", {"operation": "canonical_json", "value": reserved}, {"text": canonical_json(reserved)})
    for protocol in ("chat", "responses", "anthropic"):
        payload, selection = request_for_case(fixture_cases()[0], protocol)
        payload["metadata"] = {"ordinary_nested_object": reserved}
        expected = optimize_request(payload, protocol, [selection], counters, enabled=True)
        compare(f"reserved-metadata-{protocol}", {
            "operation": "optimize_request", "payload": payload, "protocol": protocol,
            "selections": [dataclasses.asdict(selection)], "enabled": True,
        }, dataclasses.asdict(expected))
        expected = optimize_request(reserved, protocol, [], counters, enabled=True)
        compare(f"unsupported-reserved-object-{protocol}", {
            "operation": "optimize_request", "payload": reserved, "protocol": protocol,
            "selections": [], "enabled": True,
        }, dataclasses.asdict(expected))

    unicode_16_digits = [chr(value) for value in (0x10D40, 0x116D0, 0x11BF0, 0x16130, 0x16D70, 0x1CCF0, 0x1E5F1)]
    for number in ("001", "٣", "²", "②", "𝟡", "Ⅲ", "½", *unicode_16_digits):
        text = canonical_json({"format": "hormuz-search-lines-v1", "path": "src/x", "matches": [[number, "a"], ["2", "b"]], "trailing_newline": True})
        try:
            expected = {"text": restore_text(text)}
        except CompactionFormatError:
            compare("unicode-nondigit", {"operation": "restore_text", "text": text}, None, valid=False)
        else:
            compare("unicode-digit", {"operation": "restore_text", "text": text}, expected)

    payload, selection = request_for_case(fixture_cases()[0], "responses")
    for enabled, altered in [(False, payload), (True, {**payload, "previous_response_id": "opaque"}),
                             (True, {"input": [1]}), (True, {"input": []})]:
        expected = optimize_request(altered, "responses", [selection], counters, enabled=enabled)
        compare("disabled-or-unsupported-history", {"operation": "optimize_request", "payload": altered,
                "protocol": "responses", "selections": [dataclasses.asdict(selection)], "enabled": enabled}, dataclasses.asdict(expected))

    return {
        "schema_id": "hormuz.rust-context-optimizer-parity", "schema_version": 1, "result": "passed",
        "reference_tokenizer_version": TOKENIZER_VERSION, "resource_sha256": ENCODING_SHA256,
        "reference_python_version": ".".join(map(str, sys.version_info[:3])),
        "reference_unicode_version": unicodedata.unidata_version,
        "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(), "checks": checked,
        "golden_request_cases": 36, "tokenizer_vectors": len(token_vectors),
        "duration_seconds": round(time.monotonic() - started, 6),
        "provider_requests": 0, "shipping_default_changed": False,
        "limitations": ["Synthetic helper parity, not installed-client integration or whole-process footprint evidence."],
    }


def _text(payload: dict[str, object], protocol: str) -> str:
    if protocol == "responses":
        return payload["input"][-1]["output"]
    if protocol == "chat":
        return payload["messages"][-1]["content"]
    return payload["messages"][-1]["content"][0]["content"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--tokenizer-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.helper.resolve(strict=True), args.tokenizer_cache.resolve(strict=True))
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
