import tempfile
import unittest
from pathlib import Path

import app


class TrainingDataQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "training-quality.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def completed_outcome(self, suffix, source_age_seconds):
        signal_ts = 1_700_000_000.0 + suffix * 2_000
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
                    "live", 60, "WATCH", 10, 10, 10, 10, 10, 10, "[]",
                    100, 1, app.DATA_VERSION,
                ),
            )
            signal_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        outcome_id = app.create_signal_outcome(
            signal_id=signal_id,
            mint=mint,
            trader="tester",
            signal_ts=signal_ts,
            price_at_signal=1.0,
            entry_price_basis="pump",
        )
        observed = [
            signal_ts + 10, signal_ts + 30, signal_ts + 60,
            signal_ts + 300, signal_ts + 900,
        ]
        conn = app.db()
        try:
            conn.execute(
                """
                UPDATE signal_outcomes SET
                    price_10s=1.1, price_30s=1.1, price_1m=1.1,
                    price_5m=1.1, price_15m=1.1,
                    observed_10s_ts=?, observed_30s_ts=?, observed_1m_ts=?,
                    observed_5m_ts=?, observed_15m_ts=?, status='completed'
                WHERE id=?
                """,
                (*observed, outcome_id),
            )
            conn.executemany(
                """
                INSERT INTO token_history(
                    ts, mint, market_cap_sol, trader, side, signature, source
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    (
                        checkpoint_ts - source_age_seconds, mint, 100,
                        "tester", "buy", f"history-{suffix}-{index}", "live",
                    )
                    for index, checkpoint_ts in enumerate(observed)
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return signal_id

    def test_training_export_excludes_outcomes_without_fresh_source_evidence(self):
        fresh_signal = self.completed_outcome(1, source_age_seconds=5)
        self.completed_outcome(2, source_age_seconds=5.01)

        rows = app.get_training_dataset_rows()
        stats = app.get_training_dataset_stats()

        self.assertEqual([row["signal_id"] for row in rows], [fresh_signal])
        self.assertEqual(stats["completed"], 2)
        self.assertEqual(stats["training_eligible"], 1)
        self.assertEqual(stats["excluded_unfresh"], 1)
        self.assertEqual(stats["checkpoint_max_source_age_seconds"], 5.0)


if __name__ == "__main__":
    unittest.main()
