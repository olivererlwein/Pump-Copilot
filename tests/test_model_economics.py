import unittest

import numpy as np

from scripts.compare_model_economics import (
    PUMPPORTAL_LIGHTNING_ROUND_TRIP_COST,
    simulate_payoff,
)


class PayoffSimulationTests(unittest.TestCase):
    def test_reports_conservative_fixed_payoff_after_costs(self):
        rows = [
            {"signal_ts": 0.0, "target": 1},
            {"signal_ts": 1.0, "target": 0},
            {"signal_ts": 2.0, "target": 1},
        ]

        report = simulate_payoff(
            rows,
            np.asarray([1, 1, 0]),
            target_column="target",
            round_trip_cost=0.02,
        )

        self.assertEqual(report["selected"], 2)
        self.assertEqual(report["wins"], 1)
        self.assertEqual(report["losses"], 1)
        self.assertEqual(report["precision"], 0.5)
        self.assertAlmostEqual(report["gross_return_units"], 0.15)
        self.assertAlmostEqual(report["net_return_units"], 0.11)

    def test_skips_signals_while_a_position_is_assumed_open(self):
        rows = [
            {"signal_ts": 0.0, "target": 1},
            {"signal_ts": 100.0, "target": 0},
            {"signal_ts": 1000.0, "target": 1},
        ]

        report = simulate_payoff(
            rows,
            np.asarray([1, 1, 1]),
            target_column="target",
            hold_seconds=900,
        )

        self.assertEqual(report["selected"], 3)
        self.assertEqual(report["executed"], 2)
        self.assertEqual(report["skipped_busy"], 1)
        self.assertEqual(report["wins"], 2)
        self.assertEqual(
            PUMPPORTAL_LIGHTNING_ROUND_TRIP_COST,
            0.02,
        )
        self.assertAlmostEqual(report["net_return_units"], 0.46)


if __name__ == "__main__":
    unittest.main()
