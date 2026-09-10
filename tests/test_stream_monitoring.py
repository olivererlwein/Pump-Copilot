import tempfile
import time
import unittest
from pathlib import Path
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


class ShadowReviewAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        self.original_webhook = app.DISCORD_ALERT_WEBHOOK_URL
        app.DB = Path(self.temp_dir.name) / "shadow-alert.db"
        app.DISCORD_ALERT_WEBHOOK_URL = "https://discord.test/webhook"

    def tearDown(self):
        app.DB = self.original_db
        app.DISCORD_ALERT_WEBHOOK_URL = self.original_webhook
        self.temp_dir.cleanup()

    async def test_sends_shadow_review_alert_once(self):
        comparison = {
            "completed": 100,
            "incumbent_metrics": {
                "precision": 0.7,
                "recall": 0.5,
            },
            "challenger_metrics": {
                "precision": 0.8,
                "recall": 0.6,
            },
            "incumbent_only_correct": 5,
            "challenger_only_correct": 9,
        }
        stats = {
            "challenger_model_version": "candidate-v1",
            "comparison": comparison,
            "promotion_assessment": {
                "minimum_completed": 100,
                "ready_for_review": True,
                "leader": "challenger",
                "blockers": [],
            },
        }

        with patch.object(
            app,
            "get_shadow_stats",
            return_value=stats,
        ), patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(return_value=True),
        ) as send_alert:
            self.assertTrue(await app.maybe_send_shadow_review_alert())
            self.assertFalse(await app.maybe_send_shadow_review_alert())

        self.assertEqual(send_alert.await_count, 1)


class WatchedWalletSilenceTests(unittest.IsolatedAsyncioTestCase):
    """Una wallet que deja de entregar con el stream sano debe ser visible."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "wallet-silence.db"
        app.migrate_database()
        app.WATCHED_WALLET_ALERTS.clear()

    def tearDown(self):
        app.DB = self.original_db
        app.WATCHED_WALLET_ALERTS.clear()
        self.temp_dir.cleanup()

    def record_activity(self, wallet, trader, last_event_ts):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO watched_wallet_activity(
                wallet, trader, last_event_ts, events
            )
            VALUES(?,?,?,1)
            ON CONFLICT(wallet) DO UPDATE SET
                last_event_ts = excluded.last_event_ts
            """,
            (wallet, trader, last_event_ts),
        )
        conn.commit()
        conn.close()

    async def test_silent_wallet_is_reported_while_stream_is_healthy(self):
        watched = {"activo": "wallet-activo", "callado": "wallet-callado"}
        now = time.time()

        self.record_activity("wallet-activo", "activo", now - 60)
        self.record_activity(
            "wallet-callado",
            "callado",
            now - (app.WATCHED_WALLET_SILENCE_SECONDS + 3600),
        )

        with patch.object(app, "WATCHED", watched), patch.object(
            app, "STREAM_CONNECTED", True
        ), patch.object(
            app, "DISCORD_ALERT_WEBHOOK_URL", "https://example.invalid/hook"
        ), patch.object(
            app, "send_discord_alert", new_callable=AsyncMock
        ) as send_alert:
            silent = await app.check_watched_wallet_silence()

        self.assertEqual([item["trader"] for item in silent], ["callado"])
        self.assertEqual(send_alert.await_count, 1)

    async def test_wallet_never_seen_counts_as_silent(self):
        with patch.object(
            app, "WATCHED", {"fantasma": "wallet-fantasma"}
        ), patch.object(app, "STREAM_CONNECTED", True), patch.object(
            app, "DISCORD_ALERT_WEBHOOK_URL", ""
        ):
            silent = await app.check_watched_wallet_silence()

        self.assertEqual(len(silent), 1)
        self.assertTrue(silent[0]["never_seen"])
        self.assertIsNone(silent[0]["last_event_ts"])

    async def test_alert_is_sent_once_and_then_on_recovery(self):
        watched = {"intermitente": "wallet-intermitente"}
        stale = time.time() - (app.WATCHED_WALLET_SILENCE_SECONDS + 60)
        self.record_activity("wallet-intermitente", "intermitente", stale)

        with patch.object(app, "WATCHED", watched), patch.object(
            app, "STREAM_CONNECTED", True
        ), patch.object(
            app, "DISCORD_ALERT_WEBHOOK_URL", "https://example.invalid/hook"
        ), patch.object(
            app, "send_discord_alert", new_callable=AsyncMock
        ) as send_alert:
            await app.check_watched_wallet_silence()
            await app.check_watched_wallet_silence()

            self.assertEqual(send_alert.await_count, 1)

            self.record_activity(
                "wallet-intermitente",
                "intermitente",
                time.time(),
            )
            await app.check_watched_wallet_silence()

            self.assertEqual(send_alert.await_count, 2)
            self.assertIn(
                "volvieron a entregar",
                send_alert.await_args_list[1].args[0],
            )

    async def test_no_wallet_alerts_while_the_stream_is_down(self):
        # Con el stream caído la alerta correcta es la del stream, no una
        # por cada wallet.
        with patch.object(
            app, "WATCHED", {"alguien": "wallet-alguien"}
        ), patch.object(app, "STREAM_CONNECTED", False), patch.object(
            app, "send_discord_alert", new_callable=AsyncMock
        ) as send_alert:
            silent = await app.check_watched_wallet_silence()

        self.assertEqual(silent, [])
        send_alert.assert_not_awaited()

    def test_save_trade_records_wallet_delivery(self):
        with patch.object(app, "WATCHED", {"alguien": "wallet-alguien"}):
            app.save_trade(
                "alguien",
                "wallet-alguien",
                {
                    "txType": "buy",
                    "mint": "DEMO-WALLET-ACTIVITY",
                    "solAmount": 1.0,
                    "marketCapSol": 100.0,
                    "signature": "sig-wallet-activity",
                },
                source="live",
            )

            activity = app.get_watched_wallet_activity()

        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0]["trader"], "alguien")
        self.assertEqual(activity[0]["events"], 1)
        self.assertFalse(activity[0]["silent"])


if __name__ == "__main__":
    unittest.main()
