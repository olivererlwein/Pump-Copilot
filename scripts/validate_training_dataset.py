import argparse
import json
from collections import Counter
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    args = parser.parse_args()

    payload = load_json(args.dataset)
    schema = load_json(args.schema)
    rows = payload.get("rows", [])

    metadata = schema["metadata_columns"]
    categorical = schema["categorical_features"]
    numeric = schema["numeric_features"]
    target = schema["target"]
    model_features = categorical + numeric
    required_columns = metadata + model_features + [target]

    available_columns = set()
    for row in rows:
        available_columns.update(row.keys())

    missing_columns = sorted(
        set(required_columns) - available_columns
    )

    duplicate_signal_ids = [
        signal_id
        for signal_id, count in Counter(
            row.get("signal_id") for row in rows
        ).items()
        if signal_id is not None and count > 1
    ]

    invalid_targets = sum(
        1 for row in rows
        if row.get(target) not in (0, 1)
    )

    null_counts = {
        feature: sum(
            1 for row in rows
            if row.get(feature) is None
        )
        for feature in model_features
    }

    nullable_features = set(
        schema.get("nullable_features", [])
    )

    unexpected_null_counts = {
        feature: count
        for feature, count in null_counts.items()
        if count > 0 and feature not in nullable_features
    }

    targets = Counter(row.get(target) for row in rows)
    traders = Counter(row.get("trader") for row in rows)
    unique_mints = len({
        row.get("mint")
        for row in rows
        if row.get("mint")
    })

    readiness = schema["readiness"]
    blockers = []

    if len(rows) < readiness["minimum_completed_rows"]:
        blockers.append(
            f"rows {len(rows)}/{readiness['minimum_completed_rows']}"
        )

    if targets.get(0, 0) < readiness["minimum_target_0"]:
        blockers.append(
            f"target_0 {targets.get(0, 0)}/"
            f"{readiness['minimum_target_0']}"
        )

    if targets.get(1, 0) < readiness["minimum_target_1"]:
        blockers.append(
            f"target_1 {targets.get(1, 0)}/"
            f"{readiness['minimum_target_1']}"
        )

    if len(traders) < readiness["minimum_unique_traders"]:
        blockers.append(
            f"traders {len(traders)}/"
            f"{readiness['minimum_unique_traders']}"
        )

    structural_valid = (
        not missing_columns
        and not duplicate_signal_ids
        and invalid_targets == 0
        and not unexpected_null_counts
        and int(payload.get("count") or 0) == len(rows)
        and int(payload.get("data_version") or 0)
        == int(schema["data_version"])
    )

    report = {
        "data_version": payload.get("data_version"),
        "rows": len(rows),
        "target_1": targets.get(1, 0),
        "target_0": targets.get(0, 0),
        "unique_traders": len(traders),
        "traders": dict(traders),
        "unique_mints": unique_mints,
        "duplicate_signal_ids": duplicate_signal_ids,
        "missing_columns": missing_columns,
        "null_features": {
            key: value
            for key, value in null_counts.items()
            if value > 0
        },
        "invalid_targets": invalid_targets,
        "unexpected_null_features": unexpected_null_counts,
        "structural_valid": structural_valid,
        "ready_for_training": structural_valid and not blockers,
        "readiness_blockers": blockers,
    }

    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()