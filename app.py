"""KavalWeather (Kerala Weather Risk Dashboard) — main map view.

Streamlit entry point. Renders the district risk map, the click-to-explain
detail panel, and the location search. All scoring logic lives in
``src/risk_engine.py``; all data access in ``src/data_sources.py``.
"""

import datetime as dt
import logging
import math

import folium
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
from streamlit_folium import st_folium
from streamlit_plotly_events import plotly_events

# Serialize figures with the stdlib json engine. The default "auto" prefers
# the native orjson when present, which segfaulted the Streamlit Cloud
# process (Python 3.14) while serializing the NaN-heavy 3D surface.
try:
    pio.json.config.default_engine = "json"
except Exception:  # noqa: BLE001 - engine knob varies across plotly versions
    pass

from src import config, data_sources, geo, terrain
from src.risk_engine import (FACTOR_WEIGHTS, RiskInputs, RISK_LEVELS,
                             compute_risk, level_for_score)

st.set_page_config(
    page_title="KavalWeather — Kerala Weather Risk Dashboard",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Light styling: tighter paddings for phones, badge + legend chips.
st.markdown("""
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
.risk-badge {
  display: inline-block; padding: 0.25rem 0.9rem; border-radius: 999px;
  color: white; font-weight: 700; font-size: 1.05rem; letter-spacing: .02em;
}
.legend-chip {
  display: inline-block; padding: 0.1rem 0.6rem; border-radius: 999px;
  color: white; font-size: 0.78rem; font-weight: 600; margin-right: 0.35rem;
}
.stale-note { color: #b26a00; font-size: 0.85rem; }
h1 { font-size: 1.9rem !important; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data loading + scoring
# ---------------------------------------------------------------------------

def assess_all_districts(bundle):
    """Compute a RiskResult per district from the fetched data bundle."""
    results = {}
    for name in config.DISTRICT_NAMES:
        weather = bundle["weather"].get(name)
        flood = bundle["flood"].get(name)
        if weather is None:
            continue
        inputs = data_sources.derive_risk_inputs(
            weather, flood, terrain.district_stats(name))
        results[name] = compute_risk(RiskInputs(**inputs))
    return results


def _grid_to_lists(a):
    """2D numpy grid -> nested python lists with None for NaN.

    The 3D figure is serialized by a third-party component; plain lists with
    explicit nulls keep that path independent of numpy/NaN handling in
    whichever JSON engine is active (see the orjson note at the top).
    """
    return [[None if not np.isfinite(v) else float(v) for v in row] for row in a]


def local_risk_field(risk_result, lons, lats, elev, mask):
    """Per-cell 0-100 risk over a district's DEM grid.

    The district's *weather* is uniform (one forecast per district), so the
    spatial texture comes from the terrain amplifier applied per cell: the
    district's five weather-driven factor points plus the terrain factor
    computed with each cell's own susceptibility. Cells at the district-mean
    susceptibility therefore match the district score.
    """
    susceptibility = terrain.cell_susceptibility(lons, lats, elev)
    base = sum(c.weighted_points for c in risk_result.contributions
               if c.key != "terrain")
    wet_driver = max(c.component_score for c in risk_result.contributions
                     if c.key in ("rain_24h", "rain_intensity", "discharge"))
    field = base + FACTOR_WEIGHTS["terrain"] * susceptibility * wet_driver / 100.0
    return np.where(mask, np.clip(field, 0.0, 100.0), np.nan)


def terrain_figure(name, marker=None, risk_result=None):
    """3D district surface: elevation shape, coloured by local risk.

    Colour is the per-cell risk field (blue = low, red = high, fixed 0-100
    scale) when ``risk_result`` is given, else plain elevation. ``marker``
    is an optional (label, lat, lon) pin, drawn only inside this district.

    Returns (figure, pick_info). pick_info describes the sparse clickable
    point layer (trace index + per-point lat/lon/risk/elevation arrays) so
    a click event can be resolved server-side; it is None when no risk
    field is drawn.
    """
    lons, lats, elev, mask = terrain.district_elevation(name)
    z = np.where(mask, elev, np.nan)  # NaN outside the district -> not drawn
    # Coastal cells can pick up offshore bathymetry from the DEM blend;
    # clamp so backwaters (Kuttanad ~ -3 m) stay visible without a fake trench.
    z = np.maximum(z, -10.0)

    x_list, y_list, z_list = lons.tolist(), lats.tolist(), _grid_to_lists(z)
    risk = None
    if risk_result is not None:
        risk = local_risk_field(risk_result, lons, lats, elev, mask)
        fig = go.Figure(go.Surface(
            x=x_list, y=y_list, z=z_list,
            surfacecolor=_grid_to_lists(risk),
            cmin=0, cmax=100, colorscale="Portland",
            colorbar=dict(title="Risk", thickness=12, len=0.6),
            text=np.char.mod("%.0f", np.nan_to_num(risk)).tolist(),
            hovertemplate=("Local risk: %{text}/100<br>"
                           "Elevation: %{z:.0f} m<extra></extra>"),
        ))
    else:
        fig = go.Figure(go.Surface(
            x=x_list, y=y_list, z=z_list,
            colorscale="Turbo", colorbar=dict(title="m", thickness=12, len=0.6),
            hovertemplate="Elevation: %{z:.0f} m<extra></extra>",
        ))

    if marker:
        label, mlat, mlon = marker
        i = int(np.abs(lats - mlat).argmin())
        j = int(np.abs(lons - mlon).argmin())
        if (lats.min() <= mlat <= lats.max()
                and lons.min() <= mlon <= lons.max() and mask[i, j]):
            z_range = float(np.nanmax(z) - np.nanmin(z)) or 1.0
            spot = float(z[i, j])
            fig.add_trace(go.Scatter3d(
                x=[mlon], y=[mlat], z=[spot + max(100.0, 0.05 * z_range)],
                mode="markers+text", text=[label], textposition="top center",
                marker=dict(size=5, color="#1a1a1a", symbol="diamond"),
                textfont=dict(size=13, color="#1a1a1a"),
                hovertemplate="{}<br>Elevation: {:.0f} m<extra></extra>".format(label, spot),
            ))

    pick = None
    if risk is not None:
        # Clickable pick layer: every ~3rd in-district cell, effectively
        # invisible but selectable. Clicks are resolved server-side through
        # the point index, so keep the arrays alongside the trace index.
        step = max(1, int(np.ceil(max(z.shape) / 50.0)))
        sub = np.zeros_like(mask)
        sub[::step, ::step] = True
        pi, pj = np.where(mask & sub)
        pick = {
            "trace_index": len(fig.data),
            "lat": lats[pi], "lon": lons[pj],
            "risk": risk[pi, pj], "elev": elev[pi, pj],
        }
        fig.add_trace(go.Scatter3d(
            x=pick["lon"].tolist(), y=pick["lat"].tolist(),
            z=(pick["elev"] + 25.0).tolist(),
            mode="markers",
            marker=dict(size=6, color="rgba(0,0,0,0.01)"),
            customdata=np.stack([pick["risk"], pick["elev"]], axis=1).tolist(),
            hovertemplate=("Local risk: %{customdata[0]:.0f}/100<br>"
                           "Elevation: %{customdata[1]:.0f} m"
                           "<extra>click to identify place</extra>"),
        ))

    # Compass: a line anchored in geography (it rotates with the scene,
    # unlike billboard text alone) pointing due north, "N" at its tip.
    lat_ext = float(lats.max() - lats.min())
    cx = float(lons.max())
    cy0, cy1 = float(lats.max() - 0.12 * lat_ext), float(lats.max())
    ch = float(np.nanmax(z)) * 1.1 + 200.0
    fig.add_trace(go.Scatter3d(
        x=[cx, cx], y=[cy0, cy1], z=[ch, ch],
        mode="lines+markers+text", text=["", "N"], textposition="top center",
        line=dict(color="#1a1a1a", width=5),
        marker=dict(size=[0, 5], color="#1a1a1a", symbol="diamond"),
        textfont=dict(size=14, color="#1a1a1a"),
        hoverinfo="skip",
    ))

    fig.update_layout(showlegend=False, scene_dragmode="turntable")
    # True-to-scale footprint with a gentle vertical exaggeration.
    lat_mid = math.radians(float(np.mean(lats)))
    x_km = abs(lons[-1] - lons[0]) * 111.32 * math.cos(lat_mid)
    y_km = abs(lats[0] - lats[-1]) * 111.32
    m = max(x_km, y_km)
    fig.update_layout(
        height=420, margin=dict(l=0, r=0, t=0, b=0),
        scene=dict(
            aspectmode="manual",
            aspectratio=dict(x=x_km / m, y=y_km / m, z=0.25),
            xaxis=dict(title="", showticklabels=False),
            yaxis=dict(title="", showticklabels=False),
            zaxis=dict(title="Elevation (m)"),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig, pick


with st.spinner("Fetching latest forecasts for all 14 districts…"):
    bundle = data_sources.fetch_all_districts()
    results = assess_all_districts(bundle)

st.title("KavalWeather")
st.caption(
    "Kerala's weather guardian — hyperlocal rainfall & storm risk for the next "
    "24 hours, with transparent, explainable scoring. "
    "Data: Open-Meteo forecast, GloFAS river discharge & open DEM terrain."
)

if bundle["failed"] and not results:
    st.error(
        "Live weather services are currently unreachable and no cached data is "
        "available yet. Please retry in a few minutes."
    )
    st.stop()

if bundle["stale"]:
    ts = bundle["fetched_at"].strftime("%d %b %Y %H:%M UTC") if bundle["fetched_at"] else "unknown time"
    st.warning(
        "Live data source temporarily unreachable — showing the last "
        "successful update from **{}**.".format(ts), icon="⚠️"
    )
elif bundle["fetched_at"]:
    st.caption("Data updated: {} · auto-refreshes every 30 min".format(
        bundle["fetched_at"].astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %H:%M IST")))


# ---------------------------------------------------------------------------
# Location search
# ---------------------------------------------------------------------------

search_point = None  # (label, lat, lon) of a searched place, if any
with st.expander("🔍 Search any Kerala town or village", expanded=False):
    query = st.text_input("Place name", placeholder="e.g. Munnar, Kochi, Nilambur…",
                          label_visibility="collapsed")
    if query and len(query.strip()) >= 2:
        matches = data_sources.geocode_kerala(query.strip())
        if not matches:
            st.info("No matching place found in Kerala. Try another spelling.")
        else:
            labels = ["{} ({})".format(m["name"], m["admin2"] or "Kerala") for m in matches]
            pick = st.selectbox("Matches", labels, key="search_match",
                                label_visibility="collapsed")
            m = matches[labels.index(pick)]
            search_point = (m["name"], m["latitude"], m["longitude"])

def _resolve_3d_click(clicks, pick3d):
    """Map a plotly click event to (place name, local risk, elevation).

    Prefers the pick-layer point index (exact); falls back to nearest
    pick-layer point by coordinates if the click landed on another trace.
    Returns None when there is nothing to resolve.
    """
    if not clicks or pick3d is None:
        return None
    c = clicks[-1]
    idx = None
    if c.get("curveNumber") == pick3d["trace_index"]:
        pn = c.get("pointNumber", c.get("pointIndex"))
        if isinstance(pn, int) and 0 <= pn < len(pick3d["lat"]):
            idx = pn
    if idx is None and c.get("x") is not None and c.get("y") is not None:
        try:
            lon, lat = float(c["x"]), float(c["y"])
        except (TypeError, ValueError):
            return None
        idx = int(np.argmin((pick3d["lon"] - lon) ** 2 + (pick3d["lat"] - lat) ** 2))
    if idx is None:
        return None
    lat = float(pick3d["lat"][idx])
    lon = float(pick3d["lon"][idx])
    place = data_sources.reverse_geocode(round(lat, 4), round(lon, 4)) or "Unnamed area"
    return place, float(pick3d["risk"][idx]), float(pick3d["elev"][idx])


@st.fragment
def render_3d_section(district_name, marker, risk_result):
    """3D view as an isolated fragment: chart clicks rerun only this block,
    so the folium map and detail panels are not re-rendered per click."""
    if not st.toggle("🏔️ 3D risk & terrain view — {}".format(district_name),
                     key="show_terrain_3d"):
        return
    try:
        with st.spinner("Building elevation model…"):
            fig3d, pick3d = terrain_figure(district_name, marker=marker,
                                           risk_result=risk_result)
        # streamlit-plotly-events hooks plotly's native click event, which
        # fires in 3D scenes (Streamlit's own on_select does not, and it
        # also breaks scene rotation). District-specific key so a click
        # never carries over to another district's grid.
        clicks = plotly_events(fig3d, click_event=True, override_height=430,
                               key="terrain3d_{}".format(district_name))
        spot = _resolve_3d_click(clicks, pick3d)
        if spot:
            place, risk_val, elev_val = spot
            level, color = level_for_score(risk_val)
            st.markdown(
                '📍 **{}** — local risk <span class="risk-badge" '
                'style="background:{};font-size:0.85rem;padding:0.1rem 0.6rem">'
                '{:.0f}/100 · {}</span> · elevation {:.0f} m'.format(
                    place, color, risk_val, level, elev_val),
                unsafe_allow_html=True,
            )
        st.caption(
            "Surface shape = elevation (~150 m grid, vertical scale "
            "exaggerated); colour = local risk for the next 24 h "
            "(blue low → red high), from the district forecast amplified "
            "by each cell's own terrain. Drag to rotate; click anywhere "
            "on the surface to identify that place and its risk."
        )
    except Exception as exc:  # noqa: BLE001 - degrade, but say why
        logging.getLogger(__name__).exception("3D terrain view failed")
        st.info("Terrain view is unavailable right now. The risk score "
                "is unaffected — it uses precomputed terrain data.")
        st.caption("Technical detail: {}: {}".format(
            type(exc).__name__, str(exc)[:160]))


# The detail panel shows either the searched place or the selected district
# ("view mode"). A fresh search switches to the place — and selects the
# district containing it, so the map/3D view line up; picking a district
# (dropdown or map click) switches back and dismisses the place panel.
if search_point:
    picked_label = "{}|{:.4f}|{:.4f}".format(*search_point)
    if picked_label != st.session_state.get("_last_search_pick"):
        st.session_state["_last_search_pick"] = picked_label
        st.session_state["view_mode"] = "place"
        home = terrain.district_for_point(search_point[1], search_point[2])
        if home:
            st.session_state["district_select"] = home
else:
    st.session_state.pop("_last_search_pick", None)
    if st.session_state.get("view_mode") == "place":
        st.session_state["view_mode"] = "district"


# ---------------------------------------------------------------------------
# Map + district selection
# ---------------------------------------------------------------------------

map_col, detail_col = st.columns([5, 4], gap="large")

with map_col:
    fmap = folium.Map(location=config.MAP_CENTER, zoom_start=config.MAP_ZOOM,
                      tiles="cartodbpositron", control_scale=True)

    score_props = {
        name: {"score": r.score, "level": r.level, "color": r.color, "summary": r.summary}
        for name, r in results.items()
    }
    folium.GeoJson(
        geo.geojson_with_risk(score_props),
        style_function=lambda feat: {
            "fillColor": feat["properties"]["color"],
            "color": "#37474f", "weight": 1, "fillOpacity": 0.55,
        },
        highlight_function=lambda feat: {"weight": 3, "fillOpacity": 0.75},
        tooltip=folium.GeoJsonTooltip(
            fields=["district", "level", "score"],
            aliases=["District", "Risk level", "Score (0-100)"],
        ),
    ).add_to(fmap)

    if search_point:
        folium.Marker(
            location=[search_point[1], search_point[2]],
            tooltip=search_point[0],
            icon=folium.Icon(color="blue", icon="search", prefix="fa"),
        ).add_to(fmap)

    map_out = st_folium(fmap, height=470, use_container_width=True,
                        returned_objects=["last_active_drawing"], key="main_map")

    # Map click -> selected district (a fresh click wins over the selectbox
    # once, then the selectbox takes over again).
    clicked = (map_out or {}).get("last_active_drawing")
    clicked_name = clicked["properties"].get("district") if clicked and "properties" in clicked else None
    if clicked_name and clicked_name != st.session_state.get("_last_map_click"):
        st.session_state["_last_map_click"] = clicked_name
        st.session_state["district_select"] = clicked_name
        st.session_state["view_mode"] = "district"

    # 3D terrain view, directly below the map. Behind a toggle (each 3D
    # surface holds a browser WebGL context, hard-capped per tab) and inside
    # a fragment: every click on the 3D chart triggers a rerun, and without
    # isolation that rerun re-rendered the folium map and re-shipped the
    # multi-MB 3D payload app-wide — a few clicks exhausted the tab.
    selected_district = st.session_state.get("district_select", config.DISTRICT_NAMES[0])
    render_3d_section(selected_district, search_point,
                      results.get(selected_district))

    chips = "".join(
        '<span class="legend-chip" style="background:{}">{}</span>'.format(color, name)
        for _, name, color in RISK_LEVELS
    )
    st.markdown("**Next-24h risk:** " + chips, unsafe_allow_html=True)
    st.caption("Tap a district for the full risk breakdown.")


# ---------------------------------------------------------------------------
# Detail panel (explainability)
# ---------------------------------------------------------------------------

def rain_forecast_chart(weather_json, accent="#1565c0"):
    """48-hour hourly rainfall bar chart with a marker at the +24 h boundary."""
    frame = data_sources.rain_chart_frame(weather_json, hours=48)
    fig = go.Figure(go.Bar(
        x=frame["time"].to_numpy(), y=frame["precipitation"],
        marker_color=accent, name="Rain (mm/h)",
        hovertemplate="%{x|%a %H:%M}<br>%{y:.1f} mm/h<extra></extra>",
    ))
    if len(frame) > 24:
        fig.add_vline(x=frame["time"].iloc[24].to_pydatetime(),
                      line_dash="dot", line_color="#78909c")
        fig.add_annotation(x=frame["time"].iloc[24], y=1, yref="paper",
                           text="+24 h", showarrow=False, font=dict(size=11, color="#78909c"))
    fig.update_layout(
        height=240, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
        yaxis_title="mm / hour", xaxis_title=None,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def contribution_chart(result):
    """Horizontal bars: how many of the 0-100 points each factor added."""
    contribs = list(reversed(result.contributions))  # largest on top
    fig = go.Figure(go.Bar(
        x=[c.weighted_points for c in contribs],
        y=[c.label for c in contribs],
        orientation="h", marker_color=result.color,
        hovertemplate="%{y}: +%{x:.1f} points<extra></extra>",
    ))
    fig.update_layout(
        height=210, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Contribution to risk score (points)",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig



def render_assessment(result, weather_json, flood_json):
    """Shared renderer for the district panel and searched-location panel."""
    st.markdown(
        '<span class="risk-badge" style="background:{}">{} · {}/100</span>'.format(
            result.color, result.level, result.score),
        unsafe_allow_html=True,
    )
    st.markdown("**{}**".format(result.summary))

    inputs = data_sources.derive_risk_inputs(weather_json, flood_json)
    antecedent = data_sources.past_24h_rain(weather_json)
    c1, c2, c3 = st.columns(3)
    c1.metric("Rain next 24h", _fmt(inputs["rain_24h_mm"], "mm"))
    c2.metric("Rain past 24h", _fmt(antecedent, "mm"))
    c3.metric("Peak intensity", _fmt(inputs["peak_hourly_rain_mm"], "mm/h"))
    c4, c5, c6 = st.columns(3)
    c4.metric("Max CAPE", _fmt(inputs["cape_max_jkg"], "J/kg"))
    c5.metric("Max wind gust", _fmt(inputs["wind_gust_max_kmh"], "km/h"))
    c6.metric("River discharge", _fmt(inputs["discharge_ratio"], "× normal"))

    st.markdown("##### Rainfall forecast — next 48 hours")
    st.plotly_chart(rain_forecast_chart(weather_json, accent=result.color),
                    use_container_width=True, config={"displayModeBar": False})

    st.markdown("##### Why this score?")
    st.plotly_chart(contribution_chart(result), use_container_width=True,
                    config={"displayModeBar": False})
    for c in result.contributions:
        st.markdown("- **{}** (+{:.1f} pts): {}".format(c.label, c.weighted_points, c.narrative))


def _fmt(value, unit):
    if value is None:
        return "n/a"
    return "{:.1f} {}".format(value, unit)


def _dismiss_search():
    """User picked a district explicitly -> district details take the top."""
    st.session_state["view_mode"] = "district"


with detail_col:
    district = st.selectbox("District detail", config.DISTRICT_NAMES,
                            key="district_select", on_change=_dismiss_search)

    if st.session_state.get("view_mode") == "place" and search_point:
        st.subheader("📍 {}".format(search_point[0]))
        point = data_sources.fetch_point(search_point[1], search_point[2])
        if point["failed"]:
            st.error("Could not fetch a forecast for this location right now.")
        else:
            if point["stale"]:
                st.markdown('<div class="stale-note">⚠️ Showing last cached data for this location.</div>',
                            unsafe_allow_html=True)
            p_inputs = data_sources.derive_risk_inputs(
                point["weather"], point["flood"],
                terrain.stats_for_point(search_point[1], search_point[2]))
            p_result = compute_risk(RiskInputs(**p_inputs))
            render_assessment(p_result, point["weather"], point["flood"])
        st.divider()
        st.subheader("🗺️ {} district".format(district))

    if district in results:
        render_assessment(
            results[district],
            bundle["weather"].get(district),
            bundle["flood"].get(district),
        )
    else:
        st.info("No data available for {} right now.".format(district))

st.divider()
st.caption(
    "Demo MVP — heuristic screening scores, not official warnings. Always follow "
    "IMD and Kerala SDMA advisories. See the *About & Methodology* page for details."
)
