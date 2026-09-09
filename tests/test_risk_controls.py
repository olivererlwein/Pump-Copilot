import json
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
        ), patch.object(app, "LIVE_TRADING", False):
            readiness = app.get_live_execution_readiness()

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["blockers"],
            [
                "paired_completed 42/100",
                "LIVE_EXECUTION_NOT_IMPLEMENTED",
                "LIVE_TRADING_DISABLED",
            ],
        )


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
        ), patch.object(app, "urlopen", return_value=response):
            result = app.submit_pumpportal_lightning_trade(
                {"action": "buy"},
                api_key="test-key",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "INVALID_PUMPPORTAL_RESPONSE")

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
        ), patch.object(app, "LIVE_TRADING", True), patch.object(
            app,
            "LIVE_EXECUTION_IMPLEMENTED",
            True,
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
                "idempotency_key": "signal-123",
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
            ):
                reconciled = (
                    app.reconcile_pending_pumpportal_execution_orders()[0]
                )

            self.assertTrue(reconciled["ok"])
            self.assertEqual(reconciled["status"], "CONFIRMED")

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
                **self.execution_args(),
                idempotency_key="signal-unknown",
            )

        self.assertEqual(result["status"], "PENDING_RECONCILIATION")


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
