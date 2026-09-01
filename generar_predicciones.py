"""
Paso 2 del pipeline de la app.

Lee docs/data/fixtures.json (lo deja actualizar.py), entrena el modelo de
cada competicion con futbol.db y escribe, para consumo de la PWA:

  docs/data/AAAA-MM-DD.json   un archivo por dia con partidos + prediccion
  docs/data/tabla_<LIGA>.json ranking de fuerza de cada liga
  docs/data/index.json        dias, tablas, competiciones y timestamp

La prediccion de cada partido: probabilidades 1/X/2, goles esperados,
over/under 2.5, ambos marcan, marcadores mas probables, una frase resumen
y una marca de confianza (baja si algun equipo tiene pocos datos).

Ademas de la prediccion, cada partido lleva un bloque 'analisis' con el
desglose que explica el numero (no lo reemplaza): fuerza de ataque/defensa
de cada equipo, forma reciente (ultimos 5) y cara a cara historico. La
idea es dar base estadistica para decidir, no un veredicto final.

Uso:
    python generar_predicciones.py
"""
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

from modelo import conectar, ajustar_liga
from contexto import forma_reciente, cara_a_cara

DATA_DIR = os.path.join("docs", "data")
FIXTURES_PATH = os.path.join(DATA_DIR, "fixtures.json")
FORMA_N = 5
H2H_N = 5

COMPETICIONES = {
    "PL": "Premier League", "PD": "La Liga", "SA": "Serie A",
    "BL1": "Bundesliga", "FL1": "Ligue 1", "CL": "Champions League",
}


def frase_resumen(local, visitante, p):
    p1, px, p2 = p["prob_1"], p["prob_X"], p["prob_2"]
    m = max(p1, px, p2)
    if m == p1:
        quien, prob = f"Gana {local}", p1
    elif m == p2:
        quien, prob = f"Gana {visitante}", p2
    else:
        quien, prob = "Empate", px
    if prob >= 0.55:
        fuerza = "claro favorito"
    elif prob >= 0.42:
        fuerza = "ligero favorito"
    else:
        quien, fuerza = "Partido parejo", None

    partes = [quien if fuerza is None else f"{quien} ({fuerza}, {prob*100:.0f}%)"]
    if p["prob_over_2_5"] >= 0.58:
        partes.append("se esperan goles (+2.5)")
    elif p["prob_over_2_5"] <= 0.42:
        partes.append("pocos goles (-2.5)")
    if p["prob_btts_si"] >= 0.58:
        partes.append("ambos marcan")
    return ". ".join(partes) + "."


def _pct_mas(x):
    return round((math.exp(x) - 1) * 100)


def _pct_menos(x):
    return round((1 - math.exp(-x)) * 100)


def _ppg(f):
    return (f["g"] * 3 + f["e"]) / f["pj"] if f and f["pj"] else None


