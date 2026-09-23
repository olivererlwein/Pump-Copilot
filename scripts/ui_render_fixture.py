"""Ejercita el renderer de posiciones con fixtures, sin tocar datos reales.

La regla del entorno de ejecución es la garantía más cara del frontend: REAL
solo puede aparecer con un dato explícito e inequívoco, y una ausencia de
información nunca debe parecer dinero real. Probarlo exige una posición que
hoy no existe en producción.

Se ejercita la MISMA función que usa producción —`positionCard()` dentro de la
página cargada— pasándole objetos de prueba, y se fuerza `EXECUTION_ENV` a
cada estado. Nada se escribe en SQLite, no se altera ninguna respuesta de
producción y las fixtures viven solo en la memoria de la pestaña durante la
comprobación.

    python scripts/ui_render_fixture.py
"""

import asyncio
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ui_baseline import BASE, Chrome, app_token, serve_local  # noqa: E402

# Una posición cualquiera: los valores no importan, importa cómo se marca.
FIXTURE = {
    "mint": "FIXTUREmint1111111111111111111111111111111",
    "traders": ["fixture-trader"],
    "origin_trader": "fixture-trader",
    "entry_mc": 100.0,
    "current_mc": 125.0,
    "stake_usd": 5.0,
    "status": "open",
    "pnl_usd": 1.25,
    "realized_pnl_usd": 0.0,
    "unrealized_pnl_usd": 1.25,
    "remaining_pct": 1.0,
    "tp_stage": 1,
    "decision": "COPY",
    "score": 84,
    "opened_ts": 1790000000,
    "last_action": "FIXTURE",
}

CASES = (
    # (entorno, debe aparecer, no debe aparecer, clase de superficie)
    ({"mode": "paper", "explicit": True}, "PAPER", "MODO SIN CONFIRMAR", "env-paper"),
    ({"mode": "unknown", "explicit": False}, "MODO SIN CONFIRMAR", "PAPER", "env-unknown"),
    # Un entorno que se autodeclara real pero SIN confirmación por fila debe
    # seguir cayendo al estado conservador: es el caso que no puede fallar.
    ({"mode": "real", "explicit": False}, "MODO SIN CONFIRMAR", "PAPER", "env-unknown"),
)

COVERAGE_CASES = (
    ({}, {}, True, "warn", "parcial"),
    (
        {"silent": None},
        {"withheld_incomplete_coverage": "", "saturated_wallets": []},
        True,
        "warn",
        "parcial",
    ),
    (
        {"silent": 0},
        {"withheld_incomplete_coverage": 0, "saturated_wallets": []},
        False,
        "ok",
        "0 mudas",
    ),
    (
        {"silent": 0},
        {"withheld_incomplete_coverage": 1, "saturated_wallets": []},
        False,
        "down",
        "0 mudas · 1 retenidas",
    ),
)


async def run():
    chrome = Chrome(port=9356)
    profile = pathlib.Path(os.environ["TEMP"]) / f"fixture-{int(time.time())}"
    url = chrome.launch(str(profile))
    failures = []
    try:
        await chrome.connect(url)
        await chrome.send("Page.enable")
        await chrome.send("Runtime.enable")
        await serve_local(chrome)
        token = app_token()
        await chrome.send("Page.navigate", url=BASE + "/static/index.html")
        await asyncio.sleep(1.5)
        await chrome.evaluate(
            f'localStorage.setItem("pump_token",{json.dumps(token)});'
            'localStorage.setItem("pump_theme","dark");'
        )
        await chrome.send("Page.navigate", url=BASE + "/static/index.html")
        await asyncio.sleep(6)

        # Se congela el refresco para que nada del panel reescriba el entorno
        # mientras se ejercitan las fixtures.
        await chrome.evaluate("window.refresh=function(){};true")

        for env, expected, forbidden, surface in CASES:
            html = await chrome.evaluate(
                "(()=>{const prev=EXECUTION_ENV;"
                f"EXECUTION_ENV={json.dumps(env)};"
                f"const out=positionCard({json.dumps(FIXTURE)});"
                "EXECUTION_ENV=prev;return out})()"
            )
            name = env["mode"]
            if expected not in html:
                failures.append(f"{name}: falta {expected!r}")
            if forbidden in html:
                failures.append(f"{name}: aparece {forbidden!r} y no debería")
            if surface not in html:
                failures.append(f"{name}: falta la superficie {surface!r}")
            # La garantía dura: REAL no puede salir sin confirmación por fila.
            if "env-tag real" in html:
                failures.append(f"{name}: marcó REAL sin dato que lo confirme")
            state = "OK" if not failures or not failures[-1].startswith(name) else "FALLA"
            print(f"  entorno {name:<8} -> {expected:<20} superficie {surface:<12} {state}")

        for wallets, fallback, partial, state, label in COVERAGE_CASES:
            result = json.loads(await chrome.evaluate(
                "JSON.stringify(coverageLampState("
                f"{json.dumps(wallets)},{json.dumps(fallback)}))"
            ))
            if result != {"partial": partial, "state": state, "label": label}:
                failures.append(
                    f"coverage: {result!r} != "
                    f"{(partial, state, label)!r}"
                )
            print(
                f"  cobertura {state:<5} -> {label:<20} "
                f"{'OK' if result['state'] == state else 'FALLA'}"
            )

        palette = json.loads(await chrome.evaluate("""
          (()=>{
            const host=document.createElement("div");
            host.innerHTML='<div class="status online"><span class="dot"></span></div>'+
              '<div class="system-metric"><b class="healthy">en vivo</b></div>'+
              '<div class="shadow-state ready">listo</div>'+
              '<div class="shadow-versus"><b class="candidate">Candidato</b></div>';
            document.body.append(host);
            const blue=getComputedStyle(document.documentElement)
              .getPropertyValue("--blue").trim();
            const colors=[getComputedStyle(host.querySelector(".dot")).backgroundColor,
              ...[...host.querySelectorAll(".healthy,.ready,.candidate")]
                .map(element=>getComputedStyle(element).color)];
            const marker=document.createElement("span");
            marker.style.color=blue;
            host.append(marker);
            const expected=getComputedStyle(marker).color;
            host.remove();
            return JSON.stringify({expected,colors});
          })()
        """))
        if any(color != palette["expected"] for color in palette["colors"]):
            failures.append(f"palette: {palette!r}")
        print("  estados informativos -> azul del sistema      "
              + ("OK" if palette["colors"] == [palette["expected"]] * 4 else "FALLA"))

        # Y que el entorno real de la página siga siendo el que afirma el backend.
        live = await chrome.evaluate("JSON.stringify(EXECUTION_ENV)")
        print(f"\n  entorno leído de /api/status: {live}")
    finally:
        chrome.close()

    if failures:
        print("\nFALLAS:")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print("\nTodas las fixtures pasan. Ningún dato escrito en ninguna parte.")


if __name__ == "__main__":
    asyncio.run(run())
