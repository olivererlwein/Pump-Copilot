import io
import struct
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import app
from solana_rpc_fallback import (
    PUMP_AMM_PROGRAM_ID,
    PUMP_AMM_BUY_EVENT,
    PUMP_PROGRAM_ID,
    PUMP_TRADE_EVENT,
    WSOL_MINT,
    _base58_encode,
    _rpc_request,
    parse_watched_wallet_pump_events,
)


SIGNATURE = "3" * 88
WALLET_RAW = bytes(range(1, 33))
MINT_RAW = bytes(range(33, 65))
POOL_RAW = bytes(range(65, 97))
WALLET = _base58_encode(WALLET_RAW)
MINT = _base58_encode(MINT_RAW)
POOL = _base58_encode(POOL_RAW)


def token_balance(owner, mint, amount, decimals=6, index=1):
    return {
        "accountIndex": index,
        "owner": owner,
        "mint": mint,
        "uiTokenAmount": {
            "amount": str(amount),
            "decimals": decimals,
        },
    }


def receipt_with_payload(payload, pre=None, post=None):
    import base64

    program_id = (
        PUMP_AMM_PROGRAM_ID
        if payload.startswith(PUMP_AMM_BUY_EVENT)
        else PUMP_PROGRAM_ID
    )

    return {
        "slot": 42,
        "blockTime": 1_700_000_000,
        "transaction": {
            "signatures": [SIGNATURE],
            "message": {
                "accountKeys": [
                    {"pubkey": WALLET, "signer": True},
                ]
            },
        },
        "meta": {
            "err": None,
            "logMessages": [
                f"Program {program_id} invoke [1]",
                "Program data: " + base64.b64encode(payload).decode("ascii")
            ],
            "preTokenBalances": pre or [],
            "postTokenBalances": post or [],
        },
    }


def pump_receipt():
    payload = (
        PUMP_TRADE_EVENT
        + MINT_RAW
        + struct.pack("<QQ?", 2_500_000_000, 12_500_000, True)
        + WALLET_RAW
        + struct.pack(
            "<qQQ",
            1_700_000_000,
            50_000_000_000,
            250_000_000_000_000,
        )
    )
    return receipt_with_payload(
        payload,
        pre=[token_balance(WALLET, MINT, 10_000_000)],
        post=[token_balance(WALLET, MINT, 22_500_000)],
    )


def pump_amm_receipt():
    amounts = [0] * 13
    amounts[0] = 20_000_000
    amounts[11] = 1_250_000_000
    payload = (
        PUMP_AMM_BUY_EVENT
        + struct.pack("<q13Q", 1_700_000_000, *amounts)
        + POOL_RAW
        + WALLET_RAW
    )
    return receipt_with_payload(
        payload,
        pre=[
            token_balance(POOL, MINT, 300_000_000_000_000, index=2),
            token_balance(POOL, WSOL_MINT, 30_000_000_000, 9, index=3),
        ],
        post=[
            token_balance(WALLET, MINT, 20_000_000, index=1),
            token_balance(POOL, MINT, 280_000_000_000_000, index=2),
            token_balance(POOL, WSOL_MINT, 31_250_000_000, 9, index=3),
        ],
    )


class RpcFallbackParserTests(unittest.TestCase):
    def test_parses_official_pump_trade_event(self):
        parsed = parse_watched_wallet_pump_events(
            pump_receipt(), WALLET, SIGNATURE
        )

        self.assertEqual(len(parsed), 1)
        event = parsed[0]["event"]
        self.assertEqual(event["txType"], "buy")
        self.assertEqual(event["mint"], MINT)
        self.assertEqual(event["solAmount"], 2.5)
        self.assertEqual(event["tokenAmount"], 12.5)
        self.assertEqual(event["newTokenBalance"], 22.5)
        self.assertAlmostEqual(event["marketCapSol"], 200.0)
        self.assertEqual(event["pool"], "pump")

    def test_parses_official_pumpswap_event_and_pool_price(self):
        parsed = parse_watched_wallet_pump_events(
            pump_amm_receipt(), WALLET, SIGNATURE
        )

        self.assertEqual(len(parsed), 1)
        event = parsed[0]["event"]
        self.assertEqual(event["txType"], "buy")
        self.assertEqual(event["mint"], MINT)
        self.assertEqual(event["solAmount"], 1.25)
        self.assertEqual(event["tokenAmount"], 20.0)
        self.assertAlmostEqual(event["marketCapSol"], 111.60714285714286)
        self.assertEqual(event["pool"], "pump-amm")

    def test_ignores_transfers_and_transactions_not_signed_by_wallet(self):
        receipt = pump_receipt()
        receipt["meta"]["logMessages"] = [
            "Program log: Instruction: TransferChecked"
        ]
        self.assertEqual(
            parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE),
            [],
        )

        receipt = pump_receipt()
        receipt["meta"]["logMessages"] = receipt["meta"]["logMessages"][1:]
        self.assertEqual(
            parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE),
            [],
        )

        receipt = pump_receipt()
        receipt["transaction"]["message"]["accountKeys"][0]["signer"] = False
        self.assertEqual(
            parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE),
            [],
        )


