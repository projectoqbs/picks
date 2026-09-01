"""
Backtest de ROI: ¿el edge del modelo se traduce en ganancia contra
cuotas reales de cierre del mercado?

Cuotas: CSV historicos gratuitos de football-data.co.uk (cuotas de
cierre de Pinnacle / Bet365 / media de mercado). Cubre las 5 ligas
domesticas europeas que tambien estan en futbol.db:
    PL -> E0 | PD -> SP1 | SA -> I1 | BL1 -> D1 | FL1 -> F1
(No hay Champions, Brasileirao ni Sudamerica en esa fuente.)

Metodo:
  1. Walk-forward igual que backtest.py: para cada partido de la ventana
     de test se reentrena el modelo con SOLO datos previos (reajuste cada
     7 dias) y se predice 1X2.
  2. Se empareja cada partido con su fila de cuotas (por fecha y nombres
     de equipo normalizados).
  3. Probabilidad implicita del mercado = (1/cuota) normalizada (se quita
     el margen de la casa de forma proporcional).
  4. Valor esperado de apostar al resultado i: EV_i = p_i * cuota_i - 1.
     Se apuesta al mejor EV si supera el umbral (def. 0.05).
  5. Staking: plano (1 u) y Kelly fraccionado (def. 1/4), con banca
     compuesta. Se reportan ROI, yield, acierto y drawdown.

Uso:
    python backtest_roi.py PL
    python backtest_roi.py PL --desde 2024-08-01 --umbral 0.03 --kelly 0.25
    python backtest_roi.py --todas
"""
import csv
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import numpy as np

from modelo import (conectar, cargar_partidos, ModeloPoisson, _parse_fecha,
                    HALF_LIFE_DIAS, PRIOR_SD)

CACHE_DIR = os.getenv("DIR_CUOTAS", "datos_cuotas")
DESDE_DEFECTO = "2024-08-01"
MIN_TRAIN = 150
REAJUSTE_DIAS = 7
UMBRAL_EV = 0.05
KELLY_FRAC = 0.25
BANCA_INICIAL = 100.0
KELLY_CAP = 0.05          # tope de fraccion de banca por apuesta

# liga interna -> (division en football-data.co.uk, temporadas a bajar)
DIV = {
    "PL":  ("E0",  ["2324", "2425", "2526"]),
    "PD":  ("SP1", ["2324", "2425", "2526"]),
    "SA":  ("I1",  ["2324", "2425", "2526"]),
    "BL1": ("D1",  ["2324", "2425", "2526"]),
    "FL1": ("F1",  ["2324", "2425", "2526"]),
}

