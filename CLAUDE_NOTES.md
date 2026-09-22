# Notas de Claude — auditoría de riesgo (2026-09-09/10)

Sesión de solo lectura con Claude Code. No se modificó ningún archivo del repo durante la auditoría. Esto es un resumen para que Codex/ChatGPT tenga el contexto sin repetir el trabajo.

Bitácora visual completa (para humanos): https://claude.ai/code/artifact/d79bbe28-98b4-4b7c-8915-b04006ebe0ba

## Estado

- Live trading sigue protegido: `LIVE_EXECUTION_IMPLEMENTED = False` (`app.py:171`), hardcodeado, no depende de variable de entorno.
- Ningún archivo fue tocado. Este archivo es el único cambio hecho por Claude en el repo.
- Pendiente: diseño del auto-aprendizaje de `TRADER_QUALITY` (ver sección 5), solo bocetado, sin implementar.

## 1. Cómo se genera una señal

`stream()` (`app.py:8064`) → dedupe de duplicados por firma (memoria `SEEN_SIGNATURES` + tabla persistente `processed_signatures`, `mark_signature_processed` en `app.py:725`) → si la wallet es una de las 5 watched → `save_trade()` (`app.py:7542`) → si es buy → `evaluate_buy()` (`app.py:6665`), que suma 6 sub-scores (`score_trader`, `score_timing`, `score_trade_size`, `score_token_structure`, `score_consensus`, `score_market_context`) → `decision_from_score()` (`app.py:6646`):
- `>= 80` → **COPY**
- `>= 60` → **WATCH**
- resto → **SKIP**

El modelo shadow (`observe_shadow_signal`, `app.py:6899`) se registra en paralelo pero **no influye** en la decisión — confirmado, es puramente observacional.

## 2. Cómo entra a paper trading

Solo si `decision == "COPY"` → `open_paper_position()` (`app.py:6965`). Protecciones verificadas: `risk_check()` (kill switch, monto máx, pérdida diaria máx), transacción `BEGIN IMMEDIATE`, y límite de **una sola posición abierta a la vez** (`count_open_positions() >= 1`, `app.py:3792`) — comparte esta regla con `live`.

## 3. Cómo se grabaría una compra real (no conectada todavía)

`execute_pumpportal_lightning_buy()` (`app.py:2410`): gate `require_live_trading()` (exige `LIVE_TRADING=true` env **y** `LIVE_EXECUTION_IMPLEMENTED` hardcodeado), idempotencia obligatoria, `risk_check`, envío, y reconciliación asíncrona (`pumpportal_execution_reconciliation_worker`, `app.py:8582`) que verifica el recibo on-chain contra la orden antes de escribir en `live_positions` (`record_finalized_buy_position`, `app.py:3457`).

**Confirmado: esta función no se llama desde ningún lugar de producción**, solo existe la definición y tests. No hay ruta automática COPY → compra real todavía.

## 4. Cómo se cierran posiciones reales (SÍ está conectado)

A diferencia de la compra, el cierre **sí corre automáticamente** dentro de `stream()`: para cualquier evento de un mint trackeado (`app.py:8360`, incluye trades de cualquier wallet, no solo watched) se llama `evaluate_live_position_exit()` (`app.py:7205` → invocada en `app.py:8430`), que decide sin revisión humana por operación vía `decide_live_position_exit()` (`app.py:7137`):
- `STOP_LOSS` si cae ≥20% desde el market cap de entrada
- `TRADER_EXIT` si el trader origen vendió todo su balance
- `TAKE_PROFIT` escalonado en cuartos (25/50/100% de suba)
- `TRADER_PARTIAL` si el trader origen vendió una parte

Cada venta pasa igual por `require_live_trading()` → hoy bloqueada por el mismo flag que la compra.

**Hallazgo clave:** el día que se active `LIVE_EXECUTION_IMPLEMENTED`, la venta automática queda operativa sin ningún paso adicional de revisión — activar un solo flag reactiva los dos lados del ciclo (compra manual/futura + venta ya automática).

Protección contra sobreventa verificada: antes de crear una orden de venta se suma el `requested_token_amount_raw` de las órdenes pendientes de esa posición y se resta del remanente conocido (`app.py:2599-2609`) — no se puede vender más de lo disponible aunque el `tp_stage` en DB esté desactualizado por reconciliación pendiente.

## 5. Hallazgos (con prioridad)

| Prioridad | Hallazgo | Ubicación |
|---|---|---|
| Media | Scoring de trader estático (`TRADER_QUALITY`, diccionario fijo); el "aprendizaje" que describe el comentario del código no está implementado. | `app.py:120-126`, `4287-4294` |
| Media | Único gate de compra real es una constante en código (`LIVE_EXECUTION_IMPLEMENTED`); activarla reactiva también la venta automática sin paso extra. | `app.py:171` |
| Media | `db()` recrea conexión SQLite y corre `CREATE TABLE IF NOT EXISTS` en cada llamada, incluso dentro del loop del stream — overhead en el camino crítico de stop-loss/take-profit. | `app.py:247` |
| Baja | `evaluate_buy()` traga `sqlite3.IntegrityError` en el INSERT de `evaluations` y sigue evaluando COPY con la decisión ya calculada antes del `try`. Mitigado por las protecciones de `open_paper_position()`. | `app.py:6920-6952` |
| Baja | `SEEN_SIGNATURES` en memoria se vacía entera al superar 5000 elementos; el dedupe persistente en DB sigue cubriendo el caso. | `app.py:8306-8307` |
| Info | Límite global de una sola posición abierta (paper y live comparten la regla) — confirmar que es intencional antes de escalar. | `app.py:3792` |
| Info | CLI de `graphify` roto en este entorno (`error: uv trampoline failed to canonicalize script path`). No bloqueó esta auditoría (se usó lectura directa + grep), sí puede afectar auditorías futuras. | entorno |

## 6. Cruce contra `tests/` — qué ya está probado

El código solo no cuenta toda la historia; la suite ya cubre la mayoría de las carreras de concurrencia:

- ✅ Gate de compra real / venta "observa pero nunca ejecuta" mientras está deshabilitada — `tests/test_risk_controls.py:71,426`
- ✅ Una sola posición abierta a la vez, por modo — `tests/test_risk_controls.py:553`
- ✅ Reserva de tokens: take-profit repetido y ventas simultáneas no reservan el mismo saldo — `tests/test_risk_controls.py:450,531`
- ✅ Reconciliación concurrente no duplica posiciones; identidad de recibo no se puede eludir — `tests/test_solana_receipts.py:180,211,218`
- ✅ Prioridad correcta entre stop-loss y salida del trader origen — `tests/test_risk_controls.py:494`
- ❌ Sin test: scoring de trader estático (no hay mecanismo que probar)
- ❌ Sin test: el `IntegrityError` silencioso de `evaluate_buy()` sobre `evaluations`

Conclusión: el proyecto está mejor blindado de lo que parece leyendo solo `app.py`. Los dos puntos abiertos de verdad son el scoring estático (decisión de producto pendiente, no bug) y el detalle menor de `evaluate_buy`.

## 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código)

Espeja el patrón shadow/challenger que ya existe para el modelo de ML (`assess_shadow_challenger`, `app.py:5402`) en vez de inventar uno nuevo:

1. **Fuente de datos (ya existe):** `get_trader_hit_stats()` y `get_trader_copyability_stats()` (`app.py:4364`, `4296`) — hit rate TP25/TP50/SL10 y retorno promedio por trader, ya calculados sobre `signal_outcomes`.
2. **Umbral mínimo de muestras:** no proponer ajuste con menos de ~20-30 señales con resultado completo por trader, para no ajustar sobre ruido.
3. **Fórmula candidata, separada de la real:** un `quality_candidate` en una tabla nueva, nunca leído directo por `score_trader()`.
4. **Paso limitado, no salto:** tope de movimiento por ciclo (ej. ±3 puntos/semana) para que una racha corta no cambie una decisión de COPY a SKIP de golpe.
5. **Promoción manual, no automática:** endpoint de solo lectura que muestra peso actual vs. candidato y su respaldo estadístico — la persona sigue decidiendo cuándo promoverlo.
6. **Auditoría del cambio:** cada promoción queda registrada con timestamp y las estadísticas que la justificaron.

## Reglas vigentes para quien siga este trabajo

- No tocar dinero real sin revisión doble.
- No modificar archivos de trading (buy/sell/risk_check/kill switch) sin que se pida explícitamente para ese cambio puntual.
- El shadow model debe seguir siendo puramente observacional — no debe alimentar `decision_from_score()` sin una promoción explícita y revisada.

---

# Perfil integral de calidad del trader (shadow) — 2026-09-10

Implementado por Claude (Opus 5) sobre `5201a0d`, **sin commit ni push**: el diff
queda en el working tree para revisión de Codex.

## Qué se agregó

Un sistema de evaluación multidimensional y **observacional** de la calidad de
cada trader. No toca `score_trader()`, `decision_from_score()`, ni ninguna ruta
de ejecución; se expone únicamente para revisión humana.

`app.py` quedó **puramente aditivo** (803 líneas nuevas, 0 borradas).

### Funciones nuevas (`app.py`)

| Función | Rol |
|---|---|
| `wilson_interval()` | Intervalo de confianza de Wilson (stdlib, sin numpy) |
| `beta_posterior_rate()` | Media posterior Beta reusando el prior neutral vigente |
| `get_trader_entry_samples()` | Una muestra por token (primer outcome completado de cada mint) |
| `get_trader_exit_cycles()` | Reconstrucción de ciclos entrada → ventas |
| `summarize_trader_entry_quality()` | TP25 antes de SL10, aislado |
| `summarize_trader_returns()` | Retornos y drawdown |
| `summarize_trader_consistency()` | Estabilidad entre primera y segunda mitad temporal |
| `get_trader_activity_concentration()` | Concentración de actividad (Herfindahl) |
| `summarize_trader_recency()` | Decaimiento exponencial por antigüedad |
| `summarize_trader_exit_quality()` | Comportamiento de ventas y calidad de salida |
| `summarize_trader_copyability()` | Proporción de señales realmente observables |
| `summarize_trader_evidence()` | Cantidad de evidencia y etiqueta de confianza |
| `calculate_trader_profile_score()` | Score integral 0-100 con pesos renormalizados |
| `get_trader_quality_profile()` / `get_trader_quality_profiles()` | Ensamblado |

### Endpoint nuevo

`GET /api/trader-quality-profile` (protegido con `auth()`), declara
explícitamente `"observational": true` y `"affects_decisions": false`.

## Decisiones estadísticas

1. **Unidad de muestra = token, no señal.** Una muestra por `(trader, mint)`:
   el primer outcome `completed` de cada mint. Señales repetidas del mismo
   token no son evidencia independiente. Impacto real medido: `gr3gor14n`
   pasa de 25 outcomes brutos a **2 muestras** reales.
2. **Prior bayesiano, no promedio crudo.** Se reusa el prior neutral ya
   existente (`TRADER_QUALITY_PRIOR_SUCCESSES=8`, `..._FAILURES=12`) para que
   el sistema integral sea coherente con el score vigente. El 15 neutral queda
   solo como prior matemático interno.
3. **Confianza vía intervalo de Wilson**, no vía tamaño de muestra a secas:
   `alta` (ancho ≤ 0.20), `media` (≤ 0.35), `baja`, `insuficiente`.
4. **TP25 antes de SL10 con orden estricto**: `tp25_ts < sl10_ts`. Un TP25 sin
   SL10 cuenta como éxito; un SL10 sin TP25 cuenta como fallo; ninguno de los
   dos, ninguna de las dos cosas.
5. **Ventas parciales agregadas por ciclo.** El market cap de salida se pondera
   por SOL recibido: `Σ(sol_i × mc_i) / Σ(sol_i)`. Cada ciclo aporta **un**
   resultado, no uno por venta parcial.
6. **Consistencia = estabilidad temporal.** `1 - |tasa_primera_mitad -
   tasa_segunda_mitad|`, y solo si hay ≥5 muestras por mitad.
7. **Recencia con vida media de 14 días** (configurable), decaimiento
   exponencial `0.5^(edad/vida_media)`.
8. **Pesos renormalizados sobre componentes disponibles**, para que un dato
   faltante nunca se cuente como un cero.

## Limitaciones conocidas (explícitas, no ocultas)

1. **PnL absoluto: NO disponible, y así se reporta.** `realized_pnl_available:
   false` con razón `POSITION_SIZE_NOT_RECONSTRUCTABLE`. Motivo verificado
   sobre datos reales: los eventos `create` traen `token_amount = 0` en el
   100% de los casos (0 de 284), y `new_token_balance` está presente solo en
   el 70% de las filas. La calidad de salida se mide en market cap relativo a
   la entrada, que sí usa campos presentes al 100% (`sol`, `market_cap_sol`).
2. **`new_token_balance == 0` es ambiguo.** `save_trade()` guarda 0 tanto si
   el trader vendió todo como si el campo no vino en el evento. Por eso el
   cierre de ciclo solo se declara (`closure_confirmed`) cuando antes se
   observó saldo positivo en ese mismo token. En el resto de los casos queda
   como no confirmado, no como cerrado.
3. **Ciclos observados a mitad de vida se descartan.** Si el primer evento que
   vimos de un token ya es una venta, no sabemos a qué precio entró y el ciclo
   se excluye (`skipped_mid_life`). En datos reales: 14 pares (trader,mint) en
   esa condición; 10 de ellos de `epicsealdarkeye`.
4. **Un ciclo por token.** Si un trader sale y vuelve a entrar al mismo token,
   solo se reconstruye el ciclo anclado en la primera entrada observada. Es
   conservador: prefiere descartar antes que inventar.
5. **`graphify update .` no se pudo ejecutar.** El CLI falla con
   `error: uv trampoline failed to canonicalize script path` (también
   `graphify query`). No se reinstaló para no romper una herramienta en uso.
   **Consecuencia: `graphify-out/` está desactualizado respecto a este diff** —
   refleja `5201a0d`. Hay que correrlo cuando el CLI esté reparado.

## Archivos modificados

- `app.py` — constantes del perfil, 15 funciones nuevas, endpoint nuevo, y
  campos `quality_rated` / `quality_display` / `quality_label` agregados a
  `/api/trader-stats` (aditivo: no se quitó ningún campo existente).
- `static/index.html` — la tarjeta de trader muestra **"Sin calificar"** en vez
  de un número cuando no hay evidencia suficiente (1 línea).
- `tests/test_trader_quality.py` — clase nueva `TraderQualityProfileTests`
  (16 tests) preservando la clase existente.
- `.gitignore` — `.obsidian/` (cambio previo, no relacionado).

## Resultados de pruebas

`.venv\Scripts\python.exe -m unittest discover -s tests` → **126 tests, OK**
(0 fallos). Los 16 tests nuevos cubren: muestras insuficientes, tokens
repetidos, ciclos incompletos (mid-life y solo-entrada), ventas parciales,
orden TP25/SL10, ausencia de puntuaciones manuales, PnL no inventado, cierre
ambiguo, concentración, recencia, y que el perfil no alimenta decisiones.

Validación contra la base real (`pumpcopilot.db`, solo lectura):

| Trader | Muestras | Ciclos | Resultado |
|---|---|---|---|
| epicsealdarkeye | 42 | 275 | score 67.0, confianza media |
| gr3gor14n | 2 | 1 | **Sin calificar** |
| marcell | 0 | 0 | **Sin calificar** |
| hdegroot | 0 | 0 | **Sin calificar** |
| supermandev | 0 | 2 | **Sin calificar** |

Solo un trader tiene hoy evidencia suficiente para ser calificado. Los demás
aparecen como "Sin calificar" con métricas en `null`, nunca en 0.

## Qué NO se tocó

- `score_trader()`, `decision_from_score()`, `evaluate_buy()`: sin cambios.
- Controles de live trading, `LIVE_*`, kill switch: sin cambios.
- Binance, bases de datos, datasets, `.env`, secretos, Railway: sin tocar.
- Historial de git: sin reset, sin revert, sin commit, sin push.

---

# Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)

Aprobado explícitamente por el usuario. **Esta parte sí toca la ruta de
decisión y la de dinero real** (a diferencia de la 1ª parte, que era shadow).
Sigue sin commit ni push.

## Diagnóstico: el problema no era la fórmula

El embudo real de evidencia por trader:

| Trader | Evals live | Outcomes c/precio | Muestras (antes) |
|---|---|---|---|
| epicsealdarkeye | 288 | 125 | 42 |
| gr3gor14n | 95 | 19 | 2 |
| marcell | 20 | 2 | 0 |
| supermandev | 2 | 1 | 0 |
| hdegroot | **0** | 0 | 0 |

Tres defectos encontrados:

1. **hdegroot nunca produjo una señal live**: sus 23 evaluaciones son todas
   `demo`. El umbral de 30 muestras era inalcanzable para 4 de 5 traders, así
   que el sistema de medición nunca se activaba para ellos.
2. **Se descartaba el 38% de la evidencia utilizable**: de 82 outcomes
   `expired`, 40 tenían desenlace decidible. `expired` significa "perdimos la
   observación de precio a los 20 min" (el token dejó de operar), no "no hubo
   resultado".
3. **"Sin medir" y "medido como promedio" devolvían el mismo 15**, y ese valor
   pesa hasta 30 de los 100 puntos que deciden un COPY con dinero real.

## Solución implementada (3 partes)

### Parte 1 — Recuperar la evidencia decidible

Nueva constante `DECIDABLE_OUTCOME_SQL`, compartida por la ruta de decisión
(`get_trader_hit_stats`) y la shadow (`get_trader_entry_samples`) para que no
se desincronicen. Un outcome cuenta si `status='completed'` o si es `expired`
pero alcanzó a decidirse (`tp25_ts` o `sl10_ts` presente). Los `active` quedan
fuera a propósito: todavía están mutando.

