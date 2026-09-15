import json
import math
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app


class NumericRiskValidationTests(unittest.TestCase):
    def test_rejects_invalid_slippage_inputs(self):
        invalid_values = (
            None,
            "invalid",
            0,
            -1,
            math.nan,
            math.inf,
            -math.inf,
        )

        for value in invalid_values:
            with self.subTest(value=value):
                self.assertFalse(app.validate_slippage(value, 1))
                self.assertFalse(app.validate_slippage(1, value))

    def test_rejects_invalid_liquidity_inputs(self):
        invalid_values = (
            None,
            "invalid",
            0,
            -1,
            math.nan,
            math.inf,
            -math.inf,
        )

        for value in invalid_values:
            with self.subTest(value=value):
                self.assertFalse(app.validate_liquidity(value))

    def test_rejects_invalid_amount_before_database_checks(self):
        invalid_values = (
            None,
            "invalid",
            0,
            -1,
            math.nan,
            math.inf,
            -math.inf,
        )

        with patch.object(
            app,
            "get_daily_realized_pnl",
            side_effect=AssertionError("database check should not run"),
        ):
            for value in invalid_values:
                with self.subTest(value=value):
                    result = app.risk_check("mint-a", value)
                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        result["reason"],
                        "INVALID_AMOUNT_USD",
                    )


class LiveTradingGuardTests(unittest.TestCase):
    def test_readiness_endpoint_exposes_side_specific_preflight(self):
        expected = {"ready": False, "blockers": ["LIVE_BUYS_DISABLED"]}
        with patch.object(app, "auth") as auth, patch.object(
            app,
            "get_live_execution_readiness",
            return_value=expected,
        ) as readiness:
            result = app.api_live_execution_readiness(
                x_app_token="test-token",
                execution_side=" BUY ",
            )

        auth.assert_called_once_with("test-token")
        readiness.assert_called_once_with("buy")
        self.assertEqual(result, expected)

    def test_readiness_endpoint_rejects_unknown_side(self):
        with patch.object(app, "auth"), self.assertRaises(
            app.HTTPException,
        ) as raised:
            app.api_live_execution_readiness(
                x_app_token="test-token",
                execution_side="withdraw",
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "INVALID_EXECUTION_SIDE")

    def test_live_readiness_rejects_a_different_balance_wallet(self):
        shadow_stats = {"promotion_assessment": {"blockers": []}}
        with patch.object(
            app,
            "get_shadow_stats",
            return_value=shadow_stats,
        ), patch.object(app, "API_KEY", "test-key"), patch.object(
            app,
            "PUMPPORTAL_WALLET_ADDRESS",
            "balance-wallet",
        ), patch.object(
            app,
            "PUMPPORTAL_TRADING_WALLET_ADDRESS",
            "trading-wallet",
        ), patch.object(app, "STREAM_CONNECTED", True), patch.object(
            app,
            "PUMPPORTAL_WALLET_BALANCE_SOL",
            0.05,
        ), patch.object(app, "KILL_SWITCH", False), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            False,
        ), patch.object(app, "LIVE_SELLS_ENABLED", True):
            readiness = app.get_live_execution_readiness("sell")

        self.assertFalse(readiness["ready"])
        self.assertIn("PUMPPORTAL_WALLET_MISMATCH", readiness["blockers"])

    def test_live_execution_cannot_be_enabled_before_implementation(self):
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            False,
        ):
            with self.assertRaises(app.HTTPException) as raised:
                app.require_live_trading()

        self.assertEqual(raised.exception.status_code, 501)
        self.assertEqual(
            raised.exception.detail,
            "LIVE_EXECUTION_NOT_IMPLEMENTED",
        )

        with patch.object(app, "LIVE_TRADING", True):
            status = app.status(app.APP_TOKEN)

        self.assertTrue(status["live_trading_requested"])
        self.assertFalse(status["live_execution_implemented"])
        self.assertFalse(status["live_trading_active"])

    def test_live_buy_and_sell_have_independent_switches(self):
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(app, "LIVE_BUYS_ENABLED", False), patch.object(
            app,
            "LIVE_SELLS_ENABLED",
            False,
        ):
            with self.assertRaises(app.HTTPException) as buy_raised:
                app.require_live_trading("buy")
            with self.assertRaises(app.HTTPException) as sell_raised:
                app.require_live_trading("sell")

        self.assertEqual(buy_raised.exception.detail, "LIVE_BUYS_DISABLED")
        self.assertEqual(sell_raised.exception.detail, "LIVE_SELLS_DISABLED")

        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(app, "LIVE_BUYS_ENABLED", True), patch.object(
            app,
            "LIVE_SELLS_ENABLED",
            False,
        ):
            app.require_live_trading("buy")
            with self.assertRaises(app.HTTPException) as sell_raised:
                app.require_live_trading("sell")

        self.assertEqual(sell_raised.exception.detail, "LIVE_SELLS_DISABLED")

    def test_live_readiness_reports_every_remaining_guard(self):
        shadow_stats = {
            "promotion_assessment": {
                "minimum_completed": 100,
                "ready_for_review": False,
                "leader": None,
                "blockers": ["paired_completed 42/100"],
            },
        }

        with patch.object(
            app,
            "get_shadow_stats",
            return_value=shadow_stats,
        ), patch.object(app, "API_KEY", "test-key"), patch.object(
            app,
            "STREAM_CONNECTED",
            True,
        ), patch.object(
            app,
            "PUMPPORTAL_WALLET_BALANCE_SOL",
            0.05,
        ), patch.object(
            app,
            "KILL_SWITCH",
            False,
        ), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            False,
        ), patch.object(app, "LIVE_TRADING", False), patch.object(
            app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", "",
        ):
            readiness = app.get_live_execution_readiness()

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["blockers"],
            [
                "PUMPPORTAL_TRADING_WALLET_MISSING",
                "LIVE_EXECUTION_NOT_IMPLEMENTED",
                "LIVE_TRADING_DISABLED",
            ],
        )

    def test_live_readiness_reports_side_specific_switches(self):
        shadow_stats = {
            "promotion_assessment": {
                "minimum_completed": 100,
                "ready_for_review": True,
                "leader": "challenger",
                "blockers": [],
            },
        }

        patches = (
            patch.object(app, "get_shadow_stats", return_value=shadow_stats),
            patch.object(app, "API_KEY", "test-key"),
            patch.object(app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", "wallet-a"),
            patch.object(app, "STREAM_CONNECTED", True),
            patch.object(app, "PUMPPORTAL_WALLET_BALANCE_SOL", 0.05),
            patch.object(app, "KILL_SWITCH", False),
            patch.object(app, "LIVE_EXECUTION_IMPLEMENTED", True),
            patch.object(app, "LIVE_TRADING", True),
            patch.object(app, "LIVE_BUYS_ENABLED", True),
            patch.object(app, "LIVE_SELLS_ENABLED", False),
            patch.object(app, "LIVE_BUY_USD", 1.0, create=True),
            patch.object(app, "get_live_canary_blockers", return_value=[]),
        )

        with patch.object(
            app, "PUMPPORTAL_WALLET_ADDRESS", "wallet-a"
        ), patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patches[9], patches[10], (
            patches[11]
        ):
            buy = app.get_live_execution_readiness("buy")
            sell = app.get_live_execution_readiness("sell")

        self.assertTrue(buy["ready"])
        self.assertEqual(buy["blockers"], [])
        self.assertFalse(sell["ready"])
        self.assertEqual(sell["blockers"], ["LIVE_SELLS_DISABLED"])

    def test_general_live_readiness_requires_at_least_one_side_enabled(self):
        shadow_stats = {
            "promotion_assessment": {
                "minimum_completed": 100,
                "ready_for_review": True,
                "leader": "challenger",
                "blockers": [],
            },
        }

        patches = (
            patch.object(app, "get_shadow_stats", return_value=shadow_stats),
            patch.object(app, "API_KEY", "test-key"),
            patch.object(app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", "wallet-a"),
            patch.object(app, "STREAM_CONNECTED", True),
            patch.object(app, "PUMPPORTAL_WALLET_BALANCE_SOL", 0.05),
            patch.object(app, "KILL_SWITCH", False),
            patch.object(app, "LIVE_EXECUTION_IMPLEMENTED", True),
            patch.object(app, "LIVE_TRADING", True),
            patch.object(app, "LIVE_BUYS_ENABLED", False),
            patch.object(app, "LIVE_SELLS_ENABLED", False),
        )

        with patch.object(
            app, "PUMPPORTAL_WALLET_ADDRESS", "wallet-a"
        ), patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patches[9]:
            readiness = app.get_live_execution_readiness()

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["blockers"],
            ["LIVE_BUYS_AND_SELLS_DISABLED"],
        )

    def test_live_buy_readiness_requires_explicit_safe_amount(self):
        shadow_stats = {
            "promotion_assessment": {
                "minimum_completed": 100,
                "ready_for_review": True,
                "leader": "challenger",
                "blockers": [],
            },
        }

        with patch.object(
            app,
            "get_shadow_stats",
            return_value=shadow_stats,
        ), patch.object(app, "API_KEY", "test-key"), patch.object(
            app,
            "PUMPPORTAL_TRADING_WALLET_ADDRESS",
            "wallet-a",
        ), patch.object(
            app,
            "PUMPPORTAL_WALLET_ADDRESS",
            "wallet-a",
        ), patch.object(app, "STREAM_CONNECTED", True), patch.object(
            app,
            "PUMPPORTAL_WALLET_BALANCE_SOL",
            0.05,
        ), patch.object(app, "KILL_SWITCH", False), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(app, "LIVE_BUY_USD", 0.0, create=True):
            with patch.object(
                app,
                "get_live_canary_blockers",
                return_value=[],
            ):
                readiness = app.get_live_execution_readiness("buy")

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["blockers"],
            ["LIVE_BUY_USD_INVALID"],
        )


