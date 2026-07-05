"""Field Season Dashboard — single-year multi-panel report.

Produces a vertical-stack dashboard with NDVI, GDD, precipitation,
and stress summaries sharing a unified DOY axis.

Usage:
    python field_season_dashboard.py
        --grower <slug> --farm <slug> --field <slug> --year <YYYY>

Defaults point at the minnesota-north / osm-1491018233 test field.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------
GDD_BASE_C = 10.0
GDD_CAP_C = 30.0
HEAT_THRESHOLD_C = 32.0
COLD_THRESHOLD_C = 0.0
FROST_THRESHOLD_C = -2.0   # killing frost for frost-date indicators
HEAVY_RAIN_MM = 25.0
DRY_SPELL_MIN_DAYS = 5
DRY_SPELL_MAX_PRECIP_MM = 1.0
GROWING_SEASON_DOY_START = 90   # ~Apr 1 for this latitude
GROWING_SEASON_DOY_END = 300    # ~Oct 27

XAXIS_DOY_MIN = 60    # March 1
XAXIS_DOY_MAX = 334   # November 30
MONTH_TICKS = {60: "Mar", 91: "Apr", 121: "May", 152: "Jun",
               182: "Jul", 213: "Aug", 244: "Sep", 274: "Oct", 305: "Nov"}

CDL_CLASS_NAMES = {
    0: "Background",
    1: "Corn",
    2: "Cotton",
    3: "Rice",
    4: "Sorghum",
    5: "Soybeans",
    6: "Sunflower",
    10: "Peanuts",
    11: "Tobacco",
    12: "Sweet Corn",
    13: "Pop or Orn Corn",
    14: "Mint",
    21: "Barley",
    22: "Durum Wheat",
    23: "Spring Wheat",
    24: "Winter Wheat",
    25: "Other Small Grains",
    26: "Winter Wheat/Soybeans",
    27: "Rye",
    28: "Oats",
    29: "Millet",
    30: "Speltz",
    31: "Canola",
    32: "Flaxseed",
    33: "Safflower",
    34: "Rape Seed",
    35: "Mustard",
    36: "Alfalfa",
    37: "Other Hay/Non Alflfa",
    38: "Camelina",
    39: "Buckwheat",
    41: "Sugarbeets",
    42: "Dry Beans",
    43: "Potatoes",
    44: "Other Crops",
    45: "Sweet Potatoes",
    46: "Misc Vegs & Fruits",
    47: "Watermelons",
    48: "Onions",
    49: "Cucumbers",
    50: "Chick Peas",
    51: "Lentils",
    52: "Peas",
    53: "Tomatoes",
    54: "Caneberries",
    55: "Hops",
    56: "Herbs",
    57: "Clover/Wildflowers",
    58: "Sod/Grass Seed",
    59: "Switches",
    60: "Trees",
    61: "Fallow/Idle",
    63: "Forest",
    64: "Shrubland",
    65: "Barren",
    66: "Cherries",
    67: "Peaches",
    68: "Apples",
    69: "Grapes",
    70: "Christmas Trees",
    71: "Other Tree Crops",
    72: "Citrus",
    74: "Pecans",
    75: "Almonds",
    76: "Walnuts",
    77: "Pears",
    81: "Clouds",
    82: "Developed",
    83: "Water",
    87: "Wetlands",
    88: "Nonag/Undefined",
    92: "Aquaculture",
    111: "Open Water",
    112: "Perennial Ice/Snow",
    121: "Developed/Open Space",
    122: "Developed/Low Intensity",
    123: "Developed/Medium Intensity",
    124: "Developed/High Intensity",
    131: "Barren",
    141: "Deciduous Forest",
    142: "Evergreen Forest",
    143: "Mixed Forest",
    152: "Shrubland",
    176: "Grass/Pasture",
    190: "Woody Wetlands",
    195: "Herbaceous Wetlands",
}


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _parse_date_from_filename(path: Path) -> datetime | None:
    """Extract date from filenames like sentinel_20220603_ndvi.tif."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", path.stem)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def _farm_table_basename(farm_slug: str) -> str:
    return farm_slug.replace("-", "_")


