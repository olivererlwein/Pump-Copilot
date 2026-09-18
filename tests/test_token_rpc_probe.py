import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TokenRpcProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "probe.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def test_samples_one_mint_and_skips_unchanged_receipt(self):
        receipt = {"blockTime": 1_700_000_000}
        event = {"event": {
            "mint": "mint-a",
            "vSolInBondingCurve": 10,
            "vTokensInBondingCurve": 100,
        }}
        with (
            patch.object(app, "WATCHED", {}),
            patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.test"),
            patch.object(app, "fetch_signatures_for_address", return_value=[
                {"signature": "sig-a"}
            ]) as signatures,
            patch.object(app, "fetch_confirmed_transaction", return_value=receipt) as fetch,
            patch.object(app, "parse_tracked_token_pump_events", return_value=[event]) as parse,
        ):
            first = app.token_rpc_probe_once(now=1_700_000_005, mints=["mint-a", "mint-b"])
            second = app.token_rpc_probe_once(now=1_700_000_035, mints=["mint-a", "mint-b"])

        self.assertEqual(first, {"status": "sampled", "rpc_calls": 2})
        self.assertEqual(second, {"status": "unchanged", "rpc_calls": 1})
        signatures.assert_called_with("https://rpc.test", "mint-a", limit=1)
        self.assertEqual(signatures.call_count, 2)
        fetch.assert_called_once_with("https://rpc.test", "sig-a")
        parse.assert_called_once_with(receipt, {"mint-a"}, "sig-a")
        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT status, price, rpc_calls FROM token_rpc_probe_samples "
                "ORDER BY id"
            ).fetchall()
            effects = [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in ("trades", "evaluations", "paper_positions")]
        finally:
            conn.close()
        self.assertEqual(rows, [("sampled", 0.1, 2), ("unchanged", None, 1)])
        self.assertEqual(effects, [0, 0, 0])
        with (
            patch.object(app, "APP_TOKEN", "test-token"),
            patch.object(app.time, "time", return_value=1_700_000_040),
        ):
            stats = app.api_token_rpc_probe_stats("test-token")
        self.assertFalse(stats["affects_decisions"])
        self.assertEqual(stats["last_24h"]["rpc_method_calls"], 3)
        self.assertEqual(
            stats["last_24h"]["statuses"],
            {"sampled": 1, "unchanged": 1},
        )

    def test_unavailable_receipt_is_retried_without_replaying_outcomes(self):
        with (
            patch.object(app, "WATCHED", {}),
            patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.test"),
            patch.object(app, "fetch_signatures_for_address", return_value=[
                {"signature": "sig-a"}
            ]),
            patch.object(app, "fetch_confirmed_transaction", side_effect=[
                None, {"blockTime": 1_700_000_000}
            ]) as fetch,
            patch.object(app, "parse_tracked_token_pump_events", return_value=[]),
        ):
            first = app.token_rpc_probe_once(now=1_700_000_005, mints=["mint-a"])
            second = app.token_rpc_probe_once(now=1_700_000_035, mints=["mint-a"])
        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(second["status"], "no_pump_event")
        self.assertEqual(fetch.call_count, 2)

    def test_empty_selection_makes_no_rpc_call(self):
        with patch.object(app, "fetch_signatures_for_address") as fetch:
            self.assertEqual(
                app.token_rpc_probe_once(now=1_700_000_000, mints=[]),
                {"status": "no_active_token", "rpc_calls": 0},
            )
        fetch.assert_not_called()

    def test_prefers_recent_active_signal_over_older_or_untracked_mint(self):
        conn = app.db()
        try:
            for mint, signal_ts, status in (
                ("mint-old", 1_699_999_010, "active"),
                ("mint-new", 1_699_999_020, "active"),
                ("mint-ignored", 1_699_999_030, "active"),
                ("mint-closed", 1_699_999_040, "completed"),
                ("mint-stale", 1_699_998_000, "active"),
            ):
                conn.execute(
                    "INSERT INTO signal_outcomes(mint, signal_ts, status, "
                    "created_ts, updated_ts) VALUES(?,?,?,?,?)",
                    (mint, signal_ts, status, signal_ts, signal_ts),
                )
            conn.commit()
        finally:
            conn.close()
        with (
            patch.object(app, "WATCHED", {}),
            patch.object(app, "_tracked_tokens_snapshot", return_value=[
                "mint-old", "mint-new", "mint-closed", "mint-stale"
            ]),
            patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.test"),
            patch.object(app, "fetch_signatures_for_address", return_value=[]) as fetch,
        ):
            result = app.token_rpc_probe_once(now=1_700_000_000)
        self.assertEqual(result, {"status": "no_signature", "rpc_calls": 1})
        fetch.assert_called_once_with("https://rpc.test", "mint-new", limit=1)
