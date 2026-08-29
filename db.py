"""
Esquema y utilidades compartidas de la base de datos del proyecto.
Ambos scripts de recoleccion (football-data.org y API-Football) usan
este mismo esquema para poder combinar los datos en un solo lugar.
"""
import sqlite3

DB_PATH = "futbol.db"


def conectar():
    return sqlite3.connect(DB_PATH)


def crear_tablas(conn):
    # La llave primaria es (fuente, id) y no solo id, porque los ids de
    # partidos de football-data.org y de API-Football son numeros
    # independientes de cada proveedor y podrian coincidir por casualidad.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS partidos (
            id INTEGER,
            fuente TEXT,
            competicion_codigo TEXT,
            competicion TEXT,
            temporada TEXT,
            fecha TEXT,
            equipo_local TEXT,
            equipo_visitante TEXT,
            goles_local INTEGER,
            goles_visitante INTEGER,
            estado TEXT,
            PRIMARY KEY (fuente, id)
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


def guardar_partido(conn, *, id_partido, fuente, competicion_codigo, competicion,
                     temporada, fecha, equipo_local, equipo_visitante,
                     goles_local, goles_visitante, estado):
    conn.execute("""
        INSERT OR REPLACE INTO partidos
        (id, fuente, competicion_codigo, competicion, temporada, fecha,
         equipo_local, equipo_visitante, goles_local, goles_visitante, estado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        id_partido, fuente, competicion_codigo, competicion, temporada, fecha,
        equipo_local, equipo_visitante, goles_local, goles_visitante, estado,
    ))
