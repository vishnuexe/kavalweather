"""About & Methodology page — data sources, scoring approach, limitations."""

import streamlit as st

st.set_page_config(page_title="About & Methodology — KavalWeather",
                   page_icon="🌧️", layout="centered")

st.title("About & Methodology")

st.markdown("""
## What KavalWeather does

**KavalWeather** (*kaval* — Malayalam for "watch, guard") provides a district-level, 24-hour
composite indicator of rainfall and storm-related risk across Kerala's 14
districts, together with point forecasts for any searched location. Its
distinguishing feature is **explainability**: every score is decomposed into
its contributing factors and expressed in plain language, so users can see
*why* a district is flagged — not just that it is.

This is an MVP demonstrator. The scoring is a transparent screening
heuristic designed to be replaced by physically based retrieval and
nowcasting algorithms as the product matures.

## Data sources

| Source | Variables | Cadence | Access |
|---|---|---|---|
| [Open-Meteo Forecast API](https://open-meteo.com) | Hourly precipitation, CAPE, 10 m wind gusts (best-match blend of global/regional NWP, incl. ECMWF IFS & ICON) | Hourly, 7-day horizon + past 24 h | Free, no key |
| [Open-Meteo Flood API](https://open-meteo.com/en/docs/flood-api) | Daily river discharge from **GloFAS** (Copernicus Global Flood Awareness System, ~5 km grid) | Daily, 30-day history + 7-day forecast | Free, no key |
| District boundaries | 2011 Census district polygons (public GeoJSON) | Static | Open data |

API responses are cached for 30 minutes. If a live source becomes
unreachable, the dashboard serves the last successful retrieval with a
visible timestamp rather than failing.

## Composite risk score

For each district centroid (and any searched point) we aggregate the coming
24 hours of forecast data into five indicators:

1. **Accumulated rainfall (24 h)** — weighted 35%. Breakpoints follow IMD
   operational rainfall categories: *heavy* ≥ 64.5 mm, *very heavy*
   ≥ 115.6 mm, *extremely heavy* ≥ 204.5 mm per 24 h.
2. **Peak hourly intensity** — weighted 15%. Short-duration intensity is a
   primary driver of flash flooding and urban inundation; 15 mm/h marks
   intense convective rain, 50 mm/h cloudburst-scale rates.
3. **Convective potential (CAPE)** — weighted 15%. Convective Available
   Potential Energy above ~1000 J/kg indicates a moderately unstable
   atmosphere; above ~2500 J/kg, significant thunderstorm potential.
4. **Wind gusts** — weighted 15%. Anchored to IMD wind-warning thresholds
   (gale ≈ 62 km/h; severe ≈ 89 km/h; cyclonic storm ≈ 118 km/h).
5. **River discharge anomaly** — weighted 20%. The ratio of the maximum
   GloFAS-forecast discharge over the next 72 h to the trailing 30-day mean
   discharge. Ratios ≥ 2 indicate rapid river rise; ≥ 5, a major
   hydrological anomaly.

Each indicator is mapped to a 0–100 component score by piecewise-linear
interpolation over the thresholds above, then combined as a weighted sum.
The composite is banded as **Low** (< 25), **Moderate** (25–50), **High**
(50–75) and **Severe** (≥ 75).

Because the transform is linear and additive, the score is exactly
decomposable: the "Why this score?" panel reports each factor's
contribution in points, in the same units as the composite.

## Known limitations (MVP)

* District risk is evaluated at the polygon centroid — orographic gradients
  (notably across Idukki and Wayanad) are not yet resolved.
* GloFAS discharge is a ~5 km gridded product; small coastal catchments may
  return no river cell, in which case the flood factor is treated as
  neutral and flagged as "no data".
* Thresholds are climatologically generic, not yet calibrated per district
  against historical Kerala flood events (2018, 2019, 2021).
* This tool issues **no official warnings**. Authoritative alerts come from
  the India Meteorological Department (IMD) and the Kerala State Disaster
  Management Authority (KSDMA).

## Roadmap

* Ingest IMD Doppler weather radar (DWR Kochi/Thiruvananthapuram) for
  quantitative precipitation estimation and 0–3 h extrapolation nowcasts.
* Per-district hazard calibration using terrain, soil saturation and the
  antecedent precipitation index.
* Probabilistic scoring from NWP ensembles rather than deterministic
  best-match forecasts.

---
*KavalWeather — built for demonstration under the Kerala Startup Mission
programme. Contact: the project team.*
""")
