import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from scripts.validate_frozen_exit_candidate import (
    build_prospective_rows,
    parse_cutoff,
    prospective_report,
    selected_coverage_attribution,
    selected_subscription_gap_diagnostics,
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
                "measurement_available": True,
                "coverage_ratio": 1.0,
                "intervals": 1,
                "subscription_continuous": True,
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
        self.assertIn(
            "COMPLETE_COVERAGE_SELECTIONS 0/100", report["blockers"]
        )

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
                "measurement_available": True,
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
        self.assertEqual(coverage["selected_rows_measured"], 1)
        self.assertFalse(coverage["selected_priority_reserve_trace_available"])
        self.assertIsNone(coverage["selected_rows_priority_reserve_exposed"])
        self.assertIsNone(coverage["selected_rows_mint_cap_exposed"])
        self.assertEqual(coverage["selected_rows_incomplete"], 0)
        self.assertEqual(coverage["selected_rows_unmeasured"], 0)
        self.assertEqual(coverage["selected_rows_no_subscription_interval"], 0)
        self.assertEqual(coverage["selected_median_coverage_ratio"], 1.0)
        self.assertEqual(coverage["minimum_selected_for_review"], 100)
        self.assertLess(
            coverage["complete_event_sequence_result"]["net"], 0
        )
        self.assertIn(
            "COMPLETE_COVERAGE_SELECTIONS 1/100", report["blockers"]
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
                "measurement_available": True,
                "coverage_ratio": 0.9,
                "intervals": 1,
                "subscription_continuous": False,
                "priority_reserve_trace_available": True,
                "priority_reserve_rejections": 3,
                "mint_cap_rejections": 2,
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
        self.assertEqual(coverage["selected_rows_measured"], 1)
        self.assertEqual(coverage["selected_rows_incomplete"], 1)
        self.assertEqual(coverage["selected_rows_unmeasured"], 0)
        self.assertEqual(coverage["selected_rows_known_delivery_loss"], 0)
        self.assertEqual(coverage["selected_rows_priority_reserve_exposed"], 1)
        self.assertTrue(coverage["selected_priority_reserve_trace_available"])
        self.assertEqual(coverage["selected_priority_reserve_trace_rows"], 1)
        self.assertEqual(coverage["selected_priority_reserve_rejections"], 3)
        self.assertEqual(
            coverage["selected_priority_reserve_exposed_by_mint"],
            {"mint": 1},
        )
        self.assertEqual(
            coverage["selected_priority_reserve_rejections_by_mint"],
            {"mint": 3},
        )
        self.assertEqual(coverage["selected_mint_cap_trace_rows"], 1)
        self.assertEqual(coverage["selected_rows_mint_cap_exposed"], 1)
        self.assertEqual(coverage["selected_mint_cap_rejections"], 2)
        self.assertEqual(
            coverage["selected_mint_cap_rejections_by_mint"],
            {"mint": 2},
        )
        self.assertEqual(coverage["selected_rows_no_subscription_interval"], 0)
        self.assertEqual(coverage["selected_rows_subscription_gap"], 1)
        self.assertEqual(coverage["selected_median_coverage_ratio"], 0.9)
        self.assertIsNone(coverage["complete_event_sequence_result"])
        self.assertEqual(coverage["median_subscription_coverage_ratio"], 0.9)

        split = prospective_report([row])["mint_cap_exposure_sensitivity"]
        self.assertIsNone(split["unexposed"])
        self.assertEqual(split["exposed"]["positions"], 1)

    def test_separates_activation_overlap_from_new_missing_subscriptions(self):
        def selected_row(signal_id, signal_ts, interval_count):
            return {
                "signal_id": signal_id,
                "signal_ts": signal_ts,
                "trader": "test",
                "mint": f"mint-{signal_id}",
                "probability": 0.8,
                "price_at_signal": 1.0,
                "path": [{"checkpoint_seconds": 900, "price_sol": 1.1}],
                "event_path": [{"elapsed_seconds": 30, "price_sol": 1.05}],
                "subscription_coverage": {
                    "measurement_available": True,
                    "measurement_started_ts": 1000,
                    "coverage_ratio": 0.1 if interval_count else 0.0,
                    "intervals": interval_count,
                    "subscription_continuous": False,
                    "complete": False,
                },
                "ambiguous": False,
            }

        rows = [
            selected_row(1, 990, 0),
            selected_row(2, 1000, 0),
            selected_row(3, 1010, 1),
        ]
        coverage = prospective_report(rows)["event_path_sensitivity"][
            "coverage"
        ]

        self.assertEqual(coverage["selected_rows_measured"], 3)
        self.assertEqual(coverage["selected_rows_overlapping_activation"], 1)
        self.assertEqual(coverage["selected_rows_post_activation"], 2)
        self.assertEqual(coverage["selected_rows_post_activation_complete"], 0)
        self.assertEqual(coverage["selected_rows_no_subscription_interval"], 2)
        self.assertEqual(coverage["selected_rows_post_activation_no_interval"], 1)

    def test_subscription_gap_diagnostics_preserves_missing_timing(self):
        row = {
            "signal_id": 5,
            "signal_ts": 1000,
            "trader": "test",
            "mint": "mint",
            "probability": 0.8,
            "subscription_coverage": {
                "measurement_available": True,
                "intervals": 0,
            },
            "ingest_trace": {
                "transport": "helius",
                "source": "live",
                "wss_received_ts": 1004,
                "next_subscription_start_ts": 2000,
            },
        }
        diagnostics = selected_subscription_gap_diagnostics([row], 0.45)

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["wss_received_delay_seconds"], 4.0)
        self.assertEqual(
            diagnostics[0]["next_subscription_start_seconds"], 1000.0
        )
        self.assertIsNone(diagnostics[0]["inbox_received_delay_seconds"])
        self.assertEqual(
            selected_subscription_gap_diagnostics(
                [{**row, "probability": 0.2}], 0.45
            ),
            [],
        )

    def test_coverage_attribution_separates_traders_and_overlapping_causes(self):
        rows = [
            {
                "signal_id": 1, "trader": "alpha", "mint": "hot",
                "probability": 0.8,
                "subscription_coverage": {
                    "measurement_available": True, "complete": False,
                    "known_delivery_failures": 2, "mint_cap_rejections": 2,
                    "priority_reserve_trace_available": True,
                    "intervals": 1, "subscription_continuous": False,
                },
            },
            {
                "signal_id": 2, "trader": "beta", "mint": "hot",
                "probability": 0.8,
                "subscription_coverage": {
                    "measurement_available": True, "complete": True,
                    "intervals": 1, "subscription_continuous": True,
                },
            },
            {
                "signal_id": 3, "trader": "alpha", "mint": "cold",
                "probability": 0.8,
                "subscription_coverage": {
                    "measurement_available": False,
                },
            },
            {
                "signal_id": 4, "trader": "alpha", "mint": "ignored",
                "probability": 0.2,
            },
            {
                "signal_id": 5, "trader": "gamma", "mint": "other",
                "probability": 0.8,
                "subscription_coverage": {
                    "measurement_available": True, "complete": False,
                    "known_delivery_failures": 2, "mint_cap_rejections": 1,
                    "priority_reserve_trace_available": True,
                    "intervals": 1, "subscription_continuous": True,
                },
            },
        ]

        result = selected_coverage_attribution(rows, 0.45)

        self.assertEqual(result["by_trader"]["alpha"]["selected"], 2)
        self.assertEqual(result["by_trader"]["alpha"]["known_delivery_loss"], 1)
        self.assertEqual(result["by_trader"]["alpha"]["subscription_gap"], 1)
        self.assertEqual(result["by_trader"]["alpha"]["unmeasured"], 1)
        self.assertEqual(result["by_trader"]["beta"]["complete"], 1)
        self.assertEqual(result["by_trader"]["gamma"]["other_delivery_loss"], 1)
        self.assertNotIn("other_delivery_loss", result["by_trader"]["alpha"])
        self.assertEqual(result["by_mint"]["hot"]["selected"], 2)
        self.assertEqual(len(result["incomplete_signals"]), 3)
        self.assertEqual(
            result["incomplete_signals"][0]["mint_cap_rejections"], 2
        )
        self.assertNotIn("ignored", result["by_mint"])

    def test_missing_cap_trace_is_not_counted_as_unexposed(self):
        base = {
            "signal_ts": 1000, "trader": "test", "probability": 0.8,
            "price_at_signal": 1.0,
            "path": [{"checkpoint_seconds": 900, "price_sol": 1.1}],
            "ambiguous": False,
        }
        rows = [
            {
                **base, "signal_id": 1, "mint": "known",
                "subscription_coverage": {
                    "measurement_available": True, "complete": True,
                    "mint_cap_rejections": 0,
                },
            },
            {
                **base, "signal_id": 2, "mint": "unknown",
                "subscription_coverage": {
                    "measurement_available": True, "complete": False,
                    "mint_cap_rejections": None,
                    "known_delivery_failures": 1,
                    "priority_reserve_trace_available": True,
                },
            },
        ]

        split = prospective_report(rows)["mint_cap_exposure_sensitivity"]
        attribution = selected_coverage_attribution(rows, 0.45)

        self.assertEqual(split["unexposed"]["positions"], 1)
        self.assertEqual(split["unexposed"]["unique_mints"], 1)
        self.assertIsNone(split["exposed"])
        self.assertEqual(split["selected_without_cap_trace"], 1)
        self.assertEqual(
            attribution["by_trader"]["test"]["cap_trace_unavailable"], 1
        )
        self.assertEqual(
            attribution["by_trader"]["test"]["unclassified_delivery_loss"], 1
        )
        self.assertNotIn("other_delivery_loss", attribution["by_trader"]["test"])


if __name__ == "__main__":
    unittest.main()
