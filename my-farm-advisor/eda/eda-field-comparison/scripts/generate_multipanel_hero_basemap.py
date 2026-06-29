#!/usr/bin/env python3
"""Generate a multi-panel hero basemap with zoomed grower panels + overview locator.

Produces a single large-format PNG with 3 zoomed panels (one per grower) and
a 4th overview panel showing state boundaries with rectangles indicating zoom extents.

Usage:
    python generate_multipanel_hero_basemap.py \
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
from matplotlib.patches import Patch, Rectangle

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
    states = states[states["state_fips"].isin(["19", "27", "31"])]
    return states


def plot_grower_panel(ax, grower_fields, grower, states, color, stats):
    """Plot a single zoomed panel for one grower."""
    # Plot state boundaries
    states.boundary.plot(ax=ax, color="#999999", linewidth=0.8, alpha=0.7)

    # Plot fields
    grower_fields.boundary.plot(ax=ax, color=color, linewidth=2.0, alpha=0.9)

    # Compute zoom bounds with 5% buffer
    bounds = grower_fields.total_bounds
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    buffer_x = width * 0.05
    buffer_y = height * 0.05
    ax.set_xlim(bounds[0] - buffer_x, bounds[2] + buffer_x)
    ax.set_ylim(bounds[1] - buffer_y, bounds[3] + buffer_y)

    # Centroid annotation
    centroid = grower_fields.geometry.unary_union.centroid
    acres = stats["acres"]
    label = f"{_GROWER_DISPLAY.get(grower, grower)}\n{acres:,.0f} acres"

    ax.annotate(
        label,
        xy=(centroid.x, centroid.y),
        fontsize=12,
        fontweight="bold",
        ha="center",
        va="center",
        color="white",
        path_effects=[
            pe.withStroke(linewidth=3, foreground="black"),
        ],
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor=color,
            alpha=0.85,
            edgecolor="black",
            linewidth=1.5,
        ),
    )

    ax.set_title(f"{_GROWER_DISPLAY.get(grower, grower)}", fontsize=16, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def plot_overview_panel(ax, combined, states, growers, grower_colors, grower_stats):
    """Plot overview locator with all states and zoom extent rectangles."""
    # Plot all states
    states.boundary.plot(ax=ax, color="#666666", linewidth=1.0, alpha=0.8)

    # Plot all fields faintly
    combined.boundary.plot(ax=ax, color="#333333", linewidth=0.5, alpha=0.4)

    # Add zoom extent rectangles for each grower
    for grower in growers:
        grower_fields = combined[combined["grower"] == grower]
        if grower_fields.empty:
            continue
        bounds = grower_fields.total_bounds
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        buffer_x = width * 0.05
        buffer_y = height * 0.05

        rect = Rectangle(
            (bounds[0] - buffer_x, bounds[1] - buffer_y),
            width + 2 * buffer_x,
            height + 2 * buffer_y,
            linewidth=2.5,
            edgecolor=grower_colors[grower],
            facecolor=grower_colors[grower],
            alpha=0.15,
            linestyle="-",
        )
        ax.add_patch(rect)

        # Label near the rectangle
        label_x = bounds[0] - buffer_x
        label_y = bounds[3] + buffer_y + 0.1
        ax.text(
            label_x, label_y,
            f"{_GROWER_DISPLAY.get(grower, grower)} ({grower_stats[grower]['acres']:,.0f} ac)",
            fontsize=10,
            fontweight="bold",
            color=grower_colors[grower],
            path_effects=[pe.withStroke(linewidth=2, foreground="white")],
        )

    # Overview extent: all states visible
    ax.set_xlim(states.total_bounds[0] - 0.5, states.total_bounds[2] + 0.5)
    ax.set_ylim(states.total_bounds[1] - 0.5, states.total_bounds[3] + 0.5)

    ax.set_title("Overview", fontsize=16, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def main():
    parser = argparse.ArgumentParser(description="Generate multi-panel hero basemap with state boundaries.")
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

    combined = gpd.GeoDataFrame(pd.concat(all_fields, ignore_index=True))

    if combined.crs != states.crs:
        states = states.to_crs(combined.crs)

    colors = plt.cm.Set1.colors
    grower_colors = {grower: colors[i % len(colors)] for i, grower in enumerate(growers)}

    # Create 2x2 grid
    fig, axes = plt.subplots(2, 2, figsize=(20, 16))
    fig.suptitle("Multi-State Grower Field Overview", fontsize=24, fontweight="bold", y=0.98)

    # Plot each grower in first three panels
    panel_positions = [(0, 0), (0, 1), (1, 0)]
    for (row, col), grower in zip(panel_positions, growers):
        grower_fields = combined[combined["grower"] == grower]
        plot_grower_panel(
            axes[row, col],
            grower_fields,
            grower,
            states,
            grower_colors[grower],
            grower_stats[grower],
        )

    # Overview in bottom-right
    plot_overview_panel(
        axes[1, 1],
        combined,
        states,
        growers,
        grower_colors,
        grower_stats,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    fpath = os.path.join(out_dir, "multipanel_growers_hero_basemap.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close()
    print(f"Created: {fpath}")


if __name__ == "__main__":
    main()
