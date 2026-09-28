# Graph Report - pump fun  (2026-09-27)

## Corpus Check
- 76 files · ~142,652 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1748 nodes · 3585 edges · 87 communities (73 shown, 12 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 123 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `52c90d83`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- train_baseline_model.py
- TokenHistoryIdempotencyTests
- LiveReceiptPersistenceTests
- What You Must Do When Invoked
- Ablación de features — paso 0 antes del backfill, 2026-09-11
- graphify reference: extra exports and benchmark
- WatchedWalletSilenceTests
- auth
- graphify reference: query, path, explain
- RuntimeError
- Q: ¿Cuál es el flujo completo desde que recibimos información de un token/trader hasta que se genera una señal?
- Q: ¿Qué componentes participan en paper trading?
- Q: ¿Dónde deberíamos integrar Binance sin acoplarlo directamente a la lógica específica de Pump.fun?
- Q: ¿Qué archivos o módulos serían afectados si modificamos el sistema de scoring?
- validate_training_dataset.py
- Pump Copilot
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- AGENTS.md
- extraction-spec.md
- Q: sigamos
- calculate_trader_quality_candidate
- LiveCanaryGuardTests
- ExecutionAdapterTests
- db
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- helius_standard_wss_worker
- CLAUDE_NOTES.md
- PreviewHandler
- TraderQualityProfileTests
- RpcFallbackPersistenceTests
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- MarketEventRoutingTests
- HeliusStandardWssPersistenceTests
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?
- RpcFallbackBaselineTests
- pre-push
- Informe para Codex — sesión 2026-09-22 (00:00–06:15 UTC)
- migrate_database
- El scoring sí funciona — y dónde no, 2026-09-11
- `update_paper_position()` idempotente — 2026-09-11
- PreEntryEventGuardTests
- audit_shadow_economics.py
- TrainingDataQualityTests
- InboxRoundTripTests
- MarketEventInboxConsumerTests
- HeliusWebhookSyncIntegrationTests
- fetch_helius_credit_usage
- LegacyDataMigrationTests
- HeliusWebhookTests
- ui_baseline.py
- get_training_dataset_rows
- app.py
- relabel_stop_alignment.py
- ValueError
- onchain_account_prices.py
- pump_receipt
- AccountPriceCheckpointTests
- TokenRpcProbeTests
- solana_rpc_fallback.py
- Auditoría de ingesta y dos diffs de observabilidad — 2026-09-22
- test_rpc_fallback.py
- make_row
- update_execution_order
- HeliusWebhookDeliveryAlertTests
- validate_frozen_exit_candidate.py
- selection_diagnostics
- api_helius_standard_wss_stats
- compare_model_economics.py
- diagnose_unparsed_pump_receipt
- prune_dead_css.py
- ShadowLogisticModel
- Confirmación contra producción (2026-09-10)
- parse_watched_wallet_pump_events
- execute_pumpportal_lightning_buy
- apply_paper_event

## God Nodes (most connected - your core abstractions)
1. `db()` - 154 edges
2. `auth()` - 83 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 51 edges
5. `HeliusStandardWssPersistenceTests` - 34 edges
6. `pump_receipt()` - 32 edges
7. `helius_standard_wss_worker()` - 28 edges
8. `ExecutionAdapterTests` - 27 edges
9. `HeliusWebhookTests` - 26 edges
10. `AccountPriceCheckpointTests` - 24 edges

## Surprising Connections (you probably didn't know these)
- `fetch_helius_standard_wss_transaction()` --indirect_call--> `fetch_confirmed_transaction()`  [INFERRED]
  app.py → solana_rpc_fallback.py
- `record_finalized_sell_position()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `reconcile_pumpportal_execution_order()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `reconcile_pumpportal_execution_order()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py

## Import Cycles
- None detected.

## Communities (87 total, 12 thin omitted)

### Community 0 - "train_baseline_model.py"
Cohesion: 0.23
Nodes (17): artifact_save_blocker(), build_shadow_artifact(), classification_metrics(), fit_pipeline(), get_readiness_blockers(), group_balanced_sample_weights(), main(), metrics_by_trader() (+9 more)

### Community 1 - "TokenHistoryIdempotencyTests"
Cohesion: 0.14
Nodes (5): LegacyInboxRenumberTests, El historial alimenta el scoring: una fila repetida lo sesga. Con reintentos de…, Documenta una limitación abierta, no un comportamiento deseado. PumpPortal…, Las filas de inbox viejas llevan índice de log, no ordinal., TokenHistoryIdempotencyTests

### Community 2 - "LiveReceiptPersistenceTests"
Cohesion: 0.07
Nodes (8): parse_sell_receipt(), Return exact tokens sold and the all-in net SOL proceeds., buy_receipt(), LiveReceiptPersistenceTests, Habilita el camino de venta live con el envío real interceptado.…, ReceiptAccountingTests, sell_receipt(), token_entry()

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
Cohesion: 0.05
Nodes (7): AccountCheckpointTrainingAlertTests, PumpPortalBalanceTests, PumpPortalMessageTests, Una wallet que deja de entregar con el stream sano debe ser visible., ShadowReviewAlertTests, StreamStateTests, WatchedWalletSilenceTests

### Community 7 - "auth"
Cohesion: 0.08
Nodes (69): api_account_price_checkpoint_stats(), api_token_rpc_probe_stats(), auth(), demo(), demo_can_retry(), demo_cannot_retry_risk(), demo_close_old(), demo_concurrent_idempotency() (+61 more)

### Community 8 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 9 - "RuntimeError"
Cohesion: 0.07
Nodes (33): _decode_helius_sync_addresses(), _encoded_helius_sync_addresses(), get_helius_webhook_sync_state(), get_helius_webhook_sync_status(), _helius_webhook_sync_retry_seconds(), Reconcile tracked tokens without taking ownership of base addresses., sync_helius_webhook_tokens_once(), _tracked_tokens_snapshot() (+25 more)

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

### Community 16 - "Pump Copilot"
Cohesion: 0.29
Nodes (6): Architecture, Engineering highlights, Pump Copilot, Tech stack, What it does, Why this project

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

### Community 25 - "calculate_trader_quality_candidate"
Cohesion: 0.25
Nodes (9): beta_posterior_rate(), calculate_trader_quality_candidate(), clamp_trader_quality(), Calidad por encogimiento continuo hacia el prior neutral. El posterior Beta ya…, Intervalo de Wilson. Devuelve None si no hay muestras., Media posterior Beta con el mismo prior neutral del score vigente., TP25 antes de SL10, separado del resto de dimensiones., summarize_trader_entry_quality() (+1 more)

### Community 26 - "LiveCanaryGuardTests"
Cohesion: 0.05
Nodes (8): AmbiguousExecutionOrderAlertTests, EvaluationIdempotencyTests, EvaluationIdentityMigrationTests, LiveCanaryGuardTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "db"
Cohesion: 0.04
Nodes (59): api_account_checkpoint_subscription_details(), api_helius_webhook_sample(), api_helius_webhook_stats(), api_live_positions(), api_rpc_fallback_stats(), apply_cached_signal_outcome_checkpoints(), calculate_copyability_score(), claim_market_event_inbox_processing_batch() (+51 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.08
Nodes (26): api_trader_quality_profile(), calculate_trader_profile_score(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide. (+18 more)

### Community 31 - "helius_standard_wss_worker"
Cohesion: 0.09
Nodes (23): fetch_helius_standard_wss_transaction(), finish_helius_standard_wss_transaction(), helius_standard_wss_retry_seconds(), helius_standard_wss_token_has_open_position(), helius_standard_wss_wallet_flooded(), helius_standard_wss_worker(), True la primera vez que la wallet cruza el umbral; luego queda muda., Notify once when the tracked-token subscription cap loses coverage. (+15 more)

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.15
Nodes (12): Cuándo volver a encenderlo, Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Por qué, Por qué apagarlo y no ajustarlo (+4 more)

### Community 34 - "PreviewHandler"
Cohesion: 0.14
Nodes (9): BaseHTTPRequestHandler, Namespace, api_path_allowed(), main(), parse_args(), PreviewHandler, PreviewServer, Serve the local frontend on a LAN address with a read-only API proxy. This QA… (+1 more)

### Community 35 - "TraderQualityProfileTests"
Cohesion: 0.08
Nodes (3): Perfil integral y observacional de calidad del trader., TraderQualityCandidateTests, TraderQualityProfileTests

### Community 36 - "RpcFallbackPersistenceTests"
Cohesion: 0.10
Nodes (6): Sin el flag solo cuenta; con el flag escribe al inbox como `rpc`., Cobertura parcial es peor que exclusión: parece diversidad. De una wallet cuya…, Con firma sola, la operación 1 quedaba "matched" por la operación 0. PumpPortal…, Lo que el consumidor del inbox aplicó no lo perdió el agente., El parser entrega ``None`` cuando no puede reconstruir el saldo. En producción…, RpcFallbackPersistenceTests

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
Cohesion: 0.18
Nodes (11): Advertencia importante sobre los datos, Descartado en el camino, Lo que sí funciona: la alerta, Monitor RPC fallback — baseline y diagnóstico (2026-09-10), Qué se encontró, Qué se implementó, RESULTADO EN PRODUCCIÓN: causa confirmada (2026-09-10 02:30), Revisión y correcciones (Claude, sobre el diff anterior) (+3 more)

### Community 41 - "Perfil integral de calidad del trader (shadow) — 2026-09-10"
Cohesion: 0.22
Nodes (9): Archivos modificados, Decisiones estadísticas, Endpoint nuevo, Funciones nuevas (`app.py`), Limitaciones conocidas (explícitas, no ocultas), Perfil integral de calidad del trader (shadow) — 2026-09-10, Qué NO se tocó, Qué se agregó (+1 more)

### Community 42 - "MarketEventRoutingTests"
Cohesion: 0.11
Nodes (3): MarketEventRoutingTests, PumpPortal es frontera de confianza: basura no tira la conexión. Antes, un…, StreamRoutingTests

### Community 43 - "HeliusStandardWssPersistenceTests"
Cohesion: 0.07
Nodes (3): FakeWebSocket, HeliusStandardWssFloodTests, HeliusStandardWssPersistenceTests

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?, Source Nodes

### Community 48 - "Informe para Codex — sesión 2026-09-22 (00:00–06:15 UTC)"
Cohesion: 0.07
Nodes (29): Alinear la etiqueta al stop real: el mismatch no era el problema — 2026-09-22, Cierre del último 25%: tres contrafactuales — 2026-09-22, Cobertura incompleta: medir sin aplicar — 2026-09-22, Cobertura parcial: peor que excluir, Commits, Contrafactual: stop en −10 % en vez de −20 %, Corrección al cálculo de potencia (mismo día), Criterios de aprobación, fijados de antemano (+21 more)

### Community 49 - "migrate_database"
Cohesion: 0.11
Nodes (18): migrate_database(), migrate_evaluation_event_identity(), migrate_helius_webhook_sync_state(), migrate_inbox_event_index_to_ordinal(), migrate_market_event_inbox_index_scheme(), migrate_market_event_inbox_validation(), migrate_processed_market_events(), migrate_rpc_fallback_balance_nullable() (+10 more)

### Community 50 - "El scoring sí funciona — y dónde no, 2026-09-11"
Cohesion: 0.29
Nodes (7): Consecuencia para el backfill y la watchlist, El scoring sí funciona — y dónde no, 2026-09-11, La explicación: el scoring separa de verdad, Los dos límites reales, No era sesgo de supervivencia, Nota sobre la lógica de salida, Y no es el confundidor de decu

### Community 51 - "`update_paper_position()` idempotente — 2026-09-11"
Cohesion: 0.06
Nodes (36): Barrera de ejecución real, Comparación de transportes con el consumidor activo, Consumidor seguro del inbox de Helius, Correcciones tras la revisión de Codex, Cuarta revisión: el helper no rechazaba lo que decía rechazar, Deduplicación global por evento, Dos pruebas más, por el router (pedido de Codex), El problema (+28 more)

### Community 52 - "PreEntryEventGuardTests"
Cohesion: 0.05
Nodes (16): PaperPositionIdempotencyTests, PreEntryEventGuardTests, El índice también tiene que llegar al historial por el recorrido real., La guarda tiene que sobrevivir el recorrido real, no solo la función. El patrón…, Si la transacción falla, no queda ni el efecto ni la marca. El caso peligroso…, Un evento reintentado no debe aplicarse dos veces a la misma posición.…, Una transacción puede traer varias operaciones Pump válidas. Con la firma sola…, La auditoría y el efecto son atómicos. Si la auditoría quedara fuera de la… (+8 more)

### Community 53 - "audit_shadow_economics.py"
Cohesion: 0.25
Nodes (11): _audit_model(), audit_shadow_models(), _fetch_json(), fetch_shadow_predictions(), main(), pair_completed_predictions(), _payoff(), _temporal_windows() (+3 more)

### Community 55 - "InboxRoundTripTests"
Cohesion: 0.17
Nodes (3): InboxRoundTripTests, El índice tiene que sobrevivir el viaje completo, no solo el parser. Webhook,…, Una fila escrita antes de que el índice viajara adentro del evento. Es el caso…

### Community 56 - "MarketEventInboxConsumerTests"
Cohesion: 0.05
Nodes (9): MarketEventChronologyTests, MarketEventDeduplicationTests, MarketEventInboxActivationTests, MarketEventInboxConsumerTests, MarketEventInboxValidationTests, El fallback detecta hasta 90 s tarde: una salida live decidiría sobre un precio…, Una entrada detectada tarde tiene que ser separable del dataset. Paper abre al…, El parser de Helius es nuestro: un saldo inservible es un bug. Tiene que fallar… (+1 more)

### Community 57 - "HeliusWebhookSyncIntegrationTests"
Cohesion: 0.09
Nodes (11): collect(), fetch(), latest_snapshot(), main(), print_report(), Informe de cobertura de ingesta a partir de los endpoints de producción. Solo…, FakeResponse, HeliusWebhookPlanningTests (+3 more)

### Community 58 - "fetch_helius_credit_usage"
Cohesion: 0.20
Nodes (6): api_helius_credit_usage(), Exception, fetch_helius_credit_usage(), HeliusCreditUsageError, FakeResponse, HeliusCreditUsageTests

### Community 59 - "LegacyDataMigrationTests"
Cohesion: 0.35
Nodes (3): LegacyDataMigrationTests, La transición con datos reales viejos, que es donde esto puede doler. Una base…, Una base como la de producción antes de este bloque.

### Community 60 - "HeliusWebhookTests"
Cohesion: 0.11
Nodes (5): HeliusWebhookTests, Piloto del webhook: registra qué llegó y cuándo, sin decidir nada., Lo que el consumidor del inbox escribe en `trades` no es PumpPortal. Con el…, Las consultas por firma recorren tablas que crecen con cada evento. Sin índice,…, Un token seguido de una wallet no vigilada no llega a `trades`. El stream igual…

### Community 61 - "ui_baseline.py"
Cohesion: 0.12
Nodes (19): analyse_cssom(), app_token(), capture(), Chrome, collect_cssom(), compare(), find_chrome(), main() (+11 more)

### Community 62 - "get_training_dataset_rows"
Cohesion: 0.17
Nodes (12): api_account_checkpoint_training_dataset(), api_training_dataset(), api_training_dataset_preview(), api_training_stats(), build_model_features(), get_account_checkpoint_dataset_rows(), get_training_dataset_rows(), get_training_dataset_stats() (+4 more)

### Community 63 - "app.py"
Cohesion: 0.05
Nodes (77): api_live_account_exit_monitor_stats(), api_shadow_predictions(), api_shadow_stats(), api_training_checkpoint_freshness(), api_training_expired_preview(), api_training_stats_by_trader(), api_watched_wallets(), assess_shadow_challenger() (+69 more)

### Community 64 - "relabel_stop_alignment.py"
Cohesion: 0.13
Nodes (26): checkpoint_target(), evaluate(), main(), Alinea la etiqueta con el stop que la política realmente usa. El modelo entrena…, Espeja `app.account_checkpoint_target()` con el stop parametrizado., Entrena con el split y el modelo de siempre, cambiando solo la etiqueta., analyse(), main() (+18 more)

### Community 65 - "ValueError"
Cohesion: 0.05
Nodes (63): account_checkpoint_target(), account_exit_entry_market_cap(), build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), consume_market_event_inbox_once(), decide_live_position_exit(), decision_from_score() (+55 more)

### Community 66 - "onchain_account_prices.py"
Cohesion: 0.15
Nodes (15): account_addresses(), _curve_price(), _data(), fetch_account_prices(), _mint_info(), _pool_vaults(), Read current Pump and canonical PumpSwap prices without trading effects., Return current prices only; never infer a missing pool or historical price. (+7 more)

### Community 67 - "pump_receipt"
Cohesion: 0.35
Nodes (7): parse_tracked_token_pump_events(), Return official Pump events for the requested token mints. Unlike the watched-…, pump_amm_receipt(), pump_receipt(), receipt_with_payload(), token_balance(), TrackedTokenParserTests

### Community 71 - "solana_rpc_fallback.py"
Cohesion: 0.26
Nodes (13): _base58_encode(), _event_payloads(), _optional_post_token_amount(), _parse_official_pump_events(), _parse_pump_amm_trade(), _parse_pump_trade(), _pump_amm_virtual_quote_reserves(), Strict, observational parsing for Pump trades on Solana RPC. (+5 more)

### Community 72 - "Auditoría de ingesta y dos diffs de observabilidad — 2026-09-22"
Cohesion: 0.29
Nodes (7): Auditoría de ingesta y dos diffs de observabilidad — 2026-09-22, Dos cambios, solo diagnóstico, Hook pre-push: cubre recibos y el monitor de cuenta — 2026-09-22, Notificaciones WSS sin Pump: memoria y freno por wallet — 2026-09-22 (noche), `scripts/ingest_coverage_report.py` — 2026-09-22 (noche), Slot en las salidas por precio de cuenta — 2026-09-22, Transacciones versión 1 — causa del `-32015` y fix, 2026-09-22

### Community 73 - "test_rpc_fallback.py"
Cohesion: 0.16
Nodes (10): helius_standard_wss_error_code(), token_rpc_probe_once(), token_rpc_probe_worker(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _rpc_request(), _wait_for_rpc_slot(), OutboundRequestTests (+2 more)

### Community 74 - "make_row"
Cohesion: 0.20
Nodes (6): get_deployment_blockers(), make_row(), make_schema(), TemporalGroupSplitTests, TrainingDatasetValidationTests, TrainingDiagnosticsTests

### Community 75 - "update_execution_order"
Cohesion: 0.26
Nodes (14): can_retry_execution(), check_execution_timeout(), create_execution_order(), demo_controlled_retry(), demo_execution_timeout(), demo_pending_reconciliation(), demo_retry_function(), demo_retry_limit() (+6 more)

### Community 77 - "validate_frozen_exit_candidate.py"
Cohesion: 0.15
Nodes (14): build_matrix(), build_prospective_rows(), concentration(), fetch_json(), fit_frozen_pipeline(), main(), parse_cutoff(), prospective_report() (+6 more)

### Community 79 - "selection_diagnostics"
Cohesion: 0.24
Nodes (4): schema_without_features(), selection_diagnostics(), FeatureAblationTests, SelectionDiagnosticsTests

### Community 80 - "api_helius_standard_wss_stats"
Cohesion: 0.09
Nodes (27): account_checkpoint_training_alert_state_key(), account_price_checkpoint_once(), account_price_checkpoint_worker(), api_account_checkpoint_paths(), api_account_checkpoint_training_stats(), api_helius_standard_wss_stats(), count_complete_account_checkpoint_paths(), establish_helius_token_coverage_activation() (+19 more)

### Community 81 - "compare_model_economics.py"
Cohesion: 0.29
Nodes (11): main(), evaluate_strategy(), main(), simulate_payoff(), breakeven_precision(), main(), Reconcilia la precisión del clasificador con la del backtest económico. El…, Precisión mínima para no perder: w*0.25 - (1-w)*0.10 - coste = 0. (+3 more)

### Community 82 - "diagnose_unparsed_pump_receipt"
Cohesion: 0.20
Nodes (5): diagnose_unparsed_pump_receipt(), _is_signed_by(), ¿Firmó ``wallet`` esta transacción? Solana devuelve ``accountKeys`` en dos…, Classify a rejected receipt without retaining its raw contents., RpcFallbackParserTests

### Community 84 - "prune_dead_css.py"
Cohesion: 0.24
Nodes (10): main(), normalise(), prune(), Elimina del `<style>` inline solo las reglas que el navegador probó muertas.…, Separa los comentarios del selector. En el fuente los encabezados de sección…, Compara selectores sin depender del espaciado ni de los comentarios., Devuelve (inicio, fin, prelude, cuerpo) de cada bloque de nivel actual., Quita los bloques cuyo índice de posición esté en `targets`. Emparejar por… (+2 more)

### Community 85 - "ShadowLogisticModel"
Cohesion: 0.24
Nodes (5): load_shadow_model(), build_pipeline(), ShadowLogisticModel, ShadowModelError, ShadowArtifactTests

### Community 86 - "Confirmación contra producción (2026-09-10)"
Cohesion: 0.33
Nodes (6): Causas descartadas, Confirmación contra producción (2026-09-10), Hipótesis principal, Observabilidad agregada para cerrar el diagnóstico, Prueba definitiva: consulta on-chain, Próximo paso recomendado

### Community 87 - "parse_watched_wallet_pump_events"
Cohesion: 0.19
Nodes (6): parse_watched_wallet_pump_events(), Return official Pump events signed by and attributed to ``wallet``., AccountKeyEncodingTests, Separa "el transporte nunca lo trajo" de "lo trajo y falló"., Un trade del índice cero no prueba que PumpPortal entregó el uno., El parser debe aceptar las dos codificaciones de ``accountKeys``.…

### Community 88 - "execute_pumpportal_lightning_buy"
Cohesion: 0.14
Nodes (21): api_live_execution_readiness(), assess_live_model_approval(), build_pumpportal_lightning_buy_payload(), count_open_positions(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), fetch_finalized_solana_transaction(), fetch_sol_usd_quote() (+13 more)

### Community 92 - "apply_paper_event"
Cohesion: 0.25
Nodes (8): apply_paper_event(), decide_paper_position_action(), Lee un timestamp que guardamos nosotros; inservible cuenta como ausente.…, Decide qué hacer con una posición paper. No toca la base de datos. Separado de…, Aplica el evento en una sola transacción y describe qué pasó. Deliberadamente…, Registra un evento de posición. Con ``connection`` escribe dentro de la…, save_position_event(), stored_block_event_ts()

## Knowledge Gaps
- **199 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+194 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 637 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (5× useful, score=3.917945331) _(code changed — re-verify)_
- `open_paper_position()` (3× useful, score=2.396141723) _(code changed — re-verify)_
- `observe_shadow_signal()` (2× useful, score=1.635239846) _(code changed — re-verify)_
- `create_signal_outcome()` (2× useful, score=1.635239667) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.521803681) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.521803681) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.521803608) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `HeliusStandardWssPersistenceTests` connect `HeliusStandardWssPersistenceTests` to `helius_standard_wss_worker`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Are the 65 inferred relationships involving `ValueError` (e.g. with `account_checkpoint_target()` and `account_exit_entry_market_cap()`) actually correct?**
  _`ValueError` has 65 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _199 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `TokenHistoryIdempotencyTests` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Should `LiveReceiptPersistenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.06965174129353234 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `WatchedWalletSilenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.05398110661268556 - nodes in this community are weakly interconnected._