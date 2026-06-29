#!/usr/bin/env python3
"""Generate satellite basemaps with field boundary overlays.

Produces one PNG per grower showing field boundaries on Esri World Imagery.

Usage:
    python geospatial.py \
        --growers iowa-north,minnesota-north,nebraska-grower \
        --data-root /home/coder/my-farm-advisor-runtime \
        --output-dir ./output
"""

import argparse
import os
import sys

import geopandas as gpd
import matplotlib.pyplot as plt


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


def plot_basemap(data_root, grower, out_dir):
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    bfile = os.path.join(dp, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson")
    gdf = gpd.read_file(bfile)

    # Reproject to Web Mercator for contextily
    gdf_3857 = gdf.to_crs(epsg=3857)

    fig, ax = plt.subplots(figsize=(12, 10))
    gdf_3857.boundary.plot(ax=ax, color="red", linewidth=1.5)

    # Try to add basemap
    try:
        import contextily as ctx
        ctx.add_basemap(ax, source=ctx.providers.Esri.WorldImagery, crs=gdf_3857.crs.to_string())
    except Exception as e:
        print(f"  WARNING: could not add basemap for {grower}: {e}")
        ax.set_title(f"{grower} – Field Boundaries (no basemap)")
    else:
        ax.set_title(f"{grower} – Field Boundaries on Satellite Imagery", fontsize=14, fontweight="bold")

    ax.set_xlabel("Longitude", fontsize=12)
    ax.set_ylabel("Latitude", fontsize=12)
    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{grower}_satellite_basemap.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Generate satellite basemaps with field boundary overlays.")
    parser.add_argument("--growers", required=True, help="Comma-separated list of grower slugs")
    parser.add_argument("--data-root", required=True, help="Path to the runtime data root")
    parser.add_argument("--output-dir", required=True, help="Path to the output directory")
    args = parser.parse_args()

    growers = [g.strip() for g in args.growers.split(",")]
    out_dir = os.path.join(args.output_dir, "geospatial")
    os.makedirs(out_dir, exist_ok=True)

    for grower in growers:
        print(f"Processing {grower}...")
        try:
            plot_basemap(args.data_root, grower, out_dir)
        except Exception as e:
            print(f"  ERROR processing {grower}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