def _gdd(Tmax: float, Tmin: float) -> float:
    """Growing Degree Days base 10°C, cap 30°C."""
    mean = (Tmax + Tmin) / 2.0
    capped = min(mean, GDD_CAP_C)
    return max(0.0, capped - GDD_BASE_C)


def frost_dates(df: pd.DataFrame) -> tuple[int | None, int | None]:
    """Return (last_spring_frost_doy, first_fall_frost_doy) from daily T2M_MIN.

    Uses the killing-frost threshold (-2°C).  DOY 180 (~June 29) separates
    Northern Hemisphere spring frosts from fall frosts.
    """
    below = df[df["T2M_MIN"] < FROST_THRESHOLD_C]
    spring = below[below["doy"] <= 180]
    fall = below[below["doy"] > 180]
    last_frost = int(spring["doy"].max()) if not spring.empty else None
    first_frost = int(fall["doy"].min()) if not fall.empty else None
    return last_frost, first_frost


# ---------------------------------------------------------------------------
#  Data loaders
# ---------------------------------------------------------------------------
def load_cdl(farm_tables_dir: Path, farm_slug: str, year: int, field_slug: str) -> pd.DataFrame:
    """Load CDL crop composition for a field in a given year."""
    stem = f"{_farm_table_basename(farm_slug)}_{year}_cdl.csv"
    path = farm_tables_dir / stem
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["field_id"] == field_slug].copy()
    df = df.sort_values("pct", ascending=False)
    return df


def load_weather(csv_path: Path, year: int) -> pd.DataFrame:
    """Load daily weather and add DOY, GDD columns."""
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.year == year].copy()
    if df.empty:
        return df
    df["doy"] = df["date"].dt.dayofyear
    df["gdd_daily"] = df.apply(lambda r: _gdd(r["T2M_MAX"], r["T2M_MIN"]), axis=1)
    df["gdd_cum"] = df["gdd_daily"].cumsum()
    df["precip_mm"] = df["PRECTOTCORR"]
    # Display-only imperial conversions (original data unchanged)
    df["T2M_F"] = df["T2M"] * 9 / 5 + 32
    df["T2M_MAX_F"] = df["T2M_MAX"] * 9 / 5 + 32
    df["T2M_MIN_F"] = df["T2M_MIN"] * 9 / 5 + 32
    df["precip_in"] = df["PRECTOTCORR"] / 25.4
    df["gdd_cum_F"] = df["gdd_cum"] * 9 / 5
    df["gdd_daily_F"] = df["gdd_daily"] * 9 / 5
    return df


def find_ndvi_scenes(sat_dir: Path) -> list[tuple[datetime, Path]]:
    """Return sorted list of (date, ndvi_tif_path) for a year's satellite dir."""
    if not sat_dir.exists():
        return []
    results = []
    for scene_dir in sorted(sat_dir.iterdir()):
        if not scene_dir.is_dir():
            continue
        for ndvi_path in scene_dir.glob("*_ndvi.tif"):
            dt = _parse_date_from_filename(ndvi_path)
            if dt is not None:
                results.append((dt, ndvi_path))
    results.sort(key=lambda x: x[0])
    return results


