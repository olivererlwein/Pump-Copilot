"""PnL de las señales bajo la política de salidas real de Pump Copilot.

El proxy anterior valía +0,25 cada acierto y -0,10 cada fallo. La política
real no hace eso: vende en etapas de 25% de la posición original al +25%,
+50% y +100%, y corta la posición entera al -20%. Un token que hace 4x no
vale lo mismo que uno que apenas toca +25%, y el proxy plano no los
distingue.

Reproduce `decide_live_position_exit()` (app.py) sobre la trayectoria de
checkpoints on-chain. No reentrena, no mueve umbrales y no elige umbral por
resultado: consume las probabilidades del holdout ya generadas.

    python scripts/staged_payoff_backtest.py --paths exports/checkpoint_paths.json

LÍMITES QUE NO SE TAPAN
- Solo hay 5 observaciones por señal (10s, 30s, 1m, 5m, 15m). Entre dos
  checkpoints el precio no se observa: si en ese hueco tocó un TP o el stop,
  no se sabe. Cada señal se marca como ambigua cuando su salto entre
  checkpoints consecutivos es grande, y se reporta cuántas son.
- La política no tiene salida por precio para el último 25% de la posición:
  después del TP100 solo la cierran el stop, la venta total del trader o una
  venta parcial suya. Acá no se inventa un cierre: ese resto se valora al
  último precio observado y se informa aparte.
- Las ventas del trader de origen (TRADER_EXIT / TRADER_PARTIAL) no se
  simulan: la trayectoria on-chain no trae los eventos del trader. Es una
  omisión conocida, no una simplificación conveniente.
"""

import argparse
import json
import math
import statistics

STOP_LOSS_PCT = -0.20
TP_STAGES = ((1.00, 3), (0.50, 2), (0.25, 1))
QUARTER = 0.25
PUMPPORTAL_FEE_PER_SIDE = 0.01


def simulate_position(entry_price, path, stop_loss_pct=STOP_LOSS_PCT):
    """Aplica la política real a una trayectoria. Devuelve fracciones vendidas.

    Espeja `decide_live_position_exit`: el stop cierra todo lo que queda, y
    cada etapa de take profit vende 25% de la posición ORIGINAL por etapa
    pendiente, de modo que un salto directo a +100% vende 75% de una vez.
    """
    remaining = 1.0
    stage = 0
    fills = []          # (fracción de la posición original, retorno de esa parte)
    stop_hit = False

    for point in path:
        price = float(point["price_sol"])
        if not math.isfinite(price) or price <= 0:
            continue
        change = price / entry_price - 1

        if change <= stop_loss_pct:
            if remaining > 0:
                fills.append((remaining, change))
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
                fills.append((amount, change))
                remaining -= amount
                stage = target_stage

    return {
        "fills": fills,
        "remaining": remaining,
        "stop_hit": stop_hit,
        "stage": stage,
        "final_change": (
            float(path[-1]["price_sol"]) / entry_price - 1 if path else 0.0
        ),
    }


def position_pnl(result, cost_per_side):
    """PnL por unidad invertida. El resto sin política se informa aparte."""
    realized = sum(fraction * change for fraction, change in result["fills"])
    open_tail = result["remaining"] * result["final_change"]
    # Una comisión por la compra completa y otra por cada fracción vendida.
    sold = sum(fraction for fraction, _ in result["fills"])
    fees = cost_per_side * (1.0 + sold)
    return {
        "realized": realized,
        "open_tail": open_tail,
        "tail_fraction": result["remaining"],
        "fees": fees,
        "net_realized": realized - fees,
    }


def is_ambiguous(entry_price, path):
    """Marca huecos donde un TP o el stop pudo ocurrir sin ser observado.

    Entre dos checkpoints consecutivos solo se conocen los extremos. Si el
    precio sube cruzando una etapa o baja cruzando el stop en ese tramo, el
    orden real de los eventos es desconocido.
    """
    previous = 0.0
    for point in path:
        price = float(point["price_sol"])
        if not math.isfinite(price) or price <= 0:
            continue
        change = price / entry_price - 1
        crossed_up = any(
            previous < level <= change for level, _ in reversed(TP_STAGES)
        )
        crossed_down = previous > STOP_LOSS_PCT >= change
        # Un tramo que sube y baja mucho a la vez no se puede ordenar.
        if crossed_up and crossed_down:
            return True
        # Una caída fuerte seguida de subida (o al revés) dentro del tramo
        # tampoco se ve: se aproxima por la magnitud del salto.
        if abs(change - previous) >= 0.45:
            return True
        previous = change
    return False


def describe(values):
    if not values:
        return {}
    ordered = sorted(values)
    total = sum(values)
    top5 = sum(ordered[-5:])
    top10 = sum(ordered[-10:])
    return {
        "n": len(values),
        "total": total,
        "mean": total / len(values),
        "median": statistics.median(values),
        "p10": ordered[max(0, int(0.10 * len(ordered)) - 1)],
        "p90": ordered[min(len(ordered) - 1, int(0.90 * len(ordered)))],
        "best": ordered[-1],
        "worst": ordered[0],
        "top5_share": (top5 / total) if total > 0 else None,
        "top10_share": (top10 / total) if total > 0 else None,
    }


def bootstrap_ci(values, iterations=2000, seed=11):
    import random
    if not values:
        return (None, None)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(sum(rng.choice(values) for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * iterations)], means[int(0.975 * iterations)])


