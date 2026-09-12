# Graph Report - pump fun  (2026-09-12)

## Corpus Check
- 46 files · ~83,225 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1023 nodes · 2122 edges · 58 communities (47 shown, 9 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 47 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `98fba49b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- train_baseline_model.py
- TokenHistoryIdempotencyTests
- ValueError
- What You Must Do When Invoked
- Ablación de features — paso 0 antes del backfill, 2026-09-11
- graphify reference: extra exports and benchmark
- WatchedWalletSilenceTests
- execute_pumpportal_lightning_buy
- graphify reference: query, path, explain
- ShadowPredictionTests
- Q: ¿Cuál es el flujo completo desde que recibimos información de un token/trader hasta que se genera una señal?
- Q: ¿Qué componentes participan en paper trading?
- Q: ¿Dónde deberíamos integrar Binance sin acoplarlo directamente a la lógica específica de Pump.fun?
- Q: ¿Qué archivos o módulos serían afectados si modificamos el sistema de scoring?
- validate_training_dataset.py
- Pump Copilot — Phone MVP
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- AGENTS.md
- extraction-spec.md
- Q: sigamos
- auth
- LiveCopyDispatchTests
- ExecutionAdapterTests
- post
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- get_shadow_stats
- CLAUDE_NOTES.md
- db
- TraderQualityProfileTests
- apply_paper_event
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- StreamRoutingTests
- app.py
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- api_watched_wallets
- parse_watched_wallet_pump_events
- pre-push
- PaperPositionIdempotencyTests
- api_rpc_fallback_stats
- El scoring sí funciona — y dónde no, 2026-09-11
- `update_paper_position()` idempotente — 2026-09-11
- PreEntryEventGuardTests
- open_paper_position
- startup
- InboxRoundTripTests
- MarketEventInboxValidationTests
- api_trader_quality_profile

## God Nodes (most connected - your core abstractions)
1. `db()` - 117 edges
2. `auth()` - 74 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 36 edges
5. `TraderQualityProfileTests` - 23 edges
6. `ExecutionAdapterTests` - 22 edges
7. `parse_watched_wallet_pump_events()` - 20 edges
8. `main()` - 18 edges
9. `PreEntryEventGuardTests` - 18 edges
10. `pump_receipt()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `record_finalized_sell_position()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `record_helius_webhook_transactions()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `record_finalized_buy_position()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py

## Import Cycles
- None detected.

## Communities (58 total, 9 thin omitted)

### Community 0 - "train_baseline_model.py"
Cohesion: 0.08
Nodes (37): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+29 more)

### Community 1 - "TokenHistoryIdempotencyTests"
Cohesion: 0.15
Nodes (5): LegacyInboxRenumberTests, El historial alimenta el scoring: una fila repetida lo sesga. Con reintentos de…, Documenta una limitación abierta, no un comportamiento deseado. PumpPortal…, Las filas de inbox viejas llevan índice de log, no ordinal., TokenHistoryIdempotencyTests

### Community 2 - "ValueError"
Cohesion: 0.06
Nodes (28): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_buy_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), fetch_finalized_solana_transaction(), fetch_sol_usd_quote(), fetch_solana_signature_status(), normalize_solana_signature() (+20 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "Ablación de features — paso 0 antes del backfill, 2026-09-11"
Cohesion: 0.18
Nodes (11): Ablación de features — paso 0 antes del backfill, 2026-09-11, Cambio en el script, Conclusión para el backfill, Conclusión revisada, Corrección de la ablación — prueba de identidad, 2026-09-11, Dato de partida corregido, Hallazgo principal: el modelo solo apuesta a un trader, Pero la concentración sigue siendo el problema, por otra vía (+3 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "WatchedWalletSilenceTests"
Cohesion: 0.07
Nodes (6): PumpPortalBalanceTests, PumpPortalMessageTests, Una wallet que deja de entregar con el stream sano debe ser visible., ShadowReviewAlertTests, StreamStateTests, WatchedWalletSilenceTests

### Community 7 - "execute_pumpportal_lightning_buy"
Cohesion: 0.23
Nodes (13): api_live_execution_readiness(), assess_live_model_approval(), demo_liquidity_check(), demo_live_guard(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), get_execution_order_status(), get_live_execution_readiness() (+5 more)

### Community 8 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 9 - "ShadowPredictionTests"
Cohesion: 0.12
Nodes (3): FakeShadowModel, ShadowPredictionMigrationTests, ShadowPredictionTests

### Community 10 - "Q: ¿Cuál es el flujo completo desde que recibimos información de un token/trader hasta que se genera una señal?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Cuál es el flujo completo desde que recibimos información de un token/trader hasta que se genera una señal?, Source Nodes

### Community 11 - "Q: ¿Qué componentes participan en paper trading?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Qué componentes participan en paper trading?, Source Nodes

### Community 12 - "Q: ¿Dónde deberíamos integrar Binance sin acoplarlo directamente a la lógica específica de Pump.fun?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Dónde deberíamos integrar Binance sin acoplarlo directamente a la lógica específica de Pump.fun?, Source Nodes

### Community 13 - "Q: ¿Qué archivos o módulos serían afectados si modificamos el sistema de scoring?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Qué archivos o módulos serían afectados si modificamos el sistema de scoring?, Source Nodes

### Community 16 - "Pump Copilot — Phone MVP"
Cohesion: 0.40
Nodes (4): Ejecutar, Incluye, Pump Copilot — Phone MVP, Seguridad

### Community 17 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 18 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 19 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 24 - "Q: sigamos"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: sigamos, Source Nodes

### Community 25 - "auth"
Cohesion: 0.11
Nodes (53): auth(), can_retry_execution(), check_execution_timeout(), create_execution_order(), demo_can_retry(), demo_cannot_retry_risk(), demo_concurrent_idempotency(), demo_controlled_retry() (+45 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "post"
Cohesion: 0.15
Nodes (19): demo_close_old(), demo_duplicate_check(), demo_exit_close(), demo_invalid_event(), demo_partial_close(), demo_partial_sell(), demo_profit(), demo_stop() (+11 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.07
Nodes (31): beta_posterior_rate(), calculate_trader_profile_score(), calculate_trader_quality_candidate(), clamp_trader_quality(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile() (+23 more)

### Community 31 - "get_shadow_stats"
Cohesion: 0.40
Nodes (6): api_shadow_stats(), assess_shadow_challenger(), compare_shadow_models(), get_shadow_stats(), maybe_send_shadow_review_alert(), summarize_shadow_predictions()

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.15
Nodes (12): Cuándo volver a encenderlo, Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Por qué, Por qué apagarlo y no ajustarlo (+4 more)

### Community 34 - "db"
Cohesion: 0.09
Nodes (35): api_helius_webhook_sample(), api_helius_webhook_stats(), calculate_copyability_score(), cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), create_execution_order_idempotent(), db(), expire_old_signal_outcomes() (+27 more)

### Community 35 - "TraderQualityProfileTests"
Cohesion: 0.08
Nodes (3): Perfil integral y observacional de calidad del trader., TraderQualityCandidateTests, TraderQualityProfileTests

### Community 36 - "apply_paper_event"
Cohesion: 0.25
Nodes (8): apply_paper_event(), decide_paper_position_action(), Registra un evento de posición. Con ``connection`` escribe dentro de la…, Lee un timestamp que guardamos nosotros; inservible cuenta como ausente.…, Decide qué hacer con una posición paper. No toca la base de datos. Separado de…, Aplica el evento en una sola transacción y describe qué pasó. Deliberadamente…, save_position_event(), stored_block_event_ts()

### Community 37 - "Conectar el webhook al pipeline — brief para Codex, 2026-09-11"
Cohesion: 0.29
Nodes (7): Conectar el webhook al pipeline — brief para Codex, 2026-09-11, Evidencia acumulada, LA DEPENDENCIA CRÍTICA (revisar antes de estimar), Lo que ya existe, Restricciones, Riesgos a considerar, Situación

### Community 38 - "Piloto del webhook de Helius — resultados, 2026-09-11"
Cohesion: 0.33
Nodes (6): Entrega, Estado del saldo de SOL (2026-09-11), Falso diagnóstico corregido en el camino, Latencia: parejos en promedio, muy distintos en dispersión, Piloto del webhook de Helius — resultados, 2026-09-11, Qué quedó verificado

### Community 39 - "Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)"
Cohesion: 0.20
Nodes (10): Archivos modificados en esta parte, Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte), Decisión de producto pendiente (no es código), Diagnóstico: el problema no era la fórmula, Efecto medido sobre datos reales, Parte 1 — Recuperar la evidencia decidible, Parte 2 — Encogimiento continuo en vez de escalón, Parte 3 — El dinero real solo sigue a traders medidos (+2 more)

### Community 40 - "Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)"
Cohesion: 0.12
Nodes (17): Advertencia importante sobre los datos, Causas descartadas, Confirmación contra producción (2026-09-10), Descartado en el camino, Hipótesis principal, Lo que sí funciona: la alerta, Monitor RPC fallback — baseline y diagnóstico (2026-09-10), Observabilidad agregada para cerrar el diagnóstico (+9 more)

### Community 41 - "Perfil integral de calidad del trader (shadow) — 2026-09-10"
Cohesion: 0.22
Nodes (9): Archivos modificados, Decisiones estadísticas, Endpoint nuevo, Funciones nuevas (`app.py`), Limitaciones conocidas (explícitas, no ocultas), Perfil integral de calidad del trader (shadow) — 2026-09-10, Qué NO se tocó, Qué se agregó (+1 more)

### Community 43 - "app.py"
Cohesion: 0.08
Nodes (46): api_shadow_predictions(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), build_model_features() (+38 more)

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "api_watched_wallets"
Cohesion: 0.50
Nodes (4): api_watched_wallets(), get_watched_wallet_activity(), Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…, Entrega de datos por wallet vigilada: detecta silencios individuales.

### Community 46 - "parse_watched_wallet_pump_events"
Cohesion: 0.06
Nodes (31): poll_rpc_fallback_once(), _base58_encode(), _event_payloads(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _is_signed_by(), _parse_pump_amm_trade(), _parse_pump_trade() (+23 more)

### Community 48 - "PaperPositionIdempotencyTests"
Cohesion: 0.14
Nodes (8): PaperPositionIdempotencyTests, Si la transacción falla, no queda ni el efecto ni la marca. El caso peligroso…, Un evento reintentado no debe aplicarse dos veces a la misma posición.…, Una transacción puede traer varias operaciones Pump válidas. Con la firma sola…, La auditoría y el efecto son atómicos. Si la auditoría quedara fuera de la…, Convertir un índice inválido en 0 crea colisiones. Si un índice roto se…, El cierre puede quedar confirmado y la limpieza posterior fallar.…, `BEGIN IMMEDIATE` toma el lock al abrir la transacción. Una excepción entre ese…

### Community 49 - "api_rpc_fallback_stats"
Cohesion: 0.67
Nodes (3): api_rpc_fallback_stats(), get_rpc_fallback_stats(), Auditoría RPC de trades Pump ausentes del stream de PumpPortal.

### Community 50 - "El scoring sí funciona — y dónde no, 2026-09-11"
Cohesion: 0.29
Nodes (7): Consecuencia para el backfill y la watchlist, El scoring sí funciona — y dónde no, 2026-09-11, La explicación: el scoring separa de verdad, Los dos límites reales, No era sesgo de supervivencia, Nota sobre la lógica de salida, Y no es el confundidor de decu

### Community 51 - "`update_paper_position()` idempotente — 2026-09-11"
Cohesion: 0.08
Nodes (25): Correcciones tras la revisión de Codex, Cuarta revisión: el helper no rechazaba lo que decía rechazar, Deduplicación global por evento, Dos pruebas más, por el router (pedido de Codex), El problema, Límites, Punto 2: identidad completa en las salidas live, Punto 3: protección contra eventos anteriores a la entrada (+17 more)

### Community 52 - "PreEntryEventGuardTests"
Cohesion: 0.07
Nodes (11): LegacyDataMigrationTests, PreEntryEventGuardTests, El índice también tiene que llegar al historial por el recorrido real., La guarda tiene que sobrevivir el recorrido real, no solo la función. El patrón…, Un evento anterior a la entrada no pertenece a esta posición. Con webhooks…, El índice tiene que sobrevivir el recorrido real, no solo la llamada.…, La transición con datos reales viejos, que es donde esto puede doler. Una base…, Una base como la de producción antes de este bloque. (+3 more)

### Community 53 - "open_paper_position"
Cohesion: 0.13
Nodes (17): api_live_positions(), count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_exit_open(), demo_live_position(), demo_mode_isolation(), demo_paper_mode_save() (+9 more)

### Community 54 - "startup"
Cohesion: 0.05
Nodes (48): check_watched_wallet_silence(), claim_market_event_inbox_validation_batch(), fetch_solana_balance_sol(), finish_market_event_inbox_validation(), helius_webhook(), is_pumpportal_error_message(), mark_market_event_processed(), mark_stream_problem() (+40 more)

### Community 55 - "InboxRoundTripTests"
Cohesion: 0.19
Nodes (3): InboxRoundTripTests, El índice tiene que sobrevivir el viaje completo, no solo el parser. Webhook,…, Una fila escrita antes de que el índice viajara adentro del evento. Es el caso…

### Community 57 - "api_trader_quality_profile"
Cohesion: 0.50
Nodes (4): api_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide., Perfil integral de cada trader vigilado.

## Knowledge Gaps
- **153 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+148 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 378 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.962370786) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.981185498) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.981185287) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `InboxRoundTripTests` connect `InboxRoundTripTests` to `parse_watched_wallet_pump_events`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 37 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 37 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _153 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `train_baseline_model.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0803312629399586 - nodes in this community are weakly interconnected._
- **Should `ValueError` be split into smaller, more focused modules?**
  _Cohesion score 0.06338028169014084 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `WatchedWalletSilenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.07126436781609195 - nodes in this community are weakly interconnected._