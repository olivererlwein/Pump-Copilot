# Graph Report - pump fun  (2026-09-11)

## Corpus Check
- 44 files · ~69,316 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 800 nodes · 1712 edges · 52 communities (40 shown, 10 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 41 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c30b5780`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- train_baseline_model.py
- startup
- ValueError
- What You Must Do When Invoked
- Ablación de features — paso 0 antes del backfill, 2026-09-11
- graphify reference: extra exports and benchmark
- WatchedWalletSilenceTests
- simulate_execution
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
- parse_watched_wallet_pump_events
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- get
- CLAUDE_NOTES.md
- app.py
- TraderQualityProfileTests
- open_paper_position
- Conectar el webhook al pipeline — brief para Codex, 2026-09-11
- Piloto del webhook de Helius — resultados, 2026-09-11
- Corrección de la medición de calidad del trader — 2026-09-10 (2ª parte)
- Wallets vigiladas que dejan de entregar en silencio — 2026-09-10 (3ª parte)
- Perfil integral de calidad del trader (shadow) — 2026-09-10
- MarketEventRoutingTests
- api_trader_quality_profile
- Evaluación del streaming de Helius y arreglo del parser — 2026-09-10
- api_watched_wallets
- helius_webhook
- pre-push
- TraderQualityCandidateTests
- execute_pumpportal_lightning_buy
- El scoring sí funciona — y dónde no, 2026-09-11
- Scoring dinámico de traders apagado — 2026-09-11

## God Nodes (most connected - your core abstractions)
1. `db()` - 115 edges
2. `auth()` - 74 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 26 edges
5. `TraderQualityProfileTests` - 23 edges
6. `ExecutionAdapterTests` - 22 edges
7. `parse_watched_wallet_pump_events()` - 20 edges
8. `main()` - 18 edges
9. `pump_receipt()` - 17 edges
10. `simulate_execution()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `record_helius_webhook_transactions()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `record_finalized_buy_position()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `record_finalized_sell_position()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py

## Import Cycles
- None detected.

## Communities (52 total, 10 thin omitted)

### Community 0 - "train_baseline_model.py"
Cohesion: 0.08
Nodes (37): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+29 more)

### Community 1 - "startup"
Cohesion: 0.07
Nodes (39): assess_shadow_challenger(), check_watched_wallet_silence(), cleanup_finished_outcome_token(), compare_shadow_models(), complete_finished_signal_outcomes(), expire_old_signal_outcomes(), fetch_solana_balance_sol(), get_persistent_kill_switch() (+31 more)

### Community 2 - "ValueError"
Cohesion: 0.07
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

### Community 7 - "simulate_execution"
Cohesion: 0.15
Nodes (25): can_retry_execution(), check_execution_timeout(), create_execution_order(), demo_controlled_retry(), demo_execution_check(), demo_execution_failure(), demo_execution_timeout(), demo_idempotency_check() (+17 more)

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
Cohesion: 0.16
Nodes (33): auth(), demo(), demo_can_retry(), demo_cannot_retry_risk(), demo_close_old(), demo_concurrent_idempotency(), demo_duplicate_check(), demo_execution_order_events() (+25 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "parse_watched_wallet_pump_events"
Cohesion: 0.05
Nodes (34): get_rpc_fallback_wallet_states(), poll_rpc_fallback_once(), record_rpc_fallback_event(), update_rpc_fallback_wallet_state(), _base58_encode(), _event_payloads(), fetch_confirmed_transaction(), fetch_signatures_for_address() (+26 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.20
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.07
Nodes (31): beta_posterior_rate(), calculate_trader_profile_score(), calculate_trader_quality_candidate(), clamp_trader_quality(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile() (+23 more)

### Community 31 - "get"
Cohesion: 0.10
Nodes (22): api_helius_webhook_sample(), api_helius_webhook_stats(), api_rpc_fallback_stats(), api_shadow_stats(), demo_execution_latency(), demo_reconcile_pending(), demo_reconcile_sent(), evaluations() (+14 more)

### Community 32 - "CLAUDE_NOTES.md"
Cohesion: 0.22
Nodes (8): Cómo apareció, El punto ciego, Hallazgo #3 (`db()`) — medido y descartado, 2026-09-10, Hook de pre-push para el camino del dinero — 2026-09-11, La corrección, Punto ciego del hook de pre-push — encontrado y corregido, 2026-09-11, Qué hace, Verificación

### Community 34 - "app.py"
Cohesion: 0.08
Nodes (57): api_live_positions(), api_shadow_predictions(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader() (+49 more)

### Community 36 - "open_paper_position"
Cohesion: 0.16
Nodes (14): count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check(), demo_mode_isolation(), demo_paper_mode_save(), demo_risk_mode_isolation(), demo_slippage_check() (+6 more)

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

### Community 43 - "api_trader_quality_profile"
Cohesion: 0.50
Nodes (4): api_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide., Perfil integral de cada trader vigilado.

### Community 44 - "Evaluación del streaming de Helius y arreglo del parser — 2026-09-10"
Cohesion: 0.33
Nodes (6): Cambio implementado, Evaluación del streaming de Helius y arreglo del parser — 2026-09-10, Los tres puntos verificados, Opciones evaluadas, Por qué se evalúa, Recomendación de arquitectura

### Community 45 - "api_watched_wallets"
Cohesion: 0.50
Nodes (4): api_watched_wallets(), get_watched_wallet_activity(), Entrega de datos por wallet vigilada: detecta silencios individuales., Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…

### Community 46 - "helius_webhook"
Cohesion: 0.40
Nodes (5): helius_webhook(), Registra lo que llegó por webhook. Observacional: no dispara nada. Devuelve…, Recibe transacciones de Helius. Solo mide; no alimenta decisiones., record_helius_webhook_transactions(), FastAPIRequest

### Community 49 - "execute_pumpportal_lightning_buy"
Cohesion: 0.29
Nodes (11): api_live_execution_readiness(), assess_live_model_approval(), demo_live_guard(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), get_execution_order_status(), get_live_execution_readiness(), maybe_execute_live_copy() (+3 more)

### Community 50 - "El scoring sí funciona — y dónde no, 2026-09-11"
Cohesion: 0.29
Nodes (7): Consecuencia para el backfill y la watchlist, El scoring sí funciona — y dónde no, 2026-09-11, La explicación: el scoring separa de verdad, Los dos límites reales, No era sesgo de supervivencia, Nota sobre la lógica de salida, Y no es el confundidor de decu

### Community 51 - "Scoring dinámico de traders apagado — 2026-09-11"
Cohesion: 0.50
Nodes (4): Cuándo volver a encenderlo, Por qué, Por qué apagarlo y no ajustarlo, Scoring dinámico de traders apagado — 2026-09-11

## Knowledge Gaps
- **136 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+131 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 290 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.962370786) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.981185498) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.981185287) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TraderQualityProfileTests` connect `TraderQualityProfileTests` to `TraderQualityCandidateTests`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Why does `ShadowLogisticModel` connect `train_baseline_model.py` to `app.py`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Why does `ExecutionAdapterTests` connect `ExecutionAdapterTests` to `LiveCopyDispatchTests`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _136 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `train_baseline_model.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0803312629399586 - nodes in this community are weakly interconnected._
- **Should `startup` be split into smaller, more focused modules?**
  _Cohesion score 0.06882591093117409 - nodes in this community are weakly interconnected._