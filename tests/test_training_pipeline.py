import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import numpy as np

from shadow_model import ShadowLogisticModel
from scripts.train_baseline_model import (
    build_matrix,
    build_pipeline,
    build_shadow_artifact,
    classification_metrics,
    get_deployment_blockers,
    group_balanced_sample_weights,
    main,
    select_threshold,
    summarize_partition,
    temporal_group_split,
    temporal_group_validation_folds,
    temporal_window_report,
    trader_balanced_sample_weights,
    validate_dataset,
)


def make_schema(nullable=None):
    return {
        "data_version": 2,
        "metadata_columns": ["signal_id", "signal_ts", "mint"],
        "categorical_features": ["trader"],
        "numeric_features": ["feature"],
        "nullable_features": nullable or [],
        "target": "target",
        "time_column": "signal_ts",
        "split_group": "mint",
        "deployment_readiness": {
            "minimum_holdout_target_0": 2,
            "minimum_holdout_target_1": 2,
            "minimum_holdout_target_0_groups": 2,
            "minimum_holdout_target_1_groups": 2,
            "maximum_holdout_group_share": 0.5,
        },
    }


def make_row(index, mint, target, feature=1.0):
    return {
        "signal_id": index + 1,
        "signal_ts": float(index),
        "mint": mint,
        "trader": "trader-a",
        "feature": feature,
        "target": target,
    }


class TrainingDatasetValidationTests(unittest.TestCase):
    def test_allows_declared_nullable_feature(self):
        rows = [
            make_row(0, "mint-a", 0, feature=None),
            make_row(1, "mint-b", 1),
        ]
        payload = {
            "data_version": 2,
            "count": len(rows),
            "rows": rows,
        }

        result = validate_dataset(
            payload,
            make_schema(nullable=["feature"]),
        )

        self.assertEqual(result, rows)

    def test_rejects_unexpected_null_feature(self):
        rows = [make_row(0, "mint-a", 0, feature=None)]
        payload = {
            "data_version": 2,
            "count": len(rows),
            "rows": rows,
        }

        with self.assertRaisesRegex(ValueError, "Unexpected null"):
            validate_dataset(payload, make_schema())


class TemporalGroupSplitTests(unittest.TestCase):
    def test_purges_early_rows_from_groups_in_test_window(self):
        groups = [
            "mint-a",
            "mint-b",
            "mint-late",
            "mint-c",
            "mint-d",
            "mint-e",
            "mint-f",
            "mint-g",
            "mint-h",
            "mint-late",
        ]
        targets = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
        rows = [
            make_row(index, mint, targets[index])
            for index, mint in enumerate(groups)
        ]

        train, test, purged, cutoff = temporal_group_split(
            rows,
            make_schema(),
            test_fraction=0.2,
        )

        train_mints = {row["mint"] for row in train}
        test_mints = {row["mint"] for row in test}

        self.assertFalse(train_mints & test_mints)
        self.assertEqual([row["signal_ts"] for row in purged], [2.0])
        self.assertLess(
            max(row["signal_ts"] for row in train),
            min(row["signal_ts"] for row in test),
        )
        self.assertEqual(cutoff, 8.0)

    def test_walk_forward_folds_are_temporal_and_group_disjoint(self):
        rows = [
            make_row(
                index,
                f"mint-{index // 2}",
                index % 2,
            )
            for index in range(30)
        ]

        folds = temporal_group_validation_folds(
            rows,
            make_schema(),
            fold_count=3,
        )

        self.assertEqual(len(folds), 3)
        for fold in folds:
            train = fold["train_rows"]
            validation = fold["validation_rows"]
            self.assertFalse(
                {row["mint"] for row in train}
                & {row["mint"] for row in validation}
            )
            self.assertLess(
                max(row["signal_ts"] for row in train),
                min(row["signal_ts"] for row in validation),
            )


