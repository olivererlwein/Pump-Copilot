import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from scripts.validate_frozen_exit_candidate import (
    build_prospective_rows,
    parse_cutoff,
    prospective_report,
    validate_frozen_population,
    verify_file_sha256,
)


class FakePipeline:
    def predict_proba(self, matrix):
        probabilities = np.asarray([0.8] * len(matrix))
        return np.column_stack((1 - probabilities, probabilities))


class FrozenExitCandidateTests(unittest.TestCase):
    def test_cutoff_requires_timezone(self):
        with self.assertRaises(ValueError):
            parse_cutoff("2026-09-22T20:58:47")

    def test_only_post_freeze_rows_with_paths_are_scored(self):
        schema = {
            "categorical_features": ["trader"],
            "numeric_features": ["size_score"],
        }
        rows = [
            {"signal_id": 1, "signal_ts": 99, "trader": "old", "size_score": 1},
            {"signal_id": 2, "signal_ts": 101, "trader": "new", "size_score": 2},
            {"signal_id": 3, "signal_ts": 102, "trader": "missing", "size_score": 3},
        ]
        paths = [{
            "signal_id": 2,
            "price_at_signal": 1.0,
            "checkpoint_path": [
                {"checkpoint_seconds": 900, "price_sol": 1.1}
            ],
            "event_path": [
                {"elapsed_seconds": 20, "price_sol": 1.05}
            ],
            "subscription_coverage": {
                "coverage_ratio": 1.0,
                "complete": True,
            },
        }]

        result, coverage = build_prospective_rows(
            rows, paths, FakePipeline(), schema, 100, {1}
        )

        self.assertEqual([row["signal_id"] for row in result], [2])
        self.assertEqual(coverage["future_dataset_rows"], 2)
        self.assertEqual(coverage["missing_paths"], 1)
        self.assertEqual(len(result[0]["event_path"]), 1)
        self.assertTrue(result[0]["subscription_coverage"]["complete"])

    def test_frozen_population_must_not_cross_cutoff(self):
        rows = [{"signal_id": 1, "signal_ts": 101}]

        with self.assertRaisesRegex(ValueError, "newer than the cutoff"):
            validate_frozen_population(rows, 100)

    def test_frozen_dataset_hash_mismatch_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frozen.json"
            path.write_bytes(b"frozen population")

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_file_sha256(str(path), "0" * 64)

    def test_prospective_rows_must_not_overlap_frozen_ids(self):
        schema = {
            "categorical_features": ["trader"],
            "numeric_features": ["size_score"],
        }
        rows = [{
            "signal_id": 7,
            "signal_ts": 101,
            "trader": "overlap",
            "size_score": 1,
        }]

        with self.assertRaisesRegex(ValueError, "overlaps frozen"):
            build_prospective_rows(
                rows, [], FakePipeline(), schema, 100, {7}
            )

    def test_negative_candidate_is_not_ready_for_review(self):
        rows = [{
            "signal_id": 1,
            "signal_ts": 101,
            "trader": "test",
            "mint": "mint",
            "probability": 0.8,
            "price_at_signal": 1.0,
            "path": [{"checkpoint_seconds": 900, "price_sol": 0.8}],
            "ambiguous": False,
        }]

        report = prospective_report(rows)

        self.assertFalse(report["ready_for_review"])
        self.assertIn("MEAN_CI95_DOES_NOT_EXCLUDE_ZERO", report["blockers"])
        self.assertIn("LEAVE_BEST_OUT_NOT_POSITIVE", report["blockers"])

    def test_reports_tp100_and_time_exit_shares(self):
        rows = [
            {
                "signal_id": 1,
                "signal_ts": 101,
                "trader": "test",
                "mint": "tp100",
                "probability": 0.8,
                "price_at_signal": 1.0,
                "path": [{"checkpoint_seconds": 10, "price_sol": 2.0}],
                "ambiguous": False,
            },
            {
                "signal_id": 2,
                "signal_ts": 1001,
                "trader": "test",
                "mint": "time",
                "probability": 0.8,
                "price_at_signal": 1.0,
                "path": [{"checkpoint_seconds": 900, "price_sol": 1.1}],
                "ambiguous": False,
            },
        ]

        report = prospective_report(rows)

        self.assertEqual(report["reached_tp100"], 1)
        self.assertEqual(report["reached_tp100_share"], 0.5)
        self.assertEqual(report["closed_by_time"], 1)
        self.assertEqual(report["closed_by_time_share"], 0.5)
        self.assertEqual(report["ambiguous_share"], 0.0)
        self.assertEqual(report["unambiguous_sensitivity"]["positions"], 2)

    def test_event_path_sensitivity_does_not_change_primary_result(self):
        row = {
            "signal_id": 1,
            "signal_ts": 101,
            "trader": "test",
            "mint": "mint",
            "probability": 0.8,
            "price_at_signal": 1.0,
            "path": [{"checkpoint_seconds": 900, "price_sol": 2.0}],
            "event_path": [
                {"elapsed_seconds": 30, "price_sol": 0.75}
            ],
            "subscription_coverage": {
                "coverage_ratio": 1.0,
                "complete": True,
            },
            "ambiguous": True,
        }

        report = prospective_report([row])

        self.assertGreater(report["overall"]["net"], 0)
        sensitivity = report["event_path_sensitivity"]
        self.assertEqual(sensitivity["rows_with_event_path"], 1)
        self.assertGreater(sensitivity["fixed_path_result"]["net"], 0)
        self.assertLess(sensitivity["dense_path_result"]["net"], 0)
        self.assertLess(sensitivity["event_sequence_result"]["net"], 0)
        coverage = sensitivity["coverage"]
        self.assertTrue(coverage["completeness_proven"])
        self.assertEqual(coverage["rows_measured"], 1)
        self.assertEqual(coverage["rows_complete"], 1)
        self.assertEqual(coverage["selected_rows_complete"], 1)
        self.assertLess(
            coverage["complete_event_sequence_result"]["net"], 0
        )
        self.assertFalse(report["ready_for_review"])

    def test_partial_subscription_coverage_is_not_claimed_complete(self):
        row = {
            "signal_id": 1,
            "signal_ts": 101,
            "trader": "test",
            "mint": "mint",
            "probability": 0.8,
            "price_at_signal": 1.0,
            "path": [{"checkpoint_seconds": 900, "price_sol": 1.1}],
            "event_path": [
                {"elapsed_seconds": 30, "price_sol": 1.05}
            ],
            "subscription_coverage": {
                "coverage_ratio": 0.9,
                "complete": False,
            },
            "ambiguous": False,
        }

        coverage = prospective_report([row])["event_path_sensitivity"][
            "coverage"
        ]

        self.assertFalse(coverage["completeness_proven"])
        self.assertEqual(coverage["rows_measured"], 1)
        self.assertEqual(coverage["rows_complete"], 0)
        self.assertEqual(coverage["selected_rows_complete"], 0)
        self.assertIsNone(coverage["complete_event_sequence_result"])
        self.assertEqual(coverage["median_subscription_coverage_ratio"], 0.9)


if __name__ == "__main__":
    unittest.main()
