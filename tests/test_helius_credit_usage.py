import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import app
from helius_credit_usage import HeliusCreditUsageError, fetch_helius_credit_usage


PROJECT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class FakeResponse:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self.body

    def __exit__(self, *_args):
        return False


class HeliusCreditUsageTests(unittest.TestCase):
    def test_fetch_uses_header_and_returns_billing_cycle(self):
        payload = {
            "creditsRemaining": 123,
            "prepaidCreditsRemaining": 4,
            "creditsUsed": 877,
            "subscriptionDetails": {
                "creditsLimit": 1000,
                "plan": "free",
                "billingCycle": {"start": "2026-09-01", "end": "2026-10-01"},
            },
            "usage": {"webhook": 800, "rpc": 77},
        }
        with patch("helius_credit_usage.urlopen", return_value=FakeResponse(payload)) as opened:
            result = fetch_helius_credit_usage("secret-key", PROJECT_ID)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("X-api-key"), "secret-key")
        self.assertNotIn("secret-key", request.full_url)
        self.assertEqual(result["credits_remaining"], 123)
        self.assertEqual(result["usage"]["webhook"], 800)

    def test_rejects_invalid_project_id_before_request(self):
        with patch("helius_credit_usage.urlopen") as opened:
            with self.assertRaisesRegex(HeliusCreditUsageError, "PROJECT_ID_INVALID"):
                fetch_helius_credit_usage("secret-key", "../other-project")
        opened.assert_not_called()

    def test_http_error_does_not_leak_key_or_body(self):
        error = HTTPError("https://example.test/secret", 429, "key=secret-key", {}, None)
        with patch("helius_credit_usage.urlopen", side_effect=error):
            with self.assertRaisesRegex(HeliusCreditUsageError, "HELIUS_ADMIN_HTTP_429") as raised:
                fetch_helius_credit_usage("secret-key", PROJECT_ID)
        self.assertNotIn("secret-key", str(raised.exception))

    def test_endpoint_requires_app_token_and_is_read_only(self):
        with patch.object(app, "HELIUS_API_KEY", "secret-key"), patch.object(
            app, "HELIUS_PROJECT_ID", PROJECT_ID
        ), patch.object(app, "fetch_helius_credit_usage", return_value={"credits_remaining": 12}) as fetched:
            with self.assertRaises(Exception):
                app.api_helius_credit_usage("wrong-token")
            result = app.api_helius_credit_usage(app.APP_TOKEN)
        self.assertEqual(result["credits_remaining"], 12)
        fetched.assert_called_once_with("secret-key", PROJECT_ID)

    def test_endpoint_reports_missing_configuration_without_request(self):
        with patch.object(app, "HELIUS_PROJECT_ID", ""), patch.object(
            app, "fetch_helius_credit_usage"
        ) as fetched:
            result = app.api_helius_credit_usage(app.APP_TOKEN)
        self.assertFalse(result["configured"])
        fetched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