def run(rows, thresholds, cost_per_side, one_position_seconds,
        stop_loss_pct=STOP_LOSS_PCT):
    report = []
    for threshold in thresholds:
        selected = [row for row in rows if row["probability"] >= threshold]
        per_signal = []
        ambiguous = 0
        tails = 0
        for row in selected:
            result = simulate_position(
                row["price_at_signal"], row["path"], stop_loss_pct
            )
            pnl = position_pnl(result, cost_per_side)
            ambiguous += int(row["ambiguous"])
            tails += int(result["remaining"] > 0)
            per_signal.append({**row, **pnl, "stop_hit": result["stop_hit"],
                               "stage": result["stage"]})

        realized = [item["net_realized"] for item in per_signal]
        gross = [item["realized"] for item in per_signal]
        fees = sum(item["fees"] for item in per_signal)

        # Simulación operativa: una sola posición a la vez.
        available = float("-inf")
        operational = []
        for item in sorted(per_signal, key=lambda x: x["signal_ts"]):
            if item["signal_ts"] < available:
                continue
            operational.append(item["net_realized"])
            available = item["signal_ts"] + one_position_seconds

        report.append({
            "threshold": threshold,
            "selected": len(selected),
            "wins": sum(1 for value in gross if value > 0),
            "losses": sum(1 for value in gross if value < 0),
            # Si no tocó ni +25% ni -20%, la política no vendió nada: no es
            # una pérdida, es una posición que quedó entera sin resolver.
            "untouched": sum(1 for value in gross if value == 0),
            "stopped": sum(1 for item in per_signal if item["stop_hit"]),
            "reached_tp100": sum(1 for item in per_signal if item["stage"] == 3),
            "open_tails": tails,
            "ambiguous": ambiguous,
            "gross": describe(gross),
            "net": describe(realized),
            "fees_total": fees,
            "net_ci95": bootstrap_ci(realized),
            "operational": describe(operational),
            "tail_value_if_marked": sum(
                item["open_tail"] for item in per_signal
            ),
        })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--paths", default="exports/checkpoint_paths.json")
    parser.add_argument(
        "--predictions", default="exports/holdout_predictions.json"
    )
    parser.add_argument(
        "--thresholds", type=float, nargs="+",
        default=[0.45, 0.50, 0.55, 0.70],
    )
    parser.add_argument(
        "--cost-per-side", type=float, default=PUMPPORTAL_FEE_PER_SIDE
    )
    parser.add_argument("--one-position-seconds", type=float, default=900)
    # Contrafactual: mismo ladder, mismos costes, mismas predicciones.
    parser.add_argument("--stop-pct", type=float, default=STOP_LOSS_PCT)
    args = parser.parse_args()

    paths = {
        int(row["signal_id"]): row
        for row in json.load(open(args.paths, encoding="utf-8"))["rows"]
    }
    predictions = json.load(open(args.predictions, encoding="utf-8"))

    rows = []
    missing = 0
    for item in predictions:
        record = paths.get(int(item["signal_id"]))
        if record is None or not record["checkpoint_path"]:
            missing += 1
            continue
        entry = float(record["price_at_signal"])
        if not math.isfinite(entry) or entry <= 0:
            missing += 1
            continue
        rows.append({
            "signal_id": item["signal_id"],
            "signal_ts": float(record["signal_ts"]),
            "trader": record["trader"],
            "probability": float(item["probability"]),
            "target": int(item["target"]),
            "price_at_signal": entry,
            "path": record["checkpoint_path"],
            "ambiguous": is_ambiguous(entry, record["checkpoint_path"]),
        })

    print(f"señales de holdout con trayectoria: {len(rows)}"
          f"   sin trayectoria: {missing}")
    print(f"coste por lado aplicado: {args.cost_per_side:.1%} "
          f"(solo PumpPortal Lightning)")
    print()

    print(f"stop aplicado: {args.stop_pct:.0%}")
    print()
    for entry in run(rows, args.thresholds, args.cost_per_side,
                     args.one_position_seconds, args.stop_pct):
        print("=" * 74)
        print(f"UMBRAL {entry['threshold']:.2f}"
              f"   seleccionadas {entry['selected']}"
              f"   ganadoras {entry['wins']}   perdedoras {entry['losses']}"
              f"   sin movimiento {entry['untouched']}")
        print(f"  frenadas por stop {entry['stopped']}"
              f"   llegaron a TP100 {entry['reached_tp100']}"
              f"   con resto abierto {entry['open_tails']}"
              f"   AMBIGUAS {entry['ambiguous']}")
        g, n, o = entry["gross"], entry["net"], entry["operational"]
        if not n:
            print("  (sin señales)")
            continue
        print(f"  bruto  total {g['total']:+.3f}  medio {g['mean']:+.4f}  "
              f"mediana {g['median']:+.4f}")
        print(f"  fees   total {entry['fees_total']:.3f}")
        print(f"  NETO   total {n['total']:+.3f}  medio {n['mean']:+.4f}  "
              f"mediana {n['median']:+.4f}")
        lo, hi = entry["net_ci95"]
        print(f"         IC95 del medio [{lo:+.4f} , {hi:+.4f}]")
        print(f"         mejor {n['best']:+.3f}  peor {n['worst']:+.3f}  "
              f"p10 {n['p10']:+.4f}  p90 {n['p90']:+.4f}")
        if n["top5_share"] is not None:
            print(f"         concentración: top5 {n['top5_share']:.0%}  "
                  f"top10 {n['top10_share']:.0%} del PnL total")
        print(f"  una posición 15m: {o['n']} operables, "
              f"total {o['total']:+.3f}, medio {o['mean']:+.4f}")
        print(f"  resto sin política de salida, valorado al último precio: "
              f"{entry['tail_value_if_marked']:+.3f} (NO incluido arriba)")
        print()


if __name__ == "__main__":
    main()
