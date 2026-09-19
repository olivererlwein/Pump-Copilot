import argparse
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


DEFAULT_BASE_URL = "https://web-production-4ea0d.up.railway.app"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--output-dir", default="exports")
    parser.add_argument(
        "--source",
        choices=("primary", "account-checkpoints"),
        default="primary",
    )
    args = parser.parse_args()

    load_dotenv()

    app_token = os.getenv("APP_TOKEN", "")
    if not app_token:
        raise SystemExit("APP_TOKEN is missing from .env")

    if args.source == "account-checkpoints":
        url = (
            f"{args.base_url.rstrip('/')}"
            "/api/account-checkpoint-training-dataset"
        )
        output_stem = "account_checkpoint_training_dataset"
    else:
        query = urlencode({"limit": max(1, min(args.limit, 5000))})
        url = f"{args.base_url.rstrip('/')}/api/training-dataset?{query}"
        output_stem = "training_dataset"

    request = Request(
        url,
        headers={"x-app-token": app_token},
    )

    with urlopen(request, timeout=60) as response:
        payload = json.load(response)

    rows = payload.get("rows", [])
    rows.sort(key=lambda row: float(row.get("signal_ts") or 0))

    if not rows:
        raise SystemExit("The training dataset is empty")

    version = int(payload.get("data_version") or 0)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{output_stem}_v{version}.json"
    csv_path = output_dir / f"{output_stem}_v{version}.csv"

    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print({
        "data_version": version,
        "label_source": payload.get("label_source", "primary"),
        "count": len(rows),
        "json": str(json_path),
        "csv": str(csv_path),
    })


if __name__ == "__main__":
    main()
