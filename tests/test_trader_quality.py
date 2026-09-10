import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TraderQualityCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "trader-quality.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insert_outcomes(
        self,
        trader,
        *,
        samples,
        target_hits,
        status="completed",
    ):
        conn = app.db()
        rows = []

        for index in range(samples):
            signal_ts = 1000.0 + index
            target_won = index < target_hits
            tp25_ts = signal_ts + (1.0 if target_won else 2.0)
            sl10_ts = signal_ts + (2.0 if target_won else 1.0)

            rows.append(
                (
                    index + 1,
                    f"mint-{trader}-{index}",
                    trader,
                    signal_ts,
                    1.0,
                    30.0,
                    -12.0,
                    1,
                    0,
                    1,
                    tp25_ts,
                    sl10_ts,
                    status,
                    signal_ts,
                    signal_ts,
                )
            )

        conn.executemany(
            """
            INSERT INTO signal_outcomes(
                signal_id,
                mint,
                trader,
                signal_ts,
                price_at_signal,
                max_return,
                min_return,
                hit_tp25,
                hit_tp50,
                hit_sl10,
                tp25_ts,
                sl10_ts,
                status,
                created_ts,
                updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
        conn.close()

    def test_every_trader_starts_from_the_same_neutral_quality(self):
        with patch.object(app, "TRADER_DYNAMIC_QUALITY_ENABLED", False):
            self.assertEqual(app.score_trader("marcell"), 15)
            self.assertEqual(app.score_trader("hdegroot"), 15)
            self.assertEqual(app.score_trader("new-trader"), 15)
            self.assertEqual(app.score_trader("ily"), 10)

    def test_waits_for_enough_completed_samples(self):
        self.insert_outcomes(
            "marcell",
            samples=29,
            target_hits=29,
        )

        quality = app.get_trader_quality_assessment("marcell")

        self.assertFalse(quality["ready_for_review"])
        self.assertEqual(quality["base_quality"], 15)
        self.assertEqual(quality["candidate_quality"], 15)
        self.assertEqual(quality["effective_quality"], 15)

    def test_strong_target_history_raises_quality(self):
        self.insert_outcomes(
            "epicsealdarkeye",
            samples=30,
            target_hits=24,
        )

        quality = app.get_trader_quality_assessment("epicsealdarkeye")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["target_1"], 24)
        self.assertEqual(quality["target_0"], 6)
        self.assertEqual(quality["base_quality"], 15)
        self.assertEqual(quality["candidate_quality"], 21)
        self.assertEqual(quality["effective_quality"], 21)

    def test_weak_target_history_lowers_quality(self):
        self.insert_outcomes(
            "hdegroot",
            samples=30,
            target_hits=2,
        )

        quality = app.get_trader_quality_assessment("hdegroot")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["candidate_quality"], 10)
        self.assertEqual(quality["effective_quality"], 10)

    def test_tp25_only_counts_when_it_precedes_sl10(self):
        self.insert_outcomes(
            "ordered-results",
            samples=30,
            target_hits=7,
        )

        stats = app.get_trader_hit_stats("ordered-results")

        self.assertEqual(stats["tp25_hits"], 30)
        self.assertEqual(stats["sl10_hits"], 30)
        self.assertEqual(stats["target_1"], 7)
        self.assertEqual(stats["target_0"], 23)

    def test_incomplete_outcomes_do_not_affect_quality(self):
        self.insert_outcomes(
            "unfinished",
            samples=30,
            target_hits=30,
            status="active",
        )

        quality = app.get_trader_quality_assessment("unfinished")

        self.assertEqual(quality["samples"], 0)
        self.assertFalse(quality["ready_for_review"])
        self.assertEqual(quality["effective_quality"], 15)

    def test_repeated_signals_for_one_mint_count_as_one_sample(self):
        self.insert_outcomes(
            "repeater",
            samples=30,
            target_hits=1,
        )

        conn = app.db()
        conn.execute(
            "UPDATE signal_outcomes SET mint = ? WHERE trader = ?",
            ("same-mint", "repeater"),
        )
        conn.commit()
        conn.close()

        stats = app.get_trader_hit_stats("repeater")

        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["target_1"], 1)


if __name__ == "__main__":
    unittest.main()
