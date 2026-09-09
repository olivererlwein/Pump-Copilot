import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from solana_receipts import WSOL_MINT, parse_buy_receipt


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

    def record(self):
        return app.record_finalized_buy_position(self.order_id, self.fill, self.receipt)

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


if __name__ == "__main__":
    unittest.main()
