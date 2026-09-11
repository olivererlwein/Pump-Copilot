import asyncio
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app


class StreamRoutingTests(unittest.IsolatedAsyncioTestCase):
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
                "SEEN_SIGNATURES": set(),
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
