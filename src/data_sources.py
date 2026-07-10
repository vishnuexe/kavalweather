"""Free-data clients: Open-Meteo forecast, GloFAS flood, and geocoding APIs.

Design notes
------------
* All 14 district centroids are fetched in a **single** multi-location
  request per API (comma-separated lat/lon lists), so a full dashboard
  refresh costs 2 HTTP calls.
* Responses are cached for ``CACHE_TTL_SECONDS`` via ``st.cache_data``
  when running under Streamlit (no-op decorator otherwise, keeping this
  module importable/testable without Streamlit).
* Graceful degradation: the last successful payload per request is kept
  in a module-level store that outlives the cache TTL. If a live fetch
  fails, we serve that stale copy with its original timestamp instead of
  crashing; callers surface the staleness to the user.
"""

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from src import config

# ---------------------------------------------------------------------------
# Caching layer
# ---------------------------------------------------------------------------

try:  # pragma: no cover - depends on runtime environment
    import streamlit as st

    _cached = st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
except Exception:  # noqa: BLE001 - any import/runtime issue -> plain functions
    def _cached(fn):
        return fn

#: Last known-good payloads, keyed by (kind, request signature).
#: Survives cache-TTL expiry for graceful degradation; process-lifetime only.
_LAST_GOOD: Dict[Tuple[str, str], Tuple[dt.datetime, Any]] = {}


def _get_json(url: str, params: Dict[str, Any]) -> Any:
    resp = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Raw fetches (cached)
# ---------------------------------------------------------------------------

@_cached
def _fetch_forecast_raw(lat_csv: str, lon_csv: str) -> Any:
    """Hourly forecast for one or more locations (comma-separated coords)."""
    return _get_json(config.FORECAST_URL, {
        "latitude": lat_csv,
        "longitude": lon_csv,
        "hourly": ",".join(config.HOURLY_VARS),
        "forecast_days": config.FORECAST_DAYS,
        "past_days": config.PAST_DAYS,
        "timezone": config.TIMEZONE,
    })


@_cached
def _fetch_flood_raw(lat_csv: str, lon_csv: str) -> Any:
    """Daily GloFAS river discharge for one or more locations."""
    return _get_json(config.FLOOD_URL, {
        "latitude": lat_csv,
        "longitude": lon_csv,
        "daily": "river_discharge",
        "past_days": config.FLOOD_PAST_DAYS,
        "forecast_days": config.FLOOD_FORECAST_DAYS,
    })


def _fetch_with_fallback(kind: str, fetch_fn, *args) -> Tuple[Optional[Any], Optional[dt.datetime], bool]:
    """Run a cached fetch; on failure fall back to the last good payload.

    Returns (payload, fetched_at_utc, is_stale). payload is None only if the
    fetch failed and nothing was ever cached.
    """
    key = (kind, "|".join(str(a) for a in args))
    try:
        data = fetch_fn(*args)
        fetched_at = dt.datetime.now(dt.timezone.utc)
        _LAST_GOOD[key] = (fetched_at, data)
        return data, fetched_at, False
    except Exception:  # noqa: BLE001 - degrade, never crash the dashboard
        if key in _LAST_GOOD:
            fetched_at, data = _LAST_GOOD[key]
            return data, fetched_at, True
        return None, None, True


def _as_location_list(payload: Any) -> List[dict]:
    """Open-Meteo returns a dict for 1 location, a list for many."""
    if payload is None:
        return []
    return payload if isinstance(payload, list) else [payload]


# ---------------------------------------------------------------------------
# Public API: district-level bundle
# ---------------------------------------------------------------------------

def fetch_all_districts() -> Dict[str, Any]:
    """Fetch weather + flood data for all 14 district centroids.

    Returns a dict with:
        ``weather``: {district: per-location forecast JSON}
        ``flood``:   {district: per-location flood JSON}
        ``fetched_at``: UTC datetime of the oldest payload served
        ``stale``: True if any payload came from the degradation store
        ``failed``: True if data is entirely unavailable (first-run outage)
    """
    names = config.DISTRICT_NAMES
    lat_csv = ",".join(str(config.DISTRICT_CENTROIDS[n][0]) for n in names)
    lon_csv = ",".join(str(config.DISTRICT_CENTROIDS[n][1]) for n in names)

    weather, w_ts, w_stale = _fetch_with_fallback("forecast", _fetch_forecast_raw, lat_csv, lon_csv)
    flood, f_ts, f_stale = _fetch_with_fallback("flood", _fetch_flood_raw, lat_csv, lon_csv)

    weather_list = _as_location_list(weather)
    flood_list = _as_location_list(flood)

    timestamps = [t for t in (w_ts, f_ts) if t is not None]
    return {
        "weather": dict(zip(names, weather_list)) if len(weather_list) == len(names) else {},
        "flood": dict(zip(names, flood_list)) if len(flood_list) == len(names) else {},
        "fetched_at": min(timestamps) if timestamps else None,
        "stale": w_stale or f_stale,
        "failed": weather is None,  # weather is essential; flood is optional
    }


