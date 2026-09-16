import io
import json
import struct
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import AsyncMock, patch
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
    parse_tracked_token_pump_events,
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
        self.assertEqual(event["traderPublicKey"], WALLET)

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
        self.assertEqual(event["traderPublicKey"], WALLET)

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


class TrackedTokenParserTests(unittest.TestCase):
    def test_parses_tracked_mint_without_requiring_a_watched_wallet(self):
        receipt = pump_receipt()
        receipt["transaction"]["message"]["accountKeys"][0] = {
            "pubkey": "another-fee-payer",
            "signer": True,
        }

        events = parse_tracked_token_pump_events(
            receipt, {MINT}, SIGNATURE
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"]["mint"], MINT)
        self.assertEqual(events[0]["event"]["traderPublicKey"], WALLET)

    def test_ignores_an_untracked_mint(self):
        self.assertEqual(
            parse_tracked_token_pump_events(
                pump_receipt(), {"another-mint"}, SIGNATURE
            ),
            [],
        )

    def test_multiple_operations_keep_global_ordinals(self):
        receipt = pump_receipt()
        receipt["meta"]["logMessages"].append(
            receipt["meta"]["logMessages"][-1]
        )

        events = parse_tracked_token_pump_events(receipt, {MINT}, SIGNATURE)

        self.assertEqual(
            [row["event"]["eventIndex"] for row in events],
            [0, 1],
        )

    def test_ordinal_does_not_depend_on_an_earlier_event_decoding(self):
        import base64

        other_mint_raw = bytes(range(97, 129))
        other_mint = _base58_encode(other_mint_raw)
        prior_payload = (
            PUMP_TRADE_EVENT
            + other_mint_raw
            + struct.pack("<QQ?", 1_000_000_000, 5_000_000, True)
            + WALLET_RAW
            + struct.pack(
                "<qQQ",
                1_699_999_999,
                40_000_000_000,
                200_000_000_000_000,
            )
        )

        receipt = pump_receipt()
        receipt["meta"]["logMessages"].insert(
            -1,
            "Program data: "
            + base64.b64encode(prior_payload).decode("ascii"),
        )

        without_prior_balances = parse_tracked_token_pump_events(
            receipt, {MINT}, SIGNATURE
        )

        receipt["meta"]["preTokenBalances"].append(
            token_balance(WALLET, other_mint, 10_000_000, index=2)
        )
        receipt["meta"]["postTokenBalances"].append(
            token_balance(WALLET, other_mint, 15_000_000, index=2)
        )
        with_prior_balances = parse_tracked_token_pump_events(
            receipt, {MINT}, SIGNATURE
        )

        self.assertEqual(
            [row["event"]["eventIndex"] for row in without_prior_balances],
            [1],
        )
        self.assertEqual(
            [row["event"]["eventIndex"] for row in with_prior_balances],
            [1],
        )

    def test_missing_user_balance_keeps_price_event_with_unknown_balance(self):
        pump = pump_receipt()
        pump["meta"]["preTokenBalances"] = [
            token_balance("pool-owner", MINT, 20_000_000, index=2)
        ]
        pump["meta"]["postTokenBalances"] = [
            token_balance("pool-owner", MINT, 20_000_000, index=2)
        ]

        pump_amm = pump_amm_receipt()
        pump_amm["meta"]["postTokenBalances"] = [
            row
            for row in pump_amm["meta"]["postTokenBalances"]
            if row.get("owner") != WALLET
        ]

        for receipt in (pump, pump_amm):
            with self.subTest(program=receipt["meta"]["logMessages"][0]):
                events = parse_tracked_token_pump_events(
                    receipt, {MINT}, SIGNATURE
                )

                self.assertEqual(len(events), 1)
                self.assertIsNone(events[0]["event"]["newTokenBalance"])


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

        # El stream reserva la identidad antes de escribir el trade.
        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 0, source="live")
        )
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

    def test_second_operation_of_a_transaction_is_not_hidden_by_the_first(self):
        """Con firma sola, la operación 1 quedaba "matched" por la operación 0.

        PumpPortal reserva índice 0 para todo lo que entrega; si la
        transacción traía dos operaciones, la segunda nunca se aplicó y el
        fallback tiene que decirlo.
        """
        receipt = pump_receipt()
        receipt["meta"]["logMessages"].append(
            receipt["meta"]["logMessages"][-1]
        )
        parsed_events = parse_watched_wallet_pump_events(
            receipt, WALLET, SIGNATURE
        )
        self.assertEqual(
            [item["event"]["eventIndex"] for item in parsed_events], [0, 1]
        )
        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 0, source="live")
        )

        for parsed in parsed_events:
            app.record_rpc_fallback_event("trader-a", WALLET, receipt, parsed)

        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            missing = app.reconcile_rpc_fallback_events()

        self.assertEqual(len(missing), 1)
        conn = app.db()
        try:
            statuses = conn.execute(
                "SELECT event_index, status FROM rpc_fallback_events "
                "WHERE signature = ? ORDER BY event_index",
                (SIGNATURE,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(statuses, [(0, "matched"), (1, "missing")])

    def test_identity_stats_match_event_index_and_live_transport(self):
        """Un trade del índice cero no prueba que PumpPortal entregó el uno."""
        receipt = pump_receipt()
        receipt["meta"]["logMessages"].append(
            receipt["meta"]["logMessages"][-1]
        )
        parsed_events = parse_watched_wallet_pump_events(
            receipt, WALLET, SIGNATURE
        )

        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 0, source="live")
        )
        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 1, source="helius")
        )
        for parsed in parsed_events:
            self.assertTrue(
                app.record_rpc_fallback_event(
                    "trader-a", WALLET, receipt, parsed
                )
            )

        conn = app.db()
        try:
            conn.execute(
                """
                INSERT INTO trades(
                    ts, trader, wallet, side, mint, sol,
                    market_cap_sol, signature, source
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    1.0, "trader-a", WALLET, "buy", MINT, 2.5, 200.0,
                    SIGNATURE, "live",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        stats = app.get_rpc_fallback_stats()
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["matched_identity"], 1)
        self.assertEqual(stats["identity_match_rate"], 0.5)

    def test_operation_applied_from_helius_is_not_missing(self):
        """Lo que el consumidor del inbox aplicó no lo perdió el agente."""
        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 0, source="helius")
        )
        self.assertTrue(
            app.record_rpc_fallback_event(
                "trader-a", WALLET, pump_receipt(), self.parsed()
            )
        )
        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            self.assertEqual(app.reconcile_rpc_fallback_events(), [])
        self.assertEqual(app.get_rpc_fallback_stats()["matched"], 1)

    def test_unknown_balance_is_recorded_as_null(self):
        """El parser entrega ``None`` cuando no puede reconstruir el saldo.

        En producción eso hacía `float(None)` en cada poll y el fallback no
        registraba nada. Desconocido se guarda como NULL, no como cero ni como
        error.
        """
        parsed = self.parsed()
        parsed["event"]["newTokenBalance"] = None

        self.assertTrue(
            app.record_rpc_fallback_event(
                "trader-a", WALLET, pump_receipt(), parsed
            )
        )

        conn = app.db()
        try:
            row = conn.execute(
                "SELECT new_token_balance, status FROM rpc_fallback_events "
                "WHERE signature = ?",
                (SIGNATURE,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (None, "pending"))

    def test_legacy_not_null_balance_column_is_relaxed_keeping_rows(self):
        conn = app.db()
        try:
            conn.execute("DROP INDEX IF EXISTS idx_rpc_fallback_events_status")
            conn.execute("DROP TABLE rpc_fallback_events")
            conn.execute(
                """
                CREATE TABLE rpc_fallback_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    slot INTEGER,
                    block_time REAL,
                    detected_ts REAL NOT NULL,
                    trader TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    program TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    side TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    sol REAL NOT NULL,
                    market_cap_sol REAL NOT NULL,
                    token_amount REAL NOT NULL,
                    new_token_balance REAL NOT NULL,
                    pool TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    alerted_ts REAL,
                    UNIQUE(signature, event_index, wallet)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO rpc_fallback_events(
                    id, signature, event_index, detected_ts, trader, wallet,
                    program, event_name, side, mint, sol, market_cap_sol,
                    token_amount, new_token_balance, pool, event_json, status
                )
                VALUES(41, 'legacy-sig', 0, 1.0, 'trader-a', ?, 'pump',
                       'TradeEvent', 'buy', ?, 1.0, 30.0, 1.0, 5.0, 'pump',
                       '{}', 'matched')
                """,
                (WALLET, MINT),
            )
            conn.commit()
        finally:
            conn.close()

        app.migrate_database()
        app.migrate_database()

        conn = app.db()
        try:
            not_null = {
                row[1]: bool(row[3])
                for row in conn.execute("PRAGMA table_info(rpc_fallback_events)")
            }
            legacy = conn.execute(
                "SELECT id, new_token_balance, status FROM rpc_fallback_events"
            ).fetchall()
            indexes = {
                row[1]
                for row in conn.execute("PRAGMA index_list(rpc_fallback_events)")
            }
        finally:
            conn.close()

        self.assertFalse(not_null["new_token_balance"])
        self.assertTrue(not_null["token_amount"])
        self.assertEqual(legacy, [(41, 5.0, "matched")])
        self.assertIn("idx_rpc_fallback_events_status", indexes)

        parsed = self.parsed()
        parsed["event"]["newTokenBalance"] = None
        self.assertTrue(
            app.record_rpc_fallback_event(
                "trader-a", WALLET, pump_receipt(), parsed
            )
        )

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


class AccountKeyEncodingTests(unittest.TestCase):
    """El parser debe aceptar las dos codificaciones de ``accountKeys``.

    `getTransaction` pide `jsonParsed` y devuelve diccionarios; los webhooks
    entregan el formato nativo, con strings y los firmantes al principio.
    Aceptar solo el primero haría que una fuente entera se descartara en
    silencio, indistinguible de "esa fuente no manda nada".
    """

    def native_receipt(self, keys, required_signatures=1):
        receipt = pump_receipt()
        receipt["transaction"]["message"] = {
            "accountKeys": keys,
            "header": {"numRequiredSignatures": required_signatures},
        }
        return receipt

    def test_parses_native_encoding_with_string_account_keys(self):
        receipt = self.native_receipt([WALLET, MINT])

        events = parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"]["txType"], "buy")
        self.assertEqual(events[0]["event"]["mint"], MINT)

    def test_parses_json_parsed_encoding_with_dict_account_keys(self):
        # La forma que ya usaba el sondeo RPC sigue funcionando.
        events = parse_watched_wallet_pump_events(
            pump_receipt(), WALLET, SIGNATURE
        )

        self.assertEqual(len(events), 1)

    def test_native_encoding_rejects_wallet_that_did_not_sign(self):
        # La wallet aparece en la transacción pero fuera de los firmantes.
        receipt = self.native_receipt([MINT, WALLET], required_signatures=1)

        self.assertEqual(
            parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE),
            [],
        )

    def test_native_encoding_without_header_is_rejected(self):
        receipt = pump_receipt()
        receipt["transaction"]["message"] = {"accountKeys": [WALLET]}

        self.assertEqual(
            parse_watched_wallet_pump_events(receipt, WALLET, SIGNATURE),
            [],
        )


class RpcFallbackBaselineTests(unittest.TestCase):
    """La línea de base decide qué observaciones cuentan como evidencia.

    Un fallo de red dejó wallets sin punto de partida válido y el monitor se
    trajo historial de hasta 19 días, que quedó contado como `missing` sin
    serlo. La línea de base existe para que eso no vuelva a pasar.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "rpc-baseline.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def parsed(self):
        return parse_watched_wallet_pump_events(
            pump_receipt(), WALLET, SIGNATURE
        )[0]

    def set_baseline(self, baseline_ts):
        app.update_rpc_fallback_wallet_state(WALLET, "trader-a")
        conn = app.db()
        conn.execute(
            "UPDATE rpc_fallback_wallet_state SET baseline_ts = ? "
            "WHERE wallet = ?",
            (baseline_ts, WALLET),
        )
        conn.commit()
        conn.close()

    def event_status(self):
        conn = app.db()
        row = conn.execute(
            "SELECT status FROM rpc_fallback_events WHERE signature = ?",
            (SIGNATURE,),
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def insert_trade(self, ts, signature, side="buy"):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO trades(
                ts, trader, wallet, side, mint, sol,
                market_cap_sol, signature, source
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (ts, "trader-a", WALLET, side, MINT, 2.5, 200.0,
             signature, "live"),
        )
        conn.commit()
        conn.close()

    def test_event_older_than_baseline_is_discarded_not_missing(self):
        # El recibo de prueba es del instante 1_700_000_000; la línea de base
        # se fija después, así que esa operación precede a la observación.
        self.set_baseline(1_700_000_500)
        app.record_rpc_fallback_event(
            "trader-a", WALLET, pump_receipt(), self.parsed()
        )

        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            missing = app.reconcile_rpc_fallback_events()

        self.assertEqual(missing, [])
        self.assertEqual(self.event_status(), "discarded_prebaseline")
        self.assertEqual(app.get_rpc_fallback_stats()["missing"], 0)

    def test_event_after_baseline_still_counts_as_missing(self):
        # Contrapeso del test anterior: la guarda no debe descartar de más.
        self.set_baseline(1_699_999_000)
        app.record_rpc_fallback_event(
            "trader-a", WALLET, pump_receipt(), self.parsed()
        )

        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            missing = app.reconcile_rpc_fallback_events()

        self.assertEqual(len(missing), 1)
        self.assertEqual(self.event_status(), "missing")

    def test_discarded_event_never_becomes_matched(self):
        # El descarte es terminal: si volviera a clasificarse como matched,
        # la evidencia contaminada reaparecería en las métricas.
        self.set_baseline(1_700_000_500)
        app.record_rpc_fallback_event(
            "trader-a", WALLET, pump_receipt(), self.parsed()
        )
        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            app.reconcile_rpc_fallback_events()
        self.assertEqual(self.event_status(), "discarded_prebaseline")

        self.insert_trade(1_700_000_000, SIGNATURE)

        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            app.reconcile_rpc_fallback_events()

        self.assertEqual(self.event_status(), "discarded_prebaseline")
        stats = app.get_rpc_fallback_stats()
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(stats["missing"], 0)

    def test_approximate_matches_counts_events_not_pairs(self):
        # Con una tolerancia de ±300s una observación puede emparejar con
        # varias operaciones del stream sobre el mismo token. Lo que se mide
        # es cuántos eventos tienen equivalente, no cuántos pares hay.
        self.set_baseline(1_699_999_000)
        app.record_rpc_fallback_event(
            "trader-a", WALLET, pump_receipt(), self.parsed()
        )
        with patch.object(app, "RPC_FALLBACK_GRACE_SECONDS", 0):
            app.reconcile_rpc_fallback_events()
        self.assertEqual(self.event_status(), "missing")

        self.insert_trade(1_700_000_010, "5" * 88)
        self.insert_trade(1_700_000_120, "6" * 88)

        stats = app.get_rpc_fallback_stats()

        self.assertEqual(stats["missing"], 1)
        self.assertEqual(stats["approximate_matches"], 1)
        self.assertTrue(stats["missing_events"][0]["approximate_match"])


if __name__ == "__main__":
    unittest.main()


class HeliusWebhookTests(unittest.TestCase):
    """Piloto del webhook: registra qué llegó y cuándo, sin decidir nada."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "helius-webhook.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def native_receipt(self):
        # Los webhooks entregan la codificación nativa, no jsonParsed.
        receipt = pump_receipt()
        receipt["transaction"]["message"] = {
            "accountKeys": [WALLET, MINT],
            "header": {"numRequiredSignatures": 1},
        }
        return receipt

    def test_records_pump_event_from_native_encoded_payload(self):
        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            result = app.record_helius_webhook_transactions(
                [self.native_receipt()],
                received_ts=1_700_000_002,
            )

        self.assertEqual(result["seen"], 1)
        self.assertEqual(result["parsed_events"], 1)

        conn = app.db()
        row = conn.execute(
            "SELECT trader, parsed, side, mint, block_time, received_ts "
            "FROM helius_webhook_events WHERE signature = ?",
            (SIGNATURE,),
        ).fetchone()
        conn.close()

        self.assertEqual(row[0], "trader-a")
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], "buy")
        self.assertEqual(row[3], MINT)
        # La latencia se mide con estos dos campos.
        self.assertEqual(row[5] - row[4], 2)

        inbox = app.db()
        try:
            row = inbox.execute(
                "SELECT source, event_index, wallet, trader, mint, side, "
                "block_time, received_ts, status, attempts, event_json "
                "FROM market_event_inbox WHERE signature = ?",
                (SIGNATURE,),
            ).fetchone()
        finally:
            inbox.close()
        # Índice 0: es la primera operación Pump de la transacción. Cuenta
        # operaciones, no líneas de log, así que PumpPortal —que no manda
        # índice y vale 0— le da la misma identidad a la misma operación.
        self.assertEqual(row[:10], (
            "helius", 0, WALLET, "trader-a", MINT, "buy",
            1_700_000_000, 1_700_000_002, "observed", 0,
        ))
        self.assertEqual(json.loads(row[10])["signature"], SIGNATURE)

    def test_preserves_multiple_events_from_one_transaction(self):
        receipt = self.native_receipt()
        receipt["meta"]["logMessages"].append(
            receipt["meta"]["logMessages"][-1]
        )

        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            first = app.record_helius_webhook_transactions([receipt])
            second = app.record_helius_webhook_transactions([receipt])

        self.assertEqual(first["parsed_events"], 2)
        self.assertEqual(second["parsed_events"], 2)
        self.assertEqual(second["duplicates"], 1)
        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT event_index FROM market_event_inbox "
                "WHERE signature = ? ORDER BY event_index",
                (SIGNATURE,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [(0,), (1,)])
        with patch.object(app, "APP_TOKEN", "token"):
            report = app.api_helius_webhook_stats("token")
        self.assertEqual(report["normalized_events_observed"], 2)
        self.assertEqual(report["multi_event_transactions"], 1)

    def test_repeated_delivery_is_not_counted_twice(self):
        # Helius reintenta si el endpoint no contesta a tiempo.
        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions([self.native_receipt()])
            second = app.record_helius_webhook_transactions(
                [self.native_receipt()]
            )

        self.assertEqual(second["duplicates"], 1)

        conn = app.db()
        count = conn.execute(
            "SELECT COUNT(*) FROM helius_webhook_events"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_wallet_delivery_counts_raw_noise_and_retries(self):
        pump = self.native_receipt()
        noise = self.native_receipt()
        noise["transaction"]["signatures"] = ["non-pump-signature"]
        noise["meta"]["logMessages"] = []

        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions(
                [pump, noise], received_ts=1_700_000_002
            )
            app.record_helius_webhook_transactions(
                [noise], received_ts=1_700_000_003
            )

        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT signature, parsed FROM "
                "helius_webhook_wallet_observations ORDER BY signature"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [(SIGNATURE, 1), ("non-pump-signature", 0)])

        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app.time, "time", return_value=1_700_000_010),
            patch.object(app, "WATCHED", {"trader-a": WALLET}),
        ):
            report = app.api_helius_webhook_stats("token")
        delivery = report["wallet_delivery"]
        self.assertEqual(delivery["measurement_started_ts"], 1_700_000_002)
        self.assertEqual(delivery["windows"]["24h"], [
            {
                "wallet": WALLET,
                "trader": "trader-a",
                "transactions": 2,
                "pump_transactions": 1,
                "pump_percent": 50.0,
            }
        ])
        self.assertEqual(delivery["windows"]["7d"], delivery["windows"]["24h"])

    def test_wallet_delivery_attributes_each_account_separately(self):
        second_wallet = "second-watched-wallet"
        receipt = self.native_receipt()
        receipt["transaction"]["message"]["accountKeys"] = [
            WALLET, second_wallet, MINT
        ]

        with patch.object(app, "WATCHED", {
            "trader-a": WALLET,
            "trader-b": second_wallet,
        }):
            app.record_helius_webhook_transactions(
                [receipt], received_ts=1_700_000_002
            )

        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT wallet, parsed FROM "
                "helius_webhook_wallet_observations ORDER BY wallet"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [(WALLET, 1), (second_wallet, 0)])

    def test_wallet_delivery_accepts_json_parsed_keys(self):
        receipt = self.native_receipt()
        receipt["transaction"]["message"]["accountKeys"] = [
            {"pubkey": WALLET, "signer": True},
            {"pubkey": MINT, "signer": False},
        ]

        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions([receipt])

        conn = app.db()
        try:
            row = conn.execute(
                "SELECT wallet, parsed FROM "
                "helius_webhook_wallet_observations"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (WALLET, 1))

    def test_webhook_does_not_route_events_or_save_trades(self):
        with patch.object(app, "WATCHED", {"trader-a": WALLET}), \
                patch.object(app, "route_market_event") as route, \
                patch.object(app, "save_trade") as save:
            result = app.record_helius_webhook_transactions([self.native_receipt()])
        self.assertEqual(result["parsed_events"], 1)
        route.assert_not_called()
        save.assert_not_called()

    def test_transaction_from_unwatched_wallet_is_recorded_unparsed(self):
        # Se guarda para poder medir volumen, pero no cuenta como operación.
        with patch.object(app, "WATCHED", {"otro": "otra-wallet"}):
            result = app.record_helius_webhook_transactions(
                [self.native_receipt()]
            )

        self.assertEqual(result["seen"], 1)
        self.assertEqual(result["parsed_events"], 0)

    def test_tracked_token_event_is_preserved_without_a_watched_wallet(self):
        with patch.object(app, "WATCHED", {}), patch.object(
            app, "TRACKED_TOKENS", {MINT}
        ):
            result = app.record_helius_webhook_transactions(
                [self.native_receipt()]
            )

        self.assertEqual(result["parsed_events"], 1)
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT wallet, trader, mint, event_json "
                "FROM market_event_inbox "
                "WHERE signature = ?",
                (SIGNATURE,),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row[:3], (WALLET, None, MINT))
        self.assertEqual(
            json.loads(row[3])["traderPublicKey"],
            WALLET,
        )

    def test_all_watched_wallets_in_one_transaction_are_preserved(self):
        import base64

        second_wallet_raw = bytes(range(129, 161))
        second_wallet = _base58_encode(second_wallet_raw)
        second_payload = (
            PUMP_TRADE_EVENT
            + MINT_RAW
            + struct.pack("<QQ?", 1_000_000_000, 5_000_000, False)
            + second_wallet_raw
            + struct.pack(
                "<qQQ",
                1_700_000_001,
                45_000_000_000,
                225_000_000_000_000,
            )
        )
        receipt = self.native_receipt()
        receipt["transaction"]["message"] = {
            "accountKeys": [WALLET, second_wallet, MINT],
            "header": {"numRequiredSignatures": 2},
        }
        receipt["meta"]["logMessages"].append(
            "Program data: "
            + base64.b64encode(second_payload).decode("ascii")
        )
        receipt["meta"]["preTokenBalances"].append(
            token_balance(second_wallet, MINT, 10_000_000, index=2)
        )
        receipt["meta"]["postTokenBalances"].append(
            token_balance(second_wallet, MINT, 5_000_000, index=2)
        )

        with patch.object(app, "WATCHED", {
            "trader-a": WALLET,
            "trader-b": second_wallet,
        }), patch.object(app, "TRACKED_TOKENS", set()):
            result = app.record_helius_webhook_transactions([receipt])

        self.assertEqual(result["parsed_events"], 2)
        conn = app.db()
        try:
            rows = conn.execute(
                "SELECT event_index, wallet, trader "
                "FROM market_event_inbox WHERE signature = ? "
                "ORDER BY event_index",
                (SIGNATURE,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [
            (0, WALLET, "trader-a"),
            (1, second_wallet, "trader-b"),
        ])

    def test_wallet_and_token_match_store_one_normalized_event(self):
        with patch.object(app, "WATCHED", {"trader-a": WALLET}), patch.object(
            app, "TRACKED_TOKENS", {MINT}
        ):
            result = app.record_helius_webhook_transactions(
                [self.native_receipt()]
            )

        self.assertEqual(result["parsed_events"], 1)
        conn = app.db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM market_event_inbox "
                "WHERE signature = ?",
                (SIGNATURE,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_stats_separate_webhook_and_stream_latency(self):
        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions(
                [self.native_receipt()],
                received_ts=1_700_000_003,
            )

        stats = app.get_rpc_fallback_stats()  # no debe romperse
        self.assertIsInstance(stats, dict)

        conn = app.db()
        conn.close()

        with patch.object(app, "APP_TOKEN", "token"):
            report = app.api_helius_webhook_stats("token")

        self.assertTrue(report["observational"])
        self.assertFalse(report["affects_decisions"])
        self.assertEqual(report["pump_events_parsed"], 1)
        self.assertEqual(report["normalized_events_observed"], 1)
        self.assertEqual(report["multi_event_transactions"], 0)
        self.assertEqual(report["inbox_status"], {"observed": 1})
        self.assertEqual(report["webhook_latency"]["samples"], 1)
        self.assertEqual(report["webhook_latency"]["avg_seconds"], 3.0)
        # Sin entrega por el stream, esa operación solo la vio el webhook.
        self.assertEqual(report["parsed_only_in_webhook"], 1)

    def test_stats_report_inbox_live_permissions_separately(self):
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "MARKET_EVENT_INBOX_CONSUMER_ENABLED", True),
        ):
            report = app.api_helius_webhook_stats("token")

        consumer = report["inbox_consumer"]
        self.assertTrue(consumer["affects_live_execution"])
        self.assertFalse(consumer["affects_live_buys"])
        self.assertTrue(consumer["affects_live_exits"])

    def test_stats_attribute_trades_to_the_transport_that_won_them(self):
        """Lo que el consumidor del inbox escribe en `trades` no es PumpPortal.

        Con el consumidor activo, Helius escribe `trades` con `ts` igual al
        tiempo de bloque. Si la comparación mirara `trades`, cada operación
        consumida contaría como entregada por el stream con latencia cero y la
        comparación dejaría de medir cobertura y latencia de PumpPortal.
        """
        block_time = 1_700_000_000

        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions(
                [self.native_receipt()],
                received_ts=block_time + 3,
            )

        def seed_trade(signature, ts, source):
            self.assertTrue(
                app.mark_market_event_processed(signature, 0, source=source)
            )
            conn = app.db()
            try:
                conn.execute(
                    """
                    INSERT INTO trades(
                        ts, trader, wallet, side, mint, sol, market_cap_sol,
                        signature, source
                    )
                    VALUES(?, 'trader-a', ?, 'buy', ?, 1.0, 30.0, ?, 'live')
                    """,
                    (ts, WALLET, MINT, signature),
                )
                conn.execute(
                    """
                    INSERT INTO rpc_fallback_events(
                        signature, event_index, slot, block_time, detected_ts,
                        trader, wallet, program, event_name, side, mint, sol,
                        market_cap_sol, token_amount, new_token_balance, pool,
                        event_json, status
                    )
                    VALUES(?, 0, 1, ?, ?, 'trader-a', ?, 'pump', 'TradeEvent',
                           'buy', ?, 1.0, 30.0, 1.0, 1.0, 'pump', '{}',
                           'pending')
                    """,
                    (signature, block_time, ts, WALLET, MINT),
                )
                conn.commit()
            finally:
                conn.close()

        # Ganada por el consumidor: `ts` es el tiempo de bloque exacto.
        seed_trade(SIGNATURE, block_time, "helius")

        with patch.object(app, "APP_TOKEN", "token"):
            report = app.api_helius_webhook_stats("token")

        self.assertEqual(report["parsed_only_in_webhook"], 1)
        self.assertEqual(report["pumpportal_latency"]["samples"], 0)

        # Ganada por el stream: `ts` es cuándo la guardamos nosotros.
        seed_trade("stream-only-signature", block_time + 1.5, "live")

        with patch.object(app, "APP_TOKEN", "token"):
            report = app.api_helius_webhook_stats("token")

        self.assertEqual(report["parsed_only_in_webhook"], 1)
        self.assertEqual(report["pumpportal_latency"]["samples"], 1)
        self.assertEqual(report["pumpportal_latency"]["avg_seconds"], 1.5)

    def test_transport_attribution_lookups_are_indexed(self):
        """Las consultas por firma recorren tablas que crecen con cada evento.

        Sin índice, `parsed_only_in_webhook` tardó más de tres minutos con
        300k identidades reservadas y el endpoint venció en producción.
        """
        conn = app.db()
        try:
            indexes = {
                row[1]
                for table in ("processed_market_events", "trades")
                for row in conn.execute(f"PRAGMA index_list({table})")
            }
            plan = " ".join(
                row[3]
                for row in conn.execute(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT 1 FROM processed_market_events
                    WHERE signature = ? AND source = 'live'
                    """,
                    ("sig",),
                )
            )
        finally:
            conn.close()

        self.assertIn("idx_processed_market_events_signature", indexes)
        self.assertIn("idx_trades_signature", indexes)
        self.assertIn("idx_processed_market_events_signature", plan)

    def test_stats_count_stream_delivery_even_without_a_trade_row(self):
        """Un token seguido de una wallet no vigilada no llega a `trades`.

        El stream igual lo entregó y lo reservó; contarlo como visto solo por
        el webhook infla la cobertura aparente de Helius.
        """
        with patch.object(app, "WATCHED", {"trader-a": WALLET}):
            app.record_helius_webhook_transactions([self.native_receipt()])

        self.assertTrue(
            app.mark_market_event_processed(SIGNATURE, 0, source="live")
        )

        with patch.object(app, "APP_TOKEN", "token"):
            report = app.api_helius_webhook_stats("token")

        self.assertEqual(report["parsed_only_in_webhook"], 0)


