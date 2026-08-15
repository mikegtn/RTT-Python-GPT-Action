import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from rtt_app.client import RTTClient, RTTError


class FakeResponse:
    def __init__(self, data, status=200, headers=None):
        self.body = json.dumps(data).encode()
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class RTTClientTests(unittest.TestCase):
    @patch("rtt_app.client.urlopen")
    def test_location_builds_expected_request(self, urlopen):
        urlopen.return_value = FakeResponse(
            {"services": []},
            headers={"X-RateLimit-Remaining-Minute": "29"},
        )
        response = RTTClient("secret", api_version="2026-04-09").location(
            "WAT", time_window=30, detailed=True
        )
        request = urlopen.call_args.args[0]
        self.assertIn("/gb-nr/location?", request.full_url)
        self.assertIn("code=WAT", request.full_url)
        self.assertIn("timeWindow=30", request.full_url)
        self.assertIn("detailed=true", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("Version"), "2026-04-09")
        self.assertEqual(response.rate_limits["X-RateLimit-Remaining-Minute"], "29")

    @patch("rtt_app.client.urlopen")
    def test_auto_exchanges_refresh_token_after_401(self, urlopen):
        unauthorized = HTTPError(
            "https://data.rtt.io/api/info",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"message":"unauthorized"}'),
        )
        urlopen.side_effect = [
            unauthorized,
            FakeResponse({"token": "short-lived", "validUntil": "later"}),
            FakeResponse({"api_version": "2026-04-09"}),
        ]
        response = RTTClient("refresh-token").info()
        self.assertEqual(response.data["api_version"], "2026-04-09")
        final_request = urlopen.call_args.args[0]
        self.assertEqual(
            final_request.get_header("Authorization"), "Bearer short-lived"
        )

    @patch("rtt_app.client.urlopen")
    def test_api_errors_are_friendly(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://data.rtt.io/api/info",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"detail":"not entitled"}'),
        )
        with self.assertRaisesRegex(RTTError, "not entitled"):
            RTTClient("token", token_type="access").info()


if __name__ == "__main__":
    unittest.main()

