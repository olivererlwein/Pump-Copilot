import argparse
import json

import numpy as np

PUMPPORTAL_LIGHTNING_FEE_PER_SIDE = 0.01
PUMPPORTAL_LIGHTNING_ROUND_TRIP_COST = (
    PUMPPORTAL_LIGHTNING_FEE_PER_SIDE * 2
)

if __package__:
    from scripts.train_baseline_model import (
        build_matrix,
        build_pipeline,
        classification_metrics,
        fit_pipeline,
        get_deployment_blockers,
        load_json,
        select_threshold,
        strategy_sample_weights,
        temporal_group_split,
        temporal_group_validation_folds,
        validate_dataset,
    )
else:
    from train_baseline_model import (
        build_matrix,
        build_pipeline,
        classification_metrics,
        fit_pipeline,
        get_deployment_blockers,
        load_json,
        select_threshold,
        strategy_sample_weights,
        temporal_group_split,
        temporal_group_validation_folds,
        validate_dataset,
    )


def simulate_payoff(
    rows,
    predictions,
    target_column,
    round_trip_cost=PUMPPORTAL_LIGHTNING_ROUND_TRIP_COST,
    hold_seconds=0,
):
    if len(rows) != len(predictions):
        raise ValueError("Rows and predictions must have equal length")

    selected = sum(int(value) == 1 for value in predictions)
    available_ts = float("-inf")
    executed_targets = []
    skipped_busy = 0

    indexed = sorted(
        zip(rows, predictions),
        key=lambda item: float(item[0]["signal_ts"]),
    )
    for row, prediction in indexed:
        if int(prediction) != 1:
            continue

        signal_ts = float(row["signal_ts"])
        if signal_ts < available_ts:
            skipped_busy += 1
            continue

        executed_targets.append(int(row[target_column]))
        available_ts = signal_ts + float(hold_seconds)

    wins = sum(executed_targets)
    losses = len(executed_targets) - wins
    gross_return = wins * 0.25 - losses * 0.10
    net_return = gross_return - len(executed_targets) * round_trip_cost

    return {
        "selected": selected,
        "executed": len(executed_targets),
        "skipped_busy": skipped_busy,
        "wins": wins,
        "losses": losses,
        "precision": round(wins / len(executed_targets), 6)
        if executed_targets
        else None,
        "gross_return_units": round(gross_return, 6),
        "net_return_units": round(net_return, 6),
        "average_net_return": round(
            net_return / len(executed_targets),
            6,
        )
        if executed_targets
        else None,
    }


def evaluate_strategy(rows, schema, test_fraction, sample_weighting):
    train_rows, test_rows, purged_rows, _ = temporal_group_split(
        rows,
        schema,
        test_fraction,
    )
    target = schema["target"]
    tuning_rows = []
    tuning_targets = []
    tuning_probabilities = []

    for fold in temporal_group_validation_folds(train_rows, schema):
        train_matrix, _ = build_matrix(fold["train_rows"], schema)
        validation_matrix, _ = build_matrix(
            fold["validation_rows"],
            schema,
        )
        train_targets = np.asarray([
            row[target] for row in fold["train_rows"]
        ])
        pipeline = fit_pipeline(
            build_pipeline(schema),
            train_matrix,
            train_targets,
            fold["train_rows"],
            schema,
            sample_weighting,
        )
        probabilities = pipeline.predict_proba(validation_matrix)[:, 1]
        tuning_rows.extend(fold["validation_rows"])
        tuning_targets.extend(row[target] for row in fold["validation_rows"])
        tuning_probabilities.extend(probabilities)

    tuning_targets = np.asarray(tuning_targets)
    tuning_probabilities = np.asarray(tuning_probabilities)
    threshold = select_threshold(
        tuning_targets,
        tuning_probabilities,
        sample_weight=strategy_sample_weights(
            tuning_rows,
            schema,
            sample_weighting,
        ),
    )

    train_matrix, _ = build_matrix(train_rows, schema)
    test_matrix, _ = build_matrix(test_rows, schema)
    train_targets = np.asarray([row[target] for row in train_rows])
    test_targets = np.asarray([row[target] for row in test_rows])
    pipeline = fit_pipeline(
        build_pipeline(schema),
        train_matrix,
        train_targets,
        train_rows,
        schema,
        sample_weighting,
    )
    probabilities = pipeline.predict_proba(test_matrix)[:, 1]
    predictions = (probabilities >= threshold).astype(int)

    return {
        "strategy": sample_weighting,
        "threshold": threshold,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "purged_rows": len(purged_rows),
        "deployment_blockers": get_deployment_blockers(test_rows, schema),
        "metrics": classification_metrics(
            test_targets,
            predictions,
            probabilities,
        ),
        "rows": test_rows,
        "predictions": predictions,
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
    parser.add_argument(
        "--costs",
        type=float,
        nargs="+",
        default=(0.02, 0.03, 0.05),
    )
    args = parser.parse_args()

    schema = load_json(args.schema)
    rows = validate_dataset(load_json(args.dataset), schema)
    reports = []

    for strategy in ("none", "inverse-group"):
        result = evaluate_strategy(
            rows,
            schema,
            args.test_fraction,
            strategy,
        )
        scenarios = []
        for cost in args.costs:
            for name, hold_seconds in (
                ("all_selected", 0),
                ("one_position_15m", 900),
            ):
                scenarios.append({
                    "execution": name,
                    "round_trip_cost": cost,
                    **simulate_payoff(
                        result["rows"],
                        result["predictions"],
                        schema["target"],
                        round_trip_cost=cost,
                        hold_seconds=hold_seconds,
                    ),
                })

        reports.append({
            key: value
            for key, value in result.items()
            if key not in {"rows", "predictions"}
        } | {"payoff_proxy": scenarios})

    print(json.dumps({
        "assumptions": {
            "win_return": 0.25,
            "loss_return": -0.10,
            "pumpportal_lightning_fee_per_side": (
                PUMPPORTAL_LIGHTNING_FEE_PER_SIDE
            ),
            "minimum_round_trip_cost": (
                PUMPPORTAL_LIGHTNING_ROUND_TRIP_COST
            ),
            "position_hold_seconds": 900,
            "note": (
                "Conservative label-based proxy. Costs above 2% model "
                "additional slippage and network fees; this is not "
                "realized trading PnL."
            ),
        },
        "rows": len(rows),
        "test_fraction": args.test_fraction,
        "strategies": reports,
    }, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
