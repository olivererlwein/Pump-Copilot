import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class FakeShadowModel:
    data_version = 2

    def __init__(self, model_version="test-model-v1"):
        self.model_version = model_version

    def predict(self, features):
        probability = float(features.get("probability", 0.8))
        return {
            "model_version": self.model_version,
            "data_version": self.data_version,
            "probability": probability,
            "threshold": 0.5,
            "predicted_target": int(probability >= 0.5),
        }


class ShadowPredictionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        self.original_enabled = app.SHADOW_MODE_ENABLED
        self.original_model = app.SHADOW_MODEL
        self.original_error = app.SHADOW_MODEL_LAST_ERROR
        self.original_challenger = getattr(
            app,
            "SHADOW_CHALLENGER_MODEL",
            None,
        )
        self.original_challenger_error = getattr(
            app,
            "SHADOW_CHALLENGER_LAST_ERROR",
            "",
        )
        app.DB = Path(self.temp_dir.name) / "shadow-test.db"
        app.SHADOW_MODE_ENABLED = True
        app.SHADOW_MODEL = FakeShadowModel()
        app.SHADOW_MODEL_LAST_ERROR = ""
        app.SHADOW_CHALLENGER_MODEL = None
        app.SHADOW_CHALLENGER_LAST_ERROR = ""

        conn = app.db()
        cursor = conn.execute(
            """
            INSERT INTO evaluations(
                trade_signature,
                ts,
                trader,
                mint,
                decision
            )
            VALUES(?,?,?,?,?)
            """,
            ("signature-1", 1.0, "trader-a", "mint-a", "WATCH"),
        )
        self.evaluation_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO signal_outcomes(
                signal_id,
                mint,
                trader,
                signal_ts,
                status,
                created_ts,
                updated_ts
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                self.evaluation_id,
                "mint-a",
                "trader-a",
                1.0,
                "active",
                1.0,
                1.0,
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        app.DB = self.original_db
        app.SHADOW_MODE_ENABLED = self.original_enabled
        app.SHADOW_MODEL = self.original_model
        app.SHADOW_MODEL_LAST_ERROR = self.original_error
        app.SHADOW_CHALLENGER_MODEL = self.original_challenger
        app.SHADOW_CHALLENGER_LAST_ERROR = self.original_challenger_error
        self.temp_dir.cleanup()

    def test_records_once_and_reconciles_completed_outcome(self):
        first = app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.8},
        )
        app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.2},
        )

        self.assertEqual(first["predicted_target"], 1)
        self.assertEqual(len(app.get_shadow_predictions()), 1)
        self.assertEqual(app.get_shadow_stats()["pending"], 1)

        conn = app.db()
        conn.execute(
            """
            UPDATE signal_outcomes
            SET status = 'completed', tp25_ts = 2.0
            WHERE signal_id = ?
            """,
            (self.evaluation_id,),
        )
        conn.commit()
        conn.close()

        stats = app.get_shadow_stats()
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["precision"], 1.0)
        self.assertEqual(stats["recall"], 1.0)
        self.assertEqual(stats["confusion_matrix"], [[0, 0], [0, 1]])

    def test_prediction_failure_does_not_escape(self):
        class BrokenModel:
            def predict(self, features):
                raise RuntimeError("broken model")

        app.SHADOW_MODEL = BrokenModel()

        result = app.record_shadow_prediction(
            self.evaluation_id,
            {},
        )

        self.assertIsNone(result)
        self.assertEqual(app.SHADOW_MODEL_LAST_ERROR, "broken model")
        self.assertEqual(app.get_shadow_predictions(), [])

    def test_records_incumbent_and_challenger_once_per_signal(self):
        app.SHADOW_CHALLENGER_MODEL = FakeShadowModel("test-model-v2")

        app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.8},
        )
        app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.2},
        )

        rows = app.get_shadow_predictions()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["model_version"] for row in rows},
            {"test-model-v1", "test-model-v2"},
        )
        self.assertEqual(
            set(app.get_shadow_stats()["models"]),
            {"test-model-v1", "test-model-v2"},
        )

    def test_challenger_failure_does_not_block_incumbent(self):
        class BrokenModel:
            model_version = "broken-challenger"

            def predict(self, features):
                raise RuntimeError("challenger failed")

        app.SHADOW_CHALLENGER_MODEL = BrokenModel()

        result = app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.8},
        )

        self.assertEqual(result["model_version"], "test-model-v1")
        self.assertEqual(len(app.get_shadow_predictions()), 1)
        self.assertEqual(
            app.SHADOW_CHALLENGER_LAST_ERROR,
            "challenger failed",
        )

    def test_loader_keeps_unapproved_candidate_out_of_incumbent_slot(self):
        def artifact(version, role, deployment_ready):
            return {
                "format_version": 1,
                "data_version": 2,
                "model_version": version,
                "artifact_role": role,
                "deployment_ready": deployment_ready,
                "deployment_blockers": (
                    [] if deployment_ready else ["holdout concentration"]
                ),
                "threshold": 0.5,
                "categorical": [],
                "numeric": {
                    "features": [],
                    "medians": [],
                    "indicator_indexes": [],
                    "mean": [],
                    "scale": [],
                },
                "coefficients": [],
                "intercept": 0.0,
            }

        incumbent_path = Path(self.temp_dir.name) / "incumbent.json"
        challenger_path = Path(self.temp_dir.name) / "challenger.json"
        incumbent_path.write_text(
            json.dumps(artifact("incumbent-v1", "incumbent", True)),
            encoding="utf-8",
        )
        challenger_path.write_text(
            json.dumps(artifact("candidate-v1", "challenger", False)),
            encoding="utf-8",
        )

        with patch.object(
            app, "SHADOW_MODEL_PATH", challenger_path
        ), patch.object(
            app, "SHADOW_CHALLENGER_MODEL_PATH", incumbent_path
        ):
            self.assertIsNone(app.load_shadow_model())
            self.assertIsNone(app.SHADOW_CHALLENGER_MODEL)
            self.assertIn(
                "challenger",
                app.SHADOW_MODEL_LAST_ERROR.lower(),
            )

        with patch.object(
            app, "SHADOW_MODEL_PATH", incumbent_path
        ), patch.object(
            app, "SHADOW_CHALLENGER_MODEL_PATH", challenger_path
        ):
            loaded = app.load_shadow_model()
            self.assertEqual(loaded.model_version, "incumbent-v1")
            self.assertEqual(
                app.SHADOW_CHALLENGER_MODEL.model_version,
                "candidate-v1",
            )
            self.assertFalse(
                app.SHADOW_CHALLENGER_MODEL.deployment_ready
            )

    def test_stats_keep_model_versions_separate(self):
        class NegativeModel(FakeShadowModel):
            def predict(self, features):
                return {
                    "model_version": self.model_version,
                    "data_version": self.data_version,
                    "probability": 0.2,
                    "threshold": 0.5,
                    "predicted_target": 0,
                }

        app.SHADOW_CHALLENGER_MODEL = NegativeModel("test-model-v2")
        app.record_shadow_prediction(
            self.evaluation_id,
            {"probability": 0.8},
        )
        conn = app.db()
        conn.execute(
            """
            UPDATE signal_outcomes
            SET status = 'completed', tp25_ts = 2.0
            WHERE signal_id = ?
            """,
            (self.evaluation_id,),
        )
        conn.commit()
        conn.close()

        stats = app.get_shadow_stats()

        self.assertEqual(stats["precision"], 1.0)
        self.assertEqual(stats["models"]["test-model-v1"]["recall"], 1.0)
        self.assertEqual(stats["models"]["test-model-v2"]["recall"], 0.0)

    def test_feature_failure_does_not_escape(self):
        with patch.object(
            app,
            "build_model_features",
            side_effect=RuntimeError("feature lookup failed"),
        ):
            result = app.observe_shadow_signal(self.evaluation_id)

        self.assertIsNone(result)
        self.assertEqual(
            app.SHADOW_MODEL_LAST_ERROR,
            "feature lookup failed",
        )
        self.assertEqual(app.get_shadow_predictions(), [])

    def test_feature_snapshot_uses_only_information_available_at_signal(self):
        conn = app.db()
        conn.executemany(
            """
            INSERT INTO trades(
                ts,
                trader,
                side,
                mint,
                sol,
                market_cap_sol,
                signature
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            [
                (10.0, "creator", "create", "mint-a", 0.1, 10.0, "a"),
                (80.0, "trader-a", "buy", "mint-a", 1.0, 20.0, "b"),
                (100.0, "trader-a", "buy", "mint-a", 2.0, 25.0, "c"),
                (101.0, "future", "buy", "mint-a", 3.0, 30.0, "d"),
            ],
        )
        conn.commit()
        conn.close()

        features = app.build_model_features(
            trader="trader-a",
            mint="mint-a",
            signal_ts=100.0,
            trader_score=20,
            timing_score=15,
            size_score=10,
            token_score=12,
            consensus_score=5,
            market_score=5,
            score_total=67,
            market_cap=25.0,
            sol_amount=2.0,
            price_at_signal=0.0001,
        )

        self.assertEqual(features["token_age_seconds"], 90.0)
        self.assertEqual(
            features["trader_previous_buy_gap_seconds"],
            20.0,
        )
        self.assertEqual(features["trader_recent_buy_count_60s"], 1)
        self.assertEqual(features["consensus_trader_count_30s"], 1)
        self.assertEqual(features["consensus_trader_count"], 2)
        self.assertEqual(features["buy_size_pct_mc"], 8.0)


class ShadowPredictionMigrationTests(unittest.TestCase):
    def test_migrates_single_model_unique_constraint_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_db = app.DB
            app.DB = Path(temp_dir) / "shadow-migration.db"
            try:
                conn = sqlite3.connect(app.DB)
                conn.execute(
                    """
                    CREATE TABLE model_shadow_predictions(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        evaluation_id INTEGER NOT NULL UNIQUE,
                        created_ts REAL NOT NULL,
                        model_version TEXT NOT NULL,
                        data_version INTEGER NOT NULL,
                        probability REAL NOT NULL,
                        threshold REAL NOT NULL,
                        predicted_target INTEGER NOT NULL,
                        features_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO model_shadow_predictions(
                        evaluation_id, created_ts, model_version,
                        data_version, probability, threshold,
                        predicted_target, features_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (1, 1.0, "old-model", 2, 0.8, 0.5, 1, "{}"),
                )
                conn.commit()
                conn.close()

                app.migrate_database()
                conn = sqlite3.connect(app.DB)
                rows = conn.execute(
                    """
                    SELECT evaluation_id, model_version
                    FROM model_shadow_predictions
                    """
                ).fetchall()
                conn.execute(
                    """
                    INSERT INTO model_shadow_predictions(
                        evaluation_id, created_ts, model_version,
                        data_version, probability, threshold,
                        predicted_target, features_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (1, 2.0, "new-model", 2, 0.7, 0.5, 1, "{}"),
                )
                conn.commit()
                count = conn.execute(
                    "SELECT COUNT(*) FROM model_shadow_predictions"
                ).fetchone()[0]
                conn.close()

                self.assertEqual(rows, [(1, "old-model")])
                self.assertEqual(count, 2)
            finally:
                app.DB = original_db


if __name__ == "__main__":
    unittest.main()