class LiveCanaryGuardTests(unittest.TestCase):
    def approved_model(self, version="incumbent-v1"):
        model = MagicMock()
        model.model_version = version
        model.deployment_ready = True
        return model

    def canary_patches(self):
        return (
            patch.object(app, "LIVE_CANARY_ENABLED", True, create=True),
            patch.object(
                app,
                "LIVE_APPROVED_MODEL_VERSION",
                "incumbent-v1",
                create=True,
            ),
            patch.object(app, "SHADOW_MODEL", self.approved_model()),
            patch.object(app, "LIVE_CANARY_MAX_BUY_USD", 1.0, create=True),
            patch.object(app, "LIVE_CANARY_MAX_BUYS_PER_DAY", 1, create=True),
            patch.object(
                app,
                "LIVE_CANARY_MAX_DAILY_NOTIONAL_USD",
                1.0,
                create=True,
            ),
            patch.object(
                app,
                "LIVE_CANARY_ALLOWED_TRADERS",
                {"marcell"},
                create=True,
            ),
            patch.object(app, "LIVE_SELLS_ENABLED", True),
            patch.object(
                app,
                "get_live_exit_feed_readiness",
                return_value={"ready": True, "blockers": []},
                create=True,
            ),
            patch.object(
                app,
                "get_daily_live_buy_exposure",
                return_value={"attempts": 0, "notional_usd": 0.0},
                create=True,
            ),
        )

    def test_canary_is_fail_closed_until_explicitly_enabled(self):
        with patch.object(
            app,
            "LIVE_CANARY_ENABLED",
            False,
            create=True,
        ), patch.object(
            app,
            "LIVE_APPROVED_MODEL_VERSION",
            "",
        ), patch.object(
            app,
            "SHADOW_MODEL",
            None,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_BUY_USD",
            0.0,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_BUYS_PER_DAY",
            0,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_DAILY_NOTIONAL_USD",
            0.0,
        ), patch.object(
            app,
            "LIVE_CANARY_ALLOWED_TRADERS",
            set(),
        ), patch.object(
            app,
            "LIVE_SELLS_ENABLED",
            False,
        ), patch.object(
            app,
            "get_live_exit_feed_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app,
            "get_daily_live_buy_exposure",
            return_value={"attempts": 0, "notional_usd": 0.0},
        ):
            blockers = app.get_live_canary_blockers(
                trader="marcell",
                amount_usd=1.0,
            )

        self.assertEqual(
            blockers,
            [
                "LIVE_CANARY_DISABLED",
                "LIVE_APPROVED_MODEL_VERSION_MISSING",
                "LIVE_MODEL_NOT_DEPLOYMENT_READY",
                "LIVE_CANARY_MAX_BUY_USD_INVALID",
                "LIVE_CANARY_MAX_BUYS_PER_DAY_INVALID",
                "LIVE_CANARY_MAX_DAILY_NOTIONAL_USD_INVALID",
                "LIVE_CANARY_BUY_AMOUNT_INVALID",
                "LIVE_CANARY_TRADERS_MISSING",
                "LIVE_SELLS_REQUIRED_FOR_BUYS",
            ],
        )

    def test_status_never_reports_buy_ready_when_canary_blocks(self):
        shadow_stats = {"promotion_assessment": {"blockers": []}}
        with patch.object(
            app,
            "get_shadow_stats",
            return_value=shadow_stats,
        ), patch.object(app, "API_KEY", "test-key"), patch.object(
            app,
            "PUMPPORTAL_TRADING_WALLET_ADDRESS",
            "wallet-a",
        ), patch.object(
            app,
            "PUMPPORTAL_WALLET_ADDRESS",
            "wallet-a",
        ), patch.object(app, "STREAM_CONNECTED", True), patch.object(
            app,
            "PUMPPORTAL_WALLET_BALANCE_SOL",
            0.05,
        ), patch.object(app, "KILL_SWITCH", False), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(app, "LIVE_SELLS_ENABLED", True), patch.object(
            app,
            "LIVE_BUY_USD",
            1.0,
        ), patch.object(
            app,
            "get_live_canary_blockers",
            return_value=["LIVE_CANARY_DISABLED"],
        ):
            readiness = app.get_live_execution_readiness("buy")

        self.assertFalse(readiness["ready"])
        self.assertFalse(readiness["live_buy_ready"])
        self.assertTrue(readiness["live_sell_ready"])

    def test_canary_requires_exact_approved_model_and_trader(self):
        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patches[9], patch.object(
            app,
            "LIVE_APPROVED_MODEL_VERSION",
            "other-model",
        ):
            wrong_model = app.get_live_canary_blockers("marcell", 1.0)

        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patches[9]:
            wrong_trader = app.get_live_canary_blockers("decu", 1.0)

        self.assertIn("LIVE_APPROVED_MODEL_VERSION_MISMATCH", wrong_model)
        self.assertIn("LIVE_CANARY_TRADER_NOT_ALLOWED", wrong_trader)

    def test_canary_enforces_hard_and_daily_exposure_limits(self):
        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patch.object(
            app,
            "get_daily_live_buy_exposure",
            return_value={"attempts": 1, "notional_usd": 1.0},
            create=True,
        ):
            exhausted = app.get_live_canary_blockers("marcell", 1.0)

        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patch.object(
            app,
            "LIVE_CANARY_MAX_BUY_USD",
            2.0,
            create=True,
        ), patches[4], patches[5], patches[6], patches[7], patches[8], (
            patches[9]
        ):
            unsafe_configuration = app.get_live_canary_blockers(
                "marcell",
                1.0,
            )

        self.assertIn("LIVE_CANARY_BUY_LIMIT_REACHED", exhausted)
        self.assertIn("LIVE_CANARY_NOTIONAL_LIMIT_REACHED", exhausted)
        self.assertIn("LIVE_CANARY_MAX_BUY_USD_INVALID", unsafe_configuration)

    def test_canary_requires_a_working_exit_path_before_buying(self):
        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patch.object(app, "LIVE_SELLS_ENABLED", False), (
            patches[8]
        ), patches[9]:
            blockers = app.get_live_canary_blockers("marcell", 1.0)

        self.assertIn("LIVE_SELLS_REQUIRED_FOR_BUYS", blockers)

    def test_canary_fails_closed_when_health_or_exposure_is_unknown(self):
        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patch.object(
            app,
            "get_live_exit_feed_readiness",
            side_effect=RuntimeError("health unavailable"),
        ), patches[9]:
            unknown_health = app.get_live_canary_blockers("marcell", 1.0)

        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patch.object(
            app,
            "get_daily_live_buy_exposure",
            side_effect=RuntimeError("database unavailable"),
        ):
            unknown_exposure = app.get_live_canary_blockers("marcell", 1.0)

        self.assertIn("LIVE_EXIT_FEED_STATUS_UNKNOWN", unknown_health)
        self.assertIn("LIVE_CANARY_EXPOSURE_UNKNOWN", unknown_exposure)

    def test_canary_blocks_when_a_live_order_has_no_signature(self):
        patches = self.canary_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], (
            patches[5]
        ), patches[6], patches[7], patches[8], patch.object(
            app,
            "get_daily_live_buy_exposure",
            return_value={
                "attempts": 0,
                "notional_usd": 0.0,
                "unresolved_without_signature": 1,
            },
        ):
            blockers = app.get_live_canary_blockers("marcell", 1.0)

        self.assertIn("LIVE_AMBIGUOUS_ORDER_PENDING", blockers)

    def test_live_exposure_counts_ambiguous_orders_across_days(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "ambiguous-live-order.db",
        ):
            app.migrate_database()
            order = app.create_execution_order_idempotent(
                mint="mint-ambiguous",
                side="buy",
                amount_usd=1.0,
                expected_price=1.0,
                execution_price=1.0,
                liquidity_sol=20.0,
                idempotency_key="ambiguous-order",
                source="pumpportal_lightning",
                mode="live",
            )
            conn = app.db()
            conn.execute(
                "UPDATE execution_orders SET status = 'PENDING_RECONCILIATION', "
                "ts_created = 1 WHERE id = ?",
                (order["order_id"],),
            )
            conn.commit()
            conn.close()

            exposure = app.get_daily_live_buy_exposure(now=time.time())
            blocked = app.create_execution_order_idempotent(
                mint="mint-second",
                side="buy",
                amount_usd=1.0,
                expected_price=1.0,
                execution_price=1.0,
                liquidity_sol=20.0,
                idempotency_key="second-order",
                source="pumpportal_lightning",
                mode="live",
                canary_limits={
                    "start_of_day_ts": app.get_local_day_start_ts(),
                    "max_buys": 1,
                    "max_notional_usd": 1.0,
                },
            )

        self.assertEqual(exposure["attempts"], 0)
        self.assertEqual(exposure["unresolved_without_signature"], 1)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["reason"], "LIVE_AMBIGUOUS_ORDER_PENDING")

    def test_canary_limits_never_block_an_exit(self):
        with patch.object(app, "LIVE_CANARY_ENABLED", False, create=True), (
            patch.object(app, "get_live_canary_blockers")
        ) as canary:
            blockers = app.get_live_execution_readiness("sell")["blockers"]

        canary.assert_not_called()
        self.assertNotIn("LIVE_CANARY_DISABLED", blockers)

    def test_exit_feed_requires_fresh_confirmed_token_sync(self):
        healthy = {
            "enabled": True,
            "apply": True,
            "configured": True,
            "initialized": True,
            "last_error": None,
            "pending_tokens": 0,
            "planned_additions": 0,
            "last_success_ts": 900.0,
        }
        with patch.object(
            app,
            "MARKET_EVENT_INBOX_CONSUMER_ENABLED",
            True,
        ), patch.object(
            app,
            "HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS",
            60,
        ), patch.object(
            app,
            "get_helius_webhook_sync_status",
            return_value=healthy,
        ):
            ready = app.get_live_exit_feed_readiness(now=1000.0)
            stale_status = {**healthy, "last_success_ts": 100.0}
            with patch.object(
                app,
                "get_helius_webhook_sync_status",
                return_value=stale_status,
            ):
                stale = app.get_live_exit_feed_readiness(now=1000.0)

        self.assertTrue(ready["ready"])
        self.assertFalse(stale["ready"])
        self.assertIn("HELIUS_TOKEN_SYNC_STALE", stale["blockers"])


