"""KavalWeather (Kerala Weather Risk Dashboard) — main map view.

Streamlit entry point. Renders the district risk map, the click-to-explain
detail panel, and the location search. All scoring logic lives in
``src/risk_engine.py``; all data access in ``src/data_sources.py``.
"""

import datetime as dt
import math

import folium
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from src import config, data_sources, geo, terrain
from src.risk_engine import RiskInputs, RISK_LEVELS, compute_risk

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
            pick = st.selectbox("Matches", labels, label_visibility="collapsed")
            m = matches[labels.index(pick)]
            search_point = (m["name"], m["latitude"], m["longitude"])


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

    # 3D terrain view, directly below the map. Behind a toggle on purpose:
    # each 3D surface consumes a browser WebGL context (hard-capped per
    # tab), so the chart must only exist while explicitly enabled, and
    # reuses one mounted component (stable key) across district switches.
    selected_district = st.session_state.get("district_select", config.DISTRICT_NAMES[0])
    if st.toggle("🏔️ 3D terrain view — {}".format(selected_district), key="show_terrain_3d"):
        try:
            with st.spinner("Building elevation model…"):
                st.plotly_chart(terrain_figure(selected_district),
                                use_container_width=True, key="terrain_3d_chart",
                                config={"displayModeBar": False})
            st.caption(
                "Elevation heatmap from open DEM tiles (~150 m grid, vertical "
                "scale exaggerated). Drag to rotate, pinch/scroll to zoom."
            )
        except Exception:  # noqa: BLE001 - tiles unreachable: degrade
            st.info("Terrain view is unavailable right now (elevation tile "
                    "service unreachable). The risk score is unaffected — it "
                    "uses precomputed terrain data.")

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


def terrain_figure(name):
    """3D elevation surface of a district, clipped to its boundary."""
    lons, lats, elev, mask = terrain.district_elevation(name)
    z = np.where(mask, elev, np.nan)  # NaN outside the district -> not drawn
    # Coastal cells can pick up offshore bathymetry from the DEM blend;
    # clamp so backwaters (Kuttanad ~ -3 m) stay visible without a fake trench.
    z = np.maximum(z, -10.0)
    fig = go.Figure(go.Surface(
        x=lons, y=lats, z=z,
        colorscale="Turbo", colorbar=dict(title="m", thickness=12, len=0.6),
        hovertemplate="Elevation: %{z:.0f} m<extra></extra>",
    ))
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


with detail_col:
    if search_point:
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

    district = st.selectbox("District detail", config.DISTRICT_NAMES, key="district_select")
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
