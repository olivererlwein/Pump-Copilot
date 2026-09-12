import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


MINT = "mint-idempotencia"
TRADER = "trader-a"


class PaperPositionIdempotencyTests(unittest.TestCase):
    """Un evento reintentado no debe aplicarse dos veces a la misma posición.

    `update_paper_position()` descuenta de `remaining_pct` y acumula en
    `realized_pnl_usd`. Sin identidad de evento, un reintento convierte una
    venta parcial del 25% en una del 50% y suma la ganancia dos veces.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "paper-idempotencia.db"
        app.migrate_database()
        self.abrir_posicion()

    def tearDown(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def abrir_posicion(self, entry_mc=100.0):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO paper_positions(
                opened_ts, mint, trigger_traders, entry_mc, stake_usd,
                status, pnl_usd, decision, score, origin_trader,
                current_mc, remaining_pct, realized_pnl_usd,
                unrealized_pnl_usd, last_action, tp_stage, mode
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1000.0, MINT, '["trader-a"]', entry_mc, 5.0,
                "open", 0, "COPY", 90, TRADER,
                entry_mc, 1.0, 0, 0, "HOLD", 0, "paper",
            ),
        )
        conn.commit()
        conn.close()

    def posicion(self):
        conn = app.db()
        row = conn.execute(
            "SELECT remaining_pct, realized_pnl_usd, tp_stage, status "
            "FROM paper_positions WHERE mint = ?",
            (MINT,),
        ).fetchone()
        conn.close()
        return {
            "remaining": row[0],
            "realized": row[1],
            "tp_stage": row[2],
            "status": row[3],
        }

    def venta_parcial(self, firma=None, indice=0, mint=MINT):
        # El trader de origen vende: dispara PARTIAL SELL (25%).
        app.update_paper_position(
            mint=mint, trader=TRADER, side="sell",
            market_cap=110.0, new_token_balance=50.0,
            event_signature=firma, event_index=indice,
        )

    def test_repeated_event_applies_once(self):
        self.venta_parcial(firma="firma-1")
        despues_primera = self.posicion()

        self.venta_parcial(firma="firma-1")
        despues_reintento = self.posicion()

        self.assertAlmostEqual(despues_primera["remaining"], 0.75)
        self.assertEqual(
            despues_reintento["remaining"],
            despues_primera["remaining"],
        )
        self.assertEqual(
            despues_reintento["realized"],
            despues_primera["realized"],
        )

    def test_distinct_events_each_apply(self):
        # La guarda no debe bloquear eventos legítimamente distintos.
        self.venta_parcial(firma="firma-1")
        self.venta_parcial(firma="firma-2")

        self.assertAlmostEqual(self.posicion()["remaining"], 0.50)

    def test_without_event_id_behaviour_is_unchanged(self):
        # Las rutas de demo no tienen identidad que ofrecer.
        self.venta_parcial()
        self.venta_parcial()

        self.assertAlmostEqual(self.posicion()["remaining"], 0.50)

    def test_same_event_on_another_position_still_applies(self):
        # La identidad es por evento Y posición: un mismo evento puede tocar
        # legítimamente posiciones distintas.
        conn = app.db()
        conn.execute(
            "UPDATE paper_positions SET mint = ? WHERE mint = ?",
            ("otro-mint", MINT),
        )
        conn.commit()
        conn.close()
        self.abrir_posicion()

        self.venta_parcial(firma="firma-1", mint="otro-mint")
        self.venta_parcial(firma="firma-1")

        conn = app.db()
        filas = conn.execute(
            "SELECT mint, remaining_pct FROM paper_positions ORDER BY mint"
        ).fetchall()
        conn.close()

        self.assertEqual(len(filas), 2)
        for _, restante in filas:
            self.assertAlmostEqual(restante, 0.75)

    def test_failure_after_update_leaves_no_partial_state(self):
        """Si la transacción falla, no queda ni el efecto ni la marca.

        El caso peligroso es el inverso: que la posición quede modificada sin
        su marca, porque entonces el reintento la volvería a modificar.
        """
        original = app.db

        class ConexionQueFallaAlConfirmar:
            """Delega todo salvo `commit`, que no se puede parchear directo."""

            def __init__(self, real):
                self._real = real

            def __getattr__(self, nombre):
                return getattr(self._real, nombre)

            def commit(self):
                raise sqlite3.OperationalError("fallo simulado al confirmar")

        def db_que_falla_al_confirmar():
            return ConexionQueFallaAlConfirmar(original())

        with patch.object(app, "db", db_que_falla_al_confirmar):
            with self.assertRaises(sqlite3.OperationalError):
                self.venta_parcial(firma="firma-1")

        intacta = self.posicion()
        self.assertAlmostEqual(intacta["remaining"], 1.0)
        self.assertAlmostEqual(intacta["realized"], 0)

        conn = app.db()
        marcas = conn.execute(
            "SELECT COUNT(*) FROM paper_position_applications"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(marcas, 0)

        # Y el reintento posterior sí aplica, una sola vez.
        self.venta_parcial(firma="firma-1")
        self.assertAlmostEqual(self.posicion()["remaining"], 0.75)


    def test_same_signature_distinct_indexes_both_apply(self):
        """Una transacción puede traer varias operaciones Pump válidas.

        Con la firma sola como identidad, la segunda se descartaría como
        duplicado y se perdería.
        """
        self.venta_parcial(firma="firma-1", indice=0)
        self.venta_parcial(firma="firma-1", indice=1)

        self.assertAlmostEqual(self.posicion()["remaining"], 0.50)

        conn = app.db()
        ids = [
            fila[0] for fila in conn.execute(
                "SELECT event_id FROM paper_position_applications "
                "ORDER BY event_id"
            ).fetchall()
        ]
        conn.close()
        self.assertEqual(ids, ["firma-1:0", "firma-1:1"])

    def test_audit_failure_rolls_back_the_effect_too(self):
        """La auditoría y el efecto son atómicos.

        Si la auditoría quedara fuera de la transacción y fallara, el reintento
        vería la marca de aplicación y el evento de auditoría se perdería para
        siempre.
        """
        def auditoria_que_falla(**kwargs):
            raise sqlite3.OperationalError("fallo simulado en auditoría")

        with patch.object(app, "save_position_event", auditoria_que_falla):
            with self.assertRaises(sqlite3.OperationalError):
                self.venta_parcial(firma="firma-1")

        intacta = self.posicion()
        self.assertAlmostEqual(intacta["remaining"], 1.0)

        conn = app.db()
        marcas = conn.execute(
            "SELECT COUNT(*) FROM paper_position_applications"
        ).fetchone()[0]
        eventos = conn.execute(
            "SELECT COUNT(*) FROM position_events"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(marcas, 0)
        self.assertEqual(eventos, 0)

        # El reintento aplica el efecto y deja su auditoría.
        self.venta_parcial(firma="firma-1")
        conn = app.db()
        eventos = conn.execute(
            "SELECT COUNT(*) FROM position_events"
        ).fetchone()[0]
        conn.close()
        self.assertAlmostEqual(self.posicion()["remaining"], 0.75)
        self.assertEqual(eventos, 1)

    def test_invalid_index_is_rejected_instead_of_becoming_zero(self):
        """Convertir un índice inválido en 0 crea colisiones.

        Si un índice roto se volviera 0 en silencio, dos operaciones distintas
        de la misma transacción compartirían identidad y la segunda se
        descartaría como duplicado: el error que la identidad existe para
        evitar, ahora invisible.
        """
        for invalido in ("1", None, 1.0, True, -1, [1]):
            with self.subTest(event_index=invalido):
                with self.assertRaises(ValueError):
                    app.market_event_identity("firma-1", invalido)

        # Sin firma no hay identidad que construir, y eso no es un error:
        # es la ruta de demo, que no tiene nada que ofrecer.
        self.assertIsNone(app.market_event_identity("", 0))

    def test_cleanup_is_retried_when_it_failed_after_the_commit(self):
        """El cierre puede quedar confirmado y la limpieza posterior fallar.

        `untrack_token_if_unused()` abre su propia conexión después del commit.
        Si falla, la posición ya quedó cerrada y marcada, así que el reintento
        no encuentra posición abierta. Sin este rescate la limpieza no se
        repetiría nunca y el token quedaría suscripto para siempre.
        """
        app.TRACKED_TOKENS.add(MINT)
        self.addCleanup(app.TRACKED_TOKENS.discard, MINT)

        def limpieza_que_falla(mint):
            raise sqlite3.OperationalError("fallo simulado al limpiar")

        # El EXIT cierra la posición entera: el trader de origen vende todo.
        def cierre(firma):
            app.update_paper_position(
                mint=MINT, trader=TRADER, side="sell",
                market_cap=110.0, new_token_balance=0.0,
                event_signature=firma, event_index=0,
            )

        with patch.object(app, "untrack_token_if_unused", limpieza_que_falla):
            with self.assertRaises(sqlite3.OperationalError):
                cierre("firma-1")

        # El cierre sí quedó confirmado: es justo lo que hace peligroso al caso.
        self.assertEqual(self.posicion()["status"], "closed")
        self.assertIn(MINT, app.TRACKED_TOKENS)

        # El reintento del mismo evento no aplica nada de nuevo...
        realizado_antes = self.posicion()["realized"]
        cierre("firma-1")
        self.assertEqual(self.posicion()["realized"], realizado_antes)

        # ...pero sí repite la limpieza que había quedado pendiente.
        self.assertNotIn(MINT, app.TRACKED_TOKENS)

    def test_exception_before_update_releases_the_write_lock(self):
        """`BEGIN IMMEDIATE` toma el lock al abrir la transacción.

        Una excepción entre ese punto y el UPDATE no debe dejar la base
        bloqueada para los demás escritores.
        """
        def decision_que_falla(**kwargs):
            raise RuntimeError("fallo simulado antes del UPDATE")

        with patch.object(app, "decide_paper_position_action",
                          decision_que_falla):
            with self.assertRaises(RuntimeError):
                self.venta_parcial(firma="firma-1")

        # Si el lock siguiera tomado, este escritor fallaría por timeout.
        conn = app.db()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE paper_positions SET current_mc = ? WHERE mint = ?",
            (123.0, MINT),
        )
        conn.commit()
        conn.close()

        self.assertAlmostEqual(self.posicion()["remaining"], 1.0)


WALLET = "wallet-del-trader-a"


class RouterEventIndexTests(unittest.TestCase):
    """El índice tiene que sobrevivir el recorrido real, no solo la llamada.

    `update_paper_position()` acepta `event_index`, pero eso no sirve de nada
    si el router lo pierde en el camino. Estas pruebas entran por
    `route_market_event()`, que es por donde entran los eventos en producción.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "router-indice.db"
        app.migrate_database()

        self.watched_original = app.WATCHED
        app.WATCHED = {TRADER: WALLET}
        self.addCleanup(self.restaurar)

        app.TRACKED_TOKENS.add(MINT)

        conn = app.db()
        conn.execute(
            """
            INSERT INTO paper_positions(
                opened_ts, mint, trigger_traders, entry_mc, stake_usd,
                status, pnl_usd, decision, score, origin_trader,
                current_mc, remaining_pct, realized_pnl_usd,
                unrealized_pnl_usd, last_action, tp_stage, mode
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1000.0, MINT, f'["{TRADER}"]', 100.0, 5.0,
                "open", 0, "COPY", 90, TRADER,
                100.0, 1.0, 0, 0, "HOLD", 0, "paper",
            ),
        )
        conn.commit()
        conn.close()

    def restaurar(self):
        app.WATCHED = self.watched_original
        app.TRACKED_TOKENS.discard(MINT)
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def restante(self):
        conn = app.db()
        fila = conn.execute(
            "SELECT remaining_pct FROM paper_positions WHERE mint = ?",
            (MINT,),
        ).fetchone()
        conn.close()
        return fila[0]

    def identidades_aplicadas(self):
        conn = app.db()
        ids = [
            fila[0] for fila in conn.execute(
                "SELECT event_id FROM paper_position_applications "
                "ORDER BY event_id"
            ).fetchall()
        ]
        conn.close()
        return ids

    def evento(self, indice, wallet, market_cap, balance):
        return {
            "signature": "firma-compartida",
            "eventIndex": indice,
            "mint": MINT,
            "txType": "sell",
            "traderPublicKey": wallet,
            "solAmount": 1.0,
            "tokenAmount": 1000.0,
            "newTokenBalance": balance,
            "marketCapSol": market_cap,
        }

    def test_watched_wallet_route_preserves_the_index(self):
        # Ruta por save_trade(): el trader de origen vende parte de su posición
        # dos veces dentro de la misma transacción.
        app.route_market_event(self.evento(0, WALLET, 110.0, 500.0))
        app.route_market_event(self.evento(1, WALLET, 110.0, 400.0))

        # Con la firma sola como identidad, la segunda venta se descartaba y
        # el restante quedaba en 0.75.
        self.assertAlmostEqual(self.restante(), 0.50)
        self.assertEqual(
            self.identidades_aplicadas(),
            ["firma-compartida:0", "firma-compartida:1"],
        )

    def test_tracked_token_route_preserves_the_index(self):
        # Ruta por token seguido, con una billetera ajena: el trader no
        # coincide, así que lo que dispara la venta es el precio. Dos tramos de
        # take profit dentro de la misma transacción.
        app.route_market_event(self.evento(0, "billetera-ajena", 125.0, 900.0))
        app.route_market_event(self.evento(1, "billetera-ajena", 150.0, 800.0))

        self.assertAlmostEqual(self.restante(), 0.50)
        self.assertEqual(
            self.identidades_aplicadas(),
            ["firma-compartida:0", "firma-compartida:1"],
        )

    def test_repeated_event_through_the_router_applies_once(self):
        # La otra mitad: el índice no debe romper la idempotencia real.
        evento = self.evento(1, WALLET, 110.0, 500.0)
        app.route_market_event(evento)
        app.route_market_event(evento)

        self.assertAlmostEqual(self.restante(), 0.75)
        self.assertEqual(self.identidades_aplicadas(), ["firma-compartida:1"])

    def test_event_without_index_keeps_pumpportal_semantics(self):
        # PumpPortal entrega una operación por mensaje y no manda índice.
        sin_indice = self.evento(0, WALLET, 110.0, 500.0)
        del sin_indice["eventIndex"]

        app.route_market_event(sin_indice)

        self.assertEqual(self.identidades_aplicadas(), ["firma-compartida:0"])

    def test_broken_index_in_the_event_is_rejected(self):
        roto = self.evento("1", WALLET, 110.0, 500.0)

        with self.assertRaises(ValueError):
            app.route_market_event(roto)

        self.assertAlmostEqual(self.restante(), 1.0)


if __name__ == "__main__":
    unittest.main()
