# Graph Report - pump fun  (2026-09-09)

## Corpus Check
- 33 files · ~37,908 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 373 nodes · 933 edges · 29 communities (23 shown, 5 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 12 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b98bd6b8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- get_shadow_stats
- auth
- What You Must Do When Invoked
- db
- graphify reference: extra exports and benchmark
- test_stream_monitoring.py
- require_debug_mode
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
- app.py
- ShadowLogisticModel
- simulate_execution

## God Nodes (most connected - your core abstractions)
1. `db()` - 87 edges
2. `auth()` - 67 edges
3. `require_debug_mode()` - 54 edges
4. `main()` - 18 edges
5. `simulate_execution()` - 16 edges
6. `update_execution_order()` - 15 edges
7. `update_paper_position()` - 14 edges
8. `create_execution_order()` - 13 edges
9. `evaluate_buy()` - 13 edges
10. `evaluate_strategy()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `main()` --calls--> `evaluate_strategy()`  [EXTRACTED]
  scripts/analyze_feature_ablation.py → scripts/compare_model_economics.py
- `main()` --calls--> `simulate_payoff()`  [EXTRACTED]
  scripts/analyze_feature_ablation.py → scripts/compare_model_economics.py
- `main()` --calls--> `load_json()`  [EXTRACTED]
  scripts/analyze_feature_ablation.py → scripts/train_baseline_model.py

## Import Cycles
- None detected.

## Communities (29 total, 5 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.10
Nodes (33): main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline(), build_shadow_artifact() (+25 more)

### Community 1 - "get_shadow_stats"
Cohesion: 0.18
Nodes (12): assess_shadow_challenger(), compare_shadow_models(), fetch_solana_balance_sol(), get_shadow_stats(), mark_stream_problem(), mark_stream_recovered(), maybe_send_shadow_review_alert(), post_discord_alert() (+4 more)

### Community 2 - "auth"
Cohesion: 0.17
Nodes (23): api_shadow_predictions(), api_shadow_stats(), api_training_dataset(), auth(), demo_concurrent_idempotency(), demo_execution_latency(), demo_execution_order_events(), demo_execution_orders() (+15 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "db"
Cohesion: 0.06
Nodes (55): api_training_dataset_preview(), build_model_features(), calculate_copyability_score(), cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), create_signal_outcome(), db(), decision_from_score() (+47 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "test_stream_monitoring.py"
Cohesion: 0.10
Nodes (4): PumpPortalBalanceTests, PumpPortalMessageTests, ShadowReviewAlertTests, StreamStateTests

### Community 7 - "require_debug_mode"
Cohesion: 0.22
Nodes (20): demo(), demo_close_old(), demo_duplicate_check(), demo_exit_close(), demo_exit_open(), demo_invalid_event(), demo_live_position(), demo_partial_close() (+12 more)

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
Cohesion: 0.19
Nodes (19): can_retry_execution(), check_execution_timeout(), create_execution_order(), create_execution_order_idempotent(), demo_can_retry(), demo_cannot_retry_risk(), demo_controlled_retry(), demo_execution_timeout() (+11 more)

### Community 26 - "app.py"
Cohesion: 0.14
Nodes (25): api_training_checkpoint_freshness(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check() (+17 more)

### Community 28 - "ShadowLogisticModel"
Cohesion: 0.33
Nodes (3): load_shadow_model(), ShadowLogisticModel, ShadowModelError

### Community 29 - "simulate_execution"
Cohesion: 0.16
Nodes (14): demo_execution_check(), demo_execution_failure(), demo_idempotency_check(), demo_idempotency_failed(), demo_idempotency_risk_blocked(), demo_idempotency_sent(), demo_idempotency_sent_failed(), demo_mode_save() (+6 more)

## Knowledge Gaps
- **60 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 108 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
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

- **Why does `ShadowLogisticModel` connect `ShadowLogisticModel` to `test_training_pipeline.py`, `app.py`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `load_shadow_model()` connect `ShadowLogisticModel` to `test_training_pipeline.py`, `app.py`, `db`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _60 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10225988700564972 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `db` be split into smaller, more focused modules?**
  _Cohesion score 0.06060606060606061 - nodes in this community are weakly interconnected._
- **Should `test_stream_monitoring.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1 - nodes in this community are weakly interconnected._