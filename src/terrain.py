"""Digital Elevation Model (DEM) layer: district terrain stats + 3D grids.

Data source: AWS Terrain Tiles ("terrarium" encoding), a free, key-less
public dataset on S3 derived from SRTM/Copernicus DEM and bathymetry
(https://registry.opendata.aws/terrain-tiles/). Elevation is decoded from
tile RGB as ``R*256 + G + B/256 - 32768`` metres.

Two consumers:

* **Risk scoring** — per-district terrain statistics (mean slope, share of
  low-lying land). These are static, so they are precomputed by
  ``scripts/build_terrain_stats.py`` into ``data/terrain_stats.json`` and
  loaded from disk at runtime (zero API calls). If the file is missing the
  stats are computed live and cached.
* **3D terrain view** — an elevation grid clipped to the selected district
  polygon, fetched on demand and cached for the session.
"""

import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, Optional, Tuple

import numpy as np
import requests
from PIL import Image

from src import config, geo

# Same AWS Open Data bucket via two URL styles: virtual-hosted first
# (path-style is legacy and unreliable from some networks).
TILE_URLS = [
    "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png",
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
]
STATS_PATH = config.REPO_ROOT / "data" / "terrain_stats.json"

ZOOM = 10          # ~150 m/pixel at Kerala latitudes: fine for district stats
MAX_GRID = 140     # max cells per axis for the 3D surface (browser performance)
LOWLAND_M = 10.0   # elevation threshold for "low-lying" land (m)

try:  # same optional-Streamlit caching pattern as data_sources
    import streamlit as st

    _cached = st.cache_data(show_spinner=False)  # static data: no TTL
except Exception:  # noqa: BLE001
    def _cached(fn):
        return fn


# ---------------------------------------------------------------------------
# Tile fetching / elevation grid
# ---------------------------------------------------------------------------

def _lonlat_to_tilef(lon: float, lat: float, zoom: int) -> Tuple[float, float]:
    """Lon/lat -> fractional Web-Mercator tile coordinates."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _fetch_tile(zoom: int, x: int, y: int) -> np.ndarray:
    last_error: Exception = RuntimeError("no tile URL configured")
    for url in TILE_URLS:
        try:
            resp = requests.get(url.format(z=zoom, x=x, y=y),
                                timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            rgb = np.asarray(Image.open(io.BytesIO(resp.content)).convert("RGB"),
                             dtype=np.float64)
            return rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0
        except Exception as exc:  # noqa: BLE001 - try the next URL style
            last_error = exc
    raise last_error


def elevation_grid(bbox: Tuple[float, float, float, float],
                   zoom: int = ZOOM) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stitched, cropped, downsampled elevation grid for a bounding box.

    ``bbox`` is (lon_min, lat_min, lon_max, lat_max). Returns (lons[1d],
    lats[1d] north->south, elevation[2d] metres).
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    xf0, yf0 = _lonlat_to_tilef(lon_min, lat_max, zoom)  # NW corner
    xf1, yf1 = _lonlat_to_tilef(lon_max, lat_min, zoom)  # SE corner
    tx0, ty0, tx1, ty1 = int(xf0), int(yf0), int(xf1), int(yf1)

    coords = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tiles = dict(zip(coords, pool.map(lambda c: _fetch_tile(zoom, *c), coords)))
    grid = np.vstack([
        np.hstack([tiles[(tx, ty)] for tx in range(tx0, tx1 + 1)])
        for ty in range(ty0, ty1 + 1)
    ])

    # Crop the stitched mosaic to the bbox in global pixel space.
    px0 = int((xf0 - tx0) * 256)
    px1 = int((xf1 - tx0) * 256)
    py0 = int((yf0 - ty0) * 256)
    py1 = int((yf1 - ty0) * 256)
    grid = grid[py0:py1 + 1, px0:px1 + 1]

    n_pix = 256 * (2 ** zoom)
    gx = np.arange(px0, px1 + 1) + tx0 * 256 + 0.5
    gy = np.arange(py0, py1 + 1) + ty0 * 256 + 0.5
    lons = gx / n_pix * 360.0 - 180.0
    lats = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * gy / n_pix))))

    step = max(1, int(np.ceil(max(grid.shape) / MAX_GRID)))
    return lons[::step], lats[::step], grid[::step, ::step]


# ---------------------------------------------------------------------------
# Point-in-polygon (vectorised ray casting; avoids a shapely dependency)
# ---------------------------------------------------------------------------

def _in_ring(lon2d: np.ndarray, lat2d: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Even-odd ray-casting test of grid points against one closed ring."""
    inside = np.zeros(lon2d.shape, dtype=bool)
    x, y = ring[:, 0], ring[:, 1]
    j = len(ring) - 1
    for i in range(len(ring)):
        dy = y[j] - y[i]
        if dy != 0.0:
            crosses = ((y[i] > lat2d) != (y[j] > lat2d)) & (
                lon2d < (x[j] - x[i]) * (lat2d - y[i]) / dy + x[i])
            inside ^= crosses
        j = i
    return inside


