#!/usr/bin/env python3
"""Analyze field boundary sizes across growers.

Produces per-grower field size histograms, an across-growers box plot,
and a summary CSV with acreage statistics.

Usage:
    python boundaries.py \
        --growers iowa-north,minnesota-north,nebraska-grower \
        --data-root /home/coder/my-farm-advisor-runtime \
        --output-dir ./output
"""

import argparse
import os
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def _dp(data_root):
    # Support both /path/to/runtime and /path/to/runtime/data-pipeline
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


def load_boundaries(data_root, grower):
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    bfile = os.path.join(dp, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson")
    gdf = gpd.read_file(bfile)
    gdf["grower"] = grower
    return gdf


def plot_histograms(gdf_list, out_dir):
    for gdf in gdf_list:
        grower = gdf["grower"].iloc[0]
        plt.figure(figsize=(10, 6))
        sns.histplot(data=gdf, x="area_acres", bins=10, kde=True, color="steelblue")
        plt.title(f"Field Size Distribution – {grower}", fontsize=14, fontweight="bold")
        plt.xlabel("Area (acres)", fontsize=12)
        plt.ylabel("Number of Fields", fontsize=12)
        plt.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        fpath = os.path.join(out_dir, f"{grower}_field_size_histogram.png")
        plt.savefig(fpath, dpi=300, bbox_inches="tight")
        plt.close()
        print(f"  Created: {fpath}")


def plot_across_box(gdf_list, out_dir):
    combined = pd.concat([gdf[["grower", "area_acres"]] for gdf in gdf_list], ignore_index=True)
    plt.figure(figsize=(10, 6))
    sns.boxplot(data=combined, x="grower", y="area_acres", palette="Set2")
    plt.title("Field Size Distribution Across Growers", fontsize=14, fontweight="bold")
    plt.xlabel("Grower", fontsize=12)
    plt.ylabel("Area (acres)", fontsize=12)
    plt.tight_layout()
    fpath = os.path.join(out_dir, "across_growers_field_size_boxplot.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def write_summary(gdf_list, out_dir):
    rows = []
    for gdf in gdf_list:
        grower = gdf["grower"].iloc[0]
        rows.append({
            "grower": grower,
            "count": len(gdf),
            "mean_acres": round(gdf["area_acres"].mean(), 2),
            "std_acres": round(gdf["area_acres"].std(), 2),
            "min_acres": round(gdf["area_acres"].min(), 2),
            "max_acres": round(gdf["area_acres"].max(), 2),
            "median_acres": round(gdf["area_acres"].median(), 2),
        })
    df = pd.DataFrame(rows)
    fpath = os.path.join(out_dir, "field_size_summary.csv")
    df.to_csv(fpath, index=False)
    print(f"  Created: {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Analyze field boundary sizes across growers.")
    parser.add_argument("--growers", required=True, help="Comma-separated list of grower slugs")
    parser.add_argument("--data-root", required=True, help="Path to the runtime data root")
    parser.add_argument("--output-dir", required=True, help="Path to the output directory")
    args = parser.parse_args()

    growers = [g.strip() for g in args.growers.split(",")]
    out_dir = os.path.join(args.output_dir, "boundaries")
    os.makedirs(out_dir, exist_ok=True)

    print("Loading boundaries...")
    gdf_list = []
    for grower in growers:
        try:
            gdf = load_boundaries(args.data_root, grower)
            gdf_list.append(gdf)
            print(f"  {grower}: {len(gdf)} fields, avg {gdf['area_acres'].mean():.1f} acres")
        except Exception as e:
            print(f"  ERROR loading {grower}: {e}")

    if not gdf_list:
        print("No data loaded. Exiting.")
        sys.exit(1)

    print("Generating histograms...")
    plot_histograms(gdf_list, out_dir)

    print("Generating across-growers box plot...")
    plot_across_box(gdf_list, out_dir)

    print("Writing summary CSV...")
    write_summary(gdf_list, out_dir)

    print("Done.")


if __name__ == "__main__":
    main()
