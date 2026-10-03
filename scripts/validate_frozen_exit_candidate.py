"""Validate the frozen tp100_time candidate on post-freeze observations.

The training population, feature schema, threshold, policy, costs and cutoff
are fixed before fetching the prospective outcomes. This script is offline:
it reads production through authenticated GET endpoints and never saves or
promotes a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from collections import Counter
from datetime import datetime
from urllib.request import Request, urlopen

import numpy as np
from dotenv import load_dotenv

if __package__:
    from scripts.remainder_exit_backtest import (
        TRAILING_DROP_FROM_PEAK,
        analyse,
        pnl_of,
        simulate,
    )
    from scripts.staged_payoff_backtest import is_ambiguous
    from scripts.train_baseline_model import (
        build_matrix,
        build_pipeline,
        fit_pipeline,
        load_json,
        validate_dataset,
    )
else:
    from remainder_exit_backtest import (
        TRAILING_DROP_FROM_PEAK,
        analyse,
        pnl_of,
        simulate,
    )
    from staged_payoff_backtest import is_ambiguous
    from train_baseline_model import (
        build_matrix,
        build_pipeline,
        fit_pipeline,
        load_json,
        validate_dataset,
    )


DEFAULT_BASE_URL = "https://web-production-4ea0d.up.railway.app"
DEFAULT_CUTOFF = "2026-09-22T20:58:47+00:00"
DEFAULT_FROZEN_SHA256 = (
    "6c9377bdc4ed664cd9be85018df112d8af4bf56ae4612dd75b3940cf2842d59d"
)
DEFAULT_THRESHOLD = 0.45
DEFAULT_COST_PER_SIDE = 0.01
DEFAULT_HOLD_SECONDS = 900.0
MIN_COMPLETE_COVERAGE_SELECTIONS = 100


def fetch_json(base_url: str, path: str, app_token: str) -> dict:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"x-app-token": app_token},
        method="GET",
    )
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def parse_cutoff(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Cutoff must include a timezone")
    return parsed.timestamp()


def verify_file_sha256(path: str, expected: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected.strip().lower():
        raise ValueError(
            f"Frozen dataset SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def validate_frozen_population(
    frozen_rows: list[dict], cutoff_ts: float
) -> set[int]:
    signal_ids: set[int] = set()
    for row in frozen_rows:
        signal_id = int(row["signal_id"])
        if signal_id in signal_ids:
            raise ValueError(f"Duplicate frozen signal_id: {signal_id}")
        signal_ids.add(signal_id)

        signal_ts = float(row.get("signal_ts") or 0)
        if signal_ts > cutoff_ts:
            raise ValueError(
                f"Frozen signal {signal_id} is newer than the cutoff"
            )
    return signal_ids


def fit_frozen_pipeline(frozen_rows: list[dict], schema: dict):
    matrix, _ = build_matrix(frozen_rows, schema)
    targets = np.asarray([row[schema["target"]] for row in frozen_rows])
    return fit_pipeline(
        build_pipeline(schema),
        matrix,
        targets,
        frozen_rows,
        schema,
        "none",
    )


def build_prospective_rows(
    dataset_rows: list[dict],
    path_rows: list[dict],
    pipeline,
    schema: dict,
    cutoff_ts: float,
    frozen_signal_ids: set[int],
) -> tuple[list[dict], dict]:
    future = []
    overlaps = []
    for row in dataset_rows:
        if float(row.get("signal_ts") or 0) <= cutoff_ts:
            continue
        signal_id = int(row["signal_id"])
        if signal_id in frozen_signal_ids:
            overlaps.append(signal_id)
            continue
        future.append(row)
    if overlaps:
        preview = ", ".join(str(value) for value in overlaps[:5])
        raise ValueError(
            f"Prospective dataset overlaps frozen signal IDs: {preview}"
        )
    paths = {int(row["signal_id"]): row for row in path_rows}
    if not future:
        return [], {"future_dataset_rows": 0, "missing_paths": 0}

    matrix, _ = build_matrix(future, schema)
    probabilities = pipeline.predict_proba(matrix)[:, 1]
    result = []
    missing_paths = 0
    for row, probability in zip(future, probabilities):
        path_record = paths.get(int(row["signal_id"]))
        if not path_record or not path_record.get("checkpoint_path"):
            missing_paths += 1
            continue
        entry = float(path_record["price_at_signal"])
        path = path_record["checkpoint_path"]
        result.append({
            **row,
            "probability": float(probability),
            "price_at_signal": entry,
            "path": path,
            "event_path": path_record.get("event_path") or [],
            "subscription_coverage": (
                path_record.get("subscription_coverage") or {}
            ),
            "ingest_trace": path_record.get("ingest_trace") or {},
            "ambiguous": is_ambiguous(entry, path),
        })
    return result, {
        "future_dataset_rows": len(future),
        "missing_paths": missing_paths,
    }


def selected_subscription_gap_diagnostics(rows, threshold):
    diagnostics = []
    for row in rows:
        coverage = row.get("subscription_coverage") or {}
        if (float(row["probability"]) < threshold
                or not coverage.get("measurement_available")
                or coverage.get("intervals")):
            continue
        trace = row.get("ingest_trace") or {}
        signal_ts = float(row["signal_ts"])

        def delay(key):
            value = trace.get(key)
            return float(value) - signal_ts if value is not None else None

        diagnostics.append({
            "signal_id": row["signal_id"],
            "signal_ts": signal_ts,
            "trader": row["trader"],
            "mint": row["mint"],
            "transport": trace.get("transport"),
            "source": trace.get("source"),
            "inbox_received_delay_seconds": delay("inbox_received_ts"),
            "wss_received_delay_seconds": delay("wss_received_ts"),
            "previous_subscription_end_seconds": delay(
                "previous_subscription_end_ts"
            ),
            "next_subscription_start_seconds": delay(
                "next_subscription_start_ts"
            ),
            "first_token_notification_seconds": delay(
                "first_token_notification_ts"
            ),
        })
    return diagnostics


def selected_coverage_attribution(rows, threshold):
    """Attribute selected token-coverage failures; flags may overlap."""
    by_trader = {}
    by_mint = {}
    issues = []
    for row in rows:
        if float(row["probability"]) < threshold:
            continue
        coverage = row.get("subscription_coverage") or {}
        trader = str(row.get("trader") or "")
        mint = str(row.get("mint") or "")
        measured = coverage.get("measurement_available") is True
        complete = measured and coverage.get("complete") is True
        known_loss = (
            int(coverage.get("known_delivery_failures") or 0)
            if measured else 0
        )
        cap_known = measured and coverage.get("mint_cap_rejections") is not None
        cap_rejections = (
            int(coverage["mint_cap_rejections"]) if cap_known else 0
        )
        priority_known = (
            measured
            and coverage.get("priority_reserve_trace_available") is True
        )
        priority_rejections = (
            int(coverage.get("priority_reserve_rejections") or 0)
            if priority_known else 0
        )
        remaining_loss = max(0, known_loss - cap_rejections - priority_rejections)
        flags = {
            "complete": complete,
            "unmeasured": not measured,
            "cap_trace_unavailable": measured and not cap_known,
            "known_delivery_loss": known_loss > 0,
            "mint_cap_exposed": cap_known and cap_rejections > 0,
            "priority_reserve_exposed": priority_rejections > 0,
            "other_delivery_loss": (
                cap_known and priority_known and remaining_loss > 0
            ),
            "unclassified_delivery_loss": (
                (not cap_known or not priority_known)
                and remaining_loss > 0
            ),
            "subscription_gap": bool(
                measured and not complete
                and coverage.get("intervals")
                and not coverage.get("subscription_continuous")
            ),
            "no_subscription_interval": bool(
                measured and not complete
                and not coverage.get("intervals")
            ),
            "unresolved_fetch": bool(
                coverage.get("unresolved_delivery_fetches")
            ),
        }
        if not complete and not any(flags.values()):
            flags["unattributed_incomplete"] = True
        for key, value in ((trader, by_trader), (mint, by_mint)):
            counts = value.setdefault(key, Counter())
            counts["selected"] += 1
            counts.update(name for name, present in flags.items() if present)
        if not flags["complete"]:
            issues.append({
                "signal_id": int(row["signal_id"]),
                "trader": trader,
                "mint": mint,
                "flags": [name for name, present in flags.items() if present],
                "mint_cap_rejections": int(
                    coverage.get("mint_cap_rejections") or 0
                ),
            })
    return {
        "by_trader": {key: dict(value) for key, value in sorted(by_trader.items())},
        "by_mint": {key: dict(value) for key, value in sorted(by_mint.items())},
        "incomplete_signals": issues,
        "note": (
            "Flags can overlap. Rejection counts can repeat across signals "
            "on the same mint with overlapping 15m windows; do not sum "
            "them as unique events. Token delivery loss is not wallet-signal loss."
        ),
    }


def selected_records(
    rows: list[dict], threshold: float, cost_per_side: float
) -> list[dict]:
    records = []
    for row in rows:
        if float(row["probability"]) < threshold:
            continue
        result = simulate(
            float(row["price_at_signal"]),
            row["path"],
            "tp100_time",
            TRAILING_DROP_FROM_PEAK,
        )
        records.append({
            **row,
            **pnl_of(result, cost_per_side),
            "remaining": result["remaining"],
            "stage": result["stage"],
            "stop_hit": result["stop_hit"],
        })
    return records


def concentration(records: list[dict], key: str, limit: int = 10) -> dict:
    counts = Counter(str(row.get(key) or "unknown") for row in records)
    pnl = {}
    for row in records:
        value = str(row.get(key) or "unknown")
        pnl[value] = pnl.get(value, 0.0) + float(row["net"])
    total = len(records)
    net_desc = sorted(pnl.items(), key=lambda item: item[1], reverse=True)
    return {
        "groups": len(counts),
        "largest_count_share": (
            max(counts.values()) / total if total else None
        ),
        "positive_groups": sum(value > 0 for value in pnl.values()),
        "negative_groups": sum(value < 0 for value in pnl.values()),
        "top_counts": dict(counts.most_common(limit)),
        "top_net": dict(net_desc[:limit]),
        "bottom_net": dict(reversed(net_desc[-limit:])),
    }


def prospective_report(
    rows: list[dict],
    threshold: float = DEFAULT_THRESHOLD,
    cost_per_side: float = DEFAULT_COST_PER_SIDE,
    hold_seconds: float = DEFAULT_HOLD_SECONDS,
) -> dict:
    overall = analyse(
        rows,
        threshold,
        "tp100_time",
        cost_per_side,
        TRAILING_DROP_FROM_PEAK,
        hold_seconds,
    )
    if overall is None:
        return {"ready_for_review": False, "blockers": ["NO_SELECTIONS"]}

    records = selected_records(rows, threshold, cost_per_side)
    unambiguous_rows = [row for row in rows if not row["ambiguous"]]
    unambiguous_sensitivity = analyse(
        unambiguous_rows,
        threshold,
        "tp100_time",
        cost_per_side,
        TRAILING_DROP_FROM_PEAK,
        hold_seconds,
    )
    cap_trace_rows = [
        row for row in rows
        if (row.get("subscription_coverage") or {}).get(
            "measurement_available"
        ) is True
        and (row.get("subscription_coverage") or {}).get(
            "mint_cap_rejections"
        ) is not None
    ]
    selected_without_cap_trace = (
        sum(float(row["probability"]) >= threshold for row in rows)
        - sum(float(row["probability"]) >= threshold for row in cap_trace_rows)
    )
    cap_exposure_sensitivity = {}
    for label, exposed in (("unexposed", False), ("exposed", True)):
        subset = [
            row for row in cap_trace_rows
            if bool(row["subscription_coverage"]["mint_cap_rejections"])
            is exposed
        ]
        result = analyse(
            subset, threshold, "tp100_time", cost_per_side,
            TRAILING_DROP_FROM_PEAK, hold_seconds,
        )
        cap_exposure_sensitivity[label] = (
            {**result, "unique_mints": len({
                row["mint"] for row in subset
                if float(row["probability"]) >= threshold
            })}
            if result is not None else None
        )
    event_covered_rows = []
    dense_rows = []
    event_sequence_rows = []
    event_point_counts = []
    first_event_delays = []
    event_sources = Counter()
    measured_coverage_rows = []
    complete_coverage_rows = []
    complete_event_sequence_rows = []
    coverage_ratios = []
    selected_coverage = Counter()
    selected_coverage_ratios = []
    priority_rejection_mints = Counter()
    priority_rejection_events_by_mint = Counter()
    mint_cap_mints = Counter()
    mint_cap_events_by_mint = Counter()
    for row in rows:
        subscription_coverage = row.get("subscription_coverage") or {}
        measurement_available = (
            subscription_coverage.get("measurement_available") is True
        )
        coverage_complete = bool(
            measurement_available
            and subscription_coverage.get("complete") is True
        )
        if float(row["probability"]) >= threshold:
            if measurement_available:
                selected_coverage["measured"] += 1
                priority_trace_available = (
                    subscription_coverage.get(
                        "priority_reserve_trace_available"
                    ) is True
                )
                if priority_trace_available:
                    selected_coverage["priority_trace_available_rows"] += 1
                if "mint_cap_rejections" in subscription_coverage:
                    selected_coverage["mint_cap_trace_rows"] += 1
                activation_ts = subscription_coverage.get(
                    "token_tracking_observable_from_ts",
                    subscription_coverage.get("measurement_started_ts"),
                )
                if activation_ts is not None:
                    if float(row["signal_ts"]) < float(activation_ts):
                        selected_coverage["overlaps_activation"] += 1
                    else:
                        selected_coverage["post_activation"] += 1
                        if coverage_complete:
                            selected_coverage["post_activation_complete"] += 1
                        elif not subscription_coverage.get("intervals"):
                            selected_coverage["post_activation_no_interval"] += 1
                selected_coverage_ratios.append(
                    float(subscription_coverage.get("coverage_ratio") or 0.0)
                )
                priority_rejections = (
                    int(subscription_coverage.get("priority_reserve_rejections") or 0)
                    if priority_trace_available else 0
                )
                if priority_rejections:
                    selected_coverage["priority_reserve_exposed"] += 1
                    selected_coverage["priority_reserve_rejections"] += (
                        priority_rejections
                    )
                    priority_rejection_mints[str(row["mint"])] += 1
                    priority_rejection_events_by_mint[str(row["mint"])] += (
                        priority_rejections
                    )
                cap_rejections = int(
                    subscription_coverage.get("mint_cap_rejections") or 0
                )
                if cap_rejections:
                    selected_coverage["mint_cap_exposed"] += 1
                    selected_coverage["mint_cap_rejections"] += cap_rejections
                    mint_cap_mints[str(row["mint"])] += 1
                    mint_cap_events_by_mint[str(row["mint"])] += cap_rejections
                if not coverage_complete:
                    selected_coverage["incomplete"] += 1
                    if not subscription_coverage.get("intervals"):
                        selected_coverage["no_subscription_interval"] += 1
                    elif not subscription_coverage.get("subscription_continuous"):
                        selected_coverage["subscription_gap"] += 1
                    if subscription_coverage.get("known_delivery_failures"):
                        selected_coverage["known_delivery_loss"] += 1
            else:
                selected_coverage["unmeasured"] += 1
        if measurement_available:
            measured_coverage_rows.append(row)
            coverage_ratios.append(
                float(subscription_coverage.get("coverage_ratio") or 0.0)
            )
            if coverage_complete:
                complete_coverage_rows.append(row)
        if not row.get("event_path"):
            if coverage_complete:
                final_checkpoint = max(
                    row["path"],
                    key=lambda point: float(point["checkpoint_seconds"]),
                )
                complete_event_sequence_rows.append({
                    **row,
                    "path": [final_checkpoint],
                    "ambiguous": False,
                })
            continue
        event_covered_rows.append(row)
        event_point_counts.append(len(row["event_path"]))
        first_event_delays.append(
            min(float(point["elapsed_seconds"]) for point in row["event_path"])
        )
        event_sources.update(
            str(point.get("source") or "unknown")
            for point in row["event_path"]
        )
        event_points = [
            {
                "checkpoint_seconds": float(point["elapsed_seconds"]),
                "price_sol": float(point["price_sol"]),
            }
            for point in row["event_path"]
        ]
        merged_path = sorted(
            [*row["path"], *event_points],
            key=lambda point: float(point["checkpoint_seconds"]),
        )
        dense_rows.append({
            **row,
            "path": merged_path,
            "ambiguous": is_ambiguous(
                float(row["price_at_signal"]), merged_path
            ),
        })
        final_checkpoint = max(
            row["path"],
            key=lambda point: float(point["checkpoint_seconds"]),
        )
        event_sequence_path = sorted(
            [*event_points, final_checkpoint],
            key=lambda point: float(point["checkpoint_seconds"]),
        )
        event_sequence_row = {
            **row,
            "path": event_sequence_path,
            # Ordered market events are not sparse checkpoints. Coverage is
            # reported separately instead of pretending it is complete.
            "ambiguous": False,
        }
        event_sequence_rows.append(event_sequence_row)
        if coverage_complete:
            complete_event_sequence_rows.append(event_sequence_row)
    event_path_sensitivity = (
        analyse(
            dense_rows,
            threshold,
            "tp100_time",
            cost_per_side,
            TRAILING_DROP_FROM_PEAK,
            hold_seconds,
        )
        if dense_rows else None
    )
    fixed_path_covered_sensitivity = (
        analyse(
            event_covered_rows,
            threshold,
            "tp100_time",
            cost_per_side,
            TRAILING_DROP_FROM_PEAK,
            hold_seconds,
        )
        if event_covered_rows else None
    )
    event_sequence_sensitivity = (
        analyse(
            event_sequence_rows,
            threshold,
            "tp100_time",
            cost_per_side,
            TRAILING_DROP_FROM_PEAK,
            hold_seconds,
        )
        if event_sequence_rows else None
    )
    complete_coverage_sensitivity = (
        analyse(
            complete_event_sequence_rows,
            threshold,
            "tp100_time",
            cost_per_side,
            TRAILING_DROP_FROM_PEAK,
            hold_seconds,
        )
        if complete_coverage_rows else None
    )
    ordered = sorted(records, key=lambda row: float(row["net"]), reverse=True)
    without_best = ordered[1:]
    without_best_mean = (
        sum(float(row["net"]) for row in without_best) / len(without_best)
        if without_best else None
    )

    traders = sorted({str(row.get("trader") or "unknown") for row in rows})
    leave_one_trader_out = {}
    for trader in traders:
        subset = [row for row in rows if str(row.get("trader") or "unknown") != trader]
        result = analyse(
            subset,
            threshold,
            "tp100_time",
            cost_per_side,
            TRAILING_DROP_FROM_PEAK,
            hold_seconds,
        )
        if result is not None:
            leave_one_trader_out[trader] = {
                "positions": result["positions"],
                "net": result["net"],
                "mean": result["mean"],
            }

    ci_low, ci_high = overall["ci95"]
    blockers = []
    if ci_low <= 0:
        blockers.append("MEAN_CI95_DOES_NOT_EXCLUDE_ZERO")
    if without_best_mean is None or without_best_mean <= 0:
        blockers.append("LEAVE_BEST_OUT_NOT_POSITIVE")
    complete_selected = (
        int(complete_coverage_sensitivity["positions"])
        if complete_coverage_sensitivity else 0
    )
    if complete_selected < MIN_COMPLETE_COVERAGE_SELECTIONS:
        blockers.append(
            "COMPLETE_COVERAGE_SELECTIONS "
            f"{complete_selected}/{MIN_COMPLETE_COVERAGE_SELECTIONS}"
        )
    elif complete_coverage_sensitivity["ci95"][0] <= 0:
        blockers.append("COMPLETE_COVERAGE_MEAN_CI95_DOES_NOT_EXCLUDE_ZERO")

    trader_concentration = concentration(records, "trader")
    reached_tp100 = sum(record["stage"] == 3 for record in records)
    closed_by_time = sum(
        record["remaining"] <= 1e-9
        and record["stage"] < 3
        and not record["stop_hit"]
        for record in records
    )
    priority_trace_available = bool(
        selected_coverage["measured"]
        and selected_coverage["priority_trace_available_rows"]
        == selected_coverage["measured"]
    )
    mint_cap_trace_available = bool(
        selected_coverage["measured"]
        and selected_coverage["mint_cap_trace_rows"]
        == selected_coverage["measured"]
    )
    return {
        "ready_for_review": not blockers,
        "blockers": blockers,
        "policy": "tp100_time",
        "threshold": threshold,
        "cost_per_side": cost_per_side,
        "one_position_seconds": hold_seconds,
        "overall": overall,
        "ambiguous_share": overall["ambiguous"] / overall["positions"],
        "unambiguous_sensitivity": unambiguous_sensitivity,
        "mint_cap_exposure_sensitivity": {
            **cap_exposure_sensitivity,
            "selected_without_cap_trace": selected_without_cap_trace,
            "note": (
                "Cap exposure is observed after entry and cannot select "
                "trades. Observational checkpoint-path split, not a causal "
                "estimate; CI resamples signals, not mint clusters. "
                "Candidate gates are unchanged."
            ),
        },
        "event_path_sensitivity": {
            "rows_with_event_path": len(dense_rows),
            "selected_rows_with_event_path": (
                event_path_sensitivity["positions"]
                if event_path_sensitivity else 0
            ),
            "fixed_path_result": fixed_path_covered_sensitivity,
            "dense_path_result": event_path_sensitivity,
            "event_sequence_result": event_sequence_sensitivity,
            "coverage": {
                "sources": dict(event_sources.most_common()),
                "rows_measured": len(measured_coverage_rows),
                "rows_complete": len(complete_coverage_rows),
                "selected_rows_complete": (
                    complete_selected
                ),
                "selected_rows_measured": selected_coverage["measured"],
                "selected_rows_incomplete": selected_coverage["incomplete"],
                "selected_rows_unmeasured": selected_coverage["unmeasured"],
                "selected_rows_overlapping_activation": (
                    selected_coverage["overlaps_activation"]
                ),
                "selected_rows_post_activation": (
                    selected_coverage["post_activation"]
                ),
                "selected_rows_post_activation_complete": (
                    selected_coverage["post_activation_complete"]
                ),
                "selected_rows_post_activation_no_interval": (
                    selected_coverage["post_activation_no_interval"]
                ),
                "selected_rows_known_delivery_loss": (
                    selected_coverage["known_delivery_loss"]
                ),
                "selected_priority_reserve_trace_available": (
                    priority_trace_available
                ),
                "selected_priority_reserve_trace_rows": (
                    selected_coverage["priority_trace_available_rows"]
                ),
                "selected_rows_priority_reserve_exposed": (
                    selected_coverage["priority_reserve_exposed"]
                    if priority_trace_available else None
                ),
                "selected_priority_reserve_rejections": (
                    selected_coverage["priority_reserve_rejections"]
                    if priority_trace_available else None
                ),
                "selected_priority_reserve_exposed_by_mint": (
                    dict(priority_rejection_mints.most_common())
                    if priority_trace_available else None
                ),
                "selected_priority_reserve_rejections_by_mint": (
                    dict(priority_rejection_events_by_mint.most_common())
                    if priority_trace_available else None
                ),
                "selected_mint_cap_trace_rows": (
                    selected_coverage["mint_cap_trace_rows"]
                ),
                "selected_rows_mint_cap_exposed": (
                    selected_coverage["mint_cap_exposed"]
                    if mint_cap_trace_available else None
                ),
                "selected_mint_cap_rejections": (
                    selected_coverage["mint_cap_rejections"]
                    if mint_cap_trace_available else None
                ),
                "selected_mint_cap_exposed_by_mint": (
                    dict(mint_cap_mints.most_common())
                    if mint_cap_trace_available else None
                ),
                "selected_mint_cap_rejections_by_mint": (
                    dict(mint_cap_events_by_mint.most_common())
                    if mint_cap_trace_available else None
                ),
                "selected_rows_no_subscription_interval": (
                    selected_coverage["no_subscription_interval"]
                ),
                "selected_rows_subscription_gap": (
                    selected_coverage["subscription_gap"]
                ),
                "selected_median_coverage_ratio": (
                    statistics.median(selected_coverage_ratios)
                    if selected_coverage_ratios else None
                ),
                "minimum_selected_for_review": (
                    MIN_COMPLETE_COVERAGE_SELECTIONS
                ),
                "complete_event_sequence_result": (
                    complete_coverage_sensitivity
                ),
                "median_subscription_coverage_ratio": (
                    statistics.median(coverage_ratios)
                    if coverage_ratios else None
                ),
                "median_events_per_row": (
                    statistics.median(event_point_counts)
                    if event_point_counts else None
                ),
                "median_first_event_seconds": (
                    statistics.median(first_event_delays)
                    if first_event_delays else None
                ),
                "p90_first_event_seconds": (
                    float(np.percentile(first_event_delays, 90))
                    if first_event_delays else None
                ),
                "completeness_proven": bool(
                    rows
                    and len(complete_coverage_rows) == len(rows)
                ),
            },
        },
        "without_best_mean": without_best_mean,
        "reached_tp100": reached_tp100,
        "reached_tp100_share": reached_tp100 / len(records),
        "closed_by_time": closed_by_time,
        "closed_by_time_share": closed_by_time / len(records),
        "trader_concentration": trader_concentration,
        "mint_concentration": concentration(records, "mint"),
        "leave_one_trader_out": leave_one_trader_out,
        "positive_traders": trader_concentration["positive_groups"],
        "selected_traders": trader_concentration["groups"],
        "ci95_excludes_zero_above": ci_low > 0,
        "ci95": [ci_low, ci_high],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--frozen-dataset", default="exports/account_checkpoint_live.json"
    )
    parser.add_argument("--frozen-sha256", default=DEFAULT_FROZEN_SHA256)
    parser.add_argument(
        "--schema", default="training/schema_account_checkpoints_v1.json"
    )
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--cost-per-side", type=float, default=DEFAULT_COST_PER_SIDE)
    parser.add_argument("--one-position-seconds", type=float, default=DEFAULT_HOLD_SECONDS)
    parser.add_argument("--subscription-trace", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    app_token = os.getenv("APP_TOKEN", "")
    if not app_token:
        raise SystemExit("APP_TOKEN is missing from .env")

    schema = load_json(args.schema)
    frozen_sha256 = verify_file_sha256(
        args.frozen_dataset, args.frozen_sha256
    )
    frozen_payload = load_json(args.frozen_dataset)
    frozen_rows = validate_dataset(frozen_payload, schema)
    cutoff_ts = parse_cutoff(args.cutoff)
    frozen_signal_ids = validate_frozen_population(frozen_rows, cutoff_ts)
    pipeline = fit_frozen_pipeline(frozen_rows, schema)

    dataset_payload = fetch_json(
        args.base_url, "/api/account-checkpoint-training-dataset", app_token
    )
    path_payload = fetch_json(
        args.base_url,
        (
            "/api/account-checkpoint-paths"
            f"?after_signal_ts={cutoff_ts}&include_event_path=true"
            + ("&include_subscription_trace=true" if args.subscription_trace else "")
        ),
        app_token,
    )
    if args.subscription_trace and path_payload.get("subscription_trace_included") is not True:
        raise SystemExit("Server does not support subscription traces yet")
    current_rows = validate_dataset(dataset_payload, schema)
    rows, coverage = build_prospective_rows(
        current_rows,
        path_payload.get("rows") or [],
        pipeline,
        schema,
        cutoff_ts,
        frozen_signal_ids,
    )
    report = {
        "protocol": {
            "specification_commit": (
                "dc198e0814f14ef43ccb95a5a51e76a42e91d54b"
            ),
            "frozen_rows": len(frozen_rows),
            "frozen_dataset_sha256": frozen_sha256,
            "dataset_fingerprint_recorded_with_validator": True,
            "cutoff": args.cutoff,
            "threshold": args.threshold,
            "policy": "tp100_time",
            "cost_per_side": args.cost_per_side,
            "one_position_seconds": args.one_position_seconds,
            "writes_production": False,
            "promotes_model": False,
        },
        "coverage": {
            **coverage,
            "usable_rows": len(rows),
            "unique_mints": len({row.get("mint") for row in rows}),
            "traders": dict(Counter(row.get("trader") for row in rows)),
        },
        "result": prospective_report(
            rows,
            threshold=args.threshold,
            cost_per_side=args.cost_per_side,
            hold_seconds=args.one_position_seconds,
        ),
    }
    if args.subscription_trace:
        report["selected_missing_subscription_trace"] = (
            selected_subscription_gap_diagnostics(rows, args.threshold)
        )
        report["selected_coverage_attribution"] = (
            selected_coverage_attribution(rows, args.threshold)
        )
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
