"""Elimina del `<style>` inline solo las reglas que el navegador probó muertas.

Entrada: `output/ui-baseline/full_dead.json`, producido por `ui_baseline.py` a
partir del CSSOM real de Chrome. Una regla entra ahí únicamente si TODAS sus
declaraciones, para TODOS sus selectores y dentro de su mismo contexto de
media, están redeclaradas por `app-v6.css`, que gana por ir después con igual
especificidad.

Deliberadamente NO toca las reglas mixtas —las que tienen algunas
declaraciones pisadas y otras vivas—. Quitar declaraciones sueltas de una
regla escrita con atajos es donde se rompen cosas: `background:` expande a
seis longhand y puede estar pisada solo en una.

    python scripts/prune_dead_css.py --dry-run
    python scripts/prune_dead_css.py --apply

El resultado se verifica después con `ui_baseline.py --compare`, que compara
propiedad por propiedad las 30 vistas. Si el parseo de acá se equivocara, esa
comparación lo delata.
"""

import argparse
import json
import pathlib
import re

TARGET = pathlib.Path("static/index.html")
DEAD = pathlib.Path("output/ui-baseline/full_dead.json")


COMMENT = re.compile(r"/\*.*?\*/", re.S)


def split_comments(prelude):
    """Separa los comentarios del selector.

    En el fuente los encabezados de sección van pegados al selector que sigue
    (`/* ===== V5 ===== */\\n:root`). Para comparar hay que quitarlos, y al
    borrar una regla conviene devolverlos al archivo: rotulan secciones que
    pueden seguir teniendo reglas vivas debajo.
    """
    comments = COMMENT.findall(prelude)
    return "".join(comments), COMMENT.sub("", prelude).strip()


def normalise(selector):
    """Compara selectores sin depender del espaciado ni de los comentarios."""
    _, selector = split_comments(selector)
    parts = [re.sub(r"\s+", " ", p).strip() for p in selector.split(",")]
    return tuple(sorted(p for p in parts if p))


def split_blocks(css):
    """Devuelve (inicio, fin, prelude, cuerpo) de cada bloque de nivel actual."""
    blocks = []
    depth = 0
    start = 0
    brace = None
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                brace = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append((start, index + 1,
                               css[start:brace].strip(),
                               css[brace + 1:index]))
                start = index + 1
    return blocks


def prune(css, targets):
    """Quita los bloques cuyo índice de posición esté en `targets`.

    Emparejar por selector no sirve: 43 selectores aparecen más de una vez
    dentro del propio inline, y solo algunas de esas repeticiones están
    muertas. La posición sí es unívoca, y el orden del CSSOM coincide con el
    del fuente si se recorre igual: al entrar en un `@media`, sus reglas
    internas se numeran en el lugar donde está el `@media`.
    """
    removed = []
    out = []
    cursor = 0
    counter = [0]

    def take(prelude):
        """True si hay que borrar el bloque. Devuelve los comentarios aparte."""
        index = counter[0]
        counter[0] += 1
        comments, selector = split_comments(prelude)
        if index not in targets:
            return False, ""
        expected = targets[index]
        if normalise(selector) != normalise(expected):
            raise SystemExit(
                f"Desalineación en la posición {index}: el fuente dice "
                f"{selector[:60]!r} y el navegador {expected[:60]!r}. "
                "Abortado sin escribir nada."
            )
        removed.append(selector)
        return True, comments

    for begin, end, prelude, body in split_blocks(css):
        out.append(css[cursor:begin])
        cursor = end
        if prelude.startswith("@media"):
            inner = []
            inner_cursor = 0
            for i_begin, i_end, i_prelude, _ in split_blocks(body):
                inner.append(body[inner_cursor:i_begin])
                inner_cursor = i_end
                drop, comments = take(i_prelude)
                if drop:
                    inner.append(comments)
                else:
                    inner.append(body[i_begin:i_end])
            inner.append(body[inner_cursor:])
            rebuilt = "".join(inner)
            # Un @media que queda sin reglas se va entero.
            if rebuilt.strip():
                out.append(prelude + "{" + rebuilt + "}")
            continue
        if prelude.startswith("@"):
            out.append(css[begin:end])
            continue
        drop, comments = take(prelude)
        if drop:
            out.append(comments)
        else:
            out.append(css[begin:end])
    out.append(css[cursor:])
    print(f"bloques recorridos en el fuente: {counter[0]}")
    return "".join(out), removed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (args.apply or args.dry_run):
        parser.error("elegí --dry-run o --apply")

    html = TARGET.read_text(encoding="utf-8")
    head, rest = html.split("<style>", 1)
    css, tail = rest.split("</style>", 1)

    dead = json.loads(DEAD.read_text(encoding="utf-8"))
    targets = {item["index"]: item["selector"] for item in dead}

    pruned, removed = prune(css, targets)

    print(f"reglas marcadas como muertas: {len(dead)}")
    print(f"reglas efectivamente quitadas: {len(removed)}")
    missing = len(dead) - len(removed)
    if missing:
        print(f"  AVISO: {missing} no se encontraron en el fuente "
              f"(selector normalizado distinto); quedan intactas.")
    before_lines = css.count("\n") + 1
    after_lines = pruned.count("\n") + 1
    print(f"líneas CSS inline: {before_lines} -> {after_lines} "
          f"({before_lines - after_lines} menos)")
    print(f"bytes: {len(css)} -> {len(pruned)} ({len(css) - len(pruned)} menos)")

    if args.apply:
        TARGET.write_text(head + "<style>" + pruned + "</style>" + tail,
                          encoding="utf-8")
        print(f"\naplicado sobre {TARGET}")
        pathlib.Path("output/ui-baseline/removed_rules.json").write_text(
            json.dumps(sorted(removed), indent=1), encoding="utf-8"
        )
    else:
        print("\n(dry-run: no se escribió nada)")
        for selector in removed[:15]:
            print("   -", re.sub(r"\s+", " ", selector)[:70])


if __name__ == "__main__":
    main()