class ExecutionAdapterTests(unittest.TestCase):
    def execution_args(self):
        return {
            "mint": "mint-a",
            "expected_price": 1.0,
            "execution_price": 1.0,
            "liquidity_sol": 20.0,
            "amount_usd": 5.0,
        }

    def test_dispatches_paper_orders_to_simulation(self):
        with patch.object(
            app,
            "simulate_execution",
            return_value={"ok": True, "order_id": 1},
        ) as simulate:
            result = app.execute_order(**self.execution_args())

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "simulation")
        simulate.assert_called_once()

    def test_live_canary_capacity_is_reserved_atomically(self):
        results = []
        errors = []

        def create(key):
            try:
                results.append(app.create_execution_order_idempotent(
                    mint=f"mint-{key}",
                    side="buy",
                    amount_usd=1.0,
                    expected_price=1.0,
                    execution_price=None,
                    liquidity_sol=20.0,
                    idempotency_key=key,
                    source="pumpportal_lightning",
                    mode="live",
                    canary_limits={
                        "start_of_day_ts": 0.0,
                        "max_buys": 1,
                        "max_notional_usd": 1.0,
                    },
                ))
            except Exception as exc:
                errors.append(exc)

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "canary.db",
        ):
            app.migrate_database()
            threads = [
                threading.Thread(target=create, args=(f"canary-{index}",))
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(sum(result["created"] for result in results), 1)
        blocked = [result for result in results if result.get("blocked")]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["reason"], "LIVE_CANARY_BUY_LIMIT_REACHED")

    def test_rejects_live_and_unknown_providers(self):
        with patch.object(app, "simulate_execution") as simulate:
            live = app.execute_order(
                **self.execution_args(),
                mode="live",
            )
            unknown = app.execute_order(
                **self.execution_args(),
                provider="pumpportal",
            )

        self.assertEqual(
            live["reason"],
            "LIVE_EXECUTION_NOT_IMPLEMENTED",
        )
        self.assertEqual(
            unknown["reason"],
            "EXECUTION_PROVIDER_NOT_AVAILABLE",
        )
        simulate.assert_not_called()

    def test_builds_safe_pumpportal_lightning_buy_payload(self):
        payload = app.build_pumpportal_lightning_buy_payload(
            mint="So11111111111111111111111111111111111111112",
            amount_usd=5.0,
            sol_usd_price=200.0,
            quote_ts=1000.0,
            now_ts=1010.0,
        )

        self.assertEqual(payload["amount"], 0.025)
        self.assertEqual(payload["denominatedInSol"], "true")
        self.assertEqual(payload["skipPreflight"], "false")
        self.assertNotIn("api_key", payload)

    def test_builds_safe_pumpportal_percentage_sell_payload(self):
        payload = app.build_pumpportal_lightning_sell_payload(
            mint="So11111111111111111111111111111111111111112",
            wallet_percentage=100 / 3,
        )

        self.assertEqual(payload["action"], "sell")
        self.assertEqual(payload["amount"], "33.333333333%")
        self.assertEqual(payload["denominatedInSol"], "false")
        self.assertEqual(payload["skipPreflight"], "false")

    def test_rejects_unsafe_sell_percentages(self):
        for percentage in (None, "invalid", 0, -1, 101, math.nan, math.inf):
            with self.subTest(percentage=percentage), self.assertRaises(
                ValueError
            ):
                app.build_pumpportal_lightning_sell_payload(
                    mint="So11111111111111111111111111111111111111112",
                    wallet_percentage=percentage,
                )

    def test_converts_original_position_fraction_to_wallet_percentage(self):
        cases = (
            (0.25, 1.0, 25.0),
            (0.25, 0.75, 100 / 3),
            (0.25, 0.5, 50.0),
            (0.25, 0.25, 100.0),
        )

        for sell_fraction, remaining_fraction, expected in cases:
            with self.subTest(remaining_fraction=remaining_fraction):
                actual = app.calculate_wallet_sell_percentage(
                    sell_fraction,
                    remaining_fraction,
                )
            self.assertAlmostEqual(actual, expected)

    def test_prepares_partial_sell_without_submitting(self):
        prepared = app.prepare_pumpportal_lightning_sell(
            mint="So11111111111111111111111111111111111111112",
            sell_fraction=0.25,
            remaining_fraction=0.75,
        )

        self.assertFalse(prepared["submitted"])
        self.assertAlmostEqual(prepared["wallet_percentage"], 100 / 3)
        self.assertEqual(prepared["payload"]["amount"], "33.333333333%")

    def test_rejects_invalid_position_sell_fractions(self):
        for sell_fraction, remaining_fraction in (
            (0, 1),
            (-0.25, 1),
            (0.5, 0.25),
            (0.25, 0),
            (0.25, 1.1),
            (math.nan, 1),
        ):
            with self.subTest(
                sell_fraction=sell_fraction,
                remaining_fraction=remaining_fraction,
            ), self.assertRaises(ValueError):
                app.calculate_wallet_sell_percentage(
                    sell_fraction,
                    remaining_fraction,
                )

    def test_rejects_stale_price_and_unsafe_trade_values(self):
        valid = {
            "mint": "So11111111111111111111111111111111111111112",
            "amount_usd": 5.0,
            "sol_usd_price": 200.0,
            "quote_ts": 1000.0,
            "now_ts": 1010.0,
        }

        invalid_cases = (
            ({"now_ts": 1031.0}, "STALE_SOL_USD_PRICE"),
            ({"amount_usd": 6.0}, "INVALID_AMOUNT_USD"),
            ({"slippage_pct": 6.0}, "INVALID_SLIPPAGE"),
            ({"priority_fee_sol": 0.01}, "INVALID_PRIORITY_FEE"),
            ({"pool": "unknown"}, "INVALID_PUMPPORTAL_POOL"),
        )

        for changes, expected_error in invalid_cases:
            with self.subTest(expected_error=expected_error):
                arguments = {**valid, **changes}
                with self.assertRaisesRegex(ValueError, expected_error):
                    app.build_pumpportal_lightning_buy_payload(
                        **arguments
                    )

    def test_fetches_and_validates_sol_usd_quote(self):
        response = MagicMock()
        response.read.return_value = (
            b'{"data":{"amount":"200.50","currency":"USD"}}'
        )
        response.__enter__.return_value = response

        with patch.object(app, "urlopen", return_value=response), patch.object(
            app.time,
            "time",
            return_value=1234.0,
        ):
            quote = app.fetch_sol_usd_quote()

        self.assertEqual(quote["price"], 200.5)
        self.assertEqual(quote["quoted_ts"], 1234.0)
        self.assertEqual(quote["source"], "coinbase_spot")

    def test_prepares_without_submitting_pumpportal_buy(self):
        quote = {
            "price": 200.0,
            "quoted_ts": 1000.0,
            "source": "coinbase_spot",
        }
        with patch.object(
            app,
            "fetch_sol_usd_quote",
            return_value=quote,
        ), patch.object(app.time, "time", return_value=1010.0):
            prepared = app.prepare_pumpportal_lightning_buy(
                mint="So11111111111111111111111111111111111111112",
                amount_usd=5.0,
            )

        self.assertFalse(prepared["submitted"])
        self.assertEqual(prepared["payload"]["amount"], 0.025)

    def test_live_submission_stays_blocked_by_default(self):
        with patch.object(app, "urlopen") as urlopen:
            result = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"],
            "LIVE_TRADING_DISABLED",
        )
        urlopen.assert_not_called()

    def test_live_submission_requires_buy_or_sell_side(self):
        with patch.object(app, "urlopen") as urlopen:
            result = app.submit_pumpportal_lightning_trade(
                {"action": "hold"},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "INVALID_EXECUTION_SIDE")
        urlopen.assert_not_called()

    def test_live_submission_parses_simulated_pumpportal_response(self):
        signature = "1" * 88
        response = MagicMock()
        response.read.return_value = json.dumps({
            "signature": signature,
        }).encode("utf-8")
        response.__enter__.return_value = response

        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(
            app,
            "urlopen",
            return_value=response,
        ) as urlopen:
            result = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
                api_key="test-key",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["signature"], signature)
        self.assertIn("api-key=test-key", urlopen.call_args.args[0].full_url)

    def test_rejects_malformed_pumpportal_signature(self):
        response = MagicMock()
        response.read.return_value = b'{"signature":"not-a-signature"}'

        response.__enter__.return_value = response
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(app, "urlopen", return_value=response):
            result = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
                api_key="test-key",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "INVALID_PUMPPORTAL_RESPONSE")

    def test_response_without_signature_is_ambiguous_unless_explicitly_rejected(self):
        def response(payload):
            result = MagicMock()
            result.read.return_value = json.dumps(payload).encode("utf-8")
            result.__enter__.return_value = result
            return result

        common_patches = (
            patch.object(app, "LIVE_TRADING", True),
            patch.object(app, "LIVE_EXECUTION_IMPLEMENTED", True),
            patch.object(app, "LIVE_BUYS_ENABLED", True),
        )
        with common_patches[0], common_patches[1], common_patches[2], patch.object(
            app,
            "urlopen",
            return_value=response({}),
        ):
            ambiguous = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
                api_key="test-key",
            )

        common_patches = (
            patch.object(app, "LIVE_TRADING", True),
            patch.object(app, "LIVE_EXECUTION_IMPLEMENTED", True),
            patch.object(app, "LIVE_BUYS_ENABLED", True),
        )
        with common_patches[0], common_patches[1], common_patches[2], patch.object(
            app,
            "urlopen",
            return_value=response({"errors": ["rejected"]}),
        ):
            rejected = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
                api_key="test-key",
            )

        self.assertFalse(ambiguous["ok"])
        self.assertEqual(
            ambiguous["reason"],
            "PUMPPORTAL_RESPONSE_WITHOUT_SIGNATURE",
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["reason"], "PUMPPORTAL_REJECTED")

    def test_reads_solana_signature_confirmation_without_sending(self):
        signature = "1" * 88
        response = MagicMock()
        response.read.return_value = (
            b'{"result":{"value":[{"slot":123,"err":null,'
            b'"confirmationStatus":"finalized"}]}}'
        )
        response.__enter__.return_value = response

        with patch.object(app, "urlopen", return_value=response) as urlopen:
            result = app.fetch_solana_signature_status(signature)

        self.assertTrue(result["confirmed"])
        self.assertTrue(result["finalized"])
        self.assertFalse(result["failed"])
        request = urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body["method"], "getSignatureStatuses")
        self.assertTrue(request_body["params"][1]["searchTransactionHistory"])

    def test_classifies_pending_confirmed_and_failed_signatures(self):
        cases = (
            (None, (False, False, False)),
            (
                {"slot": 1, "err": None, "confirmationStatus": "processed"},
                (True, False, False),
            ),
            (
                {"slot": 2, "err": None, "confirmationStatus": "confirmed"},
                (True, True, False),
            ),
            (
                {"slot": 3, "err": {"InstructionError": [0, "Failed"]},
                 "confirmationStatus": "confirmed"},
                (True, False, True),
            ),
        )

        for status, expected in cases:
            response = MagicMock()
            response.read.return_value = json.dumps({
                "result": {"value": [status]},
            }).encode("utf-8")
            response.__enter__.return_value = response
            with self.subTest(status=status), patch.object(
                app,
                "urlopen",
                return_value=response,
            ):
                result = app.fetch_solana_signature_status("1" * 88)

            self.assertEqual(
                (result["found"], result["confirmed"], result["failed"]),
                expected,
            )

    def test_live_buy_is_idempotent_and_waits_for_finalization(self):
        signature = "1" * 88
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "live-order.db",
        ), patch.object(app, "PUMPPORTAL_TRADING_WALLET_ADDRESS", "wallet-a"), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_BUYS_PER_DAY",
            3,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_DAILY_NOTIONAL_USD",
            3.0,
        ), patch.object(
            app,
            "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app,
            "prepare_pumpportal_lightning_buy",
            return_value={"payload": {"action": "buy"}},
        ), patch.object(
            app,
            "submit_pumpportal_lightning_trade",
            return_value={
                "ok": True,
                "reason": "PUMPPORTAL_SUBMITTED",
                "signature": signature,
            },
        ) as submit:
            app.migrate_database()
            arguments = {
                **self.execution_args(),
                "amount_usd": 1.0,
                "idempotency_key": "signal-123",
                "market_cap_sol": 100.0,
                "origin_trader": "marcell",
            }
            first = app.execute_pumpportal_lightning_buy(**arguments)
            second = app.execute_pumpportal_lightning_buy(**arguments)
            third = app.execute_pumpportal_lightning_buy(
                **{
                    **arguments,
                    "idempotency_key": "signal-456",
                }
            )

            self.assertEqual(first["status"], "PENDING_RECONCILIATION")
            self.assertEqual(second["reason"], "IDEMPOTENT_REUSE")
            self.assertEqual(third["reason"], "LIVE_EXECUTION_IN_PROGRESS")
            submit.assert_called_once()

            with patch.object(
                app,
                "fetch_solana_signature_status",
                return_value={"failed": False, "finalized": True},
            ), patch.object(app, "fetch_finalized_solana_transaction", return_value=None):
                reconciled = (
                    app.reconcile_pending_pumpportal_execution_orders()[0]
                )

            self.assertFalse(reconciled["ok"])
            self.assertEqual(reconciled["reason"], "SOLANA_RECEIPT_NOT_AVAILABLE")
            self.assertEqual(
                app.get_execution_order_status(first["order_id"])["status"],
                "PENDING_RECONCILIATION",
            )

    def test_live_buy_requires_exit_context_before_creating_order(self):
        with patch.object(app, "LIVE_TRADING", True), patch.object(
            app, "LIVE_EXECUTION_IMPLEMENTED", True,
        ), patch.object(
            app, "LIVE_BUYS_ENABLED", True,
        ), patch.object(
            app, "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ):
            missing_market_cap = app.execute_pumpportal_lightning_buy(
                **self.execution_args(), idempotency_key="missing-market-cap",
            )
            missing_trader = app.execute_pumpportal_lightning_buy(
                **self.execution_args(), idempotency_key="missing-trader",
                market_cap_sol=100.0,
            )
        self.assertEqual(missing_market_cap["reason"], "ENTRY_MARKET_CAP_REQUIRED")
        self.assertEqual(missing_trader["reason"], "ORIGIN_TRADER_REQUIRED")

    def test_uncertain_live_submission_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "uncertain-order.db",
        ), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
        ), patch.object(
            app,
            "LIVE_BUYS_ENABLED",
            True,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_BUYS_PER_DAY",
            1,
        ), patch.object(
            app,
            "LIVE_CANARY_MAX_DAILY_NOTIONAL_USD",
            1.0,
        ), patch.object(
            app,
            "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app,
            "prepare_pumpportal_lightning_buy",
            return_value={"payload": {"action": "buy"}},
        ), patch.object(
            app,
            "submit_pumpportal_lightning_trade",
            return_value={"ok": False, "reason": "PUMPPORTAL_REQUEST_FAILED"},
        ):
            app.migrate_database()
            result = app.execute_pumpportal_lightning_buy(
                **{**self.execution_args(), "amount_usd": 1.0},
                idempotency_key="signal-unknown",
                market_cap_sol=100.0,
                origin_trader="marcell",
            )

        self.assertEqual(result["status"], "PENDING_RECONCILIATION")


class AmbiguousExecutionOrderAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            app,
            "DB",
            Path(self.temp_dir.name) / "ambiguous-alert.db",
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        app.migrate_database()

    def make_live_order(self, status, ts_updated=None):
        order_id = app.create_execution_order(
            mint="mint-ambiguous-alert",
            side="buy",
            amount_usd=1.0,
            expected_price=1.0,
            execution_price=1.0,
            liquidity_sol=20.0,
            source="pumpportal_lightning",
            mode="live",
        )
        conn = app.db()
        try:
            conn.execute(
                "UPDATE execution_orders SET status = ?, reason = ?, "
                "ts_updated = ?, external_signature = NULL WHERE id = ?",
                (
                    status,
                    "PUMPPORTAL_REQUEST_FAILED",
                    float(ts_updated if ts_updated is not None else time.time()),
                    order_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return order_id

    async def test_ambiguous_order_alert_is_persistent_and_sent_once(self):
        order_id = self.make_live_order("PENDING_RECONCILIATION")

        with patch.object(
            app,
            "DISCORD_ALERT_WEBHOOK_URL",
            "https://example.invalid/hook",
        ), patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(return_value=True),
        ) as send:
            first = await app.maybe_send_ambiguous_execution_order_alerts()
            second = await app.maybe_send_ambiguous_execution_order_alerts()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        send.assert_awaited_once()
        message = send.await_args.args[0]
        self.assertIn(f"Order: {order_id}", message)
        self.assertIn("New live buys are blocked automatically", message)

        conn = app.db()
        try:
            state = conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (f"LIVE_AMBIGUOUS_ORDER_ALERT:{order_id}",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(state)

    async def test_fresh_sent_order_waits_for_timeout_before_alerting(self):
        order_id = self.make_live_order("SENT", ts_updated=995.0)

        with patch.object(
            app,
            "DISCORD_ALERT_WEBHOOK_URL",
            "https://example.invalid/hook",
        ), patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(return_value=True),
        ) as send:
            before_timeout = await app.maybe_send_ambiguous_execution_order_alerts(
                now=1000.0
            )
            after_timeout = await app.maybe_send_ambiguous_execution_order_alerts(
                now=1006.0
            )

        self.assertEqual(before_timeout, 0)
        self.assertEqual(after_timeout, 1)
        self.assertIn(f"Order: {order_id}", send.await_args.args[0])

    async def test_failed_discord_delivery_is_retried(self):
        self.make_live_order("PENDING_RECONCILIATION")

        with patch.object(
            app,
            "DISCORD_ALERT_WEBHOOK_URL",
            "https://example.invalid/hook",
        ), patch.object(
            app,
            "send_discord_alert",
            new=AsyncMock(side_effect=[False, True]),
        ) as send:
            first = await app.maybe_send_ambiguous_execution_order_alerts()
            second = await app.maybe_send_ambiguous_execution_order_alerts()

        self.assertEqual(first, 0)
        self.assertEqual(second, 1)
        self.assertEqual(send.await_count, 2)


class PositionConcurrencyTests(unittest.TestCase):
    def test_only_one_simultaneous_position_is_opened_per_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "risk-test.db",
        ), patch.object(app, "KILL_SWITCH", False), patch.object(
            app,
            "risk_check",
            return_value={"ok": True, "reason": "RISK_OK"},
        ):
            app.migrate_database()
            barrier = threading.Barrier(2)
            errors = []

            def open_position(mint):
                try:
                    barrier.wait()
                    app.open_paper_position(
                        mint=mint,
                        trader="test-trader",
                        market_cap=100.0,
                        score=80,
                        decision="COPY",
                        mode="race-test",
                    )
                except Exception as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(
                    target=open_position,
                    args=(f"DEMO-RACE-{index}",),
                )
                for index in range(2)
            ]

            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(
                app.count_open_positions(mode="race-test"),
                1,
            )


