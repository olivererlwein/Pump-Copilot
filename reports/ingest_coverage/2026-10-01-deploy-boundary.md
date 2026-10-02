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

## First post-boundary check (2026-10-02 00:01 UTC)

The frozen validator found four selected signals, all from Cooker and four
distinct mints. Two were complete with no known delivery loss or reserve
rejection. The other two had three and two known losses respectively; each
loss was a token priority-reserve rejection on that signal's mint. All four
had continuous subscription intervals. This is direct attribution, but four
signals from one trader do not establish a general loss rate or a trading
edge. The post-boundary cohort remains at 2/100 complete selections for
review, and the frozen validator reports `ready_for_review=false`.

At 2026-10-02 00:33 UTC, the read-only lifecycle endpoint counted seven
WATCH signals with a nearby account quote and an origin-trader sell with
explicit zero balance; five had matured for 15 minutes and two had continuous
token coverage. It reports `can_estimate_full_lifecycle_pnl=false`: no account
quote is yet captured at trader-sale time. Live trading, live buys and live
sells remained disabled; WSS was connected with 11 wallets ready.

## Token fair-share experiment (not part of the first boundary)

The next code change caps low-priority pending fetches from one mint at 25% of
the token queue, but only once the queue is at least half full. With the
current 500-task global limit, that means a 400-task token ceiling, a 200-task
pressure trigger, and a 100-task per-mint cap. Wallet notices, open live
positions, and already-persisted recovery tasks bypass the new cap; the
existing global limit and wallet reserve remain. Cap rejections are explicit
delivery failures, not prices or lost fills.

Compare cohorts only after the next Railway process start. Check that wallet
queue rejections stay at zero and wallet latency does not rise; compare
selected-signal completeness and reserve/cap exposure by mint, including hot
mints. If wallet delivery worsens or a selected mint loses more coverage due
to the cap without improvement elsewhere, revert this experiment. Do not
change COPY thresholds or infer trading edge from this queue intervention.

Deployed as `c0c66b3`; the new Railway process started at **2026-10-02
01:11:50.087 UTC** (`process_started_ts=1790903510.08653`). Before push at
01:11:13 UTC, WSS was connected with 11 wallets and all live gates were off.
After startup, WSS reconnected, wallet/token subscriptions were ready, and
live trading, buys and sells remained off. The 580-test suite passed locally
before deployment. Re-run the frozen validator with this second timestamp as
`--cutoff` only after post-deploy signals have matured for 15 minutes.
