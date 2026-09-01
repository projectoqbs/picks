"""
Contexto adicional para leer una prediccion: forma reciente y cara a
cara historico. No entra en el modelo, es informacion aparte para que
el usuario arme su propio criterio ademas del numero del modelo.
"""


def forma_reciente(conn, liga, equipo, hasta_fecha, n=5):
    """Ultimos n partidos de 'equipo' en esa liga antes de hasta_fecha."""
    q = """SELECT fecha, equipo_local, equipo_visitante, goles_local, goles_visitante
           FROM partidos
           WHERE competicion_codigo = ? AND (equipo_local = ? OR equipo_visitante = ?)
             AND fecha < ? AND goles_local IS NOT NULL
           ORDER BY fecha DESC LIMIT ?"""
    filas = conn.execute(q, (liga, equipo, equipo, hasta_fecha, n)).fetchall()
    filas = list(reversed(filas))     # orden cronologico para la racha

    g = e = p = gf = gc = 0
    racha = []
    for _, hl, hv, gl, gv in filas:
        es_local = hl == equipo
        gf_p, gc_p = (gl, gv) if es_local else (gv, gl)
        gf += gf_p
        gc += gc_p
        if gf_p > gc_p:
            g += 1
            racha.append("G")
        elif gf_p == gc_p:
            e += 1
            racha.append("E")
        else:
            p += 1
            racha.append("P")
    return {"pj": len(filas), "g": g, "e": e, "p": p, "gf": gf, "gc": gc, "racha": racha}


def cara_a_cara(conn, liga, local, visitante, hasta_fecha, n=5):
    """Ultimos n enfrentamientos entre los dos equipos en esa liga."""
    q = """SELECT fecha, equipo_local, equipo_visitante, goles_local, goles_visitante
           FROM partidos
           WHERE competicion_codigo = ?
             AND ((equipo_local = ? AND equipo_visitante = ?)
                  OR (equipo_local = ? AND equipo_visitante = ?))
             AND fecha < ? AND goles_local IS NOT NULL
           ORDER BY fecha DESC LIMIT ?"""
    filas = conn.execute(q, (liga, local, visitante, visitante, local,
                             hasta_fecha, n)).fetchall()
    return [
        {"fecha": f[0][:10], "local": f[1], "visitante": f[2],
         "goles_local": f[3], "goles_visitante": f[4]}
        for f in filas
    ]
