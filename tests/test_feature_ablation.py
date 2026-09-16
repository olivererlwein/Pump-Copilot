import unittest

from scripts.analyze_feature_ablation import (
    ABLATIONS,
    schema_without_features,
    selection_diagnostics,
)


class FeatureAblationTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "categorical_features": ["trader"],
            "numeric_features": ["score_total", "sol_amount", "market_cap"],
        }

    def test_removes_features_without_mutating_original_schema(self):
        reduced = schema_without_features(
            self.schema,
            {"trader", "score_total"},
        )

        self.assertEqual(reduced["categorical_features"], [])
        self.assertEqual(
            reduced["numeric_features"],
            ["sol_amount", "market_cap"],
        )
        self.assertEqual(self.schema["categorical_features"], ["trader"])
        self.assertEqual(
            self.schema["numeric_features"],
            ["score_total", "sol_amount", "market_cap"],
        )

    def test_rejects_an_ablation_that_removes_every_feature(self):
        with self.assertRaisesRegex(ValueError, "at least one feature"):
            schema_without_features(
                self.schema,
                {"trader", "score_total", "sol_amount", "market_cap"},
            )

    def test_reduced_candidate_retains_only_size_score_from_legacy_scores(self):
        excluded = ABLATIONS["only_size_score_from_legacy"]

        self.assertNotIn("size_score", excluded)
        self.assertTrue({
            "trader_score",
            "timing_score",
            "token_score",
            "consensus_score",
            "market_score",
            "score_total",
        }.issubset(excluded))


class SelectionDiagnosticsTests(unittest.TestCase):
    def test_reports_holdout_and_selected_concentration_separately(self):
        rows = [
            {"mint": "mint-a", "trader": "alice"},
            {"mint": "mint-a", "trader": "alice"},
            {"mint": "mint-b", "trader": "bob"},
            {"mint": "mint-c", "trader": "bob"},
        ]

        report = selection_diagnostics(rows, [1, 0, 1, 0])

        self.assertEqual(report["holdout_rows"], 4)
        self.assertEqual(report["holdout_mints"], 3)
        self.assertEqual(report["largest_holdout_mint_share"], 0.5)
        self.assertEqual(report["selected_mints"], 2)
        self.assertEqual(report["largest_selected_mint_share"], 0.5)
        self.assertEqual(report["selected_by_trader"], {"alice": 1, "bob": 1})

    def test_empty_selection_has_no_misleading_zero_share(self):
        report = selection_diagnostics(
            [{"mint": "mint-a", "trader": "alice"}], [0]
        )

        self.assertEqual(report["selected"], 0)
        self.assertIsNone(report["largest_selected_trader_share"])
        self.assertIsNone(report["largest_selected_mint_share"])

    def test_rejects_misaligned_predictions(self):
        with self.assertRaises(ValueError):
            selection_diagnostics([], [1])


if __name__ == "__main__":
    unittest.main()
