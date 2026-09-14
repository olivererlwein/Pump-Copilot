import asyncio
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
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
        self.effects["save_trade"].assert_called_once_with("trader", "wallet", event, source="live")
        self.effects["process_signal_outcomes_event"].assert_not_called()

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
        event = {"mint": "mint", "user": "other", "type": "SELL", "market_cap_sol": 42}
        app.route_market_event(event)
        self.effects["save_trade"].assert_not_called()
        paper = self.effects["update_paper_position"].call_args.kwargs
        live = self.effects["evaluate_live_position_exit"].call_args.kwargs
        self.assertEqual(paper["side"], "sell")
        self.assertEqual(paper["market_cap"], 42)
        self.assertEqual(paper["new_token_balance"], 0)
        self.assertIsNone(live["new_token_balance"])
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

    def test_observational_transport_cannot_reach_live_exit(self):
        app.TRACKED_TOKENS.add("mint")
        event = {
            "mint": "mint",
            "user": "other",
            "type": "SELL",
            "market_cap_sol": 42,
            "newTokenBalance": 0,
        }

        app.route_market_event(event, allow_live_execution=False)

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
