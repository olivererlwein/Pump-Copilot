"""Read-only audit of token-age availability and first-mint outcomes.

This does not change the recorded score or propose a replacement decision.
"""

import argparse
import collections
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv


DEFAULT_BASE_URL = "https://web-production-4ea0d.up.railway.app"


def summarize(rows):
    ordered = sorted(rows, key=lambda row: (float(row["signal_ts"]), int(row["signal_id"])))
    first_mints = set()
    groups = collections.defaultdict(list)
    groups["all"] = ordered
    for row in ordered:
        groups[f"trader_all:{row['trader']}"].append(row)
        mint = str(row["mint"])
        if mint not in first_mints:
            first_mints.add(mint)
            groups["first_labeled_row_per_mint"].append(row)
            groups[f"trader_first_labeled_mint:{row['trader']}"].append(row)

    report = {}
    for name, sample in groups.items():
        known = 0
        timing_max = 0
        timing_max_unknown_age = 0
        wins = 0
        missing_target = 0
        for row in sample:
            age = row.get("token_age_seconds")
            if age is not None:
                if isinstance(age, bool) or not math.isfinite(float(age)) or float(age) < 0:
                    raise ValueError("INVALID_TOKEN_AGE")
                known += 1
            timing_max += int(row["timing_score"] == 20)
            timing_max_unknown_age += int(age is None and row["timing_score"] == 20)
            target = row.get("target_tp25_before_sl10")
            if target is None:
                missing_target += 1
            elif target not in (0, 1) or isinstance(target, bool):
                raise ValueError("INVALID_TARGET")
            else:
                wins += int(target)
        report[name] = {
            "rows": len(sample),
            "stored_watched_create_age_present": known,
            "creation_age_unknown": len(sample) - known,
            "recorded_timing_20": timing_max,
            "timing_20_with_unknown_age": timing_max_unknown_age,
            "tp25_before_sl10": wins,
            "unlabeled": missing_target,
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="exports/account_checkpoint_live.json")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    if args.live:
        load_dotenv()
        token = os.getenv("APP_TOKEN", "")
        if not token:
            raise SystemExit("APP_TOKEN is missing from .env")
        request = Request(
            f"{args.base_url.rstrip('/')}/api/account-checkpoint-training-dataset",
            headers={"x-app-token": token},
        )
        with urlopen(request, timeout=90) as response:
            payload = json.load(response)
    else:
        payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    print(json.dumps({
        "source": "live_read_only" if args.live else args.dataset,
        "label_source": payload.get("label_source"),
        "scope": "completed dataset rows; first labeled row is not the first on-chain trade",
        "shadow_age_policy": (
            "unknown stays unknown; stored age comes only from watched create events; "
            "timing 20 is not token age; scores and decisions unchanged"
        ),
        "groups": summarize(payload["rows"]),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
