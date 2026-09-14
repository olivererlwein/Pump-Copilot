import unittest

from scripts.audit_shadow_economics import (
    audit_shadow_models,
    pair_completed_predictions,
)


def prediction(
    evaluation_id,
    version,
    predicted_target,
    actual_target,
    trader,
    created_ts,
    mint=None,
):
    return {
        "evaluation_id": evaluation_id,
        "model_version": version,
        "predicted_target": predicted_target,
        "actual_target": actual_target,
        "trader": trader,
        "mint": mint or f"mint-{evaluation_id}",
        "created_ts": created_ts,
    }


class ShadowEconomicsAuditTests(unittest.TestCase):
    def test_pairs_only_completed_predictions_shared_by_both_models(self):
        rows = [
            prediction(1, "inc", 1, 1, "alice", 10),
            prediction(1, "new", 0, 1, "alice", 10),
            prediction(2, "inc", 1, None, "bob", 20),
            prediction(2, "new", 1, None, "bob", 20),
            prediction(3, "inc", 0, 0, "carol", 30),
        ]

        pairs = pair_completed_predictions(rows, "inc", "new")

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["evaluation_id"], 1)
        self.assertEqual(pairs[0]["predictions"], {"inc": 1, "new": 0})

    def test_rejects_disagreement_about_the_actual_target(self):
        rows = [
            prediction(1, "inc", 1, 1, "alice", 10),
            prediction(1, "new", 1, 0, "alice", 10),
        ]

        with self.assertRaisesRegex(ValueError, "actual target"):
            pair_completed_predictions(rows, "inc", "new")

    def test_canary_gate_rejects_a_profitable_concentrated_model(self):
        rows = []
        traders = ["alice"] * 8 + ["bob", "carol"]
        for index, trader in enumerate(traders, start=1):
            rows.extend([
                prediction(index, "inc", 1, 1, trader, index * 1000),
                prediction(index, "new", 1, 1, trader, index * 1000),
            ])

        report = audit_shadow_models(
            rows,
            incumbent_version="inc",
            challenger_version="new",
            minimum_completed_pairs=10,
            maximum_trader_share=0.5,
            minimum_selected_traders=3,
            temporal_windows=2,
            minimum_positive_windows=2,
        )

        challenger = report["models"]["new"]
        self.assertGreater(challenger["economics"]["net_return_units"], 0)
        self.assertEqual(challenger["selected_traders"], 3)
        self.assertEqual(challenger["largest_trader_share"], 0.8)
        self.assertFalse(challenger["eligible_for_canary"])
        self.assertIn("largest_trader_share 0.800/0.500", challenger["blockers"])

    def test_canary_gate_accepts_diverse_profitable_temporal_results(self):
        rows = []
        traders = ["alice", "bob", "carol", "alice"] * 3
        for index, trader in enumerate(traders, start=1):
            actual = 0 if index in (4, 9) else 1
            rows.extend([
                prediction(index, "inc", 1, actual, trader, index * 1000),
                prediction(index, "new", 1, actual, trader, index * 1000),
            ])

        report = audit_shadow_models(
            rows,
            incumbent_version="inc",
            challenger_version="new",
            minimum_completed_pairs=10,
            maximum_trader_share=0.5,
            minimum_selected_traders=3,
            temporal_windows=4,
            minimum_positive_windows=3,
        )

        model = report["models"]["new"]
        self.assertEqual(model["positive_temporal_windows"], 4)
        self.assertTrue(model["eligible_for_canary"])
        self.assertEqual(model["blockers"], [])


if __name__ == "__main__":
    unittest.main()
