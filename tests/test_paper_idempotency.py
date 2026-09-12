import json
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


class PreEntryEventGuardTests(unittest.TestCase):
    """Un evento anterior a la entrada no pertenece a esta posición.

    Con webhooks atrasados, una operación vieja puede llegar después de que se
    abrió una posición nueva sobre el mismo token y moverla como si fuera
    actual. La referencia es on-chain contra on-chain: `opened_ts` mide cuándo
    reaccionamos nosotros, es posterior al evento que nos hizo entrar, y
    rechazaría operaciones legítimamente posteriores a la entrada.
    """

    ENTRADA = 1_700_000_000.0

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "pre-entrada.db"
        app.migrate_database()
        self.addCleanup(self.restaurar)
        self.abrir_posicion(self.ENTRADA)

    def restaurar(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def abrir_posicion(self, entrada_ts):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO paper_positions(
                opened_ts, mint, trigger_traders, entry_mc, stake_usd,
                status, pnl_usd, decision, score, origin_trader,
                current_mc, remaining_pct, realized_pnl_usd,
                unrealized_pnl_usd, last_action, tp_stage, mode,
                entry_block_event_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                # `opened_ts` deliberadamente muy posterior al timestamp
                # on-chain de entrada: así es en la realidad, y una guarda que
                # lo usara rechazaría eventos válidos.
                self.ENTRADA + 300, MINT, '["trader-a"]', 100.0, 5.0,
                "open", 0, "COPY", 90, TRADER,
                100.0, 1.0, 0, 0, "HOLD", 0, "paper",
                entrada_ts,
            ),
        )
        conn.commit()
        conn.close()

    def restante(self):
        conn = app.db()
        fila = conn.execute(
            "SELECT remaining_pct FROM paper_positions WHERE mint = ?",
            (MINT,),
        ).fetchone()
        conn.close()
        return fila[0]

    def marcas(self):
        conn = app.db()
        filas = conn.execute(
            "SELECT event_id, action FROM paper_position_applications "
            "ORDER BY event_id"
        ).fetchall()
        conn.close()
        return filas

    def venta(self, firma, ts, indice=0):
        app.update_paper_position(
            mint=MINT, trader=TRADER, side="sell",
            market_cap=110.0, new_token_balance=50.0,
            event_signature=firma, event_index=indice,
            event_block_event_ts=ts,
        )

    def test_event_before_entry_is_ignored(self):
        self.venta("firma-vieja", self.ENTRADA - 1)

        self.assertAlmostEqual(self.restante(), 1.0)
        self.assertEqual(
            self.marcas(), [("firma-vieja:0", "IGNORED_PRE_ENTRY")],
        )

    def test_event_after_entry_applies(self):
        self.venta("firma-nueva", self.ENTRADA + 1)

        self.assertAlmostEqual(self.restante(), 0.75)
        self.assertEqual(self.marcas()[0][1], "PARTIAL SELL")

    def test_event_at_the_same_second_as_entry_applies(self):
        # El timestamp on-chain tiene resolución de segundos: dos operaciones
        # distintas del mismo segundo son indistinguibles, y descartarlas sería
        # perder operaciones válidas. Aceptar la igualdad es lo conservador.
        self.venta("firma-empatada", self.ENTRADA)

        self.assertAlmostEqual(self.restante(), 0.75)

    def test_unknown_event_timestamp_keeps_previous_behaviour(self):
        # PumpPortal no manda timestamp: sin él la guarda se apaga.
        self.venta("firma-sin-ts", None)

        self.assertAlmostEqual(self.restante(), 0.75)

    def test_unknown_entry_timestamp_keeps_previous_behaviour(self):
        # Las posiciones abiertas antes de que la columna existiera quedan en
        # NULL. No hay referencia, así que no se rechaza nada.
        conn = app.db()
        conn.execute(
            "UPDATE paper_positions SET entry_block_event_ts = NULL "
            "WHERE mint = ?", (MINT,),
        )
        conn.commit()
        conn.close()

        self.venta("firma-vieja", self.ENTRADA - 1000)

        self.assertAlmostEqual(self.restante(), 0.75)

    def test_ignored_event_is_not_reprocessed_on_retry(self):
        self.venta("firma-vieja", self.ENTRADA - 1)
        self.venta("firma-vieja", self.ENTRADA - 1)
        self.venta("firma-vieja", self.ENTRADA - 1)

        # Una sola marca, y sigue sin aplicarse. Sin dejar constancia, el
        # reintento lo volvería a evaluar para siempre.
        self.assertEqual(
            self.marcas(), [("firma-vieja:0", "IGNORED_PRE_ENTRY")],
        )
        self.assertAlmostEqual(self.restante(), 1.0)

    def test_ignoring_one_event_does_not_block_the_others(self):
        self.venta("firma-vieja", self.ENTRADA - 1)
        self.venta("firma-nueva", self.ENTRADA + 1)

        self.assertAlmostEqual(self.restante(), 0.75)
        self.assertEqual(
            self.marcas(),
            [
                ("firma-nueva:0", "PARTIAL SELL"),
                ("firma-vieja:0", "IGNORED_PRE_ENTRY"),
            ],
        )

    def test_invalid_timestamps_are_rejected(self):
        for invalido in ("1700000000", float("inf"), float("nan"), 0, -1, True):
            with self.subTest(ts=invalido):
                with self.assertRaises(ValueError):
                    self.venta("firma-1", invalido)

    def test_open_paper_position_stores_the_entry_timestamp(self):
        app.open_paper_position(
            mint="otro-mint", trader=TRADER, market_cap=100.0,
            score=90, decision="COPY", mode="guard-test",
            entry_block_event_ts=self.ENTRADA,
        )

        conn = app.db()
        guardado = conn.execute(
            "SELECT entry_block_event_ts FROM paper_positions "
            "WHERE mint = ?", ("otro-mint",),
        ).fetchone()
        conn.close()
        self.assertEqual(guardado[0], self.ENTRADA)

    def test_open_paper_position_rejects_an_invalid_entry_timestamp(self):
        with self.assertRaises(ValueError):
            app.open_paper_position(
                mint="otro-mint", trader=TRADER, market_cap=100.0,
                score=90, decision="COPY", mode="guard-test",
                entry_block_event_ts="1700000000",
            )


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