def explicar(local, visitante, p, fl, fv, forma_l, forma_v, vloc):
    """Parrafo en lenguaje llano: de donde sale la prediccion y si el
    historico (fuerza de temporada) y la forma reciente coinciden."""
    if not fl or not fv:
        return None
    lam_l, lam_v = p["lambda_local"], p["lambda_visitante"]
    dif = fl["neto"] - fv["neto"]
    partes = []

    # 1) fuerza de fondo
    if abs(dif) < 0.12:
        partes.append(f"En fuerza de temporada están parejos "
                      f"(neto {fl['neto']:+.2f} vs {fv['neto']:+.2f}).")
    else:
        fuerte, ff, otro_neto = ((local, fl, fv["neto"]) if dif > 0
                                 else (visitante, fv, fl["neto"]))
        via = []
        if ff["ataque"] >= 0.10:
            via.append(f"ataca ~{_pct_mas(ff['ataque'])}% más que el promedio")
        if ff["defensa"] >= 0.10:
            via.append(f"encaja ~{_pct_menos(ff['defensa'])}% menos")
        via_txt = ", ".join(via) if via else "es más completo en general"
        partes.append(f"{fuerte} es más fuerte de fondo "
                      f"(neto {ff['neto']:+.2f} vs {otro_neto:+.2f}): {via_txt}.")

    # 2) ventaja de local + goles esperados
    quien_mas = local if lam_l > lam_v else visitante
    partes.append(f"{local} juega en casa (~+{_pct_mas(vloc)}% de ventaja, ya "
                  f"contada). Goles esperados: {lam_l:.2f} – {lam_v:.2f}; el "
                  f"modelo ve marcando más a {quien_mas}.")

    # 3) historico vs forma reciente
    pl, pv = _ppg(forma_l), _ppg(forma_v)
    if pl is not None and pv is not None:
        difp = pl - pv
        rl = f"{forma_l['g']}G-{forma_l['e']}E-{forma_l['p']}P"
        rv = f"{forma_v['g']}G-{forma_v['e']}E-{forma_v['p']}P"
        if abs(dif) < 0.12:
            if abs(difp) < 0.4:
                partes.append(f"También parejos en forma ({rl} vs {rv} en 5).")
            else:
                mejor = local if difp > 0 else visitante
                partes.append(f"En forma reciente pinta algo mejor {mejor} "
                              f"({rl} vs {rv} en 5).")
        else:
            fuerte = local if dif > 0 else visitante
            debil = visitante if dif > 0 else local
            if abs(difp) < 0.35:
                partes.append("En los últimos 5 van parejos, así que la ventaja "
                              "viene de la calidad de temporada, no del momento.")
            elif (difp > 0) == (dif > 0):
                partes.append(f"Y la forma reciente lo confirma: {fuerte} llega "
                              f"mejor ({rl if dif>0 else rv} vs {rv if dif>0 else rl}).")
            else:
                partes.append(f"Pero ojo: en forma reciente {debil} llega mejor "
                              f"que {fuerte}, así que puede estar más cerca de lo "
                              f"que dice el neto.")

    # 4) confianza
    if not fl["fiable"] or not fv["fiable"]:
        flojo = local if not fl["fiable"] else visitante
        partes.append(f"El modelo tiene pocos datos de {flojo}: tomalo con pinzas.")

    return " ".join(partes)


# base rate aproximada de cada mercado (cuan seguido pasa en general).
# El "lean" = prob_modelo - base mide cuanto se aparta el modelo del
# resultado tipico; se premia probabilidad alta CON lean positivo.
_BASE = {
    "1": 0.44, "X": 0.26, "2": 0.30,
    "1X": 0.70, "12": 0.74, "X2": 0.56,
    "Más de 2.5 goles": 0.52, "Menos de 2.5 goles": 0.48,
    "Más de 1.5 goles": 0.75, "Menos de 3.5 goles": 0.78,
    "Ambos marcan: Sí": 0.52, "Ambos marcan: No": 0.48,
}


def recomendar_mercado(local, visitante, p, fl, fv, forma_l, forma_v):
    """Devuelve el mercado que el modelo ve mas claro (no 'de valor':
    su lectura mas firme) con el porque, y una alternativa mas conservadora."""
    cand = [
        ("1", p["prob_1"]), ("X", p["prob_X"]), ("2", p["prob_2"]),
        ("1X", p["prob_1"] + p["prob_X"]), ("12", p["prob_1"] + p["prob_2"]),
        ("X2", p["prob_X"] + p["prob_2"]),
        ("Más de 2.5 goles", p["prob_over_2_5"]),
        ("Menos de 2.5 goles", p["prob_under_2_5"]),
        ("Más de 1.5 goles", p["prob_over_1_5"]),
        ("Menos de 3.5 goles", p["prob_under_3_5"]),
        ("Ambos marcan: Sí", p["prob_btts_si"]),
        ("Ambos marcan: No", p["prob_btts_no"]),
    ]
    puntuadas = []
    for nombre, prob in cand:
        lean = prob - _BASE[nombre]
        if prob < 0.55 or lean <= 0.03:      # ni muy incierto ni trivial
            continue
        puntuadas.append((prob * lean, prob, nombre))
    if not puntuadas:
        return None
    puntuadas.sort(reverse=True)
    _, prob_top, mercado = puntuadas[0]

    # alternativa: la de mayor probabilidad absoluta que no sea la misma
    # familia (para ofrecer algo mas seguro aunque pague menos)
    alt = None
    for _, prob_a, nombre_a in sorted(puntuadas, key=lambda t: -t[1]):
        if nombre_a != mercado:
            alt = {"mercado": nombre_a, "prob": round(prob_a, 3)}
            break

    return {
        "mercado": mercado,
        "prob": round(prob_top, 3),
        "por_que": _por_que_mercado(mercado, local, visitante, p, fl, fv,
                                    forma_l, forma_v),
        "alternativa": alt,
        "confianza": "baja" if (not fl or not fv or not fl["fiable"]
                                or not fv["fiable"]) else "alta",
    }