class RpcFallbackRequestTests(unittest.TestCase):
    def test_retries_rate_limit_then_returns_result(self):
        headers = Message()
        headers["Retry-After"] = "0"
        rate_limit = HTTPError(
            "https://rpc.test",
            429,
            "Too Many Requests",
            headers,
            None,
        )
        response = io.BytesIO(b'{"jsonrpc":"2.0","result":[]}')

        with patch(
            "solana_rpc_fallback.urlopen",
            side_effect=[rate_limit, response],
        ) as open_rpc, patch("solana_rpc_fallback.time.sleep") as sleep:
            result = _rpc_request("https://rpc.test", "getSlot", [])

        self.assertEqual(result, [])
        self.assertEqual(open_rpc.call_count, 2)
        self.assertTrue(sleep.called)

    def test_does_not_retry_authentication_failure(self):
        unauthorized = HTTPError(
            "https://rpc.test",
            401,
            "Unauthorized",
            Message(),
            None,
        )

        with patch(
            "solana_rpc_fallback.urlopen",
            side_effect=unauthorized,
        ) as open_rpc, patch("solana_rpc_fallback.time.sleep"):
            with self.assertRaises(HTTPError):
                _rpc_request("https://rpc.test", "getSlot", [])

        self.assertEqual(open_rpc.call_count, 1)


class RpcFallbackPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "rpc-fallback.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def parsed(self):
        return parse_watched_wallet_pump_events(
            pump_receipt(), WALLET, SIGNATURE
        )[0]

    def test_missing_event_can_later_reconcile_to_stream(self):
        self.assertTrue(
            app.record_rpc_fallback_event(
                "trader-a", WALLET, pump_receipt(), self.parsed()
            )
        )
        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            missing = app.reconcile_rpc_fallback_events()
        self.assertEqual(len(missing), 1)

        conn = app.db()
        conn.execute(
            """
            INSERT INTO trades(
                ts, trader, wallet, side, mint, sol,
                market_cap_sol, signature, source
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (1.0, "trader-a", WALLET, "buy", MINT, 2.5, 200.0,
             SIGNATURE, "live"),
        )
        conn.commit()
        conn.close()

        self.assertEqual(app.reconcile_rpc_fallback_events(), [])
        stats = app.get_rpc_fallback_stats()
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["missing"], 0)
        self.assertEqual(stats["matched_identity"], 1)

    def test_poll_does_not_reserve_signature_used_by_live_stream(self):
        signature_row = {
            "signature": SIGNATURE,
            "slot": 42,
            "err": None,
        }
        app.update_rpc_fallback_wallet_state(
            WALLET,
            "trader-a",
            last_signature="2" * 88,
            last_slot=41,
        )
        with patch.object(app, "WATCHED", {"trader-a": WALLET}), patch.object(
            app,
            "fetch_signatures_for_address",
            return_value=[signature_row],
        ), patch.object(
            app,
            "fetch_confirmed_transaction",
            return_value=pump_receipt(),
        ), patch.object(app, "save_trade") as save_trade, patch.object(
            app, "evaluate_buy"
        ) as evaluate_buy:
            result = app.poll_rpc_fallback_once()

        self.assertEqual(result["transactions_processed"], 1)
        save_trade.assert_not_called()
        evaluate_buy.assert_not_called()
        conn = app.db()
        reserved = conn.execute(
            "SELECT COUNT(*) FROM processed_signatures"
        ).fetchone()[0]
        observed = conn.execute(
            "SELECT COUNT(*) FROM rpc_fallback_events"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(reserved, 0)
        self.assertEqual(observed, 1)

    def test_first_poll_starts_now_without_partial_history(self):
        signature_row = {
            "signature": SIGNATURE,
            "slot": 42,
            "err": None,
        }
        with patch.object(app, "WATCHED", {"trader-a": WALLET}), patch.object(
            app,
            "fetch_signatures_for_address",
            return_value=[signature_row],
        ), patch.object(app, "fetch_confirmed_transaction") as fetch_transaction:
            result = app.poll_rpc_fallback_once()

        self.assertEqual(result["transactions_processed"], 0)
        fetch_transaction.assert_not_called()
        state = app.get_rpc_fallback_wallet_states()[WALLET]
        self.assertEqual(state["last_signature"], SIGNATURE)

    def test_failed_initial_state_still_bootstraps_current_head(self):
        signature_row = {
            "signature": SIGNATURE,
            "slot": 42,
            "err": None,
        }
        app.update_rpc_fallback_wallet_state(
            WALLET,
            "trader-a",
            last_error="HTTP Error 429",
        )

        with patch.object(app, "WATCHED", {"trader-a": WALLET}), patch.object(
            app,
            "fetch_signatures_for_address",
            return_value=[signature_row],
        ) as fetch_signatures, patch.object(
            app,
            "fetch_confirmed_transaction",
        ) as fetch_transaction:
            result = app.poll_rpc_fallback_once()

        self.assertEqual(result["transactions_processed"], 0)
        self.assertEqual(fetch_signatures.call_args.kwargs["limit"], 1)
        fetch_transaction.assert_not_called()
        state = app.get_rpc_fallback_wallet_states()[WALLET]
        self.assertEqual(state["last_signature"], SIGNATURE)

    def test_saturated_backlog_rebases_to_current_head(self):
        previous_signature = "2" * 88
        newest_signature = "4" * 88
        rows = [
            {
                "signature": newest_signature,
                "slot": 84,
                "err": None,
            }
        ] * 1000
        app.update_rpc_fallback_wallet_state(
            WALLET,
            "trader-a",
            last_signature=previous_signature,
            last_slot=41,
        )

        with patch.object(app, "WATCHED", {"trader-a": WALLET}), patch.object(
            app,
            "fetch_signatures_for_address",
            return_value=rows,
        ), patch.object(app, "fetch_confirmed_transaction") as fetch_transaction:
            result = app.poll_rpc_fallback_once()

        self.assertEqual(result["saturated_wallets"], ["trader-a"])
        fetch_transaction.assert_not_called()
        state = app.get_rpc_fallback_wallet_states()[WALLET]
        self.assertEqual(state["last_signature"], newest_signature)
        self.assertEqual(state["last_error"], "SIGNATURE_BACKLOG_REBASED")


if __name__ == "__main__":
    unittest.main()