def _decimate(ring: np.ndarray, max_vertices: int = 400) -> np.ndarray:
    """Thin a ring to <= max_vertices; boundary detail below the ~150 m DEM
    grid spacing is wasted work for masking."""
    if len(ring) <= max_vertices:
        return ring
    step = int(np.ceil(len(ring) / max_vertices))
    thinned = ring[::step]
    return np.vstack([thinned, ring[-1]])  # keep closure


def polygon_mask(lons: np.ndarray, lats: np.ndarray, geometry: dict) -> np.ndarray:
    """Boolean mask of grid cells inside a GeoJSON (Multi)Polygon."""
    lon2d, lat2d = np.meshgrid(lons, lats)
    polys = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
             else geometry["coordinates"])
    mask = np.zeros(lon2d.shape, dtype=bool)
    for poly in polys:
        poly_mask = np.zeros(lon2d.shape, dtype=bool)
        for ring in poly:  # XOR handles holes (even-odd rule)
            poly_mask ^= _in_ring(
                lon2d, lat2d,
                _decimate(np.asarray(ring, dtype=np.float64)))
        mask |= poly_mask
    return mask


def _district_geometry(name: str) -> Optional[dict]:
    for feat in geo.load_districts_geojson()["features"]:
        if feat["properties"]["district"] == name:
            return feat["geometry"]
    return None


def _geometry_bbox(geometry: dict) -> Tuple[float, float, float, float]:
    polys = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
             else geometry["coordinates"])
    pts = np.vstack([np.asarray(ring) for poly in polys for ring in poly])
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


# ---------------------------------------------------------------------------
# District elevation + terrain statistics
# ---------------------------------------------------------------------------

@_cached
def district_elevation(name: str):
    """(lons, lats, elevation, inside-mask) for one district, from DEM tiles."""
    geometry = _district_geometry(name)
    if geometry is None:
        raise ValueError("Unknown district: {}".format(name))
    lons, lats, elev = elevation_grid(_geometry_bbox(geometry))
    return lons, lats, elev, polygon_mask(lons, lats, geometry)


def compute_stats(lons: np.ndarray, lats: np.ndarray,
                  elev: np.ndarray, mask: np.ndarray) -> Dict[str, float]:
    """Terrain statistics over the in-polygon cells of an elevation grid.

    * ``mean_slope_deg`` — mean terrain slope from the DEM gradient. Note
      this is resolution-dependent (~150 m cells here); treat as an index,
      not a survey-grade slope.
    * ``lowland_frac`` — fraction of district area below ``LOWLAND_M``
      metres (flood-pooling / drainage-congestion proxy; captures Kuttanad).
    """
    masked = np.where(mask, elev, np.nan)
    lat_mid = math.radians(float(np.mean(lats)))
    dy_m = 111320.0 * abs(float(lats[0] - lats[1]))
    dx_m = 111320.0 * math.cos(lat_mid) * abs(float(lons[1] - lons[0]))
    gy, gx = np.gradient(masked, dy_m, dx_m)
    slope_deg = np.degrees(np.arctan(np.hypot(gx, gy)))

    inside = masked[mask]
    return {
        "mean_slope_deg": round(float(np.nanmean(slope_deg)), 2),
        "lowland_frac": round(float(np.mean(inside < LOWLAND_M)), 3),
        "mean_elev_m": round(float(np.mean(inside)), 1),
        "max_elev_m": round(float(np.max(inside)), 1),
    }


@lru_cache(maxsize=1)
def _stats_table() -> Dict[str, Dict[str, float]]:
    """Per-district terrain stats: precomputed file, else computed live."""
    if STATS_PATH.exists():
        with open(STATS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    table = {}
    for name in config.DISTRICT_NAMES:
        try:
            table[name] = compute_stats(*district_elevation(name))
        except Exception:  # noqa: BLE001 - terrain is optional, never crash
            continue
    return table


def district_stats(name: str) -> Optional[Dict[str, float]]:
    """Terrain stats for a district, or None if unavailable."""
    return _stats_table().get(name)


def stats_for_point(lat: float, lon: float) -> Optional[Dict[str, float]]:
    """Terrain stats of the district containing (lat, lon), if any.

    A searched point inherits its district's terrain profile — a coarse but
    honest MVP approximation (see TODOs in risk_engine for the upgrade path).
    """
    pt_lon = np.array([[lon]])
    pt_lat = np.array([[lat]])
    for feat in geo.load_districts_geojson()["features"]:
        polys = (
            [feat["geometry"]["coordinates"]]
            if feat["geometry"]["type"] == "Polygon"
            else feat["geometry"]["coordinates"])
        inside = False
        for poly in polys:
            m = False
            for ring in poly:
                m ^= bool(_in_ring(pt_lon, pt_lat,
                                   np.asarray(ring, dtype=np.float64))[0, 0])
            inside |= m
        if inside:
            return district_stats(feat["properties"]["district"])
    return None