def fetch_point(lat: float, lon: float) -> Dict[str, Any]:
    """Fetch weather + flood data for a single searched location."""
    weather, w_ts, w_stale = _fetch_with_fallback(
        "forecast", _fetch_forecast_raw, str(round(lat, 4)), str(round(lon, 4)))
    flood, f_ts, f_stale = _fetch_with_fallback(
        "flood", _fetch_flood_raw, str(round(lat, 4)), str(round(lon, 4)))
    return {
        "weather": weather,
        "flood": flood,
        "fetched_at": w_ts,
        "stale": w_stale or f_stale,
        "failed": weather is None,
    }


# ---------------------------------------------------------------------------
# Geocoding (Kerala-restricted)
# ---------------------------------------------------------------------------

@_cached
def geocode_kerala(query: str) -> List[Dict[str, Any]]:
    """Search for places in Kerala matching ``query``.

    Tries Open-Meteo geocoding first; its GeoNames index is thin on smaller
    Kerala towns and villages (misses e.g. Kattappana, Kalpetta), so when it
    returns nothing we fall back to OSM Nominatim bounded to the Kerala
    bbox. Both are free and key-less. Returns [] on total failure — the
    search box degrades quietly rather than crashing.
    """
    results = _geocode_open_meteo(query)
    if not results:
        results = _geocode_nominatim(query)
    return results


def _geocode_open_meteo(query: str) -> List[Dict[str, Any]]:
    try:
        data = _get_json(config.GEOCODING_URL, {
            "name": query, "count": 10, "language": "en", "format": "json",
        })
    except Exception:  # noqa: BLE001
        return []
    results = []
    for r in data.get("results", []) or []:
        in_kerala = r.get("admin1") == "Kerala" or (
            r.get("country_code") == "IN"
            and config.KERALA_BBOX["lat_min"] <= r.get("latitude", 0) <= config.KERALA_BBOX["lat_max"]
            and config.KERALA_BBOX["lon_min"] <= r.get("longitude", 0) <= config.KERALA_BBOX["lon_max"]
        )
        if in_kerala:
            results.append({
                "name": r["name"],
                "admin2": r.get("admin2", ""),
                "latitude": r["latitude"],
                "longitude": r["longitude"],
            })
    return results


