import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app

from helius_standard_wss import (
    build_helius_standard_wss_url,
    build_logs_subscribe_request,
    decode_wss_message,
    invokes_program,
    parse_logs_notification,
    subscription_confirmation,
)


class HeliusStandardWssProtocolTests(unittest.TestCase):
    def test_builds_filtered_wallet_subscription(self):
        request = build_logs_subscribe_request(7, "wallet-a")

        self.assertEqual(request["method"], "logsSubscribe")
        self.assertEqual(request["params"][0], {"mentions": ["wallet-a"]})
        self.assertEqual(request["params"][1], {"commitment": "confirmed"})

    def test_url_requires_key_unless_explicit(self):
        self.assertEqual(build_helius_standard_wss_url(""), "")
        self.assertIn(
            "api-key=key%20with%20spaces",
            build_helius_standard_wss_url("key with spaces"),
        )
        self.assertEqual(
            build_helius_standard_wss_url("", "wss://example.test/ws"),
            "wss://example.test/ws",
        )
        with self.assertRaises(ValueError):
            build_helius_standard_wss_url("", "https://example.test")

    def test_confirmation_binds_subscription_to_wallet(self):
        self.assertEqual(
            subscription_confirmation(
                {"jsonrpc": "2.0", "id": 3, "result": 91},
                {3: "wallet-a"},
            ),
            (91, "wallet-a"),
        )

    def test_parses_notification_with_subscription_identity(self):
        payload = {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 91,
                "result": {
                    "context": {"slot": 123},
                    "value": {
                        "signature": "signature-a",
                        "err": None,
                        "logs": ["Program pump invoke [1]"],
                    },
                },
            },
        }

        event = parse_logs_notification(payload, {91: "wallet-a"})

        self.assertEqual(event["wallet"], "wallet-a")
        self.assertEqual(event["signature"], "signature-a")
        self.assertEqual(event["slot"], 123)
        self.assertFalse(event["failed"])

    def test_ignores_unknown_or_malformed_notifications(self):
        self.assertIsNone(parse_logs_notification({}, {}))
        self.assertIsNone(parse_logs_notification({
            "method": "logsNotification",
            "params": {"subscription": 99, "result": {}},
        }, {}))

    def test_program_filter_requires_invocation_log(self):
        self.assertTrue(invokes_program(
            ["Program pump invoke [2]"],
            ("pump", "amm"),
        ))
        self.assertFalse(invokes_program(
            ["Program pump consumed 10 units"],
            ("pump", "amm"),
        ))

    def test_decode_accepts_text_and_rejects_non_object(self):
        self.assertEqual(decode_wss_message(json.dumps({"id": 1})), {"id": 1})
        with self.assertRaises(ValueError):
            decode_wss_message("[]")

    def test_retry_backoff_is_long_for_rate_limits(self):
        with patch.object(
            app, "HELIUS_STANDARD_WSS_RATE_LIMIT_RETRY_SECONDS", 300
        ):
            self.assertEqual(
                app.helius_standard_wss_retry_seconds(
                    "HTTP 429", consecutive_failures=1
                ),
                300.0,
            )

    def test_retry_backoff_grows_and_is_capped_for_other_errors(self):
        with patch.object(app, "HELIUS_STANDARD_WSS_RECONNECT_SECONDS", 3):
            self.assertEqual(
                app.helius_standard_wss_retry_seconds(
                    "connection reset", consecutive_failures=1
                ),
                3.0,
            )
            self.assertEqual(
                app.helius_standard_wss_retry_seconds(
                    "connection reset", consecutive_failures=3
                ),
                12.0,
            )
            self.assertEqual(
                app.helius_standard_wss_retry_seconds(
                    "connection reset", consecutive_failures=99
                ),
                60.0,
            )


class HeliusStandardWssPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "wss.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def event(self, wallet="wallet-a"):
        return {
            "wallet": wallet,
            "signature": "signature-a",
            "slot": 123,
            "failed": False,
            "logs": [],
        }

    def test_shared_transaction_is_fetched_once_but_attributes_both_wallets(self):
        first = app.record_helius_standard_wss_notification(
            self.event("wallet-a"), True, 200, received_ts=1_700_000_000
        )
        second = app.record_helius_standard_wss_notification(
            self.event("wallet-b"), True, 200, received_ts=1_700_000_001
        )

        self.assertTrue(first)
        self.assertFalse(second)
        conn = app.db()
        try:
            notifications = conn.execute(
                "SELECT COUNT(*) FROM helius_standard_wss_notifications"
            ).fetchone()[0]
            transactions = conn.execute(
                "SELECT COUNT(*) FROM helius_standard_wss_transactions"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(notifications, 2)
        self.assertEqual(transactions, 1)

    async def test_shadow_fetch_never_persists_to_decision_inbox(self):
        event = self.event()
        app.record_helius_standard_wss_notification(
            event, True, 200, received_ts=1_700_000_000
        )
        receipt = {"blockTime": 1_699_999_999}
        pending = {event["signature"]}
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(app, "fetch_confirmed_transaction", return_value=receipt),
            patch.object(
                app,
                "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ) as record,
        ):
            await app.fetch_helius_standard_wss_transaction(
                event,
                1_700_000_000,
                pending,
                asyncio.Semaphore(1),
                asyncio.Lock(),
                {"next_ts": 0.0},
            )

        record.assert_called_once_with(
            [receipt],
            received_ts=1_700_000_000,
            persist_inbox=False,
            persist_observation=False,
        )
        self.assertEqual(pending, set())
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT status, fetch_attempts, parsed_events "
                "FROM helius_standard_wss_transactions"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("observed", 1, 1))

    def test_stats_project_credits_only_after_one_hour(self):
        app.record_helius_standard_wss_notification(
            self.event(), True, 100_000, received_ts=1_700_000_000
        )
        app.finish_helius_standard_wss_transaction(
            "signature-a", "observed", 1, parsed_events=1,
            now=1_700_000_001,
        )
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=1_700_003_600),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
        ):
            report = app.api_helius_standard_wss_stats("token")

        self.assertFalse(report["affects_decisions"])
        self.assertEqual(report["last_24h"]["notifications"], 1)
        self.assertEqual(report["last_24h"]["parsed_events"], 1)
        self.assertTrue(report["credit_estimate"]["projection_ready"])
        self.assertEqual(
            report["credit_estimate"]["observed_credits_approx"], 3.0
        )


if __name__ == "__main__":
    unittest.main()
