import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import app

from helius_standard_wss import (
    build_helius_standard_wss_url,
    build_logs_subscribe_request,
    build_logs_unsubscribe_request,
    decode_wss_message,
    invokes_program,
    parse_logs_notification,
    select_tracked_tokens,
    select_watched_wallets,
    subscription_confirmation,
)


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return await self.incoming.get()


class HeliusStandardWssProtocolTests(unittest.TestCase):
    def test_selects_only_requested_traders(self):
        watched = {"trader-a": "wallet-a", "trader-b": "wallet-b"}
        self.assertEqual(
            select_watched_wallets(watched, "trader-b"), ["wallet-b"]
        )
        self.assertEqual(
            select_watched_wallets(watched), ["wallet-a", "wallet-b"]
        )
        with self.assertRaises(ValueError):
            select_watched_wallets(watched, "unknown")

    def test_builds_filtered_wallet_subscription(self):
        request = build_logs_subscribe_request(7, "wallet-a")

        self.assertEqual(request["method"], "logsSubscribe")
        self.assertEqual(request["params"][0], {"mentions": ["wallet-a"]})
        self.assertEqual(request["params"][1], {"commitment": "confirmed"})

    def test_token_selection_is_bounded_and_excludes_watched_wallets(self):
        selected, omitted = select_tracked_tokens(
            ["mint-c", "mint-a", "mint-b", "mint-a", "wallet-a"],
            ["wallet-a"],
            2,
        )
        self.assertEqual(selected, ["mint-a", "mint-b"])
        self.assertEqual(omitted, 1)

    def test_builds_unsubscribe_request(self):
        self.assertEqual(
            build_logs_unsubscribe_request(8, 91),
            {
                "jsonrpc": "2.0", "id": 8,
                "method": "logsUnsubscribe", "params": [91],
            },
        )

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

    def test_error_codes_never_expose_api_keys_or_urls(self):
        error = RuntimeError(
            "failed wss://mainnet.helius-rpc.com/?api-key=secret-key"
        )
        code = app.helius_standard_wss_error_code(error)
        self.assertEqual(code, "HELIUS_STANDARD_WSS_RuntimeError")
        self.assertNotIn("secret-key", code)

        error = HTTPError(
            "https://mainnet.helius-rpc.com/?api-key=secret-key",
            429,
            "rate limited secret-key",
            {},
            None,
        )
        self.assertEqual(
            app.helius_standard_wss_error_code(error),
            "HELIUS_STANDARD_WSS_HTTP_429",
        )
        self.assertEqual(
            app.helius_standard_wss_error_code(RuntimeError(
                "HELIUS_STANDARD_WSS_SUBSCRIPTION_REJECTED:secret-key"
            )),
            "HELIUS_STANDARD_WSS_SUBSCRIPTION_REJECTED",
        )
        self.assertEqual(
            app.helius_standard_wss_error_code(RuntimeError(
                "HELIUS_STANDARD_WSS_UNKNOWN_SECRET_KEY"
            )),
            "HELIUS_STANDARD_WSS_RuntimeError",
        )

    def test_fetch_errors_distinguish_only_known_rpc_failures(self):
        for message, expected in (
            ("TRANSACTION_NOT_AVAILABLE", "HELIUS_STANDARD_WSS_TRANSACTION_NOT_AVAILABLE"),
            ("INVALID_SOLANA_TRANSACTION", "HELIUS_STANDARD_WSS_INVALID_SOLANA_TRANSACTION"),
            ("INVALID_SOLANA_RPC_RESPONSE:getTransaction", "HELIUS_STANDARD_WSS_INVALID_SOLANA_RPC_RESPONSE_getTransaction"),
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    app.helius_standard_wss_error_code(ValueError(message)),
                    expected,
                )
                self.assertEqual(
                    app.helius_standard_wss_error_code(ValueError(message + ":secret-key")),
                    "HELIUS_STANDARD_WSS_ValueError",
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

    def test_custom_transport_requires_matching_rpc_configuration(self):
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test/ws"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", ""),
            patch.object(app, "APP_TOKEN", "token"),
        ):
            with self.assertRaisesRegex(
                ValueError, "HELIUS_STANDARD_WSS_RPC_URL_REQUIRED"
            ):
                app.standard_wss_rpc_url()
            report = app.api_helius_standard_wss_stats("token")

        self.assertFalse(report["configured"])
        self.assertEqual(
            report["configuration_error"],
            "HELIUS_STANDARD_WSS_RPC_URL_REQUIRED",
        )

        with patch.object(
            app, "HELIUS_STANDARD_WSS_RPC_URL", "http://example.test/rpc"
        ):
            with self.assertRaisesRegex(
                ValueError, "HELIUS_STANDARD_WSS_RPC_URL_INVALID"
            ):
                app.standard_wss_rpc_url()

    async def test_custom_transport_fetches_from_its_own_rpc(self):
        event = self.event()
        app.record_helius_standard_wss_notification(
            event, True, 200, received_ts=1_700_000_000
        )
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test/ws"),
            patch.object(
                app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"
            ),
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": 1_700_000_000},
            ) as fetch,
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ),
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
        ):
            await app.fetch_helius_standard_wss_transaction(
                event, 1_700_000_000, {event["signature"]},
                asyncio.Semaphore(1), asyncio.Lock(), {"next_ts": 0.0},
            )
            report = app.api_helius_standard_wss_stats("token")

        fetch.assert_called_once_with(
            "https://example.test/rpc", "signature-a"
        )
        self.assertTrue(report["configured"])
        self.assertEqual(report["provider"], "custom")
        self.assertEqual(report["selected_wallets"], 1)
        self.assertIsNone(report["credit_estimate"]["observed_credits_approx"])
        self.assertFalse(report["credit_estimate"]["projection_ready"])

    def test_disabled_token_tracking_does_not_recover_token_only_backlog(self):
        observed_at = time.time() - 5
        token_only = {
            **self.event(wallet="mint-a"),
            "subject_type": "token",
        }
        mixed = {
            **self.event(wallet="mint-b"),
            "signature": "signature-b",
            "subject_type": "token",
        }
        app.record_helius_standard_wss_notification(
            token_only, True, 200, received_ts=observed_at
        )
        app.record_helius_standard_wss_notification(
            mixed, True, 200, received_ts=observed_at
        )

        self.assertEqual(
            app.pending_helius_standard_wss_transactions(
                now=observed_at + 5, include_tokens=False
            ),
            [],
        )

        wallet_event = {
            **mixed,
            "wallet": "wallet-b",
            "subject_type": "wallet",
        }
        self.assertTrue(app.record_helius_standard_wss_notification(
            wallet_event, True, 200, received_ts=observed_at + 1
        ))
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet_event, True, 200, received_ts=observed_at + 2
        ))
        self.assertEqual(
            app.pending_helius_standard_wss_transactions(
                now=observed_at + 5, include_tokens=False
            ),
            [("signature-b", observed_at)],
        )
        self.assertEqual(
            len(app.pending_helius_standard_wss_transactions(
                now=observed_at + 5, include_tokens=True
            )),
            2,
        )

    def test_old_token_only_pending_does_not_restart_fetch_on_wallet_notice(self):
        now = time.time()
        token_event = {
            **self.event(),
            "wallet": "mint-a",
            "subject_type": "token",
        }
        app.record_helius_standard_wss_notification(
            token_event, True, 200, received_ts=now - 1000
        )
        wallet_event = {
            **token_event,
            "wallet": "wallet-a",
            "subject_type": "wallet",
        }
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet_event, True, 200, received_ts=now
        ))

    def test_migration_preserves_existing_wallet_notifications(self):
        conn = app.db()
        try:
            conn.execute("DROP TABLE helius_standard_wss_notifications")
            conn.execute(
                """
                CREATE TABLE helius_standard_wss_notifications(
                    signature TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    received_ts REAL NOT NULL,
                    slot INTEGER,
                    failed INTEGER NOT NULL,
                    pump_logs INTEGER NOT NULL,
                    message_bytes INTEGER NOT NULL,
                    PRIMARY KEY(signature, wallet)
                )
                """
            )
            conn.execute(
                "INSERT INTO helius_standard_wss_notifications "
                "(signature, wallet, received_ts, failed, pump_logs, "
                "message_bytes) VALUES ('old-signature', 'wallet-a', 1, 0, 1, 200)"
            )
            conn.commit()
        finally:
            conn.close()

        app.migrate_database()

        conn = app.db()
        try:
            row = conn.execute(
                "SELECT signature, wallet, subject_type "
                "FROM helius_standard_wss_notifications"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("old-signature", "wallet-a", "wallet"))

    def test_migration_adds_unparsed_reason_without_losing_transactions(self):
        conn = app.db()
        try:
            conn.execute("DROP TABLE helius_standard_wss_transactions")
            conn.execute(
                """
                CREATE TABLE helius_standard_wss_transactions(
                    signature TEXT PRIMARY KEY,
                    first_received_ts REAL NOT NULL,
                    fetched_ts REAL,
                    block_time REAL,
                    status TEXT NOT NULL,
                    fetch_attempts INTEGER NOT NULL DEFAULT 0,
                    parsed_events INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO helius_standard_wss_transactions "
                "(signature, first_received_ts, status) "
                "VALUES ('old-signature', 1, 'unparsed')"
            )
            conn.commit()
        finally:
            conn.close()

        app.migrate_database()
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT signature, status, unparsed_reason "
                "FROM helius_standard_wss_transactions"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("old-signature", "unparsed", None))

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
        conn = app.db()
        try:
            status = conn.execute(
                "SELECT status FROM helius_standard_wss_transactions"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(status, "pending_fetch")

    def test_stats_separate_wallet_coverage_and_fetch_errors(self):
        first = self.event("wallet-a")
        app.record_helius_standard_wss_notification(
            first, True, 200, received_ts=1_700_000_000
        )
        app.record_helius_standard_wss_notification(
            self.event("wallet-b"), True, 200,
            received_ts=1_700_000_001,
        )
        app.finish_helius_standard_wss_transaction(
            "signature-a", "observed", 1, parsed_events=2,
            now=1_700_000_002,
        )
        second = {**self.event("wallet-a"), "signature": "signature-b"}
        app.record_helius_standard_wss_notification(
            second, True, 200, received_ts=1_700_000_003
        )
        app.finish_helius_standard_wss_transaction(
            "signature-b", "unparsed", 1,
            unparsed_reason="no_supported_trade_payload",
            now=1_700_000_004,
        )
        third = {**self.event("wallet-b"), "signature": "signature-c"}
        app.record_helius_standard_wss_notification(
            third, True, 200, received_ts=1_700_000_005
        )
        app.finish_helius_standard_wss_transaction(
            "signature-c", "fetch_failed", 3,
            error="HELIUS_STANDARD_WSS_HTTP_429",
            now=1_700_000_006,
        )

        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=1_700_000_007),
            patch.object(
                app, "WATCHED",
                {"trader-a": "wallet-a", "trader-b": "wallet-b"},
            ),
        ):
            report = app.api_helius_standard_wss_stats("token")

        self.assertEqual(report["last_24h"]["transactions_selected"], 3)
        self.assertEqual(report["last_24h"]["fetch_errors"], [
            {
                "status": "fetch_failed",
                "code": "HELIUS_STANDARD_WSS_HTTP_429",
                "count": 1,
            },
        ])
        wallets = {
            row["trader"]: row for row in report["last_24h"]["wallets"]
        }
        self.assertEqual(
            (
                wallets["trader-a"]["notifications"],
                wallets["trader-a"]["notifications_with_parsed_transaction"],
                wallets["trader-a"]["unparsed_transactions"],
                wallets["trader-a"]["failed_transactions"],
            ),
            (2, 1, 1, 0),
        )
        self.assertEqual(
            (
                wallets["trader-b"]["notifications"],
                wallets["trader-b"]["notifications_with_parsed_transaction"],
                wallets["trader-b"]["unparsed_transactions"],
                wallets["trader-b"]["failed_transactions"],
            ),
            (2, 1, 0, 1),
        )

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

    async def test_unparsed_receipt_records_reason_without_inbox_write(self):
        event = self.event()
        app.record_helius_standard_wss_notification(
            event, True, 200, received_ts=1_700_000_000
        )
        receipt = {
            "blockTime": 1_699_999_999,
            "transaction": {
                "signatures": ["signature-a"],
                "message": {"accountKeys": [
                    {"pubkey": "wallet-a", "signer": True}
                ]},
            },
            "meta": {"err": None, "logMessages": []},
        }
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(app, "fetch_confirmed_transaction", return_value=receipt),
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 0},
            ),
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=1_700_000_001),
        ):
            await app.fetch_helius_standard_wss_transaction(
                event, 1_700_000_000, {event["signature"]},
                asyncio.Semaphore(1), asyncio.Lock(), {"next_ts": 0.0},
            )
            report = app.api_helius_standard_wss_stats("token")

        self.assertEqual(
            report["last_24h"]["unparsed_reasons"],
            {"no_supported_trade_payload": 1},
        )
        conn = app.db()
        try:
            inbox_count = conn.execute(
                "SELECT COUNT(*) FROM market_event_inbox"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(inbox_count, 0)

    async def test_diagnostic_failure_does_not_change_unparsed_status(self):
        event = self.event()
        app.record_helius_standard_wss_notification(
            event, True, 200, received_ts=1_700_000_000
        )
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": 1_699_999_999},
            ),
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 0},
            ),
            patch.object(
                app, "diagnose_unparsed_pump_receipt",
                side_effect=RuntimeError("diagnostic failed"),
            ),
        ):
            await app.fetch_helius_standard_wss_transaction(
                event, 1_700_000_000, {event["signature"]},
                asyncio.Semaphore(1), asyncio.Lock(), {"next_ts": 0.0},
            )
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT status, unparsed_reason "
                "FROM helius_standard_wss_transactions"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("unparsed", "diagnostic_unavailable"))

    async def test_apply_fetch_keeps_webhook_health_separate(self):
        event = self.event()
        app.record_helius_standard_wss_notification(
            event, True, 200, received_ts=1_700_000_000
        )
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", True),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": 1_699_999_999},
            ),
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ) as record,
        ):
            await app.fetch_helius_standard_wss_transaction(
                event,
                1_700_000_000,
                {event["signature"]},
                asyncio.Semaphore(1),
                asyncio.Lock(),
                {"next_ts": 0.0},
            )
        record.assert_called_once_with(
            [{"blockTime": 1_699_999_999}],
            received_ts=1_700_000_000,
            persist_inbox=True,
            persist_observation=False,
        )

    def test_stats_project_credits_only_after_one_hour(self):
        app.record_helius_standard_wss_notification(
            self.event(), True, 100_000, received_ts=1_700_000_000
        )
        app.finish_helius_standard_wss_transaction(
            "signature-a", "observed", 1, parsed_events=1,
            block_time=1_699_999_999,
            now=1_700_000_001,
        )
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO trades(ts, signature, source) "
                "VALUES (?, ?, ?)",
                (1_700_000_000, "signature-a", "live"),
            )
            conn.execute(
                "INSERT INTO trades(ts, signature, source) "
                "VALUES (?, ?, ?)",
                (1_700_000_000, "signature-a", "live"),
            )
            conn.execute(
                "INSERT INTO trades(ts, signature, source) "
                "VALUES (?, ?, ?)",
                (1_700_000_000, "signature-a", "helius"),
            )
            conn.execute(
                "INSERT INTO helius_webhook_events"
                "(signature, parsed, received_ts) VALUES (?, ?, ?)",
                ("signature-a", 1, 1_700_000_002),
            )
            conn.commit()
        finally:
            conn.close()
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=1_700_003_600),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
        ):
            report = app.api_helius_standard_wss_stats("token")

        self.assertFalse(report["affects_decisions"])
        self.assertEqual(report["last_24h"]["notifications"], 1)
        self.assertEqual(report["last_24h"]["parsed_events"], 1)
        self.assertEqual(
            report["last_24h"]["signature_overlap"],
            {
                "parsed_transactions": 1,
                "trades_live": 1,
                "trades_helius": 1,
                "webhook_parsed": 1,
            },
        )
        self.assertEqual(
            report["last_24h"]["delivery_timing"],
            {
                "wss_with_block_time": 1,
                "wss_mean_seconds_after_block": 1.0,
                "webhook_overlap_with_time": 1,
                "webhook_mean_seconds_after_wss": 2.0,
            },
        )
        self.assertTrue(report["credit_estimate"]["projection_ready"])
        self.assertEqual(
            report["credit_estimate"]["observed_credits_approx"], 3.0
        )

    async def test_worker_subscribes_and_observes_without_applying(self):
        socket = FakeWebSocket()
        notification = {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 91,
                "result": {
                    "context": {"slot": 123},
                    "value": {
                        "signature": "signature-a",
                        "err": None,
                        "logs": [
                            f"Program {app.PUMP_PROGRAM_ID} invoke [1]"
                        ],
                    },
                },
            },
        }
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(
                app, "WATCHED",
                {"trader-a": "wallet-a", "trader-b": "wallet-b"},
            ),
            patch.object(app, "HELIUS_STANDARD_WSS_TRADERS", "trader-a"),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": 1_700_000_000},
            ) as fetch,
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ) as record,
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(100):
                    if socket.sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 1)
                self.assertEqual(
                    socket.sent[0]["params"][0], {"mentions": ["wallet-a"]}
                )
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": "1", "result": 91,
                }))
                await socket.incoming.put(json.dumps(notification))
                for _ in range(100):
                    conn = app.db()
                    try:
                        row = conn.execute(
                            "SELECT status, parsed_events "
                            "FROM helius_standard_wss_transactions "
                            "WHERE signature = 'signature-a'"
                        ).fetchone()
                    finally:
                        conn.close()
                    if row == ("observed", 1):
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(row, ("observed", 1))
                self.assertEqual(app.HELIUS_STANDARD_WSS_STATE["subscriptions"], 1)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

        fetch.assert_called_once()
        record.assert_called_once_with(
            [{"blockTime": 1_700_000_000}],
            received_ts=unittest.mock.ANY,
            persist_inbox=False,
            persist_observation=False,
        )

    async def test_worker_recovers_recent_interrupted_fetch(self):
        observed_at = time.time() - 5
        app.record_helius_standard_wss_notification(
            self.event(), True, 200, received_ts=observed_at
        )
        socket = FakeWebSocket()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": observed_at - 1},
            ) as fetch,
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ) as record,
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(100):
                    if socket.sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 1)
                fetch.assert_not_called()
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "result": 91,
                }))
                for _ in range(100):
                    conn = app.db()
                    try:
                        row = conn.execute(
                            "SELECT status, fetched_ts "
                            "FROM helius_standard_wss_transactions "
                            "WHERE signature = 'signature-a'"
                        ).fetchone()
                    finally:
                        conn.close()
                    if row and row[1] is not None:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(row[0], "observed")
                self.assertIsNotNone(row[1])
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

        fetch.assert_called_once()
        record.assert_called_once_with(
            [{"blockTime": observed_at - 1}],
            received_ts=observed_at,
            persist_inbox=False,
            persist_observation=False,
        )

    async def test_subscription_rejection_does_not_fetch_pending_transaction(self):
        app.record_helius_standard_wss_notification(
            self.event(), True, 200, received_ts=time.time()
        )
        socket = FakeWebSocket()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(app, "fetch_confirmed_transaction") as fetch,
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(100):
                    if socket.sent:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 1)
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "error": {"code": 429, "message": "rate limited"},
                }))
                for _ in range(100):
                    if "429" in str(
                        app.HELIUS_STANDARD_WSS_STATE["last_error"]
                    ):
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(
                    app.HELIUS_STANDARD_WSS_STATE["retry_seconds"], 300.0
                )
                fetch.assert_not_called()
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

    def test_recovery_excludes_old_or_finished_transactions(self):
        app.record_helius_standard_wss_notification(
            self.event(), True, 200, received_ts=1000
        )
        self.assertEqual(
            app.pending_helius_standard_wss_transactions(now=2000), []
        )
        self.assertEqual(
            app.pending_helius_standard_wss_transactions(now=1001),
            [("signature-a", 1000.0)],
        )
        with patch.object(app, "APP_TOKEN", "token"), patch.object(
            app.time, "time", return_value=1001
        ):
            report = app.api_helius_standard_wss_stats("token")
        self.assertEqual(
            report["last_24h"]["pending_transaction_fetches"], 1
        )
        conn = app.db()
        try:
            conn.execute(
                "UPDATE helius_standard_wss_transactions "
                "SET status = 'observed' WHERE signature = 'signature-a'"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(
            app.pending_helius_standard_wss_transactions(now=1001),
            [("signature-a", 1000.0)],
        )
        app.finish_helius_standard_wss_transaction(
            "signature-a", "observed", 1, now=1002
        )
        self.assertEqual(
            app.pending_helius_standard_wss_transactions(now=1003), []
        )

    async def test_token_subscriptions_add_and_remove_without_touching_wallet(self):
        socket = FakeWebSocket()
        subscriptions = {91: "wallet-a"}
        kinds = {91: "wallet"}
        pending = {}
        pending_kinds = {}
        pending_request_started = {}
        pending_unsubscribes = {}
        pending_unsubscribe_started = {}
        with (
            patch.object(app, "TRACKED_TOKENS", {"mint-a", "mint-b"}),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", 1),
        ):
            next_id = await app.sync_helius_standard_wss_tokens(
                socket, ["wallet-a"], subscriptions, kinds,
                pending, pending_kinds, pending_request_started,
                pending_unsubscribes, pending_unsubscribe_started, 2,
            )
        self.assertEqual(next_id, 3)
        self.assertEqual(socket.sent[0]["method"], "logsSubscribe")
        self.assertEqual(socket.sent[0]["params"][0], {"mentions": ["mint-a"]})
        self.assertEqual(pending, {2: "mint-a"})
        self.assertEqual(pending_kinds, {2: "token"})
        self.assertEqual(
            app.HELIUS_STANDARD_WSS_STATE["tracked_tokens_omitted"], 1
        )
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app, "HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED", True),
            patch.dict(app.HELIUS_STANDARD_WSS_STATE, {
                "connected": True,
                "subscriptions": 2,
                "tracked_token_subscriptions": 1,
            }),
        ):
            report = app.api_helius_standard_wss_stats("token")
        self.assertFalse(report["tracked_token_subscriptions_ready"])

        subscriptions[92] = "mint-a"
        kinds[92] = "token"
        pending.clear()
        pending_kinds.clear()
        pending_request_started.clear()
        with patch.object(app, "TRACKED_TOKENS", set()):
            next_id = await app.sync_helius_standard_wss_tokens(
                socket, ["wallet-a"], subscriptions, kinds,
                pending, pending_kinds, pending_request_started,
                pending_unsubscribes, pending_unsubscribe_started, next_id,
            )
        self.assertEqual(next_id, 4)
        self.assertEqual(socket.sent[1]["method"], "logsUnsubscribe")
        self.assertEqual(socket.sent[1]["params"], [92])
        self.assertEqual(pending_unsubscribes, {3: 92})
        self.assertEqual(subscriptions[91], "wallet-a")

    async def test_worker_records_tracked_token_separately_from_wallet(self):
        socket = FakeWebSocket()
        notification = {
            "jsonrpc": "2.0", "method": "logsNotification",
            "params": {
                "subscription": 92,
                "result": {
                    "context": {"slot": 123},
                    "value": {
                        "signature": "token-signature",
                        "err": None,
                        "logs": [f"Program {app.PUMP_PROGRAM_ID} invoke [1]"],
                    },
                },
            },
        }
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(app, "HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED", True),
            patch.object(app, "HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS", 1),
            patch.object(app, "TRACKED_TOKENS", {"mint-a"}),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(
                app, "fetch_confirmed_transaction",
                return_value={"blockTime": 1_700_000_000},
            ) as fetch,
            patch.object(
                app, "record_helius_webhook_transactions",
                return_value={"parsed_events": 1},
            ) as record,
            patch.object(app, "APP_TOKEN", "token"),
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(100):
                    if len(socket.sent) == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 2)
                self.assertEqual(socket.sent[1]["params"][0], {
                    "mentions": ["mint-a"]
                })
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "result": 91,
                }))
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "result": 92,
                }))
                await socket.incoming.put(json.dumps(notification))
                for _ in range(100):
                    conn = app.db()
                    try:
                        row = conn.execute(
                            "SELECT wallet, subject_type FROM "
                            "helius_standard_wss_notifications WHERE "
                            "signature = 'token-signature'"
                        ).fetchone()
                    finally:
                        conn.close()
                    if row:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(row, ("mint-a", "token"))
                report = app.api_helius_standard_wss_stats("token")
                self.assertEqual(report["last_24h"]["token_notifications"], 1)
                self.assertEqual(report["last_24h"]["wallets"], [])
                self.assertTrue(report["wallet_subscriptions_ready"])
                self.assertTrue(report["tracked_token_subscriptions_ready"])
                self.assertEqual(
                    report["runtime"]["tracked_token_subscriptions"], 1
                )
                for _ in range(100):
                    if record.called:
                        break
                    await asyncio.sleep(0.01)
                record.assert_called_once()
                notification["params"]["subscription"] = 91
                await socket.incoming.put(json.dumps(notification))
                for _ in range(100):
                    conn = app.db()
                    try:
                        observations = conn.execute(
                            "SELECT wallet, subject_type FROM "
                            "helius_standard_wss_notifications WHERE "
                            "signature = 'token-signature' ORDER BY wallet"
                        ).fetchall()
                    finally:
                        conn.close()
                    if len(observations) == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(observations, [
                    ("mint-a", "token"), ("wallet-a", "wallet"),
                ])
                fetch.assert_called_once()
                app.TRACKED_TOKENS.clear()
                for _ in range(200):
                    if len(socket.sent) == 3:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(socket.sent[2]["method"], "logsUnsubscribe")
                self.assertEqual(socket.sent[2]["params"], [92])
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 3, "result": True,
                }))
                for _ in range(100):
                    if app.HELIUS_STANDARD_WSS_STATE[
                        "tracked_token_subscriptions"
                    ] == 0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(
                    app.HELIUS_STANDARD_WSS_STATE["subscriptions"], 1
                )
                report = app.api_helius_standard_wss_stats("token")
                self.assertTrue(report["wallet_subscriptions_ready"])
                self.assertTrue(report["tracked_token_subscriptions_ready"])
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker


if __name__ == "__main__":
    unittest.main()
