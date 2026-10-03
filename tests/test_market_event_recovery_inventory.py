import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app


class MarketEventRecoveryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "inventory.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def add_inbox(self, signature, status, claimed_ts, error=None):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO market_event_inbox(signature, event_index, "
                "event_index_scheme, source, received_ts, event_json, "
                "status, claimed_ts, last_error) "
                "VALUES(?,0,'log-v1','helius',?,'{}',?,?,?)",
                (signature, claimed_ts, status, claimed_ts, error),
            )
            conn.commit()
        finally:
            conn.close()

    def test_auth_and_read_only_evidence(self):
        old = time.time() - 1000
        self.add_inbox("sig-one", "failed", old, "ROUTING_OUTCOME_UNKNOWN")
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO processed_market_events(event_id, signature, "
                "event_index, ts, source, route_state) "
                "VALUES('sig-one:0','sig-one',0,?,'helius','uncertain')",
                (old,),
            )
            conn.execute(
                "INSERT INTO trades(ts, signature, event_index, source) "
                "VALUES(?,'sig-one',0,'live')", (old,),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-secret"):
            with self.assertRaises(HTTPException) as rejected:
                app.market_event_recovery_inventory(x_app_token="wrong")
            self.assertEqual(rejected.exception.status_code, 401)
            result = app.market_event_recovery_inventory(
                x_app_token="test-secret",
            )
        self.assertTrue(result["inventory_only"])
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertTrue(row["requires_manual_review"])
        self.assertEqual(row["signature_index_trade_rows"], 1)
        self.assertEqual(row["signature_index_evaluation_rows"], 0)
        conn = app.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT status, last_error FROM market_event_inbox"
            ).fetchone(), ("failed", "ROUTING_OUTCOME_UNKNOWN"))
            self.assertEqual(conn.execute(
                "SELECT route_state FROM processed_market_events "
                "WHERE event_id = 'sig-one:0'"
            ).fetchone()[0], "uncertain")
        finally:
            conn.close()

    def test_fresh_claim_is_excluded_and_limit_is_reported(self):
        now = time.time()
        self.add_inbox("fresh", "routing", now)
        self.add_inbox("stale-one", "routing", now - 1000)
        self.add_inbox("stale-two", "routing", now - 900)
        with patch.object(app, "APP_TOKEN", "test-secret"):
            result = app.market_event_recovery_inventory(
                limit=1, x_app_token="test-secret",
            )
        self.assertTrue(result["inbox_truncated"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertNotEqual(result["rows"][0]["signature"], "fresh")

    def test_processed_only_legacy_trade_is_ambiguous(self):
        old = time.time() - 1000
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO processed_market_events(event_id, signature, "
                "event_index, ts, source, route_state) "
                "VALUES('legacy:1','legacy',1,?,'live','uncertain')",
                (old,),
            )
            conn.execute(
                "INSERT INTO trades(ts, signature, event_index, source) "
                "VALUES(?,'legacy',NULL,'live')", (old,),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-secret"):
            result = app.market_event_recovery_inventory(
                x_app_token="test-secret",
            )
        row = result["rows"][0]
        self.assertIsNone(row["inbox_status"])
        self.assertEqual(row["signature_index_trade_rows"], 0)
        self.assertEqual(row["ambiguous_legacy_trades"], 1)
        self.assertTrue(row["requires_manual_review"])

    def test_routing_without_processed_row_is_manual(self):
        self.add_inbox("routing-only", "routing", time.time() - 1000)
        with patch.object(app, "APP_TOKEN", "test-secret"):
            result = app.market_event_recovery_inventory(
                x_app_token="test-secret",
            )
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["rows"][0]["requires_manual_review"])

    def test_fresh_reclaim_hides_old_processed_reservation(self):
        now = time.time()
        self.add_inbox("reclaimed", "reserved", now)
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO processed_market_events(event_id, signature, "
                "event_index, ts, source, route_state) "
                "VALUES('reclaimed:0','reclaimed',0,?,'helius','reserved')",
                (now - 1000,),
            )
            conn.commit()
        finally:
            conn.close()
        with patch.object(app, "APP_TOKEN", "test-secret"):
            result = app.market_event_recovery_inventory(
                x_app_token="test-secret",
            )
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
