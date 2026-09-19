import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AccountPriceCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "checkpoints.db"
        app.migrate_database()
        self.signal_ts = 1_700_000_000.0

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def outcome(self, basis="pump", mint="mint-a"):
        return app.create_signal_outcome(
            signal_id=None, mint=mint, trader="tester",
            signal_ts=self.signal_ts, price_at_signal=1e-7,
            entry_price_basis=basis,
        )

    def test_valid_sol_curve_is_recorded_once_without_touching_primary_outcome(self):
        outcome_id = self.outcome()
        snapshot = {"mint-a": {
            "status": "curve", "price_sol": 1.1e-7,
            "market_cap_sol": 110, "slot": 123,
        }}
        with patch.object(app, "fetch_account_prices", return_value=snapshot) as fetch:
            first = app.account_price_checkpoint_once(now=self.signal_ts + 12)
            second = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(first["checkpoints_recorded"], 1)
        self.assertEqual(second["status"], "no_due_checkpoint")
        fetch.assert_called_once()
        conn = app.db()
        try:
            checkpoint = conn.execute(
                "SELECT outcome_id, checkpoint_seconds, pool, price_sol, slot "
                "FROM account_price_checkpoints"
            ).fetchone()
            primary = conn.execute(
                "SELECT price_10s, status FROM signal_outcomes WHERE id=?",
                (outcome_id,),
            ).fetchone()
            effects = [conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] for table in ("paper_positions", "execution_orders")]
        finally:
            conn.close()
        self.assertEqual(checkpoint, (outcome_id, 10, "curve", 1.1e-7, 123))
        self.assertEqual(primary, (None, "active"))
        self.assertEqual(effects, [0, 0])

    def test_unsupported_quote_is_counted_without_price(self):
        self.outcome()
        with patch.object(app, "fetch_account_prices", return_value={
            "mint-a": {"status": "unsupported_quote", "slot": 123},
        }):
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(result["priced_mints"], 0)
        self.assertEqual(result["checkpoints_recorded"], 0)
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 12
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        self.assertFalse(stats["affects_decisions"])
        self.assertFalse(stats["affects_primary_outcomes"])
        self.assertEqual(stats["last_24h"]["unsupported_mints"], 1)

    def test_stats_explain_when_an_eligible_outcome_is_due(self):
        self.outcome(basis="pump", mint="curve-entry")
        self.outcome(basis="unknown", mint="unknown-entry")
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 12
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        eligibility = stats["eligibility"]
        self.assertEqual(eligibility["active_outcomes_in_window"], 1)
        self.assertEqual(eligibility["due_now"], 1)
        self.assertEqual(
            eligibility["entry_price_basis_last_24h"],
            {"pump": 1, "unknown": 1},
        )
        self.assertEqual(
            eligibility["latest_outcome"]["entry_price_basis"], "unknown"
        )
        self.assertEqual(eligibility["latest_outcome"]["age_seconds"], 12)

    def test_stats_compare_account_and_primary_prices_without_promoting_them(self):
        outcome_id = self.outcome(basis="pump", mint="curve-entry")
        conn = app.db()
        try:
            conn.execute(
                "UPDATE signal_outcomes SET price_10s=? WHERE id=?",
                (1e-7, outcome_id),
            )
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, 10, "curve-entry", "curve", 1.1e-7,
                    110, self.signal_ts + 12, 123,
                ),
            )
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, 30, "curve-entry", "curve", 1.2e-7,
                    120, self.signal_ts + 32, 124,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 40
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        comparison = stats["comparison_last_24h"]
        self.assertEqual(comparison["10"]["primary_available"], 1)
        self.assertAlmostEqual(
            comparison["10"]["median_absolute_pct_difference"], 10.0
        )
        self.assertEqual(comparison["30"]["primary_missing"], 1)
        self.assertIsNone(
            comparison["30"]["median_absolute_pct_difference"]
        )
        conn = app.db()
        try:
            primary = conn.execute(
                "SELECT price_10s, price_30s FROM signal_outcomes WHERE id=?",
                (outcome_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(primary, (1e-7, None))

    def test_amm_entry_and_missed_checkpoint_are_not_backfilled(self):
        self.outcome(basis="unknown", mint="amm-entry")
        with patch.object(app, "fetch_account_prices") as fetch:
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(result["status"], "no_due_checkpoint")
        fetch.assert_not_called()
        self.outcome(basis="pump", mint="curve-entry")
        with patch.object(app, "fetch_account_prices") as fetch:
            result = app.account_price_checkpoint_once(now=self.signal_ts + 16)
        self.assertEqual(result["status"], "no_due_checkpoint")
        fetch.assert_not_called()

    def test_late_rpc_response_does_not_fill_old_checkpoint(self):
        self.outcome()
        with patch.object(app, "fetch_account_prices", return_value={
            "mint-a": {"status": "curve", "price_sol": 1e-7,
                       "market_cap_sol": 100, "slot": 123},
        }), patch.object(app.time, "time", side_effect=[
            self.signal_ts + 12, self.signal_ts + 16,
        ]):
            result = app.account_price_checkpoint_once(now=None)
        self.assertEqual(result["checkpoints_recorded"], 0)


if __name__ == "__main__":
    unittest.main()
