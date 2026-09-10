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
        tp25_hits=0,
        tp50_hits=0,
        sl10_hits=0,
    ):
        conn = app.db()

        rows = []

        for index in range(samples):
            hit_tp25 = 1 if index < tp25_hits else 0
            hit_tp50 = 1 if index < tp50_hits else 0
            hit_sl10 = 1 if index < sl10_hits else 0
            max_return = 30.0 if hit_tp25 else 5.0
            min_return = -12.0 if hit_sl10 else -2.0

            rows.append(
                (
                    index + 1,
                    f"mint-{trader}-{index}",
                    trader,
                    1000.0 + index,
                    1.0,
                    max_return,
                    min_return,
                    hit_tp25,
                    hit_tp50,
                    hit_sl10,
                    "completed",
                    1000.0 + index,
                    1000.0 + index,
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
                status,
                created_ts,
                updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
        conn.close()

    def test_waits_for_enough_samples_before_candidate_moves(self):
        self.insert_outcomes(
            "marcell",
            samples=29,
            tp25_hits=29,
            tp50_hits=29,
        )

        quality = app.get_trader_quality_assessment("marcell")

        self.assertFalse(quality["ready_for_review"])
        self.assertEqual(quality["base_quality"], 25)
        self.assertEqual(quality["candidate_quality"], 25)
        self.assertEqual(quality["effective_quality"], 25)

    def test_strong_history_creates_capped_upgrade_candidate(self):
        self.insert_outcomes(
            "epicsealdarkeye",
            samples=30,
            tp25_hits=24,
            tp50_hits=12,
            sl10_hits=2,
        )

        quality = app.get_trader_quality_assessment("epicsealdarkeye")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["base_quality"], 18)
        self.assertEqual(quality["candidate_quality"], 21)
        self.assertEqual(quality["effective_quality"], 18)

    def test_weak_history_creates_capped_downgrade_candidate(self):
        self.insert_outcomes(
            "hdegroot",
            samples=30,
            tp25_hits=2,
            tp50_hits=0,
            sl10_hits=20,
        )

        quality = app.get_trader_quality_assessment("hdegroot")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["base_quality"], 27)
        self.assertEqual(quality["candidate_quality"], 24)
        self.assertEqual(quality["effective_quality"], 27)

    def test_dynamic_quality_only_changes_score_when_enabled(self):
        self.insert_outcomes(
            "epicsealdarkeye",
            samples=30,
            tp25_hits=24,
            tp50_hits=12,
            sl10_hits=2,
        )

        with patch.object(app, "TRADER_DYNAMIC_QUALITY_ENABLED", False):
            self.assertEqual(app.score_trader("epicsealdarkeye"), 18)

        with patch.object(app, "TRADER_DYNAMIC_QUALITY_ENABLED", True):
            self.assertEqual(app.score_trader("epicsealdarkeye"), 21)

    def test_static_score_does_not_read_dynamic_stats(self):
        with patch.object(
            app,
            "TRADER_DYNAMIC_QUALITY_ENABLED",
            False,
        ), patch.object(
            app,
            "get_trader_quality_assessment",
            side_effect=AssertionError("dynamic stats should not run"),
        ):
            self.assertEqual(app.score_trader("marcell"), 25)
            self.assertEqual(app.score_trader("ily"), 10)


if __name__ == "__main__":
    unittest.main()
