# Graph Report - pump fun  (2026-09-07)

## Corpus Check
- 26 files · ~34,021 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 319 nodes · 788 edges · 24 communities (17 shown, 6 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `912ebcca`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- simulate_execution
- app.py
- What You Must Do When Invoked
- db
- graphify reference: extra exports and benchmark
- PumpPortalBalanceTests
- stream
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

## God Nodes (most connected - your core abstractions)
1. `db()` - 86 edges
2. `auth()` - 67 edges
3. `require_debug_mode()` - 54 edges
4. `simulate_execution()` - 16 edges
5. `update_execution_order()` - 15 edges
6. `update_paper_position()` - 14 edges
7. `create_execution_order()` - 13 edges
8. `evaluate_buy()` - 13 edges
9. `open_paper_position()` - 12 edges
10. `startup()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py

## Import Cycles
- None detected.

## Communities (24 total, 6 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.13
Nodes (22): load_shadow_model(), build_matrix(), build_pipeline(), build_shadow_artifact(), classification_metrics(), get_readiness_blockers(), load_json(), main() (+14 more)

### Community 1 - "simulate_execution"
Cohesion: 0.20
Nodes (22): can_retry_execution(), check_execution_timeout(), create_execution_order(), create_execution_order_idempotent(), demo_controlled_retry(), demo_execution_timeout(), demo_idempotency_sent(), demo_idempotency_sent_failed() (+14 more)

### Community 2 - "app.py"
Cohesion: 0.09
Nodes (76): api_shadow_predictions(), api_shadow_stats(), api_training_checkpoint_freshness(), api_training_dataset(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), auth() (+68 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "db"
Cohesion: 0.06
Nodes (55): api_training_dataset_preview(), build_model_features(), calculate_copyability_score(), cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), count_open_positions(), create_signal_outcome(), db() (+47 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "PumpPortalBalanceTests"
Cohesion: 0.12
Nodes (3): PumpPortalBalanceTests, PumpPortalMessageTests, StreamStateTests

### Community 7 - "stream"
Cohesion: 0.24
Nodes (10): is_pumpportal_error_message(), mark_signature_processed(), mark_stream_problem(), mark_stream_recovered(), post_discord_alert(), save_token_history(), save_trade(), send_discord_alert() (+2 more)

### Community 8 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

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

## Knowledge Gaps
- **57 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+52 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 93 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `evaluate_buy()` (4× useful, score=3.999986605) _(code changed — re-verify)_
- `open_paper_position()` (2× useful, score=1.999993409) _(code changed — re-verify)_
- `save_trade()` (2× useful, score=1.999993292) _(code changed — re-verify)_
- `stream()` (2× useful, score=1.999993292) _(code changed — re-verify)_
- `decision_from_score()` (2× useful, score=1.999993196) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ShadowLogisticModel` connect `test_training_pipeline.py` to `app.py`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Why does `db()` connect `db` to `simulate_execution`, `app.py`, `stream`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _57 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12912912912912913 - nodes in this community are weakly interconnected._
- **Should `app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08749145591250855 - nodes in this community are weakly interconnected._
- **Should `What You Must Do When Invoked` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `db` be split into smaller, more focused modules?**
  _Cohesion score 0.06060606060606061 - nodes in this community are weakly interconnected._