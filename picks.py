"""
Tipster de combinadas.

Lee un CSV de partidos proximos con sus cuotas, los puntua con el modelo
Poisson/Dixon-Coles (modelo.py, entrenado con futbol.db hasta hoy) y
arma las apuestas COMBINADAS de 2 a 4 selecciones cuya cuota combinada
cae en una banda (por defecto 2.0 a 4.0), ordenadas por la probabilidad
que les da el modelo.

IMPORTANTE / lo que dijo el backtest de ROI: este modelo predice bien
pero NO le gana al mercado en apuestas simples. Una combinada multiplica
el margen de la casa, asi que su valor esperado es NEGATIVO y peor
cuantas mas patas. Esta herramienta ordena y explica; no promete ganar.
Por eso cada combo muestra tambien su 'edge' (prob_modelo * cuota - 1):
mientras mas cerca de 0 (o menos negativo), menos te cobra la casa.

Formato del CSV de entrada (cabecera exacta; separador coma):

    liga,local,visitante,cuota_1,cuota_X,cuota_2[,cuota_over25,cuota_under25][,fecha]

  liga  : codigo interno (PL, PD, SA, BL1, FL1, BSA, ARG, COL, UEL, CL...)
  local / visitante : nombre del equipo tal como aparece en futbol.db
                      (usa  python picks.py --equipos PL  para verlos)
  cuotas: decimales. Las columnas over/under y fecha son opcionales.

Uso:
    python picks.py --plantilla                 escribe proximos_ejemplo.csv
    python picks.py --equipos PD                 lista equipos de esa liga
    python picks.py proximos.csv
    python picks.py proximos.csv --patas 2-3 --min-prob 0.55 --banda 2.0-3.5 --top 15
    python picks.py proximos.csv --ordenar valor
"""
import csv
import os
import sys
import itertools
from difflib import get_close_matches

from modelo import conectar, cargar_partidos, ajustar_liga

BANDA = (2.0, 4.0)
PATAS = (2, 4)
MIN_PROB_PATA = 0.50     # solo selecciones que el modelo ve >= 50%
TOP = 12
MAX_PATAS_CANDIDATAS = 30


def _equipos_liga(conn, liga):
    filas = cargar_partidos(conn, liga)
    return sorted({f[1] for f in filas} | {f[2] for f in filas})


def _resolver_equipo(nombre, validos, cache):
    if nombre in cache:
        return cache[nombre]
    if nombre in validos:
        cache[nombre] = nombre
        return nombre
    cand = get_close_matches(nombre, validos, n=1, cutoff=0.6)
    if not cand:
        raise SystemExit(
            f"Equipo no encontrado: '{nombre}'.\n"
            f"  Usa uno de la lista:  python picks.py --equipos <LIGA>"
        )
    print(f"  (aviso) '{nombre}' -> '{cand[0]}'")
    cache[nombre] = cand[0]
    return cand[0]


def leer_csv(path, conn):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        raise SystemExit(f"{path} vacio.")

    modelos, equipos_validos, cache_nombre = {}, {}, {}
    partidos = []
    for i, row in enumerate(filas):
        liga = (row.get("liga") or "").strip()
        if not liga:
            continue
        if liga not in modelos:
            modelos[liga] = ajustar_liga(liga, conn=conn, verbose=True)
            equipos_validos[liga] = _equipos_liga(conn, liga)
        m = modelos[liga]
        loc = _resolver_equipo((row.get("local") or "").strip(),
                               equipos_validos[liga], cache_nombre)
        vis = _resolver_equipo((row.get("visitante") or "").strip(),
                               equipos_validos[liga], cache_nombre)
        pred = m.predecir(loc, vis)

        etiqueta = f"{liga}: {loc} vs {vis}"
        opciones = []  # (nombre_seleccion, prob_modelo, cuota)
        for sel, pk, ck in (("1", "prob_1", "cuota_1"),
                            ("X", "prob_X", "cuota_X"),
                            ("2", "prob_2", "cuota_2"),
                            ("Over2.5", "prob_over_2_5", "cuota_over25"),
                            ("Under2.5", "prob_under_2_5", "cuota_under25")):
            cuota = _f(row.get(ck))
            if cuota and cuota > 1.0:
                opciones.append((sel, float(pred[pk]), cuota))
        if not opciones:
            print(f"  (aviso) sin cuotas validas en fila {i+2}: {etiqueta}")
            continue
        partidos.append({"etiqueta": etiqueta, "aviso": pred.get("aviso"),
                         "opciones": opciones})
    return partidos


