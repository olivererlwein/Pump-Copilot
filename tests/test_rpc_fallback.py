import io
import json
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
        self.assertEqual(row[:10], (
            "helius", 1, WALLET, "trader-a", MINT, "buy",
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
        self.assertEqual(rows, [(1,), (2,)])
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

    def test_rebuilt_event_exposes_its_block_timestamp(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])
        indice, wallet, event_json, _ = self.inbox_rows()[0]

        evento = app.market_event_from_inbox_row(
            event_json, wallet=wallet, signature=SIGNATURE, event_index=indice,
        )

        self.assertEqual(app.market_event_block_ts(evento), 1_700_000_000.0)

    def test_event_index_survives_serialization(self):
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])

        filas = self.inbox_rows()
        self.assertEqual([fila[0] for fila in filas], [1, 2])

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
            [f"{SIGNATURE}:1", f"{SIGNATURE}:2"],
        )

    def test_reconstructed_event_recovers_the_signing_wallet(self):
        # El evento normalizado no lleva la billetera adentro: el parser la
        # conoce por contexto. Sin recuperarla de la fila, el router trataría
        # la operación como de una billetera ajena.
        app.record_helius_webhook_transactions([self.receipt_with_two_events()])
        indice, wallet, event_json, _ = self.inbox_rows()[0]

        self.assertNotIn("traderPublicKey", json.loads(event_json))

        evento = app.market_event_from_inbox_row(
            event_json, wallet=wallet, signature=SIGNATURE, event_index=indice,
        )
        self.assertEqual(evento["traderPublicKey"], WALLET)
        self.assertEqual(app.trader_for(evento["traderPublicKey"]), "trader-a")

    def reconstruir(self, event_json, **cambios):
        argumentos = {
            "wallet": WALLET,
            "signature": SIGNATURE,
            "event_index": 1,
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
        sin_indice = json.dumps({"signature": SIGNATURE, "txType": "sell"})

        with self.assertRaises(ValueError):
            self.reconstruir(sin_indice, event_index=0)

    def test_event_json_with_invalid_index_is_rejected_on_rebuild(self):
        roto = json.dumps({"signature": SIGNATURE, "eventIndex": "1"})

        with self.assertRaises(ValueError):
            self.reconstruir(roto)

    def test_missing_wallet_is_rejected_on_rebuild(self):
        # Sin billetera el evento se reconstruye igual, pero el router lo
        # clasifica por la ruta equivocada. Falla silenciosa: nada se rompe,
        # solo se aplica mal.
        valido = json.dumps({"signature": SIGNATURE, "eventIndex": 1})

        for vacia in (None, "", "   "):
            with self.subTest(wallet=vacia):
                with self.assertRaises(ValueError):
                    self.reconstruir(valido, wallet=vacia)

    def test_signature_mismatch_between_column_and_json_is_rejected(self):
        otro = json.dumps({"signature": "otra-firma", "eventIndex": 1})

        with self.assertRaises(ValueError):
            self.reconstruir(otro)

    def test_index_mismatch_between_column_and_json_is_rejected(self):
        # Si discrepan, la fila la escribió código viejo o está corrompida.
        # Aplicarla con una de las dos identidades es peor que rechazarla.
        desfasado = json.dumps({"signature": SIGNATURE, "eventIndex": 2})

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
