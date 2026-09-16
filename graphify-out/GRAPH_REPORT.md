# Graph Report - pump fun  (2026-09-16)

## Corpus Check
- 53 files · ~102,054 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1327 nodes · 2744 edges · 74 communities (64 shown, 8 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 81 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f27ec35b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- TokenHistoryIdempotencyTests
- LiveReceiptPersistenceTests
- What You Must Do When Invoked
- Ablación de features — paso 0 antes del backfill, 2026-09-11
- graphify reference: extra exports and benchmark
- WatchedWalletSilenceTests
- auth
- graphify reference: query, path, explain
- helius_webhook_sync.py
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
- route_market_event
- LiveCanaryGuardTests
- ExecutionAdapterTests
- LegacyDataMigrationTests
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- helius_standard_wss_worker
- CLAUDE_NOTES.md
- app.py
- TraderQualityProfileTests
- simulate_execution
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- MarketEventRoutingTests
- PaperPositionIdempotencyTests
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?
- solana_rpc_fallback.py
- pre-push
- parse_watched_wallet_pump_events
- migrate_database
- El scoring sí funciona — y dónde no, 2026-09-11
- `update_paper_position()` idempotente — 2026-09-11
- PreEntryEventGuardTests
- audit_shadow_economics.py
- ShadowPredictionTests
- InboxRoundTripTests
- MarketEventInboxConsumerTests
- HeliusWebhookSyncIntegrationTests
- ValueError
- validate_market_event_inbox_once
- HeliusWebhookTests
- db
- execute_pumpportal_lightning_buy
- RpcFallbackPersistenceTests
- pump_receipt
- api_trader_quality_profile
- poll_rpc_fallback_once
- test_rpc_fallback.py
- HeliusWebhookDeliveryAlertTests
- evaluate_buy
- consume_market_event_inbox_once
- maybe_send_ambiguous_execution_order_alerts
- api_watched_wallets
- record_helius_webhook_transactions

## God Nodes (most connected - your core abstractions)
1. `db()` - 133 edges
2. `auth()` - 75 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 39 edges
5. `pump_receipt()` - 28 edges
6. `ExecutionAdapterTests` - 27 edges
7. `HeliusWebhookTests` - 25 edges
8. `TraderQualityProfileTests` - 23 edges
9. `startup()` - 20 edges
10. `main()` - 20 edges

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

## Communities (74 total, 8 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.08
Nodes (37): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+29 more)

### Community 1 - "TokenHistoryIdempotencyTests"
Cohesion: 0.14
Nodes (5): LegacyInboxRenumberTests, El historial alimenta el scoring: una fila repetida lo sesga. Con reintentos de…, Documenta una limitación abierta, no un comportamiento deseado. PumpPortal…, Las filas de inbox viejas llevan índice de log, no ordinal., TokenHistoryIdempotencyTests

### Community 2 - "LiveReceiptPersistenceTests"
Cohesion: 0.09
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
Cohesion: 0.07
Nodes (6): PumpPortalBalanceTests, PumpPortalMessageTests, Una wallet que deja de entregar con el stream sano debe ser visible., ShadowReviewAlertTests, StreamStateTests, WatchedWalletSilenceTests

### Community 7 - "auth"
Cohesion: 0.09
Nodes (58): api_helius_standard_wss_stats(), auth(), demo(), demo_can_retry(), demo_close_old(), demo_concurrent_idempotency(), demo_daily_pnl(), demo_daily_pnl_isolation() (+50 more)

### Community 8 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 9 - "helius_webhook_sync.py"
Cohesion: 0.13
Nodes (30): _decode_helius_sync_addresses(), _encoded_helius_sync_addresses(), get_helius_webhook_sync_state(), get_helius_webhook_sync_status(), _helius_webhook_sync_retry_seconds(), Reconcile tracked tokens without taking ownership of base addresses., sync_helius_webhook_tokens_once(), _tracked_tokens_snapshot() (+22 more)

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

### Community 25 - "route_market_event"
Cohesion: 0.17
Nodes (18): decide_live_position_exit(), evaluate_live_position_exit(), is_pumpportal_error_message(), mark_market_event_processed(), market_event_identity(), market_event_index(), market_event_new_token_balance(), Identidad de un evento de mercado dentro de una transacción. La usan las dos… (+10 more)

