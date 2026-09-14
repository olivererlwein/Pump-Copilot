import argparse
import collections
import json
import math
import os
from urllib.request import Request, urlopen

from dotenv import load_dotenv

if __package__:
    from scripts.compare_model_economics import simulate_payoff
else:
    from compare_model_economics import simulate_payoff


DEFAULT_BASE_URL = "https://web-production-4ea0d.up.railway.app"


def pair_completed_predictions(
    rows,
    incumbent_version,
    challenger_version,
):
    by_evaluation = {}
    for row in rows:
        version = str(row.get("model_version") or "")
        if version not in {incumbent_version, challenger_version}:
            continue
        evaluation_id = int(row["evaluation_id"])
        by_evaluation.setdefault(evaluation_id, {})[version] = row

    paired = []
    for evaluation_id, versions in by_evaluation.items():
        if incumbent_version not in versions or challenger_version not in versions:
            continue
        incumbent = versions[incumbent_version]
        challenger = versions[challenger_version]
        incumbent_actual = incumbent.get("actual_target")
        challenger_actual = challenger.get("actual_target")
        if incumbent_actual is None or challenger_actual is None:
            continue
        if int(incumbent_actual) != int(challenger_actual):
            raise ValueError(
                f"Models disagree about actual target for {evaluation_id}"
            )
        paired.append({
            "evaluation_id": evaluation_id,
            "created_ts": float(incumbent["created_ts"]),
            "trader": str(incumbent.get("trader") or "unknown"),
            "mint": str(incumbent.get("mint") or ""),
            "actual_target": int(incumbent_actual),
            "predictions": {
                incumbent_version: int(incumbent["predicted_target"]),
                challenger_version: int(challenger["predicted_target"]),
            },
        })

    return sorted(paired, key=lambda row: row["created_ts"])


def _payoff(pairs, version, round_trip_cost, hold_seconds):
    rows = [
        {
            "signal_ts": row["created_ts"],
            "target": row["actual_target"],
        }
        for row in pairs
    ]
    predictions = [row["predictions"][version] for row in pairs]
    return simulate_payoff(
        rows,
        predictions,
        "target",
        round_trip_cost=round_trip_cost,
        hold_seconds=hold_seconds,
    )


def _temporal_windows(pairs, version, count, round_trip_cost):
    windows = []
    for index in range(count):
        start = math.floor(index * len(pairs) / count)
        end = math.floor((index + 1) * len(pairs) / count)
        rows = pairs[start:end]
        economics = _payoff(
            rows,
            version,
            round_trip_cost,
            hold_seconds=900,
        )
        windows.append({
            "window": index + 1,
            "paired_rows": len(rows),
            **economics,
        })
    return windows


def _audit_model(
    pairs,
    version,
    round_trip_cost,
    maximum_trader_share,
    minimum_selected_traders,
    temporal_windows,
    minimum_positive_windows,
    minimum_completed_pairs,
):
    selected = [row for row in pairs if row["predictions"][version] == 1]
    trader_counts = collections.Counter(row["trader"] for row in selected)
    mint_counts = collections.Counter(row["mint"] for row in selected)
    largest_trader_share = (
        max(trader_counts.values()) / len(selected) if selected else 0.0
    )
    largest_mint_share = (
        max(mint_counts.values()) / len(selected) if selected else 0.0
    )
    windows = _temporal_windows(
        pairs,
        version,
        temporal_windows,
        round_trip_cost,
    )
    positive_windows = sum(
        (window["net_return_units"] or 0) > 0 for window in windows
    )
    economics = _payoff(
        pairs,
        version,
        round_trip_cost,
        hold_seconds=900,
    )

    blockers = []
    if len(pairs) < minimum_completed_pairs:
        blockers.append(
            f"paired_completed {len(pairs)}/{minimum_completed_pairs}"
        )
    if economics["net_return_units"] <= 0:
        blockers.append("non_positive_net_return")
    if len(trader_counts) < minimum_selected_traders:
        blockers.append(
            f"selected_traders {len(trader_counts)}/{minimum_selected_traders}"
        )
    if largest_trader_share > maximum_trader_share:
        blockers.append(
            "largest_trader_share "
            f"{largest_trader_share:.3f}/{maximum_trader_share:.3f}"
        )
    if positive_windows < minimum_positive_windows:
        blockers.append(
            f"positive_windows {positive_windows}/{minimum_positive_windows}"
        )

    return {
        "eligible_for_canary": not blockers,
        "blockers": blockers,
        "selected_traders": len(trader_counts),
        "trader_counts": dict(trader_counts.most_common()),
        "largest_trader_share": round(largest_trader_share, 6),
        "selected_mints": len(mint_counts),
        "largest_mint_share": round(largest_mint_share, 6),
        "economics": economics,
        "all_selected_economics": _payoff(
            pairs,
            version,
            round_trip_cost,
            hold_seconds=0,
        ),
        "positive_temporal_windows": positive_windows,
        "temporal_windows": windows,
    }