class InboxRoundTripTests(unittest.TestCase):
    """El índice tiene que sobrevivir el viaje completo, no solo el parser.

    Webhook, inbox, deserialización, router. Lo que se guarda y se vuelve a
    leer es el evento serializado: si el índice viajara al lado del evento, se
    perdería justo en ese salto y dos operaciones de la misma transacción
    volverían a compartir identidad, silenciosamente.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "inbox-round-trip.db"
        app.migrate_database()

        self.watched_original = app.WATCHED
        app.WATCHED = {"trader-a": WALLET}
        app.TRACKED_TOKENS.add(MINT)
        self.addCleanup(self.restaurar)

        conn = app.db()
        conn.execute(
            """
            INSERT INTO paper_positions(
                opened_ts, mint, trigger_traders, entry_mc, stake_usd,
                status, pnl_usd, decision, score, origin_trader,
                current_mc, remaining_pct, realized_pnl_usd,
                unrealized_pnl_usd, last_action, tp_stage, mode
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1000.0, MINT, '["trader-a"]', 200.0, 5.0,
                "open", 0, "COPY", 90, "trader-a",
                200.0, 1.0, 0, 0, "HOLD", 0, "paper",
            ),
        )
        conn.commit()
        conn.close()

    def restaurar(self):
        app.WATCHED = self.watched_original
        app.TRACKED_TOKENS.discard(MINT)
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def receipt_with_two_events(self):
        # Dos operaciones Pump válidas en una sola transacción.
        receipt = pump_receipt()
        receipt["transaction"]["message"] = {
            "accountKeys": [WALLET, MINT],
            "header": {"numRequiredSignatures": 1},
        }
        receipt["meta"]["logMessages"].append(
            receipt["meta"]["logMessages"][-1]
        )
        return receipt

    def inbox_rows(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT event_index, wallet, event_json, block_event_ts "
                "FROM market_event_inbox WHERE signature = ? "
                "ORDER BY event_index",
                (SIGNATURE,),
            ).fetchall()
        finally:
            conn.close()

    def test_block_event_ts_survives_serialization(self):
        # Tenía la misma forma rota que el índice: el parser lo guardaba al
        # lado del evento, y solo el evento se serializa. Sin él adentro, la
        # guarda de eventos anteriores a la entrada no puede verlo al
        # reconstruir desde la cola.
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])

        for _, _, event_json, columna in self.inbox_rows():
            evento = json.loads(event_json)
            self.assertEqual(evento["blockEventTs"], 1_700_000_000)
            # La columna es copia derivada de lo que se serializa.
            self.assertEqual(columna, evento["blockEventTs"])

    def test_new_events_are_stored_with_ordinal_index_scheme(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])

        conn = app.db()
        try:
            schemes = conn.execute(
                "SELECT DISTINCT event_index_scheme "
                "FROM market_event_inbox WHERE signature = ?",
                (SIGNATURE,),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(schemes, [("ordinal-v2",)])

    def test_rebuilt_event_exposes_its_block_timestamp(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])
        indice, wallet, event_json, _ = self.inbox_rows()[0]

        evento = app.market_event_from_inbox_row(
            event_json, wallet=wallet, signature=SIGNATURE, event_index=indice,
            block_event_ts=1_700_000_000,
        )

        self.assertEqual(app.market_event_block_ts(evento), 1_700_000_000.0)

    def test_event_index_survives_serialization(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])

        filas = self.inbox_rows()
        self.assertEqual([fila[0] for fila in filas], [0, 1])

        # La columna y el JSON no pueden discrepar: la columna se deriva del
        # evento, que es lo mismo que se serializa.
        for indice, _, event_json, _ in filas:
            evento = json.loads(event_json)
            self.assertEqual(evento["eventIndex"], indice)

    def test_deserialized_events_keep_distinct_identity_through_the_router(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])

        # Lo que hará el procesador cuando la cola se active.
        for indice, wallet, event_json, _ in self.inbox_rows():
            evento = app.market_event_from_inbox_row(
                event_json,
                wallet=wallet,
                signature=SIGNATURE,
                event_index=indice,
                block_event_ts=1_700_000_000,
            )
            # Una venta parcial del trader de origen, para que cada evento
            # tenga un efecto visible y acumulable.
            evento["txType"] = "sell"
            evento["newTokenBalance"] = 10.0
            app.route_market_event(evento)

        conn = app.db()
        try:
            restante = conn.execute(
                "SELECT remaining_pct FROM paper_positions WHERE mint = ?",
                (MINT,),
            ).fetchone()[0]
            identidades = [
                fila[0] for fila in conn.execute(
                    "SELECT event_id FROM paper_position_applications "
                    "ORDER BY event_id"
                ).fetchall()
            ]
        finally:
            conn.close()

        # Con el índice perdido en la serialización, las dos operaciones
        # compartirían identidad, la segunda se descartaría como duplicado y
        # el restante quedaría en 0.75.
        self.assertAlmostEqual(restante, 0.50)
        self.assertEqual(
            identidades,
            [f"{SIGNATURE}:0", f"{SIGNATURE}:1"],
        )

    def test_reconstructed_event_recovers_the_signing_wallet(self):
        # Las filas nuevas conservan la billetera tanto en el evento como en
        # la columna de consulta, y la reconstrucción exige que coincidan.
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])
        indice, wallet, event_json, _ = self.inbox_rows()[0]

        self.assertEqual(json.loads(event_json)["traderPublicKey"], WALLET)

        evento = app.market_event_from_inbox_row(
            event_json, wallet=wallet, signature=SIGNATURE, event_index=indice,
            block_event_ts=1_700_000_000,
        )
        self.assertEqual(evento["traderPublicKey"], WALLET)
        self.assertEqual(app.trader_for(evento["traderPublicKey"]), "trader-a")

    def test_wallet_mismatch_between_column_and_json_is_rejected(self):
        event_json = json.dumps({
            "signature": SIGNATURE,
            "eventIndex": 1,
            "blockEventTs": 1_700_000_000,
            "traderPublicKey": "another-wallet",
        })

        with self.assertRaisesRegex(ValueError, "billetera.*no coinciden"):
            self.reconstruir(event_json)

    def reconstruir(self, event_json, **cambios):
        argumentos = {
            "wallet": WALLET,
            "signature": SIGNATURE,
            "event_index": 1,
            "block_event_ts": 1_700_000_000,
        }
        argumentos.update(cambios)
        return app.market_event_from_inbox_row(event_json, **argumentos)

    def test_event_json_without_index_is_rejected_on_rebuild(self):
        """Una fila escrita antes de que el índice viajara adentro del evento.

        Es el caso que importa: no un índice roto, sino uno *ausente*. La regla
        del router en vivo —ausente vale 0, porque PumpPortal manda una
        operación por mensaje— es exactamente la equivocada al reconstruir
        desde disco, donde ausente significa que el índice se perdió.

        Con `event_index=0` el chequeo de discrepancia no puede rescatarlo: la
        columna diría 0 y la ausencia también daría 0, así que coincidirían.
        Lo único que lo rechaza es exigir que el campo esté.
        """
        sin_indice = json.dumps({
            "signature": SIGNATURE,
            "blockEventTs": 1_700_000_000,
            "txType": "sell",
        })

        with self.assertRaises(ValueError):
            self.reconstruir(sin_indice, event_index=0)

    def test_event_json_with_invalid_index_is_rejected_on_rebuild(self):
        roto = json.dumps({
            "signature": SIGNATURE,
            "eventIndex": "1",
            "blockEventTs": 1_700_000_000,
        })

        with self.assertRaises(ValueError):
            self.reconstruir(roto)

    def test_missing_wallet_is_rejected_on_rebuild(self):
        # Sin billetera el evento se reconstruye igual, pero el router lo
        # clasifica por la ruta equivocada. Falla silenciosa: nada se rompe,
        # solo se aplica mal.
        valido = json.dumps({
            "signature": SIGNATURE,
            "eventIndex": 1,
            "blockEventTs": 1_700_000_000,
        })

        for vacia in (None, "", "   "):
            with self.subTest(wallet=vacia):
                with self.assertRaises(ValueError):
                    self.reconstruir(valido, wallet=vacia)

    def test_signature_mismatch_between_column_and_json_is_rejected(self):
        otro = json.dumps({
            "signature": "otra-firma",
            "eventIndex": 1,
            "blockEventTs": 1_700_000_000,
        })

        with self.assertRaises(ValueError):
            self.reconstruir(otro)

    def test_index_mismatch_between_column_and_json_is_rejected(self):
        # Si discrepan, la fila la escribió código viejo o está corrompida.
        # Aplicarla con una de las dos identidades es peor que rechazarla.
        desfasado = json.dumps({
            "signature": SIGNATURE,
            "eventIndex": 2,
            "blockEventTs": 1_700_000_000,
        })

        with self.assertRaises(ValueError):
            self.reconstruir(desfasado, event_index=1)

    def test_serialization_rejects_an_event_that_lost_its_index(self):
        # La otra punta: si el parser dejara de escribir el índice, la pérdida
        # se detecta al guardar, no recién al reconstruir.
        with self.assertRaises(ValueError):
            app.market_event_index({"signature": SIGNATURE}, required=True)

        # Y en vivo, ausente sigue siendo 0: PumpPortal no manda índice.
        self.assertEqual(app.market_event_index({"signature": SIGNATURE}), 0)