# alias para los pocos nombres que el emparejado automatico no resuelve
ALIAS = {
    "PL": {"Manchester United FC": "Man United", "Manchester City FC": "Man City",
           "Newcastle United FC": "Newcastle", "Tottenham Hotspur FC": "Tottenham",
           "Wolverhampton Wanderers FC": "Wolves",
           "Nottingham Forest FC": "Nott'm Forest",
           "Sheffield United FC": "Sheffield United",
           "West Ham United FC": "West Ham", "Brighton & Hove Albion FC": "Brighton",
           "Leeds United FC": "Leeds", "Luton Town FC": "Luton",
           "Ipswich Town FC": "Ipswich", "Leicester City FC": "Leicester"},
    "PD": {"Atlético de Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao",
           "Club Atlético de Madrid": "Ath Madrid",
           "Rayo Vallecano de Madrid": "Vallecano",
           "RC Celta de Vigo": "Celta", "RCD Espanyol de Barcelona": "Espanol",
           "RCD Mallorca": "Mallorca", "Real Betis Balompié": "Betis",
           "Real Sociedad de Fútbol": "Sociedad", "Deportivo Alavés": "Alaves",
           "CA Osasuna": "Osasuna", "Girona FC": "Girona",
           "UD Almería": "Almeria", "Cádiz CF": "Cadiz",
           "RC Deportivo La Coruña": "La Coruna"},
    "SA":  {"FC Internazionale Milano": "Inter", "AC Milan": "Milan",
            "SSC Napoli": "Napoli", "AS Roma": "Roma", "Hellas Verona FC": "Verona",
            "US Lecce": "Lecce", "US Salernitana 1919": "Salernitana",
            "ACF Fiorentina": "Fiorentina", "Bologna FC 1909": "Bologna",
            "Parma Calcio 1913": "Parma", "Genoa CFC": "Genoa",
            "AC Monza": "Monza", "Como 1907": "Como", "Cagliari Calcio": "Cagliari",
            "Torino FC": "Torino", "Udinese Calcio": "Udinese",
            "Empoli FC": "Empoli", "US Sassuolo Calcio": "Sassuolo",
            "Frosinone Calcio": "Frosinone", "Venezia FC": "Venezia",
            "US Cremonese": "Cremonese", "AC Pisa 1909": "Pisa"},
    "BL1": {"FC Bayern München": "Bayern Munich", "Borussia Dortmund": "Dortmund",
            "Bayer 04 Leverkusen": "Leverkusen", "RB Leipzig": "RB Leipzig",
            "Borussia Mönchengladbach": "M'gladbach",
            "Eintracht Frankfurt": "Ein Frankfurt", "1. FC Union Berlin": "Union Berlin",
            "SC Freiburg": "Freiburg", "VfL Wolfsburg": "Wolfsburg",
            "1. FSV Mainz 05": "Mainz", "TSG 1899 Hoffenheim": "Hoffenheim",
            "VfB Stuttgart": "Stuttgart", "1. FC Köln": "FC Koln",
            "SV Werder Bremen": "Werder Bremen", "FC Augsburg": "Augsburg",
            "VfL Bochum 1848": "Bochum", "1. FC Heidenheim 1846": "Heidenheim",
            "SV Darmstadt 98": "Darmstadt", "Holstein Kiel": "Holstein Kiel",
            "FC St. Pauli 1910": "St Pauli", "Hamburger SV": "Hamburg"},
    "FL1": {"Paris Saint-Germain FC": "Paris SG", "Olympique de Marseille": "Marseille",
            "Olympique Lyonnais": "Lyon", "AS Monaco FC": "Monaco",
            "LOSC Lille": "Lille", "Stade Rennais FC 1901": "Rennes",
            "OGC Nice": "Nice", "RC Lens": "Lens", "Stade de Reims": "Reims",
            "Stade Brestois 29": "Brest", "Montpellier HSC": "Montpellier",
            "FC Nantes": "Nantes", "Toulouse FC": "Toulouse",
            "RC Strasbourg Alsace": "Strasbourg", "AJ Auxerre": "Auxerre",
            "Angers SCO": "Angers", "AS Saint-Étienne": "St Etienne",
            "FC Lorient": "Lorient", "FC Metz": "Metz", "Le Havre AC": "Le Havre",
            "Clermont Foot 63": "Clermont", "Paris FC": "Paris FC"},
}


# --------------------------------------------------------------- cuotas
# Fuente de los CSV: football-data.co.uk. En Colombia ese dominio esta
# bloqueado por Coljuegos (secuestro de DNS), asi que el script NO lo
# descarga solo: espera los archivos ya presentes en datos_cuotas/ con
# el nombre {DIV}_{TEMPORADA}.csv, p.ej. datos_cuotas/E0_2425.csv.
# Consíguelos como accedas normalmente a ese sitio y dejalos ahi.
URL_PLANTILLA = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"


def descargar(div, season):
    destino = os.path.join(CACHE_DIR, f"{div}_{season}.csv")
    if os.path.exists(destino) and os.path.getsize(destino) > 0:
        return destino
    raise SystemExit(
        f"Falta {destino}\n"
        f"  Descargalo de {URL_PLANTILLA.format(season=season, div=div)} "
        f"y ponlo en la carpeta {CACHE_DIR}/ con ese nombre exacto."
    )


def _leer_csv(path):
    with open(path, "r", encoding="latin-1", newline="") as f:
        return list(csv.DictReader(f))


