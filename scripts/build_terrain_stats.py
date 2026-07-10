"""Precompute per-district terrain statistics into data/terrain_stats.json.

Run from the repo root whenever the district boundaries or the terrain
module's methodology change:

    python -m scripts.build_terrain_stats

The output file is committed so the deployed app never has to hit the DEM
tile service for scoring (only the on-demand 3D view does).
"""

import json

from src import config, terrain


def main() -> None:
    table = {}
    for name in config.DISTRICT_NAMES:
        stats = terrain.compute_stats(*terrain.district_elevation(name))
        table[name] = stats
        print("{:<20} slope {:>5.2f} deg   lowland {:>5.1%}   "
              "mean {:>6.1f} m   max {:>6.1f} m".format(
                  name, stats["mean_slope_deg"], stats["lowland_frac"],
                  stats["mean_elev_m"], stats["max_elev_m"]))

    with open(terrain.STATS_PATH, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=2)
    print("\nWrote {}".format(terrain.STATS_PATH))


if __name__ == "__main__":
    main()
