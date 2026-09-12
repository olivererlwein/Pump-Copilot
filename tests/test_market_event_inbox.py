import json
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
            "mint": "mint-1",
            "txType": "buy",
        }
        conn = app.db()
        conn.execute(
            """
            INSERT INTO market_event_inbox(
                signature, event_index, source, wallet, trader, mint, side,
                received_ts, event_json
            )
            VALUES(?,?,?,?,?,?,?,?,?)
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


if __name__ == "__main__":
    unittest.main()
