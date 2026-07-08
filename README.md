# Kerala Weather Risk Dashboard

Hyperlocal rainfall & storm nowcasting demo for Kerala, India: a live,
district-level flood/storm risk map with **explainable** composite scoring —
every score is decomposed into the factors that drove it, in plain language.

Built entirely on free, key-less data services (Open-Meteo forecast +
Copernicus GloFAS river discharge), so it runs at zero cost on Streamlit
Community Cloud or Hugging Face Spaces.

## Features

- **Risk map** — Kerala's 14 districts coloured green/yellow/orange/red by a
  composite 0–100 weather risk score for the next 24 h.
- **Explainability panel** — click any district: 48 h rainfall chart, factor
  contribution breakdown, and a one-line reason ("High risk, mainly due to
  85 mm of rain expected in the next 24h and river discharge at 2.3× its
  30-day average").
- **Location search** — any Kerala town/village → point forecast + risk.
- **About & Methodology** page — data sources, thresholds, limitations.
- **Graceful degradation** — 30 min response caching; if an API is down the
  app serves the last good data with a visible timestamp, never a crash.

## Repository layout

```
app.py                        Streamlit entry point (map + detail panel)
pages/
  1_About_and_Methodology.py  Methodology page
src/
  config.py                   Districts, endpoints, constants
  data_sources.py             Open-Meteo / GloFAS clients, caching, fallback
  risk_engine.py              Isolated 0–100 scoring module (pure, documented)
  geo.py                      District GeoJSON handling
data/
  kerala_districts.geojson    14-district boundaries (2011 census, open data)
.streamlit/config.toml        Theme
requirements.txt
```

`src/risk_engine.py` is deliberately self-contained (no I/O, no Streamlit)
with documented thresholds and `TODO` markers — this is the module the
science team replaces with radar-based retrieval/nowcasting later.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501.

## Deploy — Streamlit Community Cloud (recommended)

1. Push this repository to GitHub (public repo).
2. Go to https://share.streamlit.io → **Create app**.
3. Select the repo, branch `main`, main file `app.py` → **Deploy**.

No secrets or environment variables are needed.

## Deploy — Hugging Face Spaces

1. Create a new Space at https://huggingface.co/new-space with SDK
   **Streamlit**.
2. Push this repo to the Space (or upload the files):

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/<space>
   git push space main
   ```

3. The Space builds from `requirements.txt` and serves `app.py`
   automatically.

## Data sources

| Source | Used for | Terms |
|---|---|---|
| [Open-Meteo Forecast API](https://open-meteo.com) | Hourly precipitation, CAPE, wind gusts | Free for non-commercial use, no key |
| [Open-Meteo Flood API](https://open-meteo.com/en/docs/flood-api) (GloFAS) | River discharge anomaly | Free, no key |
| [Open-Meteo Geocoding](https://open-meteo.com/en/docs/geocoding-api) | Kerala place search | Free, no key |
| District GeoJSON | Map boundaries | Open data (2011 census boundaries) |

## Disclaimer

This is a demonstration MVP with heuristic screening scores, **not** an
official warning system. Authoritative alerts are issued by the India
Meteorological Department (IMD) and the Kerala State Disaster Management
Authority (KSDMA).