class ThresholdSelectionTests(unittest.TestCase):
    def test_prefers_precision_weighted_threshold(self):
        targets = np.asarray([0, 0, 0, 1, 1])
        probabilities = np.asarray([0.1, 0.4, 0.6, 0.7, 0.9])

        threshold = select_threshold(targets, probabilities)

        self.assertGreater(threshold, 0.6)

    def test_one_class_metrics_mark_rank_metrics_unavailable(self):
        metrics = classification_metrics(
            np.asarray([0, 0]),
            np.asarray([0, 1]),
            np.asarray([0.2, 0.8]),
        )

        self.assertIsNone(metrics["balanced_accuracy"])
        self.assertIsNone(metrics["roc_auc"])
        self.assertIsNone(metrics["average_precision"])
        self.assertEqual(metrics["confusion_matrix"], [[1, 1], [0, 0]])

    def test_classification_metrics_honor_sample_weights(self):
        metrics = classification_metrics(
            np.asarray([1, 1, 0]),
            np.asarray([1, 0, 1]),
            np.asarray([0.9, 0.2, 0.8]),
            sample_weight=np.asarray([1.0, 0.1, 0.1]),
        )

        self.assertEqual(metrics["precision"], 0.909091)
        self.assertEqual(metrics["recall"], 0.909091)


class TrainingDiagnosticsTests(unittest.TestCase):
    def test_group_balanced_weights_give_each_mint_equal_total_weight(self):
        rows = [
            make_row(0, "mint-a", 0),
            make_row(1, "mint-a", 1),
            make_row(2, "mint-b", 1),
        ]

        weights = group_balanced_sample_weights(rows, make_schema())

        self.assertAlmostEqual(float(weights.mean()), 1.0)
        self.assertAlmostEqual(float(weights[0] + weights[1]), 1.5)
        self.assertAlmostEqual(float(weights[2]), 1.5)

    def test_trader_balanced_weights_give_each_trader_equal_total_weight(self):
        rows = [
            make_row(0, "mint-a", 0),
            make_row(1, "mint-b", 1),
            make_row(2, "mint-c", 1),
        ]
        rows[0]["trader"] = rows[1]["trader"] = "frequent"
        rows[2]["trader"] = "rare"

        weights = trader_balanced_sample_weights(rows, make_schema())

        self.assertAlmostEqual(float(weights.mean()), 1.0)
        self.assertAlmostEqual(float(weights[0] + weights[1]), 1.5)
        self.assertAlmostEqual(float(weights[2]), 1.5)

    def test_inverse_trader_cannot_save_model_or_shadow_artifact(self):
        for extra_args in ([], ["--shadow-candidate-output", "candidate.json"]):
            with self.subTest(extra_args=extra_args):
                with (
                    patch.object(sys, "argv", [
                        "train_baseline_model.py", "--sample-weighting",
                        "inverse-trader", *extra_args,
                    ]),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                    self.assertRaises(SystemExit) as raised,
                ):
                    main()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--no-save", stderr.getvalue())

    def test_reports_temporal_target_and_trader_drift(self):
        rows = [
            make_row(index, f"mint-{index}", int(index >= 5))
            for index in range(10)
        ]
        for index, row in enumerate(rows):
            row["trader"] = "early" if index < 5 else "late"

        windows = temporal_window_report(
            rows,
            make_schema(),
            window_count=2,
        )

        self.assertEqual(sum(window["rows"] for window in windows), 10)
        self.assertEqual(windows[0]["positive_rate"], 0.0)
        self.assertEqual(windows[1]["positive_rate"], 1.0)
        self.assertEqual(windows[0]["traders"], {"early": 5})
        self.assertEqual(windows[1]["traders"], {"late": 5})
        self.assertLess(windows[0]["end_ts"], windows[1]["start_ts"])

    def test_blocks_deployment_with_too_few_holdout_positives(self):
        rows = [
            make_row(index, f"mint-{index}", target)
            for index, target in enumerate([0, 0, 0, 1])
        ]

        blockers = get_deployment_blockers(rows, make_schema())

        self.assertEqual(
            blockers,
            [
                "holdout_target_1 1/2",
                "holdout_target_1_groups 1/2",
            ],
        )

    def test_blocks_deployment_when_one_group_dominates_holdout(self):
        rows = [
            make_row(index, "mint-dominant", index % 2)
            for index in range(8)
        ]
        rows.extend([
            make_row(8, "mint-negative", 0),
            make_row(9, "mint-positive", 1),
        ])

        blockers = get_deployment_blockers(rows, make_schema())

        self.assertEqual(blockers, ["holdout_largest_group_share 0.800/0.500"])

    def test_blocks_deployment_with_too_few_independent_groups_per_class(self):
        rows = [
            make_row(0, "mint-negative", 0),
            make_row(1, "mint-negative", 0),
            make_row(2, "mint-positive", 1),
            make_row(3, "mint-positive", 1),
        ]

        blockers = get_deployment_blockers(rows, make_schema())

        self.assertEqual(
            blockers,
            [
                "holdout_target_0_groups 1/2",
                "holdout_target_1_groups 1/2",
            ],
        )

    def test_partition_summary_counts_targets_and_groups(self):
        rows = [
            make_row(0, "mint-a", 0),
            make_row(1, "mint-b", 1),
            make_row(2, "mint-b", 1),
        ]

        summary = summarize_partition(rows, make_schema())

        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["target_0"], 1)
        self.assertEqual(summary["target_1"], 2)
        self.assertEqual(summary["unique_groups"], 2)
        self.assertEqual(summary["target_0_groups"], 1)
        self.assertEqual(summary["target_1_groups"], 1)
        self.assertEqual(summary["largest_group_rows"], 2)
        self.assertEqual(summary["largest_group_share"], 0.666667)


