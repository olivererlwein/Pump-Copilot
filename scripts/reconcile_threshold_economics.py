"""Reconcilia la precisión del clasificador con la del backtest económico.

El reporte del clasificador informa la precisión en un umbral fijo (0.5)
mientras que el backtest usa el umbral elegido por validación cruzada. Si
ambos cruzan el punto de equilibrio del proxy de pago, la conclusión
económica cambia de signo según cuál se mire, y hay que poder ver el salto
fila por fila en vez de creerle a un número suelto.

Reutiliza el split, el modelo y la simulación de los scripts existentes: no
reimplementa nada, para que la comparación sea contra lo que realmente corre.

    python scripts/reconcile_threshold_economics.py \
        --dataset exports/account_checkpoint_live.json \
        --schema training/schema_account_checkpoints_v1.json

Solo diagnóstico: no entrena de nuevo, no guarda artifacts, no cambia umbrales.
"""

import argparse

import numpy as np

if __package__:
    from scripts.compare_model_economics import evaluate_strategy, simulate_payoff
    from scripts.train_baseline_model import load_json, validate_dataset
else:
    from compare_model_economics import evaluate_strategy, simulate_payoff
    from train_baseline_model import load_json, validate_dataset

WIN_RETURN = 0.25
LOSS_RETURN = -0.10


def breakeven_precision(cost):
    """Precisión mínima para no perder: w*0.25 - (1-w)*0.10 - coste = 0."""
    return (abs(LOSS_RETURN) + cost) / (WIN_RETURN + abs(LOSS_RETURN))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", default="exports/account_checkpoint_live.json")
    parser.add_argument(
        "--schema", default="training/schema_account_checkpoints_v1.json"
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--strategy", default="none")
    parser.add_argument(
        "--costs", type=float, nargs="+", default=[0.0, 0.02, 0.03, 0.05]
    )
    args = parser.parse_args()

    schema = load_json(args.schema)
    rows = validate_dataset(load_json(args.dataset), schema)
    target = schema["target"]

    result = evaluate_strategy(rows, schema, args.test_fraction, args.strategy)
    test_rows = result["rows"]
    probabilities = np.asarray(result["probabilities"])
    targets = np.asarray([row[target] for row in test_rows])
    tuned = float(result["threshold"])

    print("=" * 72)
    print("POBLACION (identica para ambas metricas)")
    print("=" * 72)
    print(f"  estrategia            {args.strategy}")
    print(f"  filas totales         {len(rows)}")
    print(f"  train / test / purga  {result['train_rows']} / "
          f"{len(test_rows)} / {result['purged_rows']}")
    print(f"  positivos en holdout  {int(targets.sum())} "
          f"({100 * targets.mean():.1f}% tasa base)")
    print(f"  umbral elegido por CV {tuned}")
    print("  el backtest consume exactamente estas probabilidades de holdout")

    print()
    print("=" * 72)
    print("EL SALTO: misma poblacion, distinto umbral")
    print("=" * 72)
    header = (f"{'umbral':>7} {'selecc':>7} {'aciertos':>9} {'precision':>10} "
              f"{'recall':>8} {'bruto':>8}")
    for cost in args.costs:
        header += f" {'neto@' + format(cost, '.0%'):>10}"
    print(header)

    interesting = sorted({round(tuned, 4), 0.5, 0.45, 0.55, 0.6, 0.65, 0.7})
    for threshold in interesting:
        predictions = (probabilities >= threshold).astype(int)
        n = int(predictions.sum())
        if n == 0:
            print(f"{threshold:>7.3f} {n:>7}   (ninguna senal seleccionada)")
            continue
        wins = int(targets[predictions == 1].sum())
        precision = wins / n
        recall = wins / max(1, int(targets.sum()))
        gross = wins * WIN_RETURN + (n - wins) * LOSS_RETURN
        line = (f"{threshold:>7.3f} {n:>7} {wins:>9} {precision:>9.1%} "
                f"{recall:>7.1%} {gross:>8.2f}")
        for cost in args.costs:
            line += f" {gross - n * cost:>10.2f}"
        mark = ""
        if abs(threshold - tuned) < 1e-9:
            mark += "  <- usado por el backtest"
        if abs(threshold - 0.5) < 1e-9:
            mark += "  <- reportado por el clasificador"
        print(line + mark)

    print()
    print("=" * 72)
    print("PUNTO DE EQUILIBRIO POR COSTE")
    print("=" * 72)
    for cost in args.costs:
        print(f"  coste {cost:>5.0%}  ->  necesita precision >= "
              f"{breakeven_precision(cost):.1%}")

    print()
    print("=" * 72)
    print("EFECTO DE LA REGLA DE UNA POSICION (15 min)")
    print("=" * 72)
    for threshold in (tuned, 0.5):
        predictions = (probabilities >= threshold).astype(int)
        for hold, name in ((0, "todas"), (900, "una posicion 15m")):
            payoff = simulate_payoff(
                test_rows, predictions, target,
                round_trip_cost=0.02, hold_seconds=hold,
            )
            precision = payoff["precision"]
            print(f"  umbral {threshold:.3f} {name:<18} "
                  f"selecc {payoff['selected']:>3} ejec {payoff['executed']:>3} "
                  f"omitidas {payoff['skipped_busy']:>3} "
                  f"precision {precision if precision is None else format(precision, '.1%'):>6} "
                  f"neto@2% {payoff['net_return_units']:>7.2f}")


if __name__ == "__main__":
    main()
