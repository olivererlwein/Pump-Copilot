"""Informe de cobertura de ingesta a partir de los endpoints de producción.

Solo lectura. Junta en una tabla lo que hoy se mira a mano en cinco
endpoints: wallets silenciosas, notificaciones WSS por wallet, fallos de
fetch, operaciones que el fallback RPC vio y nadie aplicó, y señales por
trader. Guarda un snapshot JSON y, si hay uno anterior, muestra la diferencia.

    python scripts/ingest_coverage_report.py
    python scripts/ingest_coverage_report.py --base http://localhost:8000

El token sale de APP_TOKEN (entorno o .env). Los snapshots quedan en
reports/ingest_coverage/<UTC>.json.
"""

import argparse
import collections
import datetime
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv

DEFAULT_BASE = "https://web-production-4ea0d.up.railway.app"
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "reports" / "ingest_coverage"


def fetch(base, token, path):
    request = Request(base + path, headers={"x-app-token": token})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def collect(base, token):
    now = time.time()
    status = fetch(base, token, "/api/status")
    watched = fetch(base, token, "/api/watched-wallets")
    wss = fetch(base, token, "/api/helius-standard-wss-stats")
    fallback = fetch(base, token, "/api/rpc-fallback-stats")
    signals = fetch(base, token, "/api/signals")
    training = fetch(base, token, "/api/training-stats")

    missing_24h = collections.Counter(
        event["trader"] for event in fallback.get("missing_events") or []
        if now - float(event.get("block_time") or 0) < 86400
    )
    signals_24h = collections.Counter(
        (row.get("trader"), row.get("decision")) for row in signals
        if now - float(row.get("ts") or 0) < 86400
    )
    wss_wallets = {
        row["trader"]: row for row in wss["last_24h"].get("wallets") or []
    }
    unstored = {
        row["trader"]: row
        for row in (wss.get("unstored_notifications_since_start") or {})
        .get("wallets") or []
    }
    muted = {row["trader"] for row in wss.get("muted_wallets") or []}

    wallets = []
    for row in watched["wallets"]:
        trader = row["trader"]
        coverage = wss_wallets.get(trader) or {}
        wallets.append({
            "trader": trader,
            "silent": bool(row["silent"]),
            "age_hours": row.get("age_hours"),
            "wss_pump_notifications_24h": coverage.get("pump_log_notifications", 0),
            "wss_parsed_24h": coverage.get("notifications_with_parsed_transaction", 0),
            "wss_unstored_since_start": (unstored.get(trader) or {}).get("notifications", 0),
            "wss_muted": trader in muted,
            "fallback_missing_24h": missing_24h.get(trader, 0),
            "signals_24h": sum(
                count for (name, _), count in signals_24h.items() if name == trader
            ),
        })

    return {
        "captured_at": datetime.datetime.fromtimestamp(
            now, datetime.timezone.utc
        ).isoformat(timespec="seconds"),
        "stream": {
            "connected": status["stream_connected"],
            "last_account_event_age_s": (
                round(now - status["stream_last_account_event_ts"])
                if status.get("stream_last_account_event_ts") else None
            ),
            "last_token_event_age_s": (
                round(now - status["stream_last_token_event_ts"])
                if status.get("stream_last_token_event_ts") else None
            ),
        },
        "wss": {
            "connected": wss["runtime"]["connected"],
            "selected_wallets": wss["selected_wallets"],
            "active_wallets": wss.get("active_wallets"),
            "subscriptions": wss["runtime"]["subscriptions"],
            "last_error": wss["runtime"]["last_error"],
            "last_rpc_error_detail": wss.get("last_rpc_error_detail"),
            "fetch_errors_24h": wss["last_24h"].get("fetch_errors"),
            "statuses_24h": wss["last_24h"].get("statuses"),
            "parsed_events_24h": wss["last_24h"].get("parsed_events"),
        },
        "fallback": {
            "last_error": fallback.get("last_error"),
            "saturated_wallets": fallback.get("saturated_wallets"),
            "missing_total": fallback.get("missing"),
            "matched_total": fallback.get("matched"),
        },
        "training": {
            key: training.get(key)
            for key in ("total", "training_eligible", "excluded_unfresh",
                        "target_1", "target_0")
        },
        "silent_wallets": watched["silent"],
        "wallets": wallets,
    }


def latest_snapshot():
    if not SNAPSHOT_DIR.exists():
        return None
    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def print_report(report, previous):
    print(f"Captured {report['captured_at']}")
    if previous:
        print(f"Previous {previous['captured_at']}")
    print()
    print(f"stream: {report['stream']}")
    print(f"wss:    {json.dumps(report['wss'], ensure_ascii=False)}")
    print(f"fallback: {report['fallback']}")
    print(f"training: {report['training']}")
    print(f"silent wallets: {report['silent_wallets']}"
          + (f" (was {previous['silent_wallets']})" if previous else ""))
    print()
    header = (
        f"{'trader':16} {'silent':6} {'age_h':>7} {'pump24h':>7} {'parsed':>6} "
        f"{'unstored':>8} {'muted':>5} {'missing':>7} {'signals':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in report["wallets"]:
        age = row["age_hours"]
        print(
            f"{row['trader']:16} {str(row['silent']).lower():6} "
            f"{(f'{age:.1f}' if age is not None else 'never'):>7} "
            f"{row['wss_pump_notifications_24h']:>7} {row['wss_parsed_24h']:>6} "
            f"{row['wss_unstored_since_start']:>8} "
            f"{str(row['wss_muted']).lower():>5} "
            f"{row['fallback_missing_24h']:>7} {row['signals_24h']:>7}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default=os.getenv("PUMP_COPILOT_BASE", DEFAULT_BASE))
    parser.add_argument("--no-save", action="store_true", help="no guardar snapshot")
    args = parser.parse_args()

    load_dotenv()
    token = os.getenv("APP_TOKEN", "").strip()
    if not token:
        raise SystemExit("APP_TOKEN missing (environment or .env)")

    report = collect(args.base.rstrip("/"), token)
    previous = latest_snapshot()
    print_report(report, previous)
    if not args.no_save:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAPSHOT_DIR / (
            report["captured_at"].replace(":", "").replace("+0000", "Z") + ".json"
        )
        path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nsnapshot: {path.relative_to(SNAPSHOT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
