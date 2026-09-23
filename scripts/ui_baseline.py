"""Captura el estado visual del frontend y qué CSS gana de verdad.

La Fase 1 del rediseño elimina CSS muerto, y "muerto" tiene que demostrarse,
no suponerse. Dos hojas pueden compartir un selector y seguir vivas las dos si
declaran propiedades distintas: `.card{padding;radius}` inline contra
`.card{padding}` externo deja el radio inline en pie. Comparar selectores
llevaría a borrar reglas que sí pintan.

Por eso acá no se parsea CSS a mano: se le pregunta al navegador. Chrome ya
resolvió cascada, especificidad y orden, así que su CSSOM y su
`getComputedStyle` son la única autoridad que no se equivoca.

Maneja Chrome headless por CDP sobre el paquete `websockets` que el proyecto ya
usa. No instala nada.

    python scripts/ui_baseline.py --label antes
    python scripts/ui_baseline.py --label despues
    python scripts/ui_baseline.py --compare antes despues

Solo lectura sobre producción: entra con el token, navega las cinco vistas y
mide. No envía nada.
"""

import argparse
import asyncio
import base64
import collections
import json
import os
import pathlib
import subprocess
import time
import urllib.request

import websockets

BASE = "https://web-production-4ea0d.up.railway.app"
OUT = pathlib.Path("output/ui-baseline")
VIEWS = ("home", "signals", "paper", "activity", "traders")
BREAKPOINTS = (("desktop", 1440, 900), ("tablet", 860, 1000), ("mobile", 390, 844))
THEMES = ("dark", "light")

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

