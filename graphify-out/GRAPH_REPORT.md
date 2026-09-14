# Graph Report - pump fun  (2026-09-13)

## Corpus Check
- 48 files · ~90,391 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1162 nodes · 2410 edges · 72 communities (59 shown, 11 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 65 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f4a65078`
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
- simulate_execution
- graphify reference: query, path, explain
- sync_helius_webhook_tokens_once
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
- LegacyDataMigrationTests
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- app.py
- CLAUDE_NOTES.md
- execute_pumpportal_lightning_buy
- TraderQualityProfileTests
- open_paper_position
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- MarketEventRoutingTests
- startup
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- api_watched_wallets
- parse_watched_wallet_pump_events
- pre-push
- PaperPositionIdempotencyTests
- migrate_database
- El scoring sí funciona — y dónde no, 2026-09-11
- `update_paper_position()` idempotente — 2026-09-11
- PreEntryEventGuardTests
- consume_market_event_inbox_once
- ShadowPredictionTests
- InboxRoundTripTests
- MarketEventInboxConsumerTests
- HeliusWebhookSyncIntegrationTests
- poll_rpc_fallback_once
- pump_receipt
- HeliusWebhookTests
- get
- RpcFallbackBaselineTests
- solana_rpc_fallback.py
- route_market_event
- RpcFallbackPersistenceTests
- api_trader_quality_profile
- market_event_block_ts
- apply_paper_event
- evaluate_buy
- validate_market_event_inbox_once
- get_market_event_inbox_activation_ts

## God Nodes (most connected - your core abstractions)
1. `db()` - 123 edges
2. `auth()` - 74 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 36 edges
5. `pump_receipt()` - 23 edges
6. `TraderQualityProfileTests` - 23 edges
7. `ExecutionAdapterTests` - 22 edges
8. `InboxRoundTripTests` - 20 edges
9. ``update_paper_position()` idempotente — 2026-09-11` - 20 edges
10. `startup()` - 19 edges

## Surprising Connections (you probably didn't know these)
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `poll_rpc_fallback_once()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `record_helius_webhook_transactions()` --calls--> `parse_tracked_token_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `record_helius_webhook_transactions()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py

## Import Cycles
- None detected.

## Communities (72 total, 11 thin omitted)

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

### Community 7 - "simulate_execution"
Cohesion: 0.15
Nodes (25): check_execution_timeout(), create_execution_order(), demo_controlled_retry(), demo_execution_check(), demo_execution_failure(), demo_execution_timeout(), demo_idempotency_risk_blocked(), demo_idempotency_sent() (+17 more)

### Community 8 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 9 - "sync_helius_webhook_tokens_once"
Cohesion: 0.14
Nodes (26): _decode_helius_sync_addresses(), _encoded_helius_sync_addresses(), get_helius_webhook_sync_state(), get_helius_webhook_sync_status(), helius_webhook_sync_worker(), Reconcile tracked tokens without taking ownership of base addresses., sync_helius_webhook_tokens_once(), _tracked_tokens_snapshot() (+18 more)

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
Cohesion: 0.14
Nodes (35): auth(), demo(), demo_close_old(), demo_concurrent_idempotency(), demo_duplicate_check(), demo_execution_order_events(), demo_execution_orders(), demo_exit_close() (+27 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.11
Nodes (6): EvaluationIdempotencyTests, EvaluationIdentityMigrationTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "LegacyDataMigrationTests"
Cohesion: 0.35
Nodes (3): LegacyDataMigrationTests, La transición con datos reales viejos, que es donde esto puede doler. Una base…, Una base como la de producción antes de este bloque.

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.08
Nodes (27): beta_posterior_rate(), calculate_trader_profile_score(), calculate_trader_quality_candidate(), clamp_trader_quality(), get_trader_activity_concentration(), get_trader_quality_profile(), Calidad por encogimiento continuo hacia el prior neutral. El posterior Beta ya…, Intervalo de Wilson. Devuelve None si no hay muestras. (+19 more)

### Community 31 - "app.py"
Cohesion: 0.07
Nodes (68): api_live_positions(), api_shadow_predictions(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader() (+60 more)

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.15
Nodes (12): Cuándo volver a encenderlo, Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Por qué, Por qué apagarlo y no ajustarlo (+4 more)

### Community 34 - "execute_pumpportal_lightning_buy"
Cohesion: 0.16
Nodes (17): api_live_execution_readiness(), api_shadow_stats(), assess_shadow_challenger(), build_pumpportal_lightning_buy_payload(), compare_shadow_models(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), fetch_sol_usd_quote() (+9 more)

### Community 35 - "TraderQualityProfileTests"
Cohesion: 0.08
Nodes (3): Perfil integral y observacional de calidad del trader., TraderQualityCandidateTests, TraderQualityProfileTests

### Community 36 - "open_paper_position"
Cohesion: 0.18
Nodes (13): count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check(), demo_mode_isolation(), demo_paper_mode_save(), demo_risk_mode_isolation(), demo_slippage_check() (+5 more)

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

### Community 43 - "startup"
Cohesion: 0.15
Nodes (17): check_watched_wallet_silence(), fetch_solana_balance_sol(), is_pumpportal_error_message(), mark_stream_problem(), mark_stream_recovered(), market_event_inbox_consumer_worker(), post_discord_alert(), pumpportal_balance_monitor() (+9 more)

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "api_watched_wallets"
Cohesion: 0.50
Nodes (4): api_watched_wallets(), get_watched_wallet_activity(), Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…, Entrega de datos por wallet vigilada: detecta silencios individuales.

### Community 46 - "parse_watched_wallet_pump_events"
Cohesion: 0.18
Nodes (7): _is_signed_by(), parse_watched_wallet_pump_events(), ¿Firmó ``wallet`` esta transacción? Solana devuelve ``accountKeys`` en dos…, Return official Pump events signed by and attributed to ``wallet``., AccountKeyEncodingTests, El parser debe aceptar las dos codificaciones de ``accountKeys``.…, RpcFallbackParserTests

### Community 48 - "PaperPositionIdempotencyTests"
Cohesion: 0.14
Nodes (8): PaperPositionIdempotencyTests, Si la transacción falla, no queda ni el efecto ni la marca. El caso peligroso…, Un evento reintentado no debe aplicarse dos veces a la misma posición.…, Una transacción puede traer varias operaciones Pump válidas. Con la firma sola…, La auditoría y el efecto son atómicos. Si la auditoría quedara fuera de la…, Convertir un índice inválido en 0 crea colisiones. Si un índice roto se…, El cierre puede quedar confirmado y la limpieza posterior fallar.…, `BEGIN IMMEDIATE` toma el lock al abrir la transacción. Una excepción entre ese…

### Community 49 - "migrate_database"
Cohesion: 0.12
Nodes (16): migrate_database(), migrate_evaluation_event_identity(), migrate_helius_webhook_sync_state(), migrate_inbox_event_index_to_ordinal(), migrate_market_event_inbox_index_scheme(), migrate_market_event_inbox_validation(), migrate_processed_market_events(), migrate_shadow_predictions_for_multiple_models() (+8 more)

### Community 50 - "El scoring sí funciona — y dónde no, 2026-09-11"
Cohesion: 0.29
Nodes (7): Consecuencia para el backfill y la watchlist, El scoring sí funciona — y dónde no, 2026-09-11, La explicación: el scoring separa de verdad, Los dos límites reales, No era sesgo de supervivencia, Nota sobre la lógica de salida, Y no es el confundidor de decu

### Community 51 - "`update_paper_position()` idempotente — 2026-09-11"
Cohesion: 0.06
Nodes (33): Barrera de ejecución real, Consumidor seguro del inbox de Helius, Correcciones tras la revisión de Codex, Cuarta revisión: el helper no rechazaba lo que decía rechazar, Deduplicación global por evento, Dos pruebas más, por el router (pedido de Codex), El problema, Identidad de evaluaciones (+25 more)

### Community 52 - "PreEntryEventGuardTests"
Cohesion: 0.08
Nodes (8): PreEntryEventGuardTests, El índice también tiene que llegar al historial por el recorrido real., La guarda tiene que sobrevivir el recorrido real, no solo la función. El patrón…, Un evento anterior a la entrada no pertenece a esta posición. Con webhooks…, El índice tiene que sobrevivir el recorrido real, no solo la llamada.…, RouterEventIndexTests, RouterPreEntryGuardTests, RouterTokenHistoryTests

### Community 53 - "consume_market_event_inbox_once"
Cohesion: 0.15
Nodes (14): claim_market_event_inbox_processing_batch(), consume_market_event_inbox_once(), finish_market_event_inbox_processing(), mark_market_event_processed(), mark_signature_processed(), market_event_identity(), market_event_is_after_activation(), Finaliza el consumo solo si el worker todavía posee la reserva. (+6 more)

### Community 54 - "ShadowPredictionTests"
Cohesion: 0.12
Nodes (3): FakeShadowModel, ShadowPredictionMigrationTests, ShadowPredictionTests

### Community 55 - "InboxRoundTripTests"
Cohesion: 0.18
Nodes (3): InboxRoundTripTests, Una fila escrita antes de que el índice viajara adentro del evento. Es el caso…, El índice tiene que sobrevivir el viaje completo, no solo el parser. Webhook,…

### Community 56 - "MarketEventInboxConsumerTests"
Cohesion: 0.07
Nodes (5): MarketEventChronologyTests, MarketEventDeduplicationTests, MarketEventInboxActivationTests, MarketEventInboxConsumerTests, MarketEventInboxValidationTests

### Community 57 - "HeliusWebhookSyncIntegrationTests"
Cohesion: 0.13
Nodes (4): FakeResponse, HeliusWebhookPlanningTests, HeliusWebhookSyncIntegrationTests, webhook()

### Community 58 - "poll_rpc_fallback_once"
Cohesion: 0.28
Nodes (7): poll_rpc_fallback_once(), record_rpc_fallback_event(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _rpc_request(), _wait_for_rpc_slot(), RpcFallbackRequestTests

### Community 59 - "pump_receipt"
Cohesion: 0.30
Nodes (8): parse_tracked_token_pump_events(), Return official Pump events for the requested token mints. Unlike the watched-…, OutboundRequestTests, pump_amm_receipt(), pump_receipt(), receipt_with_payload(), token_balance(), TrackedTokenParserTests

### Community 61 - "get"
Cohesion: 0.11
Nodes (20): api_helius_webhook_sample(), api_helius_webhook_stats(), api_rpc_fallback_stats(), can_retry_execution(), demo_can_retry(), demo_cannot_retry_risk(), evaluations(), get_rpc_fallback_stats() (+12 more)

### Community 63 - "solana_rpc_fallback.py"
Cohesion: 0.36
Nodes (10): _base58_encode(), _event_payloads(), _optional_post_token_amount(), _parse_official_pump_events(), _parse_pump_amm_trade(), _parse_pump_trade(), Strict, observational parsing for Pump trades on Solana RPC., Parse official Pump/PumpSwap events without choosing a consumer. (+2 more)

### Community 64 - "route_market_event"
Cohesion: 0.23
Nodes (12): decide_live_position_exit(), evaluate_live_position_exit(), market_event_index(), market_event_new_token_balance(), Apply an already deduplicated event using the existing live semantics.…, Guarda un punto de historial de market cap. Con firma, la escritura es…, Índice de una operación dentro de su transacción, leído del evento. La…, Preserva la diferencia entre un saldo desconocido y un cero real. PumpPortal… (+4 more)

### Community 66 - "api_trader_quality_profile"
Cohesion: 0.50
Nodes (4): api_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide., Perfil integral de cada trader vigilado.

### Community 67 - "market_event_block_ts"
Cohesion: 0.20
Nodes (11): helius_webhook(), market_event_block_ts(), market_event_from_inbox_row(), Registra lo que llegó por webhook. Observacional: no dispara nada. Devuelve…, Recibe y preserva transacciones; el consumidor decide si se aplican., Timestamp on-chain validado: numérico, finito y positivo. ``None`` pasa como…, Momento on-chain del evento, leído de adentro del evento. Ausente significa…, Reconstruye el evento normalizado guardado en `market_event_inbox`. La fila… (+3 more)

### Community 68 - "apply_paper_event"
Cohesion: 0.25
Nodes (8): apply_paper_event(), decide_paper_position_action(), Decide qué hacer con una posición paper. No toca la base de datos. Separado de…, Aplica el evento en una sola transacción y describe qué pasó. Deliberadamente…, Registra un evento de posición. Con ``connection`` escribe dentro de la…, Lee un timestamp que guardamos nosotros; inservible cuenta como ausente.…, save_position_event(), stored_block_event_ts()

### Community 69 - "evaluate_buy"
Cohesion: 0.29
Nodes (8): assess_live_model_approval(), decision_from_score(), evaluate_buy(), get_trader_quality_assessment(), maybe_execute_live_copy(), score_token_structure(), score_trade_size(), score_trader()

### Community 70 - "validate_market_event_inbox_once"
Cohesion: 0.25
Nodes (8): claim_market_event_inbox_validation_batch(), finish_market_event_inbox_validation(), market_event_inbox_validation_worker(), Reserva eventos observados para validarlos sin ejecutar sus efectos. La reserva…, Finaliza una reserva solo si todavía pertenece al mismo worker., Valida un lote del inbox sin llamar al router ni aplicar efectos., Valida continuamente el inbox; nunca enruta eventos., validate_market_event_inbox_once()

### Community 71 - "get_market_event_inbox_activation_ts"
Cohesion: 0.50
Nodes (4): establish_market_event_inbox_activation(), get_market_event_inbox_activation_ts(), Lee la frontera persistida del consumidor, o ``None`` si no se activó., Fija una sola vez desde cuándo el inbox puede producir efectos.

## Knowledge Gaps
- **160 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+155 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 441 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.481394081) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.740697133) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.740697031) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.740697031) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.740696948) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ShadowLogisticModel` connect `train_baseline_model.py` to `app.py`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Why does `PreEntryEventGuardTests` connect `PreEntryEventGuardTests` to `PaperPositionIdempotencyTests`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 42 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 42 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _160 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `train_baseline_model.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0803312629399586 - nodes in this community are weakly interconnected._
- **Should `TokenHistoryIdempotencyTests` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Should `ValueError` be split into smaller, more focused modules?**
  _Cohesion score 0.06666666666666667 - nodes in this community are weakly interconnected._