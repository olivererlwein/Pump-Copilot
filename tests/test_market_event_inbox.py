import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class MarketEventDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "event-dedup.db"
        app.migrate_database()
        self.addCleanup(self.restore)

    def restore(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def event_ids(self):
        conn = app.db()
        try:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT event_id FROM processed_market_events "
                    "ORDER BY event_id"
                ).fetchall()
            ]
        finally:
            conn.close()

    def test_same_signature_distinct_indexes_are_not_duplicates(self):
        self.assertTrue(app.mark_market_event_processed("sig-1", 0))
        self.assertTrue(app.mark_market_event_processed("sig-1", 1))
        self.assertFalse(app.mark_market_event_processed("sig-1", 0))
        self.assertFalse(app.mark_market_event_processed("sig-1", 1))
        self.assertEqual(self.event_ids(), ["sig-1:0", "sig-1:1"])

    def test_concurrent_delivery_reserves_the_event_once(self):
        barrier = threading.Barrier(2)
        results = []

        def reserve():
            barrier.wait()
            results.append(app.mark_market_event_processed("sig-race", 0))

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.event_ids(), ["sig-race:0"])

    def test_legacy_signature_blocks_index_zero_but_not_later_events(self):
        conn = app.db()
        conn.execute(
            "INSERT INTO processed_signatures(signature, ts, source) "
            "VALUES(?,?,?)",
            ("sig-old", 1.0, "live"),
        )
        app.migrate_processed_market_events(conn)
        conn.commit()
        conn.close()

        self.assertFalse(app.mark_market_event_processed("sig-old", 0))
        self.assertTrue(app.mark_market_event_processed("sig-old", 1))
        self.assertEqual(self.event_ids(), ["sig-old:0", "sig-old:1"])


class MarketEventInboxValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "inbox-validation.db"
        app.migrate_database()
        self.addCleanup(self.restore)

    def restore(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insert_event(self, signature="sig-1", event_index=0, event=None):
        event = event or {
            "signature": signature,
            "eventIndex": event_index,
            "blockEventTs": 100.0,
            "mint": "mint-1",
            "txType": "buy",
        }
        conn = app.db()
        conn.execute(
            """
            INSERT INTO market_event_inbox(
                signature, event_index, source, wallet, trader, mint, side,
                block_event_ts, received_ts, event_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signature,
                event_index,
                "helius",
                "wallet-1",
                "trader-1",
                "mint-1",
                "buy",
                100.0,
                100.0,
                json.dumps(event),
            ),
        )
        conn.commit()
        conn.close()

    def row(self, signature="sig-1", event_index=0):
        conn = app.db()
        try:
            return conn.execute(
                """
                SELECT status, attempts, claim_token, claimed_ts,
                       processed_ts, last_error
                FROM market_event_inbox
                WHERE signature = ? AND event_index = ?
                """,
                (signature, event_index),
            ).fetchone()
        finally:
            conn.close()

    def test_claim_is_exclusive_while_the_lease_is_fresh(self):
        self.insert_event()

        first = app.claim_market_event_inbox_validation_batch(now=200.0)
        second = app.claim_market_event_inbox_validation_batch(now=201.0)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(self.row()[:2], ("validating", 1))

    def test_stale_claim_is_recovered_and_old_owner_cannot_finish(self):
        self.insert_event()
        first = app.claim_market_event_inbox_validation_batch(now=200.0)[0]
        recovered_at = 201.0 + app.MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS
        second = app.claim_market_event_inbox_validation_batch(
            now=recovered_at
        )[0]

        self.assertNotEqual(first["claim_token"], second["claim_token"])
        self.assertFalse(app.finish_market_event_inbox_validation(
            "sig-1", 0, first["claim_token"], "validated", now=recovered_at,
        ))
        self.assertTrue(app.finish_market_event_inbox_validation(
            "sig-1", 0, second["claim_token"], "validated", now=recovered_at,
        ))
        self.assertEqual(self.row()[:2], ("validated", 2))

    def test_valid_event_is_marked_without_calling_the_router(self):
        self.insert_event()

        with patch.object(app, "route_market_event") as route:
            result = app.validate_market_event_inbox_once(now=200.0)

        route.assert_not_called()
        self.assertEqual(result, {
            "claimed": 1,
            "validated": 1,
            "rejected": 0,
            "lost_claims": 0,
        })
        status, attempts, token, claimed_ts, processed_ts, error = self.row()
        self.assertEqual((status, attempts), ("validated", 1))
        self.assertIsNone(token)
        self.assertIsNone(claimed_ts)
        self.assertEqual(processed_ts, 200.0)
        self.assertIsNone(error)

    def test_corrupt_event_is_rejected_with_an_error(self):
        self.insert_event(event={
            "signature": "another-signature",
            "eventIndex": 0,
            "mint": "mint-1",
            "txType": "buy",
        })

        result = app.validate_market_event_inbox_once(now=200.0)

        self.assertEqual(result["rejected"], 1)
        status, attempts, token, claimed_ts, processed_ts, error = self.row()
        self.assertEqual((status, attempts), ("rejected", 1))
        self.assertIsNone(token)
        self.assertIsNone(claimed_ts)
        self.assertEqual(processed_ts, 200.0)
        self.assertIn("no coinciden", error)

    def test_completed_rows_are_not_claimed_again(self):
        self.insert_event()
        app.validate_market_event_inbox_once(now=200.0)

        self.assertEqual(
            app.claim_market_event_inbox_validation_batch(now=500.0),
            [],
        )


class MarketEventInboxActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "inbox-activation.db"
        app.migrate_database()
        self.addCleanup(self.restore)

    def restore(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def test_activation_is_persisted_once_across_restarts(self):
        first = app.establish_market_event_inbox_activation(now=200.75)
        second = app.establish_market_event_inbox_activation(now=900.0)

        self.assertEqual(first, 200.75)
        self.assertEqual(second, first)
        self.assertEqual(app.get_market_event_inbox_activation_ts(), first)

    def test_historical_inbox_row_is_before_activation(self):
        self.assertFalse(app.market_event_is_after_activation(
            received_ts=199.0,
            block_event_ts=199.0,
            activation_ts=200.75,
        ))

    def test_delayed_old_transaction_is_before_activation(self):
        self.assertFalse(app.market_event_is_after_activation(
            received_ts=201.0,
            block_event_ts=199.0,
            activation_ts=200.75,
        ))

    def test_activation_second_and_later_delivery_are_accepted(self):
        self.assertTrue(app.market_event_is_after_activation(
            received_ts=200.75,
            block_event_ts=200.0,
            activation_ts=200.75,
        ))

    def test_missing_or_invalid_time_fails_closed(self):
        for value in (None, 0, float("nan"), float("inf"), "broken"):
            with self.subTest(value=value):
                self.assertFalse(app.market_event_is_after_activation(
                    received_ts=201.0,
                    block_event_ts=value,
                    activation_ts=200.75,
                ))


class MarketEventInboxConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "inbox-consumer.db"
        app.migrate_database()
        self.addCleanup(self.restore)

    def restore(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insert_validated_event(
        self,
        signature="sig-1",
        event_index=0,
        received_ts=202.0,
        block_event_ts=202.0,
        stored_block_event_ts=None,
        event_fields=None,
    ):
        event = {
            "signature": signature,
            "eventIndex": event_index,
            "blockEventTs": block_event_ts,
            "mint": "mint-1",
            "txType": "buy",
            **(event_fields or {}),
        }
        conn = app.db()
        conn.execute(
            """
            INSERT INTO market_event_inbox(
                signature, event_index, source, wallet, trader, mint, side,
                block_event_ts, received_ts, event_json, status
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signature,
                event_index,
                "helius",
                "wallet-1",
                "trader-1",
                "mint-1",
                "buy",
                (
                    block_event_ts
                    if stored_block_event_ts is None
                    else stored_block_event_ts
                ),
                received_ts,
                json.dumps(event),
                "validated",
            ),
        )
        conn.commit()
        conn.close()

    def row(self, signature="sig-1", event_index=0):
        conn = app.db()
        try:
            return conn.execute(
                """
                SELECT status, attempts, claim_token, claimed_ts,
                       processed_ts, last_error
                FROM market_event_inbox
                WHERE signature = ? AND event_index = ?
                """,
                (signature, event_index),
            ).fetchone()
        finally:
            conn.close()

    def test_post_activation_event_is_reserved_and_routed_once(self):
        self.insert_validated_event()
        app.establish_market_event_inbox_activation(now=200.5)

        with (
            patch.object(app, "mark_market_event_processed", return_value=True) as mark,
            patch.object(app, "route_market_event") as route,
        ):
            result = app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(result, {
            "claimed": 1,
            "processed": 1,
            "duplicates": 0,
            "ignored_pre_activation": 0,
            "failed": 0,
            "released": 0,
            "lost_claims": 0,
        })
        mark.assert_called_once_with("sig-1", 0, source="helius")
        routed = route.call_args.args[0]
        self.assertEqual(routed["traderPublicKey"], "wallet-1")
        self.assertEqual(routed["eventIndex"], 0)
        self.assertEqual(
            route.call_args.kwargs,
            {"allow_live_buys": False, "allow_live_exits": True},
        )
        self.assertEqual(self.row()[0], "processed")

    def test_event_before_activation_is_terminally_ignored(self):
        self.insert_validated_event(received_ts=199.0, block_event_ts=199.0)
        app.establish_market_event_inbox_activation(now=200.5)

        with (
            patch.object(app, "mark_market_event_processed") as mark,
            patch.object(app, "route_market_event") as route,
        ):
            result = app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(result["ignored_pre_activation"], 1)
        mark.assert_not_called()
        route.assert_not_called()
        self.assertEqual(self.row()[0], "ignored_pre_activation")

    def test_event_already_won_by_another_transport_is_not_routed(self):
        self.insert_validated_event()
        app.establish_market_event_inbox_activation(now=200.5)
        self.assertTrue(app.mark_market_event_processed("sig-1", 0, source="live"))

        with patch.object(app, "route_market_event") as route:
            result = app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(result["duplicates"], 1)
        route.assert_not_called()
        self.assertEqual(self.row()[0], "duplicate")

    def test_unusable_balance_fails_before_reserving_identity(self):
        """El parser de Helius es nuestro: un saldo inservible es un bug.

        Tiene que fallar al reconstruir la fila, que corre en validación y
        antes de reservar la identidad global, no en el consumidor después de
        haberla reservado.
        """
        self.insert_validated_event(event_fields={"newTokenBalance": -1})
        app.establish_market_event_inbox_activation(now=200.5)

        with (
            patch.object(app, "mark_market_event_processed") as mark,
            patch.object(app, "route_market_event") as route,
        ):
            result = app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(result["failed"], 1)
        mark.assert_not_called()
        route.assert_not_called()
        self.assertIn("INVALID_NEW_TOKEN_BALANCE", self.row()[5])

    def test_timestamp_mismatch_between_column_and_json_is_rejected(self):
        self.insert_validated_event(
            block_event_ts=199.0,
            stored_block_event_ts=202.0,
        )
        app.establish_market_event_inbox_activation(now=200.5)

        with patch.object(app, "route_market_event") as route:
            result = app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(result["failed"], 1)
        route.assert_not_called()
        self.assertIn("timestamp", self.row()[5].lower())

    def test_routing_failure_is_terminal_instead_of_risking_a_second_order(self):
        self.insert_validated_event()
        app.establish_market_event_inbox_activation(now=200.5)

        with patch.object(
            app, "route_market_event", side_effect=RuntimeError("boom")
        ):
            first = app.consume_market_event_inbox_once(now=205.0)
            second = app.consume_market_event_inbox_once(now=206.0)

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["claimed"], 0)
        self.assertFalse(
            app.mark_market_event_processed("sig-1", 0, source="live")
        )
        self.assertEqual(self.row()[0], "failed")
        self.assertIn("RuntimeError: boom", self.row()[5])

    def test_reservation_failure_returns_the_row_for_a_safe_retry(self):
        """Si la reserva lanza, no hubo efectos: la fila vuelve a la cola.

        Solo la reserva se reintenta. Reconstruir la fila es determinista y el
        enrutado ya reservó; ambos siguen siendo terminales.
        """
        self.insert_validated_event()
        app.establish_market_event_inbox_activation(now=200.5)

        with (
            patch.object(
                app,
                "mark_market_event_processed",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            patch.object(app, "route_market_event") as route,
        ):
            first = app.consume_market_event_inbox_once(now=205.0)

        route.assert_not_called()
        self.assertEqual(first["released"], 1)
        self.assertEqual(first["failed"], 0)
        status, attempts, claim_token, claimed_ts, processed_ts, error = (
            self.row()
        )
        self.assertEqual(status, "validated")
        self.assertEqual(attempts, 1)
        self.assertIsNone(claim_token)
        self.assertIsNone(claimed_ts)
        self.assertIsNone(processed_ts)
        self.assertIn("database is locked", error)

        # La identidad sigue libre: el reintento la reserva y enruta una vez.
        with patch.object(app, "route_market_event") as route:
            second = app.consume_market_event_inbox_once(now=206.0)

        route.assert_called_once()
        self.assertEqual(second["processed"], 1)
        self.assertEqual(self.row()[0], "processed")
        self.assertEqual(self.row()[1], 2)
        self.assertFalse(
            app.mark_market_event_processed("sig-1", 0, source="live")
        )

    def test_missing_activation_does_not_claim_a_validated_row(self):
        self.insert_validated_event()

        with self.assertRaisesRegex(RuntimeError, "ACTIVATION_NOT_ESTABLISHED"):
            app.consume_market_event_inbox_once(now=205.0)

        self.assertEqual(self.row()[0], "validated")

    def test_stale_processing_claim_is_recovered(self):
        self.insert_validated_event()
        first = app.claim_market_event_inbox_processing_batch(now=205.0)[0]
        recovered_at = 206.0 + app.MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS
        second = app.claim_market_event_inbox_processing_batch(
            now=recovered_at
        )[0]

        self.assertNotEqual(first["claim_token"], second["claim_token"])
        self.assertFalse(app.finish_market_event_inbox_processing(
            "sig-1", 0, first["claim_token"], "duplicate", now=recovered_at,
        ))
        self.assertTrue(app.finish_market_event_inbox_processing(
            "sig-1", 0, second["claim_token"], "duplicate", now=recovered_at,
        ))
        self.assertEqual(self.row()[:2], ("duplicate", 2))


class MarketEventChronologyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        self.original_last_token_price = app.LAST_TOKEN_PRICE
        app.LAST_TOKEN_PRICE = {}
        app.DB = Path(self.temp_dir.name) / "event-chronology.db"
        app.migrate_database()
        self.addCleanup(self.restore)

    def restore(self):
        app.DB = self.original_db
        app.LAST_TOKEN_PRICE = self.original_last_token_price
        self.temp_dir.cleanup()

    def test_save_trade_and_history_use_onchain_time(self):
        event = {
            "signature": "chronology-1",
            "eventIndex": 0,
            "blockEventTs": 1_700_000_123.0,
            "mint": "mint-chronology",
            "txType": "sell",
            "marketCapSol": 50.0,
            "newTokenBalance": None,
        }

        with (
            patch.object(app, "save_token_history") as history,
            patch.object(app, "update_paper_position"),
            patch.object(app, "evaluate_live_position_exit") as live_exit,
        ):
            app.save_trade(
                "trader-1",
                "wallet-1",
                event,
                source="live",
                allow_live_exits=False,
            )

        conn = app.db()
        try:
            trade_ts = conn.execute(
                "SELECT ts FROM trades WHERE signature = 'chronology-1'"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(trade_ts, 1_700_000_123.0)
        self.assertEqual(history.call_args.kwargs["event_ts"], 1_700_000_123.0)
        live_exit.assert_not_called()

    def test_evaluation_and_outcome_use_onchain_signal_time(self):
        event = {
            "signature": "chronology-buy",
            "blockEventTs": 1_700_000_200.0,
            "mint": "mint-chronology-buy",
            "txType": "buy",
            "solAmount": 1.0,
            "marketCapSol": 50.0,
        }

        with (
            patch.object(app, "score_trader", return_value=0),
            patch.object(app, "score_timing", return_value=0),
            patch.object(app, "score_trade_size", return_value=0),
            patch.object(app, "score_token_structure", return_value=0),
            patch.object(app, "score_consensus", return_value=0) as consensus,
            patch.object(app, "score_market_context", return_value=0) as market,
            patch.object(app, "maybe_execute_live_copy") as live_copy,
        ):
            result = app.evaluate_buy(
                "trader-1",
                event,
                source="live",
                price_at_signal=0.0001,
                allow_live_buys=False,
            )

        live_copy.assert_not_called()
        self.assertEqual(
            result["live_execution"]["reason"],
            "TRANSPORT_LIVE_BUYS_DISABLED",
        )

        conn = app.db()
        try:
            evaluation_ts = conn.execute(
                "SELECT ts FROM evaluations WHERE trade_signature = ?",
                ("chronology-buy",),
            ).fetchone()[0]
            outcome_ts = conn.execute(
                "SELECT signal_ts FROM signal_outcomes"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(evaluation_ts, 1_700_000_200.0)
        self.assertEqual(outcome_ts, 1_700_000_200.0)
        self.assertEqual(consensus.call_args.kwargs["signal_ts"], 1_700_000_200.0)
        self.assertEqual(market.call_args.kwargs["signal_ts"], 1_700_000_200.0)

    def test_delayed_event_does_not_count_future_consensus(self):
        conn = app.db()
        conn.executemany(
            """
            INSERT INTO trades(ts, trader, side, mint, source)
            VALUES(?, ?, 'buy', 'mint-consensus', 'live')
            """,
            [(100.0, "early"), (130.0, "future")],
        )
        conn.commit()
        conn.close()

        self.assertEqual(
            app.score_consensus(
                "mint-consensus", "early", signal_ts=110.0
            ),
            0,
        )

    def test_outcome_checkpoint_uses_event_time(self):
        outcome_id = app.create_signal_outcome(
            signal_id=1,
            mint="mint-outcome",
            trader="trader-1",
            signal_ts=100.0,
            price_at_signal=1.0,
        )
        event = {
            "blockEventTs": 110.0,
            "vSolInBondingCurve": 2.0,
            "vTokensInBondingCurve": 1.0,
        }

        app.process_signal_outcomes_event("mint-outcome", event)

        conn = app.db()
        try:
            observed_ts = conn.execute(
                "SELECT observed_10s_ts FROM signal_outcomes WHERE id = ?",
                (outcome_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(observed_ts, 110.0)

    def test_delayed_closer_price_replaces_a_later_checkpoint_sample(self):
        outcome_id = app.create_signal_outcome(
            signal_id=2,
            mint="mint-out-of-order",
            trader="trader-1",
            signal_ts=100.0,
            price_at_signal=1.0,
        )

        app.process_signal_outcomes_event("mint-out-of-order", {
            "blockEventTs": 130.0,
            "vSolInBondingCurve": 3.0,
            "vTokensInBondingCurve": 1.0,
        })
        app.process_signal_outcomes_event("mint-out-of-order", {
            "blockEventTs": 110.0,
            "vSolInBondingCurve": 2.0,
            "vTokensInBondingCurve": 1.0,
        })

        conn = app.db()
        try:
            row = conn.execute(
                """
                SELECT price_10s, observed_10s_ts,
                       price_30s, observed_30s_ts
                FROM signal_outcomes WHERE id = ?
                """,
                (outcome_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row, (2.0, 110.0, 3.0, 130.0))
        self.assertEqual(
            app.LAST_TOKEN_PRICE["mint-out-of-order"],
            {"price": 3.0, "ts": 130.0},
        )

    def test_event_before_signal_cannot_change_outcome_extremes(self):
        outcome_id = app.create_signal_outcome(
            signal_id=3,
            mint="mint-before-signal",
            trader="trader-1",
            signal_ts=120.0,
            price_at_signal=1.0,
        )

        app.process_signal_outcomes_event("mint-before-signal", {
            "blockEventTs": 110.0,
            "vSolInBondingCurve": 0.5,
            "vTokensInBondingCurve": 1.0,
        })

        conn = app.db()
        try:
            row = conn.execute(
                """
                SELECT max_price, min_price, price_10s
                FROM signal_outcomes WHERE id = ?
                """,
                (outcome_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row, (1.0, 1.0, None))


if __name__ == "__main__":
    unittest.main()
