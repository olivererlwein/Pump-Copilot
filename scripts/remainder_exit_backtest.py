"""Qué hacer con el 25% final de la posición: tres cierres, mismo todo lo demás.

La política actual vende 25% de la posición original en +25%, +50% y +100%, y
corta entera en -20%. El último cuarto no tiene salida por precio: el 44,5% de
las operaciones queda sin resultado económico terminal, así que la función de
pago está incompleta y no hay nada bien definido que un modelo pueda aprender.

Este script aísla ESA pregunta. El ladder no se toca: TP25, TP50 y TP100
siguen vendiendo 25% cada uno, el stop sigue en -20%, las entradas, señales,
probabilidades y umbrales son los mismos. Lo único que cambia es la regla del
remanente.

    baseline  el remanente nunca se cierra (política actual)
    tp100     al tocar +100% se liquida también todo lo que quede
    trailing  tras el TP100, el remanente sale si cae 30% desde el pico
    time      cualquier remanente se cierra al final del horizonte de 15 min
    tp100_time  una regla para cada poblacion: el ganador grande sale en
              +100% y el que nunca llego a una salida terminal, a los 15 min

DOS POBLACIONES DISTINTAS, QUE NO SE MEZCLAN
Una posición puede terminar abierta por dos motivos muy diferentes: llegó al
TP100 y le sobra el último cuarto de un ganador grande, o nunca alcanzó
ninguna salida terminal. `tp100` y `trailing` solo resuelven la primera;
`time` resuelve las dos. El reporte las separa.

EXPLORATORIO, NO SELECCIÓN
Este holdout ya fue inspeccionado varias veces. Sirve para entender qué
mecanismo económico se comporta mejor, no para validar la política elegida:
esa habrá que congelarla y medirla contra datos nuevos.

El 30% del trailing y los 15 minutos del cierre temporal se declaran antes de
mirar resultados. Los 15 minutos vienen de hasta dónde llegan los datos, no de
evidencia de que sea el horizonte económico óptimo.
"""

import argparse
import json
import math
import statistics
import sys

sys.path.insert(0, "scripts")

from staged_payoff_backtest import (  # noqa: E402
    PUMPPORTAL_FEE_PER_SIDE,
    QUARTER,
    STOP_LOSS_PCT,
    TP_STAGES,
    bootstrap_ci,
    is_ambiguous,
)

TRAILING_DROP_FROM_PEAK = 0.30


def simulate(entry_price, path, remainder_policy, trailing_drop):
    """Ladder y stop intactos; solo cambia qué pasa con el último cuarto."""
    remaining = 1.0
    stage = 0
    ladder_fills = []
    remainder_fills = []
    stop_hit = False
    peak_change = 0.0
    last_change = 0.0

    for index, point in enumerate(path):
        price = float(point["price_sol"])
        if not math.isfinite(price) or price <= 0:
            continue
        change = price / entry_price - 1
        last_change = change
        peak_change = max(peak_change, change)
        is_last = index == len(path) - 1

        if change <= STOP_LOSS_PCT:
            if remaining > 0:
                ladder_fills.append((remaining, change))
                remaining = 0.0
            stop_hit = True
            break

        target_stage = 0
        for level, candidate in TP_STAGES:
            if change >= level:
                target_stage = candidate
                break
        if target_stage > stage:
            pending = target_stage - stage
            amount = min(remaining, QUARTER * pending)
            if amount > 0:
                ladder_fills.append((amount, change))
                remaining -= amount
                stage = target_stage
            # A: al tocar +100% se liquida todo lo que quede.
            if (remainder_policy in ("tp100", "tp100_time")
                    and stage == 3 and remaining > 0):
                remainder_fills.append((remaining, change))
                remaining = 0.0

        # B: trailing solo despues del TP100, sobre lo que sobra.
        if (remainder_policy == "trailing" and stage == 3 and remaining > 0
                and change <= peak_change - trailing_drop):
            remainder_fills.append((remaining, change))
            remaining = 0.0

        # C: cierre al final del horizonte observable.
        if (remainder_policy in ("time", "tp100_time")
                and is_last and remaining > 0):
            remainder_fills.append((remaining, change))
            remaining = 0.0

    return {
        "ladder_fills": ladder_fills,
        "remainder_fills": remainder_fills,
        "remaining": remaining,
        "stop_hit": stop_hit,
        "stage": stage,
        "last_change": last_change,
    }


