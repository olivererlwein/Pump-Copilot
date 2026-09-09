import json
import math
from pathlib import Path


class ShadowModelError(ValueError):
    pass


class ShadowLogisticModel:
    def __init__(self, artifact):
        if int(artifact.get("format_version") or 0) != 1:
            raise ShadowModelError("Unsupported shadow model format")

        self.artifact = artifact
        self.model_version = str(artifact.get("model_version") or "")
        self.data_version = int(artifact.get("data_version") or 0)
        self.artifact_role = str(
            artifact.get("artifact_role") or "legacy"
        )
        self.deployment_ready = bool(
            artifact.get("deployment_ready", True)
        )
        self.deployment_blockers = list(
            artifact.get("deployment_blockers") or []
        )
        self.threshold = float(artifact["threshold"])
        self.categorical = artifact["categorical"]
        self.numeric = artifact["numeric"]
        self.coefficients = [
            float(value) for value in artifact["coefficients"]
        ]
        self.intercept = float(artifact["intercept"])

        expected = sum(
            len(item["categories"])
            for item in self.categorical
        ) + len(self.numeric["mean"])
        if expected != len(self.coefficients):
            raise ShadowModelError(
                "Shadow model coefficient count does not match features"
            )

    @classmethod
    def from_path(cls, path):
        artifact = json.loads(
            Path(path).read_text(encoding="utf-8")
        )
        return cls(artifact)

    def transform(self, features):
        values = []

        for item in self.categorical:
            value = features.get(item["name"])
            if value is None:
                value = item["impute_value"]
            value = str(value)
            values.extend(
                1.0 if value == category else 0.0
                for category in item["categories"]
            )

        raw_numeric = []
        missing = []
        for index, name in enumerate(self.numeric["features"]):
            value = features.get(name)
            is_missing = value is None
            missing.append(is_missing)
            raw_numeric.append(
                float(self.numeric["medians"][index])
                if is_missing
                else float(value)
            )

        raw_numeric.extend(
            1.0 if missing[index] else 0.0
            for index in self.numeric["indicator_indexes"]
        )

        values.extend(
            (value - float(mean)) / float(scale)
            for value, mean, scale in zip(
                raw_numeric,
                self.numeric["mean"],
                self.numeric["scale"],
            )
        )
        return values

    def predict_probability(self, features):
        values = self.transform(features)
        logit = self.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.coefficients, values)
        )

        if logit >= 0:
            return 1.0 / (1.0 + math.exp(-logit))

        exp_logit = math.exp(logit)
        return exp_logit / (1.0 + exp_logit)

    def predict(self, features):
        probability = self.predict_probability(features)
        return {
            "model_version": self.model_version,
            "data_version": self.data_version,
            "probability": probability,
            "threshold": self.threshold,
            "predicted_target": int(probability >= self.threshold),
        }
