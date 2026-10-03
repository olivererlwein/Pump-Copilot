"""Read-only consistency check of sampled WSS logs and parsed receipts.

Only ordinal-v2 inbox events from Helius/RPC can verify a candidate. Missing
receipt rows are unknown, not matches. This report never promotes prices.
"""

import argparse
import collections
import json
import math
import sqlite3
import time
from pathlib import Path


def classify(row):
    if row["event_json"] is None:
        return "no_receipt"
    if row["event_index_scheme"] != "ordinal-v2":
        return "unverifiable_scheme"
    if row["source"] not in {"helius", "rpc"}:
        return "unverifiable_source"
    try:
        event = json.loads(row["event_json"])
        if not isinstance(event, dict):
            return "invalid_receipt_event"
        if (row["block_event_ts"] is None
                or not isinstance(event.get("eventIndex"), int)
                or isinstance(event.get("eventIndex"), bool)
                or event.get("blockEventTs") is None):
            return "invalid_receipt_event"
        identity = (
            event.get("signature") == row["signature"]
            and event.get("eventIndex") == row["event_index"]
            and event.get("mint") == row["mint"]
            and event.get("txType") == row["side"]
            and event.get("traderPublicKey") == row["trader"]
            and event.get("blockEventTs") == row["block_event_ts"]
            and event.get("pool") == "pump"
        )
        if not identity:
            return "identity_mismatch"
        receipt_price = float(event["marketCapSol"])
        if not math.isfinite(receipt_price) or receipt_price <= 0:
            return "invalid_receipt_event"
        return "parser_consistent" if math.isclose(
            float(row["market_cap_sol"]), receipt_price,
            rel_tol=1e-9, abs_tol=1e-9,
        ) else "price_mismatch"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "invalid_receipt_event"


def summarize(conn, after_ts=None):
    conn.row_factory = sqlite3.Row
    cutoff = float(after_ts) if after_ts is not None else -1.0
    rows = conn.execute(
        """SELECT c.signature, c.event_index, c.mint, c.received_ts,
                  c.block_event_ts, c.side, c.trader, c.market_cap_sol,
                  i.event_index_scheme, i.source, i.event_json
           FROM helius_wss_cap_price_candidates c
           LEFT JOIN market_event_inbox i
             ON i.signature = c.signature AND i.event_index = c.event_index
           WHERE c.received_ts >= ?
           ORDER BY c.id""",
        (cutoff,),
    )
    results = collections.Counter()
    by_mint = collections.Counter()
    first_ts = last_ts = None
    receipt_rows_present = 0
    for row in rows:
        results[classify(row)] += 1
        by_mint[row["mint"]] += 1
        receipt_rows_present += int(row["event_json"] is not None)
        first_ts = (row["received_ts"] if first_ts is None
                    else min(first_ts, row["received_ts"]))
        last_ts = (row["received_ts"] if last_ts is None
                   else max(last_ts, row["received_ts"]))
    count = sum(results.values())
    buffer_count, max_id = conn.execute(
        "SELECT COUNT(*), MAX(id) FROM helius_wss_cap_price_candidates"
    ).fetchone()
    return {
        "sampled_candidate_rows": count,
        "sampled_mints": len(by_mint),
        "top_sampled_mints": [
            {"mint": mint, "rows": rows}
            for mint, rows in by_mint.most_common(10)
        ],
        "first_received_ts": first_ts,
        "last_received_ts": last_ts,
        "after_ts": after_ts,
        "buffer_rows": buffer_count,
        "buffer_eviction_detected": bool(max_id and max_id > buffer_count),
        "categories": dict(sorted(results.items())),
        "receipt_rows_present": receipt_rows_present,
        "price_comparable_rows": (
            results["parser_consistent"] + results["price_mismatch"]
        ),
        "warning": (
            "Selected rejected-token logs only, biased by per-mint and per-minute "
            "limits and a 5000-row rolling buffer. Receipt overlap is selective. "
            "Parser consistency uses the same Pump event bytes and is not "
            "independent price validation, an executable fill, or profitability."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--after-ts", type=float)
    args = parser.parse_args()
    path = args.db.resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        conn.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + 5
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        print(json.dumps(summarize(conn, args.after_ts), sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
