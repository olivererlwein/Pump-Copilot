import unittest

from scripts.train_baseline_model import (
    temporal_group_split,
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


if __name__ == "__main__":
    unittest.main()
