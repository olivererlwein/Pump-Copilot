import math
import unittest
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


if __name__ == "__main__":
    unittest.main()