**Bug latente corregido de paso:** el CTE rankeaba *todas* las filas del mint y
recién después filtraba por status, así que si la primera señal de un token no
estaba completada se perdía el token entero aunque una señal posterior sí lo
estuviera. Ahora se filtra antes de rankear. Test:
`test_first_decidable_signal_per_token_survives_ranking`.

También se cambió el conteo de muestras de `COUNT(max_return)` a `COUNT(*)`:
un outcome decidible es una muestra aunque haya perdido `max_return`. Antes
`target_1` podía superar a `samples`.

**Impacto:** epicsealdarkeye pasa de 42 a **82 muestras**.

### Parte 2 — Encogimiento continuo en vez de escalón

`calculate_trader_quality_candidate()` ya no sustituye el valor por una
constante bajo el mínimo: el posterior Beta se calcula **siempre**. Con cero
muestras da exactamente el neutral (5 + 0.4 × 25 = 15), así que no hace falta
ningún caso especial y desaparece la discontinuidad: antes, una sola muestra
que se completaba en el límite podía mover al trader hasta 8 puntos de golpe.

El mínimo de muestras dejó de decidir *el valor* y ahora decide solo *qué se
publica*: `rated=True` muestra un número, `rated=False` muestra
"Sin calificar". Se agregó el intervalo de Wilson al assessment.

Tests: `test_quality_shrinks_continuously_without_a_cliff` (una muestra más no
puede mover la calidad más de 1 punto) y
`test_zero_evidence_lands_exactly_on_the_neutral_prior`.

### Parte 3 — El dinero real solo sigue a traders medidos

`maybe_execute_live_copy()` ahora exige `rated=True` antes de despachar una
compra real, sumándose al gate del modelo shadow. Nuevo motivo de bloqueo:
`LIVE_TRADER_NOT_RATED` (incluye `trader_samples` y `minimum_samples`).

**El paper trading sigue operando con traders sin calificar** — es la única
forma de acumular evidencia sobre ellos. Si se suprimieran sus señales nunca
se los podría medir. Se aprende en papel, se arriesga solo sobre lo medido.
Test explícito: `test_unrated_trader_still_opens_paper_positions`.

## Efecto medido sobre datos reales

| Trader | Muestras | Calidad antes | Calidad ahora | rated | Dinero real |
|---|---|---|---|---|---|
| epicsealdarkeye | 42 → **82** | 23 | 22 | sí | pasa el gate |
| gr3gor14n | 2 | 15 | 14 | no | `LIVE_TRADER_NOT_RATED` |
| marcell | 0 | 15 | 15 | no | `LIVE_TRADER_NOT_RATED` |
| hdegroot | 0 | 15 | 15 | no | `LIVE_TRADER_NOT_RATED` |
| supermandev | 0 | 15 | 15 | no | `LIVE_TRADER_NOT_RATED` |

Perfil integral shadow de epicsealdarkeye: score 67.0 → **65.2**, confianza
`media` → **`alta`** (intervalo de Wilson [0.65, 0.84], ancho 0.18).

## Decisión de producto pendiente (no es código)

**hdegroot tiene 0 señales live en todo el histórico** y supermandev 2. Son
peso muerto en la watchlist: ocupan un lugar sin aportar evidencia. Eso lo
decide el usuario.

## Archivos modificados en esta parte

- `app.py` — `DECIDABLE_OUTCOME_SQL`, `get_trader_hit_stats()`,
  `get_trader_entry_samples()`, `calculate_trader_quality_candidate()`,
  `get_trader_quality_assessment()`, `maybe_execute_live_copy()`.
- `tests/test_trader_quality.py` — 5 tests nuevos; se reemplazó
  `test_waits_for_enough_completed_samples` (codificaba el escalón que se
  eliminó a propósito) por `test_below_minimum_samples_is_not_rated`.
- `tests/test_risk_controls.py` — 2 tests nuevos y 3 actualizados para el
  nuevo gate de calidad.

## Resultados de pruebas

`.venv\Scripts\python.exe -m unittest discover -s tests` → **133 tests, OK**.

`graphify update .` sigue sin poder ejecutarse (mismo error de trampolín uv),
así que `graphify-out/` continúa desactualizado respecto a este diff.

---

# Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)

Disparado por una observación del usuario: *"yo veo en pump.fun que compran y
venden, me notifica al teléfono"*, mientras el bot mostraba a esos traders sin
señales. Tenía razón: el problema no era que no operaran.

## Qué se encontró

Último evento capturado de cada wallet vigilada:

| Trader | Último evento | Silencio |
|---|---|---|
| epicsealdarkeye | 2026-09-07 20:20 | 54 h |
| gr3gor14n | 2026-09-07 20:24 | 54 h |
| marcell | 2026-09-02 15:43 | 178 h |
| supermandev | 2026-08-27 17:10 | 321 h |
| hdegroot | 2026-08-25 03:31 | 393 h |
| sapphy, FlippingProfits, Cooker, slingoor, Anglio, y22, chriskogias | nunca | — |

**El stream estaba sano hasta el 07/09** (dos wallets seguían entregando),
pero marcell dejó de llegar el 02/09, supermandev el 27/08 y hdegroot el
25/08. Wallets que dejan de entregar mientras la conexión sigue viva y otras
siguen fluyendo.

**El defecto es nuestro:** el sistema alertaba si el stream entero se caía
(`mark_stream_problem`), pero no tenía forma de notar que *una wallet
específica* dejó de entregar. Por eso pasó más de dos semanas sin detectarse.

Dato de apoyo: `Cooker` y `slingoor` tienen 10 y 13 eventos registrados bajo
el prefijo de su wallet en `token_history` en vez de su nombre — o sea que sus
operaciones llegaron por `subscribeTokenTrade` cuando todavía no estaban en
`WATCHED_WALLETS`. Confirma que los eventos existen y el problema es de
suscripción/entrega por cuenta, no de actividad del trader.

### Descartado en el camino

- **No es un error de configuración**: las 14 wallets del `.env` coinciden
  exactamente con las capturadas. Ninguna dirección está mal escrita.
- **No es el filtro de duplicados**: de 35.046 eventos procesados, 33.022 no
  quedaron en `trades`, pero eso es por diseño — son operaciones de terceros
  sobre tokens que seguimos, y sí se guardaron en `token_history` (35.363
  filas). El pipeline funciona.

## Qué se implementó

1. **Tabla `watched_wallet_activity`** (wallet, trader, last_event_ts, events),
   actualizada dentro de `save_trade()` reusando su conexión, sin costo extra
   en el loop del stream. `migrate_database()` la siembra con el historial ya
   capturado para que el monitor arranque con contexto.
2. **`get_watched_wallet_activity()`** — estado de entrega por wallet, con
   antigüedad, `silent` y `never_seen`.
3. **`check_watched_wallet_silence()` + `watched_wallet_monitor()`** — worker
   periódico que alerta por Discord una sola vez por wallet al detectar
   silencio, y avisa la recuperación. **Solo evalúa con el stream conectado**:
   si el stream está caído la alerta correcta es la del stream, no una por
   cada wallet.
4. **Re-suscripción periódica** de `subscribeAccountTrade` dentro del loop
   (`WATCHED_RESUBSCRIBE_SECONDS`, 30 min por defecto): si el proveedor
   descarta la suscripción en silencio, se recupera sola sin esperar a que se
   caiga la conexión.
5. **`GET /api/watched-wallets`** — para verlo sin esperar la alerta.

Umbrales configurables: `WATCHED_WALLET_SILENCE_SECONDS` (24 h),
`WATCHED_WALLET_CHECK_SECONDS` (15 min), `WATCHED_RESUBSCRIBE_SECONDS` (30 min).

## Verificación

Corriendo el monitor contra el historial real: **habría detectado las 14
wallets** — 7 por no haber entregado nunca y 7 por silencios de 2 a 16 días.

Suite completa: **138 tests, OK**. 5 tests nuevos en
`tests/test_stream_monitoring.py` (`WatchedWalletSilenceTests`): wallet en
silencio con stream sano, wallet nunca vista, alerta una sola vez + aviso de
recuperación, sin alertas por wallet cuando el stream está caído, y registro
de entrega desde `save_trade`.

## Advertencia importante sobre los datos

**La base local `pumpcopilot.db` no es la de producción.** `DB_PATH` no está
definido en el `.env` local, así que apunta al archivo del repo; en Railway
usa `/data/pumpcopilot.db` (volumen montado). Esta copia local está congelada
el 2026-09-07 21:54.

Todos los números de análisis anteriores (muestras por trader, silencios)
salen de ese snapshot. **Falta confirmar contra producción**: llamar a
`/api/status` y `/api/watched-wallets` en la app desplegada. Es posible que en
producción el stream siga vivo y los silencios sean distintos.

## Confirmación contra producción (2026-09-10)

URL: `https://web-production-4ea0d.up.railway.app/`

**El bot está vivo y sano**: `stream_connected: true`, último mensaje hace
menos de 3 segundos, sin errores, una sola instancia (verificado muestreando
`pumpportal_balance_checked_ts` 10 veces: un único valor).

Pero el problema **es real en producción**. Estado de entrega por wallet:

| Entregando | Silenciosas |
|---|---|
| Cooker (0.1 h), chriskogias (0.2 h), slingoor (1.6 h), epicsealdarkeye (2.3 h), decu (2.4 h) | gr3gor14n (38.8 h), FlippingProfits (39.2 h), ily (153.7 h), hdegroot (176.8 h), marcell (178.5 h), supermandev (226.5 h), sapphy / Anglio / y22 (nunca) |

### Prueba definitiva: consulta on-chain

Se consultó `getSignaturesForAddress` en Solana para las wallets silenciosas.
**Todas están operando activamente:**

| Trader | Última tx on-chain | Último evento nuestro |
|---|---|---|
| gr3gor14n | hace 0.0 h | 38.8 h |
| sapphy | hace 0.1 h | nunca |
| supermandev | hace 0.3 h | 226.5 h |
| y22 | hace 2.1 h | nunca |
| marcell | hace 3.8 h | 178.5 h |
| hdegroot | hace 9.3 h | 176.8 h |
| Anglio | hace 10.1 h | nunca |

No es que los traders no operen: **no estamos recibiendo sus eventos**.

### Causas descartadas

- **Saldo insuficiente**: 0.034 SOL, por encima del mínimo de 0.02 que exige
  PumpPortal para `subscribeAccountTrade`.
- **Config equivocada**: las 14 direcciones del `.env` coinciden exactamente.
- **Múltiples instancias**: hay una sola (la doc advierte
  *"PLEASE ONLY USE ONE WEBSOCKET CONNECTION AT A TIME"*, pero no es el caso).
- **Unsubscribe mal formado**: los 3 call sites de `TOKENS_TO_UNSUBSCRIBE`
  pasan mints, nunca wallets.
- **Filtro de duplicados**: descartado antes, el pipeline funciona.

### Hipótesis principal

`subscribeAccountTrade` probablemente **no está entregando nada**, y todo lo
que vemos de wallets vigiladas llega de rebote por `subscribeTokenTrade`.

El código marca un evento como "de trader vigilado" comparando la wallet, sin
importar por qué suscripción llegó (`app.py`, rama `is_watched_wallet`). Por
eso los 5 que "funcionan" son justamente los que operan los tokens que más
seguimos —epicsealdarkeye crea la mayoría—, y los 9 silenciosos operan tokens
que no seguimos.

**No se pudo confirmar del todo** porque haría falta abrir una segunda
conexión de prueba con la misma API key, y la documentación lo desaconseja
explícitamente. No se hizo para no perjudicar al bot en producción.

### Observabilidad agregada para cerrar el diagnóstico

`PUMPPORTAL_MESSAGE_LOG` (deque de 50) + campo `provider_messages` en
`/api/watched-wallets`. Antes solo se guardaba el **último** mensaje del
proveedor, así que la respuesta a `subscribeAccountTrade` se perdía apenas
llegaba cualquier otro mensaje: en producción se ve `"Unsubscribed."`, que es
la respuesta a un unsubscribe de tokens, no al subscribe de cuentas.

Con esto, apenas se despliegue, el log muestra qué contesta PumpPortal al
suscribir cuentas y se confirma o descarta la hipótesis.

### Próximo paso recomendado

Desplegar este diff y mirar `/api/watched-wallets`:

- si `provider_messages` muestra un error en la suscripción de cuentas → es
  eso, y hay que resolverlo con PumpPortal;
- si la suscripción se acepta pero las wallets siguen silenciosas → la
  re-suscripción periódica (cada 30 min) debería recuperarlas, y si no, hay
  que escalarlo al proveedor con esta evidencia.

En cualquiera de los dos casos, la alerta de silencio por wallet hace que esto
no vuelva a pasar dos semanas sin que nadie lo note.

## RESULTADO EN PRODUCCIÓN: causa confirmada (2026-09-10 02:30)

Commit `953a61b` desplegado. El log de mensajes del proveedor respondió la
pregunta y una prueba en vivo cerró el diagnóstico.

**PumpPortal acepta la suscripción de cuentas y no entrega los eventos.**

Secuencia verificada:

| Hora | Hecho |
|---|---|
| 02:23:14 | La app arranca y suscribe. PumpPortal responde **"Successfully subscribed to keys."** (dos veces: cuentas y tokens) |
| 02:25:51 | `gr3gor14n` opera on-chain |
| 02:30:09 | `sapphy` opera on-chain |
| 02:24 → 02:30 | Monitoreo en vivo: **cero eventos** capturados de `gr3gor14n`, `sapphy`, `supermandev` y `marcell` |

O sea: suscripción fresca, aceptada explícitamente por el proveedor, wallets
operando durante la ventana, y ningún evento entregado.

**Conclusión: no es un bug nuestro y la re-suscripción periódica no lo
resuelve** (quedó probado: la del arranque es lo más fresco posible y no
cambió nada). Sirve igual como red de seguridad ante una suscripción caída,
pero no ataca esta causa.

Sigue en pie la hipótesis de que los 5 que sí entregan lo hacen de rebote por
`subscribeTokenTrade`: son los que operan los tokens que más seguimos
(epicsealdarkeye crea la mayoría, y Cooker / slingoor / chriskogias / decu
operan esos mismos tokens).

### Lo que sí funciona: la alerta

El arreglo desplegado ahora **hace visible el problema**: `/api/watched-wallets`
reporta 9 silenciosas y 3 nunca vistas, y la alerta de Discord avisará en
cuanto una wallet cruce las 24 h. Eso era lo que faltaba para que no volviera a
pasar dos semanas inadvertido.

### Workaround propuesto (a decidir)

Las suscripciones de **tokens** funcionan bien; las de **cuentas** no. Y la
consulta on-chain vía `getSignaturesForAddress` funcionó perfecto en cada
verificación de este análisis.

Propuesta: **un worker que sondee las wallets vigiladas por RPC de Solana**
como fuente de respaldo, e inyecte los trades faltantes por el mismo camino
que hoy usa el stream (respetando `processed_signatures` para no duplicar).
14 wallets × 1 llamada cada N segundos es barato.

Contras a sopesar: agrega dependencia del RPC, hay límites de tasa en el
endpoint público de Solana, y la latencia sería mayor que la del stream
(sondeo vs. push), lo que importa para señales de copytrading.

Alternativa previa: reclamarle a PumpPortal con esta evidencia, que es
concreta y reproducible.

## Monitor RPC fallback — baseline y diagnóstico (2026-09-10)

- `rpc_fallback_wallet_state` ahora conserva `baseline_ts`, independiente de `last_polled_ts`. La migración siembra la línea de base una sola vez para estados existentes; los estados nuevos la fijan al crearse.
- Los eventos con `block_time` anterior a la línea de base se conservan y pasan a `discarded_prebaseline`; son terminales y no cuentan como `missing`.
- La reconciliación mantiene el match exacto por firma y expone diagnóstico aproximado por wallet/mint/lado y una ventana de ±5 minutos. Esto ayuda a separar formato de firma de falta real de entrega.
- `decu` conserva el comportamiento acotado: si el backlog satura el límite, se rebasa al encabezado y no se pagina exhaustivamente.
- Observación B: la instrumentación existente medía eventos parseados (6/90), pero no separaba transacciones Pump reales de otras transacciones. Este diff agrega el diagnóstico aproximado; queda pendiente medir esa separación con datos de producción, porque la copia local no representa el RPC productivo.

### Revisión y correcciones (Claude, sobre el diff anterior)

- **`approximate_matches` estaba inflado.** La consulta usaba `COUNT(*)` sobre
  un JOIN: con la tolerancia de ±300 s una misma observación empareja con
  varias operaciones del stream sobre el mismo token, y cada par sumaba uno.
  Corregido a `COUNT(DISTINCT rpc.id)` — lo que se mide es cuántos eventos
  tienen equivalente, no cuántos pares existen. Verificado sobre el mismo
  dataset: `COUNT(*)` devolvía 2 donde el valor correcto es 1. Importa porque
  la métrica existe justamente para diagnosticar, y venía sesgada al alza.
- **Faltaban tests de la lógica nueva.** `baseline_ts` y
  `discarded_prebaseline` son reglas de corrección de datos que quedaban
  dependiendo del orden de los UPDATE en `reconcile_rpc_fallback_events()`.
  Se agregó `RpcFallbackBaselineTests` con cuatro casos:
  - evento anterior a la línea de base → `discarded_prebaseline`, fuera de `missing`;
  - evento posterior a la línea de base → sigue contando como `missing` (contrapeso, para que la guarda no descarte de más);
  - un evento descartado **nunca** vuelve a `matched`, aunque después aparezca su firma en `trades`;
  - `approximate_matches` cuenta eventos y no pares (bloquea la regresión del punto anterior).
- Suite completa: **152 tests, OK** con `.venv\Scripts\python.exe`.

Sigue pendiente lo de fondo: **`matched: 0` en producción**. Hasta que aparezca
al menos un match real (o un `approximate_match`) de una wallet de control, no
se puede distinguir "PumpPortal pierde todo" de "el comparador no empareja".
El diagnóstico aproximado que se agregó es justamente lo que permitirá
separarlos cuando haya datos.

