"""
Auditoria de futbol.db antes de entrenar cualquier modelo.

No toca ninguna API: solo lee la base que dejaron los recolectores y
responde las preguntas que importan para un modelo Poisson / Dixon-Coles:

  1. Cobertura: cuantos partidos hay por fuente / competicion / temporada,
     rango de fechas y si falta algun marcador.
  2. Goles: promedio de goles por partido, cuanto mete el local vs el
     visitante (factor de localia) y tasa de over 2.5.
  3. Resultados: reparto de victorias local / empate / victoria visitante.
  4. Integridad de calendario: partidos por equipo en cada temporada
     (deberian ser ~2*(N-1) en una liga de todos contra todos).
  5. Nombres de equipos: equipos que aparecen en una temporada y
     desaparecen en la siguiente dentro de la misma competicion. Sirve
     para detectar tanto ascensos/descensos legitimos como el mismo
     equipo escrito de dos formas distintas.
  6. Sanity Poisson: compara la distribucion real de goles del local
     contra una Poisson con esa misma media.

Uso:
    python explorar_datos.py
    FUTBOL_DB=/ruta/a/futbol.db python explorar_datos.py
"""
import os
import math
import sqlite3
from collections import Counter, defaultdict

DB_PATH = os.getenv("FUTBOL_DB", "futbol.db")

# Competiciones que son de eliminacion directa (o con fase de grupos +
# llaves): ahi NO tiene sentido revisar "partidos por equipo" como si
# fuera una liga, ni el churn de nombres entre temporadas.
COPAS = {"CL", "UEL", "LIB", "SUD"}


def linea(titulo):
    print()
    print("=" * 72)
    print(titulo)
    print("=" * 72)


def conectar():
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            f"No existe {DB_PATH}. Corre primero recolectar_datos.py "
            f"(y recolectar_datos_conmebol.py) o define FUTBOL_DB."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cobertura(conn):
    linea("1. COBERTURA POR FUENTE / COMPETICION / TEMPORADA")
    filas = conn.execute("""
        SELECT fuente, competicion_codigo AS cod, competicion AS nombre,
               temporada, COUNT(*) AS n,
               SUM(goles_local IS NULL OR goles_visitante IS NULL) AS sin_marcador,
               MIN(fecha) AS desde, MAX(fecha) AS hasta
        FROM partidos
        GROUP BY fuente, competicion_codigo, temporada
        ORDER BY fuente, nombre, temporada
    """).fetchall()

    print(f"{'fuente':14} {'cod':4} {'competicion':30} {'temp':5} {'n':>5} "
          f"{'s/marc':>7}  rango de fechas")
    for f in filas:
        print(f"{f['fuente']:14} {f['cod']:4} {f['nombre'][:30]:30} "
              f"{f['temporada']:5} {f['n']:>5} {f['sin_marcador'] or 0:>7}  "
              f"{f['desde'][:10]} -> {f['hasta'][:10]}")

    total = conn.execute("SELECT COUNT(*) FROM partidos").fetchone()[0]
    nulos = conn.execute(
        "SELECT COUNT(*) FROM partidos WHERE goles_local IS NULL OR goles_visitante IS NULL"
    ).fetchone()[0]
    dup = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT fuente, id FROM partidos GROUP BY fuente, id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    print()
    print(f"  Partidos totales........: {total}")
    print(f"  Sin marcador............: {nulos}")
    print(f"  Claves (fuente,id) dup..: {dup}")


