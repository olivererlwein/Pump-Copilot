import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TraderQualityCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "trader-quality.db"
        app.migrate_database()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insert_outcomes(
        self,
        trader,
        *,
        samples,
        target_hits,
        status="completed",
    ):
        conn = app.db()
        rows = []

        for index in range(samples):
            signal_ts = 1000.0 + index
            target_won = index < target_hits
            tp25_ts = signal_ts + (1.0 if target_won else 2.0)
            sl10_ts = signal_ts + (2.0 if target_won else 1.0)

            rows.append(
                (
                    index + 1,
                    f"mint-{trader}-{index}",
                    trader,
                    signal_ts,
                    1.0,
                    30.0,
                    -12.0,
                    1,
                    0,
                    1,
                    tp25_ts,
                    sl10_ts,
                    status,
                    signal_ts,
                    signal_ts,
                )
            )

        conn.executemany(
            """
            INSERT INTO signal_outcomes(
                signal_id,
                mint,
                trader,
                signal_ts,
                price_at_signal,
                max_return,
                min_return,
                hit_tp25,
                hit_tp50,
                hit_sl10,
                tp25_ts,
                sl10_ts,
                status,
                created_ts,
                updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
        conn.close()

    def test_every_trader_starts_from_the_same_neutral_quality(self):
        with patch.object(app, "TRADER_DYNAMIC_QUALITY_ENABLED", False):
            self.assertEqual(app.score_trader("marcell"), 15)
            self.assertEqual(app.score_trader("hdegroot"), 15)
            self.assertEqual(app.score_trader("new-trader"), 15)
            self.assertEqual(app.score_trader("ily"), 10)

    def test_below_minimum_samples_is_not_rated(self):
        self.insert_outcomes(
            "marcell",
            samples=29,
            target_hits=29,
        )

        quality = app.get_trader_quality_assessment("marcell")

        # No alcanza para publicar una calificación...
        self.assertFalse(quality["rated"])
        self.assertFalse(quality["ready_for_review"])
        self.assertEqual(quality["base_quality"], 15)

    def test_quality_shrinks_continuously_without_a_cliff(self):
        # El valor no debe saltar al cruzar el mínimo de muestras: el
        # posterior encoge solo hacia el neutral cuando hay poca evidencia.
        self.insert_outcomes("gradual", samples=29, target_hits=29)
        just_below = app.get_trader_quality_assessment("gradual")

        conn = app.db()
        conn.execute(
            """
            INSERT INTO signal_outcomes(
                signal_id, mint, trader, signal_ts, price_at_signal,
                max_return, min_return, hit_tp25, hit_tp50, hit_sl10,
                tp25_ts, sl10_ts, status, created_ts, updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                999, "mint-gradual-extra", "gradual", 2000.0, 1.0,
                30.0, -12.0, 1, 0, 1,
                2001.0, 2002.0, "completed", 2000.0, 2000.0,
            ),
        )
        conn.commit()
        conn.close()

        just_above = app.get_trader_quality_assessment("gradual")

        self.assertFalse(just_below["rated"])
        self.assertTrue(just_above["rated"])
        # Una sola muestra más no puede mover la calidad más de un punto.
        self.assertLessEqual(
            abs(
                just_above["effective_quality"]
                - just_below["effective_quality"]
            ),
            1,
        )

    def test_zero_evidence_lands_exactly_on_the_neutral_prior(self):
        quality = app.get_trader_quality_assessment("brand-new")

        self.assertEqual(quality["samples"], 0)
        self.assertFalse(quality["rated"])
        self.assertEqual(quality["effective_quality"], 15)

    def test_expired_outcomes_with_a_decided_result_count_as_evidence(self):
        # 'expired' significa que perdimos la observación de precio, no que
        # el resultado no exista: si el TP25 alcanzó a disparar, es evidencia.
        self.insert_outcomes(
            "recovered",
            samples=10,
            target_hits=10,
            status="expired",
        )

        stats = app.get_trader_hit_stats("recovered")

        self.assertEqual(stats["samples"], 10)
        self.assertEqual(stats["target_1"], 10)

    def test_expired_without_any_decision_is_not_evidence(self):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO signal_outcomes(
                signal_id, mint, trader, signal_ts, price_at_signal,
                status, created_ts, updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                1, "mint-undecidable", "lost-sight", 1000.0, 1.0,
                "expired", 1000.0, 1000.0,
            ),
        )
        conn.commit()
        conn.close()

        stats = app.get_trader_hit_stats("lost-sight")

        self.assertEqual(stats["samples"], 0)

    def test_first_decidable_signal_per_token_survives_ranking(self):
        # Si la primera señal de un token es indecidible pero una posterior
        # sí se decide, el token no debe perderse entero.
        conn = app.db()
        conn.executemany(
            """
            INSERT INTO signal_outcomes(
                signal_id, mint, trader, signal_ts, price_at_signal,
                max_return, min_return, hit_tp25, hit_tp50, hit_sl10,
                tp25_ts, sl10_ts, status, created_ts, updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    1, "mint-late", "late-decider", 1000.0, 1.0,
                    None, None, 0, 0, 0,
                    None, None, "expired", 1000.0, 1000.0,
                ),
                (
                    2, "mint-late", "late-decider", 1100.0, 1.0,
                    30.0, -12.0, 1, 0, 1,
                    1101.0, 1102.0, "completed", 1100.0, 1100.0,
                ),
            ],
        )
        conn.commit()
        conn.close()

        stats = app.get_trader_hit_stats("late-decider")

        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["target_1"], 1)

    def test_strong_target_history_raises_quality(self):
        self.insert_outcomes(
            "epicsealdarkeye",
            samples=30,
            target_hits=24,
        )

        quality = app.get_trader_quality_assessment("epicsealdarkeye")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["target_1"], 24)
        self.assertEqual(quality["target_0"], 6)
        self.assertEqual(quality["base_quality"], 15)
        self.assertEqual(quality["candidate_quality"], 21)
        self.assertEqual(quality["effective_quality"], 21)

    def test_weak_target_history_lowers_quality(self):
        self.insert_outcomes(
            "hdegroot",
            samples=30,
            target_hits=2,
        )

        quality = app.get_trader_quality_assessment("hdegroot")

        self.assertTrue(quality["ready_for_review"])
        self.assertEqual(quality["candidate_quality"], 10)
        self.assertEqual(quality["effective_quality"], 10)

    def test_tp25_only_counts_when_it_precedes_sl10(self):
        self.insert_outcomes(
            "ordered-results",
            samples=30,
            target_hits=7,
        )

        stats = app.get_trader_hit_stats("ordered-results")

        self.assertEqual(stats["tp25_hits"], 30)
        self.assertEqual(stats["sl10_hits"], 30)
        self.assertEqual(stats["target_1"], 7)
        self.assertEqual(stats["target_0"], 23)

    def test_incomplete_outcomes_do_not_affect_quality(self):
        self.insert_outcomes(
            "unfinished",
            samples=30,
            target_hits=30,
            status="active",
        )

        quality = app.get_trader_quality_assessment("unfinished")

        self.assertEqual(quality["samples"], 0)
        self.assertFalse(quality["ready_for_review"])
        self.assertEqual(quality["effective_quality"], 15)

    def test_repeated_signals_for_one_mint_count_as_one_sample(self):
        self.insert_outcomes(
            "repeater",
            samples=30,
            target_hits=1,
        )

        conn = app.db()
        conn.execute(
            "UPDATE signal_outcomes SET mint = ? WHERE trader = ?",
            ("same-mint", "repeater"),
        )
        conn.commit()
        conn.close()

        stats = app.get_trader_hit_stats("repeater")

        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["target_1"], 1)


class TraderQualityProfileTests(unittest.TestCase):
    """Perfil integral y observacional de calidad del trader."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "trader-profile.db"
        app.migrate_database()
        self.now = 100000.0

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insert_outcome(
        self,
        trader,
        mint,
        *,
        signal_ts,
        tp25_offset=None,
        sl10_offset=None,
        max_return=30.0,
        min_return=-12.0,
        return_5m=5.0,
        status="completed",
        signal_id=None,
    ):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO signal_outcomes(
                signal_id,
                mint,
                trader,
                signal_ts,
                price_at_signal,
                max_return,
                min_return,
                return_5m,
                tp25_ts,
                sl10_ts,
                status,
                created_ts,
                updated_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_id if signal_id is not None else int(signal_ts),
                mint,
                trader,
                signal_ts,
                1.0,
                max_return,
                min_return,
                return_5m,
                (
                    signal_ts + tp25_offset
                    if tp25_offset is not None
                    else None
                ),
                (
                    signal_ts + sl10_offset
                    if sl10_offset is not None
                    else None
                ),
                status,
                signal_ts,
                signal_ts,
            ),
        )
        conn.commit()
        conn.close()

    def insert_trade(
        self,
        trader,
        mint,
        side,
        *,
        ts,
        sol=1.0,
        market_cap_sol=100.0,
        new_token_balance=0.0,
        source="live",
    ):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO trades(
                ts,
                trader,
                wallet,
                side,
                mint,
                sol,
                market_cap_sol,
                signature,
                source,
                token_amount,
                new_token_balance,
                pool
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                trader,
                f"wallet-{trader}",
                side,
                mint,
                sol,
                market_cap_sol,
                f"sig-{trader}-{mint}-{ts}",
                source,
                0.0,
                new_token_balance,
                "pump",
            ),
        )
        conn.commit()
        conn.close()

    def seed_entry_samples(self, trader, *, samples, target_hits):
        for index in range(samples):
            won = index < target_hits
            self.insert_outcome(
                trader,
                f"mint-{trader}-{index}",
                signal_ts=1000.0 + index,
                tp25_offset=1.0 if won else 2.0,
                sl10_offset=2.0 if won else 1.0,
                signal_id=index + 1,
            )

    # -----------------------------------------------------
    # Evidencia insuficiente
    # -----------------------------------------------------

    def test_insufficient_samples_report_unrated_not_zero(self):
        self.seed_entry_samples("scarce", samples=5, target_hits=5)

        profile = app.get_trader_quality_profile("scarce", now=self.now)

        self.assertFalse(profile["rated"])
        self.assertIsNone(profile["score"])
        self.assertEqual(profile["label"], "Sin calificar")
        self.assertFalse(profile["evidence"]["sufficient_evidence"])
        self.assertEqual(profile["evidence"]["confidence"], "insuficiente")

    def test_trader_without_any_data_reports_unavailable_not_zero(self):
        profile = app.get_trader_quality_profile("ghost", now=self.now)

        self.assertFalse(profile["rated"])
        self.assertIsNone(profile["score"])
        self.assertEqual(profile["label"], "Sin calificar")
        self.assertIsNone(profile["entry_quality"]["posterior_rate"])
        self.assertIsNone(profile["returns"]["avg_max_return"])
        self.assertIsNone(profile["returns"]["positive_5m_rate"])
        self.assertIsNone(profile["exit_quality"]["avg_exit_ratio"])
        self.assertIsNone(profile["consistency"]["stability"])
        self.assertIsNone(profile["recency"]["last_signal_age_days"])

    def test_sufficient_evidence_produces_a_score(self):
        self.seed_entry_samples("solid", samples=30, target_hits=21)

        profile = app.get_trader_quality_profile("solid", now=self.now)

        self.assertTrue(profile["rated"])
        self.assertIsNotNone(profile["score"])
        self.assertIsNone(profile["label"])
        self.assertGreater(profile["score"], 0)
        self.assertLessEqual(profile["score"], 100)

    # -----------------------------------------------------
    # Tokens repetidos
    # -----------------------------------------------------

    def test_repeated_signals_on_one_token_are_a_single_sample(self):
        for index in range(12):
            self.insert_outcome(
                "repeater",
                "same-token",
                signal_ts=1000.0 + index,
                tp25_offset=1.0,
                sl10_offset=2.0,
                signal_id=index + 1,
            )

        samples = app.get_trader_entry_samples("repeater")
        profile = app.get_trader_quality_profile("repeater", now=self.now)

        self.assertEqual(len(samples), 1)
        self.assertEqual(profile["entry_quality"]["samples"], 1)
        self.assertFalse(profile["rated"])

    # -----------------------------------------------------
    # Orden TP25 / SL10
    # -----------------------------------------------------

    def test_target_requires_tp25_strictly_before_sl10(self):
        self.insert_outcome(
            "ordered",
            "mint-win",
            signal_ts=1000.0,
            tp25_offset=1.0,
            sl10_offset=5.0,
        )
        self.insert_outcome(
            "ordered",
            "mint-loss",
            signal_ts=2000.0,
            tp25_offset=5.0,
            sl10_offset=1.0,
            signal_id=2,
        )
        self.insert_outcome(
            "ordered",
            "mint-only-tp",
            signal_ts=3000.0,
            tp25_offset=1.0,
            sl10_offset=None,
            signal_id=3,
        )
        self.insert_outcome(
            "ordered",
            "mint-only-sl",
            signal_ts=4000.0,
            tp25_offset=None,
            sl10_offset=1.0,
            signal_id=4,
        )

        entry = app.summarize_trader_entry_quality(
            app.get_trader_entry_samples("ordered")
        )

        self.assertEqual(entry["samples"], 4)
        self.assertEqual(entry["tp25_first"], 2)
        self.assertEqual(entry["sl10_first"], 2)

    # -----------------------------------------------------
    # Ciclos incompletos
    # -----------------------------------------------------

    def test_cycle_starting_mid_life_is_discarded(self):
        # Solo vemos la venta: nunca observamos a qué precio entró.
        self.insert_trade(
            "mid-life",
            "mint-a",
            "sell",
            ts=2000.0,
            market_cap_sol=150.0,
        )

        cycle_data = app.get_trader_exit_cycles("mid-life")

        self.assertEqual(cycle_data["cycles"], [])
        self.assertEqual(cycle_data["skipped_mid_life"], 1)

    def test_entry_without_any_sell_is_not_a_cycle(self):
        self.insert_trade(
            "still-open",
            "mint-a",
            "buy",
            ts=1000.0,
            market_cap_sol=100.0,
        )

        cycle_data = app.get_trader_exit_cycles("still-open")

        self.assertEqual(cycle_data["cycles"], [])
        self.assertEqual(cycle_data["skipped_mid_life"], 0)

    def test_realized_pnl_is_reported_unavailable_never_invented(self):
        self.insert_trade(
            "no-pnl",
            "mint-a",
            "buy",
            ts=1000.0,
            market_cap_sol=100.0,
        )
        self.insert_trade(
            "no-pnl",
            "mint-a",
            "sell",
            ts=1100.0,
            market_cap_sol=150.0,
        )

        exit_quality = app.summarize_trader_exit_quality(
            app.get_trader_exit_cycles("no-pnl")
        )

        self.assertFalse(exit_quality["realized_pnl_available"])
        self.assertEqual(
            exit_quality["realized_pnl_reason"],
            "POSITION_SIZE_NOT_RECONSTRUCTABLE",
        )
        self.assertNotIn("realized_pnl", exit_quality)

    def test_exit_quality_hidden_until_minimum_cycles(self):
        self.insert_trade(
            "few-cycles",
            "mint-a",
            "buy",
            ts=1000.0,
            market_cap_sol=100.0,
        )
        self.insert_trade(
            "few-cycles",
            "mint-a",
            "sell",
            ts=1100.0,
            market_cap_sol=150.0,
        )

        exit_quality = app.summarize_trader_exit_quality(
            app.get_trader_exit_cycles("few-cycles")
        )

        self.assertEqual(exit_quality["cycles"], 1)
        self.assertFalse(exit_quality["available"])
        self.assertIsNone(exit_quality["avg_exit_ratio"])

    # -----------------------------------------------------
    # Ventas parciales
    # -----------------------------------------------------

    def test_partial_sells_collapse_into_one_weighted_result(self):
        self.insert_trade(
            "scaler",
            "mint-a",
            "buy",
            ts=1000.0,
            sol=3.0,
            market_cap_sol=100.0,
            new_token_balance=500.0,
        )
        self.insert_trade(
            "scaler",
            "mint-a",
            "sell",
            ts=1100.0,
            sol=1.0,
            market_cap_sol=200.0,
            new_token_balance=250.0,
        )
        self.insert_trade(
            "scaler",
            "mint-a",
            "sell",
            ts=1200.0,
            sol=3.0,
            market_cap_sol=100.0,
            new_token_balance=0.0,
        )

        cycle_data = app.get_trader_exit_cycles("scaler")
        cycles = cycle_data["cycles"]

        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["sell_events"], 2)
        # Ponderado por SOL: (1*200 + 3*100) / 4 = 125 sobre entrada 100.
        self.assertAlmostEqual(
            cycles[0]["weighted_exit_market_cap_sol"],
            125.0,
        )
        self.assertAlmostEqual(cycles[0]["exit_ratio"], 0.25)
        self.assertTrue(cycles[0]["closure_confirmed"])

    def test_zero_balance_without_prior_evidence_is_not_a_closure(self):
        # new_token_balance == 0 puede ser "vendió todo" o "campo ausente";
        # sin evidencia previa de saldo positivo no se declara cierre.
        self.insert_trade(
            "ambiguous",
            "mint-a",
            "buy",
            ts=1000.0,
            market_cap_sol=100.0,
            new_token_balance=0.0,
        )
        self.insert_trade(
            "ambiguous",
            "mint-a",
            "sell",
            ts=1100.0,
            market_cap_sol=120.0,
            new_token_balance=0.0,
        )

        cycles = app.get_trader_exit_cycles("ambiguous")["cycles"]

        self.assertEqual(len(cycles), 1)
        self.assertFalse(cycles[0]["closure_confirmed"])

    # -----------------------------------------------------
    # Sin puntuaciones manuales por nombre
    # -----------------------------------------------------

    def test_no_manual_per_trader_scores_exist(self):
        self.assertFalse(hasattr(app, "TRADER_QUALITY"))

        source = Path(app.__file__).read_text(encoding="utf-8")

        for name in (
            "marcell",
            "hdegroot",
            "gr3gor14n",
            "epicsealdarkeye",
            "supermandev",
        ):
            self.assertNotRegex(
                source,
                rf'"{name}"\s*:\s*\d+',
                f"{name} tiene una puntuación escrita a mano",
            )

    def test_identical_history_scores_identically_regardless_of_name(self):
        self.seed_entry_samples("trader-one", samples=30, target_hits=20)
        self.seed_entry_samples("trader-two", samples=30, target_hits=20)

        first = app.get_trader_quality_profile("trader-one", now=self.now)
        second = app.get_trader_quality_profile("trader-two", now=self.now)

        self.assertEqual(first["score"], second["score"])
        self.assertEqual(
            first["entry_quality"]["posterior_rate"],
            second["entry_quality"]["posterior_rate"],
        )

    # -----------------------------------------------------
    # El perfil es observacional
    # -----------------------------------------------------

    def test_profile_never_feeds_scoring_or_execution(self):
        self.seed_entry_samples("shadow-only", samples=30, target_hits=30)

        profile = app.get_trader_quality_profile("shadow-only", now=self.now)

        self.assertTrue(profile["observational"])
        self.assertFalse(profile["affects_decisions"])

        with patch.object(
            app,
            "get_trader_quality_profile",
            side_effect=AssertionError("el perfil no debe decidir"),
        ):
            with patch.object(app, "TRADER_DYNAMIC_QUALITY_ENABLED", False):
                self.assertEqual(app.score_trader("shadow-only"), 15)

            app.decision_from_score(85)
            app.decision_from_score(65)
            app.decision_from_score(10)

    def test_concentrated_activity_is_measured(self):
        for index in range(9):
            self.insert_trade(
                "concentrated",
                "mint-a",
                "sell",
                ts=1000.0 + index,
            )

        self.insert_trade("concentrated", "mint-b", "sell", ts=2000.0)

        concentration = app.get_trader_activity_concentration("concentrated")

        self.assertTrue(concentration["available"])
        self.assertEqual(concentration["distinct_tokens"], 2)
        # 0.9^2 + 0.1^2 = 0.82
        self.assertAlmostEqual(concentration["hhi"], 0.82)

    def test_recency_decays_old_evidence(self):
        self.insert_outcome(
            "stale",
            "mint-old",
            signal_ts=self.now - (86400 * 28),
            tp25_offset=1.0,
            sl10_offset=5.0,
        )
        self.insert_outcome(
            "fresh",
            "mint-new",
            signal_ts=self.now - 60.0,
            tp25_offset=1.0,
            sl10_offset=5.0,
            signal_id=2,
        )

        stale = app.summarize_trader_recency(
            app.get_trader_entry_samples("stale"),
            now=self.now,
        )
        fresh = app.summarize_trader_recency(
            app.get_trader_entry_samples("fresh"),
            now=self.now,
        )

        self.assertLess(
            stale["recency_ratio"],
            fresh["recency_ratio"],
        )
        # 28 días con vida media de 14 => dos vidas medias => ~0.25.
        self.assertAlmostEqual(stale["recency_ratio"], 0.25, places=2)


if __name__ == "__main__":
    unittest.main()
