import os
import unittest
from unittest.mock import patch

import requests

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from opspilot.integrations.graph.client import GraphClient, GraphError, odata_string_literal, path_segment


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="payload", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        next_item = self.responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class GraphClientTests(unittest.TestCase):
    def client(self, responses):
        return GraphClient(session=FakeSession(responses))

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_timeout_retries_safe_get_only(self, _token):
        client = self.client([requests.Timeout(), FakeResponse(payload={"id": "1"})])
        with patch("opspilot.integrations.graph.client.time.sleep"):
            result = client.request("GET", "/users/1")
        self.assertEqual(result, {"id": "1"})
        self.assertEqual(len(client.session.calls), 2)

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_post_timeout_is_not_retried(self, _token):
        client = self.client([requests.Timeout()])
        with self.assertRaisesRegex(GraphError, "timed out") as raised:
            client.request("POST", "/users", body={})
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(client.session.calls), 1)

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_429_honors_retry_after_for_get(self, _token):
        client = self.client([
            FakeResponse(status_code=429, headers={"Retry-After": "2"}),
            FakeResponse(payload={"id": "1"}),
        ])
        with patch("opspilot.integrations.graph.client.time.sleep") as sleep:
            result = client.request("GET", "/users/1")
        self.assertEqual(result["id"], "1")
        sleep.assert_called_once_with(2)

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_malformed_response_is_normalized(self, _token):
        client = self.client([FakeResponse(payload=ValueError("bad json"))])
        with self.assertRaises(GraphError) as raised:
            client.request("GET", "/users/1")
        self.assertEqual(raised.exception.code, "malformed_response")

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_http_error_keeps_safe_graph_diagnostics(self, _token):
        client = self.client([FakeResponse(
            status_code=400,
            payload={"error": {"code": "Request_BadRequest", "message": "The password does not meet the password policy requirements."}},
        )])
        with self.assertRaises(GraphError) as raised:
            client.request("POST", "/users", body={})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.provider_code, "Request_BadRequest")
        self.assertIn("password policy", str(raised.exception))

    @patch("opspilot.integrations.graph.client.get_access_token", return_value="token")
    def test_collection_follows_next_link_and_limit(self, _token):
        client = self.client([
            FakeResponse(payload={"value": [{"id": "1"}], "@odata.nextLink": "https://next"}),
            FakeResponse(payload={"value": [{"id": "2"}]}),
        ])
        self.assertEqual(client.get_collection("/users", limit=2), [{"id": "1"}, {"id": "2"}])
        first_args, first_kwargs = client.session.calls[0]
        second_args, second_kwargs = client.session.calls[1]
        self.assertEqual(first_args[1], "https://graph.microsoft.com/v1.0/users")
        self.assertEqual(second_args[1], "https://next")
        self.assertIsNone(second_kwargs["params"])

    def test_odata_and_path_encoding(self):
        self.assertEqual(odata_string_literal("O'Neil"), "'O''Neil'")
        self.assertEqual(path_segment("a/b?c"), "a%2Fb%3Fc")


if __name__ == "__main__":
    unittest.main()
