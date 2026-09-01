"""
Modelo Poisson / Dixon-Coles para estimar probabilidades de un partido
a partir de futbol.db.

Idea (Maher 1982, Dixon-Coles 1997):
  Para un partido entre el local i y el visitante j,

      goles_local   ~ Poisson(lambda_local)
      goles_visita  ~ Poisson(lambda_visita)

      log lambda_local = c + ventaja_local + ataque_i - defensa_j
      log lambda_visita = c            + ataque_j - defensa_i

  'ataque' y 'defensa' son parametros por equipo (con suma cero para que
  el modelo sea identificable). 'ventaja_local' captura el plus de jugar
  en casa que la exploracion confirmo en todas las ligas.

  Sobre eso se aplica:
   - correccion Dixon-Coles (rho): ajusta la probabilidad de los
     marcadores bajos (0-0, 1-0, 0-1, 1-1), donde la Poisson pura se
     queda corta.
   - ponderacion temporal: cada partido pesa exp(-xi * dias_de_antiguedad),
     con vida media configurable (por defecto 180 dias). Asi la forma
     reciente y los ascensos/descensos entran solos.

El modelo se ajusta POR LIGA (los entornos de gol son muy distintos:
~2.0 gol/partido en Argentina vs ~3.3 en Champions).

Uso por linea de comandos:
    python modelo.py --ligas
    python modelo.py PL --equipos
    python modelo.py PL "Arsenal FC" "Chelsea FC"
    python modelo.py PL "Arsenal FC" "Chelsea FC" --sin-dc
    python modelo.py PL "Arsenal FC" "Chelsea FC" --desde 2024

Uso como libreria:
    from modelo import ajustar_liga
    m = ajustar_liga("PL")
    print(m.predecir("Arsenal FC", "Chelsea FC"))
    for equipo, atk, dfn, peso, fiable in m.ranking(solo_fiables=True)[:5]:
        print(equipo, round(atk + dfn, 2))
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

DB_PATH = os.getenv("FUTBOL_DB", "futbol.db")
HALF_LIFE_DIAS = 365     # a los 365 dias un partido pesa la mitad
MAX_GOLES = 10           # tope de la matriz de marcadores

# Prior gaussiano N(0, PRIOR_SD^2) sobre ataque y defensa de cada equipo.
# Funciona como shrinkage bayesiano (MAP): a un equipo con muchos partidos
# los datos lo mandan; a uno con poquitos (recien ascendido, Argentina con
# media temporada) el prior lo acerca a la media de la liga en vez de
# dejar que 2 partidos le den una fuerza absurda.
PRIOR_SD = 0.6

# Debajo de este peso (partidos recientes equivalentes) la fuerza de un
# equipo es poco fiable: se marca en ranking() y dispara 'aviso' en predecir().
PESO_MIN_FIABLE = 8.0


def conectar():
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            f"No existe {DB_PATH}. Corre recolectar_datos.py (y el de CONMEBOL) "
            f"o define la variable de entorno FUTBOL_DB."
        )
    return sqlite3.connect(DB_PATH)


def _parse_fecha(s):
    # las fechas vienen en ISO, unas con 'Z' y otras con '+00:00'
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ligas_disponibles(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT competicion_codigo FROM partidos ORDER BY 1"
    )]


def cargar_partidos(conn, liga, hasta=None, desde_temporada=None):
    """Lista de (fecha, local, visitante, goles_local, goles_visita) de una liga.

    hasta            : fecha ISO; solo partidos anteriores (para backtesting).
    desde_temporada  : p.ej. '2024'; excluye temporadas mas viejas. Util para
                       dejar fuera una 2026 a medio jugar o temporadas que no
                       aportan por el decaimiento temporal.
    """
    q = ("SELECT fecha, equipo_local, equipo_visitante, goles_local, goles_visitante "
         "FROM partidos "
         "WHERE competicion_codigo = ? "
         "  AND goles_local IS NOT NULL AND goles_visitante IS NOT NULL")
    args = [liga]
    if hasta:
        q += " AND fecha < ?"
        args.append(hasta)
    if desde_temporada:
        q += " AND temporada >= ?"
        args.append(str(desde_temporada))
    q += " ORDER BY fecha"
    filas = conn.execute(q, args).fetchall()
    if not filas:
        disp = ", ".join(ligas_disponibles(conn))
        raise SystemExit(f"No hay partidos para la liga '{liga}'. Disponibles: {disp}")
    return filas


def temperatura_optima(P, y, grid=None):
    """T que minimiza log-loss al aplicar p -> p**(1/T) renormalizado.

    P: (n,k) probabilidades; y: (n,) indice de la clase real. T>1 suaviza
    (corrige exceso de confianza), T<1 agudiza."""
    P = np.clip(np.asarray(P, dtype=float), 1e-12, 1.0)
    y = np.asarray(y)
    idx = np.arange(len(y))
    if grid is None:
        grid = np.concatenate([np.linspace(0.5, 1.0, 11), np.linspace(1.05, 3.0, 40)])
    mejor_T, mejor_ll = 1.0, np.inf
    for T in grid:
        Q = P ** (1.0 / T)
        Q /= Q.sum(axis=1, keepdims=True)
        ll = -np.mean(np.log(np.clip(Q[idx, y], 1e-12, 1.0)))
        if ll < mejor_ll:
            mejor_ll, mejor_T = ll, float(T)
    return mejor_T


class ModeloPoisson:
    def __init__(self, dc=True, half_life_dias=HALF_LIFE_DIAS, prior_sd=PRIOR_SD,
                 temperatura=1.0):
        self.dc = dc
        self.prior_sd = prior_sd
        self.temperatura = temperatura     # >1 suaviza probabilidades (menos confianza)
        # factores que multiplican lambda al predecir. Los usa el modelo de
        # tiros: ajusta sobre tiros a puerta y luego escala a goles con la
        # tasa de conversion. 1.0 = sin efecto (modelo de goles normal).
        self.escala_local = 1.0
        self.escala_visita = 1.0
        self.xi = np.log(2) / half_life_dias
        self.liga = None
        self.equipos = []
        self.idx = {}
        self.params_ = None      # (c, ventaja_local, ataque[], defensa[], rho)
        self.resultado_ajuste_ = None

    # ------------------------------------------------------------------ ajuste
    def ajustar(self, filas, fecha_ref=None, verbose=False):
        if fecha_ref is None:
            fecha_ref = datetime.now(timezone.utc)

        equipos = sorted({f[1] for f in filas} | {f[2] for f in filas})
        self.equipos = equipos
        self.idx = {e: k for k, e in enumerate(equipos)}
        n = len(equipos)

        hi = np.array([self.idx[f[1]] for f in filas])
        ai = np.array([self.idx[f[2]] for f in filas])
        x = np.array([f[3] for f in filas], dtype=float)   # goles local
        y = np.array([f[4] for f in filas], dtype=float)   # goles visitante

        edad = np.array([(fecha_ref - _parse_fecha(f[0])).total_seconds() / 86400
                         for f in filas])
        w = np.exp(-self.xi * np.clip(edad, 0, None))

        # partidos recientes equivalentes que respaldan a cada equipo
        self._peso_equipo = {e: 0.0 for e in equipos}
        for k in range(len(filas)):
            self._peso_equipo[equipos[hi[k]]] += w[k]
            self._peso_equipo[equipos[ai[k]]] += w[k]

        gx, gy = gammaln(x + 1), gammaln(y + 1)
        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)

        # parametros libres: [c, ventaja_local, ataque(0..n-2), defensa(0..n-2), rho]
        # el ultimo ataque/defensa se deduce por suma cero.
        def desempaquetar(p):
            c = p[0]
            vloc = p[1]
            atk = np.empty(n)
            atk[:n - 1] = p[2:2 + (n - 1)]
            atk[n - 1] = -atk[:n - 1].sum()
            dfn = np.empty(n)
            dfn[:n - 1] = p[2 + (n - 1):2 + 2 * (n - 1)]
            dfn[n - 1] = -dfn[:n - 1].sum()
            rho = p[-1] if self.dc else 0.0
            return c, vloc, atk, dfn, rho

        def nll(p):
            c, vloc, atk, dfn, rho = desempaquetar(p)
            log_lh = c + vloc + atk[hi] - dfn[ai]
            log_la = c + atk[ai] - dfn[hi]
            lh, la = np.exp(log_lh), np.exp(log_la)
            ll = (x * log_lh - lh - gx) + (y * log_la - la - gy)
            if self.dc:
                tau = np.ones_like(lh)
                tau[m00] = 1 - lh[m00] * la[m00] * rho
                tau[m01] = 1 + lh[m01] * rho
                tau[m10] = 1 + la[m10] * rho
                tau[m11] = 1 - rho
                ll = ll + np.log(np.clip(tau, 1e-12, None))
            # -log prior gaussiano sobre fuerzas (identificabilidad + shrinkage)
            pen = 0.5 * (atk @ atk + dfn @ dfn) / self.prior_sd ** 2
            return -(w * ll).sum() + pen

        p0 = np.zeros(2 + 2 * (n - 1) + 1)
        p0[0] = np.log(max((x.mean() + y.mean()) / 2, 0.1))
        p0[1] = 0.2
        p0[-1] = -0.05 if self.dc else 0.0
        bounds = [(None, None)] * (len(p0) - 1)
        bounds += [(-0.4, 0.4) if self.dc else (0.0, 0.0)]

        res = minimize(nll, p0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 1000})
        self.params_ = desempaquetar(res.x)
        self.resultado_ajuste_ = res
        if verbose:
            c, vloc, atk, dfn, rho = self.params_
            print(f"  liga={self.liga}  partidos={len(filas)}  equipos={n}  "
                  f"convergio={res.success}")
            print(f"  nll={res.fun:.1f}  ventaja_local={vloc:.3f}  rho={rho:.3f}")
        return self

    # -------------------------------------------------------------- prediccion
    def _chequear(self, *equipos):
        faltan = [e for e in equipos if e not in self.idx]
        if faltan:
            raise SystemExit(
                f"Equipo(s) desconocido(s) en {self.liga}: {faltan}\n"
                f"Equipos disponibles:\n  " + "\n  ".join(self.equipos)
            )

    def fuerza(self, equipo):
        """Desglose de fuerza de un equipo (para mostrar el 'por que'):
        ataque, defensa, neto (=ataque+defensa) y si tiene datos suficientes."""
        if equipo not in self.idx:
            return None
        _, _, atk, dfn, _ = self.params_
        k = self.idx[equipo]
        peso = self._peso_equipo.get(equipo, 0.0)
        return {
            "ataque": round(float(atk[k]), 3),
            "defensa": round(float(dfn[k]), 3),
            "neto": round(float(atk[k] + dfn[k]), 3),
            "peso": round(peso, 1),
            "fiable": bool(peso >= PESO_MIN_FIABLE),
        }

    def tasas(self, local, visitante):
        """(lambda_local, lambda_visita) esperados para ese cruce."""
        self._chequear(local, visitante)
        c, vloc, atk, dfn, _ = self.params_
        i, j = self.idx[local], self.idx[visitante]
        lh = float(np.exp(c + vloc + atk[i] - dfn[j])) * self.escala_local
        la = float(np.exp(c + atk[j] - dfn[i])) * self.escala_visita
        return lh, la

    def matriz_marcadores(self, local, visitante, max_goles=MAX_GOLES):
        lh, la = self.tasas(local, visitante)
        _, _, _, _, rho = self.params_
        gk = np.arange(max_goles + 1)
        px = np.exp(gk * np.log(lh) - lh - gammaln(gk + 1))
        py = np.exp(gk * np.log(la) - la - gammaln(gk + 1))
        M = np.outer(px, py)
        if self.dc and rho != 0.0:
            M[0, 0] *= 1 - lh * la * rho
            M[0, 1] *= 1 + lh * rho
            M[1, 0] *= 1 + la * rho
            M[1, 1] *= 1 - rho
        M = np.clip(M, 0, None)
        M /= M.sum()
        return M, lh, la

    def predecir(self, local, visitante, max_goles=MAX_GOLES):
        M, lh, la = self.matriz_marcadores(local, visitante, max_goles)
        n = M.shape[0]
        total = np.add.outer(np.arange(n), np.arange(n))

        p1 = float(np.tril(M, -1).sum())     # goles_local > goles_visita
        px = float(np.trace(M))              # empate
        p2 = float(np.triu(M, 1).sum())      # goles_local < goles_visita
        p_over = float(M[total >= 3].sum())
        p_btts = float(M[1:, 1:].sum())

        # calibracion: suaviza (o agudiza) las probabilidades sin tocar el
        # orden. T=1 no hace nada; T>1 baja la confianza del modelo.
        T = self.temperatura
        if T != 1.0:
            v = np.array([p1, px, p2]) ** (1.0 / T)
            p1, px, p2 = (v / v.sum()).tolist()
            o = np.array([p_over, 1 - p_over]) ** (1.0 / T)
            p_over = float(o[0] / o.sum())
            b = np.array([p_btts, 1 - p_btts]) ** (1.0 / T)
            p_btts = float(b[0] / b.sum())

        planos = sorted(
            (((a, b), M[a, b]) for a in range(n) for b in range(n)),
            key=lambda t: t[1], reverse=True,
        )
        top = [(f"{a}-{b}", round(float(p), 4)) for (a, b), p in planos[:5]]

        def cuota(p):
            return round(1.0 / p, 2) if p > 0 else None

        peso_min = min(self._peso_equipo[local], self._peso_equipo[visitante])
        aviso = None
        if peso_min < PESO_MIN_FIABLE:
            flojo = local if self._peso_equipo[local] <= self._peso_equipo[visitante] else visitante
            aviso = (f"pocos datos: '{flojo}' solo tiene "
                     f"{self._peso_equipo[flojo]:.1f} partidos recientes equivalentes; "
                     f"la estimacion esta muy tirada al promedio de la liga")

        return {
            "liga": self.liga,
            "local": local,
            "visitante": visitante,
            "lambda_local": round(lh, 3),
            "lambda_visitante": round(la, 3),
            "prob_1": round(p1, 4),
            "prob_X": round(px, 4),
            "prob_2": round(p2, 4),
            "prob_over_2_5": round(p_over, 4),
            "prob_under_2_5": round(1 - p_over, 4),
            "prob_btts_si": round(p_btts, 4),
            "prob_btts_no": round(1 - p_btts, 4),
            "cuotas_justas_1x2": [cuota(p1), cuota(px), cuota(p2)],
            "marcadores_probables": top,
            "aviso": aviso,
        }

    def ranking(self, solo_fiables=False):
        """(equipo, ataque, defensa, peso, fiable) ordenado por fuerza neta desc.

        Con log lambda = c + ... + ataque_i - defensa_j, un 'defensa' mas
        alto = encaja menos = mejor defensa. Por eso la fuerza neta es
        ataque + defensa (ambos altos = equipo fuerte).

        'peso' = partidos recientes equivalentes; 'fiable' = peso por encima
        de PESO_MIN_FIABLE. Con solo_fiables=True se omiten los no fiables."""
        _, _, atk, dfn, _ = self.params_
        filas = []
        for e, k in self.idx.items():
            peso = self._peso_equipo[e]
            fiable = bool(peso >= PESO_MIN_FIABLE)
            if solo_fiables and not fiable:
                continue
            filas.append((e, float(atk[k]), float(dfn[k]), round(peso, 1), fiable))
        filas.sort(key=lambda t: t[1] + t[2], reverse=True)
        return filas

    def partidos_efectivos(self):
        """Suma de pesos temporales por equipo: 'cuantos partidos recientes
        equivalentes' respalda la estimacion de cada uno."""
        return dict(sorted(self._peso_equipo.items(), key=lambda t: t[1]))