def _fecha_couk(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _num(row, *claves):
    for k in claves:
        v = (row.get(k) or "").strip()
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    return None


def cargar_cuotas(liga):
    """Lista de dicts: fecha, home, away, fthg, ftag, o1, ox, o2 (cierre)."""
    if liga not in DIV:
        raise SystemExit(f"{liga}: sin cuotas en football-data.co.uk. "
                         f"Ligas con cuotas: {', '.join(DIV)}")
    div, seasons = DIV[liga]
    filas = []
    for season in seasons:
        for row in _leer_csv(descargar(div, season)):
            fecha = _fecha_couk((row.get("Date") or "").strip())
            if not fecha:
                continue
            o1 = _num(row, "PSCH", "B365CH", "AvgCH", "PSH", "B365H", "AvgH")
            ox = _num(row, "PSCD", "B365CD", "AvgCD", "PSD", "B365D", "AvgD")
            o2 = _num(row, "PSCA", "B365CA", "AvgCA", "PSA", "B365A", "AvgA")
            fthg = _num(row, "FTHG")
            ftag = _num(row, "FTAG")
            if None in (o1, ox, o2, fthg, ftag):
                continue
            filas.append({
                "fecha": fecha, "home": (row.get("HomeTeam") or "").strip(),
                "away": (row.get("AwayTeam") or "").strip(),
                "fthg": int(fthg), "ftag": int(ftag),
                "o1": o1, "ox": ox, "o2": o2,
            })
    return filas


# ---------------------------------------------------- emparejar nombres
def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    for t in (" fc", " cf", " afc", " sc", " ac", " as", " ss", " ssc", " us",
              " calcio", " balompie", " 1.", "1. ", " club", " de futbol"):
        s = s.replace(t, " ")
    return " ".join(s.split())


def mapa_nombres(liga, nombres_db, nombres_couk):
    alias = ALIAS.get(liga, {})
    couk_norm = {_norm(c): c for c in nombres_couk}
    mapa, sin_match = {}, []
    for db in nombres_db:
        if db in alias and alias[db] in nombres_couk:
            mapa[db] = alias[db]
            continue
        n = _norm(db)
        if n in couk_norm:
            mapa[db] = couk_norm[n]
            continue
        cand = get_close_matches(n, list(couk_norm), n=1, cutoff=0.72)
        if cand:
            mapa[db] = couk_norm[cand[0]]
        else:
            sin_match.append(db)
    return mapa, sin_match


# ------------------------------------------------------------- backtest
def resultado_1x2(gl, gv):
    return 0 if gl > gv else (1 if gl == gv else 2)


def _a_dt(s):
    if len(s) <= 10:
        s = s + "T00:00:00+00:00"
    return _parse_fecha(s)


def backtest_roi(conn, liga, desde=DESDE_DEFECTO, umbral=UMBRAL_EV,
                 kelly_frac=KELLY_FRAC, half_life=HALF_LIFE_DIAS,
                 prior_sd=PRIOR_SD, dc=True, reajuste_dias=REAJUSTE_DIAS,
                 min_train=MIN_TRAIN, verbose=True):
    filas = cargar_partidos(conn, liga)
    fechas = [_parse_fecha(f[0]) for f in filas]
    desde_dt = _a_dt(desde)

    cuotas = cargar_cuotas(liga)
    nombres_db = sorted({f[1] for f in filas} | {f[2] for f in filas})
    nombres_couk = sorted({c["home"] for c in cuotas} | {c["away"] for c in cuotas})
    mapa, sin_match = mapa_nombres(liga, nombres_db, nombres_couk)
    if sin_match and verbose:
        print(f"  sin emparejar ({len(sin_match)}): {sin_match}")

    # indice de cuotas por (fecha_iso_dia, home_couk, away_couk)
    cidx = {}
    for c in cuotas:
        cidx[(c["fecha"].date(), c["home"], c["away"])] = c

    test = [i for i, d in enumerate(fechas) if d >= desde_dt and i >= min_train]

    banca = BANCA_INICIAL
    pico = banca
    max_dd = 0.0
    stake_plano = retorno_plano = 0.0
    apuestas = []          # (resultado_apostado, cuota, acerto, ev)
    n_con_cuota = n_sin_cuota = 0
    modelo = None
    prox = None

    for i in test:
        corte = fechas[i]
        if modelo is None or corte >= prox:
            modelo = ModeloPoisson(dc=dc, half_life_dias=half_life, prior_sd=prior_sd)
            modelo.liga = liga
            modelo.ajustar(filas[:i], fecha_ref=corte)
            prox = corte + timedelta(days=reajuste_dias)

        f = filas[i]
        local, visita, gl, gv = f[1], f[2], f[3], f[4]
        if local not in modelo.idx or visita not in modelo.idx:
            continue
        hc, ac = mapa.get(local), mapa.get(visita)
        c = cidx.get((corte.date(), hc, ac))
        if c is None:  # tolerancia de +-1 dia
            for dd in (-1, 1):
                c = cidx.get(((corte + timedelta(days=dd)).date(), hc, ac))
                if c:
                    break
        if c is None:
            n_sin_cuota += 1
            continue
        n_con_cuota += 1

        pred = modelo.predecir(local, visita)
        p = np.array([pred["prob_1"], pred["prob_X"], pred["prob_2"]])
        o = np.array([c["o1"], c["ox"], c["o2"]])
        ev = p * o - 1.0
        k = int(np.argmax(ev))
        if ev[k] < umbral:
            continue

        real = resultado_1x2(gl, gv)
        acerto = (k == real)
        cuota = o[k]

        # staking plano
        stake_plano += 1.0
        retorno_plano += (cuota if acerto else 0.0)

        # staking Kelly fraccionado sobre banca compuesta
        f_kelly = max(0.0, (p[k] * cuota - 1.0) / (cuota - 1.0))
        frac = min(kelly_frac * f_kelly, KELLY_CAP)
        stake = banca * frac
        banca += stake * (cuota - 1.0) if acerto else -stake
        pico = max(pico, banca)
        max_dd = max(max_dd, (pico - banca) / pico if pico > 0 else 0.0)

        apuestas.append((k, cuota, acerto, float(ev[k])))

    n_ap = len(apuestas)
    roi_plano = (retorno_plano - stake_plano) / stake_plano if stake_plano else 0.0
    aciertos = sum(1 for _, _, a, _ in apuestas if a)
    cuota_media = float(np.mean([c for _, c, _, _ in apuestas])) if apuestas else 0.0
    ev_medio = float(np.mean([e for _, _, _, e in apuestas])) if apuestas else 0.0

    por_res = {}
    for k in (0, 1, 2):
        sub = [(cu, a) for kk, cu, a, _ in apuestas if kk == k]
        if sub:
            st = len(sub)
            ret = sum(cu for cu, a in sub if a)
            por_res["1X2"[k]] = {"apuestas": st, "roi": (ret - st) / st,
                                 "acierto": sum(a for _, a in sub) / st}

    res = {
        "liga": liga, "desde": desde, "umbral": umbral,
        "config": dict(half_life=half_life, prior_sd=prior_sd, dc=dc),
        "partidos_con_cuota": n_con_cuota, "partidos_sin_cuota": n_sin_cuota,
        "apuestas": n_ap,
        "acierto_apuestas": aciertos / n_ap if n_ap else 0.0,
        "cuota_media": cuota_media, "ev_medio_esperado": ev_medio,
        "roi_plano": roi_plano,
        "banca_final_kelly": banca, "retorno_kelly": banca / BANCA_INICIAL - 1.0,
        "max_drawdown_kelly": max_dd,
        "por_resultado": por_res,
    }
    if verbose:
        _imprimir(res)
    return res


def _imprimir(r):
    c = r["config"]
    print(f"\n== ROI {r['liga']}  (desde {r['desde']}, umbral EV {r['umbral']}, "
          f"hl={c['half_life']} sd={c['prior_sd']} dc={int(c['dc'])}) ==")
    print(f"  partidos con cuota .....: {r['partidos_con_cuota']} "
          f"(sin emparejar cuota: {r['partidos_sin_cuota']})")
    print(f"  apuestas realizadas ....: {r['apuestas']}")
    if not r["apuestas"]:
        print("  -> el modelo no vio valor por encima del umbral.")
        return
    print(f"  acierto de las apuestas : {r['acierto_apuestas']*100:.1f}%  "
          f"(cuota media {r['cuota_media']:.2f})")
    print(f"  EV medio esperado ......: {r['ev_medio_esperado']*100:+.1f}%")
    print(f"  ROI staking plano ......: {r['roi_plano']*100:+.1f}%")
    print(f"  retorno Kelly 1/4 ......: {r['retorno_kelly']*100:+.1f}%  "
          f"(banca {BANCA_INICIAL:.0f} -> {r['banca_final_kelly']:.1f}, "
          f"drawdown max {r['max_drawdown_kelly']*100:.1f}%)")
    for k, d in r["por_resultado"].items():
        print(f"    {k}: {d['apuestas']:>3} apuestas  ROI {d['roi']*100:+6.1f}%  "
              f"acierto {d['acierto']*100:.0f}%")


def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.split("Uso:")[1].strip())
        return

    kw = {}
    for flag, clave, conv in (("--desde", "desde", str), ("--umbral", "umbral", float),
                              ("--kelly", "kelly_frac", float)):
        if flag in args:
            i = args.index(flag)
            kw[clave] = conv(args[i + 1])
            del args[i:i + 2]

    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = conectar()
    ligas = list(DIV) if args[0] == "--todas" else [args[0]]
    for liga in ligas:
        try:
            backtest_roi(conn, liga, **kw)
        except SystemExit as e:
            print(f"\n== {liga} ==\n  {e}")
    conn.close()


if __name__ == "__main__":
    _cli()
