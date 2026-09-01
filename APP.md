# App de pronósticos (PWA)

Muestra los partidos del día de Premier, La Liga, Serie A, Bundesliga,
Ligue 1 y Champions, cada uno con el análisis del modelo (probabilidades
1X2, goles esperados, over/under, ambos marcan, marcadores probables).

## Piezas

| Archivo | Qué hace |
|---|---|
| `actualizar.py` | Baja resultados recientes (→ `futbol.db`) y fixtures de los próximos 10 días desde football-data.org → `docs/data/fixtures.json` |
| `generar_predicciones.py` | Entrena el modelo por liga y escribe `docs/data/AAAA-MM-DD.json` + `index.json` |
| `docs/` | La PWA: `index.html`, `app.js`, `styles.css`, `sw.js`, `manifest.webmanifest` + los JSON de datos |
| `.github/workflows/actualizar.yml` | Corre los dos scripts 2×/día y commitea los datos nuevos |
| `_iconos.py` | Regenera los iconos PNG (solo si querés cambiarlos) |

## Correr el pipeline a mano (local)

```bash
python actualizar.py
python generar_predicciones.py
# previsualizar:
cd docs && python -m http.server 8000   # http://localhost:8000
```

## Publicar gratis (GitHub Pages + Actions)

1. **Crear repo en GitHub** y subir esta rama:
   ```bash
   git remote add origin https://github.com/<usuario>/<repo>.git
   git push -u origin HEAD
   ```
   (o mergear a `main` y pushear).

2. **Secret con la API key**: repo → Settings → Secrets and variables →
   Actions → *New repository secret*
   - Name: `FOOTBALL_DATA_API_TOKEN`
   - Value: tu token de football-data.org

3. **Activar Pages**: Settings → Pages → Source = *Deploy from a branch*,
   Branch = `main`, carpeta = `/docs`. Guarda.

4. **Primera corrida**: pestaña Actions → "actualizar predicciones" →
   *Run workflow*. Cuando termine, commitea los JSON y Pages publica.

5. **Instalar en el iPhone**: abrí
   `https://<usuario>.github.io/<repo>/` en Safari →
   botón Compartir → *Añadir a pantalla de inicio*.

A partir de ahí se actualiza sola 2 veces al día.

## Límites de la v1

- **Europa League** no está (no la da el plan free de football-data.org).
- Las predicciones son de un modelo Poisson/Dixon-Coles sobre resultados
  históricos: estiman lo más probable, no le ganan al mercado de apuestas
  (ver `backtest_roi.py`). La app no muestra cuotas ni consejos de apuesta.
- Equipos recién ascendidos salen marcados "datos limitados" hasta que
  acumulan partidos.