### Community 26 - "LiveCanaryGuardTests"
Cohesion: 0.05
Nodes (8): AmbiguousExecutionOrderAlertTests, EvaluationIdempotencyTests, EvaluationIdentityMigrationTests, LiveCanaryGuardTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "LegacyDataMigrationTests"
Cohesion: 0.35
Nodes (3): LegacyDataMigrationTests, La transición con datos reales viejos, que es donde esto puede doler. Una base…, Una base como la de producción antes de este bloque.

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.08
Nodes (27): beta_posterior_rate(), calculate_trader_profile_score(), calculate_trader_quality_candidate(), clamp_trader_quality(), get_trader_entry_samples(), get_trader_quality_profile(), Calidad por encogimiento continuo hacia el prior neutral. El posterior Beta ya…, Intervalo de Wilson. Devuelve None si no hay muestras. (+19 more)

### Community 31 - "helius_standard_wss_worker"
Cohesion: 0.11
Nodes (13): fetch_helius_standard_wss_transaction(), finish_helius_standard_wss_transaction(), helius_standard_wss_retry_seconds(), helius_standard_wss_worker(), update_helius_standard_wss_state(), build_helius_standard_wss_url(), build_logs_subscribe_request(), decode_wss_message() (+5 more)

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.15
Nodes (12): Cuándo volver a encenderlo, Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Por qué, Por qué apagarlo y no ajustarlo (+4 more)

### Community 34 - "app.py"
Cohesion: 0.07
Nodes (52): api_shadow_predictions(), api_shadow_stats(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader() (+44 more)

### Community 35 - "TraderQualityProfileTests"
Cohesion: 0.08
Nodes (3): Perfil integral y observacional de calidad del trader., TraderQualityCandidateTests, TraderQualityProfileTests

### Community 36 - "simulate_execution"
Cohesion: 0.14
Nodes (26): can_retry_execution(), check_execution_timeout(), create_execution_order(), create_execution_order_idempotent(), demo_cannot_retry_risk(), demo_controlled_retry(), demo_execution_timeout(), demo_idempotency_sent() (+18 more)

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

### Community 42 - "MarketEventRoutingTests"
Cohesion: 0.11
Nodes (3): MarketEventRoutingTests, PumpPortal es frontera de confianza: basura no tira la conexión. Antes, un…, StreamRoutingTests

### Community 43 - "PaperPositionIdempotencyTests"
Cohesion: 0.14
Nodes (8): PaperPositionIdempotencyTests, Si la transacción falla, no queda ni el efecto ni la marca. El caso peligroso…, Un evento reintentado no debe aplicarse dos veces a la misma posición.…, Una transacción puede traer varias operaciones Pump válidas. Con la firma sola…, La auditoría y el efecto son atómicos. Si la auditoría quedara fuera de la…, Convertir un índice inválido en 0 crea colisiones. Si un índice roto se…, El cierre puede quedar confirmado y la limpieza posterior fallar.…, `BEGIN IMMEDIATE` toma el lock al abrir la transacción. Una excepción entre ese…

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: ¿Qué contratos dependen de la identidad de evaluations al consumir eventos Helius con varias operaciones?, Source Nodes

### Community 46 - "solana_rpc_fallback.py"
Cohesion: 0.38
Nodes (9): _event_payloads(), _optional_post_token_amount(), _parse_official_pump_events(), _parse_pump_amm_trade(), _parse_pump_trade(), Strict, observational parsing for Pump trades on Solana RPC., Parse official Pump/PumpSwap events without choosing a consumer., _token_balances() (+1 more)

### Community 48 - "parse_watched_wallet_pump_events"
Cohesion: 0.17
Nodes (7): _is_signed_by(), parse_watched_wallet_pump_events(), ¿Firmó ``wallet`` esta transacción? Solana devuelve ``accountKeys`` en dos…, Return official Pump events signed by and attributed to ``wallet``., AccountKeyEncodingTests, El parser debe aceptar las dos codificaciones de ``accountKeys``.…, RpcFallbackParserTests

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
Cohesion: 0.08
Nodes (8): PreEntryEventGuardTests, El índice también tiene que llegar al historial por el recorrido real., La guarda tiene que sobrevivir el recorrido real, no solo la función. El patrón…, Un evento anterior a la entrada no pertenece a esta posición. Con webhooks…, El índice tiene que sobrevivir el recorrido real, no solo la llamada.…, RouterEventIndexTests, RouterPreEntryGuardTests, RouterTokenHistoryTests

### Community 53 - "audit_shadow_economics.py"
Cohesion: 0.25
Nodes (11): _audit_model(), audit_shadow_models(), _fetch_json(), fetch_shadow_predictions(), main(), pair_completed_predictions(), _payoff(), _temporal_windows() (+3 more)

### Community 54 - "ShadowPredictionTests"
Cohesion: 0.12
Nodes (3): FakeShadowModel, ShadowPredictionMigrationTests, ShadowPredictionTests

### Community 55 - "InboxRoundTripTests"
Cohesion: 0.18
Nodes (3): InboxRoundTripTests, El índice tiene que sobrevivir el viaje completo, no solo el parser. Webhook,…, Una fila escrita antes de que el índice viajara adentro del evento. Es el caso…

### Community 56 - "MarketEventInboxConsumerTests"
Cohesion: 0.06
Nodes (7): MarketEventChronologyTests, MarketEventDeduplicationTests, MarketEventInboxActivationTests, MarketEventInboxConsumerTests, MarketEventInboxValidationTests, El parser de Helius es nuestro: un saldo inservible es un bug. Tiene que fallar…, Si la reserva lanza, no hubo efectos: la fila vuelve a la cola. Solo la reserva…

### Community 57 - "HeliusWebhookSyncIntegrationTests"
Cohesion: 0.10
Nodes (5): FakeResponse, HeliusWebhookPlanningTests, HeliusWebhookSyncAlertTests, HeliusWebhookSyncIntegrationTests, webhook()

### Community 58 - "ValueError"
Cohesion: 0.26
Nodes (13): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), prepare_pumpportal_lightning_sell(), record_finalized_buy_position(), parse_buy_receipt(), _parse_buy_receipt(), _parse_receipt_balances() (+5 more)

