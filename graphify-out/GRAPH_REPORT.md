# Graph Report - pump fun  (2026-09-10)

## Corpus Check
- 41 files · ~59,768 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 685 nodes · 1545 edges · 39 communities (29 shown, 8 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 40 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f7ac7d89`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- startup
- LiveReceiptPersistenceTests
- What You Must Do When Invoked
- get
- graphify reference: extra exports and benchmark
- WatchedWalletSilenceTests
- auth
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
- update_execution_order
- LiveCopyDispatchTests
- ExecutionAdapterTests
- ValueError
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- TraderQualityCandidateTests
- open_paper_position
- app.py
- TraderQualityProfileTests
- execute_pumpportal_lightning_buy
- api_trader_quality_profile
- api_watched_wallets

## God Nodes (most connected - your core abstractions)
1. `db()` - 112 edges
2. `auth()` - 72 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 26 edges
5. `TraderQualityProfileTests` - 23 edges
6. `ExecutionAdapterTests` - 22 edges
7. `main()` - 18 edges
8. `simulate_execution()` - 16 edges
9. `TraderQualityCandidateTests` - 16 edges
10. `update_execution_order()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `record_finalized_sell_position()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `reconcile_pumpportal_execution_order()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `record_finalized_buy_position()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py

## Import Cycles
- None detected.

## Communities (39 total, 8 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.08
Nodes (35): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+27 more)

### Community 1 - "startup"
Cohesion: 0.06
Nodes (44): assess_shadow_challenger(), check_watched_wallet_silence(), cleanup_finished_outcome_token(), compare_shadow_models(), complete_finished_signal_outcomes(), decide_live_position_exit(), evaluate_live_position_exit(), expire_old_signal_outcomes() (+36 more)

### Community 2 - "LiveReceiptPersistenceTests"
Cohesion: 0.11
Nodes (7): parse_sell_receipt(), Return exact tokens sold and the all-in net SOL proceeds., buy_receipt(), LiveReceiptPersistenceTests, ReceiptAccountingTests, sell_receipt(), token_entry()

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "get"
Cohesion: 0.13
Nodes (19): api_live_execution_readiness(), api_shadow_stats(), demo_execution_check(), demo_execution_failure(), demo_idempotency_check(), demo_idempotency_failed(), demo_idempotency_risk_blocked(), demo_live_guard() (+11 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "WatchedWalletSilenceTests"
Cohesion: 0.07
Nodes (6): PumpPortalBalanceTests, PumpPortalMessageTests, Una wallet que deja de entregar con el stream sano debe ser visible., ShadowReviewAlertTests, StreamStateTests, WatchedWalletSilenceTests

### Community 7 - "auth"
Cohesion: 0.16
Nodes (33): auth(), demo(), demo_close_old(), demo_concurrent_idempotency(), demo_duplicate_check(), demo_execution_order_events(), demo_execution_orders(), demo_exit_close() (+25 more)

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

### Community 25 - "update_execution_order"
Cohesion: 0.17
Nodes (21): can_retry_execution(), check_execution_timeout(), create_execution_order(), demo_can_retry(), demo_cannot_retry_risk(), demo_controlled_retry(), demo_execution_timeout(), demo_idempotency_sent() (+13 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "ValueError"
Cohesion: 0.07
Nodes (47): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_buy_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), fetch_finalized_solana_transaction(), fetch_sol_usd_quote(), fetch_solana_signature_status(), get_rpc_fallback_wallet_states() (+39 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.04
Nodes (44): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Advertencia importante sobre los datos (+36 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.09
Nodes (22): calculate_trader_profile_score(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile(), Una muestra por token: el primer outcome completado de cada mint., Reconstruye ciclos entrada -> ventas por token. Solo se reconstruye un ciclo…, Retornos y drawdown. Cada métrica es None si no hay datos. (+14 more)

### Community 32 - "open_paper_position"
Cohesion: 0.16
Nodes (14): count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check(), demo_mode_isolation(), demo_paper_mode_save(), demo_risk_mode_isolation(), demo_slippage_check() (+6 more)

### Community 34 - "app.py"
Cohesion: 0.08
Nodes (52): api_live_positions(), api_rpc_fallback_stats(), api_shadow_predictions(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats() (+44 more)

### Community 36 - "execute_pumpportal_lightning_buy"
Cohesion: 0.14
Nodes (20): assess_live_model_approval(), beta_posterior_rate(), calculate_trader_quality_candidate(), clamp_trader_quality(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), get_execution_order_status(), get_live_execution_readiness() (+12 more)

### Community 37 - "api_trader_quality_profile"
Cohesion: 0.50
Nodes (4): api_trader_quality_profile(), get_trader_quality_profiles(), Perfil integral de calidad. Observacional: no mueve dinero ni decide., Perfil integral de cada trader vigilado.

### Community 38 - "api_watched_wallets"
Cohesion: 0.50
Nodes (4): api_watched_wallets(), get_watched_wallet_activity(), Entrega de datos por wallet vigilada: detecta silencios individuales., Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…

## Knowledge Gaps
- **95 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+90 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 224 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
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

- **Why does `TraderQualityProfileTests` connect `TraderQualityProfileTests` to `TraderQualityCandidateTests`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Why does `ShadowLogisticModel` connect `test_training_pipeline.py` to `app.py`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Why does `ExecutionAdapterTests` connect `ExecutionAdapterTests` to `LiveCopyDispatchTests`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _95 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08384547848990342 - nodes in this community are weakly interconnected._
- **Should `startup` be split into smaller, more focused modules?**
  _Cohesion score 0.06236786469344609 - nodes in this community are weakly interconnected._