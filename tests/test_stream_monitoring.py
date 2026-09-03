import unittest

import app


class PumpPortalMessageTests(unittest.TestCase):
    def test_detects_funding_rejection(self):
        self.assertTrue(
            app.is_pumpportal_error_message(
                "subscribeAccountTrade is only available when the "
                "API key wallet is funded with at least 0.02 SOL"
            )
        )

    def test_detects_rate_limit(self):
        self.assertTrue(
            app.is_pumpportal_error_message(
                "Rate limit exceeded"
            )
        )

    def test_accepts_subscription_confirmation(self):
        self.assertFalse(
            app.is_pumpportal_error_message(
                "Successfully subscribed"
            )
        )

    def test_accepts_empty_message(self):
        self.assertFalse(
            app.is_pumpportal_error_message(None)
        )


class StreamStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_webhook = app.DISCORD_ALERT_WEBHOOK_URL
        app.DISCORD_ALERT_WEBHOOK_URL = ""

    def tearDown(self):
        app.DISCORD_ALERT_WEBHOOK_URL = self.original_webhook
        app.STREAM_CONNECTED = False
        app.STREAM_LAST_ERROR = ""
        app.STREAM_ALERT_ACTIVE = False
        app.STREAM_FAILURE_STARTED_TS = 0.0

    async def test_problem_and_recovery_update_health_state(self):
        await app.mark_stream_problem(
            "funded with at least 0.02 SOL",
            immediate=True,
        )

        self.assertFalse(app.STREAM_CONNECTED)
        self.assertIn("0.02 SOL", app.STREAM_LAST_ERROR)

        await app.mark_stream_recovered()

        self.assertTrue(app.STREAM_CONNECTED)
        self.assertEqual(app.STREAM_LAST_ERROR, "")


if __name__ == "__main__":
    unittest.main()
