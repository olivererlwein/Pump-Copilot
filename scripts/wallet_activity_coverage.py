"""Does one wallet's coverage worsen as its activity rises?

Read-only. Reads /api/wallet-coverage-buckets and the frozen validator's
prospective rows; writes nothing to production and changes no policy.

Activity rule, fixed before looking at any outcome:
- Buckets are fixed UTC-aligned windows (default one hour).
- Activity = Pump-log notifications that mention the wallet, counted when the
  WSS delivers them, before the fetch queue. It is measured upstream of the
  capacity loss being studied. Tiering on observed own operations would be
  circular: lower coverage means fewer observed operations, so a badly covered
  busy hour would be classified as quiet.
- Excluded from activity tiers: buckets with no WSS traffic, muted buckets,
  and buckets predating reliable mute history.
- Buckets with zero notifications are `idle`. The rest are split into
  terciles by (notifications, start time): low, mid, high.

The per-tier outcome of selections is descriptive and not admissible for any
decision (CLAUDE_NOTES.md, 2026-09-28).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from urllib.parse import urlencode

from dotenv import load_dotenv

if __package__:
    from scripts import validate_frozen_exit_candidate as frozen
else:
    import validate_frozen_exit_candidate as frozen


DEFAULT_SINCE = "2026-09-28T02:29:26+00:00"
TIERS = ("muted", "unknown", "wss_silent", "idle", "low", "mid", "high")
LOST = ("queue_full", "fetch_failed", "processing_failed", "not_selected")


def assign_tiers(buckets: list[dict]) -> dict[int, str]:
    tiers = {}
    active = []
    for row in buckets:
        if row.get("muted") is True:
            tiers[row["start_ts"]] = "muted"
        elif row.get("muted") is None:
            tiers[row["start_ts"]] = "unknown"
        elif row["all_wallets_pump_notifications"] == 0:
            tiers[row["start_ts"]] = "wss_silent"
        elif row["pump_notifications"] == 0:
            tiers[row["start_ts"]] = "idle"
        else:
            active.append(row)
    active.sort(key=lambda row: (row["pump_notifications"], row["start_ts"]))
    for index, row in enumerate(active):
        tiers[row["start_ts"]] = ("low", "mid", "high")[index * 3 // len(active)]
    return tiers


def ratio(part, whole):
    return part / whole if whole else None


def mark_known_mute(buckets, mute_start, mute_end, bucket_seconds):
    for row in buckets:
        start = row["start_ts"]
        if start < mute_end and start + bucket_seconds > mute_start:
            row["muted"] = True


def summarize(
    buckets: list[dict],
    rows: list[dict],
    trader: str,
    bucket_seconds: int,
    threshold: float = frozen.DEFAULT_THRESHOLD,
    cost_per_side: float = frozen.DEFAULT_COST_PER_SIDE,
) -> dict:
    tiers = assign_tiers(buckets)
    selected = [
        row for row in rows
        if row.get("trader") == trader
        and float(row["probability"]) >= threshold
    ]
    outcomes = {
        row["signal_id"]: row["net"]
        for row in frozen.selected_records(selected, threshold, cost_per_side)
    }
    totals = {tier: Counter() for tier in TIERS}
    nets = {tier: [] for tier in TIERS}
    for row in buckets:
        tier = tiers[row["start_ts"]]
        total = totals[tier]
        total["buckets"] += 1
        for key in (
            "pump_notifications", "own_parsed_transactions",
            "mention_transactions", "other_unparsed_transactions",
        ):
            total[key] += row[key]
        for status, count in row["unattributable"].items():
            total[f"unattributable_{status}"] += count
        for transport, count in row["trades_by_transport"].items():
            total[f"own_trades_{transport}"] += count
        for status, count in row["fallback_events"].items():
            total[f"fallback_{status}"] += count
        total["fallback_rebase_buckets"] += int(row["fallback_rebase_in_bucket"])
        end = row["start_ts"] + bucket_seconds
        for signal in selected:
            if not row["start_ts"] <= float(signal["signal_ts"]) < end:
                continue
            coverage = signal.get("subscription_coverage") or {}
            total["selections"] += 1
            nets[tier].append(outcomes[signal["signal_id"]])
            if coverage.get("measurement_available") is not True:
                continue
            total["measured"] += 1
            if coverage.get("complete") is True:
                total["complete"] += 1
            elif not coverage.get("intervals"):
                total["no_subscription_interval"] += 1
            elif not coverage.get("subscription_continuous"):
                total["subscription_gap"] += 1
            if coverage.get("known_delivery_failures"):
                total["known_delivery_loss"] += 1

    report = {}
    for tier in TIERS:
        total = totals[tier]
        if not total["buckets"]:
            continue
        lost = sum(total[f"unattributable_{status}"] for status in LOST)
        attributed = (
            total["own_parsed_transactions"] + total["mention_transactions"]
        )
        report[tier] = {
            **dict(total),
            "delivery_loss_ratio": ratio(lost, total["pump_notifications"]),
            "mention_share_of_attributed": ratio(
                total["mention_transactions"], attributed
            ),
            "fallback_missing_ratio": ratio(
                total["fallback_missing"],
                total["fallback_missing"] + total["fallback_matched"],
            ),
            "descriptive_outcome_not_admissible": {
                "n": len(nets[tier]),
                "mean_net": statistics.mean(nets[tier]) if nets[tier] else None,
                "median_net": (
                    statistics.median(nets[tier]) if nets[tier] else None
                ),
            },
        }
    return {
        "trader": trader,
        "bucket_seconds": bucket_seconds,
        "tier_rule": "terciles of wallet Pump notifications per bucket",
        "bucket_tiers": {str(start): tier for start, tier in tiers.items()},
        "tiers": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--trader", default="slingoor")
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--until")
    parser.add_argument("--bucket-seconds", type=int, default=3600)
    parser.add_argument("--base-url", default=frozen.DEFAULT_BASE_URL)
    parser.add_argument(
        "--frozen-dataset", default="exports/account_checkpoint_live.json"
    )
    parser.add_argument("--frozen-sha256", default=frozen.DEFAULT_FROZEN_SHA256)
    parser.add_argument(
        "--schema", default="training/schema_account_checkpoints_v1.json"
    )
    parser.add_argument("--cutoff", default=frozen.DEFAULT_CUTOFF)
    parser.add_argument("--known-muted-from")
    parser.add_argument("--known-muted-through")
    args = parser.parse_args()

    if bool(args.known_muted_from) != bool(args.known_muted_through):
        parser.error("both --known-muted-from and --known-muted-through are required")

    load_dotenv()
    app_token = os.getenv("APP_TOKEN", "")
    if not app_token:
        raise SystemExit("APP_TOKEN is missing from .env")
    since = frozen.parse_cutoff(args.since)
    until = frozen.parse_cutoff(args.until) if args.until else since + 86400

    query = urlencode({
        "trader": args.trader, "since": since, "until": until,
        "bucket_seconds": args.bucket_seconds,
    })
    coverage = frozen.fetch_json(
        args.base_url, f"/api/wallet-coverage-buckets?{query}", app_token
    )
    known_mute = None
    if args.known_muted_from:
        mute_start = frozen.parse_cutoff(args.known_muted_from)
        mute_end = frozen.parse_cutoff(args.known_muted_through)
        if not mute_start < mute_end:
            parser.error("known mute end must be after its start")
        known_mute = {"from_ts": mute_start, "observed_through_ts": mute_end}
        mark_known_mute(
            coverage["buckets"], mute_start, mute_end, args.bucket_seconds
        )

    schema = frozen.load_json(args.schema)
    frozen.verify_file_sha256(args.frozen_dataset, args.frozen_sha256)
    frozen_rows = frozen.validate_dataset(
        frozen.load_json(args.frozen_dataset), schema
    )
    cutoff_ts = frozen.parse_cutoff(args.cutoff)
    frozen_ids = frozen.validate_frozen_population(frozen_rows, cutoff_ts)
    pipeline = frozen.fit_frozen_pipeline(frozen_rows, schema)
    dataset = frozen.fetch_json(
        args.base_url, "/api/account-checkpoint-training-dataset", app_token
    )
    paths = frozen.fetch_json(
        args.base_url,
        # Subscription coverage only travels with the event path.
        f"/api/account-checkpoint-paths?after_signal_ts={cutoff_ts}"
        "&include_event_path=true",
        app_token,
    )
    rows, _ = frozen.build_prospective_rows(
        frozen.validate_dataset(dataset, schema),
        paths.get("rows") or [],
        pipeline,
        schema,
        cutoff_ts,
        frozen_ids,
    )
    report = summarize(
        coverage["buckets"], rows, args.trader, args.bucket_seconds
    )
    report["muted"] = coverage.get("muted")
    report["non_pump_notifications_since_process_start"] = coverage.get(
        "non_pump_notifications_since_process_start"
    )
    report["process_started_ts"] = coverage.get("process_started_ts")
    report["mute_history_start_ts"] = coverage.get("mute_history_start_ts")
    report["known_mute_override"] = known_mute
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
