"""Alinea la etiqueta con el stop que la política realmente usa.

El modelo entrena contra `tp25_before_sl10` mientras la ejecución corta en
−20 %. Son dos preguntas distintas, y el backtest escalonado mostró que la
diferencia no es cosmética. Acá se re-etiqueta el histórico con el stop real,
sobre las mismas trayectorias y el mismo split, para ver si la señal sobrevive
a responder la pregunta correcta.

Además construye un objetivo económico diagnóstico —el signo del PnL que la
política habría realizado— porque el propio backtest mostró que tocar +25 %
no basta: vendiendo solo un cuarto ahí, una señal etiquetada como ganadora
puede terminar negativa.

    python scripts/relabel_stop_alignment.py

No reentrena para producción, no guarda artifacts y no mueve umbrales.
"""

import argparse
import json
import math
import sys

import numpy as np

sys.path.insert(0, "scripts")

from compare_model_economics import evaluate_strategy          # noqa: E402
from staged_payoff_backtest import (                            # noqa: E402
    is_ambiguous,
    position_pnl,
    simulate_position,
)
from train_baseline_model import load_json, validate_dataset    # noqa: E402


def checkpoint_target(entry_price, path, stop_pct):
    """Espeja `app.account_checkpoint_target()` con el stop parametrizado."""
    tp_checkpoint = None
    sl_checkpoint = None
    for point in path:
        price = float(point["price_sol"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("ACCOUNT_CHECKPOINT_PRICE_INVALID")
        return_pct = ((price - entry_price) / entry_price) * 100
        seconds = int(point["checkpoint_seconds"])
        if tp_checkpoint is None and return_pct >= 25:
            tp_checkpoint = seconds
        if sl_checkpoint is None and return_pct <= stop_pct:
            sl_checkpoint = seconds
    return int(
        tp_checkpoint is not None
        and (sl_checkpoint is None or tp_checkpoint < sl_checkpoint)
    )


def evaluate(rows, schema, test_fraction, label):
    """Entrena con el split y el modelo de siempre, cambiando solo la etiqueta."""
    schema = {**schema, "target": label}
    result = evaluate_strategy(rows, schema, test_fraction, "none")
    targets = np.asarray([row[label] for row in result["rows"]])
    probabilities = np.asarray(result["probabilities"])
    metrics = result["metrics"]
    return {
        "label": label,
        "threshold": result["threshold"],
        "train_rows": result["train_rows"],
        "test_rows": len(result["rows"]),
        "test_positives": int(targets.sum()),
        "roc_auc": metrics.get("roc_auc"),
        "average_precision": metrics.get("average_precision"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "base_rate": float(targets.mean()) if len(targets) else None,
        "probabilities": probabilities,
        "targets": targets,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", default="exports/account_checkpoint_live.json")
    parser.add_argument("--paths", default="exports/checkpoint_paths.json")
    parser.add_argument(
        "--schema", default="training/schema_account_checkpoints_v1.json"
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--cost-per-side", type=float, default=0.01)
    args = parser.parse_args()

    schema = load_json(args.schema)
    rows = validate_dataset(load_json(args.dataset), schema)
    paths = {
        int(row["signal_id"]): row
        for row in load_json(args.paths)["rows"]
    }

    # ---------------------------------------------------------------- etapa 1
    flips = 0
    resolved = 0
    unresolved = 0
    ambiguous = 0
    usable = []
    for row in rows:
        record = paths.get(int(row["signal_id"]))
        if record is None or not record["checkpoint_path"]:
            continue
        entry = float(record["price_at_signal"])
        path = record["checkpoint_path"]
        sl10 = int(row["target_tp25_before_sl10"])
        sl20 = checkpoint_target(entry, path, -20)
        row["target_tp25_before_sl20"] = sl20
        flips += int(sl10 != sl20)

        simulation = simulate_position(entry, path)
        pnl = position_pnl(simulation, args.cost_per_side)
        # Solo es una operación resuelta si la política la cerró entera: con
        # el 25% final abierto no hay PnL definitivo que etiquetar.
        closed = simulation["remaining"] <= 1e-9
        row["policy_pnl"] = pnl["net_realized"]
        row["target_policy_pnl_positive"] = int(pnl["net_realized"] > 0)
        row["policy_resolved"] = int(closed)
        resolved += int(closed)
        unresolved += int(not closed)
        ambiguous += int(is_ambiguous(entry, path))
        usable.append(row)

    sl10_rate = sum(r["target_tp25_before_sl10"] for r in usable) / len(usable)
    sl20_rate = sum(r["target_tp25_before_sl20"] for r in usable) / len(usable)

    print("=" * 74)
    print("1. RE-ETIQUETADO CON EL STOP REAL (-20%)")
    print("=" * 74)
    print(f"  filas con trayectoria      {len(usable)}")
    print(f"  tasa base con SL10         {sl10_rate:.1%}")
    print(f"  tasa base con SL20         {sl20_rate:.1%}")
    print(f"  etiquetas que cambian      {flips} ({flips/len(usable):.1%})")
    print("  (relajar el stop solo puede convertir 0 en 1, nunca al reves)")
    print(f"  trayectorias ambiguas      {ambiguous} "
          f"({ambiguous/len(usable):.1%})")

    print()
    print("  Misma trayectoria, mismo split, mismo modelo. Solo la etiqueta cambia:")
    header = (f"  {'etiqueta':<28} {'base':>7} {'AUC':>7} {'AP':>7} "
              f"{'bal.acc':>8} {'prec':>7} {'recall':>7} {'umbral':>7}")
    print(header)
    results = {}
    for label in ("target_tp25_before_sl10", "target_tp25_before_sl20"):
        report = evaluate(usable, schema, args.test_fraction, label)
        results[label] = report
        print(f"  {label:<28} {report['base_rate']:>6.1%} "
              f"{report['roc_auc']:>7.3f} {report['average_precision']:>7.3f} "
              f"{report['balanced_accuracy']:>8.3f} {report['precision']:>7.3f} "
              f"{report['recall']:>7.3f} {report['threshold']:>7.2f}")

    # ---------------------------------------------------------------- etapa 2
    print()
    print("=" * 74)
    print("2. OBJETIVO ECONOMICO: signo del PnL que la politica habria realizado")
    print("=" * 74)
    print(f"  operaciones cerradas por la politica   {resolved} "
          f"({resolved/len(usable):.1%})")
    print(f"  con el ultimo 25% todavia abierto      {unresolved} "
          f"({unresolved/len(usable):.1%})")
    print("  AVISO: mientras ese resto no tenga regla de cierre, el objetivo")
    print("  economico esta definido solo para las cerradas. Las abiertas se")
    print("  etiquetan con lo realizado hasta ahora, que las penaliza por las")
    print("  comisiones ya pagadas. No se inventa un cierre.")

    positives = sum(r["target_policy_pnl_positive"] for r in usable)
    print(f"  tasa base del objetivo economico       {positives/len(usable):.1%}")

    closed_rows = [r for r in usable if r["policy_resolved"]]
    if closed_rows:
        closed_positives = sum(
            r["target_policy_pnl_positive"] for r in closed_rows
        )
        print(f"  tasa base solo en cerradas             "
              f"{closed_positives/len(closed_rows):.1%} "
              f"(n={len(closed_rows)})")

    print()
    print("  Concordancia entre etiquetas y economia:")
    for label in ("target_tp25_before_sl10", "target_tp25_before_sl20"):
        agree = sum(
            int(r[label] == r["target_policy_pnl_positive"]) for r in usable
        )
        false_win = sum(
            int(r[label] == 1 and r["target_policy_pnl_positive"] == 0)
            for r in usable
        )
        print(f"    {label:<28} coincide {agree/len(usable):>6.1%}   "
              f"etiquetadas ganadoras pero economicamente negativas: {false_win}")

    print()
    print("  Mismo split y modelo, entrenando contra el objetivo economico:")
    print(header)
    report = evaluate(usable, schema, args.test_fraction,
                      "target_policy_pnl_positive")
    print(f"  {'target_policy_pnl_positive':<28} {report['base_rate']:>6.1%} "
          f"{report['roc_auc']:>7.3f} {report['average_precision']:>7.3f} "
          f"{report['balanced_accuracy']:>8.3f} {report['precision']:>7.3f} "
          f"{report['recall']:>7.3f} {report['threshold']:>7.2f}")

    out = "exports/account_checkpoint_relabeled.json"
    json.dump(usable, open(out, "w", encoding="utf-8"))
    print()
    print(f"dataset re-etiquetado guardado en {out} (diagnostico, no promocion)")


if __name__ == "__main__":
    main()
