import unittest

from scripts.analyze_entry_age_shadow import summarize


class EntryAgeShadowTests(unittest.TestCase):
    def test_unknown_age_and_first_mint_are_not_conflated_with_timing(self):
        rows = [
            {"signal_ts": 1, "signal_id": 1, "mint": "a", "trader": "one",
             "token_age_seconds": None, "timing_score": 20,
             "target_tp25_before_sl10": 1},
            {"signal_ts": 2, "signal_id": 2, "mint": "a", "trader": "two",
             "token_age_seconds": 0, "timing_score": 20,
             "target_tp25_before_sl10": 0},
            {"signal_ts": 3, "signal_id": 3, "mint": "b", "trader": "two",
             "token_age_seconds": 12, "timing_score": 6,
             "target_tp25_before_sl10": 0},
        ]
        result = summarize(rows)
        self.assertEqual(result["all"]["stored_watched_create_age_present"], 2)
        self.assertEqual(result["all"]["recorded_timing_20"], 2)
        self.assertEqual(result["all"]["timing_20_with_unknown_age"], 1)
        self.assertEqual(result["first_labeled_row_per_mint"], {
            "rows": 2, "stored_watched_create_age_present": 1,
            "creation_age_unknown": 1,
            "recorded_timing_20": 1, "timing_20_with_unknown_age": 1,
            "tp25_before_sl10": 1, "unlabeled": 0,
        })
        self.assertEqual(result["trader_all:two"]["rows"], 2)
        self.assertEqual(result["trader_first_labeled_mint:two"]["rows"], 1)

    def test_missing_target_is_not_counted_as_loss(self):
        row = {"signal_ts": 1, "signal_id": 1, "mint": "a", "trader": "one",
               "token_age_seconds": None, "timing_score": 20,
               "target_tp25_before_sl10": None}
        result = summarize([row])["all"]
        self.assertEqual(result["tp25_before_sl10"], 0)
        self.assertEqual(result["unlabeled"], 1)


if __name__ == "__main__":
    unittest.main()