---

# Evaluación del streaming de Helius y arreglo del parser — 2026-09-10

## Por qué se evalúa

Con 9 horas de datos limpios, el monitor RPC midió una **tasa de pérdida del
80,4%** de PumpPortal: 448 operaciones observadas on-chain, 88 entregadas, 360
no entregadas. De las 360, solo **1** tenía coincidencia aproximada, lo que
descarta que sea un problema de formato de firma: sencillamente no llegaron.

Las pérdidas incluyen wallets del grupo que "funcionaba bien" (`slingoor`,
`epicsealdarkeye`, `decu`), así que no se resuelve eligiendo mejor la
watchlist.

## Opciones evaluadas

| Opción | Costo | Veredicto |
|---|---|---|
| LaserStream gRPC | $499/mes (plan Business) | Descartado para posiciones de $5 |
| Enhanced WebSockets (`transactionSubscribe`) | Desde $49/mes (Developer) | Viable |
| **Webhooks** | Plan gratuito, 1 crédito por push | **Mejor encaje** |

Los webhooks además eliminan el problema estructural que sufrimos con
PumpPortal: no hay conexión persistente que se caiga en silencio.

## Los tres puntos verificados

**1. Payload — sirve, con una trampa que ya se corrigió.** Los webhooks raw
entregan `meta.logMessages`, `meta.pre/postTokenBalances`, `meta.err`,
`transaction.signatures` y `transaction.message.accountKeys`: todo lo que el
parser necesita. Pero usan el **formato nativo** de Solana, donde
`accountKeys` es una lista de strings, mientras que el parser esperaba los
diccionarios con `pubkey`/`signer` de `jsonParsed`. Tal como estaba habría
descartado **cada transacción en silencio**.

**2. Créditos — alcanzan sin `decu`.** Volumen real medido contra la
blockchain (muestra de 100 firmas por wallet):

| Trader | tx/mes |
|---|---|
| slingoor | 69.865 |
| Cooker | 27.082 |
| epicsealdarkeye | 20.942 |
| chriskogias | 16.161 |
| gr3gor14n | 11.093 |
| y22 | 9.949 |
| resto (7 wallets) | ~12.000 |
| **Total sin decu** | **~167.000** |

Contra 1.000.000 de créditos gratuitos mensuales: **17% de uso, seis veces de
margen**. `decu` es la excepción — más de 100 transacciones en menos de 40
segundos; sola no entra en ningún plan razonable. Misma conclusión que con la
paginación: excluirla o muestrearla.

**3. Latencia — sigue sin verificar.** Es el dato que decide si se recupera la
ventaja y no se puede medir sin un piloto real. Pendiente: registrar un
webhook con una wallet de bajo volumen (`Anglio` o `hdegroot`, ~700 tx/mes) y
comparar el retraso `blockTime` → llegada contra lo que tarda PumpPortal en
las operaciones que sí entrega.

## Cambio implementado

`_is_signed_by()` en `solana_rpc_fallback.py` reemplaza el chequeo inline y
acepta ambas codificaciones: diccionarios con `pubkey`/`signer`, o strings en
base58 tomando como firmantes los primeros `header.numRequiredSignatures`.

Tests nuevos (`AccountKeyEncodingTests`, 4 casos): formato nativo parsea,
`jsonParsed` sigue funcionando, una wallet presente pero fuera de los
firmantes se rechaza, y un mensaje nativo sin `header` se rechaza en vez de
asumir.

Suite completa: **156 tests, OK**. `graphify update .` ejecutado (710 nodos).

## Recomendación de arquitectura

**Sumar, no reemplazar.** Correr Helius y PumpPortal en paralelo: la
deduplicación por firma ya existe (`mark_signature_processed` sobre
`processed_signatures`), así que un trade que llegue por ambas vías se
descarta solo. Riesgo de migración cero, sin ventana de corte, y si PumpPortal
se arregla queda como redundancia.

---

# Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10

La auditoría original marcó como prioridad Media que `db()` (`app.py`) recrea
la conexión SQLite y corre 16 `CREATE TABLE IF NOT EXISTS` más 5
`CREATE INDEX` en cada llamada, dentro del loop del stream. **La medición no
respalda esa prioridad.**

Benchmark (200 llamadas sobre base temporal):

| | |
|---|---|
| `db()` completo | 2,38 ms |
| Solo abrir conexión + PRAGMAs | 2,01 ms |
| **Sobrecarga atribuible al esquema** | **0,37 ms (16%)** |

Sacar el DDL del camino caliente ahorraría ~0,37 ms por llamada, unos 2 ms por
evento del stream. Real, pero marginal.

El costo dominante son los 2 ms de abrir la conexión, y eso solo se resuelve
reutilizando conexiones. Con SQLite más los `asyncio.to_thread` de los workers
de reconciliación, esa es exactamente la clase de cambio que introduce errores
sutiles de concurrencia en el sistema que va a mover dinero. Para posiciones de
$5 el riesgo no se justifica.

**Decisión: no se implementa.** Queda registrado para que no vuelva a aparecer
como pendiente. Si algún día el volumen crece un orden de magnitud, conviene
re-medir antes que asumir.

Nota metodológica: la prioridad original se asignó leyendo el código, sin
medir. El número la desmintió.

---

# Piloto del webhook de Helius — resultados, 2026-09-11

El piloto funcionó de punta a punta. Las tres preguntas abiertas quedaron
respondidas.

## Latencia: parejos en promedio, muy distintos en dispersión

| | Muestras | Promedio | Mínimo | Máximo |
|---|---|---|---|---|
| Helius webhook | 2 | 3,76 s | 3,45 s | 4,06 s |
| PumpPortal | 88 | 3,32 s | 0,38 s | **39,28 s** |

Con dos muestras hay que tomarlo con pinzas, pero el promedio es equivalente y
la dispersión favorece al webhook: PumpPortal baja a 0,38 s en el mejor caso y
se va a **39 segundos** en el peor. Para decidir una compra, un promedio
parecido con menos varianza vale más que un promedio apenas menor con colas de
39 segundos.

## Entrega

`parsed_only_in_webhook: 2` — las dos operaciones Pump que capturó el webhook,
PumpPortal no las entregó. Dos de dos. Muestra chica, pero coherente con el
80,4% de pérdida medido durante 9 horas.

## Qué quedó verificado

- **El parser funciona con el formato nativo.** Se comprobó pidiendo la misma
  transacción (`4xuzhSnNq9PURN...`) en ambas codificaciones: `jsonParsed`
  devuelve `accountKeys` como diccionarios, el webhook los manda como strings
  con `header.numRequiredSignatures`, y `_is_signed_by()` resuelve las dos.
- **El filtrado es correcto.** De cinco transacciones analizadas con wallets
  vigiladas presentes, solo una parseó: las otras cuatro fueron rechazadas por
  buenos motivos — la wallet aparecía pero no firmó (posiciones 15 y 18 de
  `accountKeys`), o la transacción no invocaba los programas de Pump.
- **La autenticación funciona**: secreto incorrecto devuelve 401, correcto 200.
- **Los webhooks entregan transacciones donde la wallet está involucrada sin
  firmar**, e incluso algunas sin ninguna wallet vigilada. El parser las
  descarta bien, pero se pagan créditos igual.

## Falso diagnóstico corregido en el camino

Durante 14 minutos llegaron 20 transacciones con 0 parseadas y pareció un
problema de formato. No lo era: `gr3gor14n` no estaba operando en pump.fun
(0 de 10 de sus transacciones tocaban los programas de Pump). Esto además
**resuelve la observación B** que venía abierta desde el monitor RPC: el parser
no es demasiado estricto, simplemente la mayoría de las transacciones de estas
wallets no son de pump.fun.

Consecuencia de costo: los webhooks raw cobran **por transacción, no por
operación Pump**. Se paga por todo el ruido. La estimación de 167.000 tx/mes ya
contaba todas las transacciones, así que el cálculo del plan gratuito sigue en
pie, pero la eficiencia real es baja. Si algún día el volumen aprieta, el
`transactionSubscribe` del plan Developer permite filtrar por programa.

## Estado del saldo de SOL (2026-09-11)

La wallet de PumpPortal quedó en **0,0141 SOL**, por debajo del mínimo de 0,02
que exige `subscribeAccountTrade`. Por ahora la suscripción sigue viva, pero en
la próxima reconexión puede ser rechazada.

**No bloquea el trabajo**: el webhook se paga con créditos de API, el monitor
RPC lee gratis, y el paper trading es simulado. Lo único en riesgo es la fuente
que ya pierde el 80%. Sí se pierde el **grupo de control** para seguir midiendo
la comparación.

Nota: se observaron movimientos en esa wallet (el más reciente 7,3 h antes de
esta medición) con el live trading apagado y la función de compra real sin
llamarse desde ningún lado. El bot no puede ser la causa; queda para que el
usuario confirme que los reconoce.

---

# Hook de pre-push para el camino del dinero — 2026-09-11

`scripts/hooks/pre-push`, versionado en el repo para que aplique a cualquiera
que trabaje sobre esta copia, no solo a quien lo instaló.

**Instalación** (una vez por copia del repo):

    git config core.hooksPath scripts/hooks

## Qué hace

Frena un push a `main` cuando el diff toca funciones o constantes que deciden
si se gasta dinero real, o cuánto: los `execute_pumpportal_lightning_*`,
`maybe_execute_live_copy`, `require_live_trading`, `risk_check`, los
`record_finalized_*`, la lógica de salida de posiciones live, el kill switch, y
las constantes `LIVE_*`, `MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`,
`MAX_SLIPPAGE_PCT`.

Railway despliega desde `main`, así que un push a esa rama llega a producción
sin escala intermedia.

**No revisa el código ni llama a ningún servicio.** Solo detecta y frena; la
revisión queda como acción deliberada aparte. Esto es a propósito: un push que
dependa de que haya red o de que una herramienta externa responda falla por
motivos que no tienen nada que ver con el riesgo que intenta cubrir.

Salida de escape, cuando el cambio es deliberado y ya está revisado:

    ALLOW_RISK_PUSH=1 git push

## Verificación

Probado contra historia real del repo:

| Caso | Resultado |
|---|---|
| `c3b0844` (solo `CLAUDE_NOTES.md`) | permitido |
| `d0a6e28` "Connect COPY decisions to guarded live buys" | **frenado**, listando `execute_pumpportal_lightning_buy`, `maybe_execute_live_copy`, `LIVE_BUYS_ENABLED`, `LIVE_BUY_USD`, `MAX_POSITION_USD` |
| Mismo commit con `ALLOW_RISK_PUSH=1` | permitido |
| Rama distinta de `main` | permitido (no despliega) |

El segundo caso es el que justifica el hook: ese commit conectó la decisión
COPY con la compra real y se subió directo a `main` sin revisión previa.

---

# Scoring dinámico de traders apagado — 2026-09-11

`TRADER_DYNAMIC_QUALITY_ENABLED=false` en Railway. Verificado: los 14 traders
volvieron al prior neutral de 15.

## Por qué

El scoring no estaba midiendo la calidad del trader. Estaba midiendo **qué tan
bien lo entrega PumpPortal**.

| Trader | Muestras | Calidad | ¿Lo entrega PumpPortal? |
|---|---|---|---|
| epicsealdarkeye | 149 | 23 | sí |
| decu | 142 | 27 | sí |
| Cooker | 54 | 17 | sí |
| chriskogias | 17 | 17 | parcial |
| slingoor | 6 | 17 | parcial |
| gr3gor14n | 4 | 14 | no (39 h de silencio) |
| marcell | 0 | 15 | no (178 h) |
| supermandev | 0 | 15 | no (226 h) |
| hdegroot | 1 | 16 | no (177 h) |

Los tres con más muestras son exactamente los tres que PumpPortal entrega bien.
Las muestras salen de `signal_outcomes`, que se alimenta del stream, y el
stream pierde el 80%. Un trader cuyas operaciones no llegan nunca puede
acumular evidencia, así que se queda en el neutral por más que opere bien.

Eso es un confundidor, no ruido: la variable medida (calidad) está
sistemáticamente correlacionada con una variable ajena (tasa de entrega). Con
el dinámico encendido, `score_trader()` aportaba hasta 30 de los 100 puntos que
deciden un COPY, usando ese ranking sesgado.

## Por qué apagarlo y no ajustarlo

Con el dinámico apagado todos valen 15, que es la posición honesta: no sabemos.
Parece un retroceso pero no lo es — el ranking anterior no era "mejor que nada",
era información sesgada por un confundidor recién descubierto. Estar seguro de
algo equivocado es peor que no diferenciar.

## Cuándo volver a encenderlo

Cuando el feed de datos esté completo **y** se haya subido `DATA_VERSION`, para
que las muestras del régimen incompleto no se mezclen con las nuevas. Recién
ahí el número va a medir lo que dice medir.

Nota sobre `decu`, que encabezaba con 27: es la wallet de volumen extremo
(1000+ transacciones cada 90 s) que se excluyó del webhook. Sus 142 muestras
reflejan volumen y buena entrega más que acierto.

---

# Ablación de features — paso 0 antes del backfill, 2026-09-11

Ejecutada a pedido de Codex antes de autorizar cualquier backfill. Sin entrenar,
guardar ni promover ningún modelo. Producción sin tocar.

## Cambio en el script

Se agregó la ablación aislada `without_token_age` a
`scripts/analyze_feature_ablation.py`. La existente `without_market_context`
quita tres features a la vez (`market_score`, `market_cap`,
`token_age_seconds`), así que no permitía atribuir nada a la antigüedad del
token.

## Dato de partida corregido

El export local tenía **4 filas** (obsoleto, del 8/09). Producción tiene
**1368**. Ni los 162 que yo citaba ni los 727 de Codex eran el número actual —
tercera vez en el trabajo que una copia local desactualizada da una cifra
equivocada.

## Hallazgo principal: el modelo solo apuesta a un trader

En las cinco configuraciones, sin excepción:

| Trader | Holdout | Positivos reales | Seleccionados | Aciertos |
|---|---|---|---|---|
| decu | 180 | 76 | **37** | 37 |
| Cooker | 37 | 4 | 0 | 0 |
| slingoor | 27 | 2 | 0 | 0 |
| gr3gor14n | 25 | 0 | 0 | 0 |
| epicsealdarkeye | 5 | 4 | 0 | 0 |

La precisión de 1.000 no indica calidad: el modelo aprendió a apostar
exclusivamente a `decu`, que ocupa el 66% del holdout y tiene 42% de tasa base.
Con el umbral en 0,84 se queda con sus mejores casos y nunca se equivoca porque
nunca sale de ahí.

El detalle más elocuente: `epicsealdarkeye` tiene 4 aciertos reales de 5 filas
—la mejor tasa del conjunto— y el modelo selecciona **cero**.

Coherente con el bloqueador que ya reportaba el propio script:
`holdout_largest_group_share 0.354/0.250`.

## Respuesta sobre token_age_seconds

| Configuración | Prec | Recall | F1 | AUC | Selec | Neto |
|---|---|---|---|---|---|---|
| actual completo | 1.000 | 0.430 | 0.602 | 0.769 | 37 | 8,51 |
| actual sin token_age | 1.000 | 0.430 | 0.602 | 0.754 | 37 | 8,51 |
| actual sin mkt context | 1.000 | 0.407 | 0.579 | 0.753 | 35 | 8,05 |
| candidato completo | 1.000 | 0.407 | 0.579 | 0.771 | 35 | 8,05 |
| candidato sin token_age | 1.000 | 0.407 | 0.579 | 0.760 | 35 | 8,05 |

Quitarlo no cambia nada en el punto de operación: mismas decisiones, misma
economía. Solo baja levemente el AUC, o sea que aporta al ordenamiento pero no
a la decisión. Es el caso "queda igual".

**Por qué:** el modelo ya tiene la identidad del trader como feature, y el nulo
de `token_age_seconds` es casi un alias de esa identidad.

## Tasa de ausencia: el nulo como proxy de identidad

1261 nulos de 1368 (**92,2%**).

| Trader | Filas | Nulos | target=1 |
|---|---|---|---|
| epicsealdarkeye | 81 | **1,2%** | 60,5% |
| decu | 695 | 99,4% | 42,2% |
| Cooker | 297 | 92,6% | 18,5% |
| slingoor | 110 | 100% | 10,9% |
| gr3gor14n | 102 | 100% | 1,0% |
| chriskogias | 81 | 100% | 21,0% |

Con el dato presente el target es 1 en **53,3%** de los casos; sin él, en
**29,5%**. La ausencia informa más que el valor — pero lo que informa es quién
operó, no la antigüedad del token.

**Causa estructural confirmada:** `build_model_features()` busca el evento
`create` dentro de `trades`, y esa tabla solo guarda operaciones de wallets
vigiladas. El dato existe únicamente cuando un trader vigilado creó el token
que opera — que es lo que hace `epicsealdarkeye` y casi nadie más.

## Conclusión para el backfill

La hipótesis de Claude —que el backfill valía porque completaría un feature
roto— **queda descartada**: ese feature no mueve la aguja.

El argumento de Codex se refuerza: el backfill vale por **diversidad**, no por
volumen ni por completar features. Mientras el dataset sea 66% de un trader,
ningún modelo va a aprender a operar; va a aprender a reconocer a ese trader.

Y hay un detalle incómodo: `decu` es la wallet que excluimos del webhook por
volumen extremo, y una de las que PumpPortal sí entrega bien. El mismo
confundidor que apareció en el scoring de traders reaparece acá — el modelo se
apoya en quien mejor está representado, que es quien mejor se entrega.

## Corrección de la ablación — prueba de identidad, 2026-09-11

Codex objetó cuatro puntos del informe anterior. **Tres eran errores míos.**