class LiveCopyDispatchTests(unittest.TestCase):
    def live_signal(self):
        return {
            "mint": "So11111111111111111111111111111111111111112",
            "vSolInBondingCurve": 20.0,
        }

    def approved_model(self):
        model = MagicMock()
        model.model_version = "approved-model-v1"
        model.data_version = app.DATA_VERSION
        model.artifact_role = "incumbent"
        model.deployment_ready = True
        return model

    def positive_prediction(self):
        return {
            "model_version": "approved-model-v1",
            "data_version": app.DATA_VERSION,
            "probability": 0.8,
            "threshold": 0.6,
            "predicted_target": 1,
        }

    def rated_quality(self, rated=True, samples=40):
        return {
            "trader": "marcell",
            "rated": rated,
            "samples": samples,
            "effective_quality": 22,
        }

    def test_non_live_copy_never_reaches_live_executor(self):
        with patch.object(
            app,
            "get_live_execution_readiness",
        ) as readiness, patch.object(
            app,
            "execute_pumpportal_lightning_buy",
        ) as execute:
            result = app.maybe_execute_live_copy(
                signal_id=123,
                decision="COPY",
                trader="marcell",
                event=self.live_signal(),
                source="demo",
                price_at_signal=0.0001,
                market_cap=100.0,
                model_prediction=None,
            )

        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "NON_LIVE_SOURCE")
        readiness.assert_not_called()
        execute.assert_not_called()

    def test_non_copy_and_observe_only_traders_never_reach_executor(self):
        cases = (
            ("WATCH", "marcell", set(), "DECISION_NOT_COPY"),
            ("SKIP", "marcell", set(), "DECISION_NOT_COPY"),
            ("COPY", "marcell", {"marcell"}, "TRADER_OBSERVE_ONLY"),
        )

        for decision, trader, observe_traders, reason in cases:
            with self.subTest(reason=reason), patch.object(
                app,
                "OBSERVE_TRADERS",
                observe_traders,
            ), patch.object(
                app,
                "get_live_execution_readiness",
            ) as readiness, patch.object(
                app,
                "execute_pumpportal_lightning_buy",
            ) as execute:
                result = app.maybe_execute_live_copy(
                    signal_id=123,
                    decision=decision,
                    trader=trader,
                    event=self.live_signal(),
                    source="live",
                    price_at_signal=0.0001,
                    market_cap=100.0,
                    model_prediction=None,
                )

            self.assertFalse(result["attempted"])
            self.assertEqual(result["reason"], reason)
            readiness.assert_not_called()
            execute.assert_not_called()

    def test_copy_stays_blocked_until_live_buy_is_ready(self):
        blockers = ["LIVE_EXECUTION_NOT_IMPLEMENTED"]
        with patch.object(app, "SHADOW_MODEL", self.approved_model()), patch.object(
            app,
            "get_trader_quality_assessment",
            return_value=self.rated_quality(),
        ), patch.object(
            app,
            "get_live_execution_readiness",
            return_value={"ready": False, "blockers": blockers},
        ), patch.object(
            app,
            "execute_pumpportal_lightning_buy",
        ) as execute:
            result = app.maybe_execute_live_copy(
                signal_id=123,
                decision="COPY",
                trader="marcell",
                event=self.live_signal(),
                source="live",
                price_at_signal=0.0001,
                market_cap=100.0,
                model_prediction=self.positive_prediction(),
            )

        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "LIVE_BUY_NOT_READY")
        self.assertEqual(result["blockers"], blockers)
        execute.assert_not_called()

    def test_ready_copy_dispatches_idempotent_live_buy(self):
        with patch.object(app, "SHADOW_MODEL", self.approved_model()), patch.object(
            app,
            "get_trader_quality_assessment",
            return_value=self.rated_quality(),
        ), patch.object(
            app,
            "LIVE_BUY_USD",
            2.0,
            create=True,
        ), patch.object(
            app,
            "get_live_execution_readiness",
            return_value={"ready": True, "blockers": []},
        ), patch.object(
            app,
            "execute_pumpportal_lightning_buy",
            return_value={
                "ok": True,
                "status": "PENDING_RECONCILIATION",
                "order_id": 7,
            },
        ) as execute:
            result = app.maybe_execute_live_copy(
                signal_id=123,
                decision="COPY",
                trader="marcell",
                event=self.live_signal(),
                source="live",
                price_at_signal=0.0001,
                market_cap=100.0,
                model_prediction=self.positive_prediction(),
            )

        self.assertTrue(result["attempted"])
        self.assertEqual(result["order_id"], 7)
        execute.assert_called_once_with(
            mint="So11111111111111111111111111111111111111112",
            expected_price=0.0001,
            execution_price=None,
            liquidity_sol=20.0,
            amount_usd=2.0,
            idempotency_key="copy-evaluation-123",
            market_cap_sol=100.0,
            origin_trader="marcell",
        )

    def test_unrated_trader_never_reaches_live_executor(self):
        # Un trader sin evidencia suficiente puntúa cerca del neutral, pero
        # eso es un prior, no una medición: en real no se lo copia.
        with patch.object(
            app,
            "SHADOW_MODEL",
            self.approved_model(),
        ), patch.object(
            app,
            "get_trader_quality_assessment",
            return_value=self.rated_quality(rated=False, samples=3),
        ), patch.object(
            app,
            "get_live_execution_readiness",
        ) as readiness, patch.object(
            app,
            "execute_pumpportal_lightning_buy",
        ) as execute:
            result = app.maybe_execute_live_copy(
                signal_id=123,
                decision="COPY",
                trader="marcell",
                event=self.live_signal(),
                source="live",
                price_at_signal=0.0001,
                market_cap=100.0,
                model_prediction=self.positive_prediction(),
            )

        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "LIVE_TRADER_NOT_RATED")
        self.assertEqual(result["trader_samples"], 3)
        readiness.assert_not_called()
        execute.assert_not_called()

    def test_unrated_trader_still_opens_paper_positions(self):
        # El aprendizaje en papel debe seguir para poder acumular evidencia
        # sobre traders todavía sin calificar.
        event = {
            "mint": "DEMO-UNRATED-PAPER",
            "signature": "unrated-paper-signature",
            "solAmount": 1.0,
            "marketCapSol": 100.0,
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "unrated.db",
        ), patch.object(app, "score_trader", return_value=30), patch.object(
            app, "score_timing", return_value=20,
        ), patch.object(app, "score_trade_size", return_value=15), patch.object(
            app, "score_token_structure", return_value=15,
        ), patch.object(app, "score_consensus", return_value=10), patch.object(
            app, "score_market_context", return_value=10,
        ), patch.object(app, "OBSERVE_TRADERS", set()), patch.object(
            app, "open_paper_position",
        ) as open_position:
            app.migrate_database()

            result = app.evaluate_buy("unmeasured-trader", event, "live")

        self.assertEqual(result["decision"], "COPY")
        open_position.assert_called_once()
        self.assertFalse(result["live_execution"]["attempted"])

    def test_ready_copy_requires_valid_price_and_liquidity(self):
        cases = (
            (0.0, 20.0, "LIVE_SIGNAL_PRICE_INVALID"),
            (0.0001, 0.0, "LIVE_SIGNAL_LIQUIDITY_INVALID"),
        )

        for price, liquidity, reason in cases:
            event = self.live_signal()
            event["vSolInBondingCurve"] = liquidity
            with self.subTest(reason=reason), patch.object(
                app,
                "SHADOW_MODEL",
                self.approved_model(),
            ), patch.object(
                app,
                "get_trader_quality_assessment",
                return_value=self.rated_quality(),
            ), patch.object(
                app,
                "get_live_execution_readiness",
                return_value={"ready": True, "blockers": []},
            ), patch.object(
                app,
                "execute_pumpportal_lightning_buy",
            ) as execute:
                result = app.maybe_execute_live_copy(
                    signal_id=123,
                    decision="COPY",
                    trader="marcell",
                    event=event,
                    source="live",
                    price_at_signal=price,
                    market_cap=100.0,
                    model_prediction=self.positive_prediction(),
                )

            self.assertFalse(result["attempted"])
            self.assertEqual(result["reason"], reason)
            execute.assert_not_called()

    def test_model_must_positively_approve_live_copy(self):
        approved_model = self.approved_model()
        unapproved_model = self.approved_model()
        unapproved_model.deployment_ready = False
        negative = self.positive_prediction()
        negative.update({"probability": 0.2, "predicted_target": 0})
        wrong_version = self.positive_prediction()
        wrong_version["model_version"] = "other-model"
        cases = (
            (None, self.positive_prediction(), "LIVE_MODEL_NOT_LOADED"),
            (
                unapproved_model,
                self.positive_prediction(),
                "LIVE_MODEL_NOT_APPROVED",
            ),
            (approved_model, None, "LIVE_MODEL_PREDICTION_MISSING"),
            (approved_model, negative, "LIVE_MODEL_REJECTED"),
            (
                approved_model,
                wrong_version,
                "LIVE_MODEL_VERSION_MISMATCH",
            ),
        )

        for model, prediction, reason in cases:
            with self.subTest(reason=reason), patch.object(
                app,
                "SHADOW_MODEL",
                model,
            ), patch.object(
                app,
                "get_live_execution_readiness",
            ) as readiness, patch.object(
                app,
                "execute_pumpportal_lightning_buy",
            ) as execute:
                result = app.maybe_execute_live_copy(
                    signal_id=123,
                    decision="COPY",
                    trader="marcell",
                    event=self.live_signal(),
                    source="live",
                    price_at_signal=0.0001,
                    market_cap=100.0,
                    model_prediction=prediction,
                )

            self.assertFalse(result["attempted"])
            self.assertEqual(result["reason"], reason)
            readiness.assert_not_called()
            execute.assert_not_called()