# Propiedades que deciden si algo se ve igual. La lista completa de
# getComputedStyle trae cientos de valores derivados que cambian con el
# tamaño de la ventana y producirían falsos positivos.
TRACKED = [
    "display", "position", "color", "background-color", "background-image",
    "border-top-width", "border-right-width", "border-bottom-width",
    "border-left-width", "border-top-color", "border-left-color",
    "border-top-left-radius", "border-bottom-right-radius",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "font-family", "font-size", "font-weight", "line-height",
    "letter-spacing", "text-transform", "text-align",
    "flex-direction", "justify-content", "align-items", "gap",
    "grid-template-columns", "width", "height", "max-width", "min-height",
    "opacity", "box-shadow", "overflow-x", "overflow-y", "z-index",
    "transform", "visibility",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    raise SystemExit("No se encontró Chrome ni Edge")


def app_token():
    for line in open(".env", encoding="utf-8"):
        if line.startswith("APP_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("APP_TOKEN no está en .env")


class Chrome:
    def __init__(self, port=9222):
        self.port = port
        self.process = None
        self.socket = None
        self.message_id = 0

    def launch(self, profile):
        self.process = subprocess.Popen(
            [
                find_chrome(),
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Hace falta el socket de la PESTAÑA, no el del navegador: el del
        # navegador solo expone los dominios Browser y Target.
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2
                ) as response:
                    targets = json.load(response)
                page = next(
                    (t for t in targets
                     if t.get("type") == "page" and t.get("webSocketDebuggerUrl")),
                    None,
                )
                if page:
                    return page["webSocketDebuggerUrl"]
            except Exception:
                pass
            time.sleep(0.5)
        raise SystemExit("Chrome no expuso ninguna pestaña depurable")

    async def connect(self, url):
        self.socket = await websockets.connect(url, max_size=80_000_000)

    async def send(self, method, **params):
        self.message_id += 1
        message_id = self.message_id
        await self.socket.send(
            json.dumps({"id": message_id, "method": method, "params": params})
        )
        while True:
            payload = json.loads(await self.socket.recv())
            if payload.get("id") == message_id:
                if "error" in payload:
                    raise RuntimeError(f"{method}: {payload['error']}")
                return payload.get("result", {})

    async def evaluate(self, expression):
        result = await self.send(
            "Runtime.evaluate", expression=expression,
            returnByValue=True, awaitPromise=True,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"].get("text"))
        return result["result"].get("value")

    def close(self):
        if self.process:
            self.process.terminate()


FINGERPRINT_JS = """
(() => {
  const props = %s;
  const out = [];
  document.querySelectorAll("*").forEach((el, index) => {
    if (el.closest("[hidden]") || el.tagName === "SCRIPT"
        || el.tagName === "STYLE" || el.tagName === "LINK") return;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const key = el.tagName.toLowerCase()
      + (el.id ? "#" + el.id : "")
      + (el.className && typeof el.className === "string"
         ? "." + el.className.trim().split(/\\s+/).join(".") : "")
      + "@" + index;
    const values = {};
    props.forEach(p => { values[p] = style.getPropertyValue(p); });
    values["__box"] = [Math.round(rect.width), Math.round(rect.height)].join("x");
    out.push([key, values]);
  });
  return out;
})()
""" % json.dumps(TRACKED)

CSSOM_JS = """
(() => {
  const sheets = [];
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    const origin = sheet.ownerNode && sheet.ownerNode.tagName === "STYLE"
      ? "inline" : (sheet.href || "external");
    const collected = [];
    // Desde que Chrome soporta anidamiento, TODA CSSStyleRule expone un
    // `cssRules` vacío pero truthy. Preguntar por su existencia hacía que
    // cada regla recursara en la nada y no se coleccionara ninguna: hay que
    // mirar la longitud, y recolectar igual la regla que tiene selector.
    const walk = (list, media) => {
      for (const rule of list) {
        if (rule.cssRules && rule.cssRules.length) {
          walk(rule.cssRules, rule.conditionText || media);
        }
        if (!rule.selectorText) continue;
        const declarations = {};
        for (let i = 0; i < rule.style.length; i++) {
          const name = rule.style[i];
          declarations[name] = rule.style.getPropertyValue(name);
        }
        collected.push({ selector: rule.selectorText, media: media || "", declarations });
      }
    };
    walk(rules, "");
    sheets.push({ origin, rules: collected });
  }
  return sheets;
})()
"""


async def capture(chrome, label):
    token = app_token()
    directory = OUT / label
    directory.mkdir(parents=True, exist_ok=True)
    fingerprints = {}

    await chrome.send("Page.enable")
    await chrome.send("Runtime.enable")

    for theme in THEMES:
        for name, width, height in BREAKPOINTS:
            await chrome.send(
                "Emulation.setDeviceMetricsOverride",
                width=width, height=height, deviceScaleFactor=1,
                mobile=(name == "mobile"),
            )
            await chrome.send("Page.navigate", url=BASE + "/static/index.html")
            await asyncio.sleep(1.2)
            await chrome.evaluate(
                f'localStorage.setItem("pump_token",{json.dumps(token)});'
                f'localStorage.setItem("pump_theme",{json.dumps(theme)});'
            )
            await chrome.send("Page.navigate", url=BASE + "/static/index.html")
            # El primer refresh trae los ocho endpoints; darle margen.
            await asyncio.sleep(4.5)

            for view in VIEWS:
                await chrome.evaluate(f'showPage({json.dumps(view)})')
                await asyncio.sleep(0.7)
                key = f"{theme}-{name}-{view}"
                shot = await chrome.send(
                    "Page.captureScreenshot", format="png", captureBeyondViewport=True
                )
                (directory / f"{key}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
                fingerprints[key] = await chrome.evaluate(FINGERPRINT_JS)
                print(f"  capturado {key}")

    cssom = await chrome.evaluate(CSSOM_JS)
    (directory / "fingerprints.json").write_text(
        json.dumps(fingerprints), encoding="utf-8"
    )
    (directory / "cssom.json").write_text(json.dumps(cssom), encoding="utf-8")
    print(f"\nguardado en {directory}")
    return cssom


def analyse_cssom(cssom):
    """Qué declaración inline pierde de verdad: por propiedad, no por selector."""
    inline = next((s for s in cssom if s["origin"] == "inline"), None)
    external = [s for s in cssom if s["origin"] != "inline"]
    if inline is None:
        print("No hay hoja inline: ya está consolidado.")
        return

    # El externo gana con igual especificidad por ir después en el documento.
    wins = {}
    for sheet in external:
        for rule in sheet["rules"]:
            for selector in rule["selector"].split(","):
                for prop in rule["declarations"]:
                    wins.setdefault((selector.strip(), rule["media"]), set()).add(prop)

    dead = alive = 0
    dead_detail = {}
    alive_detail = {}
    for rule in inline["rules"]:
        for selector in rule["selector"].split(","):
            selector = selector.strip()
            covered = wins.get((selector, rule["media"]), set())
            for prop in rule["declarations"]:
                if prop in covered:
                    dead += 1
                    dead_detail.setdefault(selector, []).append(prop)
                else:
                    alive += 1
                    alive_detail.setdefault(selector, []).append(prop)

    print(f"\ndeclaraciones inline: {dead + alive}")
    print(f"  pisadas por el externo (muertas): {dead}")
    print(f"  todavía con efecto (VIVAS):       {alive}")
    print(f"\nselectores con declaraciones vivas: {len(alive_detail)}")
    for selector, props in sorted(
        alive_detail.items(), key=lambda x: -len(x[1])
    )[:20]:
        print(f"  {selector:<42} {', '.join(sorted(set(props))[:6])}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inline_alive.json").write_text(
        json.dumps({"alive": alive_detail, "dead": dead_detail}, indent=1),
        encoding="utf-8",
    )
    print(f"\ndetalle en {OUT / 'inline_alive.json'}")


def compare(before, after):
    a = json.loads((OUT / before / "fingerprints.json").read_text(encoding="utf-8"))
    b = json.loads((OUT / after / "fingerprints.json").read_text(encoding="utf-8"))
    total = 0
    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            print(f"{key}: vista ausente en una de las dos capturas")
            continue
        left = {item[0]: item[1] for item in a[key]}
        right = {item[0]: item[1] for item in b[key]}
        only = set(left) ^ set(right)
        diffs = []
        for element in set(left) & set(right):
            for prop, value in left[element].items():
                if right[element].get(prop) != value:
                    diffs.append((element, prop, value, right[element].get(prop)))
        if only or diffs:
            print(f"\n{key}: {len(diffs)} propiedades distintas, "
                  f"{len(only)} elementos sólo en una")
            for element, prop, x, y in diffs[:8]:
                print(f"    {element[:58]}  {prop}: {x} -> {y}")
        total += len(diffs) + len(only)
    print(f"\nDIFERENCIAS TOTALES: {total}")
    if total == 0:
        print("Idéntico en las cinco vistas, tres anchos y dos temas.")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--label")
    parser.add_argument("--compare", nargs=2, metavar=("ANTES", "DESPUES"))
    parser.add_argument("--analyse", metavar="LABEL")
    parser.add_argument(
        "--cssom", metavar="LABEL",
        help="Rehace solo el CSSOM, sin repetir las 30 capturas.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="A/B del CSS local contra el desplegado, misma carga de página.",
    )
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return
    if args.verify:
        chrome = Chrome(port=9341)
        profile = pathlib.Path(os.environ["TEMP"]) / f"pump-ver-{int(time.time())}"
        try:
            url = chrome.launch(str(profile))
            asyncio.run(_verify(chrome, url))
        finally:
            chrome.close()
        return
    if args.cssom:
        chrome = Chrome(port=9340)
        profile = pathlib.Path(os.environ["TEMP"]) / f"pump-css-{int(time.time())}"
        try:
            url = chrome.launch(str(profile))
            asyncio.run(collect_cssom(chrome, url, args.cssom))
        finally:
            chrome.close()
        return
    if args.analyse:
        analyse_cssom(json.loads(
            (OUT / args.analyse / "cssom.json").read_text(encoding="utf-8")
        ))
        return
    if not args.label:
        parser.error("hace falta --label, --compare o --analyse")

    chrome = Chrome()
    profile = pathlib.Path(os.environ["TEMP"]) / f"pump-ui-{int(time.time())}"
    try:
        url = chrome.launch(str(profile))
        asyncio.run(run(chrome, url, args.label))
    finally:
        chrome.close()


async def run(chrome, url, label):
    await chrome.connect(url)
    cssom = await capture(chrome, label)
    analyse_cssom(cssom)


async def verify_css(chrome, url):
    """A/B del CSS sobre la MISMA carga de página.

    La baseline salió de producción y el archivo local ya está modificado, así
    que comparar dos cargas distintas mezclaría cambios de datos con cambios de
    estilo. Acá se mide la misma página dos veces seguidas, intercambiando
    únicamente el contenido del `<style>` inline: cualquier diferencia que
    aparezca es atribuible al CSS y a nada más.
    """
    token = app_token()
    local = pathlib.Path("static/index.html").read_text(encoding="utf-8")
    new_css = local.split("<style>", 1)[1].split("</style>", 1)[0]
    new_ext = pathlib.Path("static/app-v6.css").read_text(encoding="utf-8")
    # El <link> se reemplaza por un <style> en su MISMA posición del DOM: el
    # orden de cascada depende de la posición, así que insertarlo en otro
    # lugar cambiaría qué regla gana y falsearía la comparación.
    swap = (
        "(()=>{const s=document.querySelector('style');"
        "window.__old=s.textContent;s.textContent=%s;"
        "const l=[...document.querySelectorAll('link[rel=stylesheet]')]"
        ".find(x=>x.href.includes('app-v6'));"
        "if(l){const n=document.createElement('style');n.id='__ext';"
        "n.textContent=%s;window.__link=l;l.replaceWith(n);}return true})()"
    )
    restore = (
        "(()=>{document.querySelector('style').textContent=window.__old;"
        "const n=document.getElementById('__ext');"
        "if(n&&window.__link)n.replaceWith(window.__link);return true})()"
    )

    await chrome.send("Page.enable")
    await chrome.send("Runtime.enable")
    total = 0
    changed = collections.Counter()

    for theme in THEMES:
        for name, width, height in BREAKPOINTS:
            await chrome.send(
                "Emulation.setDeviceMetricsOverride",
                width=width, height=height, deviceScaleFactor=1,
                mobile=(name == "mobile"),
            )
            await chrome.send("Page.navigate", url=BASE + "/static/index.html")
            await asyncio.sleep(1.2)
            await chrome.evaluate(
                f'localStorage.setItem("pump_token",{json.dumps(token)});'
                f'localStorage.setItem("pump_theme",{json.dumps(theme)});'
            )
            await chrome.send("Page.navigate", url=BASE + "/static/index.html")
            await asyncio.sleep(4.5)

            # El panel se refresca solo cada 5 s y vuelve a pintar el DOM
            # entero. Como los elementos se identifican por posición, un poll
            # entre la medición "antes" y la "después" cambiaría el contenido
            # y aparecería como diferencia de estilo. Se congela el refresco
            # para que lo único que varíe sea el CSS.
            await chrome.evaluate("window.refresh = function(){}; true")

            for view in VIEWS:
                await chrome.evaluate(f'showPage({json.dumps(view)})')
                await asyncio.sleep(0.5)
                before = await chrome.evaluate(FINGERPRINT_JS)
                await chrome.evaluate(
                    swap % (json.dumps(new_css), json.dumps(new_ext))
                )
                await asyncio.sleep(0.4)
                after = await chrome.evaluate(FINGERPRINT_JS)
                await chrome.evaluate(restore)

                left = {item[0]: item[1] for item in before}
                right = {item[0]: item[1] for item in after}
                diffs = []
                for element in set(left) & set(right):
                    for prop, value in left[element].items():
                        if right[element].get(prop) != value:
                            diffs.append((element, prop, value, right[element][prop]))
                only = set(left) ^ set(right)
                key = f"{theme}-{name}-{view}"
                for _, prop, _, _ in diffs:
                    changed[prop] += 1
                mark = "igual" if not diffs and not only else f"{len(diffs)} dif."
                print(f"  {key:<28} {len(left):>4} elementos  {mark}")
                total += len(diffs) + len(only)

    print(f"\nDIFERENCIAS TOTALES: {total}")
    if changed:
        # Un cambio de color que mueva `width` o `display` es regresión; que
        # mueva `color` es el trabajo. Agrupar por propiedad hace visible la
        # diferencia entre las dos cosas sin leer 4 millones de comparaciones.
        print("\nPROPIEDADES QUE CAMBIARON")
        layout = {"width", "height", "__box", "display", "position",
                  "grid-template-columns", "flex-direction", "justify-content",
                  "align-items", "overflow-x", "overflow-y", "z-index",
                  "transform", "visibility", "max-width", "min-height"}
        for prop, count in sorted(changed.items(), key=lambda x: -x[1]):
            flag = "  <-- LAYOUT" if prop in layout else ""
            print(f"  {prop:<28} {count:>6}{flag}")
        touched = set(changed) & layout
        print(f"\npropiedades de layout afectadas: "
              f"{', '.join(sorted(touched)) if touched else 'ninguna'}")
    return total


async def _verify(chrome, url):
    await chrome.connect(url)
    await verify_css(chrome, url)


async def collect_cssom(chrome, url, label):
    await chrome.connect(url)
    await chrome.send("Page.navigate", url=BASE + "/static/index.html")
    await asyncio.sleep(3)
    cssom = await chrome.evaluate(CSSOM_JS)
    directory = OUT / label
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cssom.json").write_text(json.dumps(cssom), encoding="utf-8")
    analyse_cssom(cssom)


if __name__ == "__main__":
    main()
