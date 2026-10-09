"""Pure mocked safety checks; the installed-SDK receipt is a separate real run."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import ai_work_sdk_qualification as qualification
from tools.ai_work_sdk_qualification import LoopbackBoundary, QualificationCaseFailure, QualificationFailure, checked_case


class SDKQualificationTests(unittest.TestCase):
    def test_metadata_read_count_is_bounded_and_separate_from_provider_calls(self):
        state = {"state": "active", "observations": [], "attempts": [{"state": "succeeded", "response_succeeded": True}]}
        receipt = {"response_confirmed_by_gateway": True, "endpoint_completion_verified": True}
        for count in (None, 0, 5, True, "2"):
            with self.subTest(count=count), self.assertRaises(QualificationFailure):
                checked_case("openai-chat", True, {**receipt, "gateway_confirmation_reads": count}, state, 1)
        passed = checked_case("openai-chat", True, {**receipt, "gateway_confirmation_reads": 4}, state, 1)
        self.assertEqual(4, passed["gateway_confirmation_reads"])
        self.assertEqual(1, passed["provider_fixture_calls"])

    def test_failure_diagnosis_has_only_case_and_class_without_sdk_text(self):
        failure = QualificationCaseFailure("openai-chat", True, "ValueError\nprivate-sdk-body")
        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            with patch.object(qualification, "execute", side_effect=failure), redirect_stdout(captured):
                self.assertEqual(1, qualification.main(["--output", str(output)]))
            self.assertFalse(output.exists())
        diagnosis = json.loads(captured.getvalue())
        self.assertEqual("openai-chat", diagnosis["api"])
        self.assertTrue(diagnosis["stream"])
        self.assertEqual("Exception", diagnosis["sdk_failure_type"])
        self.assertNotIn("private-sdk-body", captured.getvalue())

    def test_nonlocal_dns_and_unowned_ports_are_refused_before_network(self):
        lookup, connect = Mock(), Mock()
        boundary = LoopbackBoundary(6111, 6222)
        with patch.object(socket, "getaddrinfo", lookup), patch.object(socket.socket, "connect", connect):
            with boundary.enforce():
                for host, port in (("provider.example", 6111), ("127.0.0.1", 6333)):
                    with self.subTest(host=host), self.assertRaisesRegex(QualificationFailure, "^non_fixture_network_refused$"):
                        socket.getaddrinfo(host, port)
                with self.assertRaisesRegex(QualificationFailure, "^non_fixture_network_refused$"):
                    socket.socket.connect(object(), ("203.0.113.1", 6222))
            lookup.assert_not_called()
            connect.assert_not_called()
        self.assertEqual(3, boundary.refused)

    def test_allowed_socket_is_real_passthrough_and_guards_restore_on_failure(self):
        connect, lookup = Mock(return_value="actual-result"), Mock(return_value=[])
        boundary = LoopbackBoundary(6111, 6222)
        with patch.object(socket.socket, "connect", connect), patch.object(socket, "getaddrinfo", lookup), \
                patch.dict(os.environ, {"HTTP_PROXY": "http://private-proxy.invalid", "NO_PROXY": "original"}):
            with self.assertRaisesRegex(RuntimeError, "^fixture_failure$"):
                with boundary.enforce():
                    self.assertNotIn("HTTP_PROXY", os.environ)
                    self.assertEqual("127.0.0.1", os.environ["NO_PROXY"])
                    self.assertEqual("actual-result", socket.socket.connect(object(), ("127.0.0.1", 6111)))
                    self.assertEqual([], socket.getaddrinfo("127.0.0.1", 6222))
                    raise RuntimeError("fixture_failure")
            self.assertIs(connect, socket.socket.connect)
            self.assertIs(lookup, socket.getaddrinfo)
            self.assertEqual("http://private-proxy.invalid", os.environ["HTTP_PROXY"])
            self.assertEqual("original", os.environ["NO_PROXY"])
        self.assertEqual({"gateway": 1, "provider": 0}, boundary.connections)
        connect.assert_called_once()
        lookup.assert_called_once_with("127.0.0.1", 6222)

    def test_successful_sdk_return_alone_cannot_supply_a_passing_case(self):
        receipt = {"response_confirmed_by_gateway": True, "endpoint_completion_verified": True}
        state = {"state": "active", "observations": [], "attempts": [{"state": "succeeded", "response_succeeded": False}]}
        with self.assertRaisesRegex(QualificationFailure, "^sdk_case_not_confirmed$"):
            checked_case("openai-responses", False, receipt, state, 1)

    def test_outcomes_duplicates_and_missing_provider_call_cannot_pass(self):
        attempt = {"state": "succeeded", "response_succeeded": True}
        for state, provider_calls in (
            ({"state": "completed", "observations": [], "attempts": [attempt]}, 1),
            ({"state": "active", "observations": [{"status": "completed"}], "attempts": [attempt]}, 1),
            ({"state": "active", "observations": [], "attempts": [attempt, attempt]}, 1),
            ({"state": "active", "observations": [], "attempts": [attempt]}, 0),
        ):
            with self.subTest(state=state, provider_calls=provider_calls), self.assertRaises(QualificationFailure):
                checked_case("openai-responses", False, {"response_confirmed_by_gateway": True, "endpoint_completion_verified": True}, state, provider_calls)


if __name__ == "__main__":
    unittest.main()
