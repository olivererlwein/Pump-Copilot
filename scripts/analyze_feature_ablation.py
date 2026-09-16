import argparse
import copy
import json
from collections import Counter

if __package__:
    from scripts.compare_model_economics import (
        evaluate_strategy,
        simulate_payoff,
    )
    from scripts.train_baseline_model import (
        load_json,
        validate_dataset,
    )
else:
    from compare_model_economics import evaluate_strategy, simulate_payoff
    from train_baseline_model import load_json, validate_dataset


ABLATIONS = {
    "all_features": set(),
    "without_legacy_scores": {
        "trader_score",
        "timing_score",
        "size_score",
        "token_score",
        "consensus_score",
        "market_score",
        "score_total",
    },
    "only_size_score_from_legacy": {
        "trader_score",
        "timing_score",
        "token_score",
        "consensus_score",
        "market_score",
        "score_total",
    },
    "without_score_total": {"score_total"},
    "without_trader_score": {"trader_score"},
    "without_timing_score": {"timing_score"},
    "without_size_score": {"size_score"},
    "without_token_score": {"token_score"},
    "without_consensus_score": {"consensus_score"},
    "without_market_score": {"market_score"},
    "without_trader_identity": {"trader"},
    "without_raw_trade_size": {"sol_amount", "buy_size_pct_mc"},
    "without_consensus_evidence": {
        "consensus_score",
        "consensus_trader_count_30s",
        "consensus_trader_count",
    },
    "without_market_context": {
        "market_score",
        "market_cap",
        "token_age_seconds",
    },
    # Aislada a propósito: `without_market_context` quita tres features a la
    # vez, así que no permite atribuir nada a la antigüedad del token. Importa
    # medirla sola porque su ausencia no es aleatoria — está presente casi solo
    # para el trader que crea los tokens que opera, y por eso el nulo puede
    # estar funcionando como proxy de identidad en vez de como dato faltante.
    "without_token_age": {"token_age_seconds"},
    # Si el rendimiento se derrumba sin identidad, el modelo explota
    # principalmente quién opera y no patrones transferibles. La variante
    # combinada separa eso del posible efecto residual de la antigüedad.
    "without_trader_identity_and_token_age": {
        "trader",
        "token_age_seconds",
    },
}


def schema_without_features(schema, excluded_features):
    reduced = copy.deepcopy(schema)
    reduced["categorical_features"] = [
        feature
        for feature in schema["categorical_features"]
        if feature not in excluded_features
    ]
    reduced["numeric_features"] = [
        feature
        for feature in schema["numeric_features"]
        if feature not in excluded_features
    ]
    remaining = (
        reduced["categorical_features"] + reduced["numeric_features"]
    )
    if not remaining:
        raise ValueError("An ablation must retain at least one feature")

    reduced["nullable_features"] = [
        feature
        for feature in schema.get("nullable_features", [])
        if feature in remaining
    ]
    return reduced


def selection_diagnostics(rows, predictions):
    if len(rows) != len(predictions):
        raise ValueError("Rows and predictions must have equal length")

    holdout_mints = Counter(row["mint"] for row in rows)
    selected_rows = [
        row for row, prediction in zip(rows, predictions)
        if int(prediction) == 1
    ]
    selected_mints = Counter(row["mint"] for row in selected_rows)
    selected_traders = Counter(row["trader"] for row in selected_rows)
    return {
        "holdout_rows": len(rows),
        "holdout_mints": len(holdout_mints),
        "largest_holdout_mint_share": (
            round(max(holdout_mints.values()) / len(rows), 6)
            if rows else None
        ),
        "selected": len(selected_rows),
        "selected_mints": len(selected_mints),
        "largest_selected_mint_share": (
            round(max(selected_mints.values()) / len(selected_rows), 6)
            if selected_rows else None
        ),
        "selected_traders": len(selected_traders),
        "largest_selected_trader_share": (
            round(max(selected_traders.values()) / len(selected_rows), 6)
            if selected_rows else None
        ),
        "selected_by_trader": dict(sorted(selected_traders.items())),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="exports/training_dataset_v2.json",
    )
    parser.add_argument(
        "--schema",
        default="training/schema_v2.json",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--round-trip-cost", type=float, default=0.02)
    parser.add_argument(
        "--ablations",
        nargs="+",
        choices=tuple(ABLATIONS),
    )
    args = parser.parse_args()

    schema = load_json(args.schema)
    rows = validate_dataset(load_json(args.dataset), schema)
    reports = []

    selected_ablations = args.ablations or list(ABLATIONS)
    for name in selected_ablations:
        excluded_features = ABLATIONS[name]
        reduced_schema = schema_without_features(schema, excluded_features)
        result = evaluate_strategy(
            rows,
            reduced_schema,
            args.test_fraction,
            "inverse-group",
        )
        all_selected = simulate_payoff(
            result["rows"],
            result["predictions"],
            schema["target"],
            round_trip_cost=args.round_trip_cost,
        )
        one_position = simulate_payoff(
            result["rows"],
            result["predictions"],
            schema["target"],
            round_trip_cost=args.round_trip_cost,
            hold_seconds=900,
        )
        reports.append({
            "name": name,
            "excluded_features": sorted(excluded_features),
            "feature_count": len(
                reduced_schema["categorical_features"]
                + reduced_schema["numeric_features"]
            ),
            "threshold": result["threshold"],
            "metrics": result["metrics"],
            "all_selected": all_selected,
            "one_position_15m": one_position,
            "selection_diagnostics": selection_diagnostics(
                result["rows"], result["predictions"]
            ),
            "deployment_blockers": result["deployment_blockers"],
        })

    print(json.dumps({
        "rows": len(rows),
        "strategy": "inverse-group",
        "test_fraction": args.test_fraction,
        "round_trip_cost": args.round_trip_cost,
        "ablations": reports,
        "note": (
            "The payoff assigns +25% to every positive label and -10% to "
            "every negative label, then subtracts a fixed round-trip cost. "
            "It is not realized PnL and does not include slippage or "
            "execution failures. No model is promoted by this report."
        ),
    }, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
