import unittest

import numpy as np

from scripts.train_baseline_model import (
    classification_metrics,
    select_threshold,
    temporal_group_split,
    temporal_group_validation_folds,
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


if __name__ == "__main__":
    unittest.main()
