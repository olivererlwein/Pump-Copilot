import asyncio
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

import app

from helius_standard_wss import (
    PriorityFetchLimiter,
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


class PriorityFetchLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_tokens_use_full_capacity_until_wallet_waits(self):
        limiter = PriorityFetchLimiter(2)
        release_tokens = asyncio.Event()
        started = asyncio.Queue()

        async def use_slot(name, priority):
            async with limiter.slot(priority):
                await started.put(name)
                if name.startswith("token"):
                    await release_tokens.wait()

        tokens = [
            asyncio.create_task(use_slot(f"token-{index}", 2))
            for index in range(2)
        ]
        self.assertEqual(
            [await asyncio.wait_for(started.get(), 1) for _ in range(2)],
            ["token-0", "token-1"],
        )
        wallet = asyncio.create_task(use_slot("wallet", 1))
        another_token = asyncio.create_task(use_slot("token-2", 2))
        await asyncio.sleep(0)
        release_tokens.set()
        self.assertEqual(await asyncio.wait_for(started.get(), 1), "wallet")
        await asyncio.gather(*tokens, wallet, another_token)

    async def test_waiting_live_exit_and_wallet_precede_token(self):
        limiter = PriorityFetchLimiter(1)
        order = []

        async def use_slot(name, priority):
            async with limiter.slot(priority):
                order.append(name)

        async with limiter.slot(2):
            token = asyncio.create_task(use_slot("token", 2))
            wallet = asyncio.create_task(use_slot("wallet", 1))
            live = asyncio.create_task(use_slot("live", 0))
            await asyncio.sleep(0)
            self.assertEqual(limiter._waiting, [1, 1, 1])
        await asyncio.gather(token, wallet, live)
        self.assertEqual(order, ["live", "wallet", "token"])

    async def test_cancelled_wallet_waiter_does_not_block_tokens(self):
        limiter = PriorityFetchLimiter(1)
        async with limiter.slot(2):
            async def wait_for_wallet():
                async with limiter.slot(1):
                    pass

            wallet = asyncio.create_task(wait_for_wallet())
            await asyncio.sleep(0)
            wallet.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await wallet
            self.assertEqual(limiter._waiting, [0, 0, 0])
        async with asyncio.timeout(1):
            async with limiter.slot(2):
                pass


class HeliusStandardWssProtocolTests(unittest.TestCase):
    def test_reconnect_close_detail_distinguishes_ping_timeout(self):
        error = ConnectionClosedError(
            None, Close(1011, "keepalive ping timeout")
        )
        self.assertEqual(app.helius_standard_wss_close_detail(error), {
            "received_code": None,
            "sent_code": 1011,
            "ping_timeout": True,
        })
        self.assertIsNone(app.helius_standard_wss_close_detail(ValueError("x")))

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

    def test_token_selection_honors_priority_before_alphabetical_fallback(self):
        selected, omitted = select_tracked_tokens(
            ["mint-a", "mint-b", "mint-c"],
            [],
            2,
            priority_tokens=["mint-c", "mint-b"],
        )
        self.assertEqual(selected, ["mint-c", "mint-b"])
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
            ("SOLANA_RPC_ERROR_-32005:getTransaction", "HELIUS_STANDARD_WSS_SOLANA_RPC_ERROR_-32005"),
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

    async def test_unparsed_samples_are_recent_bounded_and_authenticated(self):
        now = time.time()
        for index in range(7):
            event = {
                **self.event(f"mint-{index}"),
                "signature": f"signature-{index}",
                "subject_type": "token",
            }
            app.record_helius_standard_wss_notification(
                event, True, 100, received_ts=now - index * 10,
            )
            app.finish_helius_standard_wss_transaction(
                event["signature"], "unparsed", 1,
                unparsed_reason="supported_payload_not_decoded",
                now=now - index * 10,
            )
        old = {**self.event("old-mint"), "signature": "old-signature"}
        app.record_helius_standard_wss_notification(
            old, True, 100, received_ts=now - 7200,
        )
        app.finish_helius_standard_wss_transaction(
            old["signature"], "unparsed", 1,
            unparsed_reason="supported_payload_not_decoded",
            now=now - 7200,
        )

        with patch.object(app, "APP_TOKEN", "token"):
            with self.assertRaises(Exception):
                app.api_helius_standard_wss_unparsed_samples("wrong")
            result = app.api_helius_standard_wss_unparsed_samples("token")
        self.assertEqual(len(result["samples"]), 5)
        self.assertEqual(
            [sample["signature"] for sample in result["samples"]],
            [f"signature-{index}" for index in range(5)],
        )
        self.assertEqual(result["samples"][0]["address"], "mint-0")
        self.assertEqual(result["samples"][0]["subject_type"], "token")

    async def test_fetch_priority_protects_live_exits_then_wallets(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        wallet = {**self.event("wallet-a"), "subject_type": "wallet"}
        self.assertEqual(await app.helius_standard_wss_fetch_priority(token), 2)
        self.assertEqual(await app.helius_standard_wss_fetch_priority(wallet), 1)
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO paper_positions(mint, status) "
                "VALUES('mint-a', 'open')"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(await app.helius_standard_wss_fetch_priority(token), 2)
        with patch.object(
            app, "helius_standard_wss_token_has_open_live_position",
            return_value=True,
        ):
            self.assertEqual(await app.helius_standard_wss_fetch_priority(token), 0)

    async def test_priority_reserve_keeps_wallet_and_open_position_capacity(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        self.assertTrue(app.record_helius_standard_wss_notification(
            token, True, 100, received_ts=1000,
        ))
        pending = {f"pending-{index}" for index in range(8)}
        tasks = set()
        semaphore = asyncio.Semaphore(1)
        lock = asyncio.Lock()
        rate = {"next_ts": 0.0}
        with patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10):
            await app.schedule_helius_standard_wss_fetch(
                token, 1000, pending, tasks, semaphore, lock, rate,
            )
        self.assertEqual(len(tasks), 0)
        self.assertEqual(len(pending), 8)
        conn = app.db()
        try:
            status, error = conn.execute(
                "SELECT status, last_error FROM "
                "helius_standard_wss_transactions WHERE signature = ?",
                (token["signature"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(status, "queue_full")
        self.assertEqual(error, "HELIUS_STANDARD_WSS_PRIORITY_RESERVE")

        wallet = {**self.event("wallet-a"), "signature": "wallet-sig"}
        protected = {**token, "signature": "protected-sig"}
        for event in (wallet, protected):
            self.assertTrue(app.record_helius_standard_wss_notification(
                event, True, 100, received_ts=1001,
            ))
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10),
            patch.object(
                app, "helius_standard_wss_token_has_open_position",
                return_value=True,
            ),
            patch.object(
                app, "fetch_helius_standard_wss_transaction",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            await app.schedule_helius_standard_wss_fetch(
                wallet, 1001, pending, tasks, semaphore, lock, rate,
            )
            await app.schedule_helius_standard_wss_fetch(
                protected, 1001, pending, tasks, semaphore, lock, rate,
            )
            await asyncio.gather(*tasks)
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(len(pending), 10)

        another_wallet = {
            **wallet, "signature": "wallet-after-global-limit",
        }
        self.assertTrue(app.record_helius_standard_wss_notification(
            another_wallet, True, 100, received_ts=1002,
        ))
        with patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10):
            await app.schedule_helius_standard_wss_fetch(
                another_wallet, 1002, pending, tasks,
                semaphore, lock, rate,
            )
        conn = app.db()
        try:
            status = conn.execute(
                "SELECT status FROM helius_standard_wss_transactions "
                "WHERE signature = ?",
                (another_wallet["signature"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(status, "queue_full")
        self.assertEqual(len(pending), 10)

    async def test_low_priority_token_is_admitted_below_reserve(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        self.assertTrue(app.record_helius_standard_wss_notification(
            token, True, 100, received_ts=1000,
        ))
        pending = {f"pending-{index}" for index in range(7)}
        tasks = set()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10),
            patch.object(
                app, "fetch_helius_standard_wss_transaction",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            await app.schedule_helius_standard_wss_fetch(
                token, 1000, pending, tasks, asyncio.Semaphore(1),
                asyncio.Lock(), {"next_ts": 0.0},
            )
            await asyncio.gather(*tasks)
        self.assertEqual(fetch.await_count, 1)

    async def test_rejection_records_pending_token_composition(self):
        token = {**self.event("hot-mint"), "subject_type": "token"}
        app.record_helius_standard_wss_notification(
            token, True, 100, received_ts=1000,
        )
        pending = {f"pending-{index}" for index in range(8)}
        tasks_by_signature = {
            signature: asyncio.Future() for signature in pending
        }
        token_tasks = {
            tasks_by_signature[f"pending-{index}"]: (
                f"pending-{index}", "hot-mint"
            ) for index in range(5)
        }
        token_tasks[tasks_by_signature["pending-5"]] = (
            "pending-5", "cool-mint"
        )
        with patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10):
            await app.schedule_helius_standard_wss_fetch(
                token, 1000, pending, set(), asyncio.Semaphore(1),
                asyncio.Lock(), {"next_ts": 0.0},
                pending_token_tasks=token_tasks,
            )
        snapshot = app.HELIUS_STANDARD_WSS_STATE[
            "last_queue_rejection_composition"
        ]
        self.assertEqual(snapshot["reason"], "HELIUS_STANDARD_WSS_PRIORITY_RESERVE")
        self.assertEqual(snapshot["pending_fetches"], 8)
        self.assertEqual(snapshot["pending_without_token_attribution"], 2)
        self.assertEqual(snapshot["rejected_mint_pending"], 5)
        self.assertEqual(snapshot["top_pending_token_mints"], [
            {"mint": "hot-mint", "pending": 5},
            {"mint": "cool-mint", "pending": 1},
        ])
        self.assertEqual(len(token_tasks), 6)

    async def test_pending_token_attribution_clears_when_fetch_finishes(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        pending = set()
        tasks = set()
        token_tasks = {}
        gate = asyncio.Event()

        async def delayed_fetch(*args):
            await gate.wait()

        with patch.object(app, "fetch_helius_standard_wss_transaction", delayed_fetch):
            await app.schedule_helius_standard_wss_fetch(
                token, 1000, pending, tasks, asyncio.Semaphore(1),
                asyncio.Lock(), {"next_ts": 0.0},
                pending_token_tasks=token_tasks,
            )
            self.assertEqual(list(token_tasks.values()), [
                (token["signature"], "mint-a")
            ])
            fetch_task = next(iter(tasks))
            fetch_task.cancel()
            await asyncio.gather(fetch_task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertEqual(token_tasks, {})

    def test_open_paper_position_protects_token_admission(self):
        self.assertFalse(app.helius_standard_wss_token_has_open_position(
            "mint-a"
        ))
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO paper_positions(mint, status) "
                "VALUES('mint-a', 'open')"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(app.helius_standard_wss_token_has_open_position(
            "mint-a"
        ))

    def test_position_lookup_error_keeps_token_admissible(self):
        with patch.object(
            app.sqlite3, "connect", side_effect=sqlite3.OperationalError,
        ):
            self.assertTrue(
                app.helius_standard_wss_token_has_open_position("mint-a")
            )

    def test_wallet_notice_recovers_token_queue_rejection_once(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        self.assertTrue(app.record_helius_standard_wss_notification(
            token, True, 100, received_ts=1000,
        ))
        app.finish_helius_standard_wss_transaction(
            token["signature"], "queue_full", 0, now=1001,
            error="HELIUS_STANDARD_WSS_PRIORITY_RESERVE",
        )
        wallet = {**token, "wallet": "wallet-a", "subject_type": "wallet"}
        self.assertTrue(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1002,
        ))
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1003,
        ))
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT status, fetched_ts, last_error FROM "
                "helius_standard_wss_transactions WHERE signature = ?",
                (token["signature"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("pending_fetch", None, None))

    def test_stale_wallet_notice_does_not_recover_token_rejection(self):
        token = {**self.event("mint-a"), "subject_type": "token"}
        app.record_helius_standard_wss_notification(
            token, True, 100, received_ts=1000,
        )
        app.finish_helius_standard_wss_transaction(
            token["signature"], "queue_full", 0, now=1001,
        )
        wallet = {**token, "wallet": "wallet-a", "subject_type": "wallet"}
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1901,
        ))

    def test_repeated_wallet_notice_recovers_recent_queue_rejection(self):
        wallet = self.event()
        self.assertTrue(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1000,
        ))
        app.finish_helius_standard_wss_transaction(
            wallet["signature"], "queue_full", 0, now=1001,
            error="HELIUS_STANDARD_WSS_QUEUE_FULL",
        )
        self.assertTrue(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1002,
        ))
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1003,
        ))
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT status, fetched_ts, last_error FROM "
                "helius_standard_wss_transactions WHERE signature = ?",
                (wallet["signature"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("pending_fetch", None, None))

    def test_old_repeated_wallet_notice_does_not_recover_queue_rejection(self):
        wallet = self.event()
        app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1000,
        )
        app.finish_helius_standard_wss_transaction(
            wallet["signature"], "queue_full", 0, now=1001,
        )
        self.assertFalse(app.record_helius_standard_wss_notification(
            wallet, True, 100, received_ts=1901,
        ))

    def test_token_subscription_intervals_are_persisted_conservatively(self):
        self.assertEqual(
            app.establish_helius_token_coverage_activation(now=50), 50.0
        )
        self.assertEqual(
            app.establish_helius_token_coverage_activation(now=60), 50.0
        )
        interval_id = app.start_helius_token_subscription_interval(
            "mint-a", now=100
        )
        self.assertGreater(interval_id, 0)
        self.assertEqual(
            app.heartbeat_helius_token_subscription_intervals(
                ["mint-a", "mint-missing"], now=110
            ),
            1,
        )
        self.assertEqual(
            app.close_helius_token_subscription_interval(
                "mint-a", "unsubscribe_confirmed", now=120
            ),
            1,
        )

        app.start_helius_token_subscription_interval("mint-stale", now=200)
        app.heartbeat_helius_token_subscription_intervals(
            ["mint-stale"], now=210
        )
        self.assertEqual(
            app.close_stale_helius_token_subscription_intervals(), 1
        )

        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT mint, subscribed_ts, last_confirmed_ts, "
                "unsubscribed_ts, close_reason "
                "FROM helius_standard_wss_token_intervals ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [
            ("mint-a", 100.0, 120.0, 120.0, "unsubscribe_confirmed"),
            ("mint-stale", 200.0, 210.0, 210.0, "process_restart"),
        ])

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
            unparsed_reason="wallet_not_signer",
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
        self.assertEqual(
            wallets["trader-a"][
                "notifications_with_wallet_not_signer_transaction"
            ],
            1,
        )
        self.assertEqual(
            wallets["trader-b"][
                "notifications_with_wallet_not_signer_transaction"
            ],
            0,
        )

    def test_stats_report_token_traffic_by_mint_for_each_window(self):
        now = 1_700_010_000
        events = [
            ({**self.event("mint-a"), "signature": "token-a-recent",
              "subject_type": "token"}, 100, now - 1800),
            ({**self.event("mint-a"), "signature": "token-a-old",
              "subject_type": "token"}, 200, now - 7200),
            ({**self.event("mint-b"), "signature": "token-b-recent",
              "subject_type": "token", "failed": True}, 300, now - 100),
        ]
        for event, message_bytes, received_ts in events:
            app.record_helius_standard_wss_notification(
                event, True, message_bytes, received_ts=received_ts,
            )
        app.finish_helius_standard_wss_transaction(
            "token-a-recent", "queue_full", 0,
            error="HELIUS_STANDARD_WSS_PRIORITY_RESERVE", now=now - 1799,
        )
        app.finish_helius_standard_wss_transaction(
            "token-a-old", "fetch_failed", 1, now=now - 7199,
        )

        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
        ):
            report = app.api_helius_standard_wss_stats("token")

        one_hour = {
            row["mint"]: row for row in report["last_1h"]["tokens"]
        }
        self.assertEqual(report["last_1h"]["token_notifications"], 2)
        self.assertEqual(report["last_1h"]["tokens_observed"], 2)
        self.assertEqual(report["last_1h"]["message_bytes"], 400)
        self.assertEqual(one_hour["mint-a"]["message_bytes"], 100)
        self.assertEqual(one_hour["mint-a"]["queue_full_transactions"], 1)
        self.assertEqual(one_hour["mint-a"]["priority_reserve_transactions"], 1)
        self.assertEqual(one_hour["mint-a"]["global_queue_full_transactions"], 0)
        self.assertEqual(report["last_1h"]["queue_rejection_reasons"], {
            "HELIUS_STANDARD_WSS_PRIORITY_RESERVE": 1,
        })
        self.assertEqual(one_hour["mint-a"]["fetch_failed_transactions"], 0)
        self.assertEqual(one_hour["mint-b"]["failed_notifications"], 1)
        self.assertEqual(one_hour["mint-b"]["queue_full_transactions"], 0)

        day = {
            row["mint"]: row for row in report["last_24h"]["tokens"]
        }
        self.assertEqual(report["last_24h"]["token_notifications"], 3)
        self.assertEqual(report["last_24h"]["tokens_observed"], 2)
        self.assertEqual(day["mint-a"]["notifications"], 2)
        self.assertEqual(day["mint-a"]["message_bytes"], 300)
        self.assertEqual(day["mint-a"]["queue_full_transactions"], 1)
        self.assertEqual(day["mint-a"]["fetch_failed_transactions"], 1)
        self.assertEqual(report["last_24h"]["queue_rejection_reasons"], {
            "HELIUS_STANDARD_WSS_PRIORITY_RESERVE": 1,
        })

        conn = app.db()
        try:
            for signal_ts in (now - 1900, now - 1850):
                conn.execute(
                    "INSERT INTO signal_outcomes(mint, signal_ts, "
                    "created_ts, updated_ts) VALUES(?,?,?,?)",
                    ("mint-a", signal_ts, signal_ts, signal_ts),
                )
            conn.execute(
                "INSERT INTO signal_outcomes(mint, signal_ts, "
                "created_ts, updated_ts) VALUES(?,?,?,?)",
                ("mint-c", now - 3700, now - 3700, now - 3700),
            )
            conn.commit()
        finally:
            conn.close()
        outside = {
            **self.event("mint-a"), "signature": "token-a-before-signal",
            "subject_type": "token",
        }
        app.record_helius_standard_wss_notification(
            outside, True, 100, received_ts=now - 3000,
        )
        app.finish_helius_standard_wss_transaction(
            outside["signature"], "queue_full", 0,
            error="HELIUS_STANDARD_WSS_PRIORITY_RESERVE", now=now - 2999,
        )
        older = {
            **self.event("mint-c"), "signature": "token-c-older",
            "subject_type": "token",
        }
        app.record_helius_standard_wss_notification(
            older, True, 100, received_ts=now - 3650,
        )
        app.finish_helius_standard_wss_transaction(
            older["signature"], "queue_full", 0,
            error="HELIUS_STANDARD_WSS_PRIORITY_RESERVE", now=now - 3649,
        )
        early = {
            **self.event("mint-x"), "signature": "cross-mint-before-signal",
            "subject_type": "token",
        }
        app.record_helius_standard_wss_notification(
            early, True, 100, received_ts=now - 2000,
        )
        app.finish_helius_standard_wss_transaction(
            early["signature"], "queue_full", 0,
            error="HELIUS_STANDARD_WSS_PRIORITY_RESERVE", now=now - 1999,
        )
        app.record_helius_standard_wss_notification(
            {**early, "wallet": "mint-a"}, True, 100,
            received_ts=now - 1800,
        )
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
        ):
            overlap = app.api_helius_standard_wss_stats(
                "token", include_outcome_overlap=True
            )["outcome_queue_overlap"]
        self.assertEqual(overlap["last_1h"], {
            "transactions": 1, "mints": 1,
        })
        self.assertEqual(overlap["last_24h"], {
            "transactions": 2, "mints": 2,
        })

    def test_stats_last_hour_excludes_older_queue_failures(self):
        now = 1_700_010_000
        old_event = self.event("wallet-a")
        app.record_helius_standard_wss_notification(
            old_event, True, 200, received_ts=now - 7200
        )
        app.finish_helius_standard_wss_transaction(
            "signature-a", "queue_full", 0,
            error="HELIUS_STANDARD_WSS_QUEUE_FULL", now=now - 7199
        )

        recent_event = {
            **self.event("wallet-b"),
            "signature": "signature-b",
        }
        app.record_helius_standard_wss_notification(
            recent_event, True, 200, received_ts=now - 60
        )
        app.finish_helius_standard_wss_transaction(
            "signature-b", "applied", 1, parsed_events=2,
            now=now - 59,
        )
        failed_event = {
            **self.event("wallet-b"),
            "signature": "signature-c",
            "failed": True,
        }
        app.record_helius_standard_wss_notification(
            failed_event, True, 200, received_ts=now - 30
        )
        unparsed_event = {
            **self.event("wallet-a"),
            "signature": "signature-d",
        }
        app.record_helius_standard_wss_notification(
            unparsed_event, True, 200, received_ts=now - 20
        )
        app.finish_helius_standard_wss_transaction(
            "signature-d", "unparsed", 1,
            unparsed_reason="supported_payload_not_decoded",
            now=now - 19,
        )

        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=now),
            patch.object(
                app, "WATCHED",
                {"trader-a": "wallet-a", "trader-b": "wallet-b"},
            ),
        ):
            report = app.api_helius_standard_wss_stats("token")

        self.assertEqual(report["last_24h"]["notifications"], 4)
        self.assertEqual(
            report["last_24h"]["statuses"],
            {"applied": 1, "queue_full": 1, "unparsed": 1},
        )
        self.assertEqual(report["last_24h"]["queue_rejection_reasons"], {
            "HELIUS_STANDARD_WSS_QUEUE_FULL": 1,
        })
        self.assertEqual(report["last_1h"], {
            "notifications": 3,
            "pump_log_notifications": 3,
            "failed_notifications": 1,
            "message_bytes": 600,
            "token_notifications": 0,
            "tokens_observed": 0,
            "tokens": [],
            "transactions_selected": 2,
            "rpc_fetch_attempts": 2,
            "parsed_events": 2,
            "pending_transaction_fetches": 0,
            "statuses": {"applied": 1, "unparsed": 1},
            "fetch_errors": [],
            "queue_rejection_reasons": {},
            "queue_pressure": {
                "peak_rejections_minute_ts": None,
                "peak_rejections_in_minute": 0,
                "selected_in_peak_minute": 0,
                "completed_transactions": 2,
                "mean_completion_seconds": 1.0,
                "max_completion_seconds": 1.0,
            },
            "unparsed_reasons": {"supported_payload_not_decoded": 1},
        })

    def test_queue_pressure_excludes_rejections_from_completion_lag(self):
        conn = app.db()
        try:
            conn.executemany(
                "INSERT INTO helius_standard_wss_transactions("
                "signature, first_received_ts, fetched_ts, status) "
                "VALUES(?, ?, ?, ?)",
                [
                    ("a", 120, 123, "applied"),
                    ("b", 125, 125, "queue_full"),
                    ("c", 128, 128, "queue_full"),
                    ("d", 180, 186, "observed"),
                ],
            )
            conn.commit()
            pressure = app.get_helius_standard_wss_queue_pressure(conn, 100)
            recent = app.get_helius_standard_wss_queue_pressure(conn, 180)
        finally:
            conn.close()
        self.assertEqual(pressure, {
            "peak_rejections_minute_ts": 120,
            "peak_rejections_in_minute": 2,
            "selected_in_peak_minute": 3,
            "completed_transactions": 2,
            "mean_completion_seconds": 4.5,
            "max_completion_seconds": 6.0,
        })
        self.assertEqual(recent["peak_rejections_in_minute"], 0)
        self.assertEqual(recent["completed_transactions"], 1)

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

    async def test_log_price_shadow_failure_does_not_change_fetch_status(self):
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
                return_value={"parsed_events": 1},
            ),
            patch.object(
                app, "note_helius_standard_wss_log_price_shadow",
                side_effect=RuntimeError("shadow failed"),
            ),
        ):
            await app.fetch_helius_standard_wss_transaction(
                event, 1_700_000_000, {event["signature"]},
                asyncio.Semaphore(1), asyncio.Lock(), {"next_ts": 0.0},
            )
        conn = app.db()
        try:
            status = conn.execute(
                "SELECT status FROM helius_standard_wss_transactions"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(status, "observed")

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
                "INSERT INTO trades(ts, signature, source, transport) "
                "VALUES (?, ?, ?, ?)",
                (1_700_000_000, "signature-a", "live", "live"),
            )
            conn.execute(
                "INSERT INTO trades(ts, signature, source, transport) "
                "VALUES (?, ?, ?, ?)",
                (1_700_000_000, "signature-a", "live", "live"),
            )
            # El transporte vive en su propia columna: `source` sigue
            # distinguiendo real de demo para el scoring.
            conn.execute(
                "INSERT INTO trades(ts, signature, source, transport) "
                "VALUES (?, ?, ?, ?)",
                (1_700_000_000, "signature-a", "live", "helius"),
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

    async def test_recovery_drains_more_than_one_batch_without_duplicates(self):
        observed_at = time.time() - 5
        conn = app.db()
        try:
            for index in range(61):
                signature = f"pending-{index:02d}"
                subject_type = "wallet" if index == 60 else "token"
                wallet = "wallet-a" if index == 60 else "mint-a"
                conn.execute(
                    "INSERT INTO helius_standard_wss_notifications("
                    "signature, wallet, received_ts, failed, pump_logs, "
                    "message_bytes, subject_type) VALUES(?,?,?,0,1,100,?)",
                    (signature, wallet, observed_at, subject_type),
                )
                conn.execute(
                    "INSERT INTO helius_standard_wss_transactions("
                    "signature, first_received_ts, status) "
                    "VALUES(?,?,'pending_fetch')",
                    (signature, observed_at),
                )
            conn.execute(
                "INSERT INTO helius_standard_wss_notifications("
                "signature, wallet, received_ts, failed, pump_logs, "
                "message_bytes, subject_type) VALUES(?,?,?,0,1,100,'token')",
                ("pending-60", "mint-a", observed_at),
            )
            conn.commit()
        finally:
            conn.close()

        pending = set()
        tasks = set()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 100),
            patch.object(
                app, "fetch_helius_standard_wss_transaction",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            args = (pending, tasks, asyncio.Semaphore(1),
                    asyncio.Lock(), {"next_ts": 0.0})
            self.assertEqual(
                await app.recover_helius_standard_wss_pending(*args), 50
            )
            self.assertEqual(
                await app.recover_helius_standard_wss_pending(*args), 11
            )
            await asyncio.gather(*tasks)
        self.assertEqual(fetch.await_count, 61)
        self.assertEqual(len(pending), 61)
        events = [call.args[0] for call in fetch.await_args_list]
        self.assertEqual(len({event["signature"] for event in events}), 61)
        self.assertIn(
            {"signature": "pending-60", "wallet": "wallet-a",
             "subject_type": "wallet"}, events,
        )
        self.assertIn(
            {"signature": "pending-00", "wallet": "mint-a",
             "subject_type": "token"}, events,
        )

    async def test_recovery_preserves_capacity_for_new_notifications(self):
        pending = {f"current-{index}" for index in range(8)}
        with patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10):
            recovered = await app.recover_helius_standard_wss_pending(
                pending, set(), asyncio.Semaphore(1), asyncio.Lock(),
                {"next_ts": 0.0},
            )
        self.assertEqual(recovered, 0)
        self.assertEqual(len(pending), 8)

    async def test_recovery_uses_wallet_reserve_when_token_capacity_is_full(self):
        observed_at = time.time() - 5
        wallet = self.event()
        app.record_helius_standard_wss_notification(
            wallet, True, 200, received_ts=observed_at,
        )
        pending = {f"token-{index}" for index in range(8)}
        tasks = set()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_PENDING", 10),
            patch.object(
                app, "fetch_helius_standard_wss_transaction",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            recovered = await app.recover_helius_standard_wss_pending(
                pending, tasks, asyncio.Semaphore(1), asyncio.Lock(),
                {"next_ts": 0.0},
            )
            await asyncio.gather(*tasks)
        self.assertEqual(recovered, 1)
        self.assertIn(wallet["signature"], pending)
        fetch.assert_awaited_once()
        self.assertEqual(fetch.await_args.args[0]["subject_type"], "wallet")

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
            patch.object(
                app, "_tracked_tokens_priority_snapshot",
                return_value=["mint-a", "mint-b"],
            ),
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
        send_timing = app.HELIUS_STANDARD_WSS_STATE[
            "last_token_subscribe_send"
        ]
        self.assertEqual(send_timing["mints"], ["mint-a"])
        self.assertGreaterEqual(send_timing["snapshot_seconds"], 0)
        self.assertGreaterEqual(send_timing["heartbeat_seconds"], 0)
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

    async def test_expired_outcome_token_is_unsubscribed(self):
        app.create_signal_outcome(
            signal_id=None, mint="mint-old", trader="tester",
            signal_ts=1_700_000_000, price_at_signal=1,
        )
        app.create_signal_outcome(
            signal_id=None, mint="mint-new", trader="tester",
            signal_ts=1_700_001_200, price_at_signal=1,
        )
        socket = FakeWebSocket()
        with (
            patch.object(app, "TRACKED_TOKENS", {"mint-old", "mint-new"}),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", 7),
            patch.object(app.time, "time", return_value=1_700_001_300),
        ):
            await app.sync_helius_standard_wss_tokens(
                socket, [], {91: "mint-old"}, {91: "token"},
                {}, {}, {}, {}, {}, 1,
            )
        self.assertEqual(
            [(message["method"], message["params"][0]) for message in socket.sent],
            [("logsUnsubscribe", 91), ("logsSubscribe", {"mentions": ["mint-new"]})],
        )

    async def test_open_paper_position_keeps_expired_outcome_token(self):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO paper_positions(mint, status, opened_ts) "
                "VALUES('mint-old', 'open', 1700000000)"
            )
            conn.commit()
        finally:
            conn.close()
        socket = FakeWebSocket()
        with (
            patch.object(app, "TRACKED_TOKENS", {"mint-old"}),
            patch.object(app.time, "time", return_value=1_700_001_300),
        ):
            await app.sync_helius_standard_wss_tokens(
                socket, [], {91: "mint-old"}, {91: "token"},
                {}, {}, {}, {}, {}, 1,
            )
        self.assertEqual(socket.sent, [])

    async def test_token_capacity_alerts_once_and_then_reports_recovery(self):
        with (
            patch.object(app, "DISCORD_ALERT_WEBHOOK_URL", "https://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", 7),
            patch.object(app, "HELIUS_STANDARD_WSS_CAPACITY_ALERT_ACTIVE", False),
            patch.object(
                app, "send_discord_alert", new_callable=AsyncMock,
                return_value=True,
            ) as alert,
        ):
            await app.update_helius_standard_wss_capacity_alert(7, 2)
            await app.update_helius_standard_wss_capacity_alert(7, 2)

            self.assertTrue(app.HELIUS_STANDARD_WSS_CAPACITY_ALERT_ACTIVE)
            alert.assert_awaited_once()
            self.assertIn("Omitted: 2", alert.await_args.args[0])
            self.assertIn("Configured limit: 7", alert.await_args.args[0])

            await app.update_helius_standard_wss_capacity_alert(6, 0)
            await app.update_helius_standard_wss_capacity_alert(6, 0)

            self.assertFalse(app.HELIUS_STANDARD_WSS_CAPACITY_ALERT_ACTIVE)
            self.assertEqual(alert.await_count, 2)
            self.assertIn("capacity recovered", alert.await_args.args[0])

    async def test_token_subscription_prioritizes_latest_active_outcome(self):
        older = app.create_signal_outcome(
            signal_id=None, mint="mint-a", trader="tester",
            signal_ts=1_700_000_000, price_at_signal=1,
        )
        newer = app.create_signal_outcome(
            signal_id=None, mint="mint-b", trader="tester",
            signal_ts=1_700_000_100, price_at_signal=1,
        )
        self.assertLess(older, newer)
        socket = FakeWebSocket()
        with (
            patch.object(app, "TRACKED_TOKENS", {"mint-a", "mint-b"}),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", 1),
            patch.object(app.time, "time", return_value=1_700_000_110),
        ):
            await app.sync_helius_standard_wss_tokens(
                socket, [], {}, {}, {}, {}, {}, {}, {}, 1,
            )
        self.assertEqual(
            socket.sent[0]["params"][0], {"mentions": ["mint-b"]}
        )

    async def test_token_capacity_keeps_existing_outcome_subscription(self):
        app.create_signal_outcome(
            signal_id=None, mint="mint-a", trader="tester",
            signal_ts=1_700_000_000, price_at_signal=1,
        )
        app.create_signal_outcome(
            signal_id=None, mint="mint-b", trader="tester",
            signal_ts=1_700_000_100, price_at_signal=1,
        )
        socket = FakeWebSocket()
        with (
            patch.object(app, "TRACKED_TOKENS", {"mint-a", "mint-b"}),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", 1),
            patch.object(app.time, "time", return_value=1_700_000_110),
        ):
            next_id = await app.sync_helius_standard_wss_tokens(
                socket, [], {91: "mint-a"}, {91: "token"},
                {}, {}, {}, {}, {}, 1,
            )
        self.assertEqual(next_id, 1)
        self.assertEqual(socket.sent, [])

    def test_open_paper_position_still_outranks_existing_outcome(self):
        app.create_signal_outcome(
            signal_id=None, mint="mint-a", trader="tester",
            signal_ts=1_700_000_000, price_at_signal=1,
        )
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO paper_positions(mint, status, opened_ts) "
                "VALUES('mint-b', 'open', 1700000000)"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(
            app._tracked_tokens_priority_snapshot(
                now=1_700_000_110, active_tokens={"mint-a"},
            ),
            ["mint-b", "mint-a"],
        )

    def test_open_paper_position_without_timestamp_remains_priority(self):
        app.create_signal_outcome(
            signal_id=None, mint="mint-a", trader="tester",
            signal_ts=1_700_000_000, price_at_signal=1,
        )
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO paper_positions(mint, status) "
                "VALUES('mint-b', 'open')"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(
            app._tracked_tokens_priority_snapshot(
                now=1_700_000_110, active_tokens={"mint-a"},
            ),
            ["mint-b", "mint-a"],
        )

    async def test_worker_subscribes_new_token_before_fallback_poll(self):
        socket = FakeWebSocket()
        tracked = set()
        wake = threading.Event()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED", True),
            patch.object(app, "HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS", 60),
            patch.object(app, "HELIUS_STANDARD_WSS_TOKEN_WAKE", wake),
            patch.object(app, "TRACKED_TOKENS", tracked),
            patch.object(
                app, "_tracked_tokens_priority_snapshot", return_value=["mint-new"]
            ),
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(
                app, "update_helius_standard_wss_capacity_alert",
                new_callable=AsyncMock,
            ) as capacity_alert,
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(200):
                    if capacity_alert.await_count:
                        break
                    await asyncio.sleep(0.005)
                self.assertEqual(capacity_alert.await_count, 1)
                self.assertEqual(len(socket.sent), 1)
                tracked.add("mint-new")
                wake.set()
                for _ in range(300):
                    if len(socket.sent) >= 2:
                        break
                    await asyncio.sleep(0.005)
                self.assertEqual(
                    socket.sent[1]["params"][0],
                    {"mentions": ["mint-new"]},
                )
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

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
            patch.object(
                app, "_tracked_tokens_priority_snapshot", return_value=["mint-a"]
            ),
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
            patch.object(
                app, "update_helius_standard_wss_capacity_alert",
                new_callable=AsyncMock,
            ) as capacity_alert,
            patch.dict(app.HELIUS_STANDARD_WSS_STATE, {
                "last_token_subscribe_ack": {"mint": "old-mint"},
            }),
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
                self.assertIsNone(app.HELIUS_STANDARD_WSS_STATE[
                    "last_token_subscribe_ack"
                ])
                capacity_alert.assert_any_await(1, 0)
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "result": 91,
                }))
                await socket.incoming.put(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "result": 92,
                }))
                for _ in range(100):
                    conn = app.db()
                    try:
                        interval = conn.execute(
                            "SELECT mint, unsubscribed_ts "
                            "FROM helius_standard_wss_token_intervals "
                            "WHERE mint = 'mint-a'"
                        ).fetchone()
                    finally:
                        conn.close()
                    if interval:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(interval, ("mint-a", None))
                ack_timing = app.HELIUS_STANDARD_WSS_STATE[
                    "last_token_subscribe_ack"
                ]
                self.assertEqual(ack_timing["mint"], "mint-a")
                self.assertGreaterEqual(ack_timing["delay_seconds"], 0)
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
                conn = app.db()
                try:
                    interval = conn.execute(
                        "SELECT mint, unsubscribed_ts, close_reason "
                        "FROM helius_standard_wss_token_intervals "
                        "WHERE mint = 'mint-a'"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertEqual(interval[0], "mint-a")
                self.assertIsNotNone(interval[1])
                self.assertEqual(interval[2], "unsubscribe_confirmed")
                capacity_alert.assert_any_await(0, 0)
                report = app.api_helius_standard_wss_stats("token")
                self.assertTrue(report["wallet_subscriptions_ready"])
                self.assertTrue(report["tracked_token_subscriptions_ready"])
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker


class HeliusStandardWssFloodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "wss-flood.db"
        app.migrate_database()
        self.reset_memory()

    def tearDown(self):
        self.reset_memory()
        app.DB = self.original_db
        self.temp_dir.cleanup()

    @staticmethod
    def reset_memory():
        with app.HELIUS_STANDARD_WSS_STATE_LOCK:
            app.HELIUS_STANDARD_WSS_UNSTORED.clear()
            app.HELIUS_STANDARD_WSS_UNSTORED_RECENT.clear()
            app.HELIUS_STANDARD_WSS_STATE["muted_wallets"] = {}

    @staticmethod
    def notification(subscription, signature, pump=False):
        logs = (
            [f"Program {app.PUMP_PROGRAM_ID} invoke [1]"] if pump
            else ["Program SpamProgram1111111111111111111111111111111 invoke [1]"]
        )
        return json.dumps({
            "jsonrpc": "2.0", "method": "logsNotification",
            "params": {
                "subscription": subscription,
                "result": {
                    "context": {"slot": 1},
                    "value": {"signature": signature, "err": None, "logs": logs},
                },
            },
        })

    def test_notifications_without_pump_logs_are_counted_not_stored(self):
        event = {
            "wallet": "wallet-a", "signature": "spam-1", "slot": 1,
            "failed": True, "logs": [],
        }
        self.assertFalse(app.record_helius_standard_wss_notification(
            event, False, 300, received_ts=1_700_000_000
        ))
        self.assertFalse(app.record_helius_standard_wss_notification(
            {**event, "signature": "spam-2"}, False, 200,
            received_ts=1_700_000_001,
        ))
        conn = app.db()
        try:
            stored = conn.execute(
                "SELECT COUNT(*) FROM helius_standard_wss_notifications"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(stored, 0)
        self.assertEqual(app.HELIUS_STANDARD_WSS_UNSTORED["wallet-a"], {
            "notifications": 2, "failed": 2, "message_bytes": 500,
            "first_ts": 1_700_000_000, "last_ts": 1_700_000_001,
        })

        with patch.object(app, "APP_TOKEN", "token"), patch.object(
            app, "WATCHED", {"trader-a": "wallet-a"}
        ):
            report = app.api_helius_standard_wss_stats("token")
        self.assertEqual(report["last_24h"]["notifications"], 0)
        unstored = report["unstored_notifications_since_start"]
        self.assertEqual(unstored["notifications"], 2)
        self.assertEqual(unstored["wallets"][0]["trader"], "trader-a")

    def test_flood_guard_fires_once_per_wallet_within_a_minute(self):
        limit = app.HELIUS_STANDARD_WSS_MAX_OTHER_PER_MINUTE
        base = 1_700_000_000.0
        with patch.object(app, "WATCHED", {"trader-a": "wallet-a"}):
            for index in range(limit):
                rate = app.note_unstored_helius_standard_wss_notification(
                    "wallet-a", 10, False, base + index * 0.01
                )
                self.assertFalse(
                    app.helius_standard_wss_wallet_flooded("wallet-a", rate)
                )
            rate = app.note_unstored_helius_standard_wss_notification(
                "wallet-a", 10, False, base + 1
            )
            self.assertEqual(rate, limit + 1)
            self.assertTrue(
                app.helius_standard_wss_wallet_flooded("wallet-a", rate)
            )
            self.assertFalse(
                app.helius_standard_wss_wallet_flooded("wallet-a", rate)
            )
            muted = app.HELIUS_STANDARD_WSS_STATE["muted_wallets"]["wallet-a"]
            self.assertEqual(muted["trader"], "trader-a")
            self.assertEqual(muted["rate_per_minute"], limit + 1)

            # Slow traffic never accumulates: one notification per minute.
            for index in range(limit + 5):
                rate = app.note_unstored_helius_standard_wss_notification(
                    "wallet-b", 10, False, base + index * 61
                )
                self.assertEqual(rate, 1)

    def test_mute_survives_restart_then_expires_and_can_repeat(self):
        with (
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app, "HELIUS_STANDARD_WSS_MUTE_SECONDS", 900),
            patch.object(app.time, "time", return_value=1_000.0),
        ):
            self.assertTrue(app.helius_standard_wss_wallet_flooded(
                "wallet-a", app.HELIUS_STANDARD_WSS_MAX_OTHER_PER_MINUTE + 1
            ))
        self.reset_memory()
        self.assertEqual(
            app.restore_helius_standard_wss_wallet_mutes(now=1_100), 1
        )
        self.assertEqual(
            app.HELIUS_STANDARD_WSS_STATE["muted_wallets"]
            ["wallet-a"]["muted_until_ts"], 1_900.0
        )
        self.assertEqual(
            app.expire_helius_standard_wss_wallet_mutes(now=1_899), []
        )
        self.assertEqual(
            app.expire_helius_standard_wss_wallet_mutes(now=1_900),
            ["wallet-a"],
        )
        self.assertEqual(
            app.restore_helius_standard_wss_wallet_mutes(now=2_000), 0
        )
        conn = app.db()
        try:
            self.assertIsNone(conn.execute(
                "SELECT unmuted_ts FROM helius_standard_wss_wallet_mutes "
                "WHERE wallet = 'wallet-a'"
            ).fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(
            app.close_helius_standard_wss_wallet_mute("wallet-a", now=2_050),
            1,
        )
        with (
            patch.object(app, "WATCHED", {"trader-a": "wallet-a"}),
            patch.object(app.time, "time", return_value=2_100.0),
        ):
            self.assertTrue(app.helius_standard_wss_wallet_flooded(
                "wallet-a", app.HELIUS_STANDARD_WSS_MAX_OTHER_PER_MINUTE + 1
            ))
        conn = app.db()
        try:
            intervals = conn.execute(
                "SELECT muted_ts, muted_until_ts, unmuted_ts "
                "FROM helius_standard_wss_wallet_mutes "
                "WHERE wallet = 'wallet-a' ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(intervals, [
            (1_000.0, 1_900.0, 2_050.0),
            (2_100.0, 3_000.0, None),
        ])

    async def test_worker_unsubscribes_flooded_wallet_and_skips_it_on_reconnect(self):
        socket = FakeWebSocket()
        with (
            patch.object(app, "HELIUS_STANDARD_WSS_URL", "wss://example.test"),
            patch.object(app, "HELIUS_STANDARD_WSS_RPC_URL", "https://example.test/rpc"),
            patch.object(app, "HELIUS_STANDARD_WSS_APPLY", False),
            patch.object(app, "HELIUS_STANDARD_WSS_MAX_OTHER_PER_MINUTE", 60),
            patch.object(app, "HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS", 0.01),
            patch.object(app, "HELIUS_STANDARD_WSS_RECONNECT_SECONDS", 0.01),
            patch.object(
                app, "WATCHED",
                {"trader-a": "wallet-a", "trader-b": "wallet-b"},
            ),
            patch.object(app, "HELIUS_STANDARD_WSS_TRADERS", ""),
            patch.object(app.websockets, "connect", return_value=socket),
            patch.object(
                app, "send_discord_alert", new=unittest.mock.AsyncMock()
            ) as alert,
        ):
            worker = asyncio.create_task(app.helius_standard_wss_worker())
            try:
                for _ in range(100):
                    if len(socket.sent) == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(
                    [req["params"][0]["mentions"] for req in socket.sent],
                    [["wallet-a"], ["wallet-b"]],
                )
                await socket.incoming.put(json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "result": 91}
                ))
                await socket.incoming.put(json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "result": 92}
                ))
                for index in range(61):
                    await socket.incoming.put(
                        self.notification(91, f"spam-{index}")
                    )
                for _ in range(300):
                    if len(socket.sent) == 3:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(socket.sent[2]["method"], "logsUnsubscribe")
                self.assertEqual(socket.sent[2]["params"], [91])
                unsubscribe_id = socket.sent[2]["id"]
                await socket.incoming.put(json.dumps(
                    {"jsonrpc": "2.0", "id": unsubscribe_id, "result": True}
                ))
                for _ in range(100):
                    if app.HELIUS_STANDARD_WSS_STATE["subscriptions"] == 1:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(app.HELIUS_STANDARD_WSS_STATE["subscriptions"], 1)
                self.assertIn(
                    "wallet-a", app.HELIUS_STANDARD_WSS_STATE["muted_wallets"]
                )
                alert.assert_awaited_once()
                self.assertIn("trader-a", alert.await_args.args[0])

                with patch.object(app, "APP_TOKEN", "token"):
                    report = app.api_helius_standard_wss_stats("token")
                self.assertEqual(report["selected_wallets"], 2)
                self.assertEqual(
                    report["selected_traders"], ["trader-a", "trader-b"]
                )
                self.assertEqual(report["active_wallets"], 1)
                self.assertTrue(report["wallet_subscriptions_ready"])
                self.assertEqual(
                    report["muted_wallets"][0]["trader"], "trader-a"
                )
                conn = app.db()
                try:
                    stored = conn.execute(
                        "SELECT COUNT(*) FROM helius_standard_wss_notifications"
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(stored, 0)

                # Force a reconnect: only the surviving wallet resubscribes.
                app.start_helius_token_subscription_interval(
                    "mint-while-connected"
                )
                await socket.incoming.put("not json")
                for _ in range(300):
                    if len(socket.sent) == 4:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 4)
                self.assertEqual(
                    socket.sent[3]["params"][0], {"mentions": ["wallet-b"]}
                )
                self.assertIsNone(app.HELIUS_STANDARD_WSS_STATE["last_error"])
                self.assertEqual(
                    app.HELIUS_STANDARD_WSS_STATE["last_reconnect_error"],
                    "HELIUS_STANDARD_WSS_JSONDecodeError",
                )
                self.assertIsNotNone(
                    app.HELIUS_STANDARD_WSS_STATE["last_reconnect_ts"]
                )
                conn = app.db()
                try:
                    reason = conn.execute(
                        "SELECT close_reason FROM "
                        "helius_standard_wss_token_intervals "
                        "WHERE mint = 'mint-while-connected'"
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(reason, "wss_reconnect")

                await socket.incoming.put(json.dumps(
                    {"jsonrpc": "2.0", "id": socket.sent[3]["id"],
                     "result": 93}
                ))
                conn = app.db()
                try:
                    conn.execute(
                        "UPDATE helius_standard_wss_wallet_mutes "
                        "SET muted_until_ts = ? WHERE wallet = 'wallet-a' "
                        "AND unmuted_ts IS NULL",
                        (time.time() - 1,),
                    )
                    conn.commit()
                finally:
                    conn.close()
                with app.HELIUS_STANDARD_WSS_STATE_LOCK:
                    app.HELIUS_STANDARD_WSS_STATE["muted_wallets"][
                        "wallet-a"
                    ]["muted_until_ts"] = time.time() - 1
                for _ in range(300):
                    if len(socket.sent) == 5:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(socket.sent), 5)
                self.assertEqual(socket.sent[4]["method"], "logsSubscribe")
                self.assertEqual(
                    socket.sent[4]["params"][0], {"mentions": ["wallet-a"]}
                )
                conn = app.db()
                try:
                    self.assertIsNone(conn.execute(
                        "SELECT unmuted_ts FROM "
                        "helius_standard_wss_wallet_mutes "
                        "WHERE wallet = 'wallet-a'"
                    ).fetchone()[0])
                finally:
                    conn.close()
                await socket.incoming.put(json.dumps(
                    {"jsonrpc": "2.0", "id": socket.sent[4]["id"],
                     "result": 94}
                ))
                for _ in range(100):
                    if app.HELIUS_STANDARD_WSS_STATE["subscriptions"] == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(app.HELIUS_STANDARD_WSS_STATE["subscriptions"], 2)
                self.assertEqual(app.HELIUS_STANDARD_WSS_STATE["reconnects"], 1)
                conn = app.db()
                try:
                    self.assertIsNotNone(conn.execute(
                        "SELECT unmuted_ts FROM "
                        "helius_standard_wss_wallet_mutes "
                        "WHERE wallet = 'wallet-a'"
                    ).fetchone()[0])
                finally:
                    conn.close()
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker


if __name__ == "__main__":
    unittest.main()