class TokenHistoryIdempotencyTests(unittest.TestCase):
    """El historial alimenta el scoring: una fila repetida lo sesga.

    Con reintentos de Helius y dos proveedores entregando lo mismo, guardar
    por firma sola no alcanza en ninguna de las dos direcciones: fundía
    operaciones distintas de una transacción y no podía distinguir un
    reintento de una operación nueva.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "historial.db"
        app.migrate_database()
        self.addCleanup(self.restaurar)

    def restaurar(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def guardar(self, firma, indice=0, source="live", market_cap=100.0):
        app.save_token_history(
            mint=MINT, market_cap=market_cap, trader=TRADER, side="buy",
            signature=firma, source=source, event_index=indice,
        )

    def filas(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT event_id, source, market_cap_sol FROM token_history "
                "ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def test_retry_of_the_same_event_writes_one_row(self):
        self.guardar("firma-1")
        self.guardar("firma-1")
        self.guardar("firma-1")

        self.assertEqual(self.filas(), [("firma-1:0", "live", 100.0)])

    def test_same_signature_distinct_indexes_write_both_rows(self):
        # Dos operaciones Pump de una misma transacción. Con la firma sola, la
        # segunda se descartaba y el historial quedaba incompleto.
        self.guardar("firma-1", indice=0)
        self.guardar("firma-1", indice=1)

        self.assertEqual(
            [fila[0] for fila in self.filas()],
            ["firma-1:0", "firma-1:1"],
        )

    def test_the_same_operation_from_two_providers_is_stored_once(self):
        # PumpPortal entrega una operación por mensaje y no manda índice, así
        # que vale 0. El parser de Helius cuenta operaciones Pump, no líneas de
        # log, así que la primera también es 0. Misma operación, misma
        # identidad, una sola fila.
        self.guardar("firma-1", indice=0, source="live")
        self.guardar("firma-1", indice=0, source="token-live")

        filas = self.filas()
        self.assertEqual(len(filas), 1)
        # Gana el primero que llegó; el segundo no pisa nada.
        self.assertEqual(filas[0][1], "live")

    def test_event_id_excludes_the_source_on_purpose(self):
        # Si `source` entrara en la identidad, cada proveedor tendría la suya y
        # la misma operación se guardaría dos veces.
        self.guardar("firma-1", source="live")
        self.guardar("firma-1", source="rpc-fallback")
        self.guardar("firma-1", source="token-live")

        self.assertEqual(len(self.filas()), 1)

    def test_pumpportal_multi_operation_remains_ambiguous(self):
        """Documenta una limitación abierta, no un comportamiento deseado.

        PumpPortal entrega una operación por mensaje y no manda índice, así
        que dos operaciones Pump de una misma transacción llegan las dos como
        índice 0 y se funden en una sola fila. Renumerar el parser no lo
        arregla: el dato que las distinguiría nunca llega por ese transporte.

        Solo lo resuelve la ingesta de Helius, que sí trae el ordinal. Si
        alguien hace que esto guarde dos filas, revisar de dónde salió el
        índice antes de dar la prueba por obsoleta.
        """
        self.guardar("firma-1", indice=0, market_cap=100.0)
        self.guardar("firma-1", indice=0, market_cap=120.0)

        filas = self.filas()
        self.assertEqual(len(filas), 1)
        # La segunda operación se pierde: no hay con qué distinguirla.
        self.assertEqual(filas[0][2], 100.0)

    def test_without_signature_behaviour_is_unchanged(self):
        # Las rutas de demo no tienen identidad que ofrecer y pueden repetir.
        self.guardar("")
        self.guardar("")

        filas = self.filas()
        self.assertEqual(len(filas), 2)
        self.assertEqual([fila[0] for fila in filas], [None, None])

    def test_distinct_signatures_each_write_a_row(self):
        self.guardar("firma-1")
        self.guardar("firma-2")

        self.assertEqual(len(self.filas()), 2)

    def test_broken_index_is_rejected(self):
        with self.assertRaises(ValueError):
            self.guardar("firma-1", indice="1")

        self.assertEqual(self.filas(), [])

    def test_legacy_rows_without_identity_do_not_block_new_ones(self):
        # Las filas anteriores a la columna quedan en NULL y fuera del índice
        # único parcial: no pueden bloquear una escritura nueva.
        conn = app.db()
        conn.execute(
            "INSERT INTO token_history(ts, mint, market_cap_sol, trader, "
            "side, signature, source) VALUES(?,?,?,?,?,?,?)",
            (1.0, MINT, 50.0, TRADER, "buy", "firma-vieja", "live"),
        )
        conn.execute(
            "INSERT INTO token_history(ts, mint, market_cap_sol, trader, "
            "side, signature, source) VALUES(?,?,?,?,?,?,?)",
            (2.0, MINT, 50.0, TRADER, "buy", "firma-vieja", "live"),
        )
        conn.commit()
        conn.close()

        self.guardar("firma-nueva")

        self.assertEqual(len(self.filas()), 3)


class LegacyDataMigrationTests(unittest.TestCase):
    """La transición con datos reales viejos, que es donde esto puede doler.

    Una base de producción ya tiene historial sin `event_id` y filas de inbox
    numeradas por posición de log. Las pruebas de la lógica nueva no dicen nada
    sobre ese cruce.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "migracion.db"
        self.addCleanup(self.restaurar)

    def restaurar(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def base_vieja(self):
        """Una base como la de producción antes de este bloque."""
        conn = sqlite3.connect(app.DB)
        conn.execute(
            """
            CREATE TABLE token_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, mint TEXT, market_cap_sol REAL,
                trader TEXT DEFAULT '', side TEXT DEFAULT '',
                signature TEXT DEFAULT '', source TEXT DEFAULT 'live'
            )
            """
        )
        conn.commit()
        conn.close()

    def historial(self):
        conn = app.db()
        try:
            return conn.execute(
                "SELECT signature, event_id, source FROM token_history "
                "ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def insertar_historial_viejo(self, *filas):
        conn = sqlite3.connect(app.DB)
        for indice, (firma, source) in enumerate(filas):
            conn.execute(
                "INSERT INTO token_history(ts, mint, market_cap_sol, trader, "
                "side, signature, source) VALUES(?,?,?,?,?,?,?)",
                (float(indice), MINT, 100.0, TRADER, "buy", firma, source),
            )
        conn.commit()
        conn.close()

    def test_historical_rows_get_identity_so_a_replay_does_not_duplicate(self):
        self.base_vieja()
        self.insertar_historial_viejo(("firma-vieja", "live"))

        app.migrate_database()

        self.assertEqual(
            self.historial(), [("firma-vieja", "firma-vieja:0", "live")],
        )

        # El replay del mismo evento desde la cola no agrega nada.
        app.save_token_history(
            mint=MINT, market_cap=100.0, trader=TRADER, side="buy",
            signature="firma-vieja", source="helius-replay", event_index=0,
        )

        self.assertEqual(len(self.historial()), 1)

    def test_duplicate_historical_signatures_do_not_break_the_migration(self):
        # Antes de que existiera cualquier deduplicación pudo quedar la misma
        # firma dos veces. Migrar las dos violaría el índice único.
        self.base_vieja()
        self.insertar_historial_viejo(
            ("firma-repetida", "live"),
            ("firma-repetida", "live"),
        )

        app.migrate_database()

        self.assertEqual(
            [fila[1] for fila in self.historial()],
            ["firma-repetida:0", None],
        )

        # Y el replay sigue sin duplicar: la identidad ya está ocupada.
        app.save_token_history(
            mint=MINT, market_cap=100.0, trader=TRADER, side="buy",
            signature="firma-repetida", source="helius-replay", event_index=0,
        )
        self.assertEqual(len(self.historial()), 2)

    def test_migration_survives_an_identity_already_taken_by_a_new_row(self):
        # Ventana del despliegue: el evento se guardó sin identidad, volvió a
        # llegar ya migrado, y la fila vieja queda sin poder tomar la suya.
        self.base_vieja()
        self.insertar_historial_viejo(("firma-1", "live"))
        app.migrate_database()

        conn = app.db()
        conn.execute(
            "UPDATE token_history SET event_id = NULL WHERE signature = ?",
            ("firma-1",),
        )
        conn.commit()
        conn.close()
        app.save_token_history(
            mint=MINT, market_cap=100.0, trader=TRADER, side="buy",
            signature="firma-1", source="nueva", event_index=0,
        )

        # La migración no puede estallar por esto.
        app.migrate_database()

        self.assertEqual(
            [fila[1] for fila in self.historial()], [None, "firma-1:0"],
        )

    def test_rows_without_signature_keep_their_null_identity(self):
        self.base_vieja()
        self.insertar_historial_viejo(("", "demo"), ("", "demo"))

        app.migrate_database()

        self.assertEqual([fila[1] for fila in self.historial()], [None, None])

    def test_migration_is_idempotent(self):
        self.base_vieja()
        self.insertar_historial_viejo(("firma-1", "live"), ("firma-2", "live"))

        app.migrate_database()
        antes = self.historial()
        app.migrate_database()

        self.assertEqual(self.historial(), antes)


class LegacyInboxRenumberTests(unittest.TestCase):
    """Las filas de inbox viejas llevan índice de log, no ordinal."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "inbox-viejo.db"
        app.migrate_database()
        self.addCleanup(self.restaurar)

    def restaurar(self):
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def insertar(self, firma, indice):
        conn = app.db()
        conn.execute(
            """
            INSERT INTO market_event_inbox(
                signature, event_index, event_index_scheme, source, wallet,
                trader, mint, side, pool, block_time, block_event_ts,
                received_ts, event_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                firma, indice, "log-v1", "helius", "wallet", TRADER, MINT,
                "buy", "pump", 1.0, 1.0, 2.0,
                json.dumps(
                    {"signature": firma, "eventIndex": indice, "mint": MINT},
                    sort_keys=True, separators=(",", ":"),
                ),
            ),
        )
        conn.commit()
        conn.close()

    def renumerar(self):
        conn = app.db()
        try:
            renumeradas = app.migrate_inbox_event_index_to_ordinal(conn)
            conn.commit()
            return renumeradas
        finally:
            conn.close()

    def filas(self):
        conn = app.db()
        try:
            return [
                (firma, indice, json.loads(cuerpo)["eventIndex"])
                for firma, indice, cuerpo in conn.execute(
                    "SELECT signature, event_index, event_json "
                    "FROM market_event_inbox ORDER BY signature, event_index"
                ).fetchall()
            ]
        finally:
            conn.close()

    def test_log_indexes_become_ordinals_in_column_and_json(self):
        # Una operación sola escrita con índice de log 1: por PumpPortal la
        # misma operación vale 0, así que sin renumerar tendría dos identidades.
        self.insertar("firma-una", 1)
        # Y una transacción de dos operaciones, en 1 y 2.
        self.insertar("firma-dos", 1)
        self.insertar("firma-dos", 2)

        renumeradas = self.renumerar()

        self.assertEqual(renumeradas, 3)
        self.assertEqual(
            self.filas(),
            [
                ("firma-dos", 0, 0),
                ("firma-dos", 1, 1),
                ("firma-una", 0, 0),
            ],
        )

    def test_renumbering_is_idempotent(self):
        self.insertar("firma-dos", 1)
        self.insertar("firma-dos", 2)
        self.renumerar()

        antes = self.filas()
        renumeradas = self.renumerar()

        self.assertEqual(renumeradas, 0)
        self.assertEqual(self.filas(), antes)

    def test_old_schema_gets_versioned_before_rows_are_renumbered(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE market_event_inbox(
                    signature TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY(signature, event_index)
                )
                """
            )
            conn.execute(
                "INSERT INTO market_event_inbox VALUES(?,?,?)",
                (
                    "firma-vieja",
                    1,
                    json.dumps({
                        "signature": "firma-vieja", "eventIndex": 1,
                    }),
                ),
            )

            app.migrate_market_event_inbox_index_scheme(conn)
            renumeradas = app.migrate_inbox_event_index_to_ordinal(conn)

            row = conn.execute(
                "SELECT event_index, event_index_scheme, event_json "
                "FROM market_event_inbox"
            ).fetchone()
            self.assertEqual(renumeradas, 1)
            self.assertEqual(row[:2], (0, "ordinal-v1"))
            self.assertEqual(json.loads(row[2])["eventIndex"], 0)
        finally:
            conn.close()

    def test_converted_rows_skip_the_full_json_scan(self):
        self.insertar("firma-dos", 1)
        self.renumerar()

        statements = []
        conn = app.db()
        try:
            conn.set_trace_callback(statements.append)
            renumeradas = app.migrate_inbox_event_index_to_ordinal(conn)
        finally:
            conn.close()

        self.assertEqual(renumeradas, 0)
        self.assertFalse(any(
            "SELECT EVENT_INDEX, EVENT_JSON" in statement.upper()
            for statement in statements
        ))

    def test_rows_from_a_rollback_are_detected_after_initial_migration(self):
        self.insertar("firma-dos", 1)
        self.renumerar()
        self.insertar("firma-dos", 3)

        renumeradas = self.renumerar()

        self.assertEqual(renumeradas, 1)
        self.assertEqual(
            self.filas(),
            [("firma-dos", 0, 0), ("firma-dos", 1, 1)],
        )

    def test_rows_already_ordinal_are_left_alone(self):
        self.insertar("firma-ok", 0)
        self.insertar("firma-ok", 1)

        self.assertEqual(self.renumerar(), 0)


class RouterTokenHistoryTests(unittest.TestCase):
    """El índice también tiene que llegar al historial por el recorrido real."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "router-historial.db"
        app.migrate_database()

        self.watched_original = app.WATCHED
        app.WATCHED = {TRADER: WALLET}
        app.TRACKED_TOKENS.add(MINT)
        self.addCleanup(self.restaurar)

    def restaurar(self):
        app.WATCHED = self.watched_original
        app.TRACKED_TOKENS.discard(MINT)
        app.DB = self.original_db
        self.temp_dir.cleanup()

    def identidades(self):
        conn = app.db()
        try:
            return [
                fila[0] for fila in conn.execute(
                    "SELECT event_id FROM token_history ORDER BY event_id"
                ).fetchall()
            ]
        finally:
            conn.close()

    def evento(self, indice, wallet):
        return {
            "signature": "firma-compartida",
            "eventIndex": indice,
            "mint": MINT,
            "txType": "sell",
            "traderPublicKey": wallet,
            "solAmount": 1.0,
            "tokenAmount": 1000.0,
            "newTokenBalance": 500.0,
            "marketCapSol": 110.0,
        }

    def test_watched_wallet_route_preserves_the_index(self):
        app.route_market_event(self.evento(0, WALLET))
        app.route_market_event(self.evento(1, WALLET))

        self.assertEqual(
            self.identidades(),
            ["firma-compartida:0", "firma-compartida:1"],
        )

    def test_tracked_token_route_preserves_the_index(self):
        app.route_market_event(self.evento(0, "billetera-ajena"))
        app.route_market_event(self.evento(1, "billetera-ajena"))

        self.assertEqual(
            self.identidades(),
            ["firma-compartida:0", "firma-compartida:1"],
        )

    def test_repeated_delivery_through_the_router_writes_one_row(self):
        evento = self.evento(0, WALLET)
        app.route_market_event(evento)
        app.route_market_event(evento)

        self.assertEqual(self.identidades(), ["firma-compartida:0"])


class RouterPreEntryGuardTests(unittest.TestCase):
    """La guarda tiene que sobrevivir el recorrido real, no solo la función.

    El patrón ya apareció tres veces —índice, billetera, timestamp—: el dato
    correcto adentro de la función y perdido en el camino hasta ella. Estas
    pruebas entran por `route_market_event()`, que es por donde entran los
    eventos en producción, y cubren sus dos rutas.
    """

    ENTRADA = 1_700_000_000.0

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp_dir.name) / "router-pre-entrada.db"
        app.migrate_database()

        self.watched_original = app.WATCHED
        app.WATCHED = {TRADER: WALLET}
        app.TRACKED_TOKENS.add(MINT)
        self.addCleanup(self.restaurar)

        conn = app.db()
        conn.execute(
            """
            INSERT INTO paper_positions(
                opened_ts, mint, trigger_traders, entry_mc, stake_usd,
                status, pnl_usd, decision, score, origin_trader,
                current_mc, remaining_pct, realized_pnl_usd,
                unrealized_pnl_usd, last_action, tp_stage, mode,
                entry_block_event_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                # Posterior al timestamp on-chain de entrada, como en la
                # realidad: si alguien vuelve a usar `opened_ts` como
                # referencia, estas pruebas fallan.
                self.ENTRADA + 300, MINT, f'["{TRADER}"]', 100.0, 5.0,
                "open", 0, "COPY", 90, TRADER,
                100.0, 1.0, 0, 0, "HOLD", 0, "paper",
                self.ENTRADA,
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

    def marcas(self):
        conn = app.db()
        filas = conn.execute(
            "SELECT event_id, action FROM paper_position_applications "
            "ORDER BY event_id"
        ).fetchall()
        conn.close()
        return filas

    def evento(self, firma, wallet, block_ts, market_cap, balance):
        return {
            "signature": firma,
            "eventIndex": 0,
            "blockEventTs": block_ts,
            "mint": MINT,
            "txType": "sell",
            "traderPublicKey": wallet,
            "solAmount": 1.0,
            "tokenAmount": 1000.0,
            "newTokenBalance": balance,
            "marketCapSol": market_cap,
        }

    def test_watched_wallet_route_ignores_an_event_before_entry(self):
        # Ruta por save_trade(): el trader de origen vende. Un evento anterior
        # a la entrada no puede mover esta posición.
        app.route_market_event(
            self.evento("firma-vieja", WALLET, self.ENTRADA - 1, 110.0, 500.0)
        )

        self.assertAlmostEqual(self.restante(), 1.0)
        self.assertEqual(
            self.marcas(), [("firma-vieja:0", "IGNORED_PRE_ENTRY")],
        )

        # Y uno posterior sí llega: la ruta funciona, no está muerta.
        app.route_market_event(
            self.evento("firma-nueva", WALLET, self.ENTRADA + 1, 110.0, 400.0)
        )

        self.assertAlmostEqual(self.restante(), 0.75)

    def test_tracked_token_route_ignores_an_event_before_entry(self):
        # Ruta por token seguido con billetera ajena: el trader no coincide,
        # así que lo que dispara la venta es el precio.
        app.route_market_event(
            self.evento(
                "firma-vieja", "billetera-ajena", self.ENTRADA - 1, 125.0, 900.0
            )
        )

        self.assertAlmostEqual(self.restante(), 1.0)
        self.assertEqual(
            self.marcas(), [("firma-vieja:0", "IGNORED_PRE_ENTRY")],
        )

        app.route_market_event(
            self.evento(
                "firma-nueva", "billetera-ajena", self.ENTRADA + 1, 125.0, 900.0
            )
        )

        self.assertAlmostEqual(self.restante(), 0.75)


if __name__ == "__main__":
    unittest.main()
