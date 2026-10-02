import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AccountPriceCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "checkpoints.db"
        app.migrate_database()
        self.signal_ts = 1_700_000_000.0

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def outcome(self, basis="pump", mint="mint-a"):
        return app.create_signal_outcome(
            signal_id=None, mint=mint, trader="tester",
            signal_ts=self.signal_ts, price_at_signal=1e-7,
            entry_price_basis=basis,
        )

    def checkpoint_dataset_outcome(self, suffix, prices):
        signal_ts = self.signal_ts + suffix * 2_000
        mint = f"mint-{suffix}"
        conn = app.db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO evaluations(
                    trade_signature, event_index, ts, trader, mint, source,
                    score, decision, trader_score, timing_score, size_score,
                    token_score, consensus_score, market_score, reasons,
                    market_cap, sol_amount, data_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"signature-{suffix}", 0, signal_ts, "tester", mint,
                    "helius", 60, "WATCH", 10, 10, 10, 10, 10, 10, "[]",
                    100, 1, app.DATA_VERSION,
                ),
            )
            signal_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        outcome_id = app.create_signal_outcome(
            signal_id=signal_id, mint=mint, trader="tester",
            signal_ts=signal_ts, price_at_signal=1.0,
            entry_price_basis="pump",
        )
        conn = app.db()
        try:
            conn.executemany(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        outcome_id, seconds, mint, "curve", price, 100,
                        signal_ts + seconds + 1, 1000 + seconds,
                    )
                    for (seconds, _grace), price in zip(
                        app.ACCOUNT_PRICE_CHECKPOINT_WINDOWS, prices
                    )
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return signal_id

    def test_paper_sell_quote_is_delayed_and_idempotent(self):
        signal_id = self.checkpoint_dataset_outcome(
            40, [1.0, 1.0, 1.0, 1.0, 1.0]
        )
        signal_ts = self.signal_ts + 40 * 2_000
        conn = app.db()
        try:
            conn.execute(
                "UPDATE evaluations SET source = 'live' WHERE id = ?",
                (signal_id,),
            )
            conn.execute(
                "UPDATE signal_outcomes SET created_ts = ? "
                "WHERE signal_id = ?", (signal_ts + 1, signal_id),
            )
            cursor = conn.execute(
                "INSERT INTO trades(ts, recorded_ts, trader, mint, side, "
                "signature, event_index, new_token_balance, source) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (signal_ts + 60, signal_ts + 70, "tester", "mint-40",
                 "sell", "sell-40", 0, 0, "live"),
            )
            trade_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        def snapshot(_url, _mints, rpc_request):
            rpc_request("rpc", "getMultipleAccounts", [])
            return {"mint-40": {"status": "curve", "price_sol": 1.1,
                                "market_cap_sol": 110.0, "slot": 7}}

        with (
            patch.object(app, "OBSERVE_TRADERS", {"tester"}),
            patch.object(app, "fetch_account_prices") as ignored_fetch,
        ):
            self.assertEqual(app.paper_copy_sell_quote_once(
                now=signal_ts + 74
            )["candidates"], 0)
            ignored_fetch.assert_not_called()
        with (
            patch.object(app, "standard_wss_rpc_url", return_value="rpc"),
            patch.object(app, "_rpc_request", return_value={}) as rpc,
            patch.object(app, "fetch_account_prices", side_effect=snapshot) as fetch,
        ):
            result = app.paper_copy_sell_quote_once(now=signal_ts + 75)
            self.assertEqual(result["recorded"], 1)
            self.assertEqual(result["rpc_calls"], 1)
            self.assertEqual(app.paper_copy_sell_quote_once(
                now=signal_ts + 76
            )["recorded"], 0)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(rpc.call_count, 1)
        conn = app.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT recorded_ts, observed_ts, price_sol FROM "
                "paper_copy_sell_quotes WHERE trade_id = ?", (trade_id,),
            ).fetchone(), (signal_ts + 70, signal_ts + 75, 1.1))
        finally:
            conn.close()

    def test_paper_sell_quote_does_not_backfill_expired_sell(self):
        signal_id = self.checkpoint_dataset_outcome(
            41, [1.0, 1.0, 1.0, 1.0, 1.0]
        )
        signal_ts = self.signal_ts + 41 * 2_000
        conn = app.db()
        try:
            conn.execute(
                "UPDATE evaluations SET source = 'live' WHERE id = ?",
                (signal_id,),
            )
            conn.execute(
                "UPDATE signal_outcomes SET created_ts = ? "
                "WHERE signal_id = ?", (signal_ts + 1, signal_id),
            )
            conn.execute(
                "INSERT INTO trades(ts, recorded_ts, trader, mint, side, "
                "signature, event_index, new_token_balance, source) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (signal_ts + 60, signal_ts + 70, "tester", "mint-41",
                 "sell", "sell-41", 0, 0, "live"),
            )
            conn.commit()
        finally:
            conn.close()
        with (
            patch.object(app, "standard_wss_rpc_url", return_value="rpc"),
            patch.object(app, "fetch_account_prices", return_value={
                "mint-41": {"status": "unsupported_pool", "slot": 7},
            }),
        ):
            self.assertEqual(app.paper_copy_sell_quote_once(
                now=signal_ts + 75
            )["recorded"], 0)
        with patch.object(app, "fetch_account_prices") as fetch:
            result = app.paper_copy_sell_quote_once(now=signal_ts + 101)
        self.assertEqual(result["candidates"], 0)
        fetch.assert_not_called()

    def test_paper_wallet_pilot_keeps_watch_separate_and_prices_both_arms(self):
        watch_id = self.checkpoint_dataset_outcome(0, [1, 1, 1, 1, 1.1])
        copy_id = self.checkpoint_dataset_outcome(1, [1, 1, 1, 1, 0.8])
        missing_id = self.checkpoint_dataset_outcome(2, [1, 1, 1, 1, 1.2])
        conn = app.db()
        try:
            conn.execute(
                "UPDATE evaluations SET source = 'live' WHERE id IN (?, ?, ?)",
                (watch_id, copy_id, missing_id),
            )
            conn.execute(
                "UPDATE evaluations SET score = 80, decision = 'COPY' WHERE id = ?",
                (copy_id,),
            )
            conn.execute(
                "DELETE FROM account_price_checkpoints WHERE outcome_id = "
                "(SELECT id FROM signal_outcomes WHERE signal_id = ?) "
                "AND checkpoint_seconds = 900",
                (missing_id,),
            )
            conn.execute(
                "UPDATE signal_outcomes SET status='completed', price_15m=1.2 "
                "WHERE signal_id=?", (missing_id,),
            )
            conn.execute(
                "UPDATE evaluations SET mint='mint-0' WHERE id=?", (missing_id,)
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 5000
        ):
            report = app.paper_wallet_pilot(
                self.signal_ts - 1, x_app_token="test-token"
            )

        strict = report["arms"]["copy_signals"]
        experimental = report["arms"]["watch_plus_copy"]
        self.assertEqual(report["evaluations"], 3)
        self.assertEqual((strict["signals"], strict["priced"]), (1, 1))
        self.assertEqual((experimental["signals"], experimental["priced"]), (3, 2))
        self.assertEqual(experimental["missing_checkpoint"], 1)
        self.assertEqual(experimental["unique_mints"], 2)
        self.assertEqual(experimental["repeated_mint_signals"], 1)
        self.assertEqual(experimental["largest_mint_cluster"], 2)
        self.assertEqual(
            experimental["missing_checkpoint_by_outcome_status"],
            {"completed": 1},
        )
        self.assertEqual(
            experimental["missing_checkpoint_primary_15m_available"], 1
        )
        self.assertEqual(
            experimental["net_usd_at_5pct_if_missing_total_loss"], -30.0
        )
        self.assertEqual(strict["net_usd_at_2pct"], -5.5)
        self.assertEqual(experimental["net_usd_at_2pct"], -3.5)
        self.assertEqual(experimental["by_trader"]["tester"]["priced"], 2)
        self.assertEqual(app.count_open_positions(mode="paper"), 0)

        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 4930
        ):
            within_grace = app.paper_wallet_pilot(
                self.signal_ts - 1, x_app_token="test-token"
            )["arms"]["watch_plus_copy"]
        self.assertEqual(within_grace["pending"], 1)
        self.assertEqual(within_grace["missing_checkpoint"], 0)

    def test_valid_sol_curve_is_recorded_once_without_touching_primary_outcome(self):
        outcome_id = self.outcome()
        snapshot = {"mint-a": {
            "status": "curve", "price_sol": 1.1e-7,
            "market_cap_sol": 110, "slot": 123,
        }}
        with patch.object(app, "fetch_account_prices", return_value=snapshot) as fetch:
            first = app.account_price_checkpoint_once(now=self.signal_ts + 12)
            second = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(first["checkpoints_recorded"], 1)
        self.assertEqual(second["status"], "no_due_checkpoint")
        fetch.assert_called_once()
        conn = app.db()
        try:
            checkpoint = conn.execute(
                "SELECT outcome_id, checkpoint_seconds, pool, price_sol, slot "
                "FROM account_price_checkpoints"
            ).fetchone()
            primary = conn.execute(
                "SELECT price_10s, status FROM signal_outcomes WHERE id=?",
                (outcome_id,),
            ).fetchone()
            effects = [conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] for table in ("paper_positions", "execution_orders")]
        finally:
            conn.close()
        self.assertEqual(checkpoint, (outcome_id, 10, "curve", 1.1e-7, 123))
        self.assertEqual(primary, (None, "active"))
        self.assertEqual(effects, [0, 0])

    def test_due_mints_beyond_first_rpc_batch_are_not_dropped(self):
        for index in range(25):
            self.outcome(mint=f"mint-{index}")

        batches = []

        def fetch(_url, mints, rpc_request):
            batches.append(list(mints))
            return {
                mint: {"status": "curve", "price_sol": 1e-7,
                       "market_cap_sol": 100, "slot": 123}
                for mint in mints
            }

        with patch.object(app, "fetch_account_prices", side_effect=fetch):
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)

        self.assertEqual([len(batch) for batch in batches], [20, 5])
        self.assertEqual(result["checkpoints_recorded"], 25)
        conn = app.db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM account_price_checkpoints "
                "WHERE checkpoint_seconds=10"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 25)

    def test_old_rows_do_not_hide_new_due_outcome(self):
        conn = app.db()
        try:
            conn.executemany(
                """
                INSERT INTO signal_outcomes(
                    mint, trader, signal_ts, price_at_signal,
                    entry_price_basis, created_ts, updated_ts
                ) VALUES(?,?,?,?,?,?,?)
                """,
                ((f"old-{index}", "tester", self.signal_ts - 100,
                  1e-7, "pump", self.signal_ts - 100,
                  self.signal_ts - 100) for index in range(5000)),
            )
            conn.commit()
        finally:
            conn.close()
        outcome_id = self.outcome(mint="new-due")

        with patch.object(app, "fetch_account_prices", return_value={
            "new-due": {"status": "curve", "price_sol": 1e-7,
                        "market_cap_sol": 100, "slot": 123},
        }):
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(result["checkpoints_recorded"], 1)
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT checkpoint_seconds FROM account_price_checkpoints "
                "WHERE outcome_id=?", (outcome_id,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (10,))

    def test_first_batch_survives_later_rpc_failure(self):
        for index in range(25):
            self.outcome(mint=f"mint-{index}")

        calls = 0

        def fetch(_url, mints, rpc_request):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("RPC_UNAVAILABLE")
            return {
                mint: {"status": "curve", "price_sol": 1e-7,
                       "market_cap_sol": 100, "slot": 123}
                for mint in mints
            }

        with patch.object(app, "fetch_account_prices", side_effect=fetch):
            with self.assertRaisesRegex(RuntimeError, "RPC_UNAVAILABLE"):
                app.account_price_checkpoint_once(now=self.signal_ts + 12)

        conn = app.db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM account_price_checkpoints "
                "WHERE checkpoint_seconds=10"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 20)

    def test_unsupported_quote_is_counted_without_price(self):
        self.outcome()
        with patch.object(app, "fetch_account_prices", return_value={
            "mint-a": {"status": "unsupported_quote", "slot": 123},
        }):
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(result["priced_mints"], 0)
        self.assertEqual(result["checkpoints_recorded"], 0)
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 12
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        self.assertFalse(stats["affects_decisions"])
        self.assertFalse(stats["affects_primary_outcomes"])
        self.assertEqual(stats["last_24h"]["unsupported_mints"], 1)

    def test_stats_explain_when_an_eligible_outcome_is_due(self):
        self.outcome(basis="pump", mint="curve-entry")
        self.outcome(basis="unknown", mint="unknown-entry")
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 12
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        eligibility = stats["eligibility"]
        self.assertEqual(eligibility["eligible_outcomes_in_window"], 1)
        self.assertEqual(eligibility["active_outcomes_in_window"], 1)
        self.assertEqual(eligibility["due_now"], 1)
        self.assertEqual(
            eligibility["entry_price_basis_last_24h"],
            {"pump": 1, "unknown": 1},
        )
        self.assertEqual(
            eligibility["latest_outcome"]["entry_price_basis"], "unknown"
        )
        self.assertEqual(eligibility["latest_outcome"]["age_seconds"], 12)

    def test_stats_separate_late_signal_creation_from_missing_probe(self):
        late = self.outcome(mint="late")
        missed = self.outcome(mint="missed")
        captured = self.outcome(mint="captured")
        conn = app.db()
        try:
            conn.execute(
                "UPDATE signal_outcomes SET created_ts=? WHERE id=?",
                (self.signal_ts + 16, late),
            )
            conn.execute(
                "UPDATE signal_outcomes SET created_ts=? WHERE id IN (?, ?)",
                (self.signal_ts + 5, missed, captured),
            )
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (captured, 10, "captured", "curve", 1e-7, 100,
                 self.signal_ts + 12, 123),
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 20
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        self.assertEqual(stats["ten_second_coverage_last_24h"], {
            "eligible": 3,
            "created_after_deadline": 1,
            "created_before_deadline_missing": 1,
            "created_before_deadline_captured": 1,
        })
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 15
        ):
            at_deadline = app.api_account_price_checkpoint_stats("test-token")
        self.assertEqual(
            at_deadline["ten_second_coverage_last_24h"]["eligible"], 0
        )

    def test_completed_primary_outcome_can_still_get_15m_account_checkpoint(self):
        outcome_id = self.outcome(basis="pump", mint="busy-mint")
        conn = app.db()
        try:
            conn.execute(
                "UPDATE signal_outcomes SET price_10s=1, price_30s=1, "
                "price_1m=1, price_5m=1, price_15m=1 WHERE id=?",
                (outcome_id,),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(app.complete_finished_signal_outcomes("busy-mint"), 1)
        snapshot = {"busy-mint": {
            "status": "curve", "price_sol": 1.1e-7,
            "market_cap_sol": 110, "slot": 123,
        }}
        with patch.object(app, "fetch_account_prices", return_value=snapshot):
            result = app.account_price_checkpoint_once(now=self.signal_ts + 902)
        self.assertEqual(result["checkpoints_recorded"], 1)
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT c.checkpoint_seconds, o.status "
                "FROM account_price_checkpoints c "
                "JOIN signal_outcomes o ON o.id=c.outcome_id "
                "WHERE o.id=?", (outcome_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (900, "completed"))
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 902
        ):
            eligibility = app.api_account_price_checkpoint_stats(
                "test-token"
            )["eligibility"]
        self.assertEqual(eligibility["eligible_outcomes_in_window"], 1)
        self.assertEqual(eligibility["active_outcomes_in_window"], 0)
        self.assertEqual(eligibility["due_now"], 0)

    def test_stats_compare_account_and_primary_prices_without_promoting_them(self):
        outcome_id = self.outcome(basis="pump", mint="curve-entry")
        conn = app.db()
        try:
            conn.execute(
                "UPDATE signal_outcomes SET price_10s=?, observed_10s_ts=? "
                "WHERE id=?",
                (1e-7, self.signal_ts + 13, outcome_id),
            )
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, 10, "curve-entry", "curve", 1.1e-7,
                    110, self.signal_ts + 12, 123,
                ),
            )
            conn.execute(
                """
                INSERT INTO account_price_checkpoints(
                    outcome_id, checkpoint_seconds, mint, pool,
                    price_sol, market_cap_sol, observed_ts, slot
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, 30, "curve-entry", "curve", 1.2e-7,
                    120, self.signal_ts + 32, 124,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app.time, "time", return_value=self.signal_ts + 40
        ):
            stats = app.api_account_price_checkpoint_stats("test-token")
        comparison = stats["comparison_last_24h"]
        self.assertEqual(comparison["10"]["primary_available"], 1)
        self.assertAlmostEqual(
            comparison["10"]["median_absolute_pct_difference"], 10.0
        )
        self.assertEqual(comparison["30"]["primary_missing"], 1)
        self.assertIsNone(
            comparison["30"]["median_absolute_pct_difference"]
        )
        recent = stats["recent_comparisons"]
        ten_second = next(
            item for item in recent if item["checkpoint_seconds"] == 10
        )
        self.assertEqual(ten_second["account_lag_seconds"], 2)
        self.assertEqual(ten_second["primary_lag_seconds"], 3)
        self.assertEqual(ten_second["observation_gap_seconds"], 1)
        self.assertAlmostEqual(ten_second["absolute_pct_difference"], 10.0)
        thirty_second = next(
            item for item in recent if item["checkpoint_seconds"] == 30
        )
        self.assertIsNone(thirty_second["primary_observed_ts"])
        self.assertIsNone(thirty_second["observation_gap_seconds"])
        conn = app.db()
        try:
            primary = conn.execute(
                "SELECT price_10s, price_30s FROM signal_outcomes WHERE id=?",
                (outcome_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(primary, (1e-7, None))

    def test_amm_entry_and_missed_checkpoint_are_not_backfilled(self):
        self.outcome(basis="unknown", mint="amm-entry")
        with patch.object(app, "fetch_account_prices") as fetch:
            result = app.account_price_checkpoint_once(now=self.signal_ts + 12)
        self.assertEqual(result["status"], "no_due_checkpoint")
        fetch.assert_not_called()
        self.outcome(basis="pump", mint="curve-entry")
        with patch.object(app, "fetch_account_prices") as fetch:
            result = app.account_price_checkpoint_once(now=self.signal_ts + 16)
        self.assertEqual(result["status"], "no_due_checkpoint")
        fetch.assert_not_called()

    def test_late_rpc_response_does_not_fill_old_checkpoint(self):
        self.outcome()
        with patch.object(app, "fetch_account_prices", return_value={
            "mint-a": {"status": "curve", "price_sol": 1e-7,
                       "market_cap_sol": 100, "slot": 123},
        }), patch.object(app.time, "time", side_effect=[
            self.signal_ts + 12, self.signal_ts + 16,
        ]):
            result = app.account_price_checkpoint_once(now=None)
        self.assertEqual(result["checkpoints_recorded"], 0)

    def test_shadow_dataset_labels_tp_before_sl_at_fixed_checkpoints(self):
        positive = self.checkpoint_dataset_outcome(
            1, [1.05, 1.30, 1.20, 0.80, 1.10]
        )
        negative = self.checkpoint_dataset_outcome(
            2, [0.85, 1.30, 1.20, 1.10, 1.00]
        )

        rows = app.get_account_checkpoint_dataset_rows()
        by_id = {row["signal_id"]: row for row in rows}

        self.assertEqual(by_id[positive]["target_tp25_before_sl10"], 1)
        self.assertEqual(by_id[positive]["tp_checkpoint_seconds"], 30)
        self.assertEqual(by_id[positive]["sl_checkpoint_seconds"], 300)
        self.assertEqual(by_id[negative]["target_tp25_before_sl10"], 0)
        self.assertEqual(by_id[negative]["sl_checkpoint_seconds"], 10)
        self.assertEqual(by_id[negative]["tp_checkpoint_seconds"], 30)
        self.assertEqual(
            by_id[positive]["label_source"], "account_checkpoints_v1"
        )

        test_minimums = {
            **app.ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS,
            "unique_traders": 1,
        }
        with patch.object(
            app.time,
            "time",
            return_value=self.signal_ts + 1000,
        ), patch.object(
            app,
            "build_model_features",
            side_effect=AssertionError("stats must not build model features"),
        ), patch.object(
            app,
            "ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS",
            test_minimums,
        ), patch.object(
            app,
            "get_account_checkpoint_subscription_coverage",
            return_value={
                positive: {
                    "measurement_available": True,
                    "complete": True,
                },
                negative: {
                    "measurement_available": True,
                    "complete": False,
                },
            },
        ):
            stats = app.get_account_checkpoint_training_stats()
        self.assertEqual(stats["complete_rows"], 2)
        self.assertEqual(stats["target_1"], 1)
        self.assertEqual(stats["target_0"], 1)
        self.assertEqual(stats["unique_mints"], 2)
        self.assertEqual(stats["unique_traders"], 1)
        self.assertFalse(stats["affects_decisions"])
        self.assertFalse(stats["readiness"]["ready_for_diagnostic"])
        self.assertEqual(
            stats["readiness"]["minimums"],
            test_minimums,
        )
        self.assertIn("complete_rows 2/300", stats["readiness"]["blockers"])
        self.assertEqual(
            stats["last_24h"],
            {"complete_rows": 2, "target_1": 1, "target_0": 1},
        )
        self.assertEqual(
            stats["readiness"]["estimated_days_at_last_24h_rate"],
            149.0,
        )
        self.assertEqual(
            stats["subscription_coverage"]["measured_observation_rows"],
            2,
        )
        self.assertEqual(
            stats["subscription_coverage"]["complete_observation_rows"],
            1,
        )
        self.assertEqual(
            stats["subscription_coverage"]["next_milestone"],
            25,
        )
        self.assertEqual(app.count_complete_account_checkpoint_paths(), 2)

        with patch.object(app, "APP_TOKEN", "test-token"):
            payload = app.api_account_checkpoint_training_dataset("test-token")
        self.assertEqual(payload["data_version"], app.DATA_VERSION)
        self.assertEqual(payload["label_source"], "account_checkpoints_v1")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["rows"]), 2)

    def test_shadow_dataset_requires_all_five_checkpoints(self):
        signal_id = self.checkpoint_dataset_outcome(
            3, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        conn = app.db()
        try:
            conn.execute(
                "DELETE FROM account_price_checkpoints "
                "WHERE outcome_id=(SELECT id FROM signal_outcomes "
                "WHERE signal_id=?) AND checkpoint_seconds=900",
                (signal_id,),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(app.get_account_checkpoint_dataset_rows(), [])

    def test_optional_event_path_uses_stored_history_without_rpc(self):
        signal_id = self.checkpoint_dataset_outcome(
            4, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 4 * 2_000
        mint = "mint-4"
        conn = app.db()
        try:
            conn.executemany(
                """
                INSERT INTO token_history(
                    ts, mint, market_cap_sol, trader, side,
                    signature, source, event_id
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (signal_ts - 1, mint, 110, "before", "buy",
                     "before", "live", "before:0"),
                    (signal_ts + 12, mint, 120, "alice", "buy",
                     "inside-1", "token-live", "inside-1:0"),
                    (signal_ts + 45, mint, 130, "bob", "sell",
                     "inside-2", "token-live", "inside-2:0"),
                    (signal_ts + 901, mint, 140, "after", "sell",
                     "after", "live", "after:0"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts,
                    unsubscribed_ts, close_reason
                ) VALUES(?,?,?,?,?)
                """,
                [
                    (mint, signal_ts + 10, signal_ts + 100,
                     signal_ts + 100, "disconnect"),
                    (mint, signal_ts + 90, signal_ts + 200,
                     signal_ts + 200, "disconnect"),
                    (mint, signal_ts + 250, signal_ts + 950,
                     signal_ts + 950, "unsubscribe_confirmed"),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        app.establish_helius_token_coverage_activation(now=signal_ts)

        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app, "fetch_account_prices"
        ) as fetch:
            payload = app.api_account_checkpoint_paths(
                "test-token",
                after_signal_ts=signal_ts - 1,
                include_event_path=True,
            )

        fetch.assert_not_called()
        self.assertTrue(payload["event_path_included"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["rows"][0]["signal_id"], signal_id)
        event_path = payload["rows"][0]["event_path"]
        self.assertEqual(
            [point["signature"] for point in event_path],
            ["inside-1", "inside-2"],
        )
        self.assertEqual(
            [point["elapsed_seconds"] for point in event_path],
            [12.0, 45.0],
        )
        self.assertAlmostEqual(event_path[0]["price_sol"], 1.2e-7)
        coverage = payload["rows"][0]["subscription_coverage"]
        self.assertTrue(coverage["measurement_available"])
        self.assertEqual(coverage["measurement_started_ts"], signal_ts)
        self.assertEqual(
            coverage["token_tracking_observable_from_ts"], signal_ts + 10
        )
        self.assertEqual(coverage["covered_seconds"], 840.0)
        self.assertAlmostEqual(coverage["coverage_ratio"], 840.0 / 900.0)
        self.assertEqual(coverage["first_subscription_delay_seconds"], 10.0)
        self.assertEqual(coverage["intervals"], 2)
        self.assertFalse(coverage["complete"])

    def test_subscription_coverage_marks_pre_instrumentation_history(self):
        signal_id = self.checkpoint_dataset_outcome(
            6, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 6 * 2_000
        app.establish_helius_token_coverage_activation(
            now=signal_ts + 1_000
        )

        coverage = app.get_account_checkpoint_subscription_coverage([{
            "signal_id": signal_id,
            "mint": "mint-6",
            "signal_ts": signal_ts,
        }])[signal_id]

        self.assertFalse(coverage["measurement_available"])
        self.assertEqual(coverage["covered_seconds"], 0)
        self.assertEqual(
            coverage["measurement_started_ts"], signal_ts + 1_000
        )

    def test_worker_start_alone_does_not_claim_token_coverage(self):
        signal_id = self.checkpoint_dataset_outcome(
            13, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 13 * 2_000
        app.establish_helius_token_coverage_activation(now=signal_ts - 100)
        row = {"signal_id": signal_id, "mint": "mint-13",
               "signal_ts": signal_ts}

        before = app.get_account_checkpoint_subscription_coverage([row])[
            signal_id
        ]
        self.assertFalse(before["measurement_available"])
        self.assertIsNone(before["token_tracking_observable_from_ts"])

        conn = app.db()
        try:
            conn.execute(
                """INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts, unsubscribed_ts
                ) VALUES(?,?,?,?)""",
                ("another-mint", signal_ts + 1_000, signal_ts + 1_100,
                 signal_ts + 1_100),
            )
            conn.commit()
        finally:
            conn.close()

        after = app.get_account_checkpoint_subscription_coverage([row])[
            signal_id
        ]
        self.assertFalse(after["measurement_available"])
        self.assertEqual(
            after["token_tracking_observable_from_ts"], signal_ts + 1_000
        )

        conn = app.db()
        try:
            conn.execute(
                """INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts, unsubscribed_ts
                ) VALUES(?,?,?,?)""",
                ("another-mint", signal_ts + 500, signal_ts + 600,
                 signal_ts + 600),
            )
            conn.commit()
        finally:
            conn.close()

        overlapping = app.get_account_checkpoint_subscription_coverage([row])[
            signal_id
        ]
        self.assertTrue(overlapping["measurement_available"])
        self.assertFalse(overlapping["complete"])

    def test_optional_subscription_trace_shows_source_and_nearby_timing(self):
        signal_id = self.checkpoint_dataset_outcome(
            12, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 12 * 2_000
        conn = app.db()
        try:
            conn.execute(
                "UPDATE evaluations SET transport = ? WHERE id = ?",
                ("helius", signal_id),
            )
            conn.execute(
                """INSERT INTO market_event_inbox(
                    signature, event_index, source, received_ts, event_json,
                    claimed_ts, processed_ts
                ) VALUES(?,?,?,?,?,?,?)""",
                ("signature-12", 0, "helius", signal_ts + 20, "{}",
                 signal_ts + 21, signal_ts + 22),
            )
            conn.execute(
                """INSERT INTO helius_standard_wss_transactions(
                    signature, first_received_ts, fetched_ts, status
                ) VALUES(?,?,?,?)""",
                ("signature-12", signal_ts + 15, signal_ts + 19,
                 "applied"),
            )
            conn.executemany(
                """INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts, unsubscribed_ts
                ) VALUES(?,?,?,?)""",
                [
                    ("mint-12", signal_ts - 100, signal_ts - 20,
                     signal_ts - 20),
                    ("mint-12", signal_ts + 1_000, signal_ts + 1_100,
                     signal_ts + 1_100),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(app, "APP_TOKEN", "test-token"):
            payload = app.api_account_checkpoint_paths(
                "test-token",
                after_signal_ts=signal_ts - 1,
                include_event_path=True,
                include_subscription_trace=True,
            )

        trace = payload["rows"][0]["ingest_trace"]
        self.assertTrue(payload["subscription_trace_included"])
        self.assertEqual(trace["source"], "helius")
        self.assertEqual(trace["transport"], "helius")
        self.assertEqual(trace["inbox_received_ts"], signal_ts + 20)
        self.assertEqual(trace["inbox_claimed_ts"], signal_ts + 21)
        self.assertEqual(trace["inbox_processed_ts"], signal_ts + 22)
        self.assertEqual(trace["wss_received_ts"], signal_ts + 15)
        self.assertEqual(trace["wss_fetched_ts"], signal_ts + 19)
        self.assertEqual(trace["previous_subscription_end_ts"], signal_ts - 20)
        self.assertEqual(trace["next_subscription_start_ts"], signal_ts + 1_000)
        self.assertIsNone(trace["first_token_notification_ts"])
        self.assertEqual(
            payload["rows"][0]["subscription_coverage"]["intervals"], 0
        )

    def test_subscription_details_separate_interval_closures_and_loss_reasons(self):
        signal_id = self.checkpoint_dataset_outcome(
            14, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 14 * 2_000
        conn = app.db()
        try:
            conn.executemany(
                """INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts,
                    unsubscribed_ts, close_reason
                ) VALUES(?,?,?,?,?)""",
                [
                    ("mint-14", signal_ts + 5, signal_ts + 100,
                     signal_ts + 100, "disconnect"),
                    ("mint-14", signal_ts + 120, signal_ts + 900,
                     signal_ts + 900, "unsubscribe_confirmed"),
                    ("other", signal_ts + 5, signal_ts + 900,
                     signal_ts + 900, "unrelated"),
                ],
            )
            for signature, received_ts, status, reason in (
                ("lost-1", signal_ts + 20, "queue_full",
                 "HELIUS_STANDARD_WSS_PRIORITY_RESERVE"),
                ("lost-2", signal_ts + 30, "queue_full",
                 "HELIUS_STANDARD_WSS_PRIORITY_RESERVE"),
                ("lost-3", signal_ts + 40, "queue_full",
                 "HELIUS_STANDARD_WSS_QUEUE_FULL"),
                ("late", signal_ts + 901, "queue_full",
                 "HELIUS_STANDARD_WSS_QUEUE_FULL"),
            ):
                conn.execute(
                    """INSERT INTO helius_standard_wss_notifications(
                        signature, wallet, received_ts, failed, pump_logs,
                        message_bytes, subject_type
                    ) VALUES(?,?,?,0,1,100,'token')""",
                    (signature, "mint-14", received_ts),
                )
                conn.execute(
                    """INSERT INTO helius_standard_wss_transactions(
                        signature, first_received_ts, status, last_error
                    ) VALUES(?,?,?,?)""",
                    (signature, received_ts, status, reason),
                )
            conn.commit()
        finally:
            conn.close()

        with patch.object(app, "APP_TOKEN", "test-token"):
            result = app.api_account_checkpoint_subscription_details(
                signal_id, "test-token"
            )

        self.assertFalse(result["affects_decisions"])
        self.assertEqual(
            [item["close_reason"] for item in result["intervals"]],
            ["disconnect", "unsubscribe_confirmed"],
        )
        self.assertEqual(
            {item["reason"]: item["count"]
             for item in result["delivery_losses"]},
            {
                "HELIUS_STANDARD_WSS_PRIORITY_RESERVE": 2,
                "HELIUS_STANDARD_WSS_QUEUE_FULL": 1,
            },
        )

    def test_subscription_coverage_accepts_bounded_startup_delay(self):
        signal_id = self.checkpoint_dataset_outcome(
            7, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 7 * 2_000
        app.establish_helius_token_coverage_activation(now=signal_ts)
        conn = app.db()
        try:
            conn.execute(
                """
                INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts,
                    unsubscribed_ts, close_reason
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "mint-7", signal_ts + 5, signal_ts + 900,
                    signal_ts + 900, "unsubscribe_confirmed",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        coverage = app.get_account_checkpoint_subscription_coverage([{
            "signal_id": signal_id,
            "mint": "mint-7",
            "signal_ts": signal_ts,
        }])[signal_id]

        self.assertTrue(coverage["measurement_available"])
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["first_subscription_delay_seconds"], 5.0)
        self.assertEqual(coverage["maximum_start_delay_seconds"], 10.0)
        self.assertAlmostEqual(coverage["coverage_ratio"], 895.0 / 900.0)

    def test_subscription_coverage_rejects_known_token_delivery_losses(self):
        signal_id = self.checkpoint_dataset_outcome(
            9, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 9 * 2_000
        app.establish_helius_token_coverage_activation(now=signal_ts)
        conn = app.db()
        try:
            conn.execute(
                """
                INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts,
                    unsubscribed_ts, close_reason
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "mint-9", signal_ts + 5, signal_ts + 900,
                    signal_ts + 900, "unsubscribe_confirmed",
                ),
            )
            for signature, wallet, subject_type, received_ts, status in (
                ("lost-queue", "mint-9", "token", signal_ts + 100,
                 "queue_full"),
                ("lost-cap", "mint-9", "token", signal_ts + 150,
                 "queue_full"),
                ("lost-fetch", "mint-9", "token", signal_ts + 200,
                 "fetch_failed"),
                ("lost-process", "mint-9", "token", signal_ts + 300,
                 "processing_failed"),
                ("other-token", "mint-other", "token", signal_ts + 400,
                 "queue_full"),
                ("wallet-only", "mint-9", "wallet", signal_ts + 500,
                 "queue_full"),
                ("too-late", "mint-9", "token", signal_ts + 901,
                 "queue_full"),
            ):
                conn.execute(
                    """
                    INSERT INTO helius_standard_wss_notifications(
                        signature, wallet, received_ts, failed, pump_logs,
                        message_bytes, subject_type
                    ) VALUES(?,?,?,0,1,100,?)
                    """,
                    (signature, wallet, received_ts, subject_type),
                )
                conn.execute(
                    """
                    INSERT INTO helius_standard_wss_transactions(
                        signature, first_received_ts, status, last_error
                    ) VALUES(?,?,?,?)
                    """,
                    (signature, received_ts, status,
                     "HELIUS_STANDARD_WSS_PRIORITY_RESERVE"
                     if signature == "lost-queue" else (
                         "HELIUS_STANDARD_WSS_MINT_PENDING_CAP"
                         if signature == "lost-cap" else None
                     )),
                )
            conn.commit()
        finally:
            conn.close()

        row = {"signal_id": signal_id, "mint": "mint-9",
               "signal_ts": signal_ts}
        coverage = app.get_account_checkpoint_subscription_coverage(
            [row]
        )[signal_id]
        self.assertTrue(coverage["subscription_continuous"])
        self.assertEqual(coverage["known_delivery_failures"], 4)
        self.assertFalse(coverage["priority_reserve_trace_available"])
        self.assertEqual(coverage["priority_reserve_rejections"], 1)
        self.assertEqual(coverage["mint_cap_rejections"], 1)
        self.assertFalse(coverage["complete"])
        progress = app.get_exit_subscription_coverage_progress([row])
        self.assertEqual(progress["known_delivery_loss_observation_rows"], 1)
        self.assertEqual(progress["complete_observation_rows"], 0)

        conn = app.db()
        try:
            conn.execute(
                "DELETE FROM helius_standard_wss_transactions "
                "WHERE signature IN ('lost-queue', 'lost-cap', "
                "'lost-fetch', 'lost-process')"
            )
            conn.commit()
        finally:
            conn.close()
        recovered = app.get_account_checkpoint_subscription_coverage(
            [row]
        )[signal_id]
        self.assertEqual(recovered["known_delivery_failures"], 0)
        self.assertTrue(recovered["complete"])

    def test_subscription_coverage_waits_for_pending_token_fetch(self):
        signal_id = self.checkpoint_dataset_outcome(
            10, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 10 * 2_000
        app.establish_helius_token_coverage_activation(now=signal_ts)
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO helius_standard_wss_token_intervals("
                "mint, subscribed_ts, last_confirmed_ts, unsubscribed_ts, "
                "close_reason) VALUES(?,?,?,?,?)",
                ("mint-10", signal_ts, signal_ts + 900,
                 signal_ts + 900, "unsubscribe_confirmed"),
            )
            for signature, wallet, subject_type in (
                ("pending-token", "mint-10", "token"),
                ("pending-wallet", "mint-10", "wallet"),
                ("pending-other", "mint-other", "token"),
            ):
                conn.execute(
                    "INSERT INTO helius_standard_wss_notifications("
                    "signature, wallet, received_ts, failed, pump_logs, "
                    "message_bytes, subject_type) VALUES(?,?,?,0,1,100,?)",
                    (signature, wallet, signal_ts + 100, subject_type),
                )
                conn.execute(
                    "INSERT INTO helius_standard_wss_transactions("
                    "signature, first_received_ts, status) "
                    "VALUES(?,?,'pending_fetch')",
                    (signature, signal_ts + 100),
                )
            conn.commit()
        finally:
            conn.close()

        row = {"signal_id": signal_id, "mint": "mint-10",
               "signal_ts": signal_ts}
        pending = app.get_account_checkpoint_subscription_coverage([row])[
            signal_id
        ]
        self.assertTrue(pending["subscription_continuous"])
        self.assertEqual(pending["known_delivery_failures"], 0)
        self.assertEqual(pending["unresolved_delivery_fetches"], 1)
        self.assertFalse(pending["complete"])
        progress = app.get_exit_subscription_coverage_progress([row])
        self.assertEqual(progress["unresolved_delivery_observation_rows"], 1)
        self.assertEqual(progress["known_delivery_loss_observation_rows"], 0)

        app.finish_helius_standard_wss_transaction(
            "pending-token", "applied", 1, now=signal_ts + 200
        )
        completed = app.get_account_checkpoint_subscription_coverage([row])[
            signal_id
        ]
        self.assertEqual(completed["unresolved_delivery_fetches"], 0)
        self.assertTrue(completed["complete"])
        self.assertEqual(
            app.get_exit_subscription_coverage_progress([row])[
                "unresolved_delivery_observation_rows"
            ], 0,
        )

    def test_subscription_coverage_rejects_excessive_startup_delay(self):
        signal_id = self.checkpoint_dataset_outcome(
            8, [1.05, 1.10, 1.15, 1.20, 1.30]
        )
        signal_ts = self.signal_ts + 8 * 2_000
        app.establish_helius_token_coverage_activation(now=signal_ts)
        conn = app.db()
        try:
            conn.execute(
                """
                INSERT INTO helius_standard_wss_token_intervals(
                    mint, subscribed_ts, last_confirmed_ts,
                    unsubscribed_ts, close_reason
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "mint-8", signal_ts + 11, signal_ts + 900,
                    signal_ts + 900, "unsubscribe_confirmed",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        coverage = app.get_account_checkpoint_subscription_coverage([{
            "signal_id": signal_id,
            "mint": "mint-8",
            "signal_ts": signal_ts,
        }])[signal_id]

        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["first_subscription_delay_seconds"], 11.0)

    def test_event_path_is_absent_by_default(self):
        self.checkpoint_dataset_outcome(
            5, [1.05, 1.10, 1.15, 1.20, 1.30]
        )

        with patch.object(app, "APP_TOKEN", "test-token"):
            payload = app.api_account_checkpoint_paths("test-token")

        self.assertFalse(payload["event_path_included"])
        self.assertFalse(payload["subscription_trace_included"])
        self.assertNotIn("event_path", payload["rows"][0])
        self.assertNotIn("subscription_coverage", payload["rows"][0])
        self.assertNotIn("ingest_trace", payload["rows"][0])

    def test_training_readiness_minimums_match_diagnostic_schema(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "training"
            / "schema_account_checkpoints_v1.json"
        )
        readiness = json.loads(
            schema_path.read_text(encoding="utf-8")
        )["readiness"]

        self.assertEqual(
            app.ACCOUNT_CHECKPOINT_TRAINING_MINIMUMS,
            {
                "complete_rows": readiness["minimum_completed_rows"],
                "target_0": readiness["minimum_target_0"],
                "target_1": readiness["minimum_target_1"],
                "unique_traders": readiness["minimum_unique_traders"],
            },
        )

    def test_shadow_label_rejects_invalid_checkpoint_price(self):
        with self.assertRaisesRegex(
            ValueError, "ACCOUNT_CHECKPOINT_PRICE_INVALID"
        ):
            app.account_checkpoint_target(1.0, [(10, 0, self.signal_ts + 10)])


if __name__ == "__main__":
    unittest.main()
