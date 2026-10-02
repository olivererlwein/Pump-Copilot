# Prospective paper copy protocol (no orders)

Start: 2026-10-01 23:45:21.332 UTC, after the Railway process replacement.
Use only newly recorded live WATCH/COPY wallet signals; keep the recorded
decision, trader and mint. Exclude OBSERVE_TRADERS as the existing pilot does.
Do not change the COPY threshold or fit a model on this cohort.

For each signal, record the full intent-to-treat denominator. A paper entry
requires the first valid account price observed between outcome creation and
30 seconds later. `price_at_signal` is the trader's event price, not our fill.
No entry quote means `entry_missing`, not a zero return or an invented fill.

Match later sells by trader and mint. Require chain sell time after signal
time, locally recorded time after outcome creation, and a non-null signature,
event index and token balance. Keep ordered partial sells; an explicit zero
balance is final. A missing balance is `balance_unknown`, not a final sale.
Multiple buys of the same mint need an unambiguous inventory allocation
before any position PnL is computed.

Each sell needs a valid account quote observed at or after its recorded time
and within 30 seconds. The current fixed 10s/30s/1m/5m/15m checkpoints do
not guarantee such a quote, so the present lifecycle-readiness endpoint must
not report PnL. A token event market cap is only a separately labeled proxy,
never an executable exit price. Missing sell-side quotes are `exit_missing`;
unclosed positions are `still_open`.

The opt-in `PAPER_COPY_SELL_QUOTE_ENABLED` worker captures a separate delayed
account quote after an eligible trader sell, with one row per trade. It starts
only when account checkpoints are enabled. Its `rpc_calls` log is separate
from fixed-checkpoint probe counts. Before activation, or when the worker is
down, an old unquoted sell is simply `unquoted`, not a proven capture failure.
The readiness endpoint remains read-only and keeps full-lifecycle PnL disabled
even when a quote exists, because inventory allocation and executable fills
are not yet established.

Activated in Railway at the process boundary **2026-10-02 01:32:27.800 UTC**
(`process_started_ts=1790904747.80009`) with
`PAPER_COPY_SELL_QUOTE_ENABLED=true` and the existing account checkpoint
worker enabled. Only this new variable was changed. The lifecycle endpoint
confirmed capture enabled and full-lifecycle PnL disabled; `/api/status`
confirmed live trading, buys and sells false. WSS reconnected with 11 wallets
and both wallet/token subscriptions ready. Count sells recorded after this
boundary separately from earlier unquoted sells; a quote is not a fill.

Once sell-side quotes and inventory allocation are captured, simulate $25 per
eligible entry with at most 10 simultaneous positions, without orders or DB
changes to real/paper execution. Show gross return and separate 2% and 5%
round-trip cost assumptions, missing-exit total-loss sensitivity, per-trader
and independent-mint results, and the fraction of all signals fully priced.
Compare against a same-latency baseline. Do not call the result profitable
unless prospective net results survive missing-data bounds and concentration
checks; no real-trading gate follows automatically.