**1. El blocker mal interpretado.** `holdout_largest_group_share 0.354/0.250`
mide concentración por **mint** (`split_group = mint`), no la participación de
`decu`. Usé ese número para respaldar una afirmación sobre concentración de
trader. La concentración de decu (66% del holdout) es real, pero ese blocker no
la respalda.

**2. "Token age no aporta nada" era demasiado absoluto.** Lo correcto: no
cambió ninguna decisión ni la economía en este holdout y con este umbral. El
AUC sí bajó (0.769 → 0.754), así que conserva información de ordenamiento.

**3. "El modelo aprendió a reconocer a decu" no estaba demostrado** — y al
probarlo, resultó falso.

**4. Faltaba reproducibilidad.** Corregido: `exports/ablation_report_2026-09-11.json`
guarda comandos exactos, checksum SHA-256 del dataset (1368 filas), timestamp
del export y commit.

### Resultado de la prueba de identidad

| Configuración | Prec | Recall | F1 | AUC | Selec | Neto |
|---|---|---|---|---|---|---|
| all_features | 1.000 | 0.430 | 0.602 | 0.769 | 37 | 8,51 |
| sin identidad | 1.000 | **0.465** | **0.635** | 0.718 | 40 | **9,20** |
| sin token_age | 1.000 | 0.430 | 0.602 | 0.754 | 37 | 8,51 |
| sin identidad + token_age | 0.952 | 0.465 | 0.625 | 0.704 | 42 | 8,96 |

**Quitar la identidad del trader no derrumba el rendimiento: lo mejora
levemente** en el punto de operación. El modelo no está memorizando quién
opera.

### Pero la concentración sigue siendo el problema, por otra vía

Sin identidad, el modelo **sigue eligiendo casi solo a decu**: 39 selecciones
suyas y 1 de Cooker. Sin saber quién es, lo elige igual — los features llevan
su firma operativa.

Y al compensar la concentración con una ponderación diagnóstica por trader
(`inverse-trader`, agregada a `train_baseline_model.py` solo para esto):

| Configuración | Ponderación | Recall | Selec | AUC |
|---|---|---|---|---|
| all_features | inverse-group (mint) | 0.430 | 37 | 0.769 |
| all_features | inverse-trader | 0.163 | 14 | **0.839** |
| sin identidad | inverse-group | 0.465 | 40 | 0.718 |
| sin identidad | inverse-trader | 0.012 | **1** | **0.854** |

Cuando se deja de dejar que decu domine el peso del entrenamiento, el modelo
**casi deja de operar** — pero su ordenamiento mejora (AUC 0.854, el mejor de
todos).

### Conclusión revisada

- El modelo **no** explota la etiqueta de identidad.
- Sí depende de la sobre-representación de decu: los features codifican su
  patrón operativo, y el rendimiento en el punto de operación se sostiene
  sobre esa concentración.
- El AUC más alto con datos balanceados sugiere que el ordenamiento es mejor
  de lo que parece, y que lo que se rompe al balancear es la **selección de
  umbral**, que queda ultraconservadora.

El diagnóstico de fondo de Codex se confirma por un camino distinto al que yo
había propuesto: **el problema es diversidad, no identidad ni features
faltantes.**

---

# Conectar el webhook al pipeline — brief para Codex, 2026-09-11

## Situación

**PumpPortal dejó de entregar por completo hace ~21 horas.** Las 14 wallets,
incluidas las cinco que venían funcionando (decu 20,7 h, gr3gor14n 20,8 h,
slingoor 20,9 h, Cooker 21,2 h, epicsealdarkeye 21,7 h). El stream sigue
conectado respondiendo *"Successfully subscribed to keys."*

Causa probable, no confirmada: el saldo de la wallet cayó a 0,0141 SOL, por
debajo de los 0,02 que PumpPortal exige para `subscribeAccountTrade`. El
momento calza pero ellos no informan nada.

**El bot no captura una sola señal desde entonces.** Sin eventos de wallet no
hay `save_trade`, sin eso no hay evaluaciones, ni outcomes, ni paper trading.

## Evidencia acumulada

| | |
|---|---|
| Tasa de pérdida del stream (monitor RPC) | **86,5%** (566 de 654) |
| Operaciones Pump capturadas por el webhook | 116 |
| De esas, **no entregadas** por PumpPortal | **116 de 116** |
| `matched` nuevo desde el apagón | 0 |

Latencia, ahora con muestra sólida:

| Fuente | Muestras | Promedio | Máximo |
|---|---|---|---|
| Helius webhook | 116 | 4,10 s | **8,33 s** |
| PumpPortal | 88 | 3,32 s | **39,28 s** |

Helius es 0,78 s más lento en promedio y **4,7× más ajustado en la cola**.

**Dato clave para la decisión:** el 80% de pérdida se midió *mientras la wallet
estaba financiada* (0,034 SOL). Recargar no devuelve un servicio sano; devuelve
uno que pierde 4 de cada 5 operaciones. Recargar cuesta 35 Bs y es opcional.

## LA DEPENDENCIA CRÍTICA (revisar antes de estimar)

El webhook vigila **wallets**. Pero el stream tiene dos ramas, y la segunda es
la que sostiene todo el aprendizaje:

- **Rama de wallet vigilada** (`app.py:10394`): `save_trade()` → `evaluate_buy()`
  → crea `signal_outcomes` con `price_at_signal`.
- **Rama de token trackeado** (`app.py:10523` en adelante):
  `process_signal_outcomes_event()`, `save_token_history()`,
  `update_paper_position()`, `evaluate_live_position_exit()`.

`process_signal_outcomes_event()` es lo que llena `price_10s`, `price_30s`,
`price_1m`, `price_5m`, `price_15m`. **Sin eventos de token, los outcomes se
crean pero nunca se completan** — expiran a los 20 minutos, y sin outcomes
completados no hay dataset de entrenamiento, ni muestras de calidad de trader,
ni muestras para el modelo shadow.

Además, `update_paper_position()` y `evaluate_live_position_exit()` también
dependen de esa rama: sin ella, las posiciones abiertas no se actualizan y los
stop-loss de posiciones reales no se evalúan.

**Consecuencia:** conectar el webhook solo para wallets restaura las señales
pero no sus resultados. Para función completa hace falta también recibir
eventos de los tokens trackeados — y `TRACKED_TOKENS` cambia dinámicamente con
cada señal nueva y cada posición que se abre o cierra.

Helius permite editar la lista de direcciones de un webhook por API, así que es
viable, pero implica gestión dinámica de direcciones, límites de edición, y un
conjunto de tokens que rota constantemente. **Eso expande el alcance más allá
de "conectar el parser al pipeline".**

## Lo que ya existe

- `parse_watched_wallet_pump_events()` en `solana_rpc_fallback.py` produce
  exactamente los campos que consume el pipeline (`marketCapSol`,
  `vSolInBondingCurve`, `solAmount`, `txType`, `newTokenBalance`, `pool`).
  Verificado contra ambas codificaciones (`jsonParsed` y nativa).
- `POST /api/helius-webhook` recibe, autentica con secreto compartido y
  registra. Apagado salvo `HELIUS_WEBHOOK_ENABLED=true`.
- `record_helius_webhook_transactions()` parsea y guarda en
  `helius_webhook_events`. **Hoy solo mide: no llama a `save_trade()`.**
- Deduplicación por firma: `mark_signature_processed()` sobre
  `processed_signatures`. Permite correr ambas fuentes en paralelo.
- 13 wallets registradas (todas menos `decu`, excluida por volumen extremo:
  1000+ transacciones cada 90 s). Consumo actual ~5% del plan gratuito.

## Riesgos a considerar

1. **Orden de llegada.** El stream procesa en orden; los webhooks pueden llegar
   desordenados. `evaluate_buy()` calcula `consensus_trader_count_30s` y
   `trader_recent_buy_count_60s` leyendo `trades`, así que insertar fuera de
   orden produce features calculadas sobre historia incompleta.
2. **Deduplicación antes, no después.** Si ambas fuentes entregan, el descarte
   tiene que ocurrir antes de `save_trade()`.
3. **`DATA_VERSION` debe subir a 3 en el mismo cambio.** Si no, el régimen
   incompleto se mezcla con el nuevo. El proyecto ya usa ese mecanismo para
   filtrar el dataset y rechazar modelos de otra versión.
4. **La transición de observacional a activo** es el momento delicado: hoy el
   webhook no puede causar daño porque no alimenta nada.

## Restricciones

- Live trading sigue bloqueado y no debe tocarse.
- `TRADER_DYNAMIC_QUALITY_ENABLED` sigue en `false` a propósito.
- El hook de pre-push va a frenar cambios que toquen el camino del dinero;
  usar `ALLOW_RISK_PUSH=1` solo tras revisión.
- Suite completa con `.venv\Scripts\python.exe`; hoy 161 tests OK.

---

# El scoring sí funciona — y dónde no, 2026-09-11

Investigación disparada por una discrepancia: los outcomes muestran 31,4% de
acierto (429 de 1368) y las posiciones paper 94% (16 de 17). Sesenta puntos de
diferencia sobre el mismo motor y el mismo mercado.

## No era sesgo de supervivencia

Primera hipótesis: que las posiciones sobre tokens muertos quedaran abiertas
para siempre y nunca contaran como pérdida. **Descartada por los datos**: 17 de
17 posiciones cerradas, cero abiertas.

## La explicación: el scoring separa de verdad

| Tramo de score | Filas | Acierto |
|---|---|---|
| SKIP (<60) | 901 | **23,0%** |
| WATCH 60-69 | 318 | 39,3% |
| WATCH 70-79 | 132 | 60,6% |
| COPY (≥80) | 17 | **100%** |

Las posiciones paper solo se abren con COPY, y COPY acierta mucho más que el
promedio. La discrepancia no era un artefacto: era selección funcionando.

## Y no es el confundidor de decu

Dado el historial de esta sesión —dos confundidores encontrados por
concentración— se verificó si la relación existe **dentro** de cada trader:

| Trader | <60 | 60-69 | 70-79 | ≥80 |
|---|---|---|---|---|
| decu | 31,1% (485) | 66,9% (136) | 60,3% (58) | 100% (16) |
| epicsealdarkeye | 14,3% (21) | 66,7% (9) | **78,0% (50)** | 100% (1) |
| Cooker | 15,9% (226) | 23,1% (65) | 66,7% (6) | — |
| chriskogias | 11,3% (62) | 55,6% (18) | — | — |
| slingoor | 11,7% (77) | 6,2% (32) | 100% (1) | — |
| **gr3gor14n** | **0,0% (29)** | **1,7% (58)** | **0,0% (15)** | — |

Excluyendo a decu por completo (673 filas): 13,5% → 18,7% → **60,8%** → 100%.
La relación se sostiene.

**El score tiene señal propia.** Es el primer hallazgo sólidamente positivo del
trabajo: todo lo demás que investigamos resultó ser un confundidor.

## Los dos límites reales

**1. COPY es, en la práctica, un evento de un solo trader.** De 17 señales
COPY históricas, **16 son de decu** y 1 de epicsealdarkeye. El sistema casi
nunca dice COPY para nadie más, así que el 94% de las posiciones paper es "los
mejores 16 momentos de decu" — real, pero angosto.

**2. `gr3gor14n` no aporta nada.** 0,0% / 1,7% / 0,0% a lo largo de 102
muestras. No es que el score no lo prediga: casi nunca acierta en ningún
tramo. Copiarlo no tiene valor en este marco.

## Consecuencia para el backfill y la watchlist

Refuerza "diversidad, no volumen", con un matiz nuevo: **no toda la diversidad
sirve**. Sumar muestras de `gr3gor14n` no va a mejorar nada.

Lo que hace falta son traders con señal propia, como `epicsealdarkeye` en el
tramo 70-79: **78% de acierto sobre 50 muestras**, el dato no-decu más sólido
del conjunto. Ese perfil es el que conviene priorizar al elegir wallets para el
backfill dirigido.

## Nota sobre la lógica de salida

Analizando las 17 posiciones cerradas aparecieron tres donde el token terminó
**por debajo** del precio de entrada y la posición igual ganó dinero
(−22,2% → +$2,26; −18,5% → +$0,85; −0,5% → +$4,68). Es la venta escalonada
cobrando en la subida antes del desplome: `exit_mc` solo registra el último
tramo. La lógica de salida por cuartos está agregando valor real, no solo
complejidad.

Queda sin medir el benchmark contra "comprar y mantener", que necesita cruzar
las posiciones con la trayectoria de precio de `signal_outcomes`. El dato
existe (`max_return`, `return_15m`); el cálculo no se hizo.

---

# Punto ciego del hook de pre-push — encontrado y corregido, 2026-09-11

## Cómo apareció

Al pushear un commit de documentación, `git push` llevó también dos commits de
Codex que estaban commiteados localmente pero sin subir (`97f863e`,
`28c8324`). Fueron a producción sin que nadie decidiera desplegarlos.

El contenido era sano —la corrección del doble procesamiento y sus tests, 170
pasando, live trading bloqueado— y producción quedó estable. Pero **no fue una
decisión deliberada**, que es exactamente lo que el hook existe para evitar.

**Lección operativa:** revisar `git log origin/main..HEAD` antes de pushear.
Un push no lleva solo tu commit; lleva todo lo que haya local por delante del
remoto.

## El punto ciego

El hook no lo frenó, y según su lógica original actuó bien: el diff **no
contenía ningún patrón de riesgo**.

El cambio de Codex agregaba un `if is_watched_wallet: continue` justo encima de
las llamadas a `update_paper_position()` y `evaluate_live_position_exit()`.
Alteró **si esas funciones se ejecutan**, sin tocar una sola línea que las
nombre.

Ese es el caso más peligroso y era justo el que se escapaba: el hook buscaba
patrones en las líneas modificadas, así que un cambio de flujo de control
alrededor de una venta resultaba invisible. Podía frenar un cambio cosmético
que mencionara `risk_check` y dejar pasar uno que decidiera cuándo se vende.

## La corrección

Se pasó de mirar solo las líneas agregadas y quitadas a `git diff -W`, que
expande cada cambio a **la función completa que lo contiene**. Así, cualquier
modificación dentro de una función que toque el camino del dinero queda
marcada, la nombre o no.

Se probó primero con contexto fijo (`-U15`) y **no alcanzaba**: el código es
espaciado y la llamada relevante quedaba decenas de líneas más abajo. `-W` no
depende de la distancia.

Verificado contra historia real:

| Commit | Antes | Ahora |
|---|---|---|
| `97f863e` (el que se escapó) | permitido | **frenado** |
| `d0a6e28` (conecta compra real) | frenado | **frenado** |
| solo documentación | permitido | permitido |
| solo `graphify-out/` | permitido | permitido |

A cambio hay más falsos positivos, que para esto es el error barato: frenar de
más cuesta un `ALLOW_RISK_PUSH=1` tras mirar el diff; frenar de menos cuesta un
despliegue no revisado al camino del dinero.

---

# `update_paper_position()` idempotente — 2026-09-11

Bloque aislado pedido por Codex. **Sin commit ni push**: diff listo para
revisión.

## El problema

`update_paper_position()` descuenta de `remaining_pct` y acumula en
`realized_pnl_usd`. Aplicar dos veces el mismo evento convierte una venta
parcial del 25% en una del 50% y suma la ganancia dos veces. Un reintento tras
un fallo de red bastaba para provocarlo.

## Qué se hizo

**Identidad por evento y posición.** Tabla nueva
`paper_position_applications(position_id, event_id, applied_ts, action)` con
clave primaria compuesta. Es por posición **y** evento a propósito: un mismo
evento puede tocar legítimamente posiciones distintas.

**La marca se escribe en la misma transacción que la actualización.** La
función abre `BEGIN IMMEDIATE`, verifica la marca, aplica el efecto, inserta la
marca y confirma. Si el proceso muere en el medio, no queda la posición
modificada sin su marca — que es el orden peligroso, porque una posición
modificada sin marca se volvería a modificar en el reintento.

**Parámetro `event_id` opcional.** Los dos llamadores de producción
(`save_trade` y `route_market_event`) pasan la firma de la transacción. Sin él
el comportamiento queda igual que antes, para las rutas de demo que no tienen
identidad que ofrecer.

## Un problema que introdujo el propio arreglo

Al agregar `BEGIN IMMEDIATE`, una excepción entre el inicio de la transacción y
el commit dejaba la conexión abierta **sosteniendo el lock de escritura**.
Antes de este cambio una excepción solo filtraba una conexión; ahora podía
bloquear a los demás escritores hasta que el recolector la liberara.

Lo detectó el propio test de fallo, que no podía limpiar su base temporal.
Corregido con `try/except/finally`: rollback y cierre en toda salida.

## Tests

`tests/test_paper_idempotency.py`, 5 casos:

- evento repetido se aplica una sola vez;
- eventos distintos sí se aplican cada uno (la guarda no bloquea de más);
- sin `event_id` el comportamiento es el anterior;
- el mismo evento sobre otra posición sí se aplica;
- **fallo al confirmar**: no queda ni el efecto ni la marca, y el reintento
  posterior aplica una sola vez.

El último necesitó un proxy de conexión: `sqlite3.Connection.commit` es de solo
lectura y no se puede parchear.

Suite completa: **176 tests, OK**. `graphify update .` ejecutado.

## Correcciones tras la revisión de Codex

Codex encontró tres bloqueadores. Los tres eran correctos.

**1. La firma sola como identidad (alta).** `event_id=signature` confundía
varias operaciones dentro de una misma transacción: la segunda se descartaba
como duplicado y se perdía. No era una limitación, era pérdida de datos.

Corregido: el parámetro pasó a ser `event_signature` + `event_index`, y la
identidad se construye con `paper_event_identity()` como `firma:indice`. Se
hizo explícito a propósito — un parámetro opaco permitía volver a pasar la
firma sola sin que se notara. El esquema de eventos normalizados de Codex ya
usa `PRIMARY KEY(signature, event_index)`, así que la convención es la misma.

