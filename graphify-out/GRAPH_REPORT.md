# Graph Report - pump fun  (2026-09-10)

## Corpus Check
- 41 files · ~60,831 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 701 nodes · 1581 edges · 40 communities (30 shown, 8 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 40 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `38fc0d4e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_training_pipeline.py
- startup
- ValueError
- What You Must Do When Invoked
- db
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
- stream
- LiveCopyDispatchTests
- ExecutionAdapterTests
- solana_rpc_fallback.py
- Notas de Claude — auditoría de riesgo (2026-09-09/10)
- get_trader_quality_profile
- TraderQualityCandidateTests
- calculate_trader_quality_candidate
- app.py
- TraderQualityProfileTests
- execute_pumpportal_lightning_buy
- process_signal_outcomes_event
- check_watched_wallet_silence
- get_shadow_stats

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
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `poll_rpc_fallback_once()` --calls--> `fetch_confirmed_transaction()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `poll_rpc_fallback_once()` --calls--> `fetch_signatures_for_address()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `poll_rpc_fallback_once()` --calls--> `parse_watched_wallet_pump_events()`  [EXTRACTED]
  app.py → solana_rpc_fallback.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py

## Import Cycles
- None detected.

## Communities (40 total, 8 thin omitted)

### Community 0 - "test_training_pipeline.py"
Cohesion: 0.08
Nodes (35): load_shadow_model(), main(), schema_without_features(), evaluate_strategy(), main(), simulate_payoff(), build_matrix(), build_pipeline() (+27 more)

### Community 1 - "startup"
Cohesion: 0.15
Nodes (14): cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), expire_old_signal_outcomes(), fetch_solana_balance_sol(), get_persistent_kill_switch(), migrate_database(), migrate_shadow_predictions_for_multiple_models(), pumpportal_balance_monitor() (+6 more)

### Community 2 - "ValueError"
Cohesion: 0.08
Nodes (25): build_pumpportal_exact_sell_payload(), build_pumpportal_lightning_sell_payload(), calculate_wallet_sell_percentage(), fetch_finalized_solana_transaction(), fetch_solana_signature_status(), normalize_solana_signature(), prepare_pumpportal_lightning_sell(), reconcile_pumpportal_execution_order() (+17 more)

### Community 3 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 4 - "db"
Cohesion: 0.09
Nodes (28): calculate_copyability_score(), create_execution_order_idempotent(), db(), get_consensus_trader_count(), get_consensus_trader_count_window(), get_daily_live_realized_pnl_sol(), get_execution_latency(), get_live_position_summary() (+20 more)

### Community 5 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 6 - "WatchedWalletSilenceTests"
Cohesion: 0.07
Nodes (6): PumpPortalBalanceTests, PumpPortalMessageTests, Una wallet que deja de entregar con el stream sano debe ser visible., ShadowReviewAlertTests, StreamStateTests, WatchedWalletSilenceTests

### Community 7 - "auth"
Cohesion: 0.06
Nodes (93): api_live_execution_readiness(), api_live_positions(), api_rpc_fallback_stats(), api_shadow_predictions(), api_shadow_stats(), api_trader_quality_profile(), api_training_dataset(), api_watched_wallets() (+85 more)

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

### Community 25 - "stream"
Cohesion: 0.21
Nodes (12): decide_live_position_exit(), evaluate_live_position_exit(), is_pumpportal_error_message(), mark_signature_processed(), mark_stream_problem(), mark_stream_recovered(), post_discord_alert(), save_token_history() (+4 more)

### Community 26 - "LiveCopyDispatchTests"
Cohesion: 0.12
Nodes (5): EvaluationIdempotencyTests, LiveCopyDispatchTests, LiveTradingGuardTests, NumericRiskValidationTests, PositionConcurrencyTests

### Community 28 - "solana_rpc_fallback.py"
Cohesion: 0.09
Nodes (23): _base58_encode(), _event_payloads(), fetch_confirmed_transaction(), fetch_signatures_for_address(), _parse_pump_amm_trade(), _parse_pump_trade(), parse_watched_wallet_pump_events(), _post_token_amount() (+15 more)

### Community 29 - "Notas de Claude — auditoría de riesgo (2026-09-09/10)"
Cohesion: 0.04
Nodes (46): 1. Cómo se genera una señal, 2. Cómo entra a paper trading, 3. Cómo se grabaría una compra real (no conectada todavía), 4. Cómo se cierran posiciones reales (SÍ está conectado), 5. Hallazgos (con prioridad), 6. Cruce contra `tests/` — qué ya está probado, 7. Diseño pendiente — auto-aprendizaje de `TRADER_QUALITY` (solo boceto, sin código), Advertencia importante sobre los datos (+38 more)

### Community 30 - "get_trader_quality_profile"
Cohesion: 0.08
Nodes (24): calculate_trader_profile_score(), get_trader_activity_concentration(), get_trader_entry_samples(), get_trader_exit_cycles(), get_trader_quality_profile(), get_trader_quality_profiles(), Una muestra por token: el primer outcome completado de cada mint., Reconstruye ciclos entrada -> ventas por token. Solo se reconstruye un ciclo… (+16 more)

### Community 32 - "calculate_trader_quality_candidate"
Cohesion: 0.25
Nodes (9): beta_posterior_rate(), calculate_trader_quality_candidate(), clamp_trader_quality(), Calidad por encogimiento continuo hacia el prior neutral. El posterior Beta ya…, Intervalo de Wilson. Devuelve None si no hay muestras., Media posterior Beta con el mismo prior neutral del score vigente., TP25 antes de SL10, separado del resto de dimensiones., summarize_trader_entry_quality() (+1 more)

### Community 34 - "app.py"
Cohesion: 0.13
Nodes (27): api_training_checkpoint_freshness(), api_training_dataset_preview(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), build_model_features(), create_signal_outcome(), decision_from_score() (+19 more)

### Community 36 - "execute_pumpportal_lightning_buy"
Cohesion: 0.19
Nodes (16): assess_live_model_approval(), build_pumpportal_lightning_buy_payload(), count_open_positions(), execute_pumpportal_lightning_buy(), execute_pumpportal_lightning_sell(), fetch_sol_usd_quote(), get_execution_order_status(), get_live_execution_readiness() (+8 more)

### Community 37 - "process_signal_outcomes_event"
Cohesion: 0.39
Nodes (8): process_signal_outcomes_event(), signal_outcome_checkpoint_worker(), update_signal_outcome_10s(), update_signal_outcome_15m(), update_signal_outcome_1m(), update_signal_outcome_30s(), update_signal_outcome_5m(), update_signal_outcome_extremes()

### Community 38 - "check_watched_wallet_silence"
Cohesion: 0.40
Nodes (5): check_watched_wallet_silence(), get_watched_wallet_activity(), Estado de entrega de cada wallet vigilada. Una wallet puede dejar de entregar…, Avisa cuando una wallet vigilada deja de entregar con el stream sano. Solo se…, watched_wallet_monitor()

### Community 39 - "get_shadow_stats"
Cohesion: 0.50
Nodes (5): assess_shadow_challenger(), compare_shadow_models(), get_shadow_stats(), maybe_send_shadow_review_alert(), summarize_shadow_predictions()

## Knowledge Gaps
- **96 isolated node(s):** `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed`, `Step 2 - Detect files` (+91 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 230 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
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
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Why does `ShadowLogisticModel` connect `test_training_pipeline.py` to `app.py`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `ExecutionAdapterTests` connect `ExecutionAdapterTests` to `LiveCopyDispatchTests`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `ValueError` (e.g. with `build_pumpportal_exact_sell_payload()` and `build_pumpportal_lightning_buy_payload()`) actually correct?**
  _`ValueError` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)` to the rest of the system?**
  _96 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_training_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08384547848990342 - nodes in this community are weakly interconnected._
- **Should `ValueError` be split into smaller, more focused modules?**
  _Cohesion score 0.0777323202805377 - nodes in this community are weakly interconnected._