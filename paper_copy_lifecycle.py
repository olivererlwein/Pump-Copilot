"""Conservative, read-only replay of observed wallet trades and account quotes."""

import collections
import math


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _balance_matches(actual, expected):
    return (isinstance(actual, (int, float)) and math.isfinite(actual)
            and actual >= 0 and math.isclose(
                actual, expected, rel_tol=1e-6, abs_tol=1e-3
            ))


def replay_paper_cycles(signals, trades, stake_usd=25.0):
    """Return only closed, fully observed cycles as hypothetical outcomes."""
    trades_by_pair = collections.defaultdict(list)
    for trade in trades:
        trades_by_pair[(trade["trader"], trade["mint"])].append(trade)
    signals_by_pair = collections.defaultdict(list)
    for signal in signals:
        signals_by_pair[(signal["trader"], signal["mint"])].append(signal)

    results = []
    for pair, pair_signals in signals_by_pair.items():
        path = sorted(trades_by_pair[pair], key=lambda t: (t["ts"], t["id"]))
        identities = collections.defaultdict(list)
        for index, trade in enumerate(path):
            identities[(trade["signature"], trade["event_index"])].append(index)
        occupied_until = -1
        for signal in sorted(pair_signals, key=lambda s: (s["ts"], s["id"])):
            result = {
                "signal_id": signal["id"], "trader": signal["trader"],
                "mint": signal["mint"], "decision": signal["decision"],
            }
            results.append(result)
            if signal["outcome_count"] != 1:
                result["status"] = "outcome_missing_or_duplicate"
                continue
            if not signal["signature"] or signal["event_index"] is None:
                result["status"] = "entry_identity_missing_or_duplicate"
                continue
            matches = identities[(signal["signature"], signal["event_index"])]
            if len(matches) != 1:
                result["status"] = "entry_identity_missing_or_duplicate"
                continue
            entry_index = matches[0]
            if entry_index <= occupied_until:
                result["status"] = "overlapping_signal"
                continue
            entry = path[entry_index]
            if (entry["side"] != "buy" or entry["transport"] == "rpc"
                    or entry["recorded_ts"] is None
                    or entry["ts"] != signal["ts"]
                    or signal["created_ts"] is None
                    or entry["recorded_ts"] > signal["created_ts"]):
                result["status"] = "unsupported_entry"
                continue
            if (not _positive(signal["entry_price"])
                    or signal["entry_pool"] not in ("curve", "amm")
                    or signal["entry_observed_ts"] is None):
                result["status"] = "missing_entry_quote"
                continue
            amount = entry["token_amount"]
            if not _positive(amount) or not _balance_matches(
                entry["new_token_balance"], amount
            ):
                result["status"] = "opening_inventory_unknown"
                continue
            balance = float(entry["new_token_balance"])
            result["paper_entered"] = True
            result["entry_observed_ts"] = signal["entry_observed_ts"]
            remaining = 1.0
            proceeds = 0.0
            partial_sells = 0
            sale_legs = []
            last_ts = entry["ts"]
            last_recorded_ts = entry["recorded_ts"]
            last_quote_ts = signal["entry_observed_ts"]
            result["status"] = "open"
            occupied_until = len(path) - 1
            for index in range(entry_index + 1, len(path)):
                trade = path[index]
                if trade["ts"] <= last_ts:
                    result["status"] = "trade_order_ambiguous"
                    break
                last_ts = trade["ts"]
                amount = trade["token_amount"]
                side = trade["side"]
                if (trade["event_index"] is None or not trade["signature"]
                        or trade["recorded_ts"] is None
                        or side not in ("buy", "sell") or not _positive(amount)):
                    result["status"] = "trade_identity_or_amount_missing"
                    break
                if len(identities[(trade["signature"], trade["event_index"])]) != 1:
                    result["status"] = "duplicate_trade_identity"
                    break
                timing_flags = []
                if trade["transport"] == "rpc":
                    timing_flags.append("rpc_transport")
                if trade["recorded_ts"] <= signal["created_ts"]:
                    timing_flags.append("before_signal_recorded")
                if trade["recorded_ts"] <= last_recorded_ts:
                    timing_flags.append("non_monotonic_recorded_time")
                if trade["recorded_ts"] <= last_quote_ts:
                    timing_flags.append("before_previous_quote")
                if (timing_flags == ["before_previous_quote"]
                        and last_quote_ts == signal["entry_observed_ts"]):
                    if side == "sell":
                        result["status"] = "sell_before_entry_quote"
                        result["paper_entered"] = False
                        result["early_sell"] = {
                            "seconds_after_signal": round(
                                trade["recorded_ts"] - signal["created_ts"], 3
                            ),
                            "seconds_before_entry_quote": round(
                                signal["entry_observed_ts"] - trade["recorded_ts"], 3
                            ),
                        }
                        occupied_until = index
                        break
                    if not _balance_matches(
                        trade["new_token_balance"], balance + amount
                    ):
                        result["status"] = "opening_inventory_unknown"
                        result["paper_entered"] = False
                        break
                    balance = float(trade["new_token_balance"])
                    last_recorded_ts = trade["recorded_ts"]
                    result["pre_entry_buys"] = result.get("pre_entry_buys", 0) + 1
                    continue
                if timing_flags:
                    result["status"] = "late_or_ambiguous_arrival"
                    result["timing_flags"] = timing_flags
                    result["first_ambiguous_trade"] = {
                        "trade_id": trade["id"], "side": side,
                        "transport": trade["transport"],
                        "block_ts": trade["ts"],
                        "recorded_ts": trade["recorded_ts"],
                        "signal_created_ts": signal["created_ts"],
                        "previous_recorded_ts": last_recorded_ts,
                        "previous_quote_ts": last_quote_ts,
                    }
                    break
                last_recorded_ts = trade["recorded_ts"]
                expected = balance + amount if side == "buy" else balance - amount
                if expected < -1e-3 or not _balance_matches(
                    trade["new_token_balance"], max(0.0, expected)
                ):
                    result["status"] = "inventory_gap"
                    break
                if side == "buy":
                    balance = float(trade["new_token_balance"])
                    continue
                quote = trade["exit_price"]
                if (not _positive(quote)
                        or trade["exit_pool"] != signal["entry_pool"]
                        or trade["exit_observed_ts"] is None
                        or not trade["recorded_ts"] <= trade["exit_observed_ts"]
                        <= trade["recorded_ts"] + 30):
                    result["status"] = "missing_or_incompatible_exit_quote"
                    break
                last_quote_ts = trade["exit_observed_ts"]
                sold_fraction = min(1.0, amount / balance)
                leg_proceeds = (stake_usd * remaining * sold_fraction
                                * quote / signal["entry_price"])
                proceeds += leg_proceeds
                remaining *= 1.0 - sold_fraction
                balance = float(trade["new_token_balance"])
                partial_sells += 1
                sale_legs.append({
                    "trade_id": trade["id"],
                    "sold_fraction_of_trader_balance": round(sold_fraction, 6),
                    "paper_remaining_fraction": round(remaining, 6),
                    "observed_exit_ts": trade["exit_observed_ts"],
                    "hypothetical_proceeds_usd": round(leg_proceeds, 2),
                })
                if balance == 0:
                    result.update({
                        "status": "complete", "sells": partial_sells,
                        "sale_legs": sale_legs,
                        "exit_ts": trade["ts"],
                        "gross_usd": round(proceeds - stake_usd, 2),
                        "net_usd_at_2pct": round(proceeds - stake_usd * 1.02, 2),
                        "net_usd_at_5pct": round(proceeds - stake_usd * 1.05, 2),
                    })
                    occupied_until = index
                    break

    counts = collections.Counter(row["status"] for row in results)
    timing_flag_counts = collections.Counter(
        flag for row in results for flag in row.get("timing_flags", ())
    )
    def summary():
        return {"complete": 0, "net_usd_at_2pct": 0.0,
                "net_usd_at_5pct": 0.0}
    by_trader = collections.defaultdict(summary)
    by_mint = collections.defaultdict(summary)
    for row in results:
        if row["status"] == "complete":
            for stats in (by_trader[row["trader"]], by_mint[row["mint"]]):
                stats["complete"] += 1
                for key in ("net_usd_at_2pct", "net_usd_at_5pct"):
                    stats[key] = round(stats[key] + row[key], 2)
    uncertain_entered = sum(
        row.get("paper_entered", False) and row["status"] != "complete"
        for row in results
    )
    complete_net_2pct = sum(
        row.get("net_usd_at_2pct", 0) for row in results
    )
    complete_net_5pct = sum(
        row.get("net_usd_at_5pct", 0) for row in results
    )
    return {
        "counts": dict(counts), "timing_flag_counts": dict(timing_flag_counts),
        "by_trader": dict(by_trader),
        "by_mint": dict(by_mint), "rows": results,
        "completed_sample_only": {
            "net_usd_at_2pct": round(complete_net_2pct, 2),
            "net_usd_at_5pct": round(complete_net_5pct, 2),
        },
        "uncertain_entered_cycles": uncertain_entered,
        "if_uncertain_entered_total_loss": {
            "net_usd_at_2pct": round(
                complete_net_2pct - stake_usd * uncertain_entered, 2
            ),
            "net_usd_at_5pct": round(
                complete_net_5pct - stake_usd * uncertain_entered, 2
            ),
        },
    }
