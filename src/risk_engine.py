"""Composite 24-hour weather risk score for a location in Kerala.

This module is deliberately **isolated and pure** (no I/O, no Streamlit, no
network): it maps a small set of meteorological/hydrological inputs to a
0-100 risk score, a categorical level, and a human-readable explanation of
which factors drove the score.

Scoring approach (MVP heuristic)
--------------------------------
Each input is converted to a 0-100 *component score* by piecewise-linear
interpolation over breakpoints anchored, where possible, to India
Meteorological Department (IMD) operational categories:

* 24 h accumulated rainfall — IMD classes: moderate >= 15.6 mm, heavy
  >= 64.5 mm, very heavy >= 115.6 mm, extremely heavy >= 204.5 mm.
* Peak hourly rainfall intensity — 15 mm/h is commonly used as the
  "intense" convective threshold; 50 mm/h is cloudburst-scale.
* CAPE (Convective Available Potential Energy) — >1000 J/kg moderately
  unstable, >2500 J/kg strongly unstable (thunderstorm potential).
* Wind gusts — anchored to IMD wind warnings (~62 km/h gale threshold,
  ~89 km/h severe, ~118 km/h cyclonic storm).
* River discharge anomaly — ratio of the maximum GloFAS forecast discharge
  over the next 72 h to the trailing 30-day mean. Ratios >= 2 indicate a
  rapidly rising river; >= 5 is a major hydrological anomaly.
* Terrain susceptibility (DEM) — a static index from district elevation
  statistics: mean slope (landslide / flash-flood runoff proneness) and
  the fraction of land below 10 m (flood pooling / drainage congestion,
  e.g. Kuttanad). Because terrain never changes, it enters the score as
  an **amplifier**: its contribution is the susceptibility scaled by the
  strongest wet-weather component (24 h rain, intensity, or discharge),
  so flat calm days are not inflated by static geography.

Component scores are combined as a weighted sum (weights in
``FACTOR_WEIGHTS``, summing to 1.0), so the composite is also 0-100.

.. warning::
   These are transparent screening heuristics for the MVP demo, **not** a
   validated hydro-meteorological hazard model.

TODO (radar science team):
    * Replace the rainfall components with QPE/QPF from IMD DWR Doppler
      radar retrievals (Z-R relationships, nowcast extrapolation).
    * Replace the linear breakpoint transforms with calibrated hazard
      curves per district (terrain, soil saturation, urban drainage).
    * Add antecedent precipitation index (API) and soil-moisture inputs.
    * Terrain: district-mean slope dilutes localised escarpments (e.g.
      Wayanad's plateau hides its landslide-prone scarps) — move to
      sub-district cells and a high-percentile slope statistic, and
      couple slope with radar QPE cell tracks rather than district rain.
    * Validate weights against historical Kerala flood events
      (2018, 2019 Nilambur/Kavalappara, 2021 Kottayam-Idukki).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Factor definitions
# ---------------------------------------------------------------------------

#: Relative weight of each factor in the composite score. Must sum to 1.0.
FACTOR_WEIGHTS = {
    "rain_24h": 0.30,       # accumulated forecast rain, next 24 h (mm)
    "rain_intensity": 0.15, # peak hourly rain, next 24 h (mm/h)
    "cape": 0.10,           # max CAPE, next 24 h (J/kg)
    "wind_gust": 0.15,      # max 10 m wind gust, next 24 h (km/h)
    "discharge": 0.15,      # river discharge anomaly ratio (dimensionless)
    "terrain": 0.15,        # DEM susceptibility x wet-weather amplification
}
assert abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1"

#: Piecewise-linear breakpoints mapping raw values -> 0-100 component score.
#: Each entry is a sequence of (raw_value, score) pairs, ascending in raw
#: value. Values outside the range are clamped.
FACTOR_BREAKPOINTS = {
    "rain_24h": [(0, 0), (15.6, 20), (64.5, 50), (115.6, 75), (204.5, 100)],
    "rain_intensity": [(0, 0), (5, 20), (15, 50), (30, 75), (50, 100)],
    "cape": [(0, 0), (1000, 25), (2000, 50), (3500, 80), (5000, 100)],
    "wind_gust": [(0, 0), (40, 20), (62, 50), (89, 80), (118, 100)],
    "discharge": [(1.0, 0), (1.5, 30), (2.0, 55), (3.0, 80), (5.0, 100)],
}

#: DEM susceptibility breakpoints. Calibrated to the observed spread of
#: district-mean values at ~150 m grid resolution (see
#: data/terrain_stats.json): mean slope ranges 0.3-6.7 deg (Alappuzha to
#: Idukki); low-lying fraction 0-62% (Wayanad to Alappuzha/Kuttanad).
SLOPE_BREAKPOINTS = [(0, 0), (2, 25), (4, 50), (6, 75), (9, 100)]      # deg
LOWLAND_BREAKPOINTS = [(0, 0), (0.10, 30), (0.25, 55), (0.45, 80), (0.65, 100)]

FACTOR_LABELS = {
    "rain_24h": "24h accumulated rain",
    "rain_intensity": "Peak rain intensity",
    "cape": "Thunderstorm potential (CAPE)",
    "wind_gust": "Wind gusts",
    "discharge": "River discharge anomaly",
    "terrain": "Terrain susceptibility (DEM)",
}

#: Risk levels: (upper score bound, name, hex colour).
RISK_LEVELS = [
    (25, "Low", "#2e7d32"),       # green
    (50, "Moderate", "#f9a825"),  # yellow
    (75, "High", "#ef6c00"),      # orange
    (101, "Severe", "#c62828"),   # red
]


@dataclass
class RiskInputs:
    """Raw meteorological/hydrological inputs for one location, next 24 h.

    Any field may be ``None`` when the upstream source has no data (e.g.
    no GloFAS river cell at a coastal point); that factor then contributes
    a neutral zero and is flagged in the explanation.
    """

    rain_24h_mm: Optional[float] = None
    peak_hourly_rain_mm: Optional[float] = None
    cape_max_jkg: Optional[float] = None
    wind_gust_max_kmh: Optional[float] = None
    discharge_ratio: Optional[float] = None  # next-72h max / 30-day mean
    # Static DEM-derived terrain descriptors (see src/terrain.py)
    mean_slope_deg: Optional[float] = None
    lowland_frac: Optional[float] = None     # fraction of land below 10 m


@dataclass
class FactorContribution:
    """How one factor contributed to the composite score."""

    key: str
    label: str
    raw_value: Optional[float]  # None = data unavailable
    unit: str
    component_score: float      # 0-100 before weighting
    weighted_points: float      # contribution to the composite (0-100 scale)
    narrative: str              # plain-language phrase, e.g. "85 mm expected"


@dataclass
class RiskResult:
    """Composite risk assessment with a full explainability trace."""

    score: float                 # 0-100
    level: str                   # Low / Moderate / High / Severe
    color: str                   # hex colour for the map
    contributions: List[FactorContribution] = field(default_factory=list)
    summary: str = ""            # one-sentence plain-language explanation


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _interpolate(breakpoints: Sequence[Tuple[float, float]], value: float) -> float:
    """Piecewise-linear interpolation with clamping at both ends."""
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for (x0, y0), (x1, y1) in zip(breakpoints, breakpoints[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return breakpoints[-1][1]  # unreachable, defensive


_FACTOR_UNITS = {
    "rain_24h": "mm",
    "rain_intensity": "mm/h",
    "cape": "J/kg",
    "wind_gust": "km/h",
    "discharge": "x baseline",
    "terrain": "/100 susceptibility",
}

#: Factors read directly from one RiskInputs attribute. "terrain" is
#: derived from two attributes and handled separately in compute_risk.
_INPUT_ATTR = {
    "rain_24h": "rain_24h_mm",
    "rain_intensity": "peak_hourly_rain_mm",
    "cape": "cape_max_jkg",
    "wind_gust": "wind_gust_max_kmh",
    "discharge": "discharge_ratio",
}


def _narrative(key: str, value: Optional[float], component_score: float) -> str:
    """Plain-language phrase describing one factor's raw value."""
    if value is None:
        return "no data available (treated as neutral)"
    if key == "rain_24h":
        return "{:.0f} mm of rain expected in the next 24h".format(value)
    if key == "rain_intensity":
        return "peak rainfall rate of {:.1f} mm/h expected".format(value)
    if key == "cape":
        qual = ("low", "moderate", "high", "extreme")[min(3, int(component_score // 30))]
        return "CAPE up to {:.0f} J/kg ({} thunderstorm potential)".format(value, qual)
    if key == "wind_gust":
        return "wind gusts up to {:.0f} km/h forecast".format(value)
    if key == "discharge":
        return "river discharge forecast at {:.1f}x its 30-day average".format(value)
    return ""


def terrain_susceptibility(mean_slope_deg: Optional[float],
                           lowland_frac: Optional[float]) -> Optional[float]:
    """Static 0-100 terrain susceptibility from DEM statistics.

    Two independent terrain hazards are scored and the **maximum** taken —
    a district is susceptible if *either* applies:

    * steep terrain (mean slope) -> landslide / flash-flood runoff, and
    * low-lying terrain (fraction below 10 m) -> flood pooling and
      drainage congestion (Kuttanad-type inundation).

    Returns None when no DEM statistics are available.
    """
    scores = []
    if mean_slope_deg is not None:
        scores.append(_interpolate(SLOPE_BREAKPOINTS, float(mean_slope_deg)))
    if lowland_frac is not None:
        scores.append(_interpolate(LOWLAND_BREAKPOINTS, float(lowland_frac)))
    return max(scores) if scores else None


def level_for_score(score: float) -> Tuple[str, str]:
    """Return (level name, hex colour) for a 0-100 composite score."""
    for upper, name, color in RISK_LEVELS:
        if score < upper:
            return name, color
    return RISK_LEVELS[-1][1], RISK_LEVELS[-1][2]


def compute_risk(inputs: RiskInputs) -> RiskResult:
    """Compute the composite 0-100 risk score with an explanation trace.

    Parameters
    ----------
    inputs:
        Raw next-24h forecast aggregates for one location. ``None`` fields
        contribute zero and are marked "no data" in the explanation.

    Returns
    -------
    RiskResult
        Composite score, categorical level, map colour, per-factor
        contributions sorted by weighted impact (largest first), and a
        one-sentence plain-language summary.
    """
    contributions: List[FactorContribution] = []
    components: dict = {}
    total = 0.0

    for key, weight in FACTOR_WEIGHTS.items():
        if key == "terrain":
            continue  # interaction term, needs the other components first
        raw = getattr(inputs, _INPUT_ATTR[key])
        if raw is None:
            component = 0.0
        else:
            component = _interpolate(FACTOR_BREAKPOINTS[key], float(raw))
        components[key] = component
        weighted = component * weight
        total += weighted
        contributions.append(FactorContribution(
            key=key,
            label=FACTOR_LABELS[key],
            raw_value=raw,
            unit=_FACTOR_UNITS[key],
            component_score=round(component, 1),
            weighted_points=round(weighted, 1),
            narrative=_narrative(key, raw, component),
        ))

    # Terrain enters as an amplifier: static susceptibility scaled by the
    # strongest wet-weather signal, so calm days stay unaffected by
    # geography while heavy rain over hazardous terrain is boosted.
    susceptibility = terrain_susceptibility(inputs.mean_slope_deg,
                                            inputs.lowland_frac)
    wet_driver = max(components["rain_24h"], components["rain_intensity"],
                     components["discharge"])
    terrain_component = (0.0 if susceptibility is None
                         else susceptibility * wet_driver / 100.0)
    terrain_weighted = terrain_component * FACTOR_WEIGHTS["terrain"]
    total += terrain_weighted
    contributions.append(FactorContribution(
        key="terrain",
        label=FACTOR_LABELS["terrain"],
        raw_value=susceptibility,
        unit=_FACTOR_UNITS["terrain"],
        component_score=round(terrain_component, 1),
        weighted_points=round(terrain_weighted, 1),
        narrative=_terrain_narrative(inputs, susceptibility, wet_driver),
    ))

    contributions.sort(key=lambda c: c.weighted_points, reverse=True)
    score = round(min(100.0, total), 1)
    level, color = level_for_score(score)

    return RiskResult(
        score=score,
        level=level,
        color=color,
        contributions=contributions,
        summary=_summarise(level, contributions),
    )


def _terrain_narrative(inputs: RiskInputs, susceptibility: Optional[float],
                       wet_driver: float) -> str:
    """Plain-language phrase for the terrain amplification factor."""
    if susceptibility is None:
        return "no terrain data available (treated as neutral)"
    slope_score = (_interpolate(SLOPE_BREAKPOINTS, inputs.mean_slope_deg)
                   if inputs.mean_slope_deg is not None else 0.0)
    lowland_score = (_interpolate(LOWLAND_BREAKPOINTS, inputs.lowland_frac)
                     if inputs.lowland_frac is not None else 0.0)
    if slope_score >= lowland_score:
        qual = "steep" if slope_score >= 60 else ("hilly" if slope_score >= 35 else "gently sloping")
        hazard = ", landslide/flash-flood prone" if slope_score >= 60 else ""
        base = "{} terrain (mean slope {:.1f} deg{})".format(
            qual, inputs.mean_slope_deg or 0.0, hazard)
    else:
        base = "low-lying terrain ({:.0f}% of land below 10 m, prone to flood pooling)".format(
            100.0 * (inputs.lowland_frac or 0.0))
    if wet_driver < 10:
        return base + ", but little wet-weather hazard to amplify right now"
    return base + " amplifying the forecast rain/flood hazard"


def _summarise(level: str, contributions: List[FactorContribution]) -> str:
    """One-sentence explanation naming the dominant factors.

    Example: "High risk, mainly due to 85 mm of rain expected in the next
    24h and river discharge forecast at 2.3x its 30-day average."
    """
    drivers = [c for c in contributions if c.weighted_points >= 5 and c.raw_value is not None]
    if not drivers:
        return "{} risk: no significant adverse weather signals in the next 24 hours.".format(level)
    top = [c.narrative for c in drivers[:2]]
    return "{} risk, mainly due to {}.".format(level, " and ".join(top))