class OutboundRequestTests(unittest.TestCase):
    def test_request_name_still_refers_to_urllib(self):
        # `from fastapi import Request` pisaría `urllib.request.Request` y
        # rompería en silencio todas las llamadas HTTP salientes del módulo.
        import urllib.request

        self.assertIs(app.Request, urllib.request.Request)


class HeliusWebhookDeliveryAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        for name, value in (
            ("DB", Path(self.temp_dir.name) / "delivery-alert.db"),
            ("HELIUS_WEBHOOK_ENABLED", True),
            ("DISCORD_ALERT_WEBHOOK_URL", "https://example.test/webhook"),
        ):
            active_patch = patch.object(app, name, value)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        app.migrate_database()

    def insert_rpc_event(self, block_time):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO rpc_fallback_events "
                "(signature, event_index, block_time, detected_ts, "
                "trader, wallet, program, event_name, side, mint, sol, "
                "market_cap_sol, token_amount, pool, event_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"rpc-{block_time}", 0, block_time, block_time,
                    "Cooker", "wallet", "pump", "TradeEvent", "buy",
                    "mint", 1.0, 100.0, 1000.0, "pump", "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_webhook_event(self, signature, received_ts, parsed):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO helius_webhook_events "
                "(signature, received_ts, parsed) VALUES (?, ?, ?)",
                (signature, received_ts, int(parsed)),
            )
            conn.commit()
        finally:
            conn.close()

    async def test_alerts_once_for_recent_onchain_activity_and_recovers(self):
        self.insert_rpc_event(1990.0)
        self.insert_webhook_event("unparsed", 1999.0, False)
        with patch.object(
            app, "send_discord_alert", new_callable=AsyncMock,
            return_value=True,
        ) as send:
            first = await app.update_helius_webhook_delivery_alert(now=2000.0)
            repeated = await app.update_helius_webhook_delivery_alert(now=2000.0)
            self.insert_webhook_event("parsed", 1999.0, True)
            recovered = await app.update_helius_webhook_delivery_alert(
                now=2000.0
            )

        self.assertTrue(first)
        self.assertFalse(repeated)
        self.assertTrue(recovered)
        self.assertEqual(send.await_count, 2)
        self.assertIn("monitor RPC", send.await_args_list[0].args[0])
        self.assertIn("volvió", send.await_args_list[1].args[0])
        self.assertFalse(app.get_helius_webhook_delivery_alert_active())

    async def test_no_alert_without_recent_rpc_activity(self):
        self.insert_rpc_event(100.0)
        with patch.object(
            app, "send_discord_alert", new_callable=AsyncMock,
            return_value=True,
        ) as send:
            result = await app.update_helius_webhook_delivery_alert(now=2000.0)

        self.assertFalse(result)
        send.assert_not_awaited()

    async def test_failed_discord_delivery_is_retried(self):
        self.insert_rpc_event(1990.0)
        with patch.object(
            app, "send_discord_alert", new_callable=AsyncMock,
            side_effect=[False, True],
        ) as send:
            failed = await app.update_helius_webhook_delivery_alert(now=2000.0)
            self.assertFalse(app.get_helius_webhook_delivery_alert_active())
            retried = await app.update_helius_webhook_delivery_alert(now=2000.0)

        self.assertFalse(failed)
        self.assertTrue(retried)
        self.assertEqual(send.await_count, 2)
        self.assertTrue(app.get_helius_webhook_delivery_alert_active())
