import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app


class MarketEventRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app, "WATCHED", {"trader": "wallet"}))
        self.stack.enter_context(patch.object(app, "TRACKED_TOKENS", set()))
        self.effects = {
            name: self.stack.enter_context(patch.object(app, name))
            for name in (
                "save_trade", "save_token_history", "update_paper_position",
                "evaluate_live_position_exit", "process_signal_outcomes_event",
            )
        }

    def test_unrelated_event_has_no_effects(self):
        app.route_market_event({"mint": "other", "traderPublicKey": "other"})
        for effect in self.effects.values():
            effect.assert_not_called()

    def test_newly_tracked_buy_does_not_become_its_own_followup(self):
        event = {"mint": "new", "traderPublicKey": "wallet", "txType": "buy"}
        self.effects["save_trade"].side_effect = lambda *a, **k: app.TRACKED_TOKENS.add("new")
        app.route_market_event(event)
        self.effects["save_trade"].assert_called_once_with(
            "trader",
            "wallet",
            event,
            source="live",
            allow_live_buys=True,
            allow_live_exits=True,
            transport="live",
        )
        self.effects["process_signal_outcomes_event"].assert_not_called()

    def test_watched_wallet_transport_permissions_reach_save_trade(self):
        event = {"mint": "mint", "traderPublicKey": "wallet", "txType": "buy"}

        app.route_market_event(
            event,
            allow_live_buys=False,
            allow_live_exits=True,
            transport="rpc",
        )

        self.effects["save_trade"].assert_called_once_with(
            "trader",
            "wallet",
            event,
            source="live",
            allow_live_buys=False,
            allow_live_exits=True,
            transport="rpc",
        )

    def test_tracking_removed_by_wallet_update_still_records_outcome(self):
        app.TRACKED_TOKENS.add("mint")
        self.effects["save_trade"].side_effect = lambda *a, **k: app.TRACKED_TOKENS.clear()
        event = {"mint": "mint", "wallet": "wallet", "txType": "sell"}
        app.route_market_event(event)
        self.effects["process_signal_outcomes_event"].assert_called_once_with(mint="mint", event=event)
        self.effects["update_paper_position"].assert_not_called()
        self.effects["evaluate_live_position_exit"].assert_not_called()

    def test_token_aliases_and_unknown_live_balance_are_preserved(self):
        app.TRACKED_TOKENS.add("mint")
        event = {
            "mint": "mint",
            "user": "other",
            "type": "SELL",
            "market_cap_sol": 42,
            "blockEventTs": 1_700_000_000.0,
        }
        app.route_market_event(event)
        self.effects["save_trade"].assert_not_called()
        paper = self.effects["update_paper_position"].call_args.kwargs
        live = self.effects["evaluate_live_position_exit"].call_args.kwargs
        self.assertEqual(paper["side"], "sell")
        self.assertEqual(paper["market_cap"], 42)
        self.assertIsNone(paper["new_token_balance"])
        self.assertIsNone(live["new_token_balance"])
        self.assertEqual(live["event_block_event_ts"], 1_700_000_000.0)
        self.assertEqual(self.effects["save_token_history"].call_args.kwargs["source"], "token-live")

    def test_explicit_unknown_balance_is_preserved_for_paper_and_live(self):
        app.TRACKED_TOKENS.add("mint")
        event = {
            "mint": "mint",
            "user": "other",
            "type": "SELL",
            "market_cap_sol": 42,
            "newTokenBalance": None,
        }

        app.route_market_event(event)

        paper = self.effects["update_paper_position"].call_args.kwargs
        live = self.effects["evaluate_live_position_exit"].call_args.kwargs
        self.assertIsNone(paper["new_token_balance"])
        self.assertIsNone(live["new_token_balance"])

    def test_transport_can_allow_live_exits_while_blocking_live_buys(self):
        app.TRACKED_TOKENS.add("mint")
        event = {
            "mint": "mint",
            "user": "other",
            "type": "SELL",
            "market_cap_sol": 42,
            "newTokenBalance": 0,
        }

        app.route_market_event(
            event,
            allow_live_buys=False,
            allow_live_exits=True,
        )

        self.effects["update_paper_position"].assert_called_once()
        self.effects["evaluate_live_position_exit"].assert_called_once()

    def test_transport_can_disable_live_exits_explicitly(self):
        app.TRACKED_TOKENS.add("mint")
        event = {
            "mint": "mint",
            "user": "other",
            "type": "SELL",
            "market_cap_sol": 42,
            "newTokenBalance": 0,
        }

        app.route_market_event(
            event,
            allow_live_buys=False,
            allow_live_exits=False,
        )

        self.effects["update_paper_position"].assert_called_once()
        self.effects["evaluate_live_position_exit"].assert_not_called()


class StreamRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_signature_is_skipped_without_stopping_the_stream(self):
        invalid_event = {
            "signature": "   ",
            "traderPublicKey": "watched-wallet",
            "mint": "ignored-mint",
            "txType": "buy",
        }
        valid_event = {
            **invalid_event,
            "signature": "valid-signature",
            "mint": "valid-mint",
        }
        socket = AsyncMock()
        socket.recv.side_effect = [
            json.dumps(invalid_event),
            json.dumps(valid_event),
            asyncio.CancelledError(),
        ]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "invalid-signature.db",
                "API_KEY": "test-key",
                "WATCHED": {"test-trader": "watched-wallet"},
                "TRACKED_TOKENS": set(),
                "SUBSCRIBED_TOKENS": set(),
                "TOKENS_TO_UNSUBSCRIBE": set(),
                "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
                "STREAM_CONNECTED": False,
                "LAST_STREAM_MESSAGE_TS": 0,
                "LAST_STREAM_EVENT_TS": 0,
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(
                app.websockets, "connect", return_value=connection,
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_recovered", new=AsyncMock(),
            ))
            problem = stack.enter_context(patch.object(
                app, "mark_stream_problem", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ))
            route = stack.enter_context(patch.object(app, "route_market_event"))
            app.migrate_database()

            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

        problem.assert_not_awaited()
        route.assert_called_once_with(valid_event)

    async def test_status_separates_account_events_from_token_events(self):
        token_event = {
            "signature": "token-signature",
            "traderPublicKey": "other-wallet",
            "mint": "tracked-mint",
            "txType": "buy",
        }
        account_event = {
            **token_event,
            "signature": "account-signature",
            "traderPublicKey": "watched-wallet",
        }
        socket = AsyncMock()
        socket.recv.side_effect = [
            json.dumps(token_event),
            asyncio.CancelledError(),
        ]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "split-ts.db",
                "API_KEY": "test-key",
                "WATCHED": {"test-trader": "watched-wallet"},
                "TRACKED_TOKENS": set(),
                "SUBSCRIBED_TOKENS": set(),
                "TOKENS_TO_UNSUBSCRIBE": set(),
                "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
                "STREAM_CONNECTED": False,
                "LAST_STREAM_MESSAGE_TS": 0,
                "LAST_STREAM_EVENT_TS": 0,
                "LAST_STREAM_ACCOUNT_EVENT_TS": 0.0,
                "LAST_STREAM_TOKEN_EVENT_TS": 0.0,
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(
                app.websockets, "connect", return_value=connection,
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_recovered", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_problem", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ))
            stack.enter_context(patch.object(app, "route_market_event"))
            app.migrate_database()

            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

            status = app.status(x_app_token=app.APP_TOKEN)
            self.assertIsNone(status["stream_last_account_event_ts"])
            self.assertGreater(status["stream_last_token_event_ts"], 0)

            socket.recv.side_effect = [
                json.dumps(account_event),
                asyncio.CancelledError(),
            ]
            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

            status = app.status(x_app_token=app.APP_TOKEN)
            self.assertGreater(status["stream_last_account_event_ts"], 0)
            self.assertGreaterEqual(
                status["stream_last_account_event_ts"],
                status["stream_last_token_event_ts"],
            )

    async def test_unusable_balance_becomes_unknown_without_stopping_the_stream(self):
        """PumpPortal es frontera de confianza: basura no tira la conexión.

        Antes, un `newTokenBalance` inservible lanzaba dentro del router con la
        identidad ya reservada: el stream reconectaba y el evento se perdía.
        Ahora pasa a desconocido, que es lo único honesto que se puede decir
        de un valor así, y el evento sigue su camino.
        """
        event = {
            "signature": "garbage-balance-signature",
            "traderPublicKey": "watched-wallet",
            "mint": "routing-mint",
            "txType": "sell",
            "marketCapSol": 100,
            "solAmount": 1,
            "newTokenBalance": -5,
        }
        socket = AsyncMock()
        socket.recv.side_effect = [
            json.dumps(event),
            asyncio.CancelledError(),
        ]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "garbage-balance.db",
                "API_KEY": "test-key",
                "WATCHED": {"test-trader": "watched-wallet"},
                "TRACKED_TOKENS": set(),
                "SUBSCRIBED_TOKENS": set(),
                "TOKENS_TO_UNSUBSCRIBE": set(),
                "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
                "STREAM_CONNECTED": False,
                "LAST_STREAM_MESSAGE_TS": 0,
                "LAST_STREAM_EVENT_TS": 0,
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(
                app.websockets, "connect", return_value=connection,
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_recovered", new=AsyncMock(),
            ))
            problem = stack.enter_context(patch.object(
                app, "mark_stream_problem", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ))
            route = stack.enter_context(patch.object(app, "route_market_event"))
            app.migrate_database()

            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

        problem.assert_not_awaited()
        route.assert_called_once()
        routed = route.call_args.args[0]
        self.assertIn("newTokenBalance", routed)
        self.assertIsNone(routed["newTokenBalance"])

    async def test_same_signature_distinct_indexes_both_reach_the_router(self):
        base_event = {
            "signature": "multi-operation-signature",
            "traderPublicKey": "watched-wallet",
            "mint": "routing-mint",
            "txType": "sell",
            "marketCapSol": 100,
            "solAmount": 1,
            "newTokenBalance": 10,
        }
        events = [
            {**base_event, "eventIndex": 0},
            {**base_event, "eventIndex": 1},
        ]
        socket = AsyncMock()
        socket.recv.side_effect = [
            json.dumps(events[0]),
            json.dumps(events[1]),
            asyncio.CancelledError(),
        ]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "multi-event-routing.db",
                "API_KEY": "test-key",
                "WATCHED": {"test-trader": "watched-wallet"},
                "TRACKED_TOKENS": set(),
                "SUBSCRIBED_TOKENS": set(),
                "TOKENS_TO_UNSUBSCRIBE": set(),
                "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
                "STREAM_CONNECTED": False,
                "LAST_STREAM_MESSAGE_TS": 0,
                "LAST_STREAM_EVENT_TS": 0,
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(
                app.websockets, "connect", return_value=connection,
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_recovered", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ))
            route = stack.enter_context(patch.object(app, "route_market_event"))
            app.migrate_database()

            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

        self.assertEqual(route.call_count, 2)
        self.assertEqual(
            [call.args[0]["eventIndex"] for call in route.call_args_list],
            [0, 1],
        )

    async def test_router_failure_is_quarantined_without_replay(self):
        event = {
            "signature": "failed-route", "traderPublicKey": "wallet",
            "mint": "mint", "txType": "buy",
        }
        socket = AsyncMock()
        socket.recv.side_effect = [json.dumps(event), asyncio.CancelledError()]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "failure.db", "API_KEY": "test-key",
                "WATCHED": {"trader": "wallet"}, "TRACKED_TOKENS": set(),
                "SUBSCRIBED_TOKENS": set(), "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(
                app.websockets, "connect", return_value=connection,
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_recovered", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app, "mark_stream_problem", new=AsyncMock(),
            ))
            stack.enter_context(patch.object(
                app.asyncio, "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ))
            route = stack.enter_context(patch.object(
                app, "route_market_event", side_effect=RuntimeError("route failed"),
            ))
            app.migrate_database()
            with self.assertRaises(asyncio.CancelledError):
                await app.stream()
            conn = app.db()
            try:
                state = conn.execute(
                    "SELECT route_state FROM processed_market_events "
                    "WHERE signature = ?", (event["signature"],),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(state, "uncertain")
            self.assertFalse(app.mark_market_event_processed(event["signature"]))
            with patch.object(app, "route_market_event") as replay:
                app.recover_reserved_stream_events_once(
                    now=time.time() + app.MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS + 1,
                )
                replay.assert_not_called()
            route.assert_called_once_with(event)

    async def check_routing(self, watched, tracked, real_effects=False):
        event = {
            "signature": "routing-test-signature",
            "traderPublicKey": "watched-wallet" if watched else "other-wallet",
            "mint": "routing-mint",
            "txType": "sell",
            "marketCapSol": 100,
            "solAmount": 1,
            "newTokenBalance": 10,
            "vSolInBondingCurve": 100,
            "vTokensInBondingCurve": 1000,
        }
        socket = AsyncMock()
        socket.recv.side_effect = [json.dumps(event), json.dumps(event), asyncio.CancelledError()]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "DB": Path(directory) / "routing.db",
                "API_KEY": "test-key",
                "WATCHED": {"test-trader": "watched-wallet"},
                "TRACKED_TOKENS": {event["mint"]} if tracked else set(),
                "SUBSCRIBED_TOKENS": set(),
                "TOKENS_TO_UNSUBSCRIBE": set(),
                "SEEN_EVENT_IDS": set(),
                "FORCE_STREAM_ERROR": False,
                "STREAM_CONNECTED": False,
                "LAST_STREAM_MESSAGE_TS": 0,
                "LAST_STREAM_EVENT_TS": 0,
                "LAST_TOKEN_PRICE": {},
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(app.websockets, "connect", return_value=connection))
            stack.enter_context(patch.object(app, "mark_stream_recovered", new=AsyncMock()))
            problem = stack.enter_context(patch.object(app, "mark_stream_problem", new=AsyncMock()))
            # Stop on unexpected stream errors instead of retrying indefinitely.
            stack.enter_context(patch.object(app.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError())))
            effects = {
                name: stack.enter_context(patch.object(
                    app, name,
                    wraps=getattr(app, name)
                    if real_effects and name != "evaluate_live_position_exit" else None,
                ))
                for name in (
                    "save_token_history", "update_paper_position",
                    "evaluate_live_position_exit", "process_signal_outcomes_event",
                )
            }
            app.migrate_database()
            if real_effects:
                conn = app.db()
                try:
                    conn.execute(
                        "INSERT INTO paper_positions "
                        "(mint, entry_mc, stake_usd, remaining_pct, realized_pnl_usd, "
                        "origin_trader, tp_stage, status, mode) "
                        "VALUES (?, 100, 5, 1, 0, 'test-trader', 0, 'open', 'paper')",
                        (event["mint"],),
                    )
                    conn.commit()
                finally:
                    conn.close()
                app.create_signal_outcome(
                    signal_id=123, mint=event["mint"], trader="test-trader",
                    signal_ts=time.time() - 15, price_at_signal=0.1,
                )
            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

            problem.assert_not_awaited()
            for name in ("save_token_history", "update_paper_position", "evaluate_live_position_exit"):
                self.assertEqual(effects[name].call_count, 1, name)
            self.assertEqual(effects["process_signal_outcomes_event"].call_count, int(tracked))
            conn = app.db()
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0], int(watched))
                state = conn.execute(
                    "SELECT route_state, route_event_json FROM processed_market_events "
                    "WHERE signature = ?", (event["signature"],),
                ).fetchone()
                self.assertEqual(state, ("processed", None) if watched else (None, None))
                if real_effects:
                    position = conn.execute(
                        "SELECT remaining_pct, tp_stage, status FROM paper_positions"
                    ).fetchone()
                    self.assertEqual(position, (0.75, 0, "open"))
                    outcome = conn.execute(
                        "SELECT price_10s, price_30s FROM signal_outcomes WHERE signal_id=123"
                    ).fetchone()
                    self.assertEqual(outcome, (0.1, None))
            finally:
                conn.close()

    async def test_watched_wallet_and_tracked_token_apply_effects_once(self):
        await self.check_routing(watched=True, tracked=True)

    async def test_watched_wallet_only_keeps_position_updates(self):
        await self.check_routing(watched=True, tracked=False)

    async def test_other_wallet_on_tracked_token_keeps_all_updates(self):
        await self.check_routing(watched=False, tracked=True)

    async def test_partial_sell_updates_paper_once_and_preserves_outcome(self):
        await self.check_routing(watched=True, tracked=True, real_effects=True)


class StreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch_db = patch.object(app, "DB", Path(self.temp.name) / "stream.db")
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.patch_watched = patch.object(app, "WATCHED", {"trader": "wallet"})
        self.patch_watched.start()
        self.addCleanup(self.patch_watched.stop)
        app.migrate_database()
        self.event = {
            "signature": "stream-recovery-sig", "traderPublicKey": "wallet",
            "mint": "mint", "txType": "buy",
        }
        self.event_id = app.market_event_identity(self.event["signature"], 0)

    def age_reservation(self):
        conn = app.db()
        try:
            conn.execute(
                "UPDATE processed_market_events SET ts = ? WHERE event_id = ?",
                (time.time() - app.MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS - 1,
                 self.event_id),
            )
            conn.commit()
        finally:
            conn.close()

    def route_state(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT route_state, route_event_json FROM processed_market_events "
                "WHERE event_id = ?", (self.event_id,),
            ).fetchone()
        finally:
            conn.close()

    def test_reserved_event_replays_once_without_live_orders(self):
        self.assertTrue(app.mark_market_event_processed(
            self.event["signature"], recovery_event=self.event,
        ))
        self.age_reservation()
        with patch.object(app, "route_market_event") as route:
            result = app.recover_reserved_stream_events_once()
            self.assertEqual(result["recovered_ids"], [self.event_id])
            route.assert_called_once_with(
                self.event, allow_live_buys=False, allow_live_exits=False,
                transport="pumpportal-recovery",
            )
            self.assertEqual(app.recover_reserved_stream_events_once()["recovered_ids"], [])
            route.assert_called_once()
        self.assertEqual(self.route_state(), ("processed", None))
        self.assertFalse(app.mark_market_event_processed(
            self.event["signature"], source="helius",
        ))

    def test_routing_state_is_never_replayed(self):
        self.assertTrue(app.mark_market_event_processed(
            self.event["signature"], recovery_event=self.event,
        ))
        self.assertTrue(app.move_stream_route_state(
            self.event_id, "reserved", "routing",
        ))
        self.age_reservation()
        with patch.object(app, "route_market_event") as route:
            result = app.recover_reserved_stream_events_once()
            route.assert_not_called()
        self.assertEqual(result["uncertain_ids"], [self.event_id])

    def test_mismatched_payload_is_quarantined(self):
        self.assertTrue(app.mark_market_event_processed(
            self.event["signature"], recovery_event=self.event,
        ))
        conn = app.db()
        try:
            conn.execute(
                "UPDATE processed_market_events SET route_event_json = ? "
                "WHERE event_id = ?",
                (json.dumps({**self.event, "signature": "other"}), self.event_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.age_reservation()
        with patch.object(app, "route_market_event") as route:
            result = app.recover_reserved_stream_events_once()
            route.assert_not_called()
        self.assertEqual(result["uncertain_ids"], [self.event_id])
        self.assertEqual(self.route_state()[0], "uncertain")

    def test_removed_wallet_is_not_silently_processed(self):
        self.assertTrue(app.mark_market_event_processed(
            self.event["signature"], recovery_event=self.event,
        ))
        self.age_reservation()
        with patch.object(app, "WATCHED", {}), patch.object(
            app, "route_market_event"
        ) as route:
            result = app.recover_reserved_stream_events_once()
            route.assert_not_called()
        self.assertEqual(result["uncertain_ids"], [self.event_id])

    def test_old_schema_migrates_without_replaying_old_rows(self):
        path = Path(self.temp.name) / "old.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "CREATE TABLE processed_market_events ("
                "event_id TEXT PRIMARY KEY, signature TEXT NOT NULL, "
                "event_index INTEGER NOT NULL, ts REAL, source TEXT DEFAULT 'live')"
            )
            conn.execute(
                "INSERT INTO processed_market_events VALUES (?, ?, 0, 1, 'live')",
                (self.event_id, self.event["signature"]),
            )
            conn.commit()
        with patch.object(app, "DB", path):
            app.migrate_database()
            conn = app.db()
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT route_state, route_event_json FROM "
                        "processed_market_events WHERE event_id = ?",
                        (self.event_id,),
                    ).fetchone(),
                    (None, None),
                )
            finally:
                conn.close()
            with patch.object(app, "route_market_event") as route:
                app.recover_reserved_stream_events_once()
                route.assert_not_called()
