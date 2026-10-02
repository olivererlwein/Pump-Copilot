import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app
from paper_copy_lifecycle import replay_paper_cycles


def signal(number, ts):
    return {
        "id": number, "signature": f"buy-{number}", "event_index": 0,
        "ts": ts, "trader": "trader", "mint": "mint", "decision": "WATCH",
        "created_ts": ts + 2, "outcome_count": 1, "entry_price": 1.0,
        "entry_pool": "curve", "entry_observed_ts": ts + 5,
    }


def trade(number, ts, side, amount, balance, price=None):
    return {
        "id": number, "ts": ts, "recorded_ts": ts + 1,
        "trader": "trader", "mint": "mint", "side": side,
        "signature": f"buy-{number}" if side == "buy" else f"sell-{number}",
        "event_index": 0, "token_amount": amount,
        "new_token_balance": balance, "transport": "helius",
        "exit_price": price, "exit_pool": "curve" if price else None,
        "exit_observed_ts": ts + 3 if price else None,
    }


class PaperCopyLifecycleReplayTests(unittest.TestCase):
    def test_multiple_buys_and_partial_sells_use_one_paper_position(self):
        report = replay_paper_cycles(
            [signal(1, 100), signal(2, 110)],
            [trade(1, 100, "buy", 100, 100),
             trade(2, 110, "buy", 100, 200),
             trade(3, 120, "sell", 50, 150, 2),
             trade(4, 130, "sell", 150, 0, 3)],
        )
        self.assertEqual(report["counts"], {
            "complete": 1, "overlapping_signal": 1,
        })
        row = report["rows"][0]
        self.assertEqual(row["sells"], 2)
        self.assertEqual(row["gross_usd"], 43.75)
        self.assertEqual(row["net_usd_at_2pct"], 43.25)
        self.assertEqual(row["net_usd_at_5pct"], 42.5)
        self.assertEqual(len(row["sale_legs"]), 2)
        self.assertEqual(report["by_mint"]["mint"]["complete"], 1)
        self.assertEqual(report["uncertain_entered_cycles"], 0)
        self.assertNotIn("gross_usd", report["rows"][1])

    def test_missing_sell_quote_never_becomes_zero_return(self):
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100),
             trade(2, 110, "sell", 100, 0)],
        )
        self.assertEqual(report["counts"], {
            "missing_or_incompatible_exit_quote": 1,
        })
        self.assertNotIn("net_usd_at_2pct", report["rows"][0])
        self.assertEqual(report["uncertain_entered_cycles"], 1)
        self.assertEqual(
            report["if_uncertain_entered_total_loss"]["net_usd_at_5pct"], -25.0
        )

    def test_inventory_gap_and_prior_holdings_are_excluded(self):
        gap = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100),
             trade(2, 110, "sell", 50, 20, 2)],
        )
        self.assertEqual(gap["rows"][0]["status"], "inventory_gap")
        prior = replay_paper_cycles(
            [signal(1, 100)], [trade(1, 100, "buy", 100, 200)],
        )
        self.assertEqual(prior["rows"][0]["status"], "opening_inventory_unknown")

    def test_same_second_order_is_not_guessed(self):
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100),
             trade(2, 100, "sell", 100, 0, 2)],
        )
        self.assertEqual(report["rows"][0]["status"], "trade_order_ambiguous")

    def test_sell_seen_before_signal_or_via_rpc_is_excluded(self):
        early = trade(2, 110, "sell", 100, 0, 2)
        early["recorded_ts"] = 101
        report = replay_paper_cycles(
            [signal(1, 100)], [trade(1, 100, "buy", 100, 100), early],
        )
        self.assertEqual(report["rows"][0]["status"], "late_or_ambiguous_arrival")
        self.assertEqual(report["rows"][0]["timing_flags"], [
            "before_signal_recorded", "non_monotonic_recorded_time",
            "before_previous_quote",
        ])
        rpc = trade(2, 110, "sell", 100, 0, 2)
        rpc["transport"] = "rpc"
        report = replay_paper_cycles(
            [signal(1, 100)], [trade(1, 100, "buy", 100, 100), rpc],
        )
        self.assertEqual(report["rows"][0]["status"], "late_or_ambiguous_arrival")
        self.assertEqual(report["timing_flag_counts"], {"rpc_transport": 1})

    def test_duplicate_sell_identity_is_not_counted_twice(self):
        first = trade(2, 110, "sell", 50, 50, 2)
        duplicate = trade(3, 120, "sell", 50, 0, 3)
        duplicate["signature"] = first["signature"]
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100), first, duplicate],
        )
        self.assertEqual(report["rows"][0]["status"], "duplicate_trade_identity")
        self.assertNotIn("net_usd_at_2pct", report["rows"][0])

    def test_exit_before_entry_quote_and_duplicate_outcome_are_excluded(self):
        sell = trade(2, 110, "sell", 100, 0, 2)
        sell["recorded_ts"] = 104
        sell["exit_observed_ts"] = 105
        report = replay_paper_cycles(
            [signal(1, 100)], [trade(1, 100, "buy", 100, 100), sell],
        )
        self.assertEqual(report["rows"][0]["status"], "sell_before_entry_quote")
        self.assertFalse(report["rows"][0]["paper_entered"])
        duplicate = signal(1, 100)
        duplicate["outcome_count"] = 2
        report = replay_paper_cycles(
            [duplicate], [trade(1, 100, "buy", 100, 100)],
        )
        self.assertEqual(report["rows"][0]["status"], "outcome_missing_or_duplicate")

    def test_uncertain_position_blocks_later_signals_for_same_pair(self):
        report = replay_paper_cycles(
            [signal(1, 100), signal(3, 120)],
            [trade(1, 100, "buy", 100, 100),
             trade(2, 110, "sell", 50, 20, 2),
             trade(3, 120, "buy", 100, 100),
             trade(4, 130, "sell", 100, 0, 2)],
        )
        self.assertEqual(report["counts"], {
            "inventory_gap": 1, "overlapping_signal": 1,
        })
        self.assertEqual(report["uncertain_entered_cycles"], 1)

    def test_next_trade_before_prior_sell_quote_is_ambiguous(self):
        first_sell = trade(2, 110, "sell", 50, 50, 2)
        first_sell["exit_observed_ts"] = 115
        second_sell = trade(3, 120, "sell", 50, 0, 3)
        second_sell["recorded_ts"] = 114
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100), first_sell, second_sell],
        )
        self.assertEqual(report["rows"][0]["status"], "late_or_ambiguous_arrival")

    def test_buy_before_entry_quote_updates_opening_inventory(self):
        second_buy = trade(2, 103, "buy", 100, 200)
        second_buy["recorded_ts"] = 104
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100), second_buy,
             trade(3, 110, "sell", 200, 0, 2)],
        )
        self.assertEqual(report["counts"], {"complete": 1})
        self.assertEqual(report["rows"][0]["pre_entry_buys"], 1)
        self.assertEqual(report["rows"][0]["net_usd_at_5pct"], 23.75)

    def test_sell_before_entry_quote_is_not_a_paper_position(self):
        early_sell = trade(2, 103, "sell", 50, 50, 2)
        early_sell["recorded_ts"] = 104
        report = replay_paper_cycles(
            [signal(1, 100)],
            [trade(1, 100, "buy", 100, 100), early_sell],
        )
        self.assertEqual(report["counts"], {"sell_before_entry_quote": 1})
        self.assertFalse(report["rows"][0]["paper_entered"])
        self.assertEqual(report["rows"][0]["early_sell"], {
            "seconds_after_signal": 2, "seconds_before_entry_quote": 1,
        })
        self.assertEqual(report["uncertain_entered_cycles"], 0)
        self.assertEqual(report["if_uncertain_entered_total_loss"]["net_usd_at_5pct"], 0)

    def test_endpoint_uses_prospective_boundary_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DB", Path(directory) / "replay.db"
        ), patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app, "OBSERVE_TRADERS", set()
        ), patch.object(app.time, "time", return_value=1790906000.0):
            app.migrate_database()
            with self.assertRaises(HTTPException):
                app.paper_copy_lifecycle_replay(
                    1790904747.0, x_app_token="test-token"
                )
            conn = app.db()
            try:
                evaluation = conn.execute(
                    "INSERT INTO evaluations(trade_signature,event_index,ts,"
                    "trader,mint,source,score,decision) VALUES(?,?,?,?,?,?,?,?)",
                    ("buy-1", 0, 1790905000, "trader", "mint", "live", 60, "WATCH"),
                ).lastrowid
                outcome = conn.execute(
                    "INSERT INTO signal_outcomes(signal_id,mint,trader,signal_ts,"
                    "created_ts,updated_ts) VALUES(?,?,?,?,?,?)",
                    (evaluation, "mint", "trader", 1790905000, 1790905002, 1790905002),
                ).lastrowid
                conn.execute(
                    "INSERT INTO account_price_checkpoints(outcome_id,"
                    "checkpoint_seconds,mint,pool,price_sol,market_cap_sol,"
                    "observed_ts,slot) VALUES(?,?,?,?,?,?,?,?)",
                    (outcome, 10, "mint", "curve", 1.0, 100, 1790905005, 123),
                )
                conn.execute(
                    "INSERT INTO trades(ts,trader,mint,side,signature,source,"
                    "event_index,recorded_ts,token_amount,new_token_balance,transport) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (1790905000, "trader", "mint", "buy", "buy-1", "live", 0,
                     1790905001, 100, 100, "helius"),
                )
                sell_id = conn.execute(
                    "INSERT INTO trades(ts,trader,mint,side,signature,source,"
                    "event_index,recorded_ts,token_amount,new_token_balance,transport) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (1790905100, "trader", "mint", "sell", "sell-1", "live", 0,
                     1790905101, 100, 0, "helius"),
                ).lastrowid
                conn.execute(
                    "INSERT INTO paper_copy_sell_quotes(trade_id,recorded_ts,"
                    "observed_ts,mint,pool,price_sol,market_cap_sol,slot) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (sell_id, 1790905101, 1790905105, "mint", "curve", 2.0, 200, 124),
                )
                conn.commit()
            finally:
                conn.close()
            report = app.paper_copy_lifecycle_replay(
                1790904747.80009, x_app_token="test-token"
            )
            self.assertEqual(report["counts"], {"complete": 1})
            self.assertEqual(report["rows"][0]["net_usd_at_5pct"], 23.75)
            self.assertFalse(report["executes_orders"])
            self.assertFalse(report["realized_pnl_estimate"])
            conn = app.db()
            try:
                conn.execute(
                    "UPDATE paper_copy_sell_quotes SET mint = ? WHERE trade_id = ?",
                    ("wrong-mint", sell_id),
                )
                conn.commit()
            finally:
                conn.close()
            report = app.paper_copy_lifecycle_replay(
                1790904747.80009, x_app_token="test-token"
            )
            self.assertEqual(report["counts"], {
                "missing_or_incompatible_exit_quote": 1,
            })
