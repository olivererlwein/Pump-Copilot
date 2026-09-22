# Pump Copilot

**Pump Copilot** is an experimental copy-trading intelligence platform for the [Pump.fun](https://pump.fun) / PumpSwap ecosystem on Solana. It watches selected trader wallets, evaluates their trades against a scoring model, and decides whether an entry is worth copying — with machine learning running in parallel to the classic heuristic model, and layered safety controls gating any real-money execution.

> **Status:** Private Railway deployment for controlled testing and canary validation. Not a public trading bot or financial product.

---

## What it does

1. Watches a configurable list of trader wallets on Solana.
2. Ingests their trades in real time from multiple sources (PumpPortal WebSocket, Helius webhooks/WSS, direct Solana RPC).
3. Normalizes and deduplicates every event using a `signature + event_index` identity — a single Solana transaction can contain multiple valid Pump events.
4. Scores each buy (0–100) across six components: trader quality, entry timing, position size, token structure, consensus, and market context.
5. Classifies the entry as `COPY`, `WATCH`, or `SKIP`.
6. Runs a parallel machine learning classifier (logistic regression) in **shadow mode** — it never overrides the classic decision automatically.
7. Simulates the trade in paper trading, and — only if it clears a long chain of risk gates — can execute a real (canary-limited) trade.
8. Reconciles confirmed live executions against their actual on-chain transaction receipts rather than trusting the broker response alone.
9. Continuously collects labeled outcome data to improve the models.

## Architecture

```
Ingestion (PumpPortal / Helius / Solana RPC)
        ↓
Event normalization & deduplication
        ↓
SQLite persistence (trades, history, evaluations)
        ↓
Scoring (trader · timing · size · token · consensus · market)
        ↓
Classic decision (COPY / WATCH / SKIP)
        ↓
Shadow ML (logistic regression, incumbent vs. challenger)
        ↓
Paper trading (simulated PnL, idempotent event application)
        ↓
Live safety gates (canary limits, kill switch, model approval, risk checks)
        ↓
Execution (PumpPortal Lightning) → on-chain reconciliation
        ↓
Position management (TP/SL, trader-exit mirroring, account-price monitor)
        ↓
Learning data (outcomes, checkpoints, dataset validation, economics)
```

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Data | SQLite |
| Blockchain | Solana (`solders`), direct RPC, Pump bonding-curve & PumpSwap parsing |
| Real-time data | PumpPortal WebSocket, Helius Webhooks & Standard WSS |
| Machine learning | scikit-learn (logistic regression), NumPy, SciPy, Joblib |
| Frontend | Installable PWA — vanilla HTML/CSS/JS (no framework) |
| Deployment | Private Railway deployment |

## Engineering highlights

- **Idempotency by design.** Trade identity is `signature + event_index`, not just the transaction signature, since one Solana transaction can carry multiple Pump events. Paper and live position updates use dedicated idempotency mechanisms designed to prevent duplicate event application.
- **Shadow-mode ML.** The classifier runs continuously against live data but is never promoted to production automatically — promotion requires clearing explicit readiness criteria (minimum labeled rows, class balance, holdout concentration limits, deployment-ready flag).
- **Leakage-aware training.** The dataset pipeline uses temporal and group (per-mint) splitting rather than a random split, and evaluates models on economic outcomes (simulated PnL after fees) in addition to standard ML metrics.
- **Defense in depth around real money.** A live buy requires clearing dozens of independent gates (wallet/balance checks, kill switch, canary limits, model-version match, liquidity/slippage checks, exit-feed availability, idempotency) before a single transaction is sent.
- **On-chain reconciliation.** Confirmed live executions are reconciled against their actual Solana transaction receipts; the system does not assume a trade succeeded solely because the broker API returned a signature.
- **Redundant exit coverage.** Live positions can be closed via either an on-chain account-price monitor or the event/webhook feed, reducing single-point-of-failure risk on position exits.
- **Automated test suite** covering risk controls, idempotency, model economics, stream routing, and on-chain price reading across 20+ test modules.

## Why this project

Pump Copilot started as a small monitor + paper-trading script and grew into a small trading-systems platform: real-time ingestion from multiple redundant sources, a scoring engine, an ML model evaluated safely in parallel to production logic, and a real-money execution path designed to fail closed by default. It's the project where I've had to think hardest about correctness under concurrency, data leakage in ML pipelines, and designing safety gates for a system that can spend real money if it's wrong.

---

*This is a private research project. Source and configuration details involving trading credentials, wallets, and provider API keys are intentionally not included in this document.*
