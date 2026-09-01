"""
Paso 1 del pipeline de la app.

Contra football-data.org (plan free) hace dos cosas para las competiciones
elegidas:
  - baja los resultados FINISHED recientes y los mete en futbol.db
    (para que el modelo entrene siempre con lo ultimo).
  - baja los partidos programados de los proximos DIAS_ADELANTE dias y
    los deja en docs/data/fixtures.json para el paso 2.

Competiciones (football-data.org plan free): Premier, La Liga, Serie A,
Bundesliga, Ligue 1 y Champions. Europa League NO esta en el plan free.

Necesita FOOTBALL_DATA_API_TOKEN en el entorno (o en .env).

Uso:
    python actualizar.py
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from db import conectar, crear_tablas, guardar_partido

load_dotenv()

API_TOKEN = os.getenv("FOOTBALL_DATA_API_TOKEN")
if not API_TOKEN:
    raise SystemExit("Falta FOOTBALL_DATA_API_TOKEN (en el entorno o en .env).")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
TIMEOUT = 30
PAUSA = 6.5                     # plan free: 10 requests/minuto
FUENTE = "football-data"

DIAS_ATRAS = 16                 # ventana de resultados recientes a refrescar
DIAS_ADELANTE = 10             # ventana de fixtures a mostrar en la app

DATA_DIR = os.path.join("docs", "data")
FIXTURES_PATH = os.path.join(DATA_DIR, "fixtures.json")

COMPETICIONES = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL": "Champions League",
}

ESTADOS_PROGRAMADO = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}


def _get(path, params):
    resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=TIMEOUT)
    if resp.status_code == 429:
        print("  429: limite de requests. Espera 60s...")
        time.sleep(60)
        return _get(path, params)
    resp.raise_for_status()
    time.sleep(PAUSA)
    return resp.json()


def refrescar_resultados(conn, codigo, nombre, desde, hasta):
    data = _get(f"/competitions/{codigo}/matches",
                {"status": "FINISHED", "dateFrom": desde, "dateTo": hasta})
    n = 0
    for m in data.get("matches", []):
        if m["score"]["fullTime"]["home"] is None:
            continue
        guardar_partido(
            conn,
            id_partido=m["id"], fuente=FUENTE,
            competicion_codigo=codigo, competicion=nombre,
            temporada=str(m["season"]["startDate"])[:4],
            fecha=m["utcDate"],
            equipo_local=m["homeTeam"]["name"],
            equipo_visitante=m["awayTeam"]["name"],
            goles_local=m["score"]["fullTime"]["home"],
            goles_visitante=m["score"]["fullTime"]["away"],
            estado=m["status"],
        )
        n += 1
    conn.commit()
    return n


def traer_fixtures(codigo, nombre, desde, hasta):
    data = _get(f"/competitions/{codigo}/matches", {"dateFrom": desde, "dateTo": hasta})
    fixtures = []
    for m in data.get("matches", []):
        if m["status"] not in ESTADOS_PROGRAMADO:
            continue
        fixtures.append({
            "id": m["id"],
            "competicion_codigo": codigo,
            "competicion": nombre,
            "fecha_utc": m["utcDate"],
            "estado": m["status"],
            "jornada": m.get("matchday"),
            "local": m["homeTeam"]["name"],
            "visitante": m["awayTeam"]["name"],
        })
    return fixtures


def main():
    hoy = datetime.now(timezone.utc).date()
    desde_res = str(hoy - timedelta(days=DIAS_ATRAS))
    hasta_fx = str(hoy + timedelta(days=DIAS_ADELANTE))
    hoy_str = str(hoy)

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = conectar()
    crear_tablas(conn)

    todos_fixtures = []
    for codigo, nombre in COMPETICIONES.items():
        print(f"{nombre} ({codigo})...")
        try:
            n = refrescar_resultados(conn, codigo, nombre, desde_res, hoy_str)
            print(f"  resultados recientes actualizados: {n}")
            fx = traer_fixtures(codigo, nombre, hoy_str, hasta_fx)
            print(f"  fixtures proximos {DIAS_ADELANTE} dias: {len(fx)}")
            todos_fixtures.extend(fx)
        except requests.exceptions.RequestException as e:
            print(f"  error: {e}. Se continua.")

    conn.close()
    todos_fixtures.sort(key=lambda x: x["fecha_utc"])
    with open(FIXTURES_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fixtures": todos_fixtures,
        }, f, ensure_ascii=False, indent=1)
    print(f"\nListo. {len(todos_fixtures)} fixtures en {FIXTURES_PATH}")


if __name__ == "__main__":
    main()