class EvaluationIdentityMigrationTests(unittest.TestCase):
    def test_legacy_evaluations_keep_ids_and_gain_composite_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "evaluation-migration.db",
        ):
            conn = app.db()
            conn.execute("DROP TABLE evaluations")
            conn.execute(
                """
                CREATE TABLE evaluations(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_signature TEXT UNIQUE,
                    ts REAL,
                    trader TEXT,
                    mint TEXT,
                    source TEXT,
                    score INTEGER,
                    decision TEXT,
                    trader_score INTEGER,
                    timing_score INTEGER,
                    size_score INTEGER,
                    token_score INTEGER,
                    consensus_score INTEGER,
                    market_score INTEGER,
                    reasons TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO evaluations(
                    id, trade_signature, ts, trader, mint, decision
                )
                VALUES(42, 'legacy-signature', 1, 'trader', 'mint', 'WATCH')
                """
            )
            conn.commit()
            conn.close()

            app.migrate_database()
            app.migrate_database()

            conn = app.db()
            try:
                legacy = conn.execute(
                    "SELECT id, trade_signature, event_index "
                    "FROM evaluations WHERE id = 42"
                ).fetchone()
                conn.execute(
                    "INSERT INTO evaluations(trade_signature, event_index) "
                    "VALUES('legacy-signature', 1)"
                )
                conn.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO evaluations(trade_signature, event_index) "
                        "VALUES('legacy-signature', 0)"
                    )
                conn.rollback()
                indexes = conn.execute(
                    "SELECT event_index FROM evaluations "
                    "WHERE trade_signature = 'legacy-signature' "
                    "ORDER BY event_index"
                ).fetchall()
            finally:
                conn.close()

        self.assertEqual(legacy, (42, "legacy-signature", 0))
        self.assertEqual(indexes, [(0,), (1,)])