### Community 59 - "validate_market_event_inbox_once"
Cohesion: 0.33
Nodes (6): finish_market_event_inbox_validation(), market_event_inbox_validation_worker(), Finaliza una reserva solo si todavía pertenece al mismo worker., Valida un lote del inbox sin llamar al router ni aplicar efectos., Valida continuamente el inbox; nunca enruta eventos., validate_market_event_inbox_once()

### Community 60 - "HeliusWebhookTests"
Cohesion: 0.12
Nodes (5): HeliusWebhookTests, Lo que el consumidor del inbox escribe en `trades` no es PumpPortal. Con el…, Las consultas por firma recorren tablas que crecen con cada evento. Sin índice,…, Un token seguido de una wallet no vigilada no llega a `trades`. El stream igual…, Piloto del webhook: registra qué llegó y cuándo, sin decidir nada.

### Community 61 - "db"
Cohesion: 0.04
Nodes (68): api_helius_webhook_sample(), api_helius_webhook_stats(), api_live_positions(), api_rpc_fallback_stats(), apply_paper_event(), calculate_copyability_score(), claim_market_event_inbox_validation_batch(), cleanup_finished_outcome_token() (+60 more)

### Community 62 - "execute_pumpportal_lightning_buy"
Cohesion: 0.13
Nodes (22): api_live_execution_readiness(), assess_live_model_approval(), count_open_positions(), demo_liquidity_check(), demo_risk_mode_isolation(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), fetch_finalized_solana_transaction() (+14 more)

### Community 63 - "RpcFallbackPersistenceTests"
Cohesion: 0.18
Nodes (3): Con firma sola, la operación 1 quedaba "matched" por la operación 0. PumpPortal…, Un trade del índice cero no prueba que PumpPortal entregó el uno., RpcFallbackPersistenceTests

