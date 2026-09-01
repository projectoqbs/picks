# picks

App de pronósticos de fútbol (PWA) sobre un modelo estadístico
Poisson / Dixon-Coles.

- **App en vivo:** https://projectoqbs.github.io/picks/
- Muestra los partidos del día de Premier, La Liga, Serie A, Bundesliga,
  Ligue 1 y Champions, cada uno con probabilidades 1X2, goles esperados,
  over/under, ambos marcan, fuerza de cada equipo, forma reciente y cara
  a cara.
- Es **base estadística para decidir**, no un pronóstico infalible ni un
  consejo de apuestas: el modelo predice razonablemente pero **no le gana
  al mercado** (ver `backtest_roi.py`).

## Cómo funciona

| Pieza | Rol |
|---|---|
| `modelo.py` | Modelo Poisson / Dixon-Coles por liga |
| `actualizar.py` | Baja resultados + fixtures de football-data.org |
| `generar_predicciones.py` | Entrena y escribe los JSON de `docs/data/` |
| `docs/` | La PWA (HTML/CSS/JS sin build) |
| `.github/workflows/actualizar.yml` | Corre el pipeline 2×/día y commitea los datos |
| `backtest*.py`, `simulador_banca.py` | Validación honesta del modelo |

Detalle de despliegue y uso local en [`APP.md`](APP.md).

## Setup del pipeline automático

El workflow necesita un secret del repo:
`Settings → Secrets and variables → Actions → New repository secret`
→ `FOOTBALL_DATA_API_TOKEN` con un token de https://www.football-data.org/
