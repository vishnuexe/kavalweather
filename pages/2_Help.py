"""Help page — plain-language guide to every value shown on the dashboard.

Written for the general public (no meteorology background assumed). The
scientific version of this material lives in the About & Methodology page.
"""

import streamlit as st

st.set_page_config(page_title="Help — KavalWeather",
                   page_icon="❓", layout="centered")

st.title("Help: understanding KavalWeather")
st.caption("A plain-language guide to every number and colour you see. "
           "No weather knowledge needed.")

st.markdown("""
## The map

The map shows Kerala's 14 districts. Each district is coloured by how risky
the weather looks over the **next 24 hours**:

| Colour | Level | What it means for you |
|---|---|---|
| 🟢 Green | **Low** (score 0–24) | Normal conditions. Nothing unusual expected. |
| 🟡 Yellow | **Moderate** (25–49) | Noticeable rain or wind likely. Carry an umbrella, expect minor delays. |
| 🟠 Orange | **High** (50–74) | Heavy rain, storms or rising rivers likely. Avoid riversides and low-lying areas; plan travel carefully. |
| 🔴 Red | **Severe** (75–100) | Dangerous weather expected. Follow official IMD / Kerala disaster authority instructions. |
| ⚪ Grey | No data | We couldn't get data for this district right now. |

**Tap or click any district** to see its details on the right (below the map
on a phone). You can also pick a district from the dropdown list.

## The risk score (0–100)

Every district gets one number between 0 and 100. It is not a percentage
and not a chance of rain — think of it as a **danger meter** that combines
five things: how much rain is coming, how intense the bursts of rain will
be, how likely thunderstorms are, how strong the wind gusts will be, and
whether nearby rivers are rising. The higher the number, the more of these
signals are elevated at the same time.

## The six numbers in the detail panel

**Rain next 24h (mm)** — the total rain expected in the coming 24 hours.
Millimetres measure how deep the water would be if none of it drained away.
As a rough guide: under 15 mm is light, 65 mm+ in a day is officially
"heavy rain", and 115 mm+ is "very heavy" — enough to flood low-lying
streets.

**Rain past 24h (mm)** — how much rain already fell in the last day. This
matters because rain falling on already-soaked ground runs off instead of
soaking in, so the same forecast rain is more dangerous after a wet day.

**Peak intensity (mm/h)** — the heaviest single hour of rain expected.
Two days of steady drizzle can total the same as one violent hour; it's the
violent hour that floods roads. Above 15 mm/h means intense downpour;
above 30 mm/h, water can rise on streets within minutes.

**Max CAPE (J/kg)** — a measure of how "charged up" the atmosphere is for
thunderstorms (the technical name is Convective Available Potential
Energy). You can read it like a battery level for storms: below about
1000 the sky is calm-natured, 1000–2500 means thunderstorms are quite
possible, and above 2500 the atmosphere has enough energy for strong
storms with lightning and sudden downpours. CAPE alone doesn't guarantee
a storm — it means the fuel is there if one gets triggered.

**Max wind gust (km/h)** — the strongest short burst of wind expected (a
gust lasts a few seconds; it is stronger than the average wind). Around
40 km/h makes umbrellas struggle, around 62 km/h small branches break and
riding a two-wheeler gets risky, and 90 km/h+ can bring down trees and
power lines.

**River discharge (× normal)** — how much water is forecast to flow in the
nearest river compared with its own average over the past 30 days. `1.0×`
means the river is at its normal level. `2.0×` means twice the usual water
is coming down it — a fast-rising river even if it isn't raining where you
stand, because the rain may have fallen upstream in the hills. Values of
3–5× signal serious flood potential. If it shows **n/a**, there is no
significant river near that point (common right on the coast), so this
factor is simply left out.

## The charts

**Rainfall forecast — next 48 hours.** Each bar is one hour; taller bars
mean heavier rain that hour. The dotted line marks 24 hours from now —
everything left of it is counted in the risk score, everything right of it
shows what's coming after. A few tall spikes are more dangerous than many
tiny bars adding up to the same total.

**Why this score?** This chart shows how many of the score's points came
from each of the five factors, biggest first. If "24h accumulated rain"
has the longest bar, rain volume is the main reason the district is
flagged; if "River discharge anomaly" leads, the concern is river flooding
rather than the sky above you. The bullet points below the chart say the
same thing in words.

## Searching for your town

Open **"Search any Kerala town or village"** at the top and type a place
name (for example *Munnar*, *Kochi* or *Nilambur*). Pick the right match
from the list — the town appears as a 📍 pin on the map and gets its own
detail panel, calculated exactly for that spot rather than for the whole
district. Useful because weather in the hills can be very different from
the coast, even inside one district.

## Messages you might see

- **"Data updated: … · auto-refreshes every 30 min"** — the time the
  forecast was last downloaded. Numbers refresh automatically about every
  half hour; there is no need to reload repeatedly.
- **"Live data source temporarily unreachable…"** (orange warning) — our
  weather provider didn't respond, so you are seeing the most recent
  successful download, with its time shown. The data is still useful but
  may be a little old.
- **"n/a"** on any number — that particular measurement wasn't available
  for that location; the score is computed from the remaining factors.

## Common questions

**Is this an official weather warning?**
No. This is a demonstration tool that helps you *understand* weather risk.
Official warnings for Kerala come from the India Meteorological Department
(IMD) and the Kerala State Disaster Management Authority (KSDMA). If our
map and an official warning disagree, always follow the official warning.

**The score is Low but it's raining outside. Is the app wrong?**
Not necessarily — ordinary rain is normal for Kerala and scores low on
purpose. The score rises only when the *amount*, *intensity* or *flood
potential* becomes unusual. Also, the score looks at the next 24 hours as
a whole, not the current minute.

**Why do two neighbouring districts have different colours?**
Each district is assessed from weather data at its own central point.
Rainfall can genuinely vary a lot over short distances, especially between
the coast and the Western Ghats.

**Where does the data come from?**
Free, public weather services: the Open-Meteo forecast system (which blends
leading global weather models) and the Copernicus GloFAS river-flow
forecast. Details are on the *About & Methodology* page.
""")

st.divider()
st.caption("Still confused by something on the dashboard? The About & "
           "Methodology page has the technical version of this guide.")
