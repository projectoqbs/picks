"""
Experimento: ¿estimar la fuerza de los equipos con TIROS A PUERTA en vez
de goles mejora la prediccion y/o el ROI contra el mercado?

Trabaja 100% sobre datos_cuotas/Matches.csv (dataset de GitHub que ya usa
backtest_roi.py): tiene goles, tiros a puerta y cuotas juntos para
E0/SP1/I1/D1/F1 hasta jun-2025. No toca futbol.db, asi no hay que
emparejar nombres entre fuentes.

Walk-forward con reajuste semanal. Para cada partido de test compara:
  - modelo GOLES : Poisson/Dixon-Coles ajustado sobre goles (el actual).
  - modelo TIROS : mismo Poisson ajustado sobre tiros a puerta, luego
                   lambda_goles = lambda_tiros * (goles/tiros del train),
                   con conversion separada para local y visita.
Metricas: log-loss 1X2 y ROI a cuota plana apostando al mejor EV > umbral.

Uso:
    python experimento_tiros.py SP1
    python experimento_tiros.py SP1 --desde 2023-08-01 --umbral 0.05
    python experimento_tiros.py --todas
"""
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from modelo import ModeloPoisson, HALF_LIFE_DIAS, PRIOR_SD

CSV_CUOTAS = os.path.join(os.getenv("DIR_CUOTAS", "datos_cuotas"), "Matches.csv")
DIVISIONES = ["E0", "SP1", "I1", "D1", "F1"]
DESDE_DEFECTO = "2023-08-01"
MIN_TRAIN = 200
REAJUSTE_DIAS = 7
UMBRAL_EV = 0.05
MAX_TRAIN_DIAS = 1100          # ~3 años; el decaimiento ya vuelve irrelevante lo previo


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def cargar(division):
    if not os.path.exists(CSV_CUOTAS):
        raise SystemExit(f"Falta {CSV_CUOTAS}. Corre antes backtest_roi.py "
                         f"para que lo descargue, o bajalo del mirror de GitHub.")
    filas = []
    with open(CSV_CUOTAS, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("Division") != division:
                continue
            try:
                fecha = datetime.strptime(row["MatchDate"], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            gh, ga = _f(row.get("FTHome")), _f(row.get("FTAway"))
            sh, sa = _f(row.get("HomeTarget")), _f(row.get("AwayTarget"))
            o1, ox, o2 = _f(row.get("MaxHome")), _f(row.get("MaxDraw")), _f(row.get("MaxAway"))
            if None in (gh, ga, sh, sa, o1, ox, o2) or sh <= 0 or sa <= 0:
                continue
            filas.append({"fecha": fecha, "home": row["HomeTeam"].strip(),
                          "away": row["AwayTeam"].strip(),
                          "gh": int(gh), "ga": int(ga), "sh": sh, "sa": sa,
                          "o1": o1, "ox": ox, "o2": o2})
    filas.sort(key=lambda r: r["fecha"])
    return filas


def resultado(gh, ga):
    return 0 if gh > ga else (1 if gh == ga else 2)


def _ajustar(filas, campo_x, campo_y, corte):
    m = ModeloPoisson(dc=True, half_life_dias=HALF_LIFE_DIAS, prior_sd=PRIOR_SD)
    tup = [(r["fecha"].isoformat(), r["home"], r["away"], r[campo_x], r[campo_y])
           for r in filas]
    m.ajustar(tup, fecha_ref=corte)
    return m


def _probs_1x2(m):
    # helper: reusa predecir() sobre nombres ya validados
    def f(local, visita):
        p = m.predecir(local, visita)
        return np.array([p["prob_1"], p["prob_X"], p["prob_2"]])
    return f


def backtest(division, desde=DESDE_DEFECTO, umbral=UMBRAL_EV, verbose=True):
    filas = cargar(division)
    desde_dt = datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    test = [i for i, r in enumerate(filas) if r["fecha"] >= desde_dt and i >= MIN_TRAIN]
    if not test:
        raise SystemExit(f"{division}: sin partidos de test desde {desde}.")

    m_gol = m_tir = None
    prox = None
    conv_h = conv_a = 0.30
    LL = {"goles": [], "tiros": [], "base": []}
    ROI = {"goles": [0.0, 0.0], "tiros": [0.0, 0.0]}   # [stake, retorno]
    n_ap = {"goles": 0, "tiros": 0}
    base_1x2 = None

    for i in test:
        r = filas[i]
        corte = r["fecha"]
        if m_gol is None or corte >= prox:
            tope = corte - timedelta(days=MAX_TRAIN_DIAS)
            train = [t for t in filas[:i] if t["fecha"] >= tope]
            m_gol = _ajustar(train, "gh", "ga", corte)
            m_tir = _ajustar(train, "sh", "sa", corte)
            sh_tot = sum(t["sh"] for t in train); sa_tot = sum(t["sa"] for t in train)
            conv_h = sum(t["gh"] for t in train) / sh_tot
            conv_a = sum(t["ga"] for t in train) / sa_tot
            m_tir.escala_local, m_tir.escala_visita = conv_h, conv_a
            kk = [resultado(t["gh"], t["ga"]) for t in train]
            base_1x2 = np.bincount(kk, minlength=3) / len(kk)
            prox = corte + timedelta(days=REAJUSTE_DIAS)

        k = resultado(r["gh"], r["ga"])
        o = np.array([r["o1"], r["ox"], r["o2"]])
        conocidos = r["home"] in m_gol.idx and r["away"] in m_gol.idx

        preds = {"base": base_1x2}
        if conocidos:
            preds["goles"] = _probs_1x2(m_gol)(r["home"], r["away"])
            preds["tiros"] = _probs_1x2(m_tir)(r["home"], r["away"])
        else:
            preds["goles"] = preds["tiros"] = base_1x2

        for nombre in ("goles", "tiros", "base"):
            p = np.clip(preds[nombre], 1e-9, 1)
            LL[nombre].append(-np.log(p[k] / p.sum()))

        for nombre in ("goles", "tiros"):
            p = preds[nombre]
            ev = p * o - 1.0
            j = int(np.argmax(ev))
            if ev[j] >= umbral:
                n_ap[nombre] += 1
                ROI[nombre][0] += 1.0
                ROI[nombre][1] += o[j] if j == k else 0.0

    res = {
        "division": division, "n_test": len(test), "desde": desde,
        "logloss_goles": float(np.mean(LL["goles"])),
        "logloss_tiros": float(np.mean(LL["tiros"])),
        "logloss_base": float(np.mean(LL["base"])),
        "roi_goles": (ROI["goles"][1] - ROI["goles"][0]) / ROI["goles"][0] if ROI["goles"][0] else 0.0,
        "roi_tiros": (ROI["tiros"][1] - ROI["tiros"][0]) / ROI["tiros"][0] if ROI["tiros"][0] else 0.0,
        "apuestas_goles": n_ap["goles"], "apuestas_tiros": n_ap["tiros"],
    }
    if verbose:
        print(f"\n== {division}  (test desde {desde}, {res['n_test']} partidos) ==")
        print(f"  log-loss 1X2   goles {res['logloss_goles']:.4f} | "
              f"tiros {res['logloss_tiros']:.4f} | base {res['logloss_base']:.4f}")
        print(f"  ROI plano EV>{umbral:.0%}  goles {res['roi_goles']*100:+.1f}% "
              f"({res['apuestas_goles']} ap) | "
              f"tiros {res['roi_tiros']*100:+.1f}% ({res['apuestas_tiros']} ap)")
        mejor = "TIROS" if res["logloss_tiros"] < res["logloss_goles"] else "goles"
        print(f"  -> mejor log-loss: {mejor}")
    return res


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.split("Uso:")[1].strip())
        return
    desde = DESDE_DEFECTO
    umbral = UMBRAL_EV
    if "--desde" in args:
        desde = args[args.index("--desde") + 1]
    if "--umbral" in args:
        umbral = float(args[args.index("--umbral") + 1])
    divs = DIVISIONES if args[0] == "--todas" else [args[0]]
    for d in divs:
        try:
            backtest(d, desde=desde, umbral=umbral)
        except SystemExit as e:
            print(f"\n== {d} ==\n  {e}")


if __name__ == "__main__":
    _cli()