@_cached
def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """Short place name for a coordinate (OSM Nominatim, village-level zoom).

    Used by the 3D terrain view to name a clicked spot. Returns None on any
    failure — callers show a generic label instead.
    """
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2",
                    "zoom": 14, "addressdetails": 1},
            headers={"User-Agent": "KavalWeather/0.1 (https://github.com/vishnuexe/kavalweather)"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    addr = data.get("address", {})
    for key in ("village", "town", "suburb", "municipality", "city",
                "hamlet", "locality", "county"):
        if addr.get(key):
            return addr[key]
    name = data.get("name") or data.get("display_name", "").split(",")[0]
    return name or None


def _geocode_nominatim(query: str) -> List[Dict[str, Any]]:
    """OSM Nominatim fallback, restricted to the Kerala bounding box.

    Nominatim usage policy: identify the app via User-Agent and stay under
    1 request/second — searches here are user-typed, cached for 30 min, and
    only fired when Open-Meteo found nothing.
    """
    bbox = config.KERALA_BBOX
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query, "format": "jsonv2", "limit": 10,
                "countrycodes": "in", "addressdetails": 1, "bounded": 1,
                "viewbox": "{},{},{},{}".format(
                    bbox["lon_min"], bbox["lat_max"], bbox["lon_max"], bbox["lat_min"]),
            },
            headers={"User-Agent": "KavalWeather/0.1 (https://github.com/vishnuexe/kavalweather)"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    results: List[Dict[str, Any]] = []
    for r in data:
        addr = r.get("address", {})
        if addr.get("state") != "Kerala":
            continue
        name = r.get("name") or r.get("display_name", "").split(",")[0]
        lat, lon = float(r["lat"]), float(r["lon"])
        # Nominatim often returns the same town as several nearby OSM
        # objects; keep the first of each (name, ~2 km cell) pair.
        key = (name, round(lat, 2), round(lon, 2))
        if any((x["name"], round(x["latitude"], 2), round(x["longitude"], 2)) == key
               for x in results):
            continue
        results.append({
            "name": name,
            "admin2": addr.get("state_district", "") or addr.get("county", ""),
            "latitude": lat,
            "longitude": lon,
        })
    return results


# ---------------------------------------------------------------------------
# Derived aggregates: raw JSON -> RiskInputs fields + chart frames
# ---------------------------------------------------------------------------

def hourly_dataframe(weather_json: dict) -> pd.DataFrame:
    """Hourly forecast JSON -> DataFrame with a parsed local-time column."""
    df = pd.DataFrame(weather_json["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def _now_local() -> pd.Timestamp:
    """Current hour in Kerala local time, naive (matches API timestamps)."""
    return pd.Timestamp.now(tz=config.TIMEZONE).floor("h").tz_localize(None)


def derive_risk_inputs(weather_json: Optional[dict], flood_json: Optional[dict],
                       terrain_stats: Optional[Dict[str, float]] = None) -> Dict[str, Optional[float]]:
    """Aggregate raw API payloads into the fields of ``RiskInputs``.

    All aggregation windows start at the current local hour. Missing series
    yield ``None`` so the risk engine can flag them as "no data".
    ``terrain_stats`` is the static DEM summary from ``src.terrain``
    (or None when the location has no terrain data).
    """
    out: Dict[str, Optional[float]] = {
        "rain_24h_mm": None, "peak_hourly_rain_mm": None,
        "cape_max_jkg": None, "wind_gust_max_kmh": None,
        "discharge_ratio": None,
        "mean_slope_deg": (terrain_stats or {}).get("mean_slope_deg"),
        "lowland_frac": (terrain_stats or {}).get("lowland_frac"),
    }
    if weather_json and "hourly" in weather_json:
        df = hourly_dataframe(weather_json)
        now = _now_local()
        nxt = df[(df["time"] >= now) & (df["time"] < now + pd.Timedelta(hours=24))]
        if not nxt.empty:
            rain = nxt["precipitation"].dropna()
            if not rain.empty:
                out["rain_24h_mm"] = float(rain.sum())
                out["peak_hourly_rain_mm"] = float(rain.max())
            cape = nxt["cape"].dropna()
            if not cape.empty:
                out["cape_max_jkg"] = float(cape.max())
            gust = nxt["wind_gusts_10m"].dropna()
            if not gust.empty:
                out["wind_gust_max_kmh"] = float(gust.max())

    out["discharge_ratio"] = _discharge_ratio(flood_json)
    return out


def _discharge_ratio(flood_json: Optional[dict]) -> Optional[float]:
    """Max GloFAS discharge over the next 72 h vs the trailing 30-day mean.

    Returns None when the location has no GloFAS river cell (common right on
    the coast) or the baseline is effectively zero.
    """
    if not flood_json or "daily" not in flood_json:
        return None
    daily = flood_json["daily"]
    times = pd.to_datetime(daily.get("time", []))
    values = pd.Series(daily.get("river_discharge", []), dtype="float64")
    if len(times) == 0 or values.dropna().empty:
        return None
    today = _now_local().normalize()
    past = values[(times < today)].dropna()
    future = values[(times >= today) & (times < today + pd.Timedelta(days=3))].dropna()
    if past.empty or future.empty:
        return None
    baseline = float(past.mean())
    if baseline < 1e-3:  # dry/no-river cell: ratio is meaningless
        return None
    return float(future.max()) / baseline


def past_24h_rain(weather_json: Optional[dict]) -> Optional[float]:
    """Accumulated rain over the previous 24 h (antecedent-wetness context)."""
    if not weather_json or "hourly" not in weather_json:
        return None
    df = hourly_dataframe(weather_json)
    now = _now_local()
    past = df[(df["time"] < now) & (df["time"] >= now - pd.Timedelta(hours=24))]
    rain = past["precipitation"].dropna()
    return float(rain.sum()) if not rain.empty else None


def rain_chart_frame(weather_json: dict, hours: int = 48) -> pd.DataFrame:
    """Hourly precipitation frame for the next ``hours`` hours (charting)."""
    df = hourly_dataframe(weather_json)
    now = _now_local()
    window = df[(df["time"] >= now) & (df["time"] < now + pd.Timedelta(hours=hours))]
    return window[["time", "precipitation"]].reset_index(drop=True)
