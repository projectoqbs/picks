"""
Backtesting walk-forward del modelo Poisson / Dixon-Coles.

Para cada partido de la ventana de test se reentrena el modelo usando
SOLO partidos anteriores a su fecha (nada de mirar el futuro) y se
compara la prediccion con el resultado real.

Para que sea viable en tiempo, el modelo se reajusta cada N dias (por
defecto 7, una vez por jornada) y entre reajustes se reutiliza el
ultimo. Es lo que harias en la practica: reentrenar cada semana.

Metricas sobre el mercado 1X2 (y sobre over/under 2.5):
  - log-loss : -media(log(prob asignada al resultado real)). Menor = mejor.
  - Brier    : media de la distancia cuadratica al vector one-hot.
  - RPS      : ranked probability score, penaliza segun lo lejos que
               quedo la masa de probabilidad (1 < X < 2 estan ordenados).
  - acierto  : % de veces que el resultado real fue el mas probable.
Baseline: frecuencias base 1X2 de la ventana de entrenamiento (modelo
"tonto" que siempre predice el reparto historico).

Calibracion: se agrupan las predicciones de victoria local en deciles y
se compara probabilidad media predicha vs frecuencia observada (ECE).

Uso:
    python backtest.py PL
    python backtest.py PL --desde 2024-08-01
    python backtest.py PL --sweep
    python backtest.py --todas
"""
import os
import sys
import time
import itertools
from datetime import datetime, timedelta, timezone

import numpy as np

from modelo import (conectar, cargar_partidos, ligas_disponibles,
                    ModeloPoisson, _parse_fecha, HALF_LIFE_DIAS, PRIOR_SD)

DESDE_DEFECTO = "2024-08-01"     # inicio de la ventana de test
MIN_TRAIN = 150                  # partidos previos minimos para evaluar
REAJUSTE_DIAS = 7


def _a_dt(s):
    if len(s) <= 10:
        s = s + "T00:00:00+00:00"
    return _parse_fecha(s)


def resultado_1x2(gl, gv):
    return 0 if gl > gv else (1 if gl == gv else 2)


def rps(probs, k):
    """Ranked probability score para 3 resultados ordenados (1, X, 2)."""
    y = np.zeros(3)
    y[k] = 1.0
    cp = np.cumsum(probs)
    cy = np.cumsum(y)
    return float(np.sum((cp - cy) ** 2)) / 2.0


def _metricas(P, K):
    """P: (n,3) probabilidades 1X2. K: (n,) indice del resultado real."""
    P = np.clip(np.asarray(P), 1e-12, 1.0)
    P = P / P.sum(axis=1, keepdims=True)
    n = len(K)
    idx = np.arange(n)
    logloss = float(-np.mean(np.log(P[idx, K])))
    onehot = np.zeros((n, 3))
    onehot[idx, K] = 1.0
    brier = float(np.mean(np.sum((P - onehot) ** 2, axis=1)))
    rps_m = float(np.mean([rps(P[i], K[i]) for i in range(n)]))
    acierto = float(np.mean(P.argmax(axis=1) == K))
    return {"n": n, "logloss": logloss, "brier": brier, "rps": rps_m, "acierto": acierto}


def _metricas_binarias(p_si, y_si):
    p = np.clip(np.asarray(p_si), 1e-12, 1 - 1e-12)
    y = np.asarray(y_si, dtype=float)
    logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    brier = float(np.mean((p - y) ** 2))
    return {"logloss": logloss, "brier": brier, "base_si": float(y.mean())}