def _por_que_mercado(m, local, visitante, p, fl, fv, forma_l, forma_v):
    lam_l, lam_v = p["lambda_local"], p["lambda_visitante"]
    tot = lam_l + lam_v
    dif_neto = (fl["neto"] - fv["neto"]) if (fl and fv) else 0.0

    if m == "2":
        base = (f"El modelo ve a {visitante} marcando más que {local} pese a la "
                f"localía ({lam_v:.2f} vs {lam_l:.2f})")
        if dif_neto < -0.15:
            base += f", y lo respalda una diferencia de fuerza clara (neto {fv['neto']:+.2f} vs {fl['neto']:+.2f})"
        return base + f". Probabilidad del modelo: {p['prob_2']*100:.0f}%."
    if m == "1":
        base = (f"{local} es favorito en casa: marca más ({lam_l:.2f} vs {lam_v:.2f})")
        if dif_neto > 0.15:
            base += f" y es más fuerte de fondo (neto {fl['neto']:+.2f} vs {fv['neto']:+.2f})"
        return base + f". Probabilidad: {p['prob_1']*100:.0f}%."
    if m in ("1X", "12", "X2"):
        cubre = {"1X": f"que {local} no pierda", "12": "que no sea empate",
                 "X2": f"que {local} no gane"}[m]
        prob = {"1X": p["prob_1"] + p["prob_X"], "12": p["prob_1"] + p["prob_2"],
                "X2": p["prob_X"] + p["prob_2"]}[m]
        peor = {"1X": ("gane " + visitante, p["prob_2"]),
                "12": ("empate", p["prob_X"]),
                "X2": ("gane " + local, p["prob_1"])}[m]
        return (f"Doble oportunidad: cubre {cubre}. El escenario que queda "
                f"afuera ({peor[0]}) es el menos probable ({peor[1]*100:.0f}%), "
                f"así que el mercado se cumple ~{prob*100:.0f}% de las veces.")
    if m in ("Menos de 2.5 goles", "Menos de 3.5 goles"):
        base = (f"Pocos goles esperados en total ({lam_l:.2f} + {lam_v:.2f} = "
                f"{tot:.2f})")
        if fl and fv and max(fl["defensa"], fv["defensa"]) >= 0.15:
            quien = local if fl["defensa"] >= fv["defensa"] else visitante
            base += f"; la defensa de {quien} aprieta"
        return base + f". El modelo ve el partido cerrado ({p['prob_under_2_5' if '2.5' in m else 'prob_under_3_5']*100:.0f}% al under)."
    if m in ("Más de 2.5 goles", "Más de 1.5 goles"):
        base = (f"Bastantes goles esperados ({lam_l:.2f} + {lam_v:.2f} = {tot:.2f})")
        if fl and fv and min(fl["defensa"], fv["defensa"]) <= 0.0:
            base += "; al menos una defensa floja"
        prob = p["prob_over_2_5"] if "2.5" in m else p["prob_over_1_5"]
        return base + f". Probabilidad al over: {prob*100:.0f}%."
    if m.startswith("Ambos marcan"):
        si = m.endswith("Sí")
        return (f"El modelo espera que {'los dos' if si else 'no los dos'} "
                f"equipos marquen: goles esperados {lam_l:.2f} y {lam_v:.2f}. "
                f"Probabilidad: {(p['prob_btts_si'] if si else p['prob_btts_no'])*100:.0f}%.")
    return f"El modelo le da {p.get('prob_1', 0)*100:.0f}% a este mercado."


