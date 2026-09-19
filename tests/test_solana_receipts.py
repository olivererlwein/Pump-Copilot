import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from solana_receipts import WSOL_MINT, parse_buy_receipt, parse_sell_receipt


SIGNATURE = "1" * 88
WALLET = "wallet-a"
MINT = "mint-a"


def token_entry(index=1, amount="9007199254740993", mint=MINT, decimals=6):
    return {"accountIndex": index, "owner": WALLET, "mint": mint,
            "uiTokenAmount": {"amount": amount, "decimals": decimals}}


def buy_receipt():
    return {
        "slot": 42, "blockTime": 1700000000, "version": 0,
        "transaction": {"signatures": [SIGNATURE], "message": {"accountKeys": [
            {"pubkey": WALLET, "signer": True},
            {"pubkey": "token-account", "signer": False},
            {"pubkey": "pool", "signer": False},
        ]}},
        "meta": {"err": None, "fee": 5000,
                 "preBalances": [2000000000, 0, 1000000000],
                 "postBalances": [1897955720, 2039280, 1100000000],
                 "preTokenBalances": [], "postTokenBalances": [token_entry()]},
    }


def sell_receipt(signature="2" * 88, sold="3000000000000000", credit=50000000):
    before = 9007199254740993
    sold_amount = int(sold)
    return {
        "slot": 43, "blockTime": 1700000060, "version": 0,
        "transaction": {"signatures": [signature], "message": {"accountKeys": [
            {"pubkey": WALLET, "signer": True},
            {"pubkey": "token-account", "signer": False},
            {"pubkey": "pool", "signer": False},
        ]}},
        "meta": {"err": None, "fee": 5000,
                 "preBalances": [1000000000, 2039280, 1100000000],
                 "postBalances": [1000000000 + credit, 2039280, 1050000000],
                 "preTokenBalances": [token_entry(amount=str(before))],
                 "postTokenBalances": [token_entry(amount=str(before - sold_amount))]},
    }


class ReceiptAccountingTests(unittest.TestCase):
    def parse(self, receipt):
        return parse_buy_receipt(receipt, SIGNATURE, WALLET, MINT)

    def test_exact_raw_amount_and_net_cost_including_account_deposit(self):
        fill = self.parse(buy_receipt())
        self.assertEqual(fill["token_amount_raw"], "9007199254740993")
        self.assertEqual(fill["token_amount"], "9007199254.740993")
        self.assertEqual(fill["net_sol_debit_lamports"], "102044280")
        self.assertEqual(fill["network_fee_lamports"], "5000")

    def test_counts_only_acquired_tokens_with_existing_holdings(self):
        receipt = buy_receipt()
        receipt["meta"]["preBalances"][1] = 2039280
        receipt["meta"]["preTokenBalances"] = [token_entry(amount="9007199254740000")]
        self.assertEqual(self.parse(receipt)["token_amount_raw"], "993")

    def test_existing_wrapped_sol_spend_is_part_of_cost(self):
        receipt = buy_receipt()
        receipt["transaction"]["message"]["accountKeys"].append(
            {"pubkey": "wrapped-account", "signer": False, "source": "lookupTable"})
        receipt["meta"]["preBalances"].append(1002039280)
        receipt["meta"]["postBalances"].append(902039280)
        receipt["meta"]["postBalances"][0] = 1997955720
        receipt["meta"]["preTokenBalances"].append(token_entry(3, "1000000000", WSOL_MINT, 9))
        receipt["meta"]["postTokenBalances"].append(token_entry(3, "900000000", WSOL_MINT, 9))
        self.assertEqual(self.parse(receipt)["net_sol_debit_lamports"], "102044280")

    def test_ambiguous_or_wrong_receipts_are_rejected(self):
        mutations = [
            lambda r: r["transaction"].update(signatures=["2" * 88]),
            lambda r: r["transaction"]["message"]["accountKeys"][0].update(signer=False),
            lambda r: r["meta"].update(err={"InstructionError": [0, "failed"]}),
            lambda r: r["meta"].pop("preTokenBalances"),
            lambda r: r["meta"]["postTokenBalances"][0].pop("owner"),
            lambda r: r["meta"]["postTokenBalances"][0].update(mint="wrong-mint"),
            lambda r: r["meta"]["postTokenBalances"][0]["uiTokenAmount"].update(amount=1.5),
            lambda r: r["meta"]["postTokenBalances"][0]["uiTokenAmount"].update(amount=str(2**64)),
            lambda r: r["meta"]["preBalances"].__setitem__(1, 2039280),
            lambda r: r["meta"]["postBalances"].pop(),
            lambda r: r["meta"]["postBalances"].__setitem__(0, 2000000000),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                receipt = buy_receipt()
                mutate(receipt)
                with self.assertRaises(ValueError):
                    self.parse(receipt)

    def test_rpc_requests_finalized_parsed_receipt_and_handles_unavailable(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"result": None}).encode()
        with patch.object(app, "urlopen", return_value=response) as fetch:
            self.assertIsNone(app.fetch_finalized_solana_transaction(SIGNATURE))
        payload = json.loads(fetch.call_args.args[0].data)
        self.assertEqual(payload["method"], "getTransaction")
        self.assertEqual(payload["params"][1], {
            "encoding": "jsonParsed", "commitment": "finalized",
            "maxSupportedTransactionVersion": 0,
        })
        for payload in ({"error": {}}, {"result": []}, {}):
            response.read.return_value = json.dumps(payload).encode()
            with patch.object(app, "urlopen", return_value=response), self.assertRaises(ValueError):
                app.fetch_finalized_solana_transaction(SIGNATURE)

    def test_sell_receipt_reports_exact_tokens_and_net_proceeds(self):
        receipt = sell_receipt()
        fill = parse_sell_receipt(receipt, "2" * 88, WALLET, MINT)
        self.assertEqual(fill["token_amount_raw"], "3000000000000000")
        self.assertEqual(fill["net_sol_credit_lamports"], "50000000")
        with self.assertRaises(ValueError):
            parse_buy_receipt(receipt, "2" * 88, WALLET, MINT)


class LiveReceiptPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db_patch = patch.object(app, "DB", Path(self.directory.name) / "receipts.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        network = patch.object(app, "urlopen", side_effect=AssertionError("Unexpected network"))
        network.start()
        self.addCleanup(network.stop)
        app.migrate_database()
        self.addCleanup(app.TRACKED_TOKENS.discard, MINT)
        self.addCleanup(app.SUBSCRIBED_TOKENS.discard, MINT)
        self.addCleanup(app.TOKENS_TO_UNSUBSCRIBE.discard, MINT)
        self.order_id = self.make_order("first")
        self.receipt = buy_receipt()
        self.fill = parse_buy_receipt(self.receipt, SIGNATURE, WALLET, MINT)

    def make_order(self, key):
        order = app.create_execution_order_idempotent(
            MINT, "buy", 1, 1, 1, 100, key, source="pumpportal_lightning", mode="live")
        conn = app.db()
        conn.execute("UPDATE execution_orders SET status = 'PENDING_RECONCILIATION', "
                     "external_signature = ?, trade_wallet = ? WHERE id = ?",
                     (SIGNATURE, WALLET, order["order_id"]))
        conn.commit()
        conn.close()
        return order["order_id"]

    def make_sell_order(self, key, amount, signature, exit_reason="", target_tp_stage=None):
        order = app.create_execution_order_idempotent(
            MINT, "sell", 0, 0, 0, 0, key, source="pumpportal_lightning",
            parent_order_id=self.order_id, mode="live")
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET status = 'PENDING_RECONCILIATION', "
            "external_signature = ?, trade_wallet = ?, requested_token_amount_raw = ?, "
            "exit_reason = ?, target_tp_stage = ? WHERE id = ?",
            (signature, WALLET, str(amount), exit_reason, target_tp_stage,
             order["order_id"]),
        )
        conn.commit()
        conn.close()
        return order["order_id"]

    def record(self):
        return app.record_finalized_buy_position(self.order_id, self.fill, self.receipt)

    def prepare_account_exit_position(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = ?, "
            "origin_trader = ? WHERE id = ?",
            (100.0, "origin-trader", self.order_id),
        )
        conn.commit()
        conn.close()
        self.record()

    @staticmethod
    def account_exit_state():
        return {
            "last_check_ts": None,
            "last_success_ts": None,
            "last_error": None,
            "status": "disabled",
            "open_positions": 0,
            "selected_mints": 0,
            "priced_mints": 0,
            "exit_results": 0,
        }

    def test_account_exit_monitor_observes_without_applying(self):
        self.prepare_account_exit_position()
        snapshots = {
            MINT: {
                "status": "curve",
                "market_cap_sol": 110.0,
                "price_sol": 0.001,
                "slot": 42,
            }
        }
        with patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_ENABLED", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_APPLY", False
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_STATE", self.account_exit_state()
        ), patch.object(
            app, "fetch_account_prices", return_value=snapshots
        ), patch.object(
            app, "evaluate_live_position_exit"
        ) as evaluate:
            status = app.live_account_exit_monitor_once(now=1000.0)

        evaluate.assert_not_called()
        self.assertEqual(status["status"], "observed")
        self.assertEqual(status["open_positions"], 1)
        self.assertEqual(status["selected_mints"], 1)
        self.assertEqual(status["priced_mints"], 1)
        self.assertFalse(status["affects_decisions"])
        self.assertFalse(status["ready"])
        self.assertIn(
            "LIVE_ACCOUNT_EXIT_MONITOR_NOT_APPLIED", status["blockers"]
        )

    def test_account_exit_monitor_is_ready_without_open_positions(self):
        with patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_ENABLED", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_APPLY", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_STATE", self.account_exit_state()
        ), patch.object(
            app, "fetch_account_prices"
        ) as fetch, patch.object(
            app, "evaluate_live_position_exit"
        ) as evaluate:
            status = app.live_account_exit_monitor_once(now=1000.0)

        fetch.assert_not_called()
        evaluate.assert_not_called()
        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "no_open_positions")
        self.assertEqual(status["open_positions"], 0)
        self.assertEqual(status["selected_mints"], 0)

    def test_account_exit_monitor_applies_price_only_exit_evaluation(self):
        self.prepare_account_exit_position()
        snapshots = {
            MINT: {
                "status": "amm",
                "market_cap_sol": 125.0,
                "price_sol": 0.002,
                "slot": 43,
            }
        }
        with patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_ENABLED", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_APPLY", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_STATE", self.account_exit_state()
        ), patch.object(
            app, "fetch_account_prices", return_value=snapshots
        ), patch.object(
            app,
            "evaluate_live_position_exit",
            return_value=[{"ok": True}],
        ) as evaluate:
            status = app.live_account_exit_monitor_once(now=1000.0)

        evaluate.assert_called_once_with(
            MINT,
            "",
            "",
            125.0,
            None,
            event_signature="",
            event_index=0,
            event_block_event_ts=None,
            entry_market_cap_source="account",
            account_price_sol=0.002,
            account_price_observed_ts=1000.0,
        )
        self.assertTrue(status["ready"])
        self.assertTrue(status["affects_decisions"])
        self.assertEqual(status["status"], "applied")
        self.assertEqual(status["exit_results"], 1)

    def test_account_exit_monitor_fails_closed_when_a_mint_is_unpriced(self):
        self.prepare_account_exit_position()
        with patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_ENABLED", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_APPLY", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_STATE", self.account_exit_state()
        ), patch.object(
            app,
            "fetch_account_prices",
            return_value={MINT: {"status": "pool_missing", "slot": 44}},
        ), patch.object(
            app, "evaluate_live_position_exit"
        ) as evaluate:
            status = app.live_account_exit_monitor_once(now=1000.0)

        evaluate.assert_not_called()
        self.assertFalse(status["ready"])
        self.assertEqual(
            status["last_error"], "LIVE_ACCOUNT_EXIT_UNPRICED_MINTS"
        )
        self.assertIn(
            "LIVE_ACCOUNT_EXIT_MONITOR_UNHEALTHY", status["blockers"]
        )
        self.assertIn(
            "LIVE_ACCOUNT_EXIT_MONITOR_INCOMPLETE", status["blockers"]
        )

    def test_account_exit_monitor_reports_rejected_exit_evaluation(self):
        self.prepare_account_exit_position()
        snapshots = {
            MINT: {
                "status": "curve",
                "market_cap_sol": 80.0,
                "price_sol": 0.0008,
                "slot": 45,
            }
        }
        with patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_ENABLED", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_APPLY", True
        ), patch.object(
            app, "LIVE_ACCOUNT_EXIT_MONITOR_STATE", self.account_exit_state()
        ), patch.object(
            app, "fetch_account_prices", return_value=snapshots
        ), patch.object(
            app,
            "evaluate_live_position_exit",
            return_value=[{"ok": False, "reason": "LIVE_EXECUTION_NOT_READY"}],
        ):
            status = app.live_account_exit_monitor_once(now=1000.0)

        self.assertFalse(status["ready"])
        self.assertEqual(
            status["last_error"], "LIVE_ACCOUNT_EXIT_EVALUATION_FAILED"
        )
        self.assertIn(
            "LIVE_ACCOUNT_EXIT_MONITOR_UNHEALTHY", status["blockers"]
        )

    def test_account_exit_uses_confirmed_cost_without_overwriting_event_basis(self):
        self.prepare_account_exit_position()
        current_market_cap = 15.0
        current_price = 1.5e-11
        expected_entry_market_cap = (
            float(self.fill["cash_cost_per_token_sol"])
            * (current_market_cap / current_price)
        )
        with patch.object(
            app,
            "execute_pumpportal_lightning_sell",
            return_value={"ok": True, "status": "PENDING_RECONCILIATION"},
        ) as execute:
            result = app.evaluate_live_position_exit(
                MINT,
                "",
                "",
                current_market_cap,
                None,
                entry_market_cap_source="account",
                account_price_sol=current_price,
                account_price_observed_ts=2000.0,
            )
            out_of_order = app.evaluate_live_position_exit(
                MINT,
                "",
                "",
                current_market_cap,
                None,
                entry_market_cap_source="account",
                account_price_sol=current_price,
                account_price_observed_ts=1999.0,
            )

        self.assertTrue(result[0]["ok"])
        self.assertEqual(
            out_of_order[0]["reason"],
            "ACCOUNT_PRICE_BEFORE_LAST_OBSERVATION",
        )
        self.assertEqual(execute.call_args.kwargs["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(execute.call_args.kwargs["target_tp_stage"], 1)
        conn = app.db()
        try:
            state = conn.execute(
                "SELECT entry_market_cap_sol, current_market_cap_sol, "
                "account_exit_entry_market_cap_sol, "
                "account_exit_current_market_cap_sol, "
                "account_exit_entry_observed_ts, "
                "account_exit_last_observed_ts "
                "FROM live_positions WHERE order_id = ?",
                (self.order_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(state[0:2], (100.0, 100.0))
        self.assertAlmostEqual(state[2], expected_entry_market_cap)
        self.assertEqual(state[3:], (15.0, 2000.0, 2000.0))

    def test_account_exit_rejects_event_identity_and_trader_context(self):
        self.prepare_account_exit_position()
        with self.assertRaisesRegex(ValueError, "ACCOUNT_EXIT_CONTEXT_INVALID"):
            app.evaluate_live_position_exit(
                MINT,
                "origin-trader",
                "sell",
                15.0,
                0,
                event_signature="event-signature",
                event_index=0,
                entry_market_cap_source="account",
                account_price_sol=1.5e-11,
                account_price_observed_ts=2000.0,
            )

    def test_account_exit_rejects_partial_baseline_state(self):
        self.prepare_account_exit_position()
        conn = app.db()
        conn.execute(
            "UPDATE live_positions SET "
            "account_exit_entry_market_cap_sol = 10, "
            "account_exit_entry_observed_ts = NULL "
            "WHERE order_id = ?",
            (self.order_id,),
        )
        conn.commit()
        conn.close()
        with patch.object(app, "execute_pumpportal_lightning_sell") as execute:
            result = app.evaluate_live_position_exit(
                MINT,
                "",
                "",
                15.0,
                None,
                entry_market_cap_source="account",
                account_price_sol=1.5e-11,
                account_price_observed_ts=2000.0,
            )

        execute.assert_not_called()
        self.assertEqual(
            result[0]["reason"], "INVALID_ACCOUNT_EXIT_BASELINE_STATE"
        )

    def test_concurrent_reconciliation_records_position_and_event_once(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.record(), range(2)))
        self.assertTrue(all(result["ok"] for result in results))
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM live_positions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM execution_order_events "
                                      "WHERE status = 'CONFIRMED'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT remaining_amount_raw FROM live_positions").fetchone()[0],
                         "9007199254740993")
        conn.close()
        self.assertEqual(app.count_open_positions(mode="live"), 1)
        self.assertEqual(app.count_open_positions(mode="paper"), 0)
        self.assertTrue(self.record()["ok"])

    def test_event_failure_rolls_back_position_and_order_then_retry_succeeds(self):
        conn = app.db()
        conn.execute("CREATE TRIGGER reject_confirmation BEFORE INSERT ON execution_order_events "
                     "WHEN NEW.status = 'CONFIRMED' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            self.record()
        self.assertEqual(app.get_execution_order_status(self.order_id)["status"], "PENDING_RECONCILIATION")
        conn = app.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM live_positions").fetchone()[0], 0)
        conn.execute("DROP TRIGGER reject_confirmation")
        conn.commit()
        conn.close()
        self.assertTrue(self.record()["ok"])

    def test_signature_cannot_create_positions_for_two_orders(self):
        self.record()
        other = self.make_order("second")
        with self.assertRaises(sqlite3.IntegrityError):
            app.record_finalized_buy_position(other, self.fill, self.receipt)
        self.assertEqual(app.get_execution_order_status(other)["status"], "PENDING_RECONCILIATION")

    def test_identity_and_manual_confirmation_cannot_bypass_receipt(self):
        altered = dict(self.fill, wallet="other-wallet")
        with self.assertRaises(ValueError):
            app.record_finalized_buy_position(self.order_id, altered, self.receipt)
        result = app.reconcile_execution_order(self.order_id, "CONFIRMED")
        self.assertEqual(result["reason"], "LIVE_RECEIPT_REQUIRED")
        self.assertFalse(app.set_execution_order_external_signature(self.order_id, "2" * 88))

    def test_late_submission_update_cannot_downgrade_confirmed_order(self):
        self.record()
        app.update_execution_order(self.order_id, "PENDING_RECONCILIATION", "late response")
        self.assertEqual(app.get_execution_order_status(self.order_id)["status"], "CONFIRMED")

    def test_finalized_failure_marks_failed_without_a_position(self):
        with patch.object(app, "fetch_solana_signature_status", return_value={
            "failed": True, "finalized": False, "confirmation_status": "finalized",
        }):
            result = app.reconcile_pumpportal_execution_order(self.order_id)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(app.count_open_positions(mode="live"), 0)

    def test_worker_waits_for_receipt_and_then_creates_position(self):
        with patch.object(app, "fetch_solana_signature_status", return_value={
            "failed": False, "finalized": True, "confirmation_status": "finalized",
        }), patch.object(app, "fetch_finalized_solana_transaction", return_value=None) as fetch:
            self.assertFalse(app.reconcile_pumpportal_execution_order(self.order_id)["ok"])
            fetch.return_value = self.receipt
            self.assertTrue(app.reconcile_pumpportal_execution_order(self.order_id)["ok"])

    def test_unfinalized_failure_and_invalid_receipt_keep_order_pending(self):
        with patch.object(app, "fetch_solana_signature_status", return_value={
            "failed": True, "finalized": False, "confirmation_status": "confirmed",
        }):
            self.assertFalse(app.reconcile_pumpportal_execution_order(self.order_id)["ok"])
        with patch.object(app, "fetch_solana_signature_status", return_value={
            "failed": False, "finalized": True,
        }), patch.object(app, "fetch_finalized_solana_transaction", return_value={}):
            self.assertEqual(app.reconcile_pumpportal_execution_order(self.order_id)["reason"],
                             "SOLANA_RECEIPT_REVIEW_REQUIRED")
        self.assertEqual(app.get_execution_order_status(self.order_id)["status"], "PENDING_RECONCILIATION")

    def test_partial_then_final_sale_allocates_all_cost_and_closes_position(self):
        self.record()
        first_amount = 3000000000000000
        first_signature = "2" * 88
        first_order = self.make_sell_order("sell-one", first_amount, first_signature)
        first_receipt = sell_receipt(first_signature, str(first_amount), 50000000)
        first_fill = parse_sell_receipt(first_receipt, first_signature, WALLET, MINT)
        first = app.record_finalized_sell_position(first_order, first_fill, first_receipt)
        self.assertEqual(first["position_status"], "open")

        remaining = 9007199254740993 - first_amount
        second_signature = "3" * 88
        second_order = self.make_sell_order("sell-two", remaining, second_signature)
        second_receipt = sell_receipt(second_signature, str(remaining), 60000000)
        second_fill = parse_sell_receipt(second_receipt, second_signature, WALLET, MINT)
        second = app.record_finalized_sell_position(second_order, second_fill, second_receipt)
        self.assertEqual(second["position_status"], "closed")

        conn = app.db()
        position = conn.execute(
            "SELECT status, remaining_amount_raw, remaining_cost_basis_lamports "
            "FROM live_positions WHERE order_id = ?", (self.order_id,),
        ).fetchone()
        sales = conn.execute(
            "SELECT allocated_cost_basis_lamports, realized_pnl_lamports "
            "FROM live_position_sales ORDER BY sell_order_id",
        ).fetchall()
        conn.close()
        self.assertEqual(position, ("closed", "0", "0"))
        self.assertEqual(sum(int(row[0]) for row in sales), 102044280)
        self.assertEqual(sum(int(row[1]) for row in sales), 110000000 - 102044280)
        self.assertEqual(app.count_open_positions(mode="live"), 0)
        self.assertTrue(app.record_finalized_sell_position(
            second_order, second_fill, second_receipt)["ok"])

    def test_sell_receipt_cannot_exceed_reserved_or_remaining_amount(self):
        self.record()
        signature = "2" * 88
        order = self.make_sell_order("sell-too-much", 100, signature)
        receipt = sell_receipt(signature, "101")
        fill = parse_sell_receipt(receipt, signature, WALLET, MINT)
        with self.assertRaises(ValueError):
            app.record_finalized_sell_position(order, fill, receipt)
        self.assertEqual(app.get_execution_order_status(order)["status"],
                         "PENDING_RECONCILIATION")

    def test_sell_write_failure_rolls_back_position_and_order(self):
        self.record()
        signature = "2" * 88
        order = self.make_sell_order("sell-rollback", 100, signature)
        receipt = sell_receipt(signature, "100")
        fill = parse_sell_receipt(receipt, signature, WALLET, MINT)
        conn = app.db()
        conn.execute(
            "CREATE TRIGGER reject_sale_confirmation BEFORE INSERT ON "
            "execution_order_events WHEN NEW.status = 'CONFIRMED' "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            app.record_finalized_sell_position(order, fill, receipt)
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT remaining_amount_raw FROM live_positions WHERE order_id = ?",
            (self.order_id,),
        ).fetchone()[0], "9007199254740993")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM live_position_sales"
        ).fetchone()[0], 0)
        conn.close()
        self.assertEqual(app.get_execution_order_status(order)["status"],
                         "PENDING_RECONCILIATION")

    def test_reconciler_routes_finalized_sell_to_linked_position(self):
        self.record()
        signature = "2" * 88
        order = self.make_sell_order("sell-worker", 100, signature)
        receipt = sell_receipt(signature, "100")
        with patch.object(app, "fetch_solana_signature_status", return_value={
            "failed": False, "finalized": True, "confirmation_status": "finalized",
        }), patch.object(app, "fetch_finalized_solana_transaction", return_value=receipt):
            result = app.reconcile_pumpportal_execution_order(order)
        self.assertTrue(result["ok"])
        self.assertEqual(result["position_status"], "open")

    def test_live_daily_loss_uses_current_sol_quote_and_fails_closed(self):
        self.record()
        signature = "2" * 88
        amount = 3000000000000000
        order = self.make_sell_order("sell-loss", amount, signature)
        receipt = sell_receipt(signature, str(amount), 1000000)
        receipt["blockTime"] = time.time()
        fill = parse_sell_receipt(receipt, signature, WALLET, MINT)
        app.record_finalized_sell_position(order, fill, receipt)
        self.assertLess(app.get_daily_live_realized_pnl_sol(), 0)
        with patch.object(app, "fetch_sol_usd_quote", return_value={"price": 200.0}):
            result = app.risk_check(MINT, 1, mode="live")
        self.assertEqual(result["reason"], "MAX_DAILY_LOSS")
        with patch.object(app, "fetch_sol_usd_quote", side_effect=RuntimeError("offline")):
            result = app.risk_check(MINT, 1, mode="live")
        self.assertEqual(result["reason"], "SOL_USD_QUOTE_UNAVAILABLE")

    def test_live_position_summary_exposes_accounting_without_raw_receipts(self):
        self.record()
        summary = app.api_live_positions(app.APP_TOKEN, 10)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["open"], 1)
        self.assertEqual(summary["positions"][0]["remaining_amount_raw"],
                         "9007199254740993")
        self.assertIsNone(
            summary["positions"][0]["account_exit_entry_market_cap_sol"]
        )
        self.assertIsNone(
            summary["positions"][0]["account_exit_last_observed_ts"]
        )
        self.assertNotIn("receipt_json", summary["positions"][0])

    def test_buy_receipt_preserves_exit_context_and_tracks_mint(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()
        conn = app.db()
        context = conn.execute(
            "SELECT entry_market_cap_sol, current_market_cap_sol, origin_trader, "
            "tp_stage, entry_block_time, last_applied_block_event_ts "
            "FROM live_positions WHERE order_id = ?", (self.order_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(
            context,
            (100.0, 100.0, "marcell", 0, 1700000000.0, 1700000000.0),
        )
        self.assertIn(MINT, app.TRACKED_TOKENS)

    def test_live_exit_rejects_pre_entry_and_out_of_order_events(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()

        with patch.object(
            app,
            "execute_pumpportal_lightning_sell",
            return_value={"ok": True, "status": "PENDING_RECONCILIATION"},
        ) as execute:
            before_entry = app.evaluate_live_position_exit(
                MINT, "other", "buy", 70, 1, "old-event", 0,
                event_block_event_ts=1699999999.0,
            )
            accepted = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "new-event", 0,
                event_block_event_ts=1700000001.0,
            )
            out_of_order = app.evaluate_live_position_exit(
                MINT, "other", "buy", 70, 1, "late-old-event", 0,
                event_block_event_ts=1700000000.5,
            )

        self.assertEqual(before_entry[0]["reason"], "EVENT_BEFORE_LIVE_ENTRY")
        self.assertTrue(accepted[0]["ok"])
        self.assertEqual(
            execute.call_args.kwargs["event_block_event_ts"],
            1700000001.0,
        )
        self.assertEqual(
            out_of_order[0]["reason"],
            "EVENT_BEFORE_LAST_LIVE_EVENT",
        )
        execute.assert_called_once()
        conn = app.db()
        try:
            state = conn.execute(
                "SELECT current_market_cap_sol, last_applied_block_event_ts "
                "FROM live_positions WHERE order_id = ?",
                (self.order_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(state, (130.0, 1700000001.0))

    def test_live_exit_accepts_event_in_the_entry_second(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()

        with patch.object(
            app,
            "execute_pumpportal_lightning_sell",
            return_value={"ok": True},
        ) as execute:
            result = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "same-second", 0,
                event_block_event_ts=1700000000.0,
            )

        self.assertTrue(result[0]["ok"])
        execute.assert_called_once()

    def test_tp_stage_advances_only_after_finalized_sell_receipt(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()
        decision = app.decide_live_position_exit(
            9007199254740993, 9007199254740993, 100, 160,
            "marcell", "other", "buy", 1, 0,
        )
        self.assertEqual(decision["target_tp_stage"], 2)
        self.assertEqual(decision["token_amount_raw"], str(9007199254740993 * 2 // 4))

        signature = "2" * 88
        amount = int(decision["token_amount_raw"])
        sell_order = self.make_sell_order(
            "tp-two", amount, signature, "TAKE_PROFIT", 2,
        )
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT tp_stage FROM live_positions WHERE order_id = ?", (self.order_id,),
        ).fetchone()[0], 0)
        conn.close()
        receipt = sell_receipt(signature, str(amount), 50000000)
        fill = parse_sell_receipt(receipt, signature, WALLET, MINT)
        app.record_finalized_sell_position(sell_order, fill, receipt)
        conn = app.db()
        state = conn.execute(
            "SELECT tp_stage, last_exit_reason FROM live_positions WHERE order_id = ?",
            (self.order_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(state, (2, "TAKE_PROFIT"))

    def test_disabled_live_mode_observes_but_never_creates_exit_order(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()
        with patch.object(app, "LIVE_TRADING", False):
            result = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "event-one",
            )
        self.assertEqual(result[0]["reason"], "LIVE_TRADING_DISABLED")
        conn = app.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM execution_orders WHERE side = 'sell'"
        ).fetchone()[0], 0)
        self.assertEqual(conn.execute(
            "SELECT current_market_cap_sol FROM live_positions WHERE order_id = ?",
            (self.order_id,),
        ).fetchone()[0], 130.0)
        conn.close()

    def test_repeated_tp_event_creates_only_one_reserved_sell(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app, "LIVE_EXECUTION_IMPLEMENTED", True,
        ), patch.object(
            app, "LIVE_SELLS_ENABLED", True,
        ), patch.object(
            app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", WALLET,
        ), patch.object(
            app, "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app, "submit_pumpportal_lightning_trade",
            return_value={"signature": "2" * 88},
        ) as submit, patch.object(
            app, "build_pumpportal_exact_sell_payload",
            return_value={"action": "sell"},
        ):
            first = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "same-event",
            )
            second = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "same-event",
            )
        self.assertTrue(first[0]["ok"])
        self.assertEqual(second[0]["reason"], "IDEMPOTENT_REUSE")
        submit.assert_called_once()
        conn = app.db()
        order = conn.execute(
            "SELECT exit_reason, target_tp_stage FROM execution_orders "
            "WHERE side = 'sell'",
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM execution_orders WHERE side = 'sell'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)
        self.assertEqual(order, ("TAKE_PROFIT", 1))

    def test_exit_decision_prioritizes_loss_and_origin_trader_actions(self):
        stop = app.decide_live_position_exit(
            1000, 600, 100, 79, "marcell", "marcell", "sell", 0, 0,
        )
        self.assertEqual(stop["reason"], "STOP_LOSS")
        self.assertEqual(stop["token_amount_raw"], "600")

        full_exit = app.decide_live_position_exit(
            1000, 600, 100, 90, "marcell", "marcell", "sell", 0, 0,
        )
        self.assertEqual(full_exit["reason"], "TRADER_EXIT")
        self.assertEqual(full_exit["token_amount_raw"], "600")

        partial = app.decide_live_position_exit(
            1000, 600, 100, 90, "marcell", "marcell", "sell", 10, 0,
        )
        self.assertEqual(partial["reason"], "TRADER_PARTIAL")
        self.assertEqual(partial["token_amount_raw"], "250")
        missing_balance = app.decide_live_position_exit(
            1000, 600, 100, 90, "marcell", "marcell", "sell", None, 0,
        )
        self.assertEqual(missing_balance["reason"], "TRADER_PARTIAL")
        after_partial = app.decide_live_position_exit(
            1000, 750, 100, 130, "marcell", "other", "buy", 1, 0,
        )
        self.assertEqual(after_partial["reason"], "TAKE_PROFIT")
        self.assertEqual(after_partial["token_amount_raw"], "250")
        self.assertIsNone(app.decide_live_position_exit(
            1000, 600, 100, 90, "marcell", "other", "sell", 0, 0,
        ))

    def live_sells_enabled(self, submit_return):
        """Habilita el camino de venta live con el envío real interceptado.

        `submit_pumpportal_lightning_trade` es el único punto que toca la
        wallet; parcheado, nada sale a la red. El `setUp` además rompe
        `urlopen`, así que una llamada saliente fallaría en vez de salir.
        """
        return (
            patch.object(app, "LIVE_TRADING", True),
            patch.object(app, "LIVE_EXECUTION_IMPLEMENTED", True),
            patch.object(app, "LIVE_SELLS_ENABLED", True),
            patch.object(app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", WALLET),
            patch.object(app, "get_live_execution_readiness",
                         return_value={"ready": True, "blockers": []}),
            patch.object(app, "build_pumpportal_exact_sell_payload",
                         return_value={"action": "sell"}),
            patch.object(app, "submit_pumpportal_lightning_trade",
                         return_value=submit_return),
        )

    def origin_trader_position(self):
        conn = app.db()
        conn.execute(
            "UPDATE execution_orders SET entry_market_cap_sol = 100, "
            "origin_trader = 'marcell' WHERE id = ?", (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.record()

    def sell_orders(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT exit_reason, idempotency_key FROM execution_orders "
                "AS o JOIN execution_idempotency AS i ON i.order_id = o.id "
                "WHERE o.side = 'sell' ORDER BY i.idempotency_key",
            ).fetchall()
        finally:
            conn.close()

    def partial_sell(self, signature, event_index):
        # El trader de origen vende parte: newTokenBalance > 0 lo distingue de
        # un cierre total.
        return app.evaluate_live_position_exit(
            MINT, "marcell", "sell", 90, 10, signature, event_index,
        )

    def test_partial_sells_of_one_transaction_are_separate_exits(self):
        self.origin_trader_position()
        contextos = self.live_sells_enabled(
            {"ok": True, "signature": "4" * 88,
             "reason": "PUMPPORTAL_SUBMITTED"},
        )
        with contextos[0], contextos[1], contextos[2], contextos[3], \
                contextos[4], contextos[5], contextos[6] as submit:
            primera = self.partial_sell("firma-compartida", 0)
            segunda = self.partial_sell("firma-compartida", 1)
            repetida = self.partial_sell("firma-compartida", 1)

        self.assertTrue(primera[0]["ok"])
        self.assertTrue(segunda[0]["ok"])
        # El mismo índice repetido no vuelve a vender.
        self.assertEqual(repetida[0]["reason"], "IDEMPOTENT_REUSE")

        # Dos operaciones, dos envíos. Con la firma sola eran uno solo, y la
        # segunda venta parcial —que debía ocurrir— se perdía.
        self.assertEqual(submit.call_count, 2)
        claves = [fila[1] for fila in self.sell_orders()]
        self.assertEqual(claves, [
            f"LIVE-EXIT-{self.order_id}-TRADER_PARTIAL-firma-compartida:0",
            f"LIVE-EXIT-{self.order_id}-TRADER_PARTIAL-firma-compartida:1",
        ])

    def test_take_profit_stays_idempotent_per_stage(self):
        # El índice no debe meterse en la clave de TAKE_PROFIT: dos eventos
        # distintos con el precio arriba del mismo escalón venden una vez.
        self.origin_trader_position()
        contextos = self.live_sells_enabled({"signature": "2" * 88})
        with contextos[0], contextos[1], contextos[2], contextos[3], \
                contextos[4], contextos[5], contextos[6] as submit:
            primera = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "firma-compartida", 0,
            )
            segunda = app.evaluate_live_position_exit(
                MINT, "other", "buy", 130, 1, "firma-compartida", 1,
            )

        self.assertTrue(primera[0]["ok"])
        self.assertEqual(segunda[0]["reason"], "IDEMPOTENT_REUSE")
        submit.assert_called_once()
        self.assertEqual(
            [fila[1] for fila in self.sell_orders()],
            [f"LIVE-EXIT-{self.order_id}-TP-1"],
        )

    def test_full_closes_stay_idempotent_per_position(self):
        # STOP_LOSS y TRADER_EXIT cierran todo: el índice tampoco entra acá.
        self.origin_trader_position()
        contextos = self.live_sells_enabled({"signature": "2" * 88})
        with contextos[0], contextos[1], contextos[2], contextos[3], \
                contextos[4], contextos[5], contextos[6] as submit:
            primera = app.evaluate_live_position_exit(
                MINT, "other", "buy", 70, 1, "firma-compartida", 0,
            )
            segunda = app.evaluate_live_position_exit(
                MINT, "other", "buy", 70, 1, "firma-compartida", 1,
            )

        self.assertTrue(primera[0]["ok"])
        self.assertEqual(segunda[0]["reason"], "IDEMPOTENT_REUSE")
        submit.assert_called_once()
        self.assertEqual(
            [fila[1] for fila in self.sell_orders()],
            [f"LIVE-EXIT-{self.order_id}-STOP_LOSS"],
        )

    def test_pumpportal_event_without_index_keeps_its_key(self):
        # PumpPortal entrega una operación por mensaje y no manda índice: la
        # identidad es firma:0, y el mismo evento sigue siendo uno solo.
        self.origin_trader_position()
        contextos = self.live_sells_enabled(
            {"ok": True, "signature": "4" * 88,
             "reason": "PUMPPORTAL_SUBMITTED"},
        )
        with contextos[0], contextos[1], contextos[2], contextos[3], \
                contextos[4], contextos[5], contextos[6] as submit:
            app.evaluate_live_position_exit(
                MINT, "marcell", "sell", 90, 10, "firma-sola",
            )
            repetida = app.evaluate_live_position_exit(
                MINT, "marcell", "sell", 90, 10, "firma-sola",
            )

        self.assertEqual(repetida[0]["reason"], "IDEMPOTENT_REUSE")
        submit.assert_called_once()
        self.assertEqual(
            [fila[1] for fila in self.sell_orders()],
            [f"LIVE-EXIT-{self.order_id}-TRADER_PARTIAL-firma-sola:0"],
        )

    def test_partial_sell_without_signature_is_refused(self):
        self.origin_trader_position()
        with patch.object(app, "submit_pumpportal_lightning_trade") as submit:
            result = app.evaluate_live_position_exit(
                MINT, "marcell", "sell", 90, 10, "", 0,
            )

        self.assertEqual(result[0]["reason"], "EVENT_SIGNATURE_REQUIRED")
        submit.assert_not_called()

    def test_broken_index_is_rejected_before_any_order(self):
        self.origin_trader_position()
        with patch.object(app, "submit_pumpportal_lightning_trade") as submit:
            with self.assertRaises(ValueError):
                app.evaluate_live_position_exit(
                    MINT, "marcell", "sell", 90, 10, "firma-sola", "1",
                )

        submit.assert_not_called()
        self.assertEqual(self.sell_orders(), [])

    def test_sell_reservation_rejects_event_older_than_position_state(self):
        self.record()
        conn = app.db()
        conn.execute(
            "UPDATE live_positions SET last_applied_block_event_ts = ? "
            "WHERE order_id = ?",
            (1700000010.0, self.order_id),
        )
        conn.commit()
        conn.close()

        contextos = self.live_sells_enabled({"signature": "2" * 88})
        with contextos[0], contextos[1], contextos[2], contextos[3], \
                contextos[4], contextos[5], contextos[6] as submit:
            result = app.execute_pumpportal_lightning_sell(
                position_order_id=self.order_id,
                token_amount_raw="1",
                idempotency_key="stale-event-reservation",
                event_block_event_ts=1700000005.0,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "STALE_LIVE_EXIT_EVENT")
        submit.assert_not_called()
        self.assertEqual(self.sell_orders(), [])

    def test_exact_sell_payload_does_not_use_wallet_percentage(self):
        mint = "1" * 32
        payload = app.build_pumpportal_exact_sell_payload(mint, "1234567", 6)
        self.assertEqual(payload["amount"], "1.234567")
        self.assertEqual(payload["denominatedInSol"], "false")

    def test_simultaneous_sell_orders_cannot_reserve_same_tokens(self):
        self.record()
        amount = 6000000000000000
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app, "LIVE_EXECUTION_IMPLEMENTED", True,
        ), patch.object(
            app, "LIVE_SELLS_ENABLED", True,
        ), patch.object(
            app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", WALLET,
        ), patch.object(
            app, "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app, "build_pumpportal_exact_sell_payload",
            return_value={"action": "sell"},
        ), patch.object(
            app, "submit_pumpportal_lightning_trade",
            return_value={"ok": True, "signature": "4" * 88,
                          "reason": "PUMPPORTAL_SUBMITTED"},
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda key: app.execute_pumpportal_lightning_sell(
                        self.order_id, amount, key),
                    ("concurrent-sell-a", "concurrent-sell-b"),
                ))
        self.assertEqual(sum(bool(result["ok"]) for result in results), 1)
        self.assertIn("SELL_AMOUNT_EXCEEDS_AVAILABLE_POSITION",
                      {result["reason"] for result in results})


if __name__ == "__main__":
    unittest.main()
