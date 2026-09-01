"""
Simulador de estrategias de gestion de banca.

Toma el flujo real de apuestas del backtest (walk-forward contra cuotas
de mercado, mismas que backtest_roi.py) y lo hace pasar por varias
estrategias de staking, mostrando la curva de capital de cada una.

El objetivo NO es encontrar un sistema ganador: es ver con datos que
sobre apuestas de valor esperado negativo TODAS las estrategias tienden
a la baja, y que el staking solo cambia la varianza y la velocidad, no
el signo. Cuando exista un edge real (p*cuota-1 > 0 de forma sostenida),
el mismo simulador dice que fraccion de Kelly usar.

Estrategias:
  plano        stake fijo = 1 unidad
  pct2         2% de la banca actual
  kelly_1_1    Kelly completo  (fraccion = edge / (cuota-1))
  kelly_1_2    medio Kelly
  kelly_1_4    cuarto de Kelly
  kelly_pos    cuarto de Kelly, pero SOLO si edge > 0 (Kelly "correcto")
  martingala   dobla tras cada perdida, reinicia tras ganar (para verla fallar)

Uso:
    python simulador_banca.py
    python simulador_banca.py --umbral 0.0 --mercado b365 --banca 1000
    python simulador_banca.py --export curvas.csv
"""
import csv
import sys

import numpy as np

import backtest_roi as B
from modelo import conectar

LIGAS = list(B.DIV)                 # E0 SP1 I1 D1 F1
BANCA = 1000.0
UNIDAD = BANCA * 0.01              # 1 unidad = 1% de la banca inicial
KELLY_CAP = 0.10                   # nunca arriesgar mas del 10% en una apuesta


def flujo(umbral, min_prob, max_cuota):
    conn = conectar()
    ap = []
    for liga in LIGAS:
        try:
            ap += B.recolectar_apuestas(conn, liga, umbral=umbral,
                                        min_prob=min_prob, max_cuota=max_cuota)
        except SystemExit as e:
            print(f"  {liga}: {e}")
    conn.close()
    ap.sort(key=lambda a: a["fecha"])
    return ap


# ---- estrategias: (nombre) -> funcion(estado, p, o) -> stake ----
def _kelly_frac(p, o, frac):
    edge = p * o - 1.0
    f = edge / (o - 1.0)
    return max(0.0, min(frac * f, KELLY_CAP))


ESTRATEGIAS = {
    "plano":      lambda st, p, o: UNIDAD,
    "pct2":       lambda st, p, o: 0.02 * st["banca"],
    "kelly_1_1":  lambda st, p, o: st["banca"] * _kelly_frac(p, o, 1.0),
    "kelly_1_2":  lambda st, p, o: st["banca"] * _kelly_frac(p, o, 0.5),
    "kelly_1_4":  lambda st, p, o: st["banca"] * _kelly_frac(p, o, 0.25),
    "kelly_pos":  lambda st, p, o: st["banca"] * _kelly_frac(p, o, 0.25) if p * o > 1 else 0.0,
    "martingala": lambda st, p, o: min(UNIDAD * (2 ** st["perdidas_seg"]), st["banca"]),
}


def simular(apuestas, nombre, fn, banca0=BANCA):
    st = {"banca": banca0, "perdidas_seg": 0}
    pico = banca0
    max_dd = 0.0
    peor_racha = racha = 0
    n_ap = n_verde = 0
    curva = [banca0]
    arruinado_en = None
    for k, a in enumerate(apuestas):
        if st["banca"] <= 1e-6:
            arruinado_en = arruinado_en or k
            curva.append(0.0)
            continue
        stake = max(0.0, min(fn(st, a["p"], a["o"]), st["banca"]))
        if stake <= 1e-9:
            curva.append(st["banca"])
            continue
        n_ap += 1
        if a["gano"]:
            st["banca"] += stake * (a["o"] - 1.0)
            st["perdidas_seg"] = 0
            racha = 0
            n_verde += 1
        else:
            st["banca"] -= stake
            st["perdidas_seg"] += 1
            racha += 1
            peor_racha = max(peor_racha, racha)
        pico = max(pico, st["banca"])
        max_dd = max(max_dd, (pico - st["banca"]) / pico if pico > 0 else 1.0)
        curva.append(st["banca"])
    return {
        "estrategia": nombre, "banca_final": st["banca"],
        "mult": st["banca"] / banca0, "apuestas": n_ap,
        "pct_verde": n_verde / n_ap if n_ap else 0.0,
        "max_drawdown": max_dd, "peor_racha_roja": peor_racha,
        "arruinado": arruinado_en is not None,
        "curva": curva,
    }


def main():
    args = sys.argv[1:]
    umbral = float(args[args.index("--umbral") + 1]) if "--umbral" in args else B.UMBRAL_EV
    if "--mercado" in args:
        B.MERCADO = args[args.index("--mercado") + 1]
    banca0 = float(args[args.index("--banca") + 1]) if "--banca" in args else BANCA
    min_prob = float(args[args.index("--min-prob") + 1]) if "--min-prob" in args else 0.0
    max_cuota = float(args[args.index("--max-cuota") + 1]) if "--max-cuota" in args else 99.0
    export = args[args.index("--export") + 1] if "--export" in args else None

    global UNIDAD
    UNIDAD = banca0 * 0.01

    print(f"Recolectando apuestas (mercado {B.MERCADO}, umbral EV {umbral})...")
    ap = flujo(umbral, min_prob, max_cuota)
    if not ap:
        raise SystemExit("Sin apuestas. Baja el umbral o revisa datos_cuotas/Matches.csv")

    edges = np.array([a["p"] * a["o"] - 1.0 for a in ap])
    aciertos = np.mean([a["gano"] for a in ap])
    roi_teorico = np.mean([(a["o"] - 1.0) if a["gano"] else -1.0 for a in ap])
    print(f"  {len(ap)} apuestas | acierto {aciertos*100:.1f}% | "
          f"EV medio declarado {edges.mean()*100:+.1f}% | "
          f"ROI real a stake plano {roi_teorico*100:+.1f}%\n")

    filas = [simular(ap, n, fn, banca0) for n, fn in ESTRATEGIAS.items()]
    print(f"{'estrategia':12} {'banca final':>12} {'x':>7} {'apost.':>7} "
          f"{'%verde':>7} {'draw.max':>9} {'racha roja':>11} {'ruina':>6}")
    for r in filas:
        print(f"{r['estrategia']:12} {r['banca_final']:>12,.0f} {r['mult']:>7.2f} "
              f"{r['apuestas']:>7} {r['pct_verde']*100:>6.1f}% {r['max_drawdown']*100:>8.1f}% "
              f"{r['peor_racha_roja']:>11} {'SI' if r['arruinado'] else 'no':>6}")

    print("\nLectura: 'x' es cuanto multiplicaste la banca (1.00 = igual, <1 = perdida).")
    print("Con EV negativo todas terminan en x<1; Kelly fraccionado solo pierde")
    print("mas lento. 'kelly_pos' casi no apuesta porque casi no hay edge > 0.")

    if export:
        n = max(len(r["curva"]) for r in filas)
        with open(export, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["apuesta"] + [r["estrategia"] for r in filas])
            for i in range(n):
                w.writerow([i] + [f"{r['curva'][i]:.2f}" if i < len(r["curva"]) else ""
                                  for r in filas])
        print(f"\nCurvas de capital -> {export}")


if __name__ == "__main__":
    main()
