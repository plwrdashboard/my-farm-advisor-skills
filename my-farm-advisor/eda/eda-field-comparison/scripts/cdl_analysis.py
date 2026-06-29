#!/usr/bin/env python3
"""Analyze CDL (Cropland Data Layer) crop composition across growers.

Produces per-grower crop composition stacked bars, rotation diversity bars,
corn-vs-soybean scatter plots, and a cross-grower state-level stacked bar
showing Corn vs Soybeans vs Other acreage split for the latest year.

Usage:
    python cdl_analysis.py \
        --growers iowa-north,minnesota-north,nebraska-grower \
        --data-root /home/coder/my-farm-advisor-runtime \
        --output-dir ./output
"""

import argparse
import glob
import os
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# FIPS code → state name lookup (CONUS subset)
_STATE_FIPS = {
    "19": "Iowa",
    "27": "Minnesota",
    "31": "Nebraska",
}

# Grower-slug fallback → state name
_GROWER_STATE = {
    "iowa-north": "Iowa",
    "minnesota-north": "Minnesota",
    "nebraska-grower": "Nebraska",
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


def load_cdl(data_root, grower):
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    tables = os.path.join(dp, "growers", grower, "farms", farm_slug, "derived", "tables")
    cdl_files = glob.glob(os.path.join(tables, "*full_composition.csv"))
    if not cdl_files:
        raise FileNotFoundError(f"No CDL full composition file found for {grower}")
    df = pd.read_csv(cdl_files[0])
    df["grower"] = grower
    return df


def load_boundaries(data_root, grower):
    """Load farm boundary geojson and return a DataFrame with field_id, state, area_acres."""
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    bfile = os.path.join(
        dp, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson"
    )
    if not os.path.exists(bfile):
        return None

    gdf = gpd.read_file(bfile)
    if gdf.empty:
        return None

    # Extract properties
    cols = ["field_id"]
    if "state_fips" in gdf.columns:
        gdf["state"] = gdf["state_fips"].map(_STATE_FIPS)
    else:
        gdf["state"] = _GROWER_STATE.get(grower, grower)

    if "area_acres" not in gdf.columns:
        return None

    return gdf[["field_id", "state", "area_acres"]].copy()


def plot_composition_stacked(df, out_dir):
    grower = df["grower"].iloc[0]
    # Aggregate pct by year and crop_name
    pivot = df.groupby(["year", "crop_name"])["pct"].sum().reset_index()
    pivot = pivot.pivot(index="year", columns="crop_name", values="pct").fillna(0)

    plt.figure(figsize=(10, 6))
    pivot.plot(kind="bar", stacked=True, colormap="tab20", ax=plt.gca())
    plt.title(f"Crop Composition by Year – {grower}", fontsize=14, fontweight="bold")
    plt.xlabel("Year", fontsize=12)
    plt.ylabel("Total Coverage (%)", fontsize=12)
    plt.legend(title="Crop", bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{grower}_crop_composition_stacked.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def plot_rotation_diversity(df, out_dir):
    grower = df["grower"].iloc[0]
    # Count unique crops per field across all years
    diversity = df.groupby("field_id")["crop_name"].nunique().reset_index()
    diversity.columns = ["field_id", "unique_crops"]

    plt.figure(figsize=(10, 6))
    sns.barplot(data=diversity, x="field_id", y="unique_crops", palette="viridis")
    plt.title(f"Crop Rotation Diversity – {grower}", fontsize=14, fontweight="bold")
    plt.xlabel("Field ID", fontsize=12)
    plt.ylabel("Unique Crops (2021–2025)", fontsize=12)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{grower}_rotation_diversity.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def plot_state_crop_split(combined_df, out_dir):
    """Create a stacked bar chart of Corn vs Soybeans vs Other by state (latest year)."""
    if combined_df.empty:
        print("  Skipping state crop split: no data")
        return

    latest_year = combined_df["year"].max()
    df = combined_df[combined_df["year"] == latest_year].copy()

    if df.empty:
        print(f"  Skipping state crop split: no data for year {latest_year}")
        return

    # Categorize crops
    def _category(crop):
        if crop == "Corn":
            return "Corn"
        if crop == "Soybeans":
            return "Soybeans"
        return "Other"

    df["category"] = df["crop_name"].apply(_category)

    # Calculate acreage per crop per field
    df["crop_acreage"] = df["area_acres"] * df["pct"]

    # Aggregate by state and category
    grouped = df.groupby(["state", "category"])["crop_acreage"].sum().reset_index()

    # Calculate percentage within each state
    state_totals = grouped.groupby("state")["crop_acreage"].sum().reset_index()
    state_totals.columns = ["state", "state_total"]
    grouped = grouped.merge(state_totals, on="state")
    grouped["pct_of_state"] = (grouped["crop_acreage"] / grouped["state_total"]) * 100

    # Pivot for plotting
    pivot = grouped.pivot(index="state", columns="category", values="pct_of_state").fillna(0)

    # Ensure expected column order
    for col in ["Corn", "Soybeans", "Other"]:
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot = pivot[["Corn", "Soybeans", "Other"]]

    # Plot
    colors = {"Corn": "#FFD700", "Soybeans": "#228B22", "Other": "#A9A9A9"}
    fig, ax = plt.subplots(figsize=(10, 6))
    pivot.plot(
        kind="bar",
        stacked=True,
        color=[colors[c] for c in pivot.columns],
        edgecolor="black",
        ax=ax,
    )
    ax.set_title(
        f"State-Level Crop Composition – {latest_year}",
        fontsize=14,
        fontweight="bold",
    )
    ax.set_xlabel("State", fontsize=12)
    ax.set_ylabel("Percentage of Total Acreage (%)", fontsize=12)
    ax.set_ylim(0, 100)
    ax.legend(title="Crop", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax.grid(True, alpha=0.3, axis="y")
    plt.xticks(rotation=0)
    plt.tight_layout()

    fpath = os.path.join(out_dir, f"{latest_year}_state_crop_split.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Analyze CDL crop composition across growers.")
    parser.add_argument("--growers", required=True, help="Comma-separated list of grower slugs")
    parser.add_argument("--data-root", required=True, help="Path to the runtime data root")
    parser.add_argument("--output-dir", required=True, help="Path to the output directory")
    args = parser.parse_args()

    growers = [g.strip() for g in args.growers.split(",")]
    out_dir = os.path.join(args.output_dir, "cdl")
    os.makedirs(out_dir, exist_ok=True)

    print("Loading CDL data...")
    dfs = []
    for grower in growers:
        try:
            df = load_cdl(args.data_root, grower)
            dfs.append(df)
            print(f"  {grower}: {len(df)} rows, {df['year'].nunique()} years, {df['field_id'].nunique()} fields")
        except Exception as e:
            print(f"  ERROR loading {grower}: {e}")

    if not dfs:
        print("No data loaded. Exiting.")
        sys.exit(1)

    print("Loading boundary data...")
    boundary_dfs = []
    for grower in growers:
        try:
            bdf = load_boundaries(args.data_root, grower)
            if bdf is not None:
                boundary_dfs.append(bdf)
                print(f"  {grower}: {len(bdf)} fields")
            else:
                print(f"  {grower}: no boundary data")
        except Exception as e:
            print(f"  ERROR loading boundaries for {grower}: {e}")

    # Build combined CDL + boundary DataFrame for cross-grower plots
    if boundary_dfs:
        all_boundaries = pd.concat(boundary_dfs, ignore_index=True)
        all_cdl = pd.concat(dfs, ignore_index=True)
        combined = all_cdl.merge(all_boundaries, on="field_id", how="inner")
    else:
        combined = pd.DataFrame()

    for df in dfs:
        grower = df["grower"].iloc[0]
        print(f"Processing {grower}...")
        plot_composition_stacked(df, out_dir)
        plot_rotation_diversity(df, out_dir)

    # Cross-grower state-level plot
    if not combined.empty:
        print("Generating state-level crop split...")
        plot_state_crop_split(combined, out_dir)

    print("Done.")


if __name__ == "__main__":
    main()
