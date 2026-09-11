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
