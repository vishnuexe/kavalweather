"""District boundary handling for the Kerala map layer."""

import json
from functools import lru_cache
from typing import Dict

from src import config


@lru_cache(maxsize=1)
def load_districts_geojson() -> dict:
    """Load the bundled 14-district Kerala boundary GeoJSON (2011 census).

    Cached for the process lifetime — the file is static.
    """
    with open(config.DISTRICTS_GEOJSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def geojson_with_risk(scores: Dict[str, dict]) -> dict:
    """Return a copy of the district GeoJSON with risk properties injected.

    ``scores`` maps district name -> {"score", "level", "color", "summary"}.
    Districts missing from ``scores`` are styled grey ("no data").
    """
    base = load_districts_geojson()
    features = []
    for feat in base["features"]:
        name = feat["properties"]["district"]
        info = scores.get(name, {})
        props = {
            "district": name,
            "score": info.get("score", "n/a"),
            "level": info.get("level", "No data"),
            "color": info.get("color", "#9e9e9e"),
            "summary": info.get("summary", "Data unavailable."),
        }
        features.append({"type": "Feature", "properties": props,
                         "geometry": feat["geometry"]})
    return {"type": "FeatureCollection", "features": features}
