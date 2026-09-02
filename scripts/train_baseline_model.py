import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_readiness_blockers(rows, schema):
    target = schema["target"]
    targets = Counter(row[target] for row in rows)
    traders = {row["trader"] for row in rows}
    readiness = schema["readiness"]
    blockers = []

    checks = (
        ("rows", len(rows), readiness["minimum_completed_rows"]),
        ("target_0", targets[0], readiness["minimum_target_0"]),
        ("target_1", targets[1], readiness["minimum_target_1"]),
        (
            "traders",
            len(traders),
            readiness["minimum_unique_traders"],
        ),
    )

    for name, actual, required in checks:
        if actual < required:
            blockers.append(f"{name} {actual}/{required}")

    return blockers


def validate_dataset(payload, schema):
    rows = payload.get("rows", [])
    categorical = schema["categorical_features"]
    numeric = schema["numeric_features"]
    target = schema["target"]
    required = (
        schema["metadata_columns"]
        + categorical
        + numeric
        + [target]
    )
    nullable = set(schema.get("nullable_features", []))

    if not rows:
        raise ValueError("The training dataset is empty")

    if int(payload.get("data_version") or 0) != int(
        schema["data_version"]
    ):
        raise ValueError("Dataset and schema versions do not match")

    if int(payload.get("count") or 0) != len(rows):
        raise ValueError("Dataset count does not match row count")

    signal_ids = set()

    for index, row in enumerate(rows):
        missing = [name for name in required if name not in row]
        if missing:
            raise ValueError(f"Row {index} is missing columns: {missing}")

        signal_id = row["signal_id"]
        if signal_id in signal_ids:
            raise ValueError(f"Duplicate signal_id: {signal_id}")
        signal_ids.add(signal_id)

        if row[target] not in (0, 1):
            raise ValueError(f"Invalid target in row {index}")

        if not row.get(schema["split_group"]):
            raise ValueError(f"Missing split group in row {index}")

        for feature in categorical:
            if row[feature] is None and feature not in nullable:
                raise ValueError(
                    f"Unexpected null for {feature} in row {index}"
                )

        for feature in numeric:
            value = row[feature]
            if value is None:
                if feature not in nullable:
                    raise ValueError(
                        f"Unexpected null for {feature} in row {index}"
                    )
                continue

            number = float(value)
            if not math.isfinite(number):
                raise ValueError(
                    f"Non-finite value for {feature} in row {index}"
                )

    return rows


def temporal_group_split(rows, schema, test_fraction):
    time_column = schema["time_column"]
    group_column = schema["split_group"]
    ordered = sorted(rows, key=lambda row: float(row[time_column]))
    cutoff_index = min(
        len(ordered) - 1,
        max(1, int(len(ordered) * (1.0 - test_fraction))),
    )
    cutoff_ts = float(ordered[cutoff_index][time_column])

    test_groups = {
        row[group_column]
        for row in ordered
        if float(row[time_column]) >= cutoff_ts
    }
    train_rows = [
        row for row in ordered
        if row[group_column] not in test_groups
    ]
    test_rows = [
        row for row in ordered
        if (
            row[group_column] in test_groups
            and float(row[time_column]) >= cutoff_ts
        )
    ]
    purged_rows = [
        row for row in ordered
        if (
            row[group_column] in test_groups
            and float(row[time_column]) < cutoff_ts
        )
    ]

    if not train_rows or not test_rows:
        raise ValueError("Temporal group split produced an empty partition")

    train_groups = {row[group_column] for row in train_rows}
    if train_groups & test_groups:
        raise ValueError("A split group appears in both partitions")

    target = schema["target"]
    for name, partition in (("train", train_rows), ("test", test_rows)):
        classes = {row[target] for row in partition}
        if classes != {0, 1}:
            raise ValueError(
                f"The {name} partition does not contain both classes"
            )

    return train_rows, test_rows, purged_rows, cutoff_ts


def build_matrix(rows, schema):
    categorical = schema["categorical_features"]
    numeric = schema["numeric_features"]
    columns = categorical + numeric
    matrix = []

    for row in rows:
        values = []
        for feature in categorical:
            value = row[feature]
            values.append(np.nan if value is None else str(value))
        for feature in numeric:
            value = row[feature]
            values.append(np.nan if value is None else float(value))
        matrix.append(values)

    return np.asarray(matrix, dtype=object), columns


