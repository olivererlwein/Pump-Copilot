# Graph Report - pump fun  (2026-09-09)

## Corpus Check
- 36 files · ~42,130 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 450 nodes · 1086 edges · 31 communities (25 shown, 5 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 24 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b33da94a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- app.py
- LiveReceiptPersistenceTests
- What You Must Do When Invoked
- startup
- graphify reference: extra exports and benchmark
- test_stream_monitoring.py
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
- simulate_execution
- get
- ExecutionAdapterTests
- open_paper_position
- execute_pumpportal_lightning_buy
- evaluate_buy

## God Nodes (most connected - your core abstractions)
1. `db()` - 94 edges
2. `auth()` - 68 edges
3. `require_debug_mode()` - 54 edges
4. `ExecutionAdapterTests` - 20 edges
5. `main()` - 18 edges
6. `simulate_execution()` - 16 edges
7. `update_execution_order()` - 14 edges
8. `update_paper_position()` - 14 edges
9. `create_execution_order()` - 13 edges
10. `evaluate_buy()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `reconcile_pumpportal_execution_order()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `record_finalized_buy_position()` --calls--> `parse_buy_receipt()`  [EXTRACTED]
  app.py → solana_receipts.py
- `main()` --calls--> `evaluate_strategy()`  [EXTRACTED]
  scripts/analyze_feature_ablation.py → scripts/compare_model_economics.py

## Import Cycles
- None detected.

## Communities (31 total, 5 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.08
Nodes (39): build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), load_shadow_model(), prepare_pumpportal_lightning_sell(), main(), schema_without_features(), evaluate_strategy(), main() (+31 more)

### Community 1 - "app.py"
Cohesion: 0.11
Nodes (38): api_shadow_predictions(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), calculate_copyability_score() (+30 more)

### Community 2 - "LiveReceiptPersistenceTests"
Cohesion: 0.13
Nodes (10): record_finalized_buy_position(), parse_buy_receipt(), _parse_buy_receipt(), Conservative accounting of finalized buys from Solana jsonParsed receipts., Return exact balance deltas, not an inferred swap price or fee breakdown. Net…, unsigned_integer(), buy_receipt(), LiveReceiptPersistenceTests (+2 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "startup"
Cohesion: 0.08
Nodes (36): assess_shadow_challenger(), cleanup_finished_outcome_token(), compare_shadow_models(), complete_finished_signal_outcomes(), expire_old_signal_outcomes(), fetch_solana_balance_sol(), get_live_execution_readiness(), get_persistent_kill_switch() (+28 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "test_stream_monitoring.py"
Cohesion: 0.10
Nodes (4): PumpPortalBalanceTests, PumpPortalMessageTests, ShadowReviewAlertTests, StreamStateTests

### Community 7 - "auth"
Cohesion: 0.17
Nodes (31): auth(), demo(), demo_close_old(), demo_duplicate_check(), demo_execution_check(), demo_execution_failure(), demo_execution_order_events(), demo_exit_close() (+23 more)

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

### Community 25 - "simulate_execution"
Cohesion: 0.23
Nodes (19): check_execution_timeout(), create_execution_order(), demo_controlled_retry(), demo_execution_timeout(), demo_idempotency_sent(), demo_idempotency_sent_failed(), demo_pending_reconciliation(), demo_retry_function() (+11 more)

### Community 26 - "get"
Cohesion: 0.11
Nodes (20): api_live_execution_readiness(), api_shadow_stats(), can_retry_execution(), demo_can_retry(), demo_cannot_retry_risk(), demo_concurrent_idempotency(), demo_execution_orders(), demo_idempotency_risk_blocked() (+12 more)

### Community 27 - "ExecutionAdapterTests"
Cohesion: 0.08
Nodes (4): ExecutionAdapterTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "open_paper_position"
Cohesion: 0.16
Nodes (14): count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check(), demo_mode_isolation(), demo_paper_mode_save(), demo_risk_mode_isolation(), demo_slippage_check() (+6 more)

### Community 29 - "execute_pumpportal_lightning_buy"
Cohesion: 0.19
Nodes (13): build_pumpportal_lightning_buy_payload(), execute_pumpportal_lightning_buy(), fetch_finalized_solana_transaction(), fetch_sol_usd_quote(), fetch_solana_signature_status(), normalize_solana_signature(), prepare_pumpportal_lightning_buy(), pumpportal_execution_reconciliation_worker() (+5 more)

### Community 30 - "evaluate_buy"
Cohesion: 0.17
Nodes (12): build_model_features(), create_signal_outcome(), decision_from_score(), evaluate_buy(), observe_shadow_signal(), record_shadow_prediction(), score_consensus(), score_market_context() (+4 more)

## Knowledge Gaps
- **60 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 135 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.962370786) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.981185498) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.981185382) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.981185287) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ShadowLogisticModel` connect `test_training_pipeline.py` to `app.py`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_lightning_buy_payload()` and `build_pumpportal_lightning_sell_payload()`) actually correct?**
  _`ValueError` has 19 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _60 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07981220657276995 - nodes in this community are weakly interconnected._
- **Should `app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._
- **Should `LiveReceiptPersistenceTests` be split into smaller, more focused modules?**
  _Cohesion score 0.12561576354679804 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._