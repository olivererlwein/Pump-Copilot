import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PaperImmediateQuoteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.directory.name) / "immediate.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.directory.cleanup()

    def add_signal(self, signal_id, now, trader):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO evaluations(id, trade_signature, event_index, ts, "
                "recorded_ts, trader, mint, source, transport, decision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (signal_id, f"sig-{signal_id}", 0, now - 1, now - 0.5,
                 trader, f"mint-{signal_id}", "live", "helius", "WATCH"),
            )
            conn.commit()
        finally:
            conn.close()
        app.create_signal_outcome(signal_id, f"mint-{signal_id}", trader,
                                  now - 1, 1.0, entry_price_basis="pump")
        conn = app.db()
        try:
            conn.execute("UPDATE signal_outcomes SET created_ts = ? "
                         "WHERE signal_id = ?", (now - 0.4, signal_id))
            conn.commit()
        finally:
            conn.close()

    def test_budget_idempotence_and_failure_do_not_retry(self):
        now = 1_800_000_000.0
        for signal_id in range(1, 26):
            self.add_signal(signal_id, now, f"trader-{signal_id % 11}")

        def quote(_url, _mints, rpc_request):
            rpc_request("https://rpc.example", "getMultipleAccounts", [])
            rpc_request("https://rpc.example", "getMultipleAccounts", [])
            return {_mints[0]: {"status": "curve", "price_sol": 1.0,
                                "slot": 100}}

        with (patch.object(app, "OBSERVE_TRADERS", set()),
              patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.example"),
              patch.object(app, "urlopen", side_effect=lambda *_args, **_kwargs:
                           io.BytesIO(b'{"result":{}}')),
              patch.object(app, "fetch_account_prices", side_effect=quote) as fetch):
            for _ in range(25):
                app.paper_immediate_quote_once(now=now)
            self.assertEqual(app.paper_immediate_quote_once(now=now)["status"],
                             "daily_cap")
            self.assertEqual(fetch.call_count, 20)
        conn = app.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*), SUM(rpc_reserved), SUM(rpc_used) "
                "FROM paper_immediate_quotes").fetchone(), (20, 40, 40))
        finally:
            conn.close()

    def test_one_failed_quote_is_not_retried(self):
        now = 1_800_000_000.0
        self.add_signal(1, now, "trader")
        with (patch.object(app, "OBSERVE_TRADERS", set()),
              patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.example"),
              patch.object(app, "fetch_account_prices", side_effect=TimeoutError)):
            self.assertEqual(app.paper_immediate_quote_once(now=now)["status"],
                             "error")
            self.assertEqual(app.paper_immediate_quote_once(now=now)["status"],
                             "no_fresh_signal")
        conn = app.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT status, rpc_reserved FROM paper_immediate_quotes"
            ).fetchone(), ("error", 2))
        finally:
            conn.close()

    def test_report_uses_first_sell_and_both_clocks(self):
        now = 1_800_000_000.0
        self.add_signal(1, now, "trader")
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO trades(ts, recorded_ts, trader, mint, side, "
                "source) VALUES(?,?,?,?,?,?)",
                (now + 2, now + 5, "trader", "mint-1", "sell", "live"),
            )
            conn.commit()
        finally:
            conn.close()

        def quote(_url, mints, rpc_request):
            rpc_request("https://rpc.example", "getMultipleAccounts", [])
            return {mints[0]: {"status": "curve", "price_sol": 1.0,
                               "slot": 100}}

        with (patch.object(app, "OBSERVE_TRADERS", set()),
              patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.example"),
              patch.object(app, "urlopen", side_effect=lambda *_args, **_kwargs:
                           io.BytesIO(b'{"result":{}}')),
              patch.object(app, "fetch_account_prices", side_effect=quote),
              patch.object(app, "auth"),
              patch.object(app.time, "time", return_value=now + 3)):
            app.paper_immediate_quote_once(now=now + 3)
            report = app.paper_immediate_quote_pilot(
                after_ts=now - 10, x_app_token="ignored",
            )
        trader = report["per_trader"]["trader"]
        self.assertEqual(trader["priced"], 1)
        self.assertEqual(trader["before_sell_onchain"], 0)
        self.assertEqual(trader["before_sell_recorded"], 1)
        self.assertEqual(trader["before_sell_both"], 0)

    def test_per_trader_cap_and_null_sell_recorded_time(self):
        now = 1_800_000_000.0
        for signal_id in range(1, 6):
            self.add_signal(signal_id, now, "same-trader")
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO trades(ts, trader, mint, side, source) "
                "VALUES(?,?,?,?,?)",
                (now + 2, "same-trader", "mint-1", "sell", "live"),
            )
            conn.commit()
        finally:
            conn.close()
        with (patch.object(app, "OBSERVE_TRADERS", set()),
              patch.object(app, "standard_wss_rpc_url", return_value="https://rpc.example"),
              patch.object(app, "fetch_account_prices", return_value={}),
              patch.object(app, "auth"),
              patch.object(app.time, "time", return_value=now + 1)):
            for _ in range(5):
                app.paper_immediate_quote_once(now=now)
            report = app.paper_immediate_quote_pilot(
                after_ts=now - 10, x_app_token="ignored",
            )
        self.assertEqual(report["per_trader"]["same-trader"]["attempted"], 3)
        self.assertEqual(report["per_trader"]["same-trader"]["unattempted"], 2)
        self.assertIsNone(report["signals"][0]["first_sell_recorded_ts"])


if __name__ == "__main__":
    unittest.main()
