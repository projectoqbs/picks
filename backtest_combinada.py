"""
Backtest empirico de la combinada por cuota objetivo.

Para cada JORNADA historica arma la combinada que haria combinada.py
(las selecciones mas firmes del modelo cuya cuota combinada REAL queda
cerca del objetivo), la apuesta a stake plano y mide el ROI de cada
nivel de objetivo sobre las temporadas.

Sirve para ver la curva empirica del balance "probabilidad vs cuota":
cuanto pierde de verdad un objetivo 2 frente a un 3, 4, 5.

Datos: datos_cuotas/Matches.csv (mismo dataset que backtest_roi.py).
Cuotas: mejor precio de mercado (Max*) por defecto; --mercado b365 usa Bet365.
Cubre E0/SP1/I1/D1/F1, temporadas 2023-24 y 2024-25.

Uso:
    python backtest_combinada.py
    python backtest_combinada.py --desde 2024-08-01 --min-prob 0.55 --objetivos 2,3,4,5
"""
import csv
import itertools
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from modelo import ModeloPoisson, HALF_LIFE_DIAS, PRIOR_SD

CSV = os.path.join(os.getenv("DIR_CUOTAS", "datos_cuotas"), "Matches.csv")
DIVISIONES = ["E0", "SP1", "I1", "D1", "F1"]
DESDE = "2023-08-01"
MIN_TRAIN = 200
REAJUSTE_DIAS = 7
MAX_TRAIN_DIAS = 1100
MIN_PROB = 0.55
TOL = 0.12                 # +-12% de la cuota objetivo
PATAS = (2, 4)
MAX_LEGS_DIA = 16         # tope de patas candidatas por dia
OBJETIVOS = [2.0, 3.0, 4.0, 5.0]
MERCADO = "max"


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def cargar(division):
    if not os.path.exists(CSV):
        raise SystemExit(f"Falta {CSV}. Corre antes backtest_roi.py para bajarlo.")
    o = ("MaxHome", "MaxDraw", "MaxAway", "MaxOver25", "MaxUnder25") if MERCADO == "max" \
        else ("OddHome", "OddDraw", "OddAway", "Over25", "Under25")
    filas = []
    with open(CSV, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("Division") != division:
                continue
            try:
                fecha = datetime.strptime(row["MatchDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            gh, ga = _f(row.get("FTHome")), _f(row.get("FTAway"))
            cu = [_f(row.get(k)) for k in o]
            if None in (gh, ga, *cu):
                continue
            filas.append({"fecha": fecha, "home": row["HomeTeam"].strip(),
                          "away": row["AwayTeam"].strip(), "gh": int(gh), "ga": int(ga),
                          "o1": cu[0], "ox": cu[1], "o2": cu[2],
                          "oover": cu[3], "ounder": cu[4]})
    filas.sort(key=lambda r: r["fecha"])
    return filas


def res_1x2(gh, ga):
    return 0 if gh > ga else (1 if gh == ga else 2)


def _ajustar(filas, corte):
    # SOLO partidos anteriores al corte (nada de mirar el futuro), y no mas
    # viejos que MAX_TRAIN_DIAS (el decaimiento ya los vuelve irrelevantes).
    tope = corte - timedelta(days=MAX_TRAIN_DIAS)
    train = [(r["fecha"].isoformat(), r["home"], r["away"], r["gh"], r["ga"])
             for r in filas if tope <= r["fecha"] < corte]
    m = ModeloPoisson(dc=True, half_life_dias=HALF_LIFE_DIAS, prior_sd=PRIOR_SD)
    m.ajustar(train, fecha_ref=corte)
    return m


def legs_partido(m, r, midx, min_prob):
    if r["home"] not in m.idx or r["away"] not in m.idx:
        return []
    p = m.predecir(r["home"], r["away"])
    real = res_1x2(r["gh"], r["ga"])
    over = (r["gh"] + r["ga"]) >= 3
    opciones = [
        ("1", p["prob_1"], r["o1"], real == 0),
        ("X", p["prob_X"], r["ox"], real == 1),
        ("2", p["prob_2"], r["o2"], real == 2),
        ("O2.5", p["prob_over_2_5"], r["oover"], over),
        ("U2.5", 1 - p["prob_over_2_5"], r["ounder"], not over),
    ]
    return [{"m": midx, "sel": s, "p": pr, "o": od, "gano": gn}
            for s, pr, od, gn in opciones if pr >= min_prob and od > 1.0]


def mejor_combo(legs, objetivo, tol, patas):
    legs = sorted(legs, key=lambda x: x["p"], reverse=True)[:MAX_LEGS_DIA]
    lo, hi = objetivo * (1 - tol), objetivo * (1 + tol)
    mejor = None
    for n in range(patas[0], patas[1] + 1):
        for combo in itertools.combinations(legs, n):
            if len({c["m"] for c in combo}) != n:
                continue
            cu = pr = 1.0
            for c in combo:
                cu *= c["o"]; pr *= c["p"]
            if not (lo <= cu <= hi):
                continue
            clave = (abs(cu - objetivo), -pr)
            if mejor is None or clave < mejor[0]:
                mejor = (clave, combo, cu, pr)
    return mejor


def backtest(division, desde, min_prob, objetivos, tol=TOL, verbose=True):
    filas = cargar(division)
    desde_dt = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    idx_test = [i for i, r in enumerate(filas)
                if r["fecha"] >= desde_dt and i >= MIN_TRAIN]
    if not idx_test:
        raise SystemExit(f"{division}: sin test desde {desde}")

    por_dia = defaultdict(list)
    for i in idx_test:
        por_dia[filas[i]["fecha"].date()].append(i)

    m = None
    prox = None
    acc = {t: {"n": 0, "stake": 0.0, "ret": 0.0, "ac": 0, "sp": 0.0, "scu": 0.0}
           for t in objetivos}
    simple = {"n": 0, "stake": 0.0, "ret": 0.0, "ac": 0}

    for dia in sorted(por_dia):
        corte = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
        if m is None or corte >= prox:
            m = _ajustar(filas, corte)
            prox = corte + timedelta(days=REAJUSTE_DIAS)

        legs_dia = []
        for midx, i in enumerate(por_dia[dia]):
            legs_dia += legs_partido(m, filas[i], midx, min_prob)
        if not legs_dia:
            continue

        # referencia "1 pata": la seleccion mas probable de cada partido
        for midx in {l["m"] for l in legs_dia}:
            cand = [l for l in legs_dia if l["m"] == midx]
            l = max(cand, key=lambda x: x["p"])
            simple["n"] += 1
            simple["stake"] += 1.0
            simple["ret"] += l["o"] if l["gano"] else 0.0
            simple["ac"] += int(l["gano"])

        for t in objetivos:
            best = mejor_combo(legs_dia, t, tol, PATAS)
            if not best:
                continue
            _, combo, cu, pr = best
            gano = all(c["gano"] for c in combo)
            a = acc[t]
            a["n"] += 1
            a["stake"] += 1.0
            a["ret"] += cu if gano else 0.0
            a["ac"] += int(gano)
            a["sp"] += pr
            a["scu"] += cu

    if verbose:
        print(f"\n== {division}  (test desde {desde}, mercado {MERCADO}) ==")
        s = simple
        print(f"  1 pata (simple): {s['n']:>4} apuestas  acierto {s['ac']/s['n']*100:4.1f}%  "
              f"ROI {(s['ret']-s['stake'])/s['stake']*100:+6.1f}%")
        for t in objetivos:
            a = acc[t]
            if not a["n"]:
                print(f"  objetivo {t:.0f}: sin combinadas en tolerancia")
                continue
            roi = (a["ret"] - a["stake"]) / a["stake"] * 100
            print(f"  objetivo {t:.0f}: {a['n']:>4} combis  cuota media {a['scu']/a['n']:.2f}  "
                  f"prob modelo {a['sp']/a['n']*100:4.1f}%  acierto real {a['ac']/a['n']*100:4.1f}%  "
                  f"ROI {roi:+6.1f}%")
    return {"division": division, "simple": simple, "objetivos": acc}


def _cli():
    global MERCADO
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    desde = DESDE
    min_prob = MIN_PROB
    objetivos = OBJETIVOS
    if "--desde" in args:
        desde = args[args.index("--desde") + 1]
    if "--min-prob" in args:
        min_prob = float(args[args.index("--min-prob") + 1])
    if "--mercado" in args:
        MERCADO = args[args.index("--mercado") + 1]
    if "--objetivos" in args:
        objetivos = [float(x) for x in args[args.index("--objetivos") + 1].split(",")]

    tot = {t: {"n": 0, "stake": 0.0, "ret": 0.0, "ac": 0} for t in objetivos}
    tsimple = {"n": 0, "stake": 0.0, "ret": 0.0, "ac": 0}
    for d in DIVISIONES:
        try:
            r = backtest(d, desde, min_prob, objetivos)
        except SystemExit as e:
            print(f"\n== {d} ==\n  {e}")
            continue
        for k in tsimple:
            tsimple[k] += r["simple"][k]
        for t in objetivos:
            for k in tot[t]:
                tot[t][k] += r["objetivos"][t][k]

    print("\n" + "=" * 60 + "\nTOTAL (5 ligas)")
    s = tsimple
    if s["n"]:
        print(f"  1 pata (simple): {s['n']:>4}  acierto {s['ac']/s['n']*100:4.1f}%  "
              f"ROI {(s['ret']-s['stake'])/s['stake']*100:+6.1f}%")
    for t in objetivos:
        a = tot[t]
        if a["n"]:
            print(f"  objetivo {t:.0f}: {a['n']:>4}  acierto {a['ac']/a['n']*100:4.1f}%  "
                  f"ROI {(a['ret']-a['stake'])/a['stake']*100:+6.1f}%")


if __name__ == "__main__":
    _cli()