class ShadowArtifactTests(unittest.TestCase):
    def test_stdlib_shadow_probability_matches_sklearn(self):
        schema = make_schema(nullable=["feature"])
        rows = [
            make_row(0, "mint-a", 0, feature=None),
            make_row(1, "mint-b", 1, feature=1.0),
            make_row(2, "mint-c", 0, feature=2.0),
            make_row(3, "mint-d", 1, feature=3.0),
            make_row(4, "mint-e", 0, feature=4.0),
            make_row(5, "mint-f", 1, feature=5.0),
        ]
        rows[1]["trader"] = "trader-b"
        rows[3]["trader"] = "trader-b"
        matrix, _ = build_matrix(rows, schema)
        targets = np.asarray([row["target"] for row in rows])
        pipeline = build_pipeline(schema)
        pipeline.fit(matrix, targets)
        artifact = build_shadow_artifact(
            pipeline,
            schema,
            threshold=0.6,
            fit_rows=len(rows),
            trained_at="2026-09-07T00:00:00+00:00",
        )
        shadow = ShadowLogisticModel(artifact)

        self.assertEqual(shadow.artifact_role, "incumbent")
        self.assertTrue(shadow.deployment_ready)
        self.assertEqual(shadow.deployment_blockers, [])

        samples = [
            {"trader": "trader-a", "feature": None},
            {"trader": "trader-b", "feature": 2.5},
            {"trader": "unseen-trader", "feature": 8.0},
        ]
        sklearn_matrix = np.asarray([
            [sample["trader"], sample["feature"]]
            for sample in samples
        ], dtype=object)
        expected = pipeline.predict_proba(sklearn_matrix)[:, 1]

        for sample, probability in zip(samples, expected):
            self.assertAlmostEqual(
                shadow.predict_probability(sample),
                probability,
                places=12,
            )

        retrained_at_later_time = build_shadow_artifact(
            pipeline,
            schema,
            threshold=0.6,
            fit_rows=len(rows),
            trained_at="2026-09-08T00:00:00+00:00",
        )
        self.assertEqual(
            artifact["model_version"],
            retrained_at_later_time["model_version"],
        )

    def test_shadow_candidate_records_non_deployable_role(self):
        schema = make_schema(nullable=["feature"])
        rows = [
            make_row(0, "mint-a", 0, feature=0.0),
            make_row(1, "mint-b", 1, feature=1.0),
        ]
        matrix, _ = build_matrix(rows, schema)
        targets = np.asarray([row["target"] for row in rows])
        pipeline = build_pipeline(schema)
        pipeline.fit(matrix, targets)

        artifact = build_shadow_artifact(
            pipeline,
            schema,
            threshold=0.6,
            fit_rows=len(rows),
            trained_at="2026-09-08T00:00:00+00:00",
            artifact_role="challenger",
            deployment_ready=False,
            deployment_blockers=["holdout concentration"],
        )
        shadow = ShadowLogisticModel(artifact)

        self.assertEqual(shadow.artifact_role, "challenger")
        self.assertFalse(shadow.deployment_ready)
        self.assertEqual(
            shadow.deployment_blockers,
            ["holdout concentration"],
        )


if __name__ == "__main__":
    unittest.main()
