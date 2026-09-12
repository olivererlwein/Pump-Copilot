# Graph Report - pump fun  (2026-09-12)

## Corpus Check
- 46 files · ~85,281 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1061 nodes · 2196 edges · 59 communities (49 shown, 8 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 50 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8e003be4`
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
- startup
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
- get_shadow_stats
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- db
- CLAUDE_NOTES.md
- app.py
- TraderQualityProfileTests
- apply_paper_event
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- StreamRoutingTests
- route_market_event
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- LegacyDataMigrationTests
- pump_receipt
- pre-push
- PaperPositionIdempotencyTests
- migrate_database
- El scoring sí funciona — y dónde no, 2026-09-11
- `update_paper_position()` idempotente — 2026-09-11
- PreEntryEventGuardTests
- execute_pumpportal_lightning_buy
- validate_market_event_inbox_once
- InboxRoundTripTests
- MarketEventInboxValidationTests
- calculate_trader_quality_candidate
- poll_rpc_fallback_once

## God Nodes (most connected - your core abstractions)
1. `db()` - 119 edges
2. `auth()` - 74 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 36 edges
5. `pump_receipt()` - 23 edges
6. `TraderQualityProfileTests` - 23 edges
7. `ExecutionAdapterTests` - 22 edges
8. `InboxRoundTripTests` - 20 edges
9. `main()` - 18 edges
10. `PreEntryEventGuardTests` - 18 edges

## Surprising Connections (you probably didn't know these)
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `poll_rpc_fallback_once()` --calls--> `fetch_confirmed_transaction()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `poll_rpc_fallback_once()` --calls--> `fetch_signatures_for_address()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `poll_rpc_fallback_once()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `record_helius_webhook_transactions()` --calls--> `parse_tracked_token_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py

## Import Cycles
- None detected.

## Communities (59 total, 8 thin omitted)

### Community 0 - "train_baseline_model.py"
Cohesion: 0.08
Nodes (37): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+29 more)

### Community 1 - "TokenHistoryIdempotencyTests"
Cohesion: 0.14
Nodes (5): LegacyInboxRenumberTests, El historial alimenta el scoring: una fila repetida lo sesga. Con reintentos de…, Documenta una limitación abierta, no un comportamiento deseado. PumpPortal…, Las filas de inbox viejas llevan índice de log, no ordinal., TokenHistoryIdempotencyTests

### Community 2 - "ValueError"
Cohesion: 0.07
Nodes (26): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), fetch_finalized_solana_transaction(), fetch_solana_signature_status(), normalize_solana_signature(), prepare_pumpportal_lightning_sell(), reconcile_pumpportal_execution_order() (+18 more)

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

### Community 7 - "startup"
Cohesion: 0.17
Nodes (17): cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), expire_old_signal_outcomes(), get_persistent_kill_switch(), process_signal_outcomes_event(), pumpportal_execution_reconciliation_worker(), reconcile_finished_signal_outcomes(), reconcile_pending_pumpportal_execution_orders() (+9 more)

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
Cohesion: 0.07
Nodes (89): api_live_execution_readiness(), api_live_positions(), api_rpc_fallback_stats(), api_shadow_predictions(), api_shadow_stats(), api_training_dataset(), api_watched_wallets(), auth() (+81 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "get_shadow_stats"
Cohesion: 0.14
Nodes (15): assess_shadow_challenger(), check_watched_wallet_silence(), compare_shadow_models(), fetch_solana_balance_sol(), get_shadow_stats(), get_watched_wallet_activity(), maybe_send_shadow_review_alert(), post_discord_alert() (+7 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.08
Nodes (26): api_trader_quality_profile(), calculate_trader_profile_score(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide. (+18 more)

### Community 31 - "db"
Cohesion: 0.08
Nodes (34): api_helius_webhook_sample(), api_helius_webhook_stats(), calculate_copyability_score(), count_open_positions(), create_execution_order_idempotent(), db(), establish_market_event_inbox_activation(), get_consensus_trader_count() (+26 more)

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.15
Nodes (12): Cuándo volver a encenderlo, Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Por qué, Por qué apagarlo y no ajustarlo (+4 more)

### Community 34 - "app.py"
Cohesion: 0.11
Nodes (32): api_training_checkpoint_freshness(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), assess_live_model_approval(), build_model_features(), create_signal_outcome() (+24 more)

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

### Community 43 - "route_market_event"
Cohesion: 0.09
Nodes (30): decide_live_position_exit(), evaluate_live_position_exit(), helius_webhook(), is_pumpportal_error_message(), mark_market_event_processed(), mark_signature_processed(), mark_stream_problem(), mark_stream_recovered() (+22 more)

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "LegacyDataMigrationTests"
Cohesion: 0.35
Nodes (3): LegacyDataMigrationTests, La transición con datos reales viejos, que es donde esto puede doler. Una base…, Una base como la de producción antes de este bloque.

### Community 46 - "pump_receipt"
Cohesion: 0.05
Nodes (35): _base58_encode(), _event_payloads(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _is_signed_by(), _optional_post_token_amount(), _parse_official_pump_events(), _parse_pump_amm_trade() (+27 more)

### Community 48 - "PaperPositionIdempotencyTests"
Cohesion: 0.14
Nodes (8): PaperPositionIdempotencyTests, Si la transacción falla, no queda ni el efecto ni la marca. El caso peligroso…, Un evento reintentado no debe aplicarse dos veces a la misma posición.…, Una transacción puede traer varias operaciones Pump válidas. Con la firma sola…, La auditoría y el efecto son atómicos. Si la auditoría quedara fuera de la…, Convertir un índice inválido en 0 crea colisiones. Si un índice roto se…, El cierre puede quedar confirmado y la limpieza posterior fallar.…, `BEGIN IMMEDIATE` toma el lock al abrir la transacción. Una excepción entre ese…

### Community 49 - "migrate_database"
Cohesion: 0.17
Nodes (12): migrate_database(), migrate_inbox_event_index_to_ordinal(), migrate_market_event_inbox_index_scheme(), migrate_market_event_inbox_validation(), migrate_processed_market_events(), migrate_shadow_predictions_for_multiple_models(), migrate_token_history_identity(), Le da identidad a las filas de historial anteriores a `event_id`. Sin esto,… (+4 more)

### Community 50 - "El scoring sí funciona — y dónde no, 2026-09-11"
Cohesion: 0.29
Nodes (7): Consecuencia para el backfill y la watchlist, El scoring sí funciona — y dónde no, 2026-09-11, La explicación: el scoring separa de verdad, Los dos límites reales, No era sesgo de supervivencia, Nota sobre la lógica de salida, Y no es el confundidor de decu

### Community 51 - "`update_paper_position()` idempotente — 2026-09-11"
Cohesion: 0.07
Nodes (28): Correcciones tras la revisión de Codex, Cuarta revisión: el helper no rechazaba lo que decía rechazar, Deduplicación global por evento, Dos pruebas más, por el router (pedido de Codex), El problema, Límites, Parser de Helius por token y frontera de activación, Punto 2: identidad completa en las salidas live (+20 more)

### Community 52 - "PreEntryEventGuardTests"
Cohesion: 0.08
Nodes (8): PreEntryEventGuardTests, El índice también tiene que llegar al historial por el recorrido real., La guarda tiene que sobrevivir el recorrido real, no solo la función. El patrón…, Un evento anterior a la entrada no pertenece a esta posición. Con webhooks…, El índice tiene que sobrevivir el recorrido real, no solo la llamada.…, RouterEventIndexTests, RouterPreEntryGuardTests, RouterTokenHistoryTests

### Community 53 - "execute_pumpportal_lightning_buy"
Cohesion: 0.31
Nodes (10): build_pumpportal_lightning_buy_payload(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), fetch_sol_usd_quote(), get_execution_order_status(), get_live_execution_readiness(), prepare_pumpportal_lightning_buy(), require_live_trading() (+2 more)

### Community 54 - "validate_market_event_inbox_once"
Cohesion: 0.20
Nodes (10): claim_market_event_inbox_validation_batch(), finish_market_event_inbox_validation(), market_event_from_inbox_row(), market_event_inbox_validation_worker(), Reconstruye el evento normalizado guardado en `market_event_inbox`. La fila…, Reserva eventos observados para validarlos sin ejecutar sus efectos. La reserva…, Finaliza una reserva solo si todavía pertenece al mismo worker., Valida un lote del inbox sin llamar al router ni aplicar efectos. (+2 more)

### Community 55 - "InboxRoundTripTests"
Cohesion: 0.18
Nodes (3): InboxRoundTripTests, Una fila escrita antes de que el índice viajara adentro del evento. Es el caso…, El índice tiene que sobrevivir el viaje completo, no solo el parser. Webhook,…

### Community 56 - "MarketEventInboxValidationTests"
Cohesion: 0.11
Nodes (3): MarketEventDeduplicationTests, MarketEventInboxActivationTests, MarketEventInboxValidationTests

### Community 57 - "calculate_trader_quality_candidate"
Cohesion: 0.25
Nodes (9): beta_posterior_rate(), calculate_trader_quality_candidate(), clamp_trader_quality(), Calidad por encogimiento continuo hacia el prior neutral. El posterior Beta ya…, Intervalo de Wilson. Devuelve None si no hay muestras., Media posterior Beta con el mismo prior neutral del score vigente., TP25 antes de SL10, separado del resto de dimensiones., summarize_trader_entry_quality() (+1 more)

### Community 58 - "poll_rpc_fallback_once"
Cohesion: 0.33
Nodes (6): get_rpc_fallback_wallet_states(), mark_rpc_fallback_events_alerted(), poll_rpc_fallback_once(), reconcile_rpc_fallback_events(), rpc_fallback_shadow_worker(), update_rpc_fallback_wallet_state()

## Knowledge Gaps
- **156 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+151 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 396 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.962370786) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.981185498) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.981185287) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PreEntryEventGuardTests` connect `PreEntryEventGuardTests` to `PaperPositionIdempotencyTests`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `PaperPositionIdempotencyTests` connect `PaperPositionIdempotencyTests` to `PreEntryEventGuardTests`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 38 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 38 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _156 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `train_baseline_model.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0803312629399586 - nodes in this community are weakly interconnected._
- **Should `TokenHistoryIdempotencyTests` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Should `ValueError` be split into smaller, more focused modules?**
  _Cohesion score 0.06666666666666667 - nodes in this community are weakly interconnected._