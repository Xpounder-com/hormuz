"""A deliberately closed JSON response must not advertise implicit keep alive."""
from http import HTTPStatus
import io
import unittest
from unittest import mock

from hormuz.server import GatewayRequestHandler


class JsonConnectionTests(unittest.TestCase):
    def handler(self, close):
        handler = GatewayRequestHandler.__new__(GatewayRequestHandler)
        handler.close_connection = close
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._send_attribution_header = mock.Mock()
        return handler

    def test_budget_and_other_closing_errors_advertise_connection_close(self):
        handler = self.handler(True)
        handler._send_json(HTTPStatus.PAYMENT_REQUIRED, {"error": {"code": "budget_exhausted"}})
        self.assertIn(mock.call("Connection", "close"), handler.send_header.call_args_list)

    def test_reusable_json_response_keeps_connection_available(self):
        handler = self.handler(False)
        handler._send_json(HTTPStatus.OK, {"status": "ok"})
        self.assertFalse(any(call.args[0] == "Connection" for call in handler.send_header.call_args_list))
