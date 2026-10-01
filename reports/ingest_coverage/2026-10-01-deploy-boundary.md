# Pump Copilot: ingestion diagnostic boundary

- Previous production commit: `76ec332`.
- New production commit: `6ad715f` (checkpoint batching, nullable event balance,
  lifecycle readiness, per-signal priority-reserve exposure).
- Pre-deploy check: 2026-10-01 23:44:03 UTC; live, buys and sells disabled;
  WSS connected; 11 active wallets.
- New process start: **2026-10-01 23:45:21.332 UTC**
  (`process_started_ts=1790898321.3320065` from
  `/api/wallet-coverage-buckets`). This is the phase cutoff, not push time.
- The service returned HTTP 502 at 23:45:33 UTC during replacement. The new
  read-only lifecycle endpoint was confirmed at 23:47:35 UTC. The exact
  interruption duration was not measured.
- Post-deploy check: live trading disabled, WSS connected, 11 active wallets,
  wallet and token subscriptions ready.
- First post-cutoff readiness sample at 23:51:39 UTC: one WATCH signal, an
  account quote within 30 seconds of outcome creation, and an origin-trader
  sell after recording with explicit zero balance. This is not an executable
  entry or exit, and full-lifecycle PnL remains unavailable.

Keep pre- and post-boundary selections separate. For a post-boundary frozen
candidate report, use the existing validator with
`--cutoff 2026-10-01T23:45:21.332006+00:00`; do not refit the frozen model or
change its threshold. Exclude signals whose 15-minute window overlaps the
deployment interruption when evaluating continuous coverage. Priority-reserve
exposure means rejected token observations in a signal window, not lost fills;
the same rejection can overlap multiple signals.