def build_pipeline(schema):
    categorical_count = len(schema["categorical_features"])
    numeric_count = len(schema["numeric_features"])
    categorical_columns = list(range(categorical_count))
    numeric_columns = list(
        range(categorical_count, categorical_count + numeric_count)
    )

    numeric_pipeline = Pipeline(
        steps=[
            (
                "imputer",
                SimpleImputer(strategy="median", add_indicator=True),
            ),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "encoder",
                OneHotEncoder(handle_unknown="ignore"),
            ),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[
            ("categorical", categorical_pipeline, categorical_columns),
            ("numeric", numeric_pipeline, numeric_columns),
        ]
    )

    return Pipeline(
        steps=[
            ("preprocess", preprocessor),
            (
                "classifier",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=42,
                ),
            ),
        ]
    )


def classification_metrics(targets, predictions, probabilities):
    return {
        "balanced_accuracy": round(
            balanced_accuracy_score(targets, predictions), 6
        ),
        "precision": round(
            precision_score(targets, predictions, zero_division=0), 6
        ),
        "recall": round(
            recall_score(targets, predictions, zero_division=0), 6
        ),
        "f1": round(
            f1_score(targets, predictions, zero_division=0), 6
        ),
        "roc_auc": round(roc_auc_score(targets, probabilities), 6),
        "average_precision": round(
            average_precision_score(targets, probabilities), 6
        ),
        "confusion_matrix": confusion_matrix(
            targets,
            predictions,
            labels=[0, 1],
        ).tolist(),
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
    parser.add_argument(
        "--output",
        default="models/baseline_v2.joblib",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--allow-not-ready", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if not 0.1 <= args.test_fraction <= 0.4:
        raise SystemExit("--test-fraction must be between 0.1 and 0.4")

    payload = load_json(args.dataset)
    schema = load_json(args.schema)
    rows = validate_dataset(payload, schema)
    blockers = get_readiness_blockers(rows, schema)

    if blockers and not args.allow_not_ready:
        print(json.dumps({
            "trained": False,
            "readiness_blockers": blockers,
        }, indent=2))
        raise SystemExit(2)

    if blockers and not args.no_save:
        raise SystemExit(
            "A preliminary model cannot be saved; add --no-save"
        )

    train_rows, test_rows, purged_rows, cutoff_ts = temporal_group_split(
        rows,
        schema,
        args.test_fraction,
    )
    train_matrix, feature_columns = build_matrix(train_rows, schema)
    test_matrix, _ = build_matrix(test_rows, schema)
    target = schema["target"]
    train_targets = np.asarray([row[target] for row in train_rows])
    test_targets = np.asarray([row[target] for row in test_rows])

    pipeline = build_pipeline(schema)
    pipeline.fit(train_matrix, train_targets)
    probabilities = pipeline.predict_proba(test_matrix)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)

    dummy = DummyClassifier(strategy="prior")
    dummy.fit(train_matrix, train_targets)
    dummy_probabilities = dummy.predict_proba(test_matrix)[:, 1]
    dummy_predictions = dummy.predict(test_matrix)

    report = {
        "trained": True,
        "preliminary": bool(blockers),
        "readiness_blockers": blockers,
        "data_version": schema["data_version"],
        "rows": len(rows),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "purged_rows": len(purged_rows),
        "train_mints": len({row["mint"] for row in train_rows}),
        "test_mints": len({row["mint"] for row in test_rows}),
        "mint_overlap": 0,
        "cutoff_ts": cutoff_ts,
        "train_max_ts": max(row["signal_ts"] for row in train_rows),
        "test_min_ts": min(row["signal_ts"] for row in test_rows),
        "train_targets": dict(Counter(map(int, train_targets))),
        "test_targets": dict(Counter(map(int, test_targets))),
        "model_metrics": classification_metrics(
            test_targets,
            predictions,
            probabilities,
        ),
        "dummy_metrics": classification_metrics(
            test_targets,
            dummy_predictions,
            dummy_probabilities,
        ),
        "saved": not args.no_save,
    }

    if not args.no_save:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "pipeline": pipeline,
            "schema": schema,
            "feature_columns": feature_columns,
            "threshold": 0.5,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "sklearn_version": sklearn.__version__,
            "report": report,
        }, output_path)

    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
