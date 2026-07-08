"""Central configuration: districts, API endpoints, cache and UI constants.

Keep all tunable constants here so the app code stays free of magic numbers.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DISTRICTS_GEOJSON = REPO_ROOT / "data" / "kerala_districts.geojson"

# --------------------------------------------------------------------------
# Kerala's 14 districts with polygon centroids (WGS84).
# Centroids computed from the district boundary polygons in
# data/kerala_districts.geojson (2011 census boundaries).
# --------------------------------------------------------------------------
DISTRICT_CENTROIDS = {
    "Kasaragod": (12.4626, 75.1521),
    "Kannur": (11.9997, 75.5240),
    "Wayanad": (11.7126, 76.0968),
    "Kozhikode": (11.4859, 75.8323),
    "Malappuram": (11.1323, 76.1535),
    "Palakkad": (10.7950, 76.5565),
    "Thrissur": (10.4714, 76.3150),
    "Ernakulam": (10.0831, 76.5452),
    "Idukki": (9.8399, 77.0565),
    "Kottayam": (9.6369, 76.6508),
    "Alappuzha": (9.4261, 76.4485),
    "Pathanamthitta": (9.2841, 76.9270),
    "Kollam": (8.9619, 76.8718),
    "Thiruvananthapuram": (8.6089, 77.0129),
}
DISTRICT_NAMES = list(DISTRICT_CENTROIDS.keys())

# Rough bounding box of Kerala, used to sanity-check geocoding results.
KERALA_BBOX = {"lat_min": 8.0, "lat_max": 12.9, "lon_min": 74.7, "lon_max": 77.5}

# Map defaults
MAP_CENTER = (10.35, 76.30)
MAP_ZOOM = 7

# --------------------------------------------------------------------------
# Open-Meteo endpoints (free, no API key)
# --------------------------------------------------------------------------
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

TIMEZONE = "Asia/Kolkata"

# Hourly variables requested from the forecast API
HOURLY_VARS = ["precipitation", "cape", "wind_gusts_10m"]
FORECAST_DAYS = 7
PAST_DAYS = 1  # past 24 h of observed/assimilated data for context

# Flood API: days of history used as the discharge baseline
FLOOD_PAST_DAYS = 30
FLOOD_FORECAST_DAYS = 7

# Cache TTL for API responses (seconds). Open-Meteo asks non-commercial
# users to stay under 10k calls/day; 30 min is far below that.
CACHE_TTL_SECONDS = 1800

REQUEST_TIMEOUT = 20  # seconds
