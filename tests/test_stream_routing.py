import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app


class StreamRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def check_routing(self, watched, tracked):
        event = {
            "signature": "routing-test-signature",
            "traderPublicKey": "watched-wallet" if watched else "other-wallet",
            "mint": "routing-mint",
            "txType": "sell",
            "marketCapSol": 100,
            "solAmount": 1,
            "newTokenBalance": 10,
        }
        socket = AsyncMock()
        socket.recv.side_effect = [json.dumps(event), asyncio.CancelledError()]
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
            }.items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(app.websockets, "connect", return_value=connection))
            stack.enter_context(patch.object(app, "mark_stream_recovered", new=AsyncMock()))
            problem = stack.enter_context(patch.object(app, "mark_stream_problem", new=AsyncMock()))
            # Stop on unexpected stream errors instead of retrying indefinitely.
            stack.enter_context(patch.object(app.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError())))
            effects = {
                name: stack.enter_context(patch.object(app, name))
                for name in (
                    "save_token_history", "update_paper_position",
                    "evaluate_live_position_exit", "process_signal_outcomes_event",
                )
            }
            app.migrate_database()
            with self.assertRaises(asyncio.CancelledError):
                await app.stream()

            problem.assert_not_awaited()
            for name in ("save_token_history", "update_paper_position", "evaluate_live_position_exit"):
                self.assertEqual(effects[name].call_count, 1, name)
            self.assertEqual(effects["process_signal_outcomes_event"].call_count, int(tracked))
            conn = app.db()
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0], int(watched))
            finally:
                conn.close()

    async def test_watched_wallet_and_tracked_token_apply_effects_once(self):
        await self.check_routing(watched=True, tracked=True)

    async def test_watched_wallet_only_keeps_position_updates(self):
        await self.check_routing(watched=True, tracked=False)

    async def test_other_wallet_on_tracked_token_keeps_all_updates(self):
        await self.check_routing(watched=False, tracked=True)
