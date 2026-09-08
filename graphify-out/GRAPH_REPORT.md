# Graph Report - pump fun  (2026-09-07)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 231 nodes · 715 edges · 16 communities (13 shown, 2 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `13b2f4fe`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14

## God Nodes (most connected - your core abstractions)
1. `db()` - 86 edges
2. `auth()` - 67 edges
3. `require_debug_mode()` - 54 edges
4. `simulate_execution()` - 16 edges
5. `update_execution_order()` - 15 edges
6. `update_paper_position()` - 14 edges
7. `create_execution_order()` - 13 edges
8. `evaluate_buy()` - 13 edges
9. `main()` - 12 edges
10. `open_paper_position()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `load_shadow_model()` --uses--> `ShadowLogisticModel`  [INFERRED]
  app.py → shadow_model.py
- `ShadowArtifactTests` --uses--> `ShadowLogisticModel`  [INFERRED]
  tests/test_training_pipeline.py → shadow_model.py

## Import Cycles
- None detected.

## Communities (16 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.13
Nodes (22): load_shadow_model(), build_matrix(), build_pipeline(), build_shadow_artifact(), classification_metrics(), get_readiness_blockers(), load_json(), main() (+14 more)

### Community 1 - "Community 1"
Cohesion: 0.14
Nodes (28): can_retry_execution(), check_execution_timeout(), create_execution_order(), create_execution_order_idempotent(), demo_can_retry(), demo_cannot_retry_risk(), demo_controlled_retry(), demo_execution_timeout() (+20 more)

### Community 2 - "Community 2"
Cohesion: 0.18
Nodes (27): auth(), demo_concurrent_idempotency(), demo_execution_check(), demo_execution_failure(), demo_execution_latency(), demo_execution_order_events(), demo_execution_orders(), demo_idempotency_check() (+19 more)

### Community 3 - "Community 3"
Cohesion: 0.13
Nodes (25): api_shadow_predictions(), api_shadow_stats(), api_training_checkpoint_freshness(), api_training_expired_preview(), api_training_stats(), api_training_stats_by_trader(), create_signal_outcome(), decision_from_score() (+17 more)

### Community 4 - "Community 4"
Cohesion: 0.15
Nodes (19): cleanup_finished_outcome_token(), complete_finished_signal_outcomes(), db(), expire_old_signal_outcomes(), get_consensus_trader_count(), get_consensus_trader_count_window(), get_execution_latency(), get_persistent_kill_switch() (+11 more)

### Community 5 - "Community 5"
Cohesion: 0.19
Nodes (16): demo(), demo_close_old(), demo_duplicate_check(), demo_exit_close(), demo_invalid_event(), demo_partial_close(), demo_partial_sell(), demo_profit() (+8 more)

### Community 6 - "Community 6"
Cohesion: 0.13
Nodes (3): PumpPortalBalanceTests, PumpPortalMessageTests, StreamStateTests

### Community 7 - "Community 7"
Cohesion: 0.18
Nodes (13): fetch_solana_balance_sol(), is_pumpportal_error_message(), mark_signature_processed(), mark_stream_problem(), mark_stream_recovered(), post_discord_alert(), pumpportal_balance_monitor(), record_pumpportal_wallet_balance() (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.20
Nodes (10): count_open_positions(), demo_daily_pnl(), demo_daily_pnl_isolation(), demo_liquidity_check(), demo_risk_mode_isolation(), demo_slippage_check(), get_daily_realized_pnl(), risk_check() (+2 more)

### Community 10 - "Community 10"
Cohesion: 0.39
Nodes (8): process_signal_outcomes_event(), signal_outcome_checkpoint_worker(), update_signal_outcome_10s(), update_signal_outcome_15m(), update_signal_outcome_1m(), update_signal_outcome_30s(), update_signal_outcome_5m(), update_signal_outcome_extremes()

### Community 11 - "Community 11"
Cohesion: 0.29
Nodes (7): demo_exit_open(), demo_live_position(), demo_mode_isolation(), demo_paper_mode_save(), demo_partial_open(), open_paper_position(), save_position_event()

### Community 12 - "Community 12"
Cohesion: 0.33
Nodes (6): api_training_dataset(), api_training_dataset_preview(), build_model_features(), get_training_dataset_rows(), observe_shadow_signal(), record_shadow_prediction()

### Community 13 - "Community 13"
Cohesion: 0.50
Nodes (4): calculate_copyability_score(), get_trader_copyability_stats(), get_trader_reliable_returns(), get_trader_reliable_samples()

## Knowledge Gaps
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ShadowLogisticModel` connect `Community 0` to `Community 3`?**
  _High betweenness centrality (0.146) - this node is a cross-community bridge._
- **Why does `db()` connect `Community 4` to `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 7`, `Community 8`, `Community 10`, `Community 11`, `Community 12`, `Community 13`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.12912912912912913 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.1402116402116402 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.12615384615384614 - nodes in this community are weakly interconnected._
- **Should `Community 4` be split into smaller, more focused modules?**
  _Cohesion score 0.14619883040935672 - nodes in this community are weakly interconnected._
- **Should `Community 6` be split into smaller, more focused modules?**
  _Cohesion score 0.13333333333333333 - nodes in this community are weakly interconnected._