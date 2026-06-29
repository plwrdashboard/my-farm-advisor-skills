#!/usr/bin/env python3
"""Generate a combined hero basemap showing all growers' fields on state boundaries.

Produces a single large-format PNG with all field boundaries overlaid on
state boundary outlines (Iowa, Minnesota, Nebraska), with grower-level
acreage annotations at cluster centroids.

Usage:
    python generate_combined_hero_basemap.py \
        --growers iowa-north,minnesota-north,nebraska-grower \
        --data-root /home/coder/my-farm-advisor-runtime \
        --output-dir ./output
"""

import argparse
import os
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import pandas as pd
from matplotlib.patches import Patch

# FIPS code mapping
_STATE_FIPS = {
    "19": "Iowa",
    "27": "Minnesota",
    "31": "Nebraska",
}

_GROWER_DISPLAY = {
    "iowa-north": "Iowa North",
    "minnesota-north": "Minnesota North",
    "nebraska-grower": "Nebraska Grower",
}


def _dp(data_root):
    if os.path.isdir(os.path.join(data_root, "growers")):
        return data_root
    return os.path.join(data_root, "data-pipeline")


def get_farm_slug(data_root, grower):
    dp = _dp(data_root)
    farms_dir = os.path.join(dp, "growers", grower, "farms")
    farm_slugs = [d for d in os.listdir(farms_dir) if os.path.isdir(os.path.join(farms_dir, d))]
    if not farm_slugs:
        raise ValueError(f"No farms found for grower {grower}")
    return farm_slugs[0]


def load_fields(data_root, grower):
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    bfile = os.path.join(dp, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson")
    gdf = gpd.read_file(bfile)
    gdf["grower"] = grower
    return gdf


def load_state_boundaries(data_root):
    dp = _dp(data_root)
    states_file = os.path.join(dp, "shared", "geoadmin", "l1_states", "states_usa.geojson")
    states = gpd.read_file(states_file)
    # Filter to our three states
    states = states[states["state_fips"].isin(["19", "27", "31"])]
    return states


def main():
    parser = argparse.ArgumentParser(description="Generate combined hero basemap with state boundaries.")
    parser.add_argument("--growers", required=True, help="Comma-separated list of grower slugs")
    parser.add_argument("--data-root", required=True, help="Path to the runtime data root")
    parser.add_argument("--output-dir", required=True, help="Path to the output directory")
    args = parser.parse_args()

    growers = [g.strip() for g in args.growers.split(",")]
    out_dir = os.path.join(args.output_dir, "geospatial")
    os.makedirs(out_dir, exist_ok=True)

    print("Loading state boundaries...")
    states = load_state_boundaries(args.data_root)

    print("Loading field boundaries...")
    all_fields = []
    grower_stats = {}
    for grower in growers:
        try:
            gdf = load_fields(args.data_root, grower)
            all_fields.append(gdf)
            total_acres = gdf["area_acres"].sum() if "area_acres" in gdf.columns else 0
            grower_stats[grower] = {
                "count": len(gdf),
                "acres": total_acres,
            }
            print(f"  {grower}: {len(gdf)} fields, {total_acres:.1f} acres")
        except Exception as e:
            print(f"  ERROR loading {grower}: {e}")

    if not all_fields:
        print("No field data loaded. Exiting.")
        sys.exit(1)

    # Combine all fields
    combined = gpd.GeoDataFrame(pd.concat(all_fields, ignore_index=True))

    # Ensure common CRS with states
    if combined.crs != states.crs:
        states = states.to_crs(combined.crs)

    # Compute combined bounds with 5% buffer
    bounds = combined.total_bounds  # minx, miny, maxx, maxy
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    buffer_x = width * 0.15
    buffer_y = height * 0.15
    xlim = (bounds[0] - buffer_x, bounds[2] + buffer_x)
    ylim = (bounds[1] - buffer_y, bounds[3] + buffer_y)

    # Setup colors
    colors = plt.cm.Set1.colors
    grower_colors = {grower: colors[i % len(colors)] for i, grower in enumerate(growers)}

    # Create figure
    fig, ax = plt.subplots(figsize=(20, 16))

    # Plot state boundaries
    states.boundary.plot(ax=ax, color="#999999", linewidth=0.8, alpha=0.7)

    # Plot field boundaries by grower
    for grower in growers:
        grower_fields = combined[combined["grower"] == grower]
        if not grower_fields.empty:
            grower_fields.boundary.plot(
                ax=ax,
                color=grower_colors[grower],
                linewidth=1.5,
                alpha=0.9,
                label=_GROWER_DISPLAY.get(grower, grower),
            )

    # Add centroid annotations
    for grower in growers:
        grower_fields = combined[combined["grower"] == grower]
        if grower_fields.empty:
            continue
        centroid = grower_fields.geometry.unary_union.centroid
        acres = grower_stats[grower]["acres"]
        label = f"{_GROWER_DISPLAY.get(grower, grower)}\n{acres:,.0f} acres"

        ax.annotate(
            label,
            xy=(centroid.x, centroid.y),
            fontsize=14,
            fontweight="bold",
            ha="center",
            va="center",
            color="white",
            path_effects=[
                pe.withStroke(linewidth=3, foreground="black"),
            ],
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor=grower_colors[grower],
                alpha=0.85,
                edgecolor="black",
                linewidth=1.5,
            ),
        )

    # Styling
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_title("Multi-State Grower Field Overview", fontsize=24, fontweight="bold", pad=20)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])

    # Legend
    legend_elements = [
        Patch(facecolor=grower_colors[g], edgecolor="black", linewidth=1.5,
              label=_GROWER_DISPLAY.get(g, g))
        for g in growers
    ]
    ax.legend(
        handles=legend_elements,
        loc="lower right",
        fontsize=12,
        framealpha=0.9,
        edgecolor="black",
        title="Growers",
        title_fontsize=13,
    )

    # Remove spines
    for spine in ax.spines.values():
        spine.set_visible(False)

    plt.tight_layout()
    fpath = os.path.join(out_dir, "combined_growers_hero_basemap.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close()
    print(f"Created: {fpath}")


if __name__ == "__main__":
    main()