def compute_ndvi_timeseries(
    scenes: list[tuple[datetime, Path]], boundary_path: Path
) -> pd.DataFrame:
    """Compute mean NDVI per scene masked to the field boundary."""
    try:
        import geopandas as gpd
        import rasterio
        from rasterio.mask import mask
        from shapely.geometry import mapping
    except ImportError:
        print("  [WARN] rasterio/geopandas not available — skipping NDVI", file=sys.stderr)
        return pd.DataFrame()

    if not boundary_path.exists():
        return pd.DataFrame()

    fields = gpd.read_file(boundary_path)
    field_geom = mapping(fields.geometry.iloc[0])

    rows = []
    for dt, ndvi_path in scenes:
        try:
            with rasterio.open(ndvi_path) as src:
                out_image, _ = mask(src, [field_geom], crop=True, nodata=np.nan)
                data = out_image[0]
            valid = data[~np.isnan(data)]
            if valid.size == 0:
                continue
            rows.append(
                {
                    "date": dt,
                    "doy": dt.timetuple().tm_yday,
                    "mean_ndvi": float(np.mean(valid)),
                    "min_ndvi": float(np.min(valid)),
                    "max_ndvi": float(np.max(valid)),
                    "std_ndvi": float(np.std(valid)),
                    "pixel_count": int(valid.size),
                }
            )
        except Exception as exc:
            print(f"    Skipping {ndvi_path.name}: {exc}", file=sys.stderr)
            continue
    return pd.DataFrame(rows)


def compute_dry_spells(df: pd.DataFrame, growing_season_only: bool = True) -> list[dict]:
    """Find dry spells: consecutive days with precip < threshold.

    When *growing_season_only* is True, only dry spells that occur within
    the growing season window (DOY 90–300) are reported.
    """
    if df.empty:
        return []
    if growing_season_only:
        df = df[
            (df["doy"] >= GROWING_SEASON_DOY_START) &
            (df["doy"] <= GROWING_SEASON_DOY_END)
        ].copy()
        if df.empty:
            return []
    dry = df["precip_mm"] < DRY_SPELL_MAX_PRECIP_MM
    spells = []
    start = None
    for i, is_dry in enumerate(dry):
        if is_dry and start is None:
            start = i
        elif not is_dry and start is not None:
            length = i - start
            if length >= DRY_SPELL_MIN_DAYS:
                spells.append(
                    {
                        "start_doy": int(df.iloc[start]["doy"]),
                        "end_doy": int(df.iloc[i - 1]["doy"]),
                        "duration_days": length,
                    }
                )
            start = None
    # Check if still in dry spell at end
    if start is not None:
        length = len(df) - start
        if length >= DRY_SPELL_MIN_DAYS:
            spells.append(
                {
                    "start_doy": int(df.iloc[start]["doy"]),
                    "end_doy": int(df.iloc[-1]["doy"]),
                    "duration_days": length,
                }
            )
    return spells


# ---------------------------------------------------------------------------
#  Plotting
# ---------------------------------------------------------------------------
def _apply_xaxis(ax, show_labels: bool = False):
    """Apply March–November DOY limits and month-name ticks."""
    ax.set_xlim(XAXIS_DOY_MIN, XAXIS_DOY_MAX)
    tick_vals = sorted(MONTH_TICKS.keys())
    ax.set_xticks(tick_vals)
    if show_labels:
        ax.set_xticklabels([MONTH_TICKS[v] for v in tick_vals], fontsize=10)
    else:
        ax.set_xticklabels([])


