import json
import sqlite3
import unittest

from scripts.validate_wss_cap_price_shadow import summarize
from solana_rpc_fallback import (
    parse_pump_trade_log_prices, parse_tracked_token_pump_events,
)
from tests.test_rpc_fallback import MINT, SIGNATURE, pump_receipt


class CapPriceShadowValidationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE helius_wss_cap_price_candidates("
            "id INTEGER PRIMARY KEY, signature TEXT, event_index INTEGER, "
            "mint TEXT, received_ts REAL, block_event_ts INTEGER, side TEXT, "
            "trader TEXT, market_cap_sol REAL)"
        )
        self.conn.execute(
            "CREATE TABLE market_event_inbox("
            "signature TEXT, event_index INTEGER, event_index_scheme TEXT, "
            "source TEXT, event_json TEXT)"
        )

    def tearDown(self):
        self.conn.close()

    def candidate(self, signature, received_ts=120):
        self.conn.execute(
            "INSERT INTO helius_wss_cap_price_candidates "
            "(signature, event_index, mint, received_ts, block_event_ts, "
            "side, trader, market_cap_sol) VALUES (?, 0, 'mint-a', ?, 100, "
            "'buy', 'trader-a', 12.5)",
            (signature, received_ts),
        )

    def receipt(self, signature, **overrides):
        event = {
            "signature": signature, "eventIndex": 0, "mint": "mint-a",
            "txType": "buy", "traderPublicKey": "trader-a",
            "blockEventTs": 100, "pool": "pump", "marketCapSol": 12.5,
        }
        event.update(overrides)
        self.conn.execute(
            "INSERT INTO market_event_inbox VALUES (?, 0, 'ordinal-v2', "
            "'helius', ?)", (signature, json.dumps(event)),
        )

    def test_matches_receipt_and_keeps_missing_separate(self):
        self.candidate("matched")
        self.receipt("matched")
        self.candidate("missing")
        report = summarize(self.conn)
        self.assertEqual(report["sampled_candidate_rows"], 2)
        self.assertEqual(report["categories"], {
            "parser_consistent": 1, "no_receipt": 1,
        })
        self.assertEqual(report["price_comparable_rows"], 1)
        self.assertEqual(report["receipt_rows_present"], 1)

    def test_wrong_scheme_cannot_verify_price(self):
        self.candidate("old")
        self.receipt("old")
        self.conn.execute(
            "UPDATE market_event_inbox SET event_index_scheme='log-v1'"
        )
        self.assertEqual(
            summarize(self.conn)["categories"], {"unverifiable_scheme": 1}
        )

    def test_non_receipt_source_and_bad_json_are_not_matches(self):
        self.candidate("other-source")
        self.receipt("other-source")
        self.conn.execute(
            "UPDATE market_event_inbox SET source='demo' "
            "WHERE signature='other-source'"
        )
        self.candidate("bad-json")
        self.conn.execute(
            "INSERT INTO market_event_inbox VALUES "
            "('bad-json', 0, 'ordinal-v2', 'helius', '{')"
        )
        self.assertEqual(summarize(self.conn)["categories"], {
            "unverifiable_source": 1, "invalid_receipt_event": 1,
        })

    def test_distinguishes_identity_and_price_mismatches(self):
        self.candidate("identity")
        self.receipt("identity", traderPublicKey="other")
        self.candidate("price")
        self.receipt("price", marketCapSol=13.5)
        self.assertEqual(summarize(self.conn)["categories"], {
            "identity_mismatch": 1, "price_mismatch": 1,
        })

    def test_filters_by_candidate_receive_time_and_rejects_nonfinite_price(self):
        self.candidate("older", 100)
        self.receipt("older")
        self.candidate("newer", 200)
        self.receipt("newer", marketCapSol=float("nan"))
        report = summarize(self.conn, after_ts=150)
        self.assertEqual(report["sampled_candidate_rows"], 1)
        self.assertEqual(report["categories"], {"invalid_receipt_event": 1})

    def test_real_pump_log_candidate_matches_normalized_receipt(self):
        receipt = pump_receipt()
        candidate = parse_pump_trade_log_prices(
            receipt["meta"]["logMessages"], SIGNATURE, MINT,
        )[0]
        normalized = parse_tracked_token_pump_events(
            receipt, {MINT}, SIGNATURE,
        )[0]["event"]
        self.conn.execute(
            "INSERT INTO helius_wss_cap_price_candidates "
            "(signature, event_index, mint, received_ts, block_event_ts, "
            "side, trader, market_cap_sol) VALUES (?, ?, ?, 120, ?, ?, ?, ?)",
            (SIGNATURE, candidate["eventIndex"], MINT,
             candidate["blockEventTs"], candidate["txType"],
             candidate["traderPublicKey"], candidate["marketCapSol"]),
        )
        self.conn.execute(
            "INSERT INTO market_event_inbox VALUES (?, ?, 'ordinal-v2', "
            "'helius', ?)",
            (SIGNATURE, normalized["eventIndex"], json.dumps(normalized)),
        )
        self.assertEqual(
            summarize(self.conn)["categories"], {"parser_consistent": 1}
        )

    def test_null_timestamp_and_boolean_index_are_not_validated(self):
        self.candidate("null-time")
        self.receipt("null-time", blockEventTs=None)
        self.conn.execute(
            "UPDATE helius_wss_cap_price_candidates SET block_event_ts=NULL"
        )
        self.candidate("bool-index")
        self.receipt("bool-index", eventIndex=False)
        self.assertEqual(
            summarize(self.conn)["categories"], {"invalid_receipt_event": 2}
        )

    def test_report_discloses_buffer_eviction_and_mint_concentration(self):
        self.candidate("old", 100)
        self.candidate("retained", 200)
        self.conn.execute(
            "DELETE FROM helius_wss_cap_price_candidates "
            "WHERE signature='old'"
        )
        report = summarize(self.conn)
        self.assertTrue(report["buffer_eviction_detected"])
        self.assertEqual(report["top_sampled_mints"], [
            {"mint": "mint-a", "rows": 1}
        ])


if __name__ == "__main__":
    unittest.main()
