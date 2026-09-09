import math
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
