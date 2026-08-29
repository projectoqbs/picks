import os
import time
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
API_TOKEN = os.getenv("FOOTBALL_DATA_API_TOKEN")
if not API_TOKEN:
    raise SystemExit(
        "Falta FOOTBALL_DATA_API_TOKEN. Crea un archivo .env junto a este script con:\n"
        "FOOTBALL_DATA_API_TOKEN=tu_token_aqui"
    )

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
REQUEST_TIMEOUT = 30  # segundos
MAX_REINTENTOS_429 = 5

# Ligas top disponibles en el plan free
COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL": "Champions League",
}

# Temporadas a traer (año de inicio de temporada). 2023-2025 son temporadas
# completas para entrenar el modelo Poisson/Dixon-Coles; 2026 es la temporada
# en curso. Ajusta esta lista según lo que responda tu plan de la API.
SEASONS = [2023, 2024, 2025, 2026]

DB_PATH = "futbol.db"


def crear_tablas(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS partidos (
            id INTEGER PRIMARY KEY,
            competicion_codigo TEXT,
            competicion TEXT,
            temporada TEXT,
            fecha TEXT,
            equipo_local TEXT,
            equipo_visitante TEXT,
            goles_local INTEGER,
            goles_visitante INTEGER,
            estado TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_partidos_liga_temporada
        ON partidos (competicion_codigo, temporada)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_partidos_equipos
        ON partidos (equipo_local, equipo_visitante)
    """)
    conn.commit()


def traer_partidos(codigo_liga, season=None, intento=1):
    url = f"{BASE_URL}/competitions/{codigo_liga}/matches"
    params = {"status": "FINISHED"}
    if season:
        params["season"] = season

    resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 429:
        if intento > MAX_REINTENTOS_429:
            print(f"  Limite de requests alcanzado {MAX_REINTENTOS_429} veces seguidas para "
                  f"{codigo_liga} (temporada {season}). Se omite por ahora.")
            return []
        espera = 60
        print(f"  Limite de requests alcanzado, esperando {espera}s... (intento {intento})")
        time.sleep(espera)
        return traer_partidos(codigo_liga, season, intento + 1)

    if resp.status_code == 403:
        print(f"  Sin acceso a {codigo_liga} temporada {season} con tu plan actual (403). Se omite.")
        return []

    if resp.status_code == 404:
        print(f"  {codigo_liga} temporada {season} no encontrada (404). Se omite.")
        return []

    resp.raise_for_status()
    return resp.json().get("matches", [])


def guardar_partidos(conn, matches, codigo_liga, nombre_liga):
    if not matches:
        return
    cur = conn.cursor()
    for m in matches:
        cur.execute("""
            INSERT OR REPLACE INTO partidos
            (id, competicion_codigo, competicion, temporada, fecha, equipo_local, equipo_visitante,
             goles_local, goles_visitante, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m["id"],
            codigo_liga,
            nombre_liga,
            str(m["season"]["startDate"])[:4],
            m["utcDate"],
            m["homeTeam"]["name"],
            m["awayTeam"]["name"],
            m["score"]["fullTime"]["home"],
            m["score"]["fullTime"]["away"],
            m["status"],
        ))
    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    crear_tablas(conn)

    for codigo, nombre in COMPETITIONS.items():
        print(f"Trayendo {nombre} ({codigo})...")
        total_liga = 0
        try:
            for season in SEASONS:
                matches = traer_partidos(codigo, season)
                guardar_partidos(conn, matches, codigo, nombre)
                total_liga += len(matches)
                print(f"  Temporada {season}: {len(matches)} partidos guardados.")
                time.sleep(6.5)  # margen bajo el limite de 10 requests/minuto del plan free
        except requests.exceptions.RequestException as e:
            print(f"  Error trayendo {nombre}: {e}. Se continua con la siguiente liga.")
            continue
        print(f"  Total {nombre}: {total_liga} partidos.")

    conn.close()
    print("Listo. Datos guardados en", DB_PATH)


if __name__ == "__main__":
    main()
