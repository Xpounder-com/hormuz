"""Offline, synthetic evaluation. Never imported by the Hormuz gateway.

Loads two exact upstream stdlib-only modules, not Headroom's package initializer.
No provider requests, content cache, tool discovery, or model quality claims.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HEADROOM_SHA = "e67b3c8a29443a60d6b0018fb22f525c5cd7e709"
MODULES = {
    "folds": ("headroom/transforms/lossless_compaction.py", "a1084e69edbad3a2cd9aa03bd89cab20f249f6162c076f384d2c57944bc2f0c8"),
    "schemas": ("headroom/proxy/tool_schema_compaction.py", "4f7922cb6322bfe0976bfdcbe1978544d120f565e311a0d5510d16abbb91160f"),
}


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_upstream(root):
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != HEADROOM_SHA:
        raise ValueError(f"Expected Headroom {HEADROOM_SHA}, found {revision}")
    loaded = {}
    for name, (relative, digest) in MODULES.items():
        path = root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Upstream source digest mismatch: {relative}")
        spec = importlib.util.spec_from_file_location("eval_headroom_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def pack_table(text):
    """Independent native baseline: factor identical ordered JSON object keys.

    Only canonical JSON enters this experiment; that allows exact reconstruction,
    including number spellings, without pretending formatting is irrelevant.
    This is not Headroom's SmartCrusher and does not sample/drop rows.
    """
    try:
        rows = json.loads(text, object_pairs_hook=reject_duplicates)
        if dump(rows) != text or not isinstance(rows, list) or len(rows) < 2:
            return text
        if not all(isinstance(row, dict) for row in rows):
            return text
        columns = list(rows[0])
        if not columns or any(list(row) != columns for row in rows):
            return text
        candidate = dump({"format": "hormuz-eval-table-v1", "columns": columns,
                          "rows": [[row[key] for key in columns] for row in rows]})
        return candidate if unpack_table(candidate) == text and len(candidate.encode()) < len(text.encode()) else text
    except (ValueError, TypeError, OverflowError):
        return text


def unpack_table(text):
    value = json.loads(text)
    assert value["format"] == "hormuz-eval-table-v1"
    columns = value["columns"]
    assert len(columns) == len(set(columns))
    return dump([dict(zip(columns, row, strict=True)) for row in value["rows"]])


def exact_fold(folds, text, kind):
    candidate = folds.compact_lossless(text, kind)
    if candidate == text:
        return text
    inverses = {
        "log": [folds.expand_runs], "text": [folds.expand_runs],
        "search": [folds.search_unheading, folds.search_dir_unheading],
        "paths": [folds.path_unheading],
        "config": [lambda v: folds.expand_runs(folds.unfold_repeated_blocks(v))],
    }
    # Diffs have no exact inverse; color-stripped logs fail this stricter check.
    for inverse in inverses.get(kind, []):
        try:
            if inverse(candidate) == text:
                return candidate
        except (ValueError, IndexError):
            pass
    return text


def counts(text, encoders):
    return {name: len(enc.encode(text, disallowed_special=())) for name, enc in encoders.items()}


def token_guard(original, candidate, encoders):
    before, after = counts(original, encoders), counts(candidate, encoders)
    if all(after[name] < before[name] for name in encoders):
        return candidate
    return original


def transform_payload(payload, protocol, selected, transform):
    """Fixture-only boundary adapter. Caller explicitly selects result IDs.

    It touches only string tool outputs, never tool schemas, instructions,
    assistant calls, images, or protocol fields. No automatic classification.
    """
    result = copy.deepcopy(payload)
    if protocol == "responses":
        items = result.get("input", [])
        if isinstance(items, list):
            for item in items:
                if (isinstance(item, dict) and item.get("type") == "function_call_output"
                        and item.get("call_id") in selected and isinstance(item.get("output"), str)):
                    item["output"] = transform(item["output"])
    elif protocol in {"chat", "anthropic"}:
        for message in result.get("messages", []):
            if protocol == "chat" and message.get("role") == "tool":
                if message.get("tool_call_id") in selected and isinstance(message.get("content"), str):
                    message["content"] = transform(message["content"])
            elif protocol == "anthropic" and message.get("role") == "user":
                blocks = message.get("content")
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if (isinstance(block, dict) and block.get("type") == "tool_result"
                            and block.get("tool_use_id") in selected and isinstance(block.get("content"), str)):
                        block["content"] = transform(block["content"])
    return result


def guarded_payload(payload, protocol, selected, transform, encoders):
    candidate = transform_payload(payload, protocol, selected, transform)
    # Escaping text inside a JSON request can change token economics. Guard the
    # full serialized envelope as well as the standalone content.
    if token_guard(dump(payload), dump(candidate), encoders) == dump(candidate):
        return candidate
    return copy.deepcopy(payload)


def fixtures():
    return [
        ("repeated_log_with_one_error", "log", "INFO worker heartbeat healthy\n" * 80 + "ERROR payment reconciliation failed at event 79\n" + "INFO worker heartbeat healthy\n" * 30),
        ("unique_timestamped_log", "log", "".join(f"2026-09-07T12:00:{i:02}Z INFO event={i} value={i*17}\n" for i in range(60))),
        ("search_repeated_file", "search", "".join(f"src/service/request_handler.py:{i}: check_policy(rule_{i})\n" for i in range(1, 61))),
        ("path_listing", "paths", "".join(f"src/service/generated/contract_{i}.py\n" for i in range(60))),
        ("repeated_config_stanzas", "config", "[worker]\nmode = read_only\nretries = 0\n" * 35),
        ("uniform_json_tool_results", "json", dump([{"record_id": i, "status": "ok", "allowed": True, "amount": i / 2, "note": None} for i in range(70)])),
        ("nested_uniform_json", "json", dump([{"record_id": i, "metadata": {"owner": "team-a", "tags": ["review", "internal"]}, "title": "保留原文"} for i in range(40)])),
        ("heterogeneous_json", "json", dump([{"id": 1}, {"id": 2, "critical_exception": "must preserve"}])),
        ("dense_instruction_text", "text", "Never deploy before approval. Retain all audit evidence. Do not treat an expired authorization as valid."),
        ("source_code_verbatim", "code", "def authorize(actor):\n    if actor.expired:\n        return False\n    return actor.approved\n"),
        ("diff_with_index", "diff", "diff --git a/a.py b/a.py\nindex 1234567..7654321 100644\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-allow = True\n+allow = False\n"),
        ("ansi_log", "log", "\x1b[31mERROR preserve color bytes\x1b[0m\n" * 40),
        ("literal_marker_collision", "log", "Original evidence:\n... (repeated 5 times)\n" + "same\n" * 20),
    ]


def schema_probes(schemas):
    """Counterexamples, not a comprehensive JSON Schema validator."""
    probes = [
        ("property_named_title", {"type": "object", "title": "Annotation", "properties": {"title": {"type": "string"}}, "required": ["title"]}, lambda x: "title" in x["properties"]),
        ("const_object_keys", {"type": "object", "const": {"title": "approved", "status": "ok"}}, lambda x: x["const"] == {"title": "approved", "status": "ok"}),
        ("enum_object_keys", {"enum": [{"title": "approved"}, {"title": "denied"}]}, lambda x: x["enum"] == [{"title": "approved"}, {"title": "denied"}]),
        ("default_object_keys", {"type": "object", "default": {"title": "approved"}}, lambda x: x["default"] == {"title": "approved"}),
        ("defs_named_title", {"$defs": {"title": {"type": "string"}}, "$ref": "#/$defs/title"}, lambda x: "title" in x["$defs"]),
        ("instruction_spacing", {"description": "The literal separator is two spaces: 'a  b'."}, lambda x: x["description"] == "The literal separator is two spaces: 'a  b'."),
    ]
    results = []
    for name, schema, check in probes:
        payload = {"tools": [{"type": "function", "function": {"name": "check", "parameters": schema}}]}
        changed, modified, _, _ = schemas.compact_tools(payload)
        after = changed["tools"][0]["function"]["parameters"]
        results.append({"probe": name, "preserved": check(after), "modified": modified,
                        "before": schema, "after": after})
    return results


def run(root):
    import tiktoken
    # Pin the dependency to make offline counts reproducible.
    if tiktoken.__version__ != "0.12.0":
        raise ValueError("Use tiktoken==0.12.0")
    encoders = {name: tiktoken.get_encoding(name) for name in ("cl100k_base", "o200k_base")}
    modules = load_upstream(root)
    # Make the upstream per-call kill switch deterministic; only this process.
    os.environ["HEADROOM_LOSSLESS_COMPACTION"] = "1"
    rows = []
    for name, kind, original in fixtures():
        upstream = modules["folds"].compact_lossless(original, kind)
        candidate = pack_table(original) if kind == "json" else exact_fold(modules["folds"], original, kind)
        final = token_guard(original, candidate, encoders)
        rows.append({"fixture": name, "kind": kind, "input_sha256": hashlib.sha256(original.encode()).hexdigest(),
                     "baseline_tokens": counts(original, encoders), "upstream_fold_tokens": counts(upstream, encoders),
                     "guarded_tokens": counts(final, encoders), "changed": final != original,
                     "upstream_changed": upstream != original,
                     "method": "native_table_baseline" if kind == "json" else "headroom_fold_with_strict_guard",
                     "baseline_bytes": len(original.encode()), "guarded_bytes": len(final.encode())})
    text = next(text for name, _, text in fixtures() if name == "uniform_json_tool_results")
    instruction = "Preserve all records and types. Never deploy or change authorization."
    envelopes = {
        "chat": {"model": "fixture-model", "messages": [{"role": "system", "content": instruction},
            {"role": "user", "content": "Check the records."},
            {"role": "assistant", "tool_calls": [{"id": "selected", "type": "function", "function": {"name": "records", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "selected", "content": text}]},
        "responses": {"model": "fixture-model", "store": False, "instructions": instruction,
            "input": [{"type": "message", "role": "user", "content": "Check the records."},
                      {"type": "function_call", "call_id": "selected", "name": "records", "arguments": "{}"},
                      {"type": "function_call_output", "call_id": "selected", "output": text}]},
        "anthropic": {"model": "fixture-model", "max_tokens": 100, "system": instruction,
            "messages": [{"role": "user", "content": "Check the records."},
                         {"role": "assistant", "content": [{"type": "tool_use", "id": "selected", "name": "records", "input": {}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "selected", "content": text}]}]},
    }
    request_checks = []
    for protocol, payload in envelopes.items():
        transformed = guarded_payload(payload, protocol, {"selected"}, pack_table, encoders)
        restored = transform_payload(transformed, protocol, {"selected"}, unpack_table) if transformed != payload else transformed
        assert restored == payload
        request_checks.append({"protocol": protocol, "fixture": "uniform_json_tool_results",
                               "full_json_before_tokens": counts(dump(payload), encoders),
                               "full_json_after_tokens": counts(dump(transformed), encoders),
                               "request_reconstruction_exact": restored == payload})
    return {"schema_version": 1, "headroom_commit": HEADROOM_SHA,
            "source_modules": MODULES, "tokenizer": "tiktoken==0.12.0", "python": sys.version,
            "evidence_scope": "synthetic offline data preservation and standalone content token counts; not model accuracy, provider billing, or full Headroom pipeline",
            "results": rows, "request_envelope_checks": request_checks,
            "tool_schema_probes": schema_probes(modules["schemas"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headroom-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def forbid_network(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("Evaluation is offline; prefetch tokenizer vocabularies separately")
    sys.addaudithook(forbid_network)
    evidence = run(args.headroom_source)
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(evidence['results'])} synthetic cases and {len(evidence['tool_schema_probes'])} schema probes to {args.output}")


if __name__ == "__main__":
    main()