def _f(v):
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def construir_combos(partidos, banda=BANDA, patas=PATAS, min_prob=MIN_PROB_PATA,
                     ordenar="prob"):
    # una pata candidata por (partido, seleccion) que supere min_prob
    patas_cand = []
    for idx, p in enumerate(partidos):
        for sel, prob, cuota in p["opciones"]:
            if prob >= min_prob:
                patas_cand.append({
                    "partido": idx, "etiqueta": p["etiqueta"], "sel": sel,
                    "prob": prob, "cuota": cuota,
                    "implicita": 1.0 / cuota, "edge": prob * cuota - 1.0,
                })
    # nos quedamos con las de mayor probabilidad para acotar la combinatoria
    patas_cand.sort(key=lambda x: x["prob"], reverse=True)
    patas_cand = patas_cand[:MAX_PATAS_CANDIDATAS]

    lo, hi = banda
    pmin, pmax = patas
    combos = []
    for n in range(pmin, pmax + 1):
        for combo in itertools.combinations(patas_cand, n):
            if len({c["partido"] for c in combo}) != n:   # 1 pata por partido
                continue
            cuota = 1.0
            prob = 1.0
            for c in combo:
                cuota *= c["cuota"]
                prob *= c["prob"]
            if not (lo <= cuota <= hi):
                continue
            combos.append({
                "patas": combo, "n": n, "cuota": cuota, "prob": prob,
                "implicita": 1.0 / cuota, "edge": prob * cuota - 1.0,
            })
    clave = (lambda x: x["edge"]) if ordenar == "valor" else (lambda x: x["prob"])
    combos.sort(key=clave, reverse=True)
    return combos


def imprimir(combos, top=TOP):
    if not combos:
        print("\nNo hay combinaciones en la banda pedida con esos filtros.")
        return
    print(f"\n{len(combos)} combinaciones posibles. Top {min(top, len(combos))} "
          f"(prob = probabilidad del modelo, impl = la que implica la cuota):\n")
    for r, c in enumerate(combos[:top], 1):
        print(f"#{r}  {c['n']} patas | cuota {c['cuota']:.2f} | "
              f"prob modelo {c['prob']*100:.1f}% | impl {c['implicita']*100:.1f}% | "
              f"edge {c['edge']*100:+.1f}%")
        for p in c["patas"]:
            print(f"     - {p['etiqueta']}  ->  {p['sel']:<8} "
                  f"@ {p['cuota']:.2f}   prob {p['prob']*100:.0f}%  "
                  f"(impl {p['implicita']*100:.0f}%, edge {p['edge']*100:+.0f}%)")
        print()


PLANTILLA = """liga,local,visitante,cuota_1,cuota_X,cuota_2,cuota_over25,cuota_under25,fecha
PD,Real Madrid CF,FC Barcelona,2.10,3.60,3.30,1.55,2.45,2026-09-01
PL,Arsenal FC,Chelsea FC,1.90,3.70,3.90,1.70,2.15,2026-09-01
SA,FC Internazionale Milano,Juventus FC,2.05,3.30,3.70,1.95,1.85,2026-09-02
"""


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    if args[0] == "--plantilla":
        destino = "proximos_ejemplo.csv"
        with open(destino, "w", encoding="utf-8", newline="") as f:
            f.write(PLANTILLA)
        print(f"Escrito {destino}. Edítalo con tus partidos y cuotas y corre:\n"
              f"  python picks.py {destino}")
        return

    conn = conectar()
    if args[0] == "--equipos":
        if len(args) < 2:
            print("Falta la liga. Ej: python picks.py --equipos PD")
        else:
            print("\n".join(_equipos_liga(conn, args[1])))
        conn.close()
        return

    path = args[0]
    banda = BANDA
    patas = PATAS
    min_prob = MIN_PROB_PATA
    top = TOP
    ordenar = "prob"
    if "--banda" in args:
        lo, hi = args[args.index("--banda") + 1].split("-")
        banda = (float(lo), float(hi))
    if "--patas" in args:
        a, b = args[args.index("--patas") + 1].split("-")
        patas = (int(a), int(b))
    if "--min-prob" in args:
        min_prob = float(args[args.index("--min-prob") + 1])
    if "--top" in args:
        top = int(args[args.index("--top") + 1])
    if "--ordenar" in args:
        ordenar = args[args.index("--ordenar") + 1]

    if not os.path.exists(path):
        raise SystemExit(f"No existe {path}. Genera una plantilla con: "
                         f"python picks.py --plantilla")

    partidos = leer_csv(path, conn)
    conn.close()
    for p in partidos:
        if p["aviso"]:
            print(f"  (pocos datos) {p['etiqueta']}: {p['aviso']}")
    combos = construir_combos(partidos, banda=banda, patas=patas,
                              min_prob=min_prob, ordenar=ordenar)
    imprimir(combos, top=top)


if __name__ == "__main__":
    _cli()