**2. La auditoría fuera de la transacción (alta).** `save_position_event()`
corría después del commit. Si fallaba, el reintento veía la marca de
aplicación y el evento de auditoría se perdía para siempre.

Corregido: `save_position_event()` acepta `connection` y escribe dentro de la
transacción del llamador sin confirmar. El efecto y su auditoría ahora caen o
sobreviven juntos.

**3. El lock fuera del try/finally (media).** `BEGIN IMMEDIATE` tomaba el lock
de escritura antes de que empezara la protección, así que una excepción en la
consulta o en los cálculos dejaba la conexión abierta sosteniéndolo.

Corregido reestructurando: se extrajo `decide_paper_position_action()` como
función pura —mismo patrón que la pareja ya existente
`decide_live_position_exit` / `evaluate_live_position_exit`— y
`update_paper_position()` quedó como un envoltorio transaccional compacto, con
toda la vida de la conexión dentro del try/finally.

### Tests agregados

- misma firma con índices distintos: ambas operaciones se aplican, y las
  marcas quedan como `firma-1:0` y `firma-1:1`;
- fallo al guardar la auditoría: no queda ni el efecto, ni la marca, ni el
  evento; el reintento posterior aplica y audita una sola vez;
- excepción después de `BEGIN IMMEDIATE` y antes del `UPDATE`: la base no
  queda bloqueada, verificado abriendo otra transacción de escritura después.

Suite completa: **179 tests, OK**. `graphify update .` ejecutado.

## Segunda revisión de Codex: dos problemas más

Codex aprobó la auditoría atómica y la protección del lock, y encontró dos
cosas más. Las dos eran correctas.

**1. El índice no llegaba por las rutas reales (alta).** `update_paper_position()`
aceptaba `event_index`, pero `save_trade()` y `route_market_event()` mandaban
siempre 0. La prueba multi-evento llamaba a la función directo, así que no veía
la pérdida. El parámetro existía y no servía para nada.

Corregido: el índice se lee del evento, al lado de la firma, con
`market_event_index()`, y se propaga por las dos rutas.

*Una desviación de lo pedido, deliberada.* Codex dijo que las funciones debían
"recibir y propagar" el índice; lo puse adentro del evento en vez de como
parámetro aparte. El motivo: cuando la ingesta normalizada tenga cola y
reintentos, el evento se va a guardar y releer, y un índice que viajara al lado
se perdería en ese salto mientras la firma sobrevive — que es exactamente la
clase de pérdida que este bloque existe para cerrar. Firma e índice son dos
mitades de una misma identidad y conviene que no se puedan separar. El contrato
para la ingesta de Helius queda: poner `eventIndex` en el evento parseado.

Ausente significa 0, porque PumpPortal entrega una operación por mensaje.
Presente pero inválido se rechaza.

**2. El cierre podía quedar confirmado y reportarse como fallido (media).**
`untrack_token_if_unused()` corre después del commit y abre otra conexión. Si
falla, la posición ya quedó cerrada y marcada.

El caso era peor de lo que parecía desde afuera: el `SELECT` filtra por
`status = 'open'`, así que el reintento ni siquiera llegaba a la guarda de
idempotencia — salía antes por `if not position`. La limpieza no se repetía
nunca y el token quedaba suscripto para siempre sin nada que lo justificara.

Corregido: cuando no hay posición abierta, se pregunta si este mismo evento
cerró una posición de este mint. Si la cerró, la limpieza se repite. Es
idempotente, así que repetirla es gratis y no repetirla no se arregla después.
Para eso se separó `apply_paper_event()` —el núcleo transaccional, que devuelve
qué pasó— de `update_paper_position()`, que hace el trabajo posterior al commit.

**3. Índices inválidos convertidos en 0 (media).** `int(event_index or 0)` con
un `except` que caía en 0 podía darle la misma identidad a dos operaciones
distintas de la misma transacción, y la segunda se perdía como duplicado:
justo el error que la identidad existe para evitar, pero ahora invisible.

Corregido: `paper_event_identity()` levanta `ValueError`. El índice lo produce
nuestro propio parser, así que un valor inválido es un bug y conviene que se
vea.

### Tests agregados (7)

Por el router, que es por donde entran los eventos en producción:

- ruta de billetera vigilada (vía `save_trade()`): dos ventas parciales con la
  misma firma e índices 0 y 1 se aplican las dos;
- ruta de token seguido con billetera ajena: dos tramos de take profit, ídem;
- evento repetido por el router: se aplica una sola vez;
- evento sin índice: conserva la semántica de PumpPortal;
- índice roto en el evento: se rechaza;
- índice inválido en `paper_event_identity()`: `"1"`, `None`, `1.0`, `True`,
  `-1`, `[1]` — todos rechazados;
- limpieza que falla después del commit: el reintento la repite.

**Verificados contra el bug, no solo en verde.** Con el índice fijo en 0, 4 de
las 5 pruebas del router fallan (`0.75 != 0.5` en las dos rutas: la venta
perdida). Con la limpieza movida detrás del corte por `applied`, la prueba de
reintento falla. La quinta prueba del router pasa en ambos casos a propósito:
es la que fija la semántica de PumpPortal.

Suite completa: **186 tests, OK**. `graphify update .` ejecutado.

## Tercera revisión de Codex: el índice se perdía al serializar

Codex aprobó la parte transaccional paper y encontró un bloqueante real: el
diseño "identidad adentro del evento" era correcto pero estaba a medio aplicar.

**El bloqueante.** El parser guardaba el índice *al lado* del evento
(`result["event_index"]`), y lo que se serializa a `event_json` es solamente
`result["event"]`. La columna `event_index` del inbox quedaba bien, pero al
reconstruir el evento desde el JSON el índice no estaba: `market_event_index()`
devolvía 0 y las dos operaciones de una transacción volvían a colisionar.

Eran **dos** sitios de serialización, no uno: el inbox y
`record_rpc_fallback_event()`. Los dos con el mismo agujero.

Corregido en el parser, que es donde nace el dato: el índice se escribe adentro
del evento normalizado y el hermano se eliminó. Con una sola fuente, los dos
sitios de serialización lo conservan solos y la columna se deriva del mismo
evento que se serializa, así que columna y JSON no pueden discrepar.

**Un hallazgo al escribir la prueba.** El evento normalizado no lleva adentro la
billetera que firmó —el parser la conoce por contexto y no la escribe—, así que
un evento reconstruido del JSON parece de una billetera ajena y el router lo
manda por la ruta equivocada. Por eso se agregó
`market_event_from_inbox_row()`, que vuelve a juntar el JSON con las columnas de
la fila. Es el contrato que va a necesitar el procesador cuando la cola se
active; hoy solo lo usan las pruebas.

### Tests agregados (4), en `InboxRoundTripTests`

Webhook → inbox → deserialización → router, con dos operaciones Pump en una
sola transacción:

- el índice sobrevive la serialización, y columna y JSON coinciden;
- los dos eventos reconstruidos mantienen identidad distinta atravesando el
  router: el restante queda en 0.50, no en 0.75;
- el evento reconstruido recupera la billetera que firmó;
- un `event_json` con índice roto se rechaza al reconstruir, en vez de volverse
  0 en silencio.

**Verificados contra el bug exacto.** Reproduciendo el estado que reportó Codex
—parser escribiendo el hermano, columna leyendo el hermano— el inbox guarda 1 y
2 correctamente y aun así la prueba del router falla con `0.75 != 0.5`: la
segunda venta perdida. Es justo el caso que no se ve mirando la base.

## Cuarta revisión: el helper no rechazaba lo que decía rechazar

Codex encontró que `market_event_from_inbox_row()` no fallaba con un índice
*ausente*, y que la prueba usaba un índice roto (`"1"`), que es otro caso.

Tenía razón, y el error fue de diseño mío: reutilicé `market_event_index()` en
dos contextos con reglas incompatibles. "Ausente vale 0" es correcto para el
router en vivo —PumpPortal entrega una operación por mensaje y no manda
índice— y es exactamente la regla equivocada al reconstruir desde disco, donde
ausente significa que el índice se perdió. La misma función no podía servir a
los dos sin decir cuál de los dos contratos se le está pidiendo.

Corregido con `market_event_index(event, required=False)`. `required=True` en
los dos sitios de serialización y en la reconstrucción, que son los lugares
donde el evento es nuestro; `False` solo en la ruta en vivo. Así la pérdida se
detecta también **al guardar**, no recién al reconstruir.

El helper además ahora exige todo, porque cada cosa que falte produce un error
silencioso distinto:

- **billetera obligatoria y no vacía**: el evento normalizado no lleva adentro
  quién firmó, así que sin ella el router lo clasifica por la ruta equivocada.
  No se rompe nada: se aplica mal, que es peor;
- **firma e índice de la fila**, comparados contra los del JSON. El JSON es la
  fuente de la identidad y las columnas son una copia derivada; si discrepan,
  la fila la escribió código viejo o está corrompida, y aplicarla con una de
  las dos identidades es peor que rechazarla.

### Tests agregados (5), total 9 en `InboxRoundTripTests`

Índice ausente, índice inválido, billetera ausente (`None`, `""`, `"   "`),
firma discrepante, índice discrepante, y la ruta en vivo que debe seguir
aceptando ausente como 0.

**Un detalle de la prueba de índice ausente.** Con `event_index=1` la
rechazaría el chequeo de discrepancia (0 contra 1) y no probaría nada sobre
`required`. Usa `event_index=0`: ahí columna y ausencia coinciden, y lo único
que la rechaza es exigir que el campo esté.

**Verificadas contra el bug.** Quitando `required=True` del helper, la prueba de
índice ausente falla con `ValueError not raised` — literalmente el hallazgo de
Codex. Quitando el chequeo de billetera, fallan los tres subcasos.

Suite completa: **195 tests, OK**. `graphify update .` ejecutado.

Commiteado en `main` como `9c9636a`, sin push. Codex aprobó el informe.

## Punto 2: identidad completa en las salidas live

Cambio aislado, sobre el camino del dinero. El hook de pre-push lo frena.

**El problema.** `evaluate_live_position_exit()` recibía solo la firma, y la
clave de idempotencia de `TRADER_PARTIAL` era
`LIVE-EXIT-{orden}-TRADER_PARTIAL-{firma}`. Dos ventas parciales del trader de
origen en una misma transacción compartían clave: la segunda se descartaba como
`IDEMPOTENT_REUSE`. Una venta que debía ocurrir y no ocurría — el espejo del bug
paper, pero acá con plata real.

**El cambio.**

- `evaluate_live_position_exit()` acepta `event_index=0` y las dos rutas reales
  se lo pasan con `market_event_index(event)`;
- la identidad se calcula **una sola vez, antes del bucle de decisiones**, para
  que un índice inválido falle igual sin importar qué decisión salga;
- solo `TRADER_PARTIAL` la usa en la clave, ahora `firma:índice`;
- `TAKE_PROFIT` sigue siendo idempotente **por etapa** (`TP-{n}`) y
  `STOP_LOSS`/`TRADER_EXIT` **por cierre** (el motivo solo). Sin cambios, a
  propósito: son idempotencias de estado, no de evento.

**Un renombre.** `paper_event_identity()` pasó a `market_event_identity()`. La
usan ahora las dos rutas, y compartir la función es lo que garantiza que el
formato sea el mismo en las dos; el nombre viejo mentía sobre su alcance. Toca
4 líneas fuera de este bloque (definición, un llamado en `update_paper_position`
y dos en tests).

### Tests agregados (6), en `LiveReceiptPersistenceTests`

Sin red —el `setUp` de la clase rompe `urlopen`— y con
`submit_pumpportal_lightning_trade` parcheado, que es el único punto que toca la
wallet. Ninguna orden real.

- dos índices de una firma producen dos ventas parciales, y repetir el mismo
  índice produce una sola;
- `TAKE_PROFIT` con dos índices distintos vende una sola vez;
- `STOP_LOSS` con dos índices distintos cierra una sola vez;
- PumpPortal sin índice conserva su clave, con identidad `firma:0`;
- venta parcial sin firma sigue dando `EVENT_SIGNATURE_REQUIRED`;
- índice roto levanta `ValueError` antes de crear ninguna orden.

**Verificados en las dos direcciones.** Volviendo la clave de `TRADER_PARTIAL` a
la firma sola, la prueba de dos parciales falla en `assertTrue(segunda["ok"])`:
la segunda venta no se ejecuta. Y metiendo el índice en las claves de
`TAKE_PROFIT` y de cierre, fallan las otras dos — o sea que están pinchando el
comportamiento que había que dejar quieto, no solo pasando.

Un dato del segundo experimento: con el índice en la clave de cierre, el segundo
evento llega hasta `SELL_AMOUNT_EXCEEDS_AVAILABLE_POSITION`. Intenta vender de
nuevo y lo frena la reserva de tokens, que es la última barrera. La idempotencia
por cierre es la que evita llegar hasta ahí.

Suite completa: **201 tests, OK**. `git diff --check` limpio.

Commiteado en `main` como `3bd8353`, sin push. Codex lo aprobó.

## Punto 3: protección contra eventos anteriores a la entrada

**El hallazgo que cambió el diseño.** La idea original era comparar contra
`opened_ts`, y no sirve: ese reloj mide cuándo reaccionó la app, no cuándo
ocurrió el evento. Abrimos a las 12:00:05 por un evento on-chain de las
12:00:00; un evento on-chain de las 12:00:03 es posterior a la entrada y hay
que aplicarlo, pero es anterior a `opened_ts`. Una guarda así rechazaría
operaciones legítimas.

La comparación correcta es on-chain contra on-chain. Codex confirmó el
diagnóstico y el diseño.

**Segundo hallazgo.** `block_event_ts` tenía exactamente la misma forma rota que
tenía `event_index`: el parser lo guardaba como hermano del evento, y solo el
evento se serializa. Al reconstruir desde la cola, el dato no estaba. Movido
adentro como `blockEventTs`, y la columna del inbox ahora se deriva de él.

**El cambio.**

- columna `entry_block_event_ts REAL`, sin DEFAULT: las posiciones que ya
  existen quedan en NULL, que es la verdad;
- `evaluate_buy()` → `open_paper_position()`, que valida al entrar;
- el timestamp del evento llega a `update_paper_position()` y
  `apply_paper_event()` desde las dos rutas reales;
- se rechaza **solo** si los dos timestamps existen y el del evento es menor.
  La igualdad se acepta: resolución de segundos, así que dos transacciones del
  mismo segundo son indistinguibles y descartarlas perdería operaciones
  válidas;
- si falta cualquiera de los dos lados, queda el comportamiento previo;
- el evento descartado se marca como `IGNORED_PRE_ENTRY` en
  `paper_position_applications`, para que el reintento no lo evalúe para
  siempre.

**Una asimetría deliberada en la validación.** `validated_block_event_ts()` es
estricta —numérico, finito, positivo— y se aplica a lo que entra desde el
parser, porque ahí un valor inválido es un bug y conviene que estalle apenas
aparece. `stored_block_event_ts()` es indulgente y trata lo inservible como
ausente, porque un valor ya guardado que estallara rompería esa posición en cada
evento, para siempre. Sin referencia confiable la guarda se apaga, que es la
misma regla que ya rige para un dato ausente.

### Tests agregados (12)

Evento anterior, posterior, igual, timestamp de evento desconocido, timestamp de
entrada desconocido, reintento del descartado, un descarte que no bloquea a los
demás, timestamps inválidos (`"1700000000"`, `inf`, `nan`, `0`, `-1`, `True`),
`open_paper_position()` guardando y validando, y dos de round trip por el inbox.

**Verificados contra el bug.** Con la guarda desactivada, el evento viejo mueve
la posición (`0.75 != 1.0`), y el que llega después de uno válido la mueve de
nuevo (`0.5 != 0.75`). Devolviendo `block_event_ts` a viajar al lado del evento,
el evento reconstruido pierde el timestamp (`None != 1700000000.0`).

### Dos pruebas más, por el router (pedido de Codex)

El patrón ya apareció tres veces —índice, billetera, timestamp—: dato correcto
adentro de la función y perdido en el camino hasta ella. Estas entran por
`route_market_event()` y cubren sus dos rutas:

- wallet vigilada → `save_trade()` → evento anterior queda `IGNORED_PRE_ENTRY`;
- token seguido de wallet ajena → ruta de token → mismo resultado.

Las dos comprueban después que un evento posterior sí se aplica, para que no
pasen por estar la ruta muerta.

**Verificadas por separado.** Cortando la propagación en `save_trade()` falla
solo la primera; cortándola en la ruta de token, solo la segunda. Cada una cubre
su ruta y ninguna tapa a la otra.

Suite completa: **215 tests, OK**. `git diff --check` limpio.

Commiteado en `main` como `a301310` y **pusheado** junto con `3bd8353` y
`9c9636a` (`ab8c487..a301310`). El hook dejó constancia del salto por
`ALLOW_RISK_PUSH=1` en vez de permitirlo en silencio. Producción sana: stream
conectado, sin error activo, shadow con 1.234 predicciones y 1.036 completadas.

## `save_token_history()` idempotente

**Un choque con el requisito, encontrado antes de escribir.** Codex pidió que la
misma operación traída por PumpPortal y por Helius se guarde una sola vez. No
podía cumplirse: `eventIndex` era la posición dentro de `logMessages`, y para
una transacción de una sola operación eso da **1** por el parser de Helius —
después de la línea `invoke`— contra **0** por PumpPortal, que no manda índice.
Misma operación, dos identidades, dos filas. La prueba existente lo mostraba:
`assertEqual(rows, [(1,), (2,)])`.

Corregido en el parser: `eventIndex` cuenta **operaciones Pump parseadas**, no
líneas de log. La posición en el log es un detalle de cómo encontramos el evento
por ese camino; el ordinal entre operaciones es independiente del proveedor, que
es lo que la identidad necesita. Los índices del inbox pasan de 1,2 a 0,1.

