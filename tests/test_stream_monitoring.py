import unittest
from unittest.mock import AsyncMock, patch

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

    async def test_inactivity_alert_is_sent_once_after_threshold(self):
        app.DISCORD_ALERT_WEBHOOK_URL = "https://discord.test/webhook"

        with patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(),
        ) as send_alert, patch.object(
            app.time,
            "time",
            side_effect=[1000.0, 1181.0, 1362.0],
        ):
            await app.mark_stream_problem("STREAM_INACTIVITY_TIMEOUT")
            await app.mark_stream_problem("STREAM_INACTIVITY_TIMEOUT")
            await app.mark_stream_problem("STREAM_INACTIVITY_TIMEOUT")

        self.assertFalse(app.STREAM_CONNECTED)
        self.assertTrue(app.STREAM_ALERT_ACTIVE)
        self.assertEqual(send_alert.await_count, 1)


class PumpPortalBalanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_threshold = app.PUMPPORTAL_LOW_BALANCE_SOL
        app.PUMPPORTAL_LOW_BALANCE_SOL = 0.025
        app.PUMPPORTAL_BALANCE_ALERT_ACTIVE = False
        app.PUMPPORTAL_WALLET_BALANCE_SOL = None
        app.PUMPPORTAL_BALANCE_LAST_ERROR = ""

    def tearDown(self):
        app.PUMPPORTAL_LOW_BALANCE_SOL = self.original_threshold
        app.PUMPPORTAL_BALANCE_ALERT_ACTIVE = False
        app.PUMPPORTAL_WALLET_BALANCE_SOL = None

    async def test_alerts_once_when_balance_becomes_low(self):
        with patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(),
        ) as send_alert:
            await app.record_pumpportal_wallet_balance(0.024)
            await app.record_pumpportal_wallet_balance(0.023)

        self.assertTrue(app.PUMPPORTAL_BALANCE_ALERT_ACTIVE)
        self.assertEqual(send_alert.await_count, 1)

    async def test_sends_recovery_after_top_up(self):
        app.PUMPPORTAL_BALANCE_ALERT_ACTIVE = True

        with patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(),
        ) as send_alert:
            await app.record_pumpportal_wallet_balance(0.05)

        self.assertFalse(app.PUMPPORTAL_BALANCE_ALERT_ACTIVE)
        self.assertEqual(send_alert.await_count, 1)
        self.assertIn(
            "recovered",
            send_alert.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