class EvaluationIdempotencyTests(unittest.TestCase):
    def test_full_event_identity_keeps_distinct_operations_and_deduplicates_retries(self):
        event = {
            "mint": "DEMO-DUPLICATE-EVALUATION",
            "signature": "duplicate-evaluation-signature",
            "eventIndex": 0,
            "solAmount": 1.0,
            "marketCapSol": 100.0,
        }
        second_operation = {**event, "eventIndex": 1}
        model_prediction = {
            "model_version": "approved-model-v1",
            "data_version": app.DATA_VERSION,
            "probability": 0.8,
            "threshold": 0.6,
            "predicted_target": 1,
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app,
            "DB",
            Path(temp_dir) / "evaluation-test.db",
        ), patch.object(app, "score_trader", return_value=30), patch.object(
            app,
            "score_timing",
            return_value=20,
        ), patch.object(app, "score_trade_size", return_value=15), patch.object(
            app,
            "score_token_structure",
            return_value=15,
        ), patch.object(app, "score_consensus", return_value=10), patch.object(
            app,
            "score_market_context",
            return_value=10,
        ), patch.object(app, "OBSERVE_TRADERS", set()), patch.object(
            app,
            "observe_shadow_signal",
            return_value=model_prediction,
        ), patch.object(
            app,
            "maybe_execute_live_copy",
            return_value={"attempted": False, "reason": "LIVE_BUY_NOT_READY"},
            create=True,
        ) as live_copy, patch.object(
            app,
            "open_paper_position",
        ) as open_position:
            app.migrate_database()

            first = app.evaluate_buy(
                "test-trader",
                event,
                price_at_signal=0.0001,
            )
            second = app.evaluate_buy(
                "test-trader",
                event,
                price_at_signal=0.0001,
            )
            third = app.evaluate_buy(
                "test-trader",
                second_operation,
                price_at_signal=0.0001,
            )

            conn = app.db()
            evaluation_indexes = conn.execute(
                "SELECT event_index FROM evaluations "
                "WHERE trade_signature = ? ORDER BY event_index",
                (event["signature"],),
            ).fetchall()
            conn.close()

        self.assertEqual(first["decision"], "COPY")
        self.assertEqual(second["decision"], "COPY")
        self.assertEqual(third["decision"], "COPY")
        self.assertEqual(evaluation_indexes, [(0,), (1,)])
        self.assertEqual(open_position.call_count, 2)
        self.assertEqual(live_copy.call_count, 2)
        self.assertEqual(
            live_copy.call_args.kwargs["model_prediction"],
            model_prediction,
        )


if __name__ == "__main__":
    unittest.main()