def _calibracion(p_local, y_local, bins=10):
    p = np.asarray(p_local)
    y = np.asarray(y_local, dtype=float)
    bordes = np.linspace(0, 1, bins + 1)
    ece, filas = 0.0, []
    for lo, hi in zip(bordes[:-1], bordes[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if not m.any():
            continue
        conf, obs, peso = p[m].mean(), y[m].mean(), m.mean()
        ece += peso * abs(conf - obs)
        filas.append((lo, hi, int(m.sum()), conf, obs))
    return ece, filas


def backtest_liga(conn, liga, desde=DESDE_DEFECTO, reajuste_dias=REAJUSTE_DIAS,
                  half_life=HALF_LIFE_DIAS, prior_sd=PRIOR_SD, dc=True,
                  min_train=MIN_TRAIN, verbose=True):
    filas = cargar_partidos(conn, liga)
    fechas = [_parse_fecha(f[0]) for f in filas]
    desde_dt = _a_dt(desde)

    test = [i for i, d in enumerate(fechas) if d >= desde_dt and i >= min_train]
    if not test:
        raise SystemExit(f"{liga}: sin partidos de test desde {desde} "
                         f"(con {min_train} de entrenamiento minimo).")

    P1x2, K1x2 = [], []
    pov, yov = [], []
    p_loc, y_loc = [], []
    n_fallback = 0
    modelo = None
    prox_reajuste = None
    t0 = time.time()

    for c, i in enumerate(test):
        corte = fechas[i]
        if modelo is None or corte >= prox_reajuste:
            train = filas[:i]
            modelo = ModeloPoisson(dc=dc, half_life_dias=half_life, prior_sd=prior_sd)
            modelo.liga = liga
            modelo.ajustar(train, fecha_ref=corte)
            prox_reajuste = corte + timedelta(days=reajuste_dias)
            # frecuencias base para fallback y baseline, de la ventana train
            kk = [resultado_1x2(f[3], f[4]) for f in train]
            base_1x2 = np.bincount(kk, minlength=3) / len(kk)
            tot = np.array([f[3] + f[4] for f in train])
            base_over = float((tot >= 3).mean())

        f = filas[i]
        local, visita, gl, gv = f[1], f[2], f[3], f[4]
        k = resultado_1x2(gl, gv)

        if local in modelo.idx and visita in modelo.idx:
            pred = modelo.predecir(local, visita)
            p = [pred["prob_1"], pred["prob_X"], pred["prob_2"]]
            po = pred["prob_over_2_5"]
        else:
            p = list(base_1x2)
            po = base_over
            n_fallback += 1

        P1x2.append(p);  K1x2.append(k)
        pov.append(po);  yov.append(1 if (gl + gv) >= 3 else 0)
        p_loc.append(p[0]);  y_loc.append(1 if k == 0 else 0)

    K1x2 = np.array(K1x2)
    m = _metricas(P1x2, K1x2)
    base = _metricas(np.tile(base_1x2, (len(K1x2), 1)), K1x2)  # baseline ultimo train
    mov = _metricas_binarias(pov, yov)
    ece, cal = _calibracion(p_loc, y_loc)

    res = {
        "liga": liga, "config": dict(half_life=half_life, prior_sd=prior_sd, dc=dc),
        "n_test": m["n"], "n_fallback": n_fallback,
        "logloss": m["logloss"], "brier": m["brier"], "rps": m["rps"],
        "acierto": m["acierto"],
        "logloss_baseline": base["logloss"], "rps_baseline": base["rps"],
        "over25_logloss": mov["logloss"], "over25_brier": mov["brier"],
        "ece_local": ece, "segundos": round(time.time() - t0, 1),
        "_calibracion": cal,
    }
    if verbose:
        _imprimir(res)
    return res


def _imprimir(r):
    c = r["config"]
    print(f"\n== {r['liga']}  (half_life={c['half_life']} prior_sd={c['prior_sd']} "
          f"dc={c['dc']}) ==")
    print(f"  partidos test .......: {r['n_test']}  (fallback base: {r['n_fallback']})")
    print(f"  log-loss 1X2 ........: {r['logloss']:.4f}   baseline: {r['logloss_baseline']:.4f}")
    print(f"  RPS 1X2 ............: {r['rps']:.4f}   baseline: {r['rps_baseline']:.4f}")
    print(f"  Brier 1X2 ..........: {r['brier']:.4f}")
    print(f"  acierto (argmax) ...: {r['acierto']*100:.1f}%")
    print(f"  over/under 2.5 .....: log-loss {r['over25_logloss']:.4f}  brier {r['over25_brier']:.4f}")
    print(f"  ECE victoria local .: {r['ece_local']:.4f}")
    print(f"  tiempo ............: {r['segundos']}s")


def _sweep(conn, liga, desde):
    grid_hl = [180, 365, 540]
    grid_sd = [0.4, 0.6, 0.9]
    grid_dc = [True, False]
    print(f"Sweep {liga}: {len(grid_hl)*len(grid_sd)*len(grid_dc)} configuraciones "
          f"(ventana de test desde {desde})...")
    resultados = []
    for hl, sd, dc in itertools.product(grid_hl, grid_sd, grid_dc):
        r = backtest_liga(conn, liga, desde=desde, half_life=hl, prior_sd=sd,
                          dc=dc, verbose=False)
        resultados.append(r)
        print(f"  hl={hl:>3} sd={sd} dc={int(dc)} -> "
              f"logloss={r['logloss']:.4f} rps={r['rps']:.4f} "
              f"acierto={r['acierto']*100:.1f}%")
    resultados.sort(key=lambda x: x["logloss"])
    print("\nMejores por log-loss:")
    for r in resultados[:5]:
        c = r["config"]
        print(f"  hl={c['half_life']:>3} sd={c['prior_sd']} dc={int(c['dc'])}  "
              f"logloss={r['logloss']:.4f}  rps={r['rps']:.4f}  "
              f"brier={r['brier']:.4f}  acierto={r['acierto']*100:.1f}%")
    return resultados


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.split("Uso:")[1].strip())
        return

    desde = DESDE_DEFECTO
    if "--desde" in args:
        i = args.index("--desde")
        desde = args[i + 1]
        del args[i:i + 2]

    conn = conectar()
    if args[0] == "--todas":
        for liga in ligas_disponibles(conn):
            try:
                backtest_liga(conn, liga, desde=desde)
            except SystemExit as e:
                print(f"\n== {liga} ==\n  {e}")
        conn.close()
        return

    liga = args[0]
    if "--sweep" in args:
        _sweep(conn, liga, desde)
    else:
        r = backtest_liga(conn, liga, desde=desde)
        print("\n  Calibracion victoria local (pred -> obs):")
        for lo, hi, n, conf, obs in r["_calibracion"]:
            print(f"    [{lo:.1f},{hi:.1f})  n={n:>4}  pred={conf:.3f}  obs={obs:.3f}")
    conn.close()


if __name__ == "__main__":
    _cli()