def build_dashboard(
    weather_df: pd.DataFrame,
    ndvi_df: pd.DataFrame,
    cdl_df: pd.DataFrame,
    field_slug: str,
    year: int,
    output_path: Path,
):
    """Create the vertical-stack multi-panel dashboard."""
    has_weather = not weather_df.empty
    has_ndvi = not ndvi_df.empty
    has_cdl = not cdl_df.empty

    # 3 panels max: NDVI, GDD, Precip+Stress
    n_panels = 0
    panel_heights = []
    if has_ndvi:
        n_panels += 1
        panel_heights.append(1)
    if has_weather:
        n_panels += 3  # GDD + Temp + Precip
        panel_heights.extend([1, 1, 1.4])

    if n_panels == 0:
        print("  No data to plot.", file=sys.stderr)
        return

    fig = plt.figure(figsize=(18, 3 + 2.5 * n_panels))
    gs = fig.add_gridspec(
        n_panels, 1,
        height_ratios=panel_heights,
        hspace=0.25,
        left=0.08, right=0.90, top=0.92, bottom=0.08,
    )

    # ---- Header ----
    header_lines = [f"Field {field_slug} — {year}"]
    if has_cdl:
        crop_parts = []
        for _, row in cdl_df.iterrows():
            name = row.get("crop_name", f"Class {row['crop_code']}")
            crop_parts.append(f"{name} ({row['pct']:.0f}%)")
        header_lines.append(" | ".join(crop_parts[:3]))
    fig.suptitle("\n".join(header_lines), fontsize=15, fontweight="bold", y=0.98)

    ax_idx = 0

    # ---- 1. NDVI Season Curve ----
    if has_ndvi:
        ax = fig.add_subplot(gs[ax_idx])
        ax_idx += 1
        ax.plot(ndvi_df["doy"], ndvi_df["mean_ndvi"], "s-", color="#2ca02c",
                linewidth=2, markersize=7, markerfacecolor="white",
                markeredgewidth=1.5, markeredgecolor="#2ca02c", zorder=3)
        ax.fill_between(ndvi_df["doy"],
                         ndvi_df["mean_ndvi"] - ndvi_df["std_ndvi"],
                         ndvi_df["mean_ndvi"] + ndvi_df["std_ndvi"],
                         alpha=0.15, color="#2ca02c")

        peak = ndvi_df.loc[ndvi_df["mean_ndvi"].idxmax()]
        ax.annotate(f"Peak NDVI: {peak['mean_ndvi']:.3f}\nDOY {int(peak['doy'])}",
                     xy=(peak["doy"], peak["mean_ndvi"]),
                     xytext=(10, 20), textcoords="offset points",
                     fontsize=9, fontweight="bold", color="darkgreen",
                     arrowprops=dict(arrowstyle="->", color="darkgreen", lw=1.2))

        ax.set_ylabel("Mean NDVI", fontsize=11)
        ax.set_ylim(-0.1, 1.0)
        ax.axhline(y=0, color="grey", linestyle=":", linewidth=0.7)
        ax.set_title("NDVI Season Curve", fontsize=12, fontweight="bold", loc="left")
        ax.grid(True, alpha=0.3)
        _apply_xaxis(ax, show_labels=True)

        if has_ndvi and len(ndvi_df) > 1 and ndvi_df["mean_ndvi"].std() > 0.01:
            ndvi_score = "GOOD" if ndvi_df["mean_ndvi"].max() > 0.6 else "LOW"
            ax.text(0.99, 0.95, f"Data Quality: {ndvi_score}",
                    transform=ax.transAxes, ha="right", va="top",
                    fontsize=9, color="darkgreen" if ndvi_score == "GOOD" else "darkorange",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))

    # ---- 2. GDD Accumulation ----
    if has_weather:
        ax = fig.add_subplot(gs[ax_idx])
        ax_idx += 1
        ax.plot(weather_df["doy"], weather_df["gdd_cum_F"], "-", color="#d62728",
                linewidth=2, zorder=3)
        ax.fill_between(weather_df["doy"], 0, weather_df["gdd_cum_F"],
                         alpha=0.1, color="#d62728")
        total_gdd_F = weather_df["gdd_cum_F"].iloc[-1] if not weather_df.empty else 0
        ax.set_ylabel("Cumulative GDD (F-days)", fontsize=11)
        ax.set_title(f"GDD Accumulation (Base 50°F — Total: {total_gdd_F:.0f} F-days)",
                     fontsize=12, fontweight="bold", loc="left")
        ax.grid(True, alpha=0.3)
        _apply_xaxis(ax, show_labels=True)

        # Frost date indicators
        last_frost, first_frost = frost_dates(weather_df)
        if last_frost:
            ax.axvline(x=last_frost, color="#9467bd", linestyle="--", linewidth=1.2, alpha=0.7)
            ax.annotate(f"Last Frost (DOY {last_frost})",
                        xy=(last_frost, ax.get_ylim()[1] * 0.95),
                        fontsize=8, color="#9467bd", ha="center", fontweight="bold")
        if first_frost:
            ax.axvline(x=first_frost, color="#9467bd", linestyle="--", linewidth=1.2, alpha=0.7)
            ax.annotate(f"First Frost (DOY {first_frost})",
                        xy=(first_frost, ax.get_ylim()[1] * 0.85),
                        fontsize=8, color="#9467bd", ha="center", fontweight="bold")

        # ---- 3. Daily Mean Temperature ----
        ax = fig.add_subplot(gs[ax_idx])
        ax_idx += 1
        ax.fill_between(weather_df["doy"], weather_df["T2M_MIN_F"], weather_df["T2M_MAX_F"],
                        alpha=0.2, color="#e6550d", label="Min–Max range")
        ax.plot(weather_df["doy"], weather_df["T2M_F"], "-", color="#e6550d",
                linewidth=1.5, alpha=0.9, zorder=3, label="Mean (T2M)")
        ax.axhline(y=32, color="#3182bd", linestyle=":", linewidth=0.8, alpha=0.5)
        ax.set_ylabel("Temperature (°F)", fontsize=11)
        ax.set_title("Daily Mean Temperature", fontsize=12, fontweight="bold", loc="left")
        ax.grid(True, alpha=0.3)
        _apply_xaxis(ax, show_labels=True)

        # Heat stress day indicators (T2M_F > 90°F)
        heat_stress = weather_df[weather_df["T2M_MAX_F"] > 90]
        for _, hs in heat_stress.iterrows():
            ax.axvline(x=hs["doy"], color="red", linewidth=0.6, alpha=0.35, zorder=1)

        handles, labels = ax.get_legend_handles_labels()
        handles.append(plt.Line2D([0], [0], color="red", linewidth=1, alpha=0.5))
        labels.append("Heat stress (>90°F)")
        ax.legend(handles=handles, labels=labels, fontsize=8, loc="upper left")

        # ---- 4. Precipitation + Stress Overlay ----
        ax = fig.add_subplot(gs[ax_idx])
        ax_idx += 1

        precip_max = weather_df["precip_in"].max()
        y_top = precip_max * 1.4 if precip_max > 0 else 10

        # Dry spell bands (behind everything)
        spells = compute_dry_spells(weather_df)
        for s in spells:
            ax.axvspan(s["start_doy"], s["end_doy"],
                       color="#ff7f0e", alpha=0.12, zorder=1)

        # Precipitation bars
        ax.bar(weather_df["doy"], weather_df["precip_in"], width=0.8,
               color="#1f77b4", alpha=0.8, edgecolor="none", zorder=2)

        # Heavy rain threshold line (1.0 inches)
        ax.axhline(y=1.0, color="red", linestyle="--", linewidth=1)

        # Heavy rain markers
        heavy = weather_df[weather_df["precip_in"] > 1.0]
        if not heavy.empty:
            ax.scatter(heavy["doy"], heavy["precip_in"], color="red", s=30,
                       zorder=5, marker="o", edgecolors="darkred", linewidths=0.5)
            for _, hr in heavy.iterrows():
                ax.annotate(f"{hr['precip_in']:.1f}",
                            xy=(hr["doy"], hr["precip_in"]),
                            xytext=(0, 6), textcoords="offset points",
                            fontsize=7, ha="center", color="darkred")

        # Cumulative precipitation (twin axis)
        ax_twin = ax.twinx()
        ax_twin.plot(weather_df["doy"], weather_df["precip_in"].cumsum(),
                     "-", color="#e6550d", linewidth=2, zorder=4)
        cumul_total_in = weather_df["precip_in"].sum()
        ax_twin.set_ylabel(f"Cumulative Precip (in) — Total: {cumul_total_in:.1f} in",
                           fontsize=11, color="#e6550d")
        ax_twin.tick_params(axis="y", labelcolor="#e6550d")
        ax_twin.set_ylim(0, cumul_total_in * 1.15)

        # Heat stress markers (red triangles near top)
        heat = weather_df[weather_df["T2M_MAX"] > HEAT_THRESHOLD_C]
        if not heat.empty:
            ax.scatter(heat["doy"], np.full(len(heat), y_top * 0.92),
                       color="red", marker="v", s=16, zorder=6,
                       label="Heat >90°F")

        # Cold stress markers (blue triangles near bottom)
        cold = weather_df[weather_df["T2M_MIN"] < COLD_THRESHOLD_C]
        if not cold.empty:
            ax.scatter(cold["doy"], np.full(len(cold), y_top * 0.08),
                       color="#3182bd", marker="^", s=16, zorder=6,
                       label="Cold <32°F")

        ax.set_ylabel("Precipitation (in)", fontsize=11)
        ax.set_title("Precipitation & Season Stress", fontsize=12,
                     fontweight="bold", loc="left")
        ax.set_ylim(0, y_top)
        ax.grid(True, alpha=0.3, axis="y")
        _apply_xaxis(ax, show_labels=True)

        # Combined legend
        legend_elements = [
            plt.Line2D([0], [0], color="#1f77b4", lw=4, label="Daily precip"),
            plt.Line2D([0], [0], color="#e6550d", lw=2, label="Cumulative precip"),
            plt.Line2D([0], [0], color="red", lw=0, marker="o", markersize=5,
                       label="Heavy rain >1.0 in"),
            plt.Line2D([0], [0], color="red", lw=0, marker="v", markersize=6,
                       label="Heat >90°F"),
            plt.Line2D([0], [0], color="#3182bd", lw=0, marker="^", markersize=6,
                       label="Cold <32°F"),
            plt.Rectangle((0, 0), 1, 1, color="#ff7f0e", alpha=0.25,
                          label=f"Dry spell (≥{DRY_SPELL_MIN_DAYS}d)"),
        ]
        ax.legend(handles=legend_elements, fontsize=7.5, loc="upper right",
                  ncol=2, framealpha=0.85)

    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    print(f"  Dashboard saved: {output_path}")


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Field Season Weather Dashboard")
    parser.add_argument("--grower", default="minnesota-north")
    parser.add_argument("--farm", default="minnesota-north-minnesota")
    parser.add_argument("--field", default="osm-1491018233")
    parser.add_argument("--year", type=int, required=True, help="Target year")
    parser.add_argument("--data-root",
                        default="/home/coder/my-farm-advisor-runtime/data-pipeline")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    field_dir = data_root / "growers" / args.grower / "farms" / args.farm / "fields" / args.field
    farm_tables_dir = data_root / "growers" / args.grower / "farms" / args.farm / "derived" / "tables"
    boundary_path = field_dir / "boundary" / "field_boundary.geojson"
    weather_csv = field_dir / "weather" / "daily_weather.csv"
    sat_dir = field_dir / "satellite" / "sentinel" / str(args.year)
    output_png = field_dir / "derived" / "reports" / f"field_season_dashboard_{args.year}.png"
    output_json = field_dir / "derived" / "summaries" / f"field_season_summary_{args.year}.json"

    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    print(f"  Field:       {args.field}")
    print(f"  Year:        {args.year}")
    print(f"  Weather:     {weather_csv}")
    print(f"  Satellite:   {sat_dir}")
    print(f"  Boundary:    {boundary_path}")
    print()

    # Load CDL
    cdl_df = load_cdl(farm_tables_dir, args.farm, args.year, args.field)
    if cdl_df.empty:
        print("  [INFO] No CDL data for this field/year.")
    else:
        print("  CDL Crop Composition:")
        for _, r in cdl_df.iterrows():
            print(f"    {r['crop_name']:<20} {r['pct']:>6.2f}%")
    print()

    # Load weather
    weather_df = load_weather(weather_csv, args.year)
    if weather_df.empty:
        print("  [WARN] No weather data for this year.")
    else:
        print(f"  Weather: {len(weather_df)} days loaded")
        total_gdd_F = weather_df["gdd_cum_F"].iloc[-1]
        total_precip_in = weather_df["precip_in"].sum()
        print(f"    Total GDD:  {total_gdd_F:.0f} F-days")
        print(f"    Total Precip: {total_precip_in:.1f} in")
        heat = (weather_df["T2M_MAX"] > HEAT_THRESHOLD_C).sum()
        cold = (weather_df["T2M_MIN"] < COLD_THRESHOLD_C).sum()
        heavy = (weather_df["precip_mm"] > HEAVY_RAIN_MM).sum()
        print(f"    Heat days: {heat}, Cold nights: {cold}, Heavy rain: {heavy}")
        spells = compute_dry_spells(weather_df)
        print(f"    Dry spells (≥{DRY_SPELL_MIN_DAYS}d with little precip): {len(spells)}")
        for s in spells:
            print(f"      DOY {s['start_doy']}–{s['end_doy']}  ({s['duration_days']} d)")
    print()

    # Load NDVI
    scenes = find_ndvi_scenes(sat_dir)
    ndvi_df = pd.DataFrame()
    if scenes:
        print(f"  NDVI scenes found: {len(scenes)}")
        for dt, path in scenes:
            print(f"    {dt.date()}  {path.name}")
        ndvi_df = compute_ndvi_timeseries(scenes, boundary_path)
        if not ndvi_df.empty:
            print(f"  NDVI time series: {len(ndvi_df)} points")
            peak = ndvi_df.loc[ndvi_df["mean_ndvi"].idxmax()]
            print(f"    Peak NDVI: {peak['mean_ndvi']:.3f} on DOY {int(peak['doy'])}")
    else:
        print("  [INFO] No NDVI scenes for this year.")
    print()

    # Save JSON summary
    summary = {
        "field": args.field,
        "year": args.year,
        "cdl": cdl_df[["crop_code", "crop_name", "pct"]].to_dict(orient="records")
        if not cdl_df.empty else [],
        "weather": {
            "days": len(weather_df),
            "total_gdd": round(float(weather_df["gdd_cum"].iloc[-1]), 1)
            if not weather_df.empty else None,
            "total_precip_mm": round(float(weather_df["precip_mm"].sum()), 1)
            if not weather_df.empty else None,
            "heat_days": int((weather_df["T2M_MAX"] > HEAT_THRESHOLD_C).sum())
            if not weather_df.empty else None,
            "cold_days": int((weather_df["T2M_MIN"] < COLD_THRESHOLD_C).sum())
            if not weather_df.empty else None,
            "heavy_rain_days": int((weather_df["precip_mm"] > HEAVY_RAIN_MM).sum())
            if not weather_df.empty else None,
            "dry_spells": compute_dry_spells(weather_df)
            if not weather_df.empty else [],
        },
        "ndvi": {
            "scenes": len(scenes),
            "time_series_points": len(ndvi_df),
            "peak_ndvi": round(float(ndvi_df["mean_ndvi"].max()), 4)
            if not ndvi_df.empty else None,
            "peak_doy": int(ndvi_df.loc[ndvi_df["mean_ndvi"].idxmax()]["doy"])
            if not ndvi_df.empty else None,
        },
    }
    with open(output_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Summary saved: {output_json}")

    # Build dashboard
    build_dashboard(weather_df, ndvi_df, cdl_df, args.field, args.year, output_png)
    print("  Done.")


if __name__ == "__main__":
    main()
