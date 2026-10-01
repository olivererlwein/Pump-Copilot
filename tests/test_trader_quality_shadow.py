import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TraderQualityShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(app, "DB", Path(self.temp_dir.name) / "shadow.db")
        self.db_patch.start()
        app.migrate_database()
        self.start = app.TRADER_QUALITY_SHADOW_START_TS

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def add_signal(
        self, mint, offset, *, source="live", transport="helius",
        entry=1.0, basis="pump", exit_price=None, observed_offset=None,
    ):
        conn = app.db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO evaluations(
                    ts, trader, mint, source, transport, score, decision,
                    trader_score
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (self.start + offset, "trader-one", mint, source,
                 transport, 65, "WATCH", 15),
            )
            evaluation_id = cursor.lastrowid
            cursor = conn.execute(
                """
                INSERT INTO signal_outcomes(
                    signal_id, mint, trader, signal_ts, price_at_signal,
                    entry_price_basis, created_ts, updated_ts
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (evaluation_id, mint, "trader-one", self.start + offset,
                 entry, basis, self.start + offset, self.start + offset),
            )
            if exit_price is not None:
                conn.execute(
                    """
                    INSERT INTO account_price_checkpoints(
                        outcome_id, checkpoint_seconds, mint, pool, price_sol,
                        market_cap_sol, observed_ts, slot
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (cursor.lastrowid, 900, mint, "pump", exit_price,
                     exit_price * app.PUMP_TOKEN_SUPPLY,
                     self.start + observed_offset, 1),
                )
            conn.commit()
            return evaluation_id
        finally:
            conn.close()

    def test_snapshot_uses_only_prior_independent_observed_mints(self):
        self.add_signal("win", 1000, exit_price=1.10, observed_offset=1950)
        self.add_signal("win", 1200, exit_price=100, observed_offset=2100)
        self.add_signal("missing", 1500)
        self.add_signal("pending", 3000)
        self.add_signal("late", 2000, exit_price=1.5, observed_offset=3700)
        self.add_signal("demo", 1000, source="demo", exit_price=100,
                        observed_offset=1950)
        self.add_signal("rpc", 1000, transport="rpc", exit_price=100,
                        observed_offset=1950)
        self.add_signal("current-mint", 1000, exit_price=100,
                        observed_offset=1950)
        self.add_signal("old-mint", -100)
        self.add_signal("old-mint", 1000, exit_price=100,
                        observed_offset=1950)
        current_id = self.add_signal("current-mint", 3600)

        app.record_trader_quality_shadow(
            current_id, "trader-one", "current-mint", self.start + 3600,
            65, 15,
        )
        conn = app.db()
        try:
            row = conn.execute(
                """
                SELECT quality, score, quality_if_missing_total_loss,
                       score_if_missing_total_loss,
                       measured_mints, profitable_mints,
                       missing_mints, pending_mints, unsupported_mints,
                       mean_net_return_pct
                FROM trader_quality_shadow WHERE evaluation_id = ?
                """,
                (current_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (16, 66, 15, 65, 1, 1, 2, 1, 1, 5.0))

    def test_shadow_recomputes_from_uncapped_components(self):
        current_id = self.add_signal("new-mint", 1000)
        app.record_trader_quality_shadow(
            current_id, "trader-one", "new-mint", self.start + 1000,
            120, 30,
        )
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT quality, score FROM trader_quality_shadow WHERE evaluation_id = ?",
                (current_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (15, 100))

    def test_shadow_failure_does_not_change_or_block_decision(self):
        event = {
            "mint": "DEMO-SHADOW-ISOLATION", "signature": "shadow-isolation",
            "solAmount": 1.0, "marketCapSol": 70.0, "pool": "pump",
        }
        order = []

        def live_copy(**_kwargs):
            order.append("live")
            return {"attempted": False, "reason": "TEST"}

        def shadow_failure(*_args):
            order.append("shadow")
            raise RuntimeError("broken")

        with patch.object(app, "maybe_execute_live_copy", side_effect=live_copy), \
             patch.object(app, "record_trader_quality_shadow",
                          side_effect=shadow_failure):
            result = app.evaluate_buy(
                "trader-one", event, source="live", price_at_signal=0.1,
            )
        conn = app.db()
        try:
            recorded = conn.execute(
                "SELECT score, decision FROM evaluations WHERE trade_signature = ?",
                (event["signature"],),
            ).fetchone()
            outcomes = conn.execute(
                "SELECT COUNT(*) FROM signal_outcomes"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((result["score"], result["decision"]), recorded)
        self.assertEqual(outcomes, 1)
        self.assertFalse(result["live_execution"]["attempted"])
        self.assertEqual(order, ["live", "shadow"])

    def test_read_endpoint_reports_shadow_without_affecting_decisions(self):
        event = {
            "mint": "DEMO-SHADOW-ENDPOINT", "signature": "shadow-endpoint",
            "solAmount": 1.0, "marketCapSol": 70.0, "pool": "pump",
        }
        result = app.evaluate_buy(
            "trader-one", event, source="live", price_at_signal=0.1,
            allow_live_buys=False,
        )
        with patch.object(app, "auth"):
            report = app.api_trader_quality_shadow("test-token")
        self.assertFalse(report["affects_decisions"])
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["recorded_decision"], result["decision"])
        self.assertEqual(report["rows"][0]["shadow_quality"], 15)
        self.assertEqual(report["rows"][0]["quality_if_missing_total_loss"], 15)


if __name__ == "__main__":
    unittest.main()
