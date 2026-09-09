import math
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
