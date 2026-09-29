import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from scripts.wallet_activity_coverage import (
    assign_tiers, mark_known_mute, summarize,
)


def wss_event(wallet, signature):
    return {
        "wallet": wallet, "signature": signature, "slot": 1,
        "failed": False, "logs": [],
    }


class WalletCoverageBucketsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "coverage.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def notify(self, wallet, signature, ts, status=None, reason=None):
        app.record_helius_standard_wss_notification(
            wss_event(wallet, signature), True, 100, received_ts=ts
        )
        if status:
            app.finish_helius_standard_wss_transaction(
                signature, status, 1, unparsed_reason=reason, now=ts + 1
            )

    def report(self, muted=None, started=0.0):
        state = {**app.HELIUS_STANDARD_WSS_STATE,
                 "muted_wallets": muted or {}}
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "WATCHED", {"sling": "wallet-s", "other": "wallet-o"}),
            patch.object(app, "HELIUS_STANDARD_WSS_STATE", state),
            patch.object(app, "PROCESS_STARTED_TS", started),
        ):
            return app.api_wallet_coverage_buckets(
                "sling", 7200.0, 14400.0, 3600, "token"
            )

    def test_separates_own_trades_mentions_and_unattributable_losses(self):
        self.notify("wallet-s", "own", 7300, "observed")
        self.notify("wallet-s", "mention", 7400, "unparsed", "wallet_not_signer")
        self.notify("wallet-s", "lost", 7500, "queue_full")
        self.notify("wallet-o", "elsewhere", 7600, "observed")
        self.notify("wallet-s", "later", 11000, "observed")

        buckets = self.report()["buckets"]

        self.assertEqual([row["start_ts"] for row in buckets], [7200, 10800])
        first = buckets[0]
        self.assertEqual(first["pump_notifications"], 3)
        self.assertEqual(first["all_wallets_pump_notifications"], 4)
        self.assertEqual(first["own_parsed_transactions"], 1)
        self.assertEqual(first["mention_transactions"], 1)
        self.assertEqual(first["unattributable"], {"queue_full": 1})
        self.assertEqual(buckets[1]["own_parsed_transactions"], 1)

    def test_counts_real_trades_by_transport_and_fallback_status(self):
        conn = app.db()
        try:
            conn.executemany(
                "INSERT INTO trades(ts, trader, wallet, side, mint, source, "
                "transport) VALUES(?, 'sling', 'wallet-s', 'buy', 'm', ?, ?)",
                [(7300, "live", "helius_standard_wss"),
                 (7400, "live", "rpc"),
                 (7500, "demo", "helius_standard_wss")],
            )
            conn.execute(
                "INSERT INTO rpc_fallback_events(signature, event_index, "
                "block_time, detected_ts, trader, wallet, program, "
                "event_name, side, mint, sol, market_cap_sol, token_amount, "
                "pool, event_json, status) VALUES('f', 0, 7300, 7400, "
                "'sling', 'wallet-s', 'pump', 'buy', 'buy', 'm', 1, 1, 1, "
                "'pump', '{}', 'missing')"
            )
            conn.execute(
                "INSERT INTO rpc_fallback_wallet_state(wallet, trader, "
                "last_rebase_ts) VALUES('wallet-s', 'sling', 7350)"
            )
            conn.commit()
        finally:
            conn.close()

        first = self.report()["buckets"][0]

        self.assertEqual(
            first["trades_by_transport"],
            {"helius_standard_wss": 1, "rpc": 1},
        )
        self.assertEqual(first["fallback_events"], {"missing": 1})
        self.assertTrue(first["fallback_rebase_in_bucket"])

    def test_muted_is_unknown_before_process_start(self):
        muted = {"wallet-s": {"trader": "sling", "muted_ts": 11000.0}}
        buckets = self.report(muted=muted, started=9000.0)["buckets"]
        self.assertEqual([row["muted"] for row in buckets], [None, True])

        buckets = self.report(started=9000.0)["buckets"]
        self.assertEqual([row["muted"] for row in buckets], [None, False])

    def test_persisted_mute_marks_only_its_historical_interval(self):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO app_state(key, value) VALUES(?, ?)",
                (app.HELIUS_WSS_MUTE_HISTORY_START_STATE_KEY, "7200"),
            )
            conn.execute(
                "INSERT INTO helius_standard_wss_wallet_mutes("
                "wallet, trader, muted_ts, muted_until_ts, unmuted_ts, "
                "rate_per_minute) VALUES('wallet-s', 'sling', 11000, "
                "11500, 11500, 601)"
            )
            conn.commit()
            with patch.object(app, "PROCESS_STARTED_TS", 20000.0):
                buckets = app.get_wallet_coverage_buckets(
                    conn, "wallet-s", 7200.0, 18000.0, 3600
                )
        finally:
            conn.close()
        self.assertEqual([row["muted"] for row in buckets],
                         [False, True, False])

    def test_expired_cooldown_remains_muted_until_subscription_confirmation(self):
        conn = app.db()
        try:
            conn.execute(
                "INSERT INTO app_state(key, value) VALUES(?, ?)",
                (app.HELIUS_WSS_MUTE_HISTORY_START_STATE_KEY, "7200"),
            )
            conn.execute(
                "INSERT INTO helius_standard_wss_wallet_mutes("
                "wallet, trader, muted_ts, muted_until_ts, rate_per_minute) "
                "VALUES('wallet-s', 'sling', 11000, 11500, 601)"
            )
            conn.commit()
            with patch.object(app, "PROCESS_STARTED_TS", 20000.0):
                buckets = app.get_wallet_coverage_buckets(
                    conn, "wallet-s", 7200.0, 18000.0, 3600
                )
        finally:
            conn.close()
        self.assertEqual([row["muted"] for row in buckets],
                         [False, True, True])

    def test_rejects_unbounded_ranges(self):
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "WATCHED", {"sling": "wallet-s"}),
        ):
            with self.assertRaises(app.HTTPException):
                app.api_wallet_coverage_buckets(
                    "sling", 0.0, 8 * 86400.0, 3600, "token"
                )