### Community 64 - "pump_receipt"
Cohesion: 0.23
Nodes (5): pump_receipt(), Lo que el consumidor del inbox aplicó no lo perdió el agente., El parser entrega ``None`` cuando no puede reconstruir el saldo. En producción…, La línea de base decide qué observaciones cuentan como evidencia. Un fallo de…, RpcFallbackBaselineTests

### Community 65 - "api_trader_quality_profile"
Cohesion: 0.50
Nodes (4): api_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide., Perfil integral de cada trader vigilado.

### Community 66 - "poll_rpc_fallback_once"
Cohesion: 0.28
Nodes (7): poll_rpc_fallback_once(), record_rpc_fallback_event(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _rpc_request(), _wait_for_rpc_slot(), RpcFallbackRequestTests

### Community 67 - "test_rpc_fallback.py"
Cohesion: 0.23
Nodes (8): _base58_encode(), parse_tracked_token_pump_events(), Return official Pump events for the requested token mints. Unlike the watched-…, OutboundRequestTests, pump_amm_receipt(), receipt_with_payload(), token_balance(), TrackedTokenParserTests

### Community 69 - "evaluate_buy"
Cohesion: 0.22
Nodes (10): decision_from_score(), evaluate_buy(), market_event_block_ts(), market_event_from_inbox_row(), Timestamp on-chain validado: numérico, finito y positivo. ``None`` pasa como…, Momento on-chain del evento, leído de adentro del evento. Ausente significa…, Reconstruye el evento normalizado guardado en `market_event_inbox`. La fila…, score_token_structure() (+2 more)

### Community 70 - "consume_market_event_inbox_once"
Cohesion: 0.25
Nodes (8): claim_market_event_inbox_processing_batch(), consume_market_event_inbox_once(), finish_market_event_inbox_processing(), market_event_is_after_activation(), Permite solo eventos recibidos y ocurridos desde la activación. `blockEventTs`…, Reserva filas validadas para un único consumidor con lease., Cierra la reserva solo si el worker todavía la posee. ``validated`` devuelve la…, Enruta una vez los eventos validados posteriores a la activación. La identidad…

### Community 71 - "maybe_send_ambiguous_execution_order_alerts"
Cohesion: 0.33
Nodes (6): get_ambiguous_pumpportal_execution_orders(), maybe_send_ambiguous_execution_order_alerts(), pumpportal_execution_reconciliation_worker(), Return live orders that cannot be reconciled without manual review., Alert once per live order that PumpPortal left without a signature., reconcile_pending_pumpportal_execution_orders()

### Community 72 - "api_watched_wallets"
Cohesion: 0.29
Nodes (7): api_watched_wallets(), check_watched_wallet_silence(), get_watched_wallet_activity(), Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…, Avisa cuando una wallet vigilada deja de entregar con el stream sano. Solo se…, Entrega de datos por wallet vigilada: detecta silencios individuales., watched_wallet_monitor()

### Community 73 - "record_helius_webhook_transactions"
Cohesion: 0.40
Nodes (5): helius_webhook(), Registra lo que llegó por webhook. Observacional: no dispara nada. Devuelve…, Recibe y preserva transacciones; el consumidor decide si se aplican., record_helius_webhook_transactions(), FastAPIRequest

## Knowledge Gaps
- **166 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+161 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 495 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (5× useful, score=4.48103331) _(code changed — re-verify)_
- `open_paper_position()` (3× useful, score=2.740515747) _(code changed — re-verify)_
- `observe_shadow_signal()` (2× useful, score=1.870256883) _(code changed — re-verify)_
- `create_signal_outcome()` (2× useful, score=1.870256677) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.740517646) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.740517646) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.740517563) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ExecutionAdapterTests` connect `ExecutionAdapterTests` to `LiveCanaryGuardTests`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `PaperPositionIdempotencyTests` connect `PaperPositionIdempotencyTests` to `PreEntryEventGuardTests`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 47 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 47 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _166 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07902973395931143 - nodes in this community are weakly interconnected._
- **Should `TokenHistoryIdempotencyTests` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Should `LiveReceiptPersistenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.08665269042627533 - nodes in this community are weakly interconnected._