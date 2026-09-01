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
    base["analisis"] = {
        "fuerza_local": modelo.fuerza(local),
        "fuerza_visitante": modelo.fuerza(visitante),
        "forma_local": forma_reciente(conn, liga, local, fx["fecha_utc"], FORMA_N),
        "forma_visitante": forma_reciente(conn, liga, visitante, fx["fecha_utc"], FORMA_N),
        "cara_a_cara": cara_a_cara(conn, liga, local, visitante, fx["fecha_utc"], H2H_N),
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