Las filas del inbox ya escritas en producción conservan la numeración vieja. Es
aceptable porque el inbox es observacional y todavía no lo consume nada, pero
conviene saberlo antes de activar la cola: **si se procesa el inbox histórico,
esas filas tienen índices de log.**

**El cambio en el historial.**

- columna `event_id TEXT` e índice único **parcial** sobre ella, solo donde no
  es NULL;
- `event_id = market_event_identity(signature, event_index)`, sin `source`: la
  identidad es de la operación, no de quién la trajo;
- `INSERT OR IGNORE` contra ese índice en vez de consultar y después insertar —
  entre esas dos operaciones cabe otro escritor, y acá llegan reintentos y dos
  proveedores a la vez. La deduplicación vive adentro de la sentencia que
  escribe, y todo va en una transacción;
- sin firma, comportamiento anterior, para demos;
- el índice se propaga por las dos rutas reales.

**Un efecto secundario bueno.** La deduplicación anterior era
`SELECT ... WHERE signature = ?` y `signature` no tiene índice: cada guardado
recorría la tabla entera. Ahora resuelve por índice único.

**Las filas viejas quedan en NULL** y fuera del índice parcial, así que crearlo
no puede chocar con datos existentes y no hizo falta backfill. El costo es una
ventana de segundos en el despliegue: un evento guardado justo antes de la
migración y reintentado justo después podría duplicarse una vez. Una fila, y
solo en ese instante.

### Tests agregados (11)

Reintento, misma firma con índices distintos, la misma operación por dos
proveedores, `source` fuera de la identidad, sin firma, firmas distintas, índice
roto, filas viejas sin identidad que no bloquean, y tres por el router cubriendo
sus dos rutas más la entrega repetida.

**Verificados contra el bug, en las dos piezas.** Con identidad por firma sola,
la segunda operación de una transacción se pierde (`['firma-1']` en vez de dos
filas) y fallan 6 pruebas. Con `eventIndex` de vuelta en índice de log, falla el
caso de una sola operación (`('helius', 1, …)` contra `('helius', 0, …)`), que
es exactamente donde divergían los proveedores.

### Segunda revisión: la transición con datos viejos

Codex frenó el commit por tres cosas. Las tres correctas, y ninguna se veía
mirando solo la lógica nueva.

**1. `ALTER TABLE` en cada `db()`.** Lo había puesto ahí siguiendo el precedente
de la columna `mode`, para garantizar que la columna existiera antes del índice.
Pero `db()` abre conexión constantemente, así que era una excepción por
conexión. **Medido: +0,49 ms por conexión, +8,1%.**

Movido a `migrate_token_history_identity()`, que corre una vez al arrancar. El
índice sigue en `db()` pero envuelto en `try/except`: en una base que todavía no
tiene la columna se saltea —`migrate_database()` llama a `db()` antes de
agregarla— y lo crea la migración. De ahí en adelante es un no-op.

**2. Filas históricas sin identidad.** Quedaban en NULL, fuera del índice
parcial, así que un replay desde la cola habría duplicado historial existente.

Migradas a `firma:0`, **una sola fila por firma**: si hubiera firmas repetidas
de antes de que existiera cualquier deduplicación, migrar todas violaría el
índice único. Migrando la primera, la identidad queda ocupada y el replay
encuentra su duplicado igual. `UPDATE OR IGNORE` cubre el caso restante —una
fila vieja cuya identidad ya se la llevó una fila nueva, que es la ventana del
despliegue— para que la migración no estalle al arrancar.

**3. La renumeración rompía la identidad de las filas de inbox ya guardadas.**
Las había dejado anotadas como límite; Codex tiene razón en que eso no alcanza.
`migrate_inbox_event_index_to_ordinal()` las renumera 0,1,2… por firma,
respetando el orden que tenían, en la columna y adentro del JSON. Como el
ordinal nuevo nunca es mayor que el índice viejo, actualizar en orden ascendente
no puede chocar con la clave primaria `(signature, event_index)`.

**3b, y esto sigue abierto.** Codex marcó que la renumeración *no* resuelve con
certeza transacciones múltiples de PumpPortal, y es así: ese transporte entrega
una operación por mensaje y no manda índice, así que dos operaciones Pump de una
misma transacción llegan las dos como 0 y se funden en una fila. No lo arregla
ningún esquema de numeración nuestro: el dato que las distinguiría nunca llega
por ahí. Solo lo resuelve la ingesta de Helius, que trae el ordinal real —una
razón más para migrar. Queda documentado con una prueba que fija la limitación,
`test_pumpportal_multi_operation_remains_ambiguous`, explícitamente marcada como
comportamiento conocido y no deseado.

### Tests de migración y replay (9 más)

Fila histórica que recibe identidad y hace que el replay no duplique; firmas
históricas repetidas que no rompen la migración; identidad ya tomada por una
fila nueva; filas sin firma que conservan NULL; migración idempotente;
renumerado del inbox en columna y JSON; renumerado idempotente; filas ya
ordinales que no se tocan; y la limitación de PumpPortal.

**Verificados contra el bug.** Sin el backfill, la fila histórica queda en NULL
y el replay duplica. Sin el renumerado, `0 != 3`.

Suite completa: **235 tests, OK**. `git diff --check` limpio.

## Límites

- **Esto protege paper, no live.** Las salidas live van a necesitar su propia
  referencia temporal, basada en la confirmación on-chain de nuestra compra
  real y no en el timestamp de la señal del trader. Marcado por Codex, sin
  abordar.
- **Resolución de segundos.** Dos transacciones distintas dentro del mismo
  segundo siguen siendo ambiguas. Aceptar la igualdad es lo conservador por
  ahora.
- **Riesgo residual marcado por Codex, no bloqueante.** `STOP_LOSS` y
  `TRADER_EXIT` tienen claves de idempotencia distintas aunque los dos cierran
  la posición entera. La reserva de tokens evita una segunda venta. A revisar
  aparte.
- La limpieza se repite solo cuando el mismo evento vuelve. Si el evento nunca
  se reintenta, el token queda suscripto igual. Cerrarlo del todo pide una
  reconciliación periódica, no un rescate en el camino del evento.

## Renumeración del inbox sin barrido permanente

Se cerró el costo de `migrate_inbox_event_index_to_ordinal()` en cada arranque.
No se usó un marcador global porque una reversión de Railway podría escribir
filas viejas después de marcar la migración como terminada. Cada fila lleva ahora
`event_index_scheme`: el valor por defecto `log-v1` permite detectar escrituras
de código anterior, mientras la ingesta actual escribe `ordinal-v1`
explícitamente.

El arranque consulta mediante un índice solo las firmas con `log-v1`; los JSON
se leen exclusivamente para esas firmas. En el primer despliegue, la columna se
añade a la tabla existente y sus filas se etiquetan tras renumerarlas. En
reinicios normales no hay barrido del contenido. Pruebas específicas cubren el
esquema anterior, el arranque sin lectura de JSON, una fila creada por una
reversión y la escritura nueva de Helius como `ordinal-v1`.

Suite completa: **249 tests, OK**. Sin cambios en señales, scoring, paper
trading ni ejecución live.

## Validador observacional del inbox de Helius

Codex agregó la primera etapa del consumidor, todavía sin efectos. El contrato
es deliberadamente limitado: reclama filas `observed`, reconstruye y valida el
evento guardado, y termina en `validated` o `rejected`. Nunca llama a
`route_market_event()` y el interruptor
`MARKET_EVENT_INBOX_VALIDATION_ENABLED` queda apagado por defecto.

La reserva usa `claim_token` y `claimed_ts`. Una reserva vencida puede ser
recuperada después de `MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS`; el token
impide que el worker viejo confirme o rechace la fila después de perderla. Los
intentos, errores y tiempos finales quedan en la misma fila. El endpoint
`/api/helius-webhook-stats` expone configuración y conteos por estado.

Pruebas específicas: exclusión durante una reserva vigente, recuperación tras
vencimiento, protección contra el dueño viejo, validación sin invocar el
router, rechazo con error persistido y filas terminales no reclamadas otra vez.

El siguiente paso, separado y todavía no implementado, es consumir filas
`validated` mediante otro interruptor. Ese paso sí tocará el pipeline funcional
y requiere una revisión nueva antes de permitir cualquier efecto.

## Deduplicación global por evento

Antes del despacho apareció otro prerrequisito: el stream reservaba solamente
la firma en `processed_signatures`. Una transacción con eventos `0` y `1`
habría descartado el segundo antes de llegar a `route_market_event()`.

Se agregó `processed_market_events`, con `firma:índice` como clave primaria, y
`mark_market_event_processed()` hace la reserva mediante `INSERT OR IGNORE`
dentro de una transacción. `stream()` usa esa identidad tanto en memoria como
en SQLite. Las firmas históricas se migran como índice `0`; el índice `0` nuevo
también se sigue escribiendo en la tabla vieja para que una reversión no vuelva
a ejecutar los eventos comunes. `mark_signature_processed()` queda como
compatibilidad y representa explícitamente el índice `0`.

Pruebas nuevas: dos índices de una misma firma pasan una vez cada uno, dos
writers concurrentes reservan una sola vez, la firma histórica bloquea el
índice `0` pero no el `1`, y el websocket entrega ambos índices al router.

Esto todavía no despacha Helius. El próximo bloque puede construir el parser
por token y la frontera temporal de activación sobre esta identidad completa.

## Parser de Helius por token y frontera de activación

Se agregó una segunda vista del parser oficial de Pump/PumpSwap para eventos de
tokens seguidos. A diferencia de la vista por wallet, no exige que el firmante
sea una wallet vigilada: filtra por mint y conserva en el evento la wallet real
que operó. Ambas vistas comparten el mismo parser y los mismos ordinales, de
modo que una operación que coincida por wallet y por token se guarda una sola
vez en `market_event_inbox`.

`record_helius_webhook_transactions()` ahora preserva eventos relevantes por
wallet o por token, pero sigue siendo estrictamente observacional: no llama a
`route_market_event()`, no modifica posiciones y no genera órdenes. La
suscripción dinámica de los mints en Helius queda para el siguiente bloque.

Se definió además una frontera persistente de activación en `app_state`. Se fija
una sola vez y exige que tanto la recepción como el timestamp on-chain sean
posteriores a la activación. Los timestamps ausentes o inválidos fallan
cerrados. La frontera todavía no se establece automáticamente ni existe un
consumidor con efectos, por lo que las observaciones históricas no pueden entrar
accidentalmente al pipeline en este despliegue.

Pruebas nuevas: parsing por mint sin wallet vigilada, exclusión de mints no
seguidos, ordinales múltiples, deduplicación wallet+token, persistencia de la
wallet dentro del JSON, rechazo de discrepancias y cinco casos de la frontera
temporal. También se limpiaron los errores de tipado existentes en el parser.

Suite completa: **260 tests, OK**. Pyright: **0 errores** en los cuatro archivos
Python modificados. `git diff --check` limpio. Sin cambio de `DATA_VERSION`, sin
consumidor funcional y sin cambio en live trading.

## Revisión del ordinal de Helius

La revisión externa detectó que `ordinal-v1` contaba solo los eventos que el
parser lograba decodificar por completo. Una operación posterior podía cambiar
de identidad si una operación anterior empezaba a decodificarse al recibir
balances más completos o después de una mejora del parser.

El parser ahora asigna el ordinal sobre cada payload reconocido por los
discriminadores oficiales de Pump/PumpSwap, antes de intentar decodificarlo.
Los eventos nuevos se escriben como `ordinal-v2`; las filas `ordinal-v1`
existentes no se reescriben porque pertenecen al historial observacional que la
frontera temporal excluirá de cualquier consumidor futuro.

También se cerraron tres hallazgos relacionados: un balance final de usuario
ausente queda como `None` sin descartar el precio útil; una transacción firmada
por varias wallets vigiladas conserva las operaciones de todas; y las wallets
desconocidas se guardan con `trader = NULL` en lugar de inventar una identidad
con los primeros seis caracteres de la dirección.

El webhook sigue siendo observacional. Antes de activar un consumidor habrá que
preservar el significado de `newTokenBalance = None` también al pasar por
`save_trade()`: hoy esa ruta histórica lo convierte en cero para paper, lo que
podría parecer falsamente un cierre total. No afecta este bloque porque el inbox
todavía no se enruta.

Suite completa después de la revisión: **263 tests, OK**.

## Sincronización dinámica de tokens con el webhook de Helius

Se agregó el bloque previo al consumidor funcional: mantener en Helius los
tokens de `TRACKED_TOKENS` que necesitan eventos de mercado. Sigue apagado por
defecto y separado en dos interruptores:

- `HELIUS_WEBHOOK_SYNC_ENABLED=false`: no arranca el worker ni hace requests.
- `HELIUS_WEBHOOK_SYNC_APPLY=false`: consulta y publica el plan en métricas,
  pero no envía `PUT`.

La sincronización lee la configuración remota completa y reemplaza únicamente
`accountAddresses`, preservando URL, tipo, autorización, encoding, estado y
tipos de transacción soportados. Las direcciones que ya estaban en Helius se
consideran base ajena; solo se retiran tokens que este worker haya agregado.
Una eliminación manual remota es autoritativa y no se revive desde cache.

El estado se persiste en `helius_webhook_sync_state`. `pending_tokens` se graba
antes del `PUT`: si Helius aplica el cambio y Railway reinicia antes de confirmar
localmente, el token no se absorbe como dirección base y el siguiente ciclo lo
puede reconciliar. Las respuestas sin `accountAddresses`, las confirmaciones
distintas al plan y listas mayores al límite oficial fallan cerradas.

Para controlar costo, el worker agrupa cambios cada 5 segundos, no consulta si
el conjunto deseado no cambió, audita remotamente cada 15 minutos y espera 60
segundos tras errores. `/api/helius-webhook-stats` expone el estado bajo
`webhook_sync` sin mostrar API key, webhook ID ni secreto.

Este bloque solo cambia cobertura observacional del webhook. No establece la
frontera de activación, no consume el inbox, no llama a `route_market_event()` y
no afecta scoring, paper trading ni órdenes live.

Verificación final: **276 tests, OK**. Pyright: **0 errores y 0 warnings**.

## Consumidor seguro del inbox de Helius

Codex implementó la segunda etapa de la migración: filas `validated` pueden
ser reclamadas con lease y pasar por `route_market_event()`. El worker queda
apagado por defecto mediante `MARKET_EVENT_INBOX_CONSUMER_ENABLED=false`.
Cuando se habilita, fija una frontera persistente y solo acepta eventos cuyo
`received_ts` y `block_event_ts` sean posteriores. El historial anterior queda
terminalmente como `ignored_pre_activation`.

La exclusión entre PumpPortal y Helius usa `processed_market_events` con la
identidad completa `firma:índice`. Los estados terminales del inbox son
`processed`, `duplicate`, `ignored_pre_activation` y `failed`. Si una excepción
ocurre después de reservar la identidad global, no se reintenta automáticamente:
la fila queda `failed` y se alerta por Discord, porque repetir una ruta que pudo
alcanzar el camino del dinero sería inseguro. La única excepción que se
reintenta es la de la reserva misma (un error de base al escribir
`processed_market_events`): ahí no hubo efectos, la fila vuelve a `validated`
con el error anotado y el siguiente ciclo la vuelve a reclamar; el worker lo
cuenta como `released`. Una fila que no se puede reconstruir sigue siendo
`failed`, porque es determinista. El lease permite recuperar un worker muerto
antes de la reserva global y el token de claim impide que el dueño anterior
confirme una fila recuperada.

La reconstrucción ahora compara también `block_event_ts` entre la columna y el
JSON. Firma, índice, wallet y timestamp deben coincidir antes de aplicar el
evento. Esto cierra el caso donde una columna reciente podía hacer pasar un JSON
viejo por la frontera de activación.

### Barrera de ejecución real

El consumidor llama al router con `allow_live_execution=False`. Helius sí puede
alimentar trades, scoring, señales, outcomes, historial y paper trading, pero no
puede ejecutar compras reales ni salidas de posiciones live. PumpPortal conserva
el comportamiento anterior. `/api/helius-webhook-stats` expone esta distinción
en `inbox_consumer.affects_live_execution=false`.

### Semántica y cronología de datos

`newTokenBalance=None` ya no se convierte en cero: se guarda como `NULL`, no
confirma un cierre de trader y en paper produce como máximo una venta parcial.
La ausencia completa del campo en PumpPortal conserva el valor histórico cero.
Un `null` explícito, venga del transporte que venga, es desconocido: antes se
convertía en cero y fabricaba una salida total. El perfil de calidad tampoco
convierte los nuevos `NULL` en cierres.

Un `newTokenBalance` inservible (negativo, no finito, no numérico) se trata
distinto según quién lo trae. En el stream, PumpPortal es frontera de
confianza: el valor pasa a desconocido con un log y el evento sigue; antes
lanzaba dentro del router con la identidad ya reservada, el stream reconectaba
y el evento se perdía. En el inbox, el parser de Helius es nuestro: la
reconstrucción de la fila lo rechaza en validación, antes de reservar la
identidad global, para que nunca llegue al consumidor.

Los eventos normalizados usan `blockEventTs` para `trades.ts`,
`token_history.ts`, `evaluations.ts`, `signal_outcomes.signal_ts` y los
checkpoints de outcomes. Consenso y contexto de mercado excluyen filas futuras
respecto del evento evaluado. Si los webhooks llegan fuera de orden, una muestra
tardía más cercana puede corregir un checkpoint temprano sin pisar checkpoints
posteriores; una operación anterior a la señal tampoco puede modificar sus
extremos. PumpPortal, que no entrega timestamp on-chain, sigue usando la hora de
recepción.