def predecir_partido(modelo, fx, conn):
    local, visitante = fx["local"], fx["visitante"]
    conocidos = local in modelo.idx and visitante in modelo.idx
    base = {
        "id": fx["id"],
        "competicion": fx["competicion"],
        "competicion_codigo": fx["competicion_codigo"],
        "fecha_utc": fx["fecha_utc"],
        "jornada": fx.get("jornada"),
        "local": local,
        "visitante": visitante,
    }
    if not conocidos:
        base["prediccion"] = None
        base["nota"] = "sin datos suficientes de alguno de los equipos"
        return base

    p = modelo.predecir(local, visitante)
    base["prediccion"] = {
        "prob_1": round(p["prob_1"], 3),
        "prob_X": round(p["prob_X"], 3),
        "prob_2": round(p["prob_2"], 3),
        "goles_esperados": [p["lambda_local"], p["lambda_visitante"]],
        "prob_over_2_5": round(p["prob_over_2_5"], 3),
        "prob_btts": round(p["prob_btts_si"], 3),
        "marcadores": p["marcadores_probables"][:3],
        "cuotas_justas": p["cuotas_justas_1x2"],
        "resumen": frase_resumen(local, visitante, p),
        "confianza": "baja" if p["aviso"] else "alta",
    }
    if p["aviso"]:
        base["nota"] = p["aviso"]

    liga = fx["competicion_codigo"]
    fl = modelo.fuerza(local)
    fv = modelo.fuerza(visitante)
    forma_l = forma_reciente(conn, liga, local, fx["fecha_utc"], FORMA_N)
    forma_v = forma_reciente(conn, liga, visitante, fx["fecha_utc"], FORMA_N)
    vloc = float(modelo.params_[1])
    base["analisis"] = {
        "fuerza_local": fl,
        "fuerza_visitante": fv,
        "forma_local": forma_l,
        "forma_visitante": forma_v,
        "cara_a_cara": cara_a_cara(conn, liga, local, visitante, fx["fecha_utc"], H2H_N),
        "explicacion": explicar(local, visitante, p, fl, fv, forma_l, forma_v, vloc),
        "recomendacion": recomendar_mercado(local, visitante, p, fl, fv,
                                            forma_l, forma_v),
    }
    return base


def escribir_tabla(modelo, codigo, nombre):
    filas = []
    for equipo, atk, dfn, peso, fiable in modelo.ranking():
        filas.append({
            "equipo": equipo, "ataque": round(atk, 3), "defensa": round(dfn, 3),
            "neto": round(atk + dfn, 3), "peso": peso, "fiable": fiable,
        })
    destino = os.path.join(DATA_DIR, f"tabla_{codigo}.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump({"competicion_codigo": codigo, "competicion": nombre,
                   "equipos": filas}, f, ensure_ascii=False, indent=1)
    return f"tabla_{codigo}.json"


def main():
    if not os.path.exists(FIXTURES_PATH):
        raise SystemExit(f"Falta {FIXTURES_PATH}. Corre antes actualizar.py")
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        fixtures = json.load(f)["fixtures"]

    por_liga = defaultdict(list)
    for fx in fixtures:
        por_liga[fx["competicion_codigo"]].append(fx)

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = conectar()
    partidos_pred = []
    tablas = []
    for codigo, fxs in por_liga.items():
        nombre = COMPETICIONES.get(codigo, codigo)
        print(f"{nombre}: {len(fxs)} partidos, entrenando modelo...")
        try:
            modelo = ajustar_liga(codigo, conn=conn)
        except SystemExit as e:
            print(f"  no se pudo entrenar: {e}")
            continue
        for fx in fxs:
            partidos_pred.append(predecir_partido(modelo, fx, conn))
        archivo = escribir_tabla(modelo, codigo, nombre)
        tablas.append({"codigo": codigo, "nombre": nombre, "archivo": archivo})
    conn.close()

    # agrupar por dia (fecha UTC -> fecha local se resuelve en el cliente)
    por_dia = defaultdict(list)
    for p in partidos_pred:
        dia = p["fecha_utc"][:10]
        por_dia[dia].append(p)

    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for dia, lista in por_dia.items():
        lista.sort(key=lambda x: x["fecha_utc"])
        with open(os.path.join(DATA_DIR, f"{dia}.json"), "w", encoding="utf-8") as f:
            json.dump({"fecha": dia, "actualizado": ahora, "partidos": lista},
                      f, ensure_ascii=False, indent=1)

    index = {
        "actualizado": ahora,
        "dias": sorted(por_dia),
        "competiciones": [{"codigo": c, "nombre": n} for c, n in COMPETICIONES.items()],
        "tablas": tablas,
        "total_partidos": len(partidos_pred),
    }
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print(f"\nListo. {len(partidos_pred)} partidos en {len(por_dia)} dias, "
         f"{len(tablas)} tablas -> {DATA_DIR}/")


if __name__ == "__main__":
    main()
