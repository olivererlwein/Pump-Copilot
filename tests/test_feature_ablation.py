import unittest

from scripts.analyze_feature_ablation import ABLATIONS, schema_without_features


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


if __name__ == "__main__":
    unittest.main()
