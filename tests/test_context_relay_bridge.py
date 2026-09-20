"""The native relay's one-request Python bridge stays bounded and optional."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from hormuz import context_relay_bridge
from hormuz.compaction_formats import MAX_REQUEST_BYTES, restore_text


class BridgeTests(unittest.TestCase):
    def test_unsupported_route_never_probes_gateway(self) -> None:
        with mock.patch.object(context_relay_bridge, "probe_gateway_capability") as probe:
            self.assertEqual(
                context_relay_bridge.transform(b"{}", "/v1/messages", "codex", "http://127.0.0.1:9"),
                (b"{}", False),
            )
            self.assertEqual(
                context_relay_bridge.transform(b"{}", "/v1/messages/count_tokens", "claude-code", "http://127.0.0.1:9"),
                (b"{}", False),
            )
            probe.assert_not_called()

    def test_compatible_gateway_uses_existing_lossless_optimizer(self) -> None:
        paths = "".join(f"src/generated/file_{index % 20}.py\n" for index in range(160))
        body = json.dumps({"model": "approved", "input": [
            {"type": "function_call", "call_id": "records", "name": "exec_command",
             "arguments": json.dumps({"cmd": "rg --files src/generated"})},
            {"type": "function_call_output", "call_id": "records", "output": paths},
        ]}).encode()
        with mock.patch.object(context_relay_bridge, "probe_gateway_capability", return_value=True), \
                mock.patch("hormuz.client_relay.load_token_counters", return_value={
                    "cl100k_base": len, "o200k_base": len,
                }):
            changed, modified = context_relay_bridge.transform(
                body, "/v1/responses", "codex", "http://127.0.0.1:9"
            )
        self.assertTrue(modified)
        self.assertNotEqual(changed, body)
        self.assertEqual(restore_text(json.loads(changed)["input"][1]["output"]), paths)

    def test_subprocess_protocol_fails_open_to_exact_request_without_gateway_support(self) -> None:
        command = [
            sys.executable, "-m", "hormuz.context_relay_bridge", "--client", "codex",
            "--path", "/v1/responses",
        ]
        original = b'{ "input" : [] }\n'
        gateway = b"http://127.0.0.1:9"
        frame = len(gateway).to_bytes(2, "big") + gateway
        result = subprocess.run(command, input=frame + original, capture_output=True, timeout=10, check=False)
        self.assertEqual((result.returncode, result.stdout), (0, b"\x00"))
        oversized = subprocess.run(
            command, input=frame + b"x" * (MAX_REQUEST_BYTES + 1), capture_output=True,
            timeout=10, check=False,
        )
        self.assertEqual((oversized.returncode, oversized.stdout), (2, b""))
        truncated = subprocess.run(
            command, input=b"\x00\x14short", capture_output=True, timeout=10, check=False,
        )
        self.assertEqual((truncated.returncode, truncated.stdout), (2, b""))


if __name__ == "__main__":
    unittest.main()
