import tempfile
import unittest
from pathlib import Path

import app


class WalletStageTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "timing.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def test_fetch_timestamps_persist_and_migration_is_repeatable(self):
        app.migrate_database()
        app.record_helius_standard_wss_notification(
            {"signature": "sig-fetch", "wallet": "wallet-a", "slot": 1},
            True, 100, received_ts=1000,
        )
        app.finish_helius_standard_wss_transaction(
            "sig-fetch", "applied", 1, parsed_events=1, block_time=995,
            now=1005, fetch_started_ts=1001, receipt_received_ts=1003,
        )
        conn = app.db()
        try:
            row = conn.execute(
                "SELECT fetch_started_ts, receipt_received_ts, fetched_ts "
                "FROM helius_standard_wss_transactions WHERE signature = 'sig-fetch'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (1001, 1003, 1005))

    def test_wallet_stages_and_missing_rows_are_reported_separately(self):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO helius_standard_wss_notifications "
                "(signature, wallet, received_ts, failed, pump_logs, message_bytes, subject_type) "
                "VALUES ('sig-a', 'wallet-a', 1010, 0, 1, 100, 'wallet'), "
                "('sig-b', 'wallet-b', 1020, 0, 1, 100, 'wallet')"
            )
            conn.execute(
                "INSERT INTO helius_standard_wss_transactions "
                "(signature, first_received_ts, fetched_ts, fetch_started_ts, "
                "receipt_received_ts, block_time, status) "
                "VALUES ('sig-a', 1010, 1015, 1011, 1012, 1000, 'applied'), "
                "('sig-b', 1020, 1021, NULL, NULL, NULL, 'queue_full')"
            )
            conn.execute(
                "INSERT INTO market_event_inbox "
                "(signature, event_index, source, wallet, received_ts, inserted_ts, event_json) "
                "VALUES ('sig-a', 0, 'helius', 'wallet-a', 1010, 1013, '{}')"
            )
            conn.execute(
                "INSERT INTO trades "
                "(signature, event_index, wallet, source, transport, recorded_ts) "
                "VALUES ('sig-a', 0, 'wallet-a', 'live', 'helius', 1016)"
            )
            conn.execute(
                "INSERT INTO evaluations "
                "(id, trade_signature, event_index, transport, recorded_ts) "
                "VALUES (1, 'sig-a', 0, 'helius', 1017)"
            )
            conn.commit()
            report = app.get_helius_standard_wss_wallet_stage_timing(conn, 1000)
        finally:
            conn.close()

        wallets = {row["wallet"]: row for row in report["wallets"]}
        self.assertEqual(report["sampled_wallet_notifications"], 2)
        self.assertEqual(wallets["wallet-a"]["decisions"], 1)
        self.assertEqual(wallets["wallet-a"]["stages"]["block_to_notice"], {
            "n": 1, "p50_seconds": 10.0, "p95_seconds": 10.0,
        })
        self.assertEqual(wallets["wallet-a"]["stages"]["inbox_to_decision"]["p50_seconds"], 4.0)
        self.assertEqual(wallets["wallet-b"]["queue_full"], 1)
        self.assertEqual(wallets["wallet-b"]["decisions"], 0)
        self.assertEqual(wallets["wallet-b"]["stages"]["block_to_decision"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
