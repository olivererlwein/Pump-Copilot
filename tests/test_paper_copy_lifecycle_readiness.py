import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PaperCopyLifecycleReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "readiness.db"
        app.migrate_database()
        self.now = 10_000.0

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def signal(self, signature, ts, decision, mint):
        conn = app.db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO evaluations(
                    trade_signature, event_index, ts, trader, mint,
                    source, score, decision
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (signature, 0, ts, "test-trader", mint, "live", 60, decision),
            )
            signal_id = cursor.lastrowid
            outcome = conn.execute(
                """
                INSERT INTO signal_outcomes(
                    signal_id, mint, trader, signal_ts, price_at_signal,
                    entry_price_basis, created_ts, updated_ts
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (signal_id, mint, "test-trader", ts, 1e-7,
                 "pump", ts + 2, ts + 2),
            )
            conn.commit()
            return signal_id, outcome.lastrowid
        finally:
            conn.close()

    def test_readiness_reports_missing_sell_identity_and_balance_without_pnl(self):
        signal_id, outcome_id = self.signal("sig-a", 9_050, "WATCH", "mint-a")
        self.signal("sig-b", 9_500, "COPY", "mint-b")
        conn = app.db()
        try:
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (outcome_id, 10, "mint-a", "curve", 1e-7, 100,
                 9_055, 123),
            )
            conn.execute(
                """
                INSERT INTO trades(
                    ts, trader, mint, side, signature, source,
                    event_index, recorded_ts, new_token_balance
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (9_100, "test-trader", "mint-a", "sell", "legacy-sell",
                 "live", None, 9_101, None),
            )
            conn.execute(
                """
                INSERT INTO trades(ts, trader, mint, side, signature, source)
                VALUES(?,?,?,?,?,?)
                """,
                (9_110, "test-trader", "mint-a", "sell", "old-sell", "live"),
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.now
        ), patch.object(
            app, "get_account_checkpoint_subscription_coverage",
            return_value={signal_id: {"complete": True}},
        ) as coverage:
            report = app.paper_copy_lifecycle_readiness(
                9_000, x_app_token="test-token"
            )

        self.assertEqual(report["counts"], {
            "signals": 2,
            "decision_watch": 1,
            "decision_copy": 1,
            "account_quote_within_30s_of_outcome_creation": 1,
            "signals_with_origin_sell_after_recording": 1,
            "signals_with_sell_missing_index": 1,
            "signals_with_sell_missing_balance": 1,
            "signals_with_legacy_sell_without_recorded_time": 1,
            "matured_15m": 1,
            "continuous_15m_token_coverage": 1,
        })
        self.assertEqual(coverage.call_args.args[0], [
            {"signal_id": signal_id, "signal_ts": 9_050, "mint": "mint-a"}
        ])
        self.assertFalse(report["can_estimate_full_lifecycle_pnl"])
        self.assertFalse(report["affects_decisions"])
        self.assertNotIn("signals_with_ambiguous_legacy_zero_balance", report["counts"])

    def test_readiness_counts_sell_quote_but_never_calls_it_pnl(self):
        self.signal("sig-a", 9_050, "WATCH", "mint-a")
        conn = app.db()
        try:
            cursor = conn.execute(
                "INSERT INTO trades(ts, trader, mint, side, signature, "
                "source, event_index, recorded_ts, new_token_balance) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (9_100, "test-trader", "mint-a", "sell", "sell-a",
                 "live", 0, 9_101, 0),
            )
            conn.execute(
                "INSERT INTO paper_copy_sell_quotes(trade_id, recorded_ts, "
                "observed_ts, mint, pool, price_sol, market_cap_sol, slot) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (cursor.lastrowid, 9_101, 9_105, "mint-a", "curve",
                 1e-7, 100, 123),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.now
        ):
            report = app.paper_copy_lifecycle_readiness(
                9_000, x_app_token="test-token"
            )
        self.assertEqual(
            report["counts"]["signals_with_all_eligible_sell_quotes"], 1
        )
        self.assertFalse(report["can_estimate_full_lifecycle_pnl"])
        conn = app.db()
        try:
            conn.execute("DELETE FROM paper_copy_sell_quotes")
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.now
        ):
            unquoted = app.paper_copy_lifecycle_readiness(
                9_000, x_app_token="test-token"
            )
        self.assertEqual(
            unquoted["counts"]["signals_with_unquoted_eligible_sell_after_30s"],
            1,
        )