Límite conocido: eso deja dos relojes en las mismas columnas. La hora de
recepción de PumpPortal siempre es mayor que el tiempo de bloque, típicamente
por uno a tres segundos. Para una señal traída por Helius, `ts <= signal_ts`
excluye las operaciones que PumpPortal recibió en esa ventana aunque on-chain
sean anteriores; para una señal traída por PumpPortal, una operación Helius con
tiempo de bloque dentro de esa ventana cuenta como anterior aunque no lo sea. El
sesgo está acotado por la latencia del stream y no se corrige con una tolerancia:
la misma tolerancia que recuperaría las excluidas dejaría entrar futuro en la
otra dirección, que es peor para un modelo de scoring. Se elimina solo con un
tiempo de bloque para PumpPortal, que el stream no entrega.

Las posiciones paper ahora guardan `last_applied_block_event_ts`. Un webhook
posterior a la entrada pero anterior al último evento aplicado queda auditado
como `IGNORED_OUT_OF_ORDER`, por lo que no puede retroceder `current_mc`, etapas
de take profit ni ventas parciales. La referencia se inicializa con el timestamp
on-chain de entrada; posiciones antiguas y eventos sin timestamp conservan el
comportamiento previo. `LAST_TOKEN_PRICE` también avanza de forma monotónica y
ya no se sobrescribe con precios atrasados.

Pruebas nuevas cubren activación ausente, frontera histórica, discrepancia de
timestamp, carrera real entre transportes, lease vencido, fallo terminal,
bloqueo de ejecución live, saldo desconocido, seis casos de cronología y la
guarda monotónica de posiciones paper.
Verificación final: **295 tests, OK**; `py_compile` y `git diff --check` limpios.
El ejecutable CLI de Pyright no está instalado en este entorno, por lo que no se
repitió ese chequeo estático en esta ronda.

### Identidad de evaluaciones

La tabla `evaluations` ya deduplica por `(trade_signature, event_index)`. La
migración reconstruye la tabla dentro de una transacción, conserva los IDs que
enlazan outcomes y predicciones shadow, y asigna índice `0` a las evaluaciones
históricas. Una misma transacción puede producir varias señales sin confundirlas
con un reintento del mismo evento. La prueba de migración corre dos veces para
confirmar idempotencia y conserva un ID histórico explícito.

### Comparación de transportes con el consumidor activo

`/api/helius-webhook-stats` atribuye cada operación al transporte que la ganó
usando `processed_market_events.source` (`live` para el stream, `helius` para el
consumidor del inbox), no `trades`. Con el consumidor activo Helius también
escribe `trades` con `ts` igual al tiempo de bloque; contarlas como PumpPortal
daría latencia cero y `parsed_only_in_webhook` caería a cero aunque el stream no
hubiera entregado nada. `pumpportal_latency` descarta firmas ganadas por ambos
transportes porque `trades` no guarda índice para separar sus filas, y
`parsed_only_in_webhook` ahora también reconoce entregas del stream que nunca
llegan a `trades` (tokens seguidos operados por wallets no vigiladas). Límite
conocido: si el consumidor gana una operación antes que el stream, la copia del
stream se descarta sin rastro y cuenta como vista solo por el webhook.

### Fallback RPC con identidad completa

`record_rpc_fallback_event()` y `reconcile_rpc_fallback_events()` emparejan
contra `processed_market_events` por `(signature, event_index)`, no contra
`trades` por firma sola. Dos consecuencias. La segunda operación de una
transacción ya no queda `matched` escondida detrás de la primera: PumpPortal
reserva índice 0 para todo lo que entrega, así que una operación 1 que nadie
aplicó ahora se reporta `missing`, y el conteo de faltantes puede subir por
casos reales que antes no se veían. Y lo que aplicó el consumidor del inbox de
Helius cuenta como aplicado: `matched` significa "algún transporte lo aplicó",
que es lo que importa para alertar por Discord. La comparación por transporte
vive en `/api/helius-webhook-stats`.

### Saldo desconocido en el fallback RPC

`rpc_fallback_events.new_token_balance` admite `NULL`. La columna nació
`NOT NULL` cuando el parser convertía un saldo irrecuperable en cero; desde que
lo entrega como `None`, `record_rpc_fallback_event()` hacía `float(None)` en
cada poll, el worker no registraba nada (`rpc_fallback_last_success_ts` nulo en
producción) y la auditoría de cobertura quedaba ciega. La migración reconstruye
la tabla conservando IDs y corre antes de recrear el índice; es idempotente.
Nada lee esa columna: el consumidor usa `event_json`.

# Auditoría de ingesta y dos diffs de observabilidad — 2026-09-22

Estado verificado en producción (04:10 UTC): el webhook de Helius no entrega
nada desde el 17-09 03:12 UTC (créditos del plan free agotados: 1.044.217 de
1.000.000; el sync recibe `HTTP_429 max usage reached` cada 300 s, que es el
backoff previsto, no un reintento descontrolado). El único transporte que
aplica decisiones es el piloto WSS sobre Alchemy, filtrado a 5 wallets. Las
otras 9 (decu incluida, origen de 16 de 17 posiciones paper) están ciegas:
`/api/watched-wallets` → `silent: 9`. El fallback RPC ve compras reales de
esas wallets que nadie aplica. Del tráfico del webhook, 633.360 transacciones
recibidas contra 62.967 eventos de wallets vigiladas: el 90 % eran entregas por
los 30 tokens que el sync mantenía en el webhook, y es la explicación más
probable del consumo de créditos. Ninguna de estas dos cosas se arregla con
código; son variables de Railway (`HELIUS_STANDARD_WSS_TRADERS`,
`HELIUS_WEBHOOK_SYNC_APPLY`).

El watchdog de 120 s no es un bug: PumpPortal solo entrega
`subscribeTokenTrade` de mints con outcome activo, y cuando no hay ninguno la
conexión queda muda. Faltaba el dato para afirmar si entrega alguna operación
de cuenta.

## Dos cambios, solo diagnóstico

- `/api/status` separa `stream_last_account_event_ts` (wallet vigilada) de
  `stream_last_token_event_ts` (token suscrito). Antes `stream_last_event_ts`
  mezclaba ambos y no se podía saber si las suscripciones de cuenta de
  PumpPortal entregan algo.
- `solana_rpc_fallback._rpc_request` guarda el último error JSON-RPC del
  proveedor en `LAST_RPC_ERROR_DETAIL` (código, método, mensaje truncado a 300
  caracteres con todo fragmento largo de la URL del RPC redactado, porque ahí
  viaja la API key). Se expone como `last_rpc_error_detail` en
  `/api/rpc-fallback-stats` y `/api/helius-standard-wss-stats`. Motivo: en
  producción aparece `SOLANA_RPC_ERROR_-32015:getTransaction` (66 fetch fallidos
  del WSS en 24 h y 4 wallets del fallback bloqueadas) aunque ambos call sites
  pasan `maxSupportedTransactionVersion: 0`, y contra el RPC público la misma
  firma responde bien. Sin el mensaje del proveedor no hay forma de saber qué
  significa ese código en Alchemy. La excepción conserva el formato
  `SOLANA_RPC_ERROR_{code}:{method}` que la clasificación ya parsea.

Riesgo abierto anotado, sin tocar: `live_account_exit_monitor_once` pasa
`account_price_observed_ts=checked_ts` (reloj local tomado antes del RPC) y
descarta `snapshot["slot"]`; la guarda `ACCOUNT_PRICE_BEFORE_LAST_OBSERVATION`
solo compara ese reloj, así que un nodo RPC rezagado pasa como observación
nueva. Sin impacto con `apply=false`; bloqueante antes de APPLY.

Verificación: **452 tests, OK**; `git diff --check` limpio; ambos tests nuevos
fallan contra el código anterior.

## Transacciones versión 1 — causa del `-32015` y fix, 2026-09-22

Cinco segundos después de desplegar `last_rpc_error_detail`, producción mostró
el mensaje completo: *"Transaction version (1) is not supported by the
requesting client… maxSupportedTransactionVersion: 1"*. Solana ya produce
transacciones versión 1 (mensaje con `transactionConfig`, sin
`addressTableLookups`) y los dos `getTransaction` del repo pedían `0`. Cada
trade v1 de una wallet vigilada se perdía como `fetch_failed` (66 en 24 h en
el WSS) y bloqueaba la cola del fallback (4 wallets). La prueba manual del
día anterior contra el RPC público no lo reprodujo porque las 8 firmas
probadas eran v0/legacy.

Cambio: `MAX_SUPPORTED_TRANSACTION_VERSION = 1` en `solana_rpc_fallback`,
usado por `fetch_confirmed_transaction` (ingesta WSS + fallback) y por
`fetch_finalized_solana_transaction` en `app.py` (recibos de órdenes reales).
`_parse_receipt_balances` acepta ahora versión 1; la 2 sigue rechazada con
`UNSUPPORTED_TRANSACTION_VERSION`. Este último punto toca el camino de
reconciliación: sin él, una compra real confirmada en una tx v1 no se podía
reconciliar nunca (fallaba primero en el RPC y, con el fetch arreglado, en la
guarda). Se verificó con un recibo real de PumpSwap v1
(`tests/fixtures/pumpswap_sell_v1.json`, sell de epicsealdarkeye, 2026-09-21):
el parser de ingesta produce el `SellEvent` correcto y el de recibos devuelve
1.542.898,009062 tokens, 560.623.763 lamports netos, fee 74.391, coherente con
el evento del programa.

Verificación: **455 tests, OK**; los tres tests nuevos y el ajuste del
existente fallan contra el código anterior.

## Slot en las salidas por precio de cuenta — 2026-09-22

Cierra el riesgo abierto del monitor: `live_account_exit_monitor_once` pasaba
solo `checked_ts` (reloj local tomado antes del RPC) y descartaba
`snapshot["slot"]`, así que la guarda `ACCOUNT_PRICE_BEFORE_LAST_OBSERVATION`
no distinguía un precio nuevo de la respuesta de un nodo RPC rezagado con
reloj local más alto.

Cambio, todo dentro de la ruta `entry_market_cap_source="account"`:

- `evaluate_live_position_exit(..., account_price_slot=)` es obligatorio en
  esa ruta (entero positivo real, no bool ni string) y prohibido en la ruta
  por eventos; ambos casos fallan cerrado con los errores ya existentes.
- Columna nueva `live_positions.account_exit_last_slot` (migración por
  `ALTER TABLE` idempotente, como las anteriores).
- Guarda nueva `ACCOUNT_PRICE_SLOT_BEFORE_LAST_OBSERVATION`: un snapshot con
  slot menor al último aplicado se rechaza aunque su timestamp sea mayor. La
  guarda por timestamp sigue igual.
- El monitor solo considera "priced" un mint cuyo snapshot trae slot válido;
  sin slot, el mint cuenta como no valuado y el ciclo entero falla con
  `LIVE_ACCOUNT_EXIT_UNPRICED_MINTS`, como ya hacía con precios inválidos.

No cambia la ruta por eventos ni ningún flag; `apply` sigue en `false`.
Verificación: **457 tests, OK**; los tests nuevos y los ajustados fallan
contra el código anterior. Este commit sí toca `evaluate_live_position_exit`,
así que el hook pre-push lo frena con razón.

## Hook pre-push: cubre recibos y el monitor de cuenta — 2026-09-22

El commit `3ebac26` (aceptar transacciones v1) cambió la guarda de versión de
`_parse_receipt_balances`, que decide si una orden real se reconcilia, y el
hook no lo frenó: `parse_*_receipt` y `fetch_finalized_solana_transaction` no
estaban en `RISK_PATTERN`. Se agregan esos tres nombres y
`live_account_exit_monitor_once` (única función que llama a
`evaluate_live_position_exit` con precios de cuenta). Comprobado con
`git diff -W` sobre `3ebac26`: ahora habría frenado por
`fetch_finalized_solana_transaction` y `parse_sell_receipt`. Solo amplía el
patrón; no quita nada.

## Notificaciones WSS sin Pump: memoria y freno por wallet — 2026-09-22 (noche)

Al ampliar el WSS a 14 wallets, decu entregó 44 notificaciones por segundo
sin un solo log de Pump: su dirección aparece mencionada en spam de tokens y
`logsSubscribe` por mención lo trae todo. Cada notificación era un `INSERT`
en `helius_standard_wss_notifications` (sin retención): 3,8 millones de filas
y ~500 MB por día, más 44 escrituras por segundo compitiendo con el stream y
paper en el mismo SQLite. Se sacó decu de `HELIUS_STANDARD_WSS_TRADERS` a
mano (quedan 13). Este cambio evita depender de que alguien lo vea.

- `record_helius_standard_wss_notification` ya no escribe las notificaciones
  sin logs de Pump: las cuenta en memoria por wallet
  (`HELIUS_STANDARD_WSS_UNSTORED`: cantidad, fallidas, bytes, primera y última)
  y devuelve `False`. Las que traen Pump se guardan exactamente igual. En
  `/api/helius-standard-wss-stats`, `last_24h.notifications` y
  `message_bytes` pasan a contar solo lo almacenado; el resto aparece en
  `unstored_notifications_since_start`, por wallet. Límite conocido: con
  proveedor Helius, `credit_estimate` ya no ve los bytes no almacenados
  (hoy el proveedor es Alchemy y ese estimado no aplica).
- Freno: `note_unstored_helius_standard_wss_notification` devuelve el ritmo
  del último minuto; si supera `HELIUS_STANDARD_WSS_MAX_OTHER_PER_MINUTE`
  (600; decu hacía 2.600), el worker manda `logsUnsubscribe` para esa wallet,
  la anota en `runtime.muted_wallets` (trader, hora, ritmo), imprime y avisa
  por Discord una sola vez. Al reconectar, las wallets mudas no se vuelven a
  suscribir; se limpia con un reinicio. `wallet_subscriptions_ready` compara
  contra `active_wallets` (seleccionadas menos mudas) y el timeout de
  suscripción también. Las notificaciones de tokens no entran al freno.

Tests: sin Pump no hay fila y sí contador; el freno dispara una sola vez al
cruzar el umbral y el tráfico lento nunca acumula; el worker con
`FakeWebSocket` desuscribe la wallet inundada, deja la otra, avisa, expone
`active_wallets=1` con `ready=true`, y tras un reconnect forzado solo
resuscribe la sobreviviente.
Verificación: **460 tests, OK**; los tres nuevos fallan contra el código
anterior. No toca el camino del dinero ni ningún flag.

## `scripts/ingest_coverage_report.py` — 2026-09-22 (noche)

Un comando para la medición diaria: `python scripts/ingest_coverage_report.py`
(token de `APP_TOKEN` en entorno o `.env`; `--base` para otro host). Lee
`/api/status`, `/api/watched-wallets`, `/api/helius-standard-wss-stats`,
`/api/rpc-fallback-stats`, `/api/signals` y `/api/training-stats`, imprime una
tabla por wallet (silenciosa, edad, notificaciones Pump y parseadas 24 h, no
almacenadas, muda, faltantes del fallback 24 h, señales 24 h) y guarda un
snapshot en `reports/ingest_coverage/<UTC>.json`; si hay uno anterior,
muestra el valor previo de wallets silenciosas. Solo lectura.

Baseline tomado 06:01 UTC, veinte minutos después de pasar a 13 wallets:
`silent: 9`; las nueve nuevas ya muestran notificaciones Pump (sapphy 7,
hdegroot 9, ily 8, decu 7, gr3gor14n 2) pero `parsed 0`, a confirmar mañana si
es `wallet_not_signer` o falta de tiempo. Los 66 `fetch_failed -32015` de la
ventana de 24 h son anteriores al fix v1.

# Informe para Codex — sesión 2026-09-22 (00:00–06:15 UTC)

## Commits

| Commit | Qué | Dinero | Producción |
|---|---|---|---|
| `07a865b` | `last_rpc_error_detail` (mensaje JSON-RPC redactado) + `stream_last_account_event_ts` / `stream_last_token_event_ts` | no | desplegado |
| `3ebac26` | Aceptar transacciones Solana versión 1 en ingesta y recibos; fixture real v1 | sí (guarda del parser de recibos) | desplegado |
| `2657f6a` | Slot del snapshot como orden real en salidas por precio de cuenta; columna `account_exit_last_slot` | sí (`apply=false`) | desplegado |
| `ebbc1c4` | Hook pre-push cubre `parse_*_receipt`, `fetch_finalized_solana_transaction`, `live_account_exit_monitor_once` | — | desplegado |
| `13ded74` | Notificaciones WSS sin Pump en memoria; freno automático por wallet (600/min) | no | **local** |
| `e9b1076` | `scripts/ingest_coverage_report.py` + baseline | no | **local** |

Producción = `ebbc1c4`. Los dos últimos esperan OK para push (no entran en
`RISK_PATTERN`; el hook no los frena).

## Hallazgos verificados en producción

1. **Helius muerto desde 17-09 03:12 UTC**: créditos free agotados
   (1.044.217/1.000.000). El 90 % de las 633 k transacciones del webhook eran
   entregas por los 30 tokens que el sync mantenía. `HELIUS_WEBHOOK_SYNC_APPLY`
   ahora `false` (usuario, 01:40 UTC).
2. **9 de 14 wallets ciegas desde entonces**, el WSS de Alchemy filtraba a 5.
   Usuario amplió a 14 (01:37 UTC) y luego a 13 (01:49 UTC) al sacar decu.
3. **`-32015` = transacciones versión 1.** Ambos `getTransaction` pedían
   `maxSupportedTransactionVersion: 0`. Fix desplegado; `fetch_errors: []`
   desde entonces y el fallback desbloqueó gr3gor14n, epicsealdarkeye,
   supermandev, Cooker.
4. **decu no sirve por `logsSubscribe` por mención**: 44 notificaciones/s de
   spam, cero Pump, 31.571 filas en 12 minutos. Fuera del WSS hasta que
   `13ded74` esté desplegado; aun así, para decu hace falta otro transporte
   (filtrar por programa Pump, no por mención) o confirmar que el fallback
   RPC no se ahoga con el mismo spam (`SIGNATURE_BACKLOG_REBASED` sugiere que
   sí).