def ajustar_liga(liga, conn=None, dc=True, half_life_dias=HALF_LIFE_DIAS,
                 prior_sd=PRIOR_SD, temperatura=1.0, hasta=None,
                 desde_temporada=None, verbose=False):
    propio = conn is None
    if propio:
        conn = conectar()
    try:
        filas = cargar_partidos(conn, liga, hasta=hasta,
                                desde_temporada=desde_temporada)
        m = ModeloPoisson(dc=dc, half_life_dias=half_life_dias, prior_sd=prior_sd,
                          temperatura=temperatura)
        m.liga = liga
        m.ajustar(filas, verbose=verbose)
        return m
    finally:
        if propio:
            conn.close()


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        conn = conectar()
        print(__doc__.split("Uso por linea de comandos:")[1].split('"""')[0].strip())
        print("\nLigas en la base:", ", ".join(ligas_disponibles(conn)))
        conn.close()
        return

    dc = "--sin-dc" not in args
    args = [a for a in args if a != "--sin-dc"]

    desde_temporada = None
    if "--desde" in args:
        i = args.index("--desde")
        desde_temporada = args[i + 1]
        del args[i:i + 2]

    conn = conectar()

    if args[0] == "--ligas":
        print(", ".join(ligas_disponibles(conn)))
        conn.close()
        return

    liga = args[0]
    if len(args) >= 2 and args[1] == "--equipos":
        filas = cargar_partidos(conn, liga)
        eq = sorted({f[1] for f in filas} | {f[2] for f in filas})
        print("\n".join(eq))
        conn.close()
        return

    if len(args) < 3:
        print('Faltan equipos. Ej: python modelo.py PL "Arsenal FC" "Chelsea FC"')
        conn.close()
        return

    local, visitante = args[1], args[2]
    m = ajustar_liga(liga, conn=conn, dc=dc,
                     desde_temporada=desde_temporada, verbose=True)
    conn.close()

    pred = m.predecir(local, visitante)
    print()
    for k, v in pred.items():
        print(f"  {k:22}: {v}")


if __name__ == "__main__":
    _cli()