def goles_y_localia(conn):
    linea("2. GOLES Y FACTOR DE LOCALIA (por competicion, solo temporadas con datos)")
    filas = conn.execute("""
        SELECT competicion_codigo AS cod, competicion AS nombre,
               COUNT(*) AS n,
               AVG(goles_local + goles_visitante) AS gpp,
               AVG(goles_local) AS gl,
               AVG(goles_visitante) AS gv,
               AVG((goles_local + goles_visitante) > 2.5) AS over25
        FROM partidos
        WHERE goles_local IS NOT NULL AND goles_visitante IS NOT NULL
        GROUP BY competicion_codigo
        ORDER BY gpp DESC
    """).fetchall()

    print(f"{'cod':4} {'competicion':30} {'n':>5} {'gol/part':>9} "
          f"{'local':>7} {'visita':>7} {'dif':>6} {'over2.5':>8}")
    for f in filas:
        dif = f["gl"] - f["gv"]
        print(f"{f['cod']:4} {f['nombre'][:30]:30} {f['n']:>5} "
              f"{f['gpp']:>9.2f} {f['gl']:>7.2f} {f['gv']:>7.2f} "
              f"{dif:>+6.2f} {f['over25']*100:>7.1f}%")
    print()
    print("  'dif' = goles de local - goles de visitante. Un valor positivo")
    print("  y estable es la ventaja de campo que el modelo debe capturar.")


def resultados(conn):
    linea("3. REPARTO DE RESULTADOS (1 / X / 2)")
    filas = conn.execute("""
        SELECT competicion_codigo AS cod, competicion AS nombre,
               COUNT(*) AS n,
               AVG(goles_local > goles_visitante) AS local,
               AVG(goles_local = goles_visitante) AS empate,
               AVG(goles_local < goles_visitante) AS visita
        FROM partidos
        WHERE goles_local IS NOT NULL AND goles_visitante IS NOT NULL
        GROUP BY competicion_codigo
        ORDER BY local DESC
    """).fetchall()

    print(f"{'cod':4} {'competicion':30} {'n':>5} {'1 (local)':>10} "
          f"{'X (empate)':>11} {'2 (visita)':>11}")
    for f in filas:
        print(f"{f['cod']:4} {f['nombre'][:30]:30} {f['n']:>5} "
              f"{f['local']*100:>9.1f}% {f['empate']*100:>10.1f}% "
              f"{f['visita']*100:>10.1f}%")


def calendario(conn):
    linea("4. INTEGRIDAD DE CALENDARIO (ligas: partidos por equipo y temporada)")
    filas = conn.execute("""
        SELECT competicion_codigo AS cod, competicion AS nombre, temporada,
               equipo, COUNT(*) AS pj
        FROM (
            SELECT competicion_codigo, competicion, temporada,
                   equipo_local AS equipo FROM partidos
            UNION ALL
            SELECT competicion_codigo, competicion, temporada,
                   equipo_visitante AS equipo FROM partidos
        )
        GROUP BY competicion_codigo, temporada, equipo
    """).fetchall()

    porcomp = defaultdict(list)
    for f in filas:
        if f["cod"] in COPAS:
            continue
        porcomp[(f["cod"], f["nombre"], f["temporada"])].append((f["equipo"], f["pj"]))

    print(f"{'cod':4} {'temp':5} {'equipos':>8} {'pj min':>7} {'pj max':>7} "
          f"{'pj medio':>9}  observacion")
    for (cod, nombre, temp), equipos in sorted(porcomp.items()):
        pjs = [pj for _, pj in equipos]
        pj_min, pj_max = min(pjs), max(pjs)
        pj_med = sum(pjs) / len(pjs)
        nota = ""
        if pj_max - pj_min > 4:
            peor = min(equipos, key=lambda x: x[1])
            nota = f"desbalance: {peor[0][:20]} solo {peor[1]} pj"
        print(f"{cod:4} {temp:5} {len(equipos):>8} {pj_min:>7} {pj_max:>7} "
              f"{pj_med:>9.1f}  {nota}")


