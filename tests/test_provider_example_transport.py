"""No network: regression checks for connection reuse, deadlines, and cleanup."""
import unittest
from unittest import mock
from urllib.request import Request

from hormuz.work_client import WorkClientError
from tools import provider_example_transport as transport


def response(status=200, body=b'{}'):
    result = mock.MagicMock()
    result.__enter__.return_value = result
    result.status = status
    result.getheaders.return_value = [("Content-Type", "application/json")]
    result.read.return_value = body
    return result


class TransportTests(unittest.TestCase):
    def test_serial_calls_share_connection_and_close_on_exit(self):
        with mock.patch.object(transport.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = [response(), response()]
            with transport.LoopbackTransport(9876) as client:
                client.request("GET", "/v1/work/state")
                client.request("GET", "/v1/work/state")
            connection.assert_called_once_with("127.0.0.1", 9876, timeout=transport.CONNECT_TIMEOUT)
            self.assertEqual(connection.return_value.request.call_count, 2)
            self.assertEqual(connection.return_value.sock.settimeout.call_count, 2)
            connection.return_value.close.assert_called_once()

    def test_failure_closes_without_retry(self):
        with mock.patch.object(transport.http.client, "HTTPConnection") as connection:
            connection.return_value.request.side_effect = TimeoutError("private detail")
            with self.assertRaisesRegex(WorkClientError, "example_transport_timeout"):
                transport.LoopbackTransport(9876).request("POST", "/v1/responses")
            connection.return_value.request.assert_called_once()
            connection.return_value.close.assert_called_once()

    def test_redirect_is_refused_and_error_body_is_not_exposed(self):
        with mock.patch.object(transport.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = [response(302), response(429, b'{"error":{"code":"private secret body"}}')]
            with transport.LoopbackTransport(9876) as client:
                request = Request(client.endpoint + "/v1/work/state")
                with self.assertRaisesRegex(WorkClientError, "gateway_redirect_refused"):
                    client.open(request, timeout=30)
                with self.assertRaisesRegex(WorkClientError, "gateway_request_failed"):
                    client.open(request, timeout=30)
                with self.assertRaisesRegex(WorkClientError, "invalid_gateway_url"):
                    client.open(Request("https://external.invalid/v1/work/state"), timeout=30)
            self.assertEqual(connection.return_value.request.call_count, 2)

    def test_response_limit_closes_connection(self):
        with mock.patch.object(transport.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value = response(body=b'x' * (transport.MAX_RESPONSE_BYTES + 1))
            with self.assertRaisesRegex(WorkClientError, "gateway_response_too_large"):
                transport.LoopbackTransport(9876).request("GET", "/v1/work/state")
            connection.return_value.close.assert_called_once()
