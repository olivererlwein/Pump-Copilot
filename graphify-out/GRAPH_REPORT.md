# Graph Report - pump fun  (2026-09-10)

## Corpus Check
- 39 files · ~48,462 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 524 nodes · 1258 edges · 36 communities (27 shown, 7 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 29 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d0a6e282`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- startup
- LiveReceiptPersistenceTests
- What You Must Do When Invoked
- evaluate_buy
- graphify reference: extra exports and benchmark
- test_stream_monitoring.py
- app.py
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
- execute_pumpportal_lightning_buy
- LiveCopyDispatchTests
- ExecutionAdapterTests
- stream
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- ShadowLogisticModel
- TraderQualityCandidateTests
- get_shadow_stats
- db
- process_signal_outcomes_event

## God Nodes (most connected - your core abstractions)
1. `db()` - 100 edges
2. `auth()` - 69 edges
3. `require_debug_mode()` - 54 edges
4. `LiveReceiptPersistenceTests` - 26 edges
5. `ExecutionAdapterTests` - 22 edges
6. `main()` - 18 edges
7. `simulate_execution()` - 16 edges
8. `update_execution_order()` - 15 edges
9. `update_paper_position()` - 15 edges
10. `evaluate_buy()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `record_finalized_buy_position()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `record_finalized_sell_position()` --calls--> `parse_sell_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `reconcile_pumpportal_execution_order()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py

## Import Cycles
- None detected.

## Communities (36 total, 7 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.10
Nodes (32): main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline(), build_shadow_artifact() (+24 more)

### Community 1 - "startup"
Cohesion: 0.15
Nodes (14): cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), expire_old_signal_outcomes(), fetch_solana_balance_sol(), get_persistent_kill_switch(), migrate_database(), migrate_shadow_predictions_for_multiple_models(), pumpportal_balance_monitor() (+6 more)

### Community 2 - "LiveReceiptPersistenceTests"
Cohesion: 0.07
Nodes (29): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_buy_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), fetch_finalized_solana_transaction(), fetch_sol_usd_quote(), fetch_solana_signature_status(), normalize_solana_signature() (+21 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "evaluate_buy"
Cohesion: 0.12
Nodes (16): build_model_features(), calculate_trader_quality_candidate(), clamp_trader_quality(), create_signal_outcome(), decision_from_score(), evaluate_buy(), get_trader_hit_stats(), get_trader_quality_assessment() (+8 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "test_stream_monitoring.py"
Cohesion: 0.10
Nodes (4): PumpPortalBalanceTests, PumpPortalMessageTests, ShadowReviewAlertTests, StreamStateTests

### Community 7 - "app.py"
Cohesion: 0.07
Nodes (102): api_live_execution_readiness(), api_live_positions(), api_shadow_predictions(), api_shadow_stats(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview() (+94 more)

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

### Community 25 - "execute_pumpportal_lightning_buy"
Cohesion: 0.26
Nodes (12): assess_live_model_approval(), check_execution_timeout(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), get_execution_order_status(), get_live_execution_readiness(), mark_order_pending_reconciliation(), maybe_execute_live_copy() (+4 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "stream"
Cohesion: 0.21
Nodes (12): decide_live_position_exit(), evaluate_live_position_exit(), is_pumpportal_error_message(), mark_signature_processed(), mark_stream_problem(), mark_stream_recovered(), post_discord_alert(), save_token_history() (+4 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.18
Nodes (10): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Estado (+2 more)

### Community 30 - "ShadowLogisticModel"
Cohesion: 0.33
Nodes (3): load_shadow_model(), ShadowLogisticModel, ShadowModelError

### Community 32 - "get_shadow_stats"
Cohesion: 0.50
Nodes (5): assess_shadow_challenger(), compare_shadow_models(), get_shadow_stats(), maybe_send_shadow_review_alert(), summarize_shadow_predictions()

### Community 34 - "db"
Cohesion: 0.14
Nodes (18): calculate_copyability_score(), create_execution_order_idempotent(), db(), get_consensus_trader_count(), get_consensus_trader_count_window(), get_daily_live_realized_pnl_sol(), get_live_position_summary(), get_signal_checkpoint_lags() (+10 more)

### Community 35 - "process_signal_outcomes_event"
Cohesion: 0.39
Nodes (8): process_signal_outcomes_event(), signal_outcome_checkpoint_worker(), update_signal_outcome_10s(), update_signal_outcome_15m(), update_signal_outcome_1m(), update_signal_outcome_30s(), update_signal_outcome_5m(), update_signal_outcome_extremes()

## Knowledge Gaps
- **69 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+64 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 158 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.962370786) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.981185498) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.981185287) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ExecutionAdapterTests` connect `ExecutionAdapterTests` to `LiveCopyDispatchTests`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Why does `ShadowLogisticModel` connect `ShadowLogisticModel` to `test_training_pipeline.py`, `app.py`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _69 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1016949152542373 - nodes in this community are weakly interconnected._
- **Should `LiveReceiptPersistenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.0706605222734255 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `evaluate_buy` be split into smaller, more focused modules?**
  _Cohesion score 0.125 - nodes in this community are weakly interconnected._