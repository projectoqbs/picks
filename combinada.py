"""
Constructor de combinada por cuota objetivo.

Le decis una cuota (p.ej. 4) y arma, con los partidos de un dia, la
combinada de 2 a 4 selecciones que:
  - el modelo ve como lo mas probable,
  - y cuya cuota combinada queda cerca de la que pediste.

Usa las predicciones que ya genero la app (docs/data/AAAA-MM-DD.json),
asi que no reentrena nada.

Cuotas: por defecto usa las "cuotas justas" del modelo (1 / probabilidad).
OJO: con cuotas justas, una combinada de cuota 4 tiene ~25% de
probabilidad POR DEFINICION (1/4). El modelo no la hace mas probable de
lo que la cuota indica; solo elige sus selecciones mas firmes para
llegar a ese nivel de riesgo con el menor numero de patas.
Para comparar contra cuotas reales (BetPlay, etc.) pasa --cuotas con un
CSV  local,visitante,cuota_1,cuota_X,cuota_2[,cuota_over25,cuota_under25]
y ahi si el ranking por probabilidad del modelo tiene sentido de "valor".

Uso:
    python combinada.py --dias                       lista dias disponibles
    python combinada.py 2026-09-05 --objetivo 4
    python combinada.py 2026-09-05 --objetivo 4 --min-prob 0.6 --patas 2-3 --top 5
    python combinada.py 2026-09-05 --objetivo 4 --cuotas cuotas_dia.csv
"""
import csv
import json
import os
import sys
import itertools

DIR_DATA = os.getenv("DIR_DATA", os.path.join("docs", "data"))
OBJETIVO = 4.0
TOLERANCIA = 0.15          # +-15% de la cuota objetivo (4 -> 3.4 a 4.6)
PATAS = (2, 4)
MIN_PROB = 0.55
TOP = 8
MAX_CAND = 28             # patas candidatas para acotar la combinatoria


def cargar_dia(dia):
    path = os.path.join(DIR_DATA, f"{dia}.json")
    if not os.path.exists(path):
        raise SystemExit(f"No existe {path}. Corre antes actualizar.py + "
                         f"generar_predicciones.py, o usa --dias.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["partidos"]


def cargar_cuotas_csv(path):
    """{ (local.lower(), visitante.lower()) -> {sel: cuota} }"""
    m = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            k = ((row.get("local") or "").strip().lower(),
                 (row.get("visitante") or "").strip().lower())
            d = {}
            for sel, col in (("1", "cuota_1"), ("X", "cuota_X"), ("2", "cuota_2"),
                             ("Over 2.5", "cuota_over25"), ("Under 2.5", "cuota_under25")):
                try:
                    v = float(str(row.get(col, "")).replace(",", "."))
                    if v > 1.0:
                        d[sel] = v
                except (TypeError, ValueError):
                    pass
            if d:
                m[k] = d
    return m


def patas_de_partido(p, cuotas_reales):
    """Lista de (etiqueta, seleccion, prob_modelo, cuota_usada, cuota_real)."""
    pr = p["prediccion"]
    if not pr:
        return []
    et = f"{p['local']} vs {p['visitante']}"
    reales = cuotas_reales.get((p["local"].lower(), p["visitante"].lower()), {})
    opciones = [
        ("1", pr["prob_1"]),
        ("X", pr["prob_X"]),
        ("2", pr["prob_2"]),
        ("Over 2.5", pr["prob_over_2_5"]),
        ("Under 2.5", round(1 - pr["prob_over_2_5"], 3)),
        ("Ambos marcan", pr["prob_btts"]),
        ("No marcan ambos", round(1 - pr["prob_btts"], 3)),
    ]
    patas = []
    for sel, prob in opciones:
        if prob <= 0:
            continue
        justa = round(1.0 / prob, 2)
        real = reales.get(sel)
        patas.append((et, sel, prob, real if real else justa, real))
    return patas


def construir(partidos, objetivo=OBJETIVO, tol=TOLERANCIA, patas_rng=PATAS,
              min_prob=MIN_PROB, cuotas_reales=None):
    cuotas_reales = cuotas_reales or {}
    modo_valor = bool(cuotas_reales)
    cand = []
    for i, p in enumerate(partidos):
        for et, sel, prob, cuota, real in patas_de_partido(p, cuotas_reales):
            if prob < min_prob:
                continue
            if modo_valor and real is None:
                continue          # en modo valor solo patas con cuota real
            cand.append({"i": i, "et": et, "sel": sel, "prob": prob,
                         "cuota": cuota, "real": real})
    cand.sort(key=lambda x: x["prob"], reverse=True)
    cand = cand[:MAX_CAND]

    lo, hi = objetivo * (1 - tol), objetivo * (1 + tol)
    pmin, pmax = patas_rng
    out = []
    for n in range(pmin, pmax + 1):
        for combo in itertools.combinations(cand, n):
            if len({c["i"] for c in combo}) != n:      # 1 pata por partido
                continue
            cuota = 1.0
            prob = 1.0
            for c in combo:
                cuota *= c["cuota"]
                prob *= c["prob"]
            if not (lo <= cuota <= hi):
                continue
            pmin_leg = min(c["prob"] for c in combo)
            out.append({"combo": combo, "n": n, "cuota": cuota, "prob": prob,
                        "prob_min_pata": pmin_leg,
                        "dist": abs(cuota - objetivo)})
    # con cuotas justas prob ~ 1/cuota, asi que "la mas probable que igual
    # llega a ~objetivo" = la mas cercana al objetivo. A igualdad: menos
    # patas y weakest-link mas fuerte (la pata mas floja lo mas alta posible).
    out.sort(key=lambda x: (x["dist"], x["n"], -x["prob_min_pata"]))
    return out


def imprimir(combos, objetivo, hay_reales, top=TOP):
    if not combos:
        print(f"\nNo se pudo armar ninguna combinada cerca de cuota {objetivo} "
              f"con esos filtros. Proba bajar --min-prob o subir la tolerancia.")
        return
    print(f"\nObjetivo cuota ~{objetivo}. {len(combos)} combinaciones posibles. "
          f"Top {min(top, len(combos))} (mas probable primero):\n")
    for r, c in enumerate(combos[:top], 1):
        cab = (f"#{r}  {c['n']} patas | cuota {c['cuota']:.2f} | "
               f"prob modelo {c['prob']*100:.1f}%")
        if hay_reales:
            cab += f" | edge {(c['prob']*c['cuota']-1)*100:+.0f}%"
        print(cab)
        for p in c["combo"]:
            linea = f"     - {p['et']}  ->  {p['sel']:<15} prob {p['prob']*100:.0f}%"
            if p["real"]:
                linea += f"  @ {p['real']:.2f} (real)"
            else:
                linea += f"  @ {p['cuota']:.2f} (justa)"
            print(linea)
        print()
    if not hay_reales:
        print("Nota: cuotas = las justas del modelo (1/prob). Una combinada de")
        print(f"cuota {objetivo:g} tiene ~{100/objetivo:.0f}% de prob por definicion; el")
        print("modelo solo elige las patas mas firmes para llegar ahi. Para")
        print("evaluar valor real, pasa --cuotas con las cuotas de tu casa.")
    else:
        print("Modo valor: solo patas con cuota real. 'edge' = prob_modelo*cuota-1;")
        print("recorda que el backtest dio edge negativo en simple, y una combinada")
        print("lo empeora. Elegi el edge menos negativo, no esperes ganancia.")


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--dias":
        if os.path.isdir(DIR_DATA):
            ds = sorted(f[:-5] for f in os.listdir(DIR_DATA)
                        if len(f) == 15 and f.endswith(".json"))
            print("\n".join(ds) or "(no hay dias generados)")
        else:
            print(f"No existe {DIR_DATA}")
        return

    dia = args[0]
    objetivo = OBJETIVO
    tol = TOLERANCIA
    patas_rng = PATAS
    min_prob = MIN_PROB
    top = TOP
    cuotas_reales = {}
    if "--objetivo" in args:
        objetivo = float(args[args.index("--objetivo") + 1])
    if "--tol" in args:
        tol = float(args[args.index("--tol") + 1])
    if "--patas" in args:
        a, b = args[args.index("--patas") + 1].split("-")
        patas_rng = (int(a), int(b))
    if "--min-prob" in args:
        min_prob = float(args[args.index("--min-prob") + 1])
    if "--top" in args:
        top = int(args[args.index("--top") + 1])
    if "--cuotas" in args:
        cuotas_reales = cargar_cuotas_csv(args[args.index("--cuotas") + 1])

    partidos = cargar_dia(dia)
    combos = construir(partidos, objetivo=objetivo, tol=tol, patas_rng=patas_rng,
                       min_prob=min_prob, cuotas_reales=cuotas_reales)
    imprimir(combos, objetivo, hay_reales=bool(cuotas_reales), top=top)


if __name__ == "__main__":
    _cli()
