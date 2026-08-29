"""
Recolecta partidos de torneos que suelen jugarse entre semana:
Copa Libertadores, Copa Sudamericana, UEFA Europa League, y las ligas
locales de Argentina y Colombia. Usa API-Football (api-sports.io) porque
football-data.org no cubre estos torneos/ligas en el plan free.

El plan free de API-Football da 100 requests/dia, asi que este script:
  1. Resuelve el ID de cada liga/torneo UNA sola vez y lo cachea en
     ligas_api_football.json (no vuelve a gastar requests en esto).
  2. Trae partidos temporada por temporada, y se detiene con margen antes
     de gastar las 100 requests del dia (para no dejarte sin cupo para
     otras cosas que corras el mismo dia).
"""
import os
import json
import time
import requests
from dotenv import load_dotenv

from db import conectar, crear_tablas, guardar_partido

load_dotenv()

API_KEY = os.getenv("API_FOOTBALL_KEY")
if not API_KEY:
    raise SystemExit(
        "Falta API_FOOTBALL_KEY. Crea (o completa) el archivo .env junto a este script con:\n"
        "API_FOOTBALL_KEY=tu_key_aqui"
    )

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
REQUEST_TIMEOUT = 30
FUENTE = "api-football"
CACHE_PATH = "ligas_api_football.json"

# Limite diario real del plan free es 100. Dejamos margen para no
# quedarnos sin cupo si corres otra cosa el mismo dia.
LIMITE_REQUESTS_POR_CORRIDA = 85

SEASONS = [2023, 2024, 2025, 2026]

# codigo interno -> como buscamos la liga/torneo en la API
TORNEOS_CONTINENTALES = {
    "LIB": "Copa Libertadores",
    "SUD": "Copa Sudamericana",
    "UEL": "UEFA Europa League",
}

# codigo interno -> (pais, palabras clave que debe contener el nombre de
# la liga para identificar la primera division, todo en minusculas)
LIGAS_LOCALES = {
    "ARG": ("Argentina", ["liga profesional", "primera division", "primera división"]),
    "COL": ("Colombia", ["primera a", "categoria primera a", "categoría primera a"]),
}

contador_requests = 0


def _get(path, params):
    global contador_requests
    if contador_requests >= LIMITE_REQUESTS_POR_CORRIDA:
        print(f"  Limite de {LIMITE_REQUESTS_POR_CORRIDA} requests de esta corrida alcanzado. "
              f"Vuelve a correr el script manana para seguir donde quedo.")
        return None
    resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    contador_requests += 1
    time.sleep(1)

    if resp.status_code == 429:
        print("  429: limite de requests de API-Football alcanzado. Se detiene la corrida.")
        return None
    resp.raise_for_status()

    data = resp.json()
    errores = data.get("errors")
    if errores:
        print(f"  La API devolvio un error: {errores}. Se omite esta consulta.")
        return None
    return data


def cargar_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def resolver_torneo_continental(nombre_busqueda):
    data = _get("/leagues", {"search": nombre_busqueda})
    if not data:
        return None
    for item in data.get("response", []):
        if item["league"]["name"].strip().lower() == nombre_busqueda.strip().lower():
            return item
    resultados = data.get("response", [])
    return resultados[0] if resultados else None


def resolver_liga_local(pais, palabras_clave):
    data = _get("/leagues", {"country": pais})
    if not data:
        return None
    excluir = ["reserv", "women", "femenin", "u17", "u18", "u19", "u20", "u21", "youth", "copa"]
    for item in data.get("response", []):
        nombre = item["league"]["name"].strip().lower()
        if any(mala in nombre for mala in excluir):
            continue
        if any(clave in nombre for clave in palabras_clave):
            return item
    return None


def resolver_ligas():
    cache = cargar_cache()
    cambiado = False

    for codigo, nombre_busqueda in TORNEOS_CONTINENTALES.items():
        if codigo in cache:
            continue
        print(f"Resolviendo {nombre_busqueda}...")
        encontrado = resolver_torneo_continental(nombre_busqueda)
        if encontrado:
            cache[codigo] = {
                "id": encontrado["league"]["id"],
                "nombre": encontrado["league"]["name"],
                "pais": encontrado.get("country", {}).get("name"),
                "temporadas_disponibles": [s["year"] for s in encontrado.get("seasons", [])],
            }
            cambiado = True
            print(f"  -> id={cache[codigo]['id']} ({cache[codigo]['nombre']})")
        else:
            print(f"  No se encontro {nombre_busqueda}. Revisa el nombre de busqueda o tu plan.")

    for codigo, (pais, palabras_clave) in LIGAS_LOCALES.items():
        if codigo in cache:
            continue
        print(f"Resolviendo primera division de {pais}...")
        encontrado = resolver_liga_local(pais, palabras_clave)
        if encontrado:
            cache[codigo] = {
                "id": encontrado["league"]["id"],
                "nombre": encontrado["league"]["name"],
                "pais": pais,
                "temporadas_disponibles": [s["year"] for s in encontrado.get("seasons", [])],
            }
            cambiado = True
            print(f"  -> id={cache[codigo]['id']} ({cache[codigo]['nombre']})")
        else:
            print(f"  No se encontro la primera division de {pais}. Revisa las palabras clave.")

    if cambiado:
        guardar_cache(cache)
    return cache


def traer_partidos(id_liga, season):
    data = _get("/fixtures", {"league": id_liga, "season": season, "status": "FT"})
    if not data:
        return []
    return data.get("response", [])


def guardar_partidos(conn, fixtures, codigo, nombre_liga, temporada):
    if not fixtures:
        return
    for fx in fixtures:
        guardar_partido(
            conn,
            id_partido=fx["fixture"]["id"],
            fuente=FUENTE,
            competicion_codigo=codigo,
            competicion=nombre_liga,
            temporada=str(temporada),
            fecha=fx["fixture"]["date"],
            equipo_local=fx["teams"]["home"]["name"],
            equipo_visitante=fx["teams"]["away"]["name"],
            goles_local=fx["goals"]["home"],
            goles_visitante=fx["goals"]["away"],
            estado=fx["fixture"]["status"]["short"],
        )
    conn.commit()


def main():
    conn = conectar()
    crear_tablas(conn)

    ligas = resolver_ligas()
    if not ligas:
        print("No se pudo resolver ninguna liga/torneo. Revisa tu API key y tu plan.")
        conn.close()
        return

    for codigo, info in ligas.items():
        nombre_liga = info["nombre"]
        id_liga = info["id"]
        temporadas_disponibles = set(info.get("temporadas_disponibles") or [])
        print(f"Trayendo {nombre_liga} ({codigo}, id={id_liga})...")
        total_liga = 0
        try:
            for season in SEASONS:
                if temporadas_disponibles and season not in temporadas_disponibles:
                    print(f"  Temporada {season}: no disponible para tu plan/torneo. Se omite.")
                    continue
                fixtures = traer_partidos(id_liga, season)
                guardar_partidos(conn, fixtures, codigo, nombre_liga, season)
                total_liga += len(fixtures)
                print(f"  Temporada {season}: {len(fixtures)} partidos guardados.")
        except requests.exceptions.RequestException as e:
            print(f"  Error trayendo {nombre_liga}: {e}. Se continua con la siguiente liga.")
            continue
        print(f"  Total {nombre_liga}: {total_liga} partidos.")

    conn.close()
    print(f"Listo. {contador_requests} requests usadas de esta corrida. Datos guardados en futbol.db")


if __name__ == "__main__":
    main()