def audit_shadow_models(
    rows,
    incumbent_version,
    challenger_version,
    round_trip_cost=0.05,
    minimum_completed_pairs=100,
    maximum_trader_share=0.5,
    minimum_selected_traders=3,
    temporal_windows=4,
    minimum_positive_windows=3,
):
    pairs = pair_completed_predictions(
        rows,
        incumbent_version,
        challenger_version,
    )
    settings = {
        "round_trip_cost": round_trip_cost,
        "minimum_completed_pairs": minimum_completed_pairs,
        "maximum_trader_share": maximum_trader_share,
        "minimum_selected_traders": minimum_selected_traders,
        "temporal_windows": temporal_windows,
        "minimum_positive_windows": minimum_positive_windows,
        "position_hold_seconds": 900,
        "win_return": 0.25,
        "loss_return": -0.10,
    }
    return {
        "paired_completed": len(pairs),
        "incumbent_version": incumbent_version,
        "challenger_version": challenger_version,
        "acceptance_criteria": settings,
        "models": {
            version: _audit_model(pairs, version, **{
                "round_trip_cost": round_trip_cost,
                "maximum_trader_share": maximum_trader_share,
                "minimum_selected_traders": minimum_selected_traders,
                "temporal_windows": temporal_windows,
                "minimum_positive_windows": minimum_positive_windows,
                "minimum_completed_pairs": minimum_completed_pairs,
            })
            for version in (incumbent_version, challenger_version)
        },
        "note": (
            "Label-based payoff proxy, not realized PnL. Eligibility only "
            "permits a controlled canary review; it never enables trading."
        ),
    }


def _fetch_json(base_url, path, app_token):
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"x-app-token": app_token},
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--round-trip-cost", type=float, default=0.05)
    args = parser.parse_args()

    load_dotenv()
    app_token = os.getenv("APP_TOKEN", "")
    if not app_token:
        raise SystemExit("APP_TOKEN is missing from .env")

    stats = _fetch_json(args.base_url, "/api/shadow-stats", app_token)
    incumbent = stats.get("model_version")
    challenger = stats.get("challenger_model_version")
    if not incumbent or not challenger:
        raise SystemExit("Both incumbent and challenger must be loaded")

    safe_limit = max(1, min(args.limit, 1000))
    payload = _fetch_json(
        args.base_url,
        f"/api/shadow-predictions?limit={safe_limit}",
        app_token,
    )
    report = audit_shadow_models(
        payload.get("rows", []),
        incumbent,
        challenger,
        round_trip_cost=args.round_trip_cost,
    )
    comparison = stats.get("comparison") or {}
    report["production_comparison_completed"] = comparison.get("completed")
    report["api_rows_returned"] = payload.get("count", 0)
    report["coverage_limited"] = (
        int(comparison.get("completed") or 0) > report["paired_completed"]
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
