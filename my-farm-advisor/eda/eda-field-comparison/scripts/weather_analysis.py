#!/usr/bin/env python3
"""Analyze weather patterns across growers and fields.

Produces per-grower annual temperature box plots, annual precipitation bars,
temperature-vs-precipitation scatter plots, and a cross-grower state-level
GDD comparison with frost dates for the latest year.

Usage:
    python weather_analysis.py \
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


def load_weather(data_root, grower):
    dp = _dp(data_root)
    farm_slug = get_farm_slug(data_root, grower)
    # Load boundaries to get valid field_ids and state
    bfile = os.path.join(dp, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson")
    gdf = gpd.read_file(bfile)
    field_ids = gdf["field_id"].tolist()

    # Derive state name
    if "state_fips" in gdf.columns:
        state_fips = str(gdf["state_fips"].iloc[0])
        state_name = _STATE_FIPS.get(state_fips, _GROWER_STATE.get(grower, grower))
    else:
        state_name = _GROWER_STATE.get(grower, grower)

    weather_dfs = []
    for fid in field_ids:
        wfile = os.path.join(dp, "growers", grower, "farms", farm_slug, "fields", fid, "weather", "daily_weather.csv")
        if not os.path.exists(wfile):
            print(f"    WARNING: missing weather for {fid}")
            continue
        df = pd.read_csv(wfile)
        df["date"] = pd.to_datetime(df["date"])
        df["year"] = df["date"].dt.year
        weather_dfs.append(df)

    if not weather_dfs:
        raise FileNotFoundError(f"No weather data found for {grower}")

    combined = pd.concat(weather_dfs, ignore_index=True)
    combined["grower"] = grower
    combined["state"] = state_name
    return combined


def plot_annual_temp(df, out_dir):
    grower = df["grower"].iloc[0]
    plt.figure(figsize=(10, 6))
    sns.boxplot(data=df, x="year", y="T2M", palette="coolwarm")
    plt.title(f"Annual Temperature Distribution – {grower}", fontsize=14, fontweight="bold")
    plt.xlabel("Year", fontsize=12)
    plt.ylabel("Daily Mean Temperature (°C)", fontsize=12)
    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{grower}_annual_temp_boxplot.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def plot_annual_precip(df, out_dir):
    grower = df["grower"].iloc[0]
    annual = df.groupby("year")["PRECTOTCORR"].sum().reset_index()
    annual.columns = ["year", "total_precip_mm"]

    plt.figure(figsize=(10, 6))
    sns.barplot(data=annual, x="year", y="total_precip_mm", palette="Blues_d")
    plt.title(f"Annual Total Precipitation – {grower}", fontsize=14, fontweight="bold")
    plt.xlabel("Year", fontsize=12)
    plt.ylabel("Total Precipitation (mm)", fontsize=12)
    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{grower}_annual_precip_bar.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def plot_state_gdd_comparison(all_dfs, out_dir):
    """Create a state-level cumulative GDD comparison with frost dates (latest year)."""
    if not all_dfs:
        print("  Skipping state GDD comparison: no data")
        return

    combined = pd.concat(all_dfs, ignore_index=True)
    latest_year = combined["year"].max()
    df = combined[combined["year"] == latest_year].copy()

    if df.empty:
        print(f"  Skipping state GDD comparison: no data for year {latest_year}")
        return

    # Calculate daily GDD per field
    df["gdd"] = ((df["T2M_MIN"] + df["T2M_MAX"]) / 2 - 10).clip(lower=0)
    df["doy"] = df["date"].dt.dayofyear

    # Aggregate by state and day of year
    state_daily = df.groupby(["state", "doy"]).agg({
        "gdd": "mean",
        "T2M_MIN": "mean"
    }).reset_index()
    state_daily = state_daily.sort_values(["state", "doy"])

    # Cumulative GDD per state
    state_daily["cum_gdd"] = state_daily.groupby("state")["gdd"].cumsum()

    # Find frost dates per state (-2.2°C threshold)
    frost_threshold = -2.2
    frost_dates = {}
    for state in state_daily["state"].unique():
        state_data = state_daily[state_daily["state"] == state].sort_values("doy")

        # Last spring frost: last frost before DOY 182 (July 1)
        spring = state_data[state_data["doy"] < 182]
        spring_frosts = spring[spring["T2M_MIN"] <= frost_threshold]
        last_spring = spring_frosts["doy"].max() if not spring_frosts.empty else None

        # First fall frost: first frost after DOY 182
        fall = state_data[state_data["doy"] >= 182]
        fall_frosts = fall[fall["T2M_MIN"] <= frost_threshold]
        first_fall = fall_frosts["doy"].min() if not fall_frosts.empty else None

        frost_dates[state] = {"last_spring": last_spring, "first_fall": first_fall}

    # Plot
    colors = {
        "Iowa": "steelblue",
        "Minnesota": "darkgreen",
        "Nebraska": "darkorange"
    }

    fig, ax = plt.subplots(figsize=(14, 7))

    for state in sorted(state_daily["state"].unique()):
        state_data = state_daily[state_daily["state"] == state].sort_values("doy")
        color = colors.get(state, "gray")

        ax.plot(state_data["doy"], state_data["cum_gdd"],
                color=color, linewidth=2, label=state)

        # Frost date lines (color-matched, dashed)
        if state in frost_dates:
            ls = frost_dates[state]["last_spring"]
            ff = frost_dates[state]["first_fall"]
            if ls is not None:
                ax.axvline(x=ls, color=color, linestyle="--", alpha=0.7, linewidth=1.5)
            if ff is not None:
                ax.axvline(x=ff, color=color, linestyle="--", alpha=0.7, linewidth=1.5)

    ax.set_title(f"State-Level Cumulative GDD – {latest_year}", fontsize=14, fontweight="bold")
    ax.set_xlabel("Day of Year", fontsize=12)
    ax.set_ylabel("Cumulative GDD (base 10°C)", fontsize=12)
    ax.legend(title="State", loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.text(0.98, 0.02, "Dashed lines = frost dates (-2.2°C)",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, style="italic", alpha=0.7)

    plt.tight_layout()
    fpath = os.path.join(out_dir, f"{latest_year}_state_gdd_comparison.png")
    plt.savefig(fpath, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Created: {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Analyze weather patterns across growers.")
    parser.add_argument("--growers", required=True, help="Comma-separated list of grower slugs")
    parser.add_argument("--data-root", required=True, help="Path to the runtime data root")
    parser.add_argument("--output-dir", required=True, help="Path to the output directory")
    args = parser.parse_args()

    growers = [g.strip() for g in args.growers.split(",")]
    out_dir = os.path.join(args.output_dir, "weather")
    os.makedirs(out_dir, exist_ok=True)

    print("Loading weather data...")
    dfs = []
    for grower in growers:
        try:
            df = load_weather(args.data_root, grower)
            dfs.append(df)
            print(f"  {grower}: {len(df)} daily rows, {df['year'].nunique()} years, {df['field_id'].nunique()} fields")
        except Exception as e:
            print(f"  ERROR loading {grower}: {e}")

    if not dfs:
        print("No data loaded. Exiting.")
        sys.exit(1)

    for df in dfs:
        grower = df["grower"].iloc[0]
        print(f"Processing {grower}...")
        plot_annual_temp(df, out_dir)
        plot_annual_precip(df, out_dir)

    # Cross-grower state-level GDD comparison
    print("Generating state-level GDD comparison...")
    plot_state_gdd_comparison(dfs, out_dir)

    print("Done.")


if __name__ == "__main__":
    main()
