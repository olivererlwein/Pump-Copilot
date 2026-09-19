import json
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

    def checkpoint_dataset_outcome(self, suffix, prices):
        signal_ts = self.signal_ts + suffix * 2_000
        mint = f"mint-{suffix}"
        conn = app.db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO evaluations(
                    trade_signature, event_index, ts, trader, mint, source,
                    score, decision, trader_score, timing_score, size_score,
                    token_score, consensus_score, market_score, reasons,
                    market_cap, sol_amount, data_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"signature-{suffix}", 0, signal_ts, "tester", mint,
                    "helius", 60, "WATCH", 10, 10, 10, 10, 10, 10, "[]",
                    100, 1, app.DATA_VERSION,
                ),
            )
            signal_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        outcome_id = app.create_signal_outcome(
            signal_id=signal_id, mint=mint, trader="tester",
            signal_ts=signal_ts, price_at_signal=1.0,
            entry_price_basis="pump",
        )
        conn = app.db()
        try:
            conn.executemany(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        outcome_id, seconds, mint, "curve", price, 100,
                        signal_ts + seconds + 1, 1000 + seconds,
                    )
                    for (seconds, _grace), price in zip(
                        app.ACCOUNT_PRICE_CHECKPOINT_WINDOWS, prices
                    )
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return signal_id

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
                "UPDATE signal_outcomes SET price_10s=?, observed_10s_ts=? "
                "WHERE id=?",
                (1e-7, self.signal_ts + 13, outcome_id),
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
        recent = stats["recent_comparisons"]
        ten_second = next(
            item for item in recent if item["checkpoint_seconds"] == 10
        )
        self.assertEqual(ten_second["account_lag_seconds"], 2)
        self.assertEqual(ten_second["primary_lag_seconds"], 3)
        self.assertEqual(ten_second["observation_gap_seconds"], 1)
        self.assertAlmostEqual(ten_second["absolute_pct_difference"], 10.0)
        thirty_second = next(
            item for item in recent if item["checkpoint_seconds"] == 30
        )
        self.assertIsNone(thirty_second["primary_observed_ts"])
        self.assertIsNone(thirty_second["observation_gap_seconds"])
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

    def test_shadow_dataset_labels_tp_before_sl_at_fixed_checkpoints(self):
        positive = self.checkpoint_dataset_outcome(
            1, [1.05, 1.30, 1.20, 0.80, 1.10]
        )
        negative = self.checkpoint_dataset_outcome(
            2, [0.85, 1.30, 1.20, 1.10, 1.00]
        )

        rows = app.get_account_checkpoint_dataset_rows()
        by_id = {row["signal_id"]: row for row in rows}

        self.assertEqual(by_id[positive]["target_tp25_before_sl10"], 1)
        self.assertEqual(by_id[positive]["tp_checkpoint_seconds"], 30)
        self.assertEqual(by_id[positive]["sl_checkpoint_seconds"], 300)
        self.assertEqual(by_id[negative]["target_tp25_before_sl10"], 0)
        self.assertEqual(by_id[negative]["sl_checkpoint_seconds"], 10)
        self.assertEqual(by_id[negative]["tp_checkpoint_seconds"], 30)
        self.assertEqual(
            by_id[positive]["label_source"], "account_checkpoints_v1"
        )

        test_minimums = {
            **app.ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS,
            "unique_traders": 1,
        }
        with patch.object(
            app.time,
            "time",
            return_value=self.signal_ts + 1000,
        ), patch.object(
            app,
            "build_model_features",
            side_effect=AssertionError("stats must not build model features"),
        ), patch.object(
            app,
            "ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS",
            test_minimums,
        ):
            stats = app.get_account_checkpoint_training_stats()
        self.assertEqual(stats["complete_rows"], 2)
        self.assertEqual(stats["target_1"], 1)
        self.assertEqual(stats["target_0"], 1)
        self.assertEqual(stats["unique_mints"], 2)
        self.assertEqual(stats["unique_traders"], 1)
        self.assertFalse(stats["affects_decisions"])
        self.assertFalse(stats["readiness"]["ready_for_diagnostic"])
        self.assertEqual(
            stats["readiness"]["minimums"],
            test_minimums,
        )
        self.assertIn("complete_rows 2/300", stats["readiness"]["blockers"])
        self.assertEqual(
            stats["last_24h"],
            {"complete_rows": 2, "target_1": 1, "target_0": 1},
        )
        self.assertEqual(
            stats["readiness"]["estimated_days_at_last_24h_rate"],
            149.0,
        )
        self.assertEqual(app.count_complete_account_checkpoint_paths(), 2)

        with patch.object(app, "APP_TOKEN", "test-token"):
            payload = app.api_account_checkpoint_training_dataset("test-token")
        self.assertEqual(payload["data_version"], app.DATA_VERSION)
        self.assertEqual(payload["label_source"], "account_checkpoints_v1")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["rows"]), 2)

    def test_shadow_dataset_requires_all_five_checkpoints(self):
        signal_id = self.checkpoint_dataset_outcome(
            3, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        conn = app.db()
        try:
            conn.execute(
                "DELETE FROM account_price_checkpoints "
                "WHERE outcome_id=(SELECT id FROM signal_outcomes "
                "WHERE signal_id=?) AND checkpoint_seconds=900",
                (signal_id,),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(app.get_account_checkpoint_dataset_rows(), [])

    def test_training_readiness_minimums_match_diagnostic_schema(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "training"
            / "schema_account_checkpoints_v1.json"
        )
        readiness = json.loads(
            schema_path.read_text(encoding="utf-8")
        )["readiness"]

        self.assertEqual(
            app.ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS,
            {
                "complete_rows": readiness["minimum_completed_rows"],
                "target_0": readiness["minimum_target_0"],
                "target_1": readiness["minimum_target_1"],
                "unique_traders": readiness["minimum_unique_traders"],
            },
        )

    def test_shadow_label_rejects_invalid_checkpoint_price(self):
        with self.assertRaisesRegex(
            ValueError, "ACCOUNT_CHECKPOINT_PRICE_INVALID"
        ):
            app.account_checkpoint_target(1.0, [(10, 0, self.signal_ts + 10)])


if __name__ == "__main__":
    unittest.main()