def nombres_equipos(conn):
    linea("5. NOMBRES DE EQUIPOS QUE ENTRAN/SALEN ENTRE TEMPORADAS")
    filas = conn.execute("""
        SELECT DISTINCT competicion_codigo AS cod, competicion AS nombre,
               temporada, equipo
        FROM (
            SELECT competicion_codigo, competicion, temporada,
                   equipo_local AS equipo FROM partidos
            UNION
            SELECT competicion_codigo, competicion, temporada,
                   equipo_visitante AS equipo FROM partidos
        )
    """).fetchall()

    equipos_por = defaultdict(lambda: defaultdict(set))  # cod -> temporada -> {equipos}
    nombre_comp = {}
    for f in filas:
        if f["cod"] in COPAS:
            continue
        equipos_por[f["cod"]][f["temporada"]].add(f["equipo"])
        nombre_comp[f["cod"]] = f["nombre"]

    hubo = False
    for cod, portemp in sorted(equipos_por.items()):
        temporadas = sorted(portemp)
        for prev, curr in zip(temporadas, temporadas[1:]):
            salen = portemp[prev] - portemp[curr]
            entran = portemp[curr] - portemp[prev]
            if not salen and not entran:
                continue
            hubo = True
            print(f"\n  {cod} ({nombre_comp[cod]})  {prev} -> {curr}")
            if salen:
                print(f"    salen ({len(salen)}): " + ", ".join(sorted(salen)))
            if entran:
                print(f"    entran ({len(entran)}): " + ", ".join(sorted(entran)))
            # pistas de mismo equipo escrito distinto: prefijo compartido
            for s in sorted(salen):
                for e in sorted(entran):
                    if _parecidos(s, e):
                        print(f"    ~ posible mismo club: '{s}'  vs  '{e}'")
    if not hubo:
        print("  Sin cambios de plantel entre temporadas (o una sola temporada).")
    print()
    print("  Cambios moderados = ascensos/descensos normales. Revisa los")
    print("  marcados con '~': pueden ser el mismo club con dos grafias.")


def _parecidos(a, b):
    a1, b1 = a.lower().split(), b.lower().split()
    if not a1 or not b1:
        return False
    # comparten la primera palabra significativa, o uno contiene al otro
    if a1[0] == b1[0] and len(a1[0]) >= 4:
        return True
    if a.lower() in b.lower() or b.lower() in a.lower():
        return True
    return False


def sanity_poisson(conn):
    linea("6. SANITY POISSON: goles del local, real vs Poisson(media)")
    filas = conn.execute("""
        SELECT competicion_codigo AS cod, goles_local AS g
        FROM partidos
        WHERE goles_local IS NOT NULL
    """).fetchall()

    porcomp = defaultdict(list)
    for f in filas:
        porcomp[f["cod"]].append(f["g"])

    print(f"{'cod':4} {'media':>6}  distribucion 0..5+  (R=real  P=poisson)")
    for cod, gs in sorted(porcomp.items()):
        n = len(gs)
        media = sum(gs) / n
        real = Counter(min(g, 5) for g in gs)
        real_pct = [real[k] / n for k in range(6)]
        pois_pct = [_poisson_pmf(k, media) for k in range(5)]
        pois_pct.append(1 - sum(pois_pct))
        r = " ".join(f"{p*100:4.1f}" for p in real_pct)
        p = " ".join(f"{p*100:4.1f}" for p in pois_pct)
        print(f"{cod:4} {media:>6.2f}  R: {r}")
        print(f"{'':4} {'':>6}  P: {p}")
    print()
    print("  Si R y P se parecen, una Poisson simple ya es un punto de")
    print("  partida razonable. Diferencias grandes en 0 y 1 gol suelen")
    print("  justificar el ajuste de Dixon-Coles.")


def _poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def main():
    conn = conectar()
    print(f"Base: {os.path.abspath(DB_PATH)}")
    cobertura(conn)
    goles_y_localia(conn)
    resultados(conn)
    calendario(conn)
    nombres_equipos(conn)
    sanity_poisson(conn)
    conn.close()
    print()
    print("Fin de la auditoria.")


if __name__ == "__main__":
    main()