def bucket(start, notifications, all_wallets=None, muted=False, **extra):
    return {
        "start_ts": start,
        "pump_notifications": notifications,
        "all_wallets_pump_notifications": (
            notifications if all_wallets is None else all_wallets
        ),
        "own_parsed_transactions": 0,
        "mention_transactions": 0,
        "other_unparsed_transactions": 0,
        "unattributable": {},
        "trades_by_transport": {},
        "fallback_events": {},
        "fallback_rebase_in_bucket": False,
        "muted": muted,
        **extra,
    }


class ActivityTierTests(unittest.TestCase):
    def test_tiers_use_notification_terciles_and_exclude_outages(self):
        buckets = [
            bucket(0, 0, all_wallets=0),
            bucket(1, 50, muted=True),
            bucket(2, 0, all_wallets=9),
            bucket(3, 30), bucket(4, 10), bucket(5, 20),
            bucket(6, 60), bucket(7, 40), bucket(8, 50),
        ]
        tiers = assign_tiers(buckets)
        self.assertEqual(
            [tiers[start] for start in range(9)],
            ["wss_silent", "muted", "idle",
             "mid", "low", "low", "high", "mid", "high"],
        )

    def test_mute_and_unknown_take_priority_over_silent_or_activity(self):
        buckets = [
            bucket(0, 0, all_wallets=0, muted=True),
            bucket(1, 20, all_wallets=20, muted=None),
            bucket(2, 10, all_wallets=10),
        ]
        self.assertEqual(assign_tiers(buckets), {
            0: "muted", 1: "unknown", 2: "low",
        })

    def test_known_legacy_mute_marks_partial_buckets(self):
        buckets = [
            bucket(0, 0, muted=None),
            bucket(3600, 0, muted=None),
            bucket(7200, 0, muted=None),
        ]
        mark_known_mute(buckets, 3500, 3700, 3600)
        self.assertEqual(
            [row["muted"] for row in buckets], [True, True, None]
        )

    def test_partial_edge_buckets_exclude_signals_outside_requested_window(self):
        buckets = [bucket(7200, 10), bucket(10800, 20)]
        rows = [
            {"signal_id": index, "trader": "sling", "probability": 0.9,
             "signal_ts": ts}
            for index, ts in enumerate((7200, 7300, 12499, 12500), start=1)
        ]
        with patch(
            "scripts.validate_frozen_exit_candidate.selected_records",
            side_effect=lambda selected, *_: [
                {**row, "net": -0.1} for row in selected
            ],
        ) as records:
            report = summarize(
                buckets, rows, "sling", 3600, since=7250, until=12500
            )
        self.assertEqual(
            [row["signal_id"] for row in records.call_args.args[0]],
            [2, 3],
        )
        self.assertEqual(
            sum(tier.get("selections", 0) for tier in report["tiers"].values()),
            2,
        )

    def test_summary_counts_losses_and_selection_coverage_per_tier(self):
        buckets = [
            bucket(0, 10, own_parsed_transactions=6, mention_transactions=2,
                   unattributable={"queue_full": 2}),
            bucket(100, 20, unattributable={"queue_full": 10}),
            bucket(200, 30, unattributable={"queue_full": 24}),
        ]
        signal = {
            "trader": "sling", "probability": 0.9, "signal_ts": 250,
            "price_at_signal": 1.0, "path": [],
            "subscription_coverage": {
                "measurement_available": True, "complete": False,
                "intervals": [{}], "subscription_continuous": False,
            },
        }
        rows = [
            {**signal, "signal_id": 1},
            {**signal, "signal_id": 2, "trader": "someone-else"},
        ]
        with patch(
            "scripts.validate_frozen_exit_candidate.selected_records",
            side_effect=lambda selected, *_: [
                {**row, "net": -0.1} for row in selected
            ],
        ):
            report = summarize(buckets, rows, "sling", 100)["tiers"]

        self.assertEqual(report["low"]["delivery_loss_ratio"], 0.2)
        self.assertEqual(report["low"]["mention_share_of_attributed"], 0.25)
        self.assertEqual(report["high"]["delivery_loss_ratio"], 0.8)
        self.assertEqual(report["high"]["selections"], 1)
        self.assertEqual(report["high"]["measured"], 1)
        self.assertEqual(report["high"]["subscription_gap"], 1)
        self.assertNotIn("selections", report["low"])


if __name__ == "__main__":
    unittest.main()