5. El watchdog de PumpPortal a 120 s no es bug: `stream_last_account_event_ts`
   es `null` desde el deploy; PumpPortal solo entrega tokens suscritos.

## Números de cierre (06:01 UTC, `reports/ingest_coverage/2026-09-22T060106Z.json`)

WSS 13/13 suscripciones, sin error. `silent: 9` (todavía; edad 185–514 h, 3
nunca vistas). Nuevas con notificaciones Pump en 20 min: sapphy 7, hdegroot 9,
ily 8, decu 7, gr3gor14n 2; `parsed 0` en todas. Señales 24 h: slingoor 19,
Cooker 21, epicsealdarkeye 6, chriskogias 4. Dataset principal:
`training_eligible 274`, `excluded_unfresh 1312`.

## Pendiente, en orden

1. Push + verificación de `13ded74` y `e9b1076` (mañana, con OK).
2. Correr `scripts/ingest_coverage_report.py` a las 24 h del cambio de
   wallets: `silent` debe bajar, `parsed` de las nuevas debe ser > 0 o
   `unparsed_reasons` debe explicar por qué, `fetch_errors` vacío.
3. decu: decidir transporte. Opciones: `logsSubscribe` con `mentions` del
   programa Pump y filtrar por firmante (más volumen, menos spam) o
   `getSignaturesForAddress` con filtro de programa en el fallback.
4. Gates para operar siguen todos en `false`; sin modelo aprobable
   (`training_eligible < 300`, challenger con blocker de holdout). Nada de
   esto cambia hasta tener semanas de datos con 13 wallets.

## Qué vio el WSS de cada operación faltante — 2026-09-22

La medición de las 17:41 dejó una pregunta sin respuesta: gr3gor14n tenía 9
operaciones reales que nadie aplicó y 103 notificaciones con logs de Pump sin
un solo evento parseado. Ocho de las nueve son anteriores al fix de
transacciones v1, pero una es posterior, y con los datos de hoy no se puede
distinguir "el transporte nunca la trajo" de "la trajo y algo falló después".

`missing_events` en `/api/rpc-fallback-stats` agrega tres campos por
operación: `wss_notified` (hay alguna notificación para esa firma),
`wss_pump_logs` (si alguna traía logs de Pump; `null` si no hubo
notificación) y `wss_fetch_status` (el estado de
`helius_standard_wss_transactions`, por ejemplo `fetch_failed` o `unparsed`).
Las dos subconsultas filtran por `signature`, primera columna de la clave
primaria de ambas tablas, sobre una lista ya acotada a 20 filas.

Dato del contexto: gr3gor14n opera a través de Jupiter (`JUP6LkbZbjS1` en el
nivel superior, Pump como CPI interno) y firma en segundo lugar. Bajadas por
RPC, el parser las procesa bien (`parsed=1`, índice 0), así que el problema no
está en el parser.
Verificación: **461 tests, OK**; el test nuevo falla contra el código anterior.

## El fallback RPC puede aplicar lo que el WSS no entrega — 2026-09-22

Cerrado el diagnóstico del día: `missing_events` con los campos nuevos mostró
dos causas separadas. Seis operaciones (epicsealdarkeye, chriskogias) con
`wss_notified: true` y `wss_fetch_status: fetch_failed`, todas anteriores al
fix de transacciones v1: backlog ya resuelto. Y **catorce de gr3gor14n con
`wss_notified: false`**: la notificación nunca llegó.

Las dos que se inspeccionaron por RPC usan Address Lookup Tables (4 y 1) y van
por Jupiter, con gr3gor14n como segundo firmante; el parser las procesa bien.
De sus 30 transacciones más recientes, 22 son menciones ajenas sin ALT —todas
entregadas— y ninguna es propia. La correlación en la muestra es 2/2 faltantes
con ALT contra 22/22 entregadas sin ALT: `logsSubscribe` con `mentions` no
entrega las transacciones con ALT aunque la wallet esté en las claves
estáticas como firmante. Es una limitación del transporte, no del parser.

Importa más allá de gr3gor14n (que tiene 1 acierto en 102): cualquier trader
que pase a operar por un agregador se vuelve invisible para el único
transporte que aplica, sin aviso.

`RPC_FALLBACK_APPLY` (default `false`) hace que el fallback, además de
registrar el evento, escriba el recibo al inbox con
`record_helius_webhook_transactions(..., persist_inbox=True,
inbox_source="rpc")`, igual que el WSS. No hay camino nuevo: el consumidor del
inbox ya existente valida, reserva identidad y aplica.

Dos diferencias respecto de Helius, por la latencia de hasta 90 segundos del
fallback:

- El consumidor rutea según `market_event_inbox.source`. Una fila `rpc` se
  reserva en `processed_market_events` con `source='rpc'` —la atribución por
  transporte sigue siendo honesta— y se rutea con `allow_live_exits=False`
  además de `allow_live_buys=False`. Decidir una salida live con un precio de
  hasta 90 segundos atrás sería peor que no decidirla; las salidas siguen
  siendo del transporte en vivo. `/api/rpc-fallback-stats` lo expone como
  `affects_live_exits: false`.
- La cronología es la del bloque: el inbox guarda `block_event_ts` del evento,
  no la hora de detección, que corrompería los checkpoints.

La doble aplicación la sigue bloqueando `processed_market_events` por
identidad completa, el mismo árbitro que ya usan el stream, el webhook y el
WSS entre sí: si otro transporte ganó la operación, la fila queda `duplicate`
y no se rutea.

Tests: con el flag apagado no se escribe nada al inbox y con el flag encendido
se escribe una fila `rpc` con el tiempo de bloque; una fila `rpc` se reserva
con `source='rpc'` y se rutea con ambas banderas live en falso; una fila `rpc`
que otro transporte ya aplicó queda duplicada sin rutear.
Verificación: **464 tests, OK**; los tests nuevos fallan contra el código
anterior. El flag queda apagado: activarlo es una decisión aparte.

## El transporte llega hasta `trades` y `evaluations` — 2026-09-22

Antes de activar `RPC_FALLBACK_APPLY` faltaba poder distinguir sus filas.
`route_market_event()` pasaba `source="live"` fijo, así que una señal del
fallback quedaba idéntica a una del stream en `trades`, `evaluations` y por lo
tanto en el dataset. Importa porque paper abre al market cap del evento, que
es el precio del momento del trade y no el de la detección: una entrada del
fallback registra un fill que no era alcanzable, y sin marca el modelo
aprende de entradas optimistas sin forma de filtrarlas después.

La primera idea —sobrescribir `trades.source` con el transporte— era un bug
esperando: cinco consultas filtran por `source = 'live'` para decir "real, no
demo", entre ellas el consenso de scoring (`app.py:9514`) y las estadísticas
de trader (`6454`, `6705`). Habrían dejado fuera todo lo que no viniera del
stream. `source` sigue significando real-o-demo; el transporte va en una
columna nueva.

- `trades.transport` y `evaluations.transport`, ambas `DEFAULT 'live'` por
  `ALTER TABLE` idempotente: las filas históricas quedan como lo que eran.
- `transport` viaja por `route_market_event()` → `save_trade()` →
  `evaluate_buy()`. El consumidor del inbox pasa el suyo (`helius` o `rpc`);
  el stream conserva `live`.
- `/api/signals` expone `transport` por señal.
- El guard del detector de silencio pasa de `source == "live"` a
  `source != "demo"`: pregunta si la wallet nos está llegando, no por dónde.
  Con el transporte real en juego, la condición vieja habría marcado como
  silenciosas a wallets que sí entregan por Helius o RPC.
- `signature_overlap` en las estadísticas del WSS ya comparaba
  `trades.source = 'helius'`, que nunca podía darse porque todo se escribía
  como `live`: `trades_helius` era siempre 0. Ahora mira `transport` y mide lo
  que decía medir.

Tests: una señal `rpc` queda con `source='live'` y `transport='rpc'` en
`trades` y en `evaluations`, y cuenta como entrega de la wallet; las llamadas
de ruteo trasladan el transporte; el test de solapamiento del WSS siembra
`transport` y vuelve a distinguir los transportes.
Verificación: **465 tests, OK**; el test nuevo falla contra el código anterior
(`no such column: transport`). `RPC_FALLBACK_APPLY` sigue apagado.

## El salto 36,1% -> 28,7% era el umbral, y cruza el equilibrio — 2026-09-22

El dataset on-chain (`account_checkpoints_v1`, 609 filas en producción, 308
mints, 7 traders, tasa base 24,8 %) entrena un modelo sin bloqueos de
deployment, con holdout de 122 filas sin ningún mint compartido con el
entrenamiento y 76 filas purgadas: ROC AUC 0,760, average precision 0,472
contra 0,213 del baseline tonto, y la señal sobrevive al balanceo por trader
(AUC 0,736). Es la primera evidencia real del proyecto, no infraestructura.

Pero el reporte del clasificador daba 36,1 % de precisión y el backtest
económico 28,7 %, y el equilibrio del proxy con 2 % de coste está en 34,3 %:
la conclusión cambiaba de signo según cuál se mirara. `scripts/reconcile_
threshold_economics.py` lo resuelve reutilizando el split y el modelo de los
scripts existentes.

**Misma población, mismo modelo, mismas probabilidades de holdout. La única
diferencia es el umbral.** `select_threshold` elige 0,45 por validación
cruzada, que selecciona 80 señales con 28,7 %; a 0,50 selecciona 61 con
36,1 %. No hay filas excluidas ni subconjuntos distintos.

| umbral | selecc | aciertos | precisión | neto@0% | neto@2% | neto@3% |
|---|---|---|---|---|---|---|
| 0,45 (CV) | 80 | 23 | 28,7 % | +0,05 | −1,55 | −2,35 |
| 0,50 | 61 | 22 | 36,1 % | +1,60 | **+0,38** | −0,23 |
| 0,70 | 23 | 12 | 52,2 % | +1,90 | +1,44 | +1,21 |

Hallazgo de fondo: **el umbral se afina contra una métrica de clasificación,
no contra la economía**, y por eso la validación cruzada eligió el peor de los
dos económicamente.

Lo que NO se hace con esto: elegir 0,50 ni 0,70 porque rinden mejor acá. Eso
sería ajustar un hiperparámetro contra el holdout. Y los intervalos de Wilson
al 95 % lo dejan claro: **todos los umbrales cruzan el equilibrio**
(0,50 → [25,2 %, 48,6 %]; 0,70 → [33,0 %, 70,8 %] con n=23). Con 122 filas de
holdout la muestra no distingue rentable de no rentable.

Descomposición del 2 %: es **solo** PumpPortal Lightning, 1 % por lado,
constante en `compare_model_economics.py:6`. No incluye fee del protocolo,
slippage ni priority fees. Para dimensionarlo: `MAX_SLIPPAGE_PCT = 5.0`
permite hasta 5 % de slippage *por lado*, así que el peor escenario del script
(5 % ida y vuelta) queda por debajo de lo que la propia configuración de
riesgo tolera solo en slippage. El 2 % es un piso, no una estimación
conservadora.

Veredicto corregido: no es "pierde". Es **"la muestra todavía no puede
decidir"**, y el costo real está sin descomponer. Live sigue cerrado.

## El payoff escalonado real: peor que el proxy, no mejor — 2026-09-22

`scripts/staged_payoff_backtest.py` reproduce `decide_live_position_exit()`
sobre las trayectorias on-chain, consumiendo las probabilidades del holdout ya
generadas: no reentrena, no mueve umbrales y no elige umbral por resultado.

La hipótesis era que el proxy plano (+0,25 / −0,10) subestimaba a los
ganadores grandes. **Es al revés.** La política real rinde peor que la
etiqueta por dos razones estructurales:

- El stop está en **−20 %**, mientras la etiqueta usaba −10 %. Los perdedores
  se sostienen el doble.
- Un TP25 vende **solo el 25 %** de la posición. Una señal que toca +25 % y
  después cae al stop liquida 25 % a +25 % y 75 % a −20 %: neto −0,0875. La
  etiqueta la contaba como acierto completo (+0,25).

Por eso a umbral 0,45 la etiqueta daba 23 aciertos de 80 y la política deja
17 posiciones con PnL positivo.

| umbral | selecc | gana | pierde | sin mov. | stop | neto total | neto medio | IC95 del medio |
|---|---|---|---|---|---|---|---|---|
| 0,45 (CV) | 80 | 17 | 41 | 22 | 44 | −5,344 | −0,0668 | **[−0,127, −0,004]** |
| 0,50 | 61 | 16 | 24 | 21 | 26 | −0,927 | −0,0152 | [−0,085, +0,065] |
| 0,55 | 58 | 16 | 22 | 20 | 24 | −0,353 | −0,0061 | [−0,080, +0,074] |
| 0,70 | 23 | 9 | 5 | 9 | 6 | +1,450 | +0,0630 | [−0,043, +0,193] |

**En el umbral que la validación cruzada eligió, el intervalo de confianza es
enteramente negativo.** Es el primer resultado estadísticamente limpio del
proyecto, y dice que pierde. En los umbrales altos el intervalo cruza cero:
indistinguible de no operar.

La mediana del PnL neto es −0,01 en casi todos los umbrales: **la operación
típica pierde exactamente la comisión**. La media la mueven unos pocos
ganadores grandes. A umbral 0,70 las cinco mejores operaciones aportan el
161 % del PnL total —el resto en conjunto resta—, con n=23. Eso no es una
estrategia, es una muestra chica con cola gorda.

Límites declarados, no tapados:

- **Ambigüedad**: solo hay 5 observaciones por señal. Entre dos checkpoints el
  precio no se ve. 21 de 80 señales a umbral 0,45 (26 %) tienen tramos donde
  el orden entre TP y stop es indeterminable. No se asume el caso favorable.
- **El último 25 % no tiene salida por precio.** Tras el TP100 la política
  solo lo cierra con el stop, con la venta total del trader o con una parcial
  suya. Y el stop se mide **desde la entrada**, así que después de un +100 %
  ese resto solo se vende si el precio cae un 60 % desde el pico. 36 de 80
  posiciones terminan así. Valorarlo al último precio sumaría +1,317, pero esa
  plata **no es realizable con la política actual** y por eso va aparte.
- **Las ventas del trader de origen no se simulan**: la trayectoria on-chain
  no trae sus eventos. `TRADER_EXIT` y `TRADER_PARTIAL` cerrarían posiciones
  antes, a menudo mejor. Es una omisión conocida que puede subestimar.
- El coste aplicado es 1 % por lado, **solo PumpPortal Lightning**, y se cobra
  sobre la compra entera más cada fracción vendida.

Conclusión: con la política de salidas que el proyecto ya tenía, estas señales
no ganan plata. El problema no es solo el modelo: **la política de salidas
tiene dos defectos propios** —stop al doble de distancia que la etiqueta que
entrena el modelo, y un 25 % de cada posición sin regla de cierre— que
conviene revisar antes de culpar al clasificador.

## Alinear la etiqueta al stop real: el mismatch no era el problema — 2026-09-22

`scripts/relabel_stop_alignment.py` re-etiqueta las 609 observaciones con el
stop que la ejecución realmente usa (−20 %) sobre las mismas trayectorias y el
mismo split, y construye además un objetivo económico con el signo del PnL que
la política habría realizado.

**Solo 4 etiquetas de 609 cambian (0,7 %).** La tasa base pasa de 24,8 % a
25,5 % y las métricas quedan iguales: AUC 0,760 → 0,750, AP 0,472 → 0,471.

Con cinco checkpoints gruesos, un token que perfora −10 % casi siempre perfora
también −20 % en el mismo tramo, o se recupera antes de que se lo observe. La
resolución no alcanza para distinguirlos.

**Corrección de la hipótesis anterior:** que el modelo entrenara con −10 % y
la ejecución cortara en −20 % era coherentemente feo, pero **no es la causa de
que la estrategia pierda plata**. La causa es la otra: **el TP25 vende solo el
25 % de la posición y deja el 75 % expuesto al stop**. De las ~151 señales
etiquetadas como ganadoras, **38 (el 25 %) terminan económicamente negativas**
por exactamente eso.

### Objetivo económico

- La política cierra entera solo el **55,5 %** de las posiciones; el 44,5 %
  queda con el último cuarto abierto, sin regla que lo cierre.
- Tasa base del objetivo económico: **19,0 %** en total, **10,9 % entre las
  cerradas** (n=338). Bajo la política real, una de cada nueve operaciones
  cerradas gana plata.
- Entrenando contra ese objetivo con el mismo split: **AUC 0,726, AP 0,385**
  contra una base de 16,4 % (lift ≈ 2,3×). **La señal sobrevive a la pregunta
  difícil**; lo que no alcanza es el tamaño del efecto frente a los costes.
- Trayectorias ambiguas: 187 de 609 (30,7 %).

### Contrafactual: stop en −10 % en vez de −20 %

Mismo ladder, mismos costes, mismas predicciones, solo cambia el stop:

| umbral | stop | neto total | neto medio | IC95 | mediana |
|---|---|---|---|---|---|
| 0,45 | −20 % | −5,344 | −0,0668 | [−0,127, −0,004] | −0,0931 |
| 0,45 | −10 % | −5,097 | −0,0637 | [−0,118, −0,006] | −0,1356 |
| 0,50 | −20 % | −0,927 | −0,0152 | [−0,085, +0,065] | −0,0100 |
| 0,50 | −10 % | −1,347 | −0,0221 | [−0,087, +0,054] | −0,0781 |

**El stop no decide nada.** Cortar antes mejora marginalmente a umbral 0,45 y
empeora a 0,50; ambos intervalos se solapan casi por completo. No hay
evidencia de que el −20 % esté destruyendo valor, ni de que el −10 % lo
rescate. La palanca está en otro lado.

Prioridad que queda: **definir la salida del último 25 %** es ahora el
bloqueo real, porque sin ella el 44,5 % de las operaciones no tiene resultado
económico definido y no hay función de payoff completa que un modelo pueda
aprender.