def pnl_of(result, cost_per_side):
    ladder = sum(fraction * change for fraction, change in result["ladder_fills"])
    remainder = sum(
        fraction * change for fraction, change in result["remainder_fills"]
    )
    sold = (sum(f for f, _ in result["ladder_fills"])
            + sum(f for f, _ in result["remainder_fills"]))
    fees = cost_per_side * (1.0 + sold)
    return {
        "ladder": ladder,
        "remainder": remainder,
        "gross": ladder + remainder,
        "fees": fees,
        "net": ladder + remainder - fees,
    }


def max_drawdown(sequence):
    """Peor caída de la curva de capital, en orden temporal."""
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in sequence:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def analyse(rows, threshold, policy, cost_per_side, trailing_drop,
            one_position_seconds):
    selected = [row for row in rows if row["probability"] >= threshold]
    records = []
    for row in selected:
        result = simulate(
            row["price_at_signal"], row["path"], policy, trailing_drop
        )
        records.append({**row, **pnl_of(result, cost_per_side),
                        "remaining": result["remaining"],
                        "stage": result["stage"],
                        "stop_hit": result["stop_hit"]})

    nets = [record["net"] for record in records]
    if not nets:
        return None
    ordered = sorted(nets)
    total = sum(nets)

    open_after_tp100 = sum(
        1 for r in records if r["remaining"] > 1e-9 and r["stage"] == 3
    )
    open_never_terminal = sum(
        1 for r in records if r["remaining"] > 1e-9 and r["stage"] < 3
        and not r["stop_hit"]
    )

    available = float("-inf")
    operational = []
    for record in sorted(records, key=lambda x: x["signal_ts"]):
        if record["signal_ts"] < available:
            continue
        operational.append(record["net"])
        available = record["signal_ts"] + one_position_seconds

    return {
        "policy": policy,
        "threshold": threshold,
        "positions": len(records),
        "fully_closed": sum(1 for r in records if r["remaining"] <= 1e-9),
        "open_after_tp100": open_after_tp100,
        "open_never_terminal": open_never_terminal,
        "ambiguous": sum(1 for r in records if r["ambiguous"]),
        "gross": sum(r["gross"] for r in records),
        "from_remainder": sum(r["remainder"] for r in records),
        "fees": sum(r["fees"] for r in records),
        "net": total,
        "mean": total / len(nets),
        "median": statistics.median(nets),
        "ci95": bootstrap_ci(nets),
        "win_rate": sum(1 for v in nets if v > 0) / len(nets),
        "top5_share": (sum(ordered[-5:]) / total) if total > 0 else None,
        "top10_share": (sum(ordered[-10:]) / total) if total > 0 else None,
        "operational_n": len(operational),
        "operational_net": sum(operational),
        "operational_drawdown": max_drawdown(operational),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--paths", default="exports/checkpoint_paths.json")
    parser.add_argument(
        "--predictions", default="exports/holdout_predictions.json"
    )
    parser.add_argument(
        "--thresholds", type=float, nargs="+", default=[0.45, 0.50, 0.55]
    )
    parser.add_argument(
        "--cost-per-side", type=float, default=PUMPPORTAL_FEE_PER_SIDE
    )
    parser.add_argument("--one-position-seconds", type=float, default=900)
    parser.add_argument(
        "--trailing-sensitivities", type=float, nargs="+",
        default=[0.20, 0.40],
    )
    args = parser.parse_args()

    paths = {
        int(row["signal_id"]): row
        for row in json.load(open(args.paths, encoding="utf-8"))["rows"]
    }
    rows = []
    for item in json.load(open(args.predictions, encoding="utf-8")):
        record = paths.get(int(item["signal_id"]))
        if record is None or not record["checkpoint_path"]:
            continue
        entry = float(record["price_at_signal"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        rows.append({
            "signal_id": item["signal_id"],
            "signal_ts": float(record["signal_ts"]),
            "probability": float(item["probability"]),
            "price_at_signal": entry,
            "path": record["checkpoint_path"],
            "ambiguous": is_ambiguous(entry, record["checkpoint_path"]),
        })

    print(f"señales de holdout: {len(rows)}   "
          f"coste {args.cost_per_side:.0%} por lado (solo PumpPortal)")
    print(f"trailing declarado antes de medir: "
          f"{TRAILING_DROP_FROM_PEAK:.0%} desde el pico")
    print("EXPLORATORIO: este holdout ya fue inspeccionado; no valida la "
          "política que se elija.")
    print()

    for threshold in args.thresholds:
        print("=" * 78)
        print(f"UMBRAL {threshold:.2f}"
              + ("   (preseleccionado por CV)" if threshold == 0.45
                 else "   (sensibilidad)"))
        print("=" * 78)
        head = (f"  {'política':<10} {'pos':>4} {'cerr':>5} {'ab.TP100':>9} "
                f"{'ab.sin sal.':>12} {'bruto':>8} {'del 25%':>9} "
                f"{'fees':>6} {'NETO':>8} {'medio':>8} {'mediana':>8} "
                f"{'IC95':>20} {'win':>6} {'top5':>6} {'DD op.':>8}")
        print(head)
        for policy in ("baseline", "tp100", "trailing", "time",
                       "tp100_time"):
            r = analyse(rows, threshold, policy, args.cost_per_side,
                        TRAILING_DROP_FROM_PEAK, args.one_position_seconds)
            if r is None:
                continue
            lo, hi = r["ci95"]
            top5 = "  n/a" if r["top5_share"] is None else f"{r['top5_share']:>5.0%}"
            print(f"  {policy:<10} {r['positions']:>4} {r['fully_closed']:>5} "
                  f"{r['open_after_tp100']:>9} {r['open_never_terminal']:>12} "
                  f"{r['gross']:>+8.3f} {r['from_remainder']:>+9.3f} "
                  f"{r['fees']:>6.3f} {r['net']:>+8.3f} {r['mean']:>+8.4f} "
                  f"{r['median']:>+8.4f} "
                  f"[{lo:>+7.4f},{hi:>+7.4f}] {r['win_rate']:>5.0%} "
                  f"{top5} {r['operational_drawdown']:>+8.3f}")
        print(f"  ambiguas: "
              f"{analyse(rows, threshold, 'baseline', args.cost_per_side, TRAILING_DROP_FROM_PEAK, args.one_position_seconds)['ambiguous']}"
              f" de {analyse(rows, threshold, 'baseline', args.cost_per_side, TRAILING_DROP_FROM_PEAK, args.one_position_seconds)['positions']}")
        print()

    print("=" * 78)
    print("SENSIBILIDAD DEL TRAILING (exploratoria, no selección)")
    print("=" * 78)
    for drop in [TRAILING_DROP_FROM_PEAK] + list(args.trailing_sensitivities):
        r = analyse(rows, 0.45, "trailing", args.cost_per_side, drop,
                    args.one_position_seconds)
        tag = " <- declarado" if drop == TRAILING_DROP_FROM_PEAK else ""
        print(f"  caída {drop:>4.0%} desde el pico -> neto {r['net']:>+8.3f}  "
              f"medio {r['mean']:>+8.4f}  del 25%: {r['from_remainder']:>+7.3f}"
              f"{tag}")


if __name__ == "__main__":
    main()
