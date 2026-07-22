"""Batch ETc computation for all corn fields × years.

Reads existing field_season_summary_<year>.json files, computes
FAO-56 Penman-Monteith ETc, and appends ETc data to each summary.

Usage:
    python run_etc_batch.py
    python run_etc_batch.py --data-root /custom/path
"""

from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------
_DATA_PIPELINE_ROOT = Path(
    os.environ.get(
        "DATA_PIPELINE_DATA_ROOT",
        "/home/coder/my-farm-advisor-runtime/data-pipeline",
    )
)
_GROWERS_ROOT = _DATA_PIPELINE_ROOT / "growers"
_RM_TABLES = _DATA_PIPELINE_ROOT / "shared" / "corn_maturity" / "tables"

# ---------------------------------------------------------------------------
#  ETc / FAO-56 constants (copied from field_season_dashboard.py)
# ---------------------------------------------------------------------------
GDD_BASE_C = 10.0
GDD_CAP_C = 30.0
KC_INI = 0.30
KC_MID = 1.15
KC_END = 0.70
ALBEDO = 0.23
SIGMA = 4.903e-9
_BASE_STAGE_THRESHOLDS = [(120, 1415), (1415, 1800), (1800, 2190)]

YEARS = [2021, 2022, 2023, 2024, 2025]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cdl_table_prefix(farm_slug: str) -> str:
    norm = farm_slug.strip().replace("-", "_")
    if norm.endswith("_farm"):
        norm = norm[: -len("_farm")]
    return norm


def _power_elevation(lat: float, lon: float) -> float:
    url = (
        f"https://power.larc.nasa.gov/api/temporal/daily/point"
        f"?parameters=T2M&latitude={lat}&longitude={lon}"
        f"&start=20250101&end=20250103&community=RE&format=JSON"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        coord = data.get("geometry", {}).get("coordinates", [])
        return float(coord[2]) if len(coord) >= 3 else 0.0
    except Exception as exc:
        print(f"    [WARN] Elevation fetch failed for {lat},{lon}: {exc}")
        return 0.0


def _hargreaves_ra(lat_rad: float, doy: np.ndarray) -> np.ndarray:
    dr = 1 + 0.033 * np.cos(2 * np.pi / 365 * doy)
    delta = 0.409 * np.sin(2 * np.pi / 365 * doy - 1.39)
    ws = np.arccos(-np.tan(lat_rad) * np.tan(delta))
    ra = (24 * 60 / np.pi) * 0.0820 * dr * (
        ws * np.sin(lat_rad) * np.sin(delta)
        + np.cos(lat_rad) * np.cos(delta) * np.sin(ws)
    )
    return ra


def _compute_eto_fao56(
    tmax, tmin, tmean, rs, u10, rh, lat_deg: float, elev: float, doy: np.ndarray
) -> np.ndarray:
    lat = np.radians(lat_deg)
    u2 = u10 * 4.87 / np.log(67.8 * 10 - 5.42)
    es_tmean = 0.6108 * np.exp(17.27 * tmean / (tmean + 237.3))
    delta = 4098 * es_tmean / (tmean + 237.3) ** 2
    p = 101.3 * ((293 - 0.0065 * elev) / 293) ** 5.26
    gamma = 0.665e-3 * p
    es_tmax = 0.6108 * np.exp(17.27 * tmax / (tmax + 237.3))
    es_tmin = 0.6108 * np.exp(17.27 * tmin / (tmin + 237.3))
    es = (es_tmax + es_tmin) / 2
    ea = es * rh / 100
    ra = _hargreaves_ra(lat, doy)
    rso = np.maximum((0.75 + 2e-5 * elev) * ra, 0.01)
    rns = (1 - ALBEDO) * rs
    tmax_k = tmax + 273.16
    tmin_k = tmin + 273.16
    rnl = np.maximum(
        SIGMA * (tmax_k ** 4 + tmin_k ** 4) / 2
        * (0.34 - 0.14 * np.sqrt(ea))
        * (1.35 * rs / rso - 0.35),
        0,
    )
    rn = rns - rnl
    numer1 = 0.408 * delta * rn
    numer2 = gamma * (900 / (tmean + 273)) * u2 * (es - ea)
    denom = delta + gamma * (1 + 0.34 * u2)
    return np.maximum((numer1 + numer2) / denom, 0)


def _compute_etc(weather_df: pd.DataFrame, lat: float, elev: float, rm_value: float) -> pd.DataFrame:
    df = weather_df.copy()
    lat_deg = float(df["lat"].iloc[0]) if "lat" in df.columns else lat
    doy = df["doy"].values
    rs = df["ALLSKY_SFC_SW_DWN"].values
    u10 = df["WS10M"].values
    rh = df["RH2M"].values
    eto = _compute_eto_fao56(
        df["T2M_MAX"].values, df["T2M_MIN"].values, df["T2M"].values,
        rs, u10, rh, lat_deg, elev, doy,
    )
    df["eto_mm"] = eto

    df["tavg"] = (df["T2M_MAX"] + df["T2M_MIN"]) / 2
    spring = df[(df["doy"] >= 91) & (df["doy"] <= 152)]
    plant_doy = 121
    for i in range(len(spring) - 4):
        if all(spring.iloc[i:i + 5]["tavg"] >= 10):
            plant_doy = int(spring.iloc[i]["doy"])
            break

    gdd_cum = df["gdd_cum"]
    scale = rm_value / 114.0
    starts, ends = [], []
    for lo, hi in _BASE_STAGE_THRESHOLDS:
        lo_s, hi_s = int(round(lo * scale)), int(round(hi * scale))
        cum = gdd_cum.values
        doys = gdd_cum.index.values
        d_lo = int(doys[np.argmax(cum >= lo_s)]) if np.any(cum >= lo_s) else None
        d_hi = int(doys[np.argmax(cum >= hi_s)]) if np.any(cum >= hi_s) else None
        starts.append(d_lo)
        ends.append(d_hi)

    def _kc(doy_val):
        if doy_val < plant_doy:
            return 0.0
        s0, s1, s2 = starts
        e0, e1, e2 = ends
        if s0 is None or doy_val < s0:
            return KC_INI
        if e0 is None or doy_val < e0:
            total = max((e0 - s0), 1)
            frac = (doy_val - s0) / total if doy_val >= s0 else 0
            return KC_INI + (KC_MID - KC_INI) * min(frac, 1.0)
        if s1 is None or doy_val < s1:
            return KC_MID
        if e1 is None or doy_val < e1:
            return KC_MID
        if s2 is None or doy_val < s2:
            total = max((e1 - s1), 1) if e1 and s1 else 1
            frac = (doy_val - s1) / total if doy_val >= s1 else 0
            return KC_MID + (KC_END - KC_MID) * min(frac, 1.0)
        if e2 is None or doy_val < e2:
            total = max((e2 - s2), 1) if e2 and s2 else 1
            frac = (doy_val - s2) / total if doy_val >= s2 else 0
            return KC_MID + (KC_END - KC_MID) * min(frac, 1.0)
        return KC_END

    df["kc"] = df["doy"].apply(_kc)
    df["etc_mm"] = df["eto_mm"] * df["kc"]
    df["cum_etc"] = df["etc_mm"].cumsum()
    return df


def load_rm_table(year: int) -> dict[str, tuple[float, float | None]]:
    path = _RM_TABLES / f"rm_by_fips_{year}.csv"
    if not path.exists():
        print(f"  [WARN] RM table not found: {path}")
        return {}
    rm_by_fips: dict[str, tuple[float, float | None]] = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rm_val = float(row["rm_relative_maturity"])
            rm_b = row.get("rm_band", "")
            band = float(rm_b) if rm_b else None
            rm_by_fips[row["fips"]] = (rm_val, band)
    return rm_by_fips


def get_field_fips(boundary_path: Path) -> str | None:
    geo = _load_json(boundary_path)
    if not geo.get("features"):
        return None
    props = geo["features"][0].get("properties", {})
    state_fips = str(props.get("state_fips", ""))
    county_fips = str(props.get("county_fips", ""))
    if state_fips and county_fips:
        return state_fips + county_fips
    return None


def load_weather(weather_path: Path, year: int) -> pd.DataFrame:
    if not weather_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(weather_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.year == year].copy()
    if df.empty:
        return df
    df["doy"] = df["date"].dt.dayofyear
    df["gdd_daily"] = df.apply(
        lambda r: max(0.0, min((r["T2M_MAX"] + r["T2M_MIN"]) / 2, GDD_CAP_C) - GDD_BASE_C),
        axis=1,
    )
    df["gdd_cum"] = df["gdd_daily"].cumsum()
    df["precip_mm"] = df["PRECTOTCORR"].clip(lower=0)
    return df


def get_corn_fields(farm_cdl_path: Path, field_ids: list[str]) -> set[str]:
    if not farm_cdl_path.exists():
        return set()
    cdl = pd.read_csv(farm_cdl_path)
    cdl_corn = cdl[cdl["crop_name"].str.lower() == "corn"]
    corn_pct = cdl_corn.groupby("field_id")["pct"].sum()
    return {str(fid) for fid, pct in corn_pct.items() if pct > 50}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Data root: {_DATA_PIPELINE_ROOT}")
    print()

    if not _GROWERS_ROOT.exists():
        print(f"[ERROR] Growers root not found: {_GROWERS_ROOT}")
        sys.exit(1)

    # Pre-load RM tables for all years
    rm_tables: dict[int, dict[str, tuple[float, float | None]]] = {}
    for year in YEARS:
        rm_tables[year] = load_rm_table(year)

    # Elevation cache: (lat, lon) -> elev
    elev_cache: dict[tuple[float, float], float] = {}

    total_processed = 0
    total_skipped = 0
    daily_records: list[pd.DataFrame] = []

    for gdir in sorted(_GROWERS_ROOT.iterdir()):
        if not gdir.is_dir():
            continue
        grower_slug = gdir.name
        farms_dir = gdir / "farms"
        if not farms_dir.exists():
            continue

        for fdir in sorted(farms_dir.iterdir()):
            if not fdir.is_dir():
                continue
            farm_slug = fdir.name
            prefix = _cdl_table_prefix(farm_slug)

            boundary_path = None

            # Map field_id -> field_dir
            field_dirs: dict[str, Path] = {}
            fields_root = fdir / "fields"
            if not fields_root.exists():
                continue
            for field_dir in sorted(fields_root.iterdir()):
                if not field_dir.is_dir():
                    continue
                bid_path = field_dir / "boundary" / "field_boundary.geojson"
                if not bid_path.exists():
                    continue
                fid = field_dir.name
                field_dirs[fid] = field_dir
                if boundary_path is None:
                    boundary_path = bid_path

            if not field_dirs:
                continue

            for year in YEARS:
                cdl_path = fdir / "derived" / "tables" / f"{prefix}_{year}_cdl.csv"
                corn_ids = get_corn_fields(cdl_path, list(field_dirs.keys()))
                if not corn_ids:
                    continue

                for fid in sorted(corn_ids):
                    field_dir = field_dirs[fid]
                    weather_csv = field_dir / "weather" / "daily_weather.csv"
                    summary_path = field_dir / "derived" / "summaries" / f"field_season_summary_{year}.json"

                    if not summary_path.exists():
                        print(f"  [SKIP] {grower_slug}/{farm_slug}/{fid}/{year} — no summary JSON")
                        total_skipped += 1
                        continue

                    weather_df = load_weather(weather_csv, year)
                    if weather_df.empty:
                        print(f"  [SKIP] {grower_slug}/{farm_slug}/{fid}/{year} — no weather data")
                        total_skipped += 1
                        continue

                    # Get RM value for this field's FIPS
                    bid_path = field_dir / "boundary" / "field_boundary.geojson"
                    fips = get_field_fips(bid_path)
                    rm_val = None
                    rm_band = None
                    if fips and fips in rm_tables.get(year, {}):
                        rm_val, rm_band = rm_tables[year][fips]
                    if rm_val is None and fips:
                        # Fall back to year-average
                        avg_csv = _RM_TABLES / "rm_by_fips_2021_2025_average.csv"
                        if avg_csv.exists():
                            with open(avg_csv) as f:
                                for row in csv.DictReader(f):
                                    if row["fips"] == fips:
                                        rm_val = float(row["rm_relative_maturity"])
                                        rm_b = row.get("rm_band", "")
                                        rm_band = float(rm_b) if rm_b else None
                                        break
                    if rm_val is None:
                        print(f"  [SKIP] {grower_slug}/{farm_slug}/{fid}/{year} — no RM for FIPS {fips}")
                        total_skipped += 1
                        continue

                    # Get elevation (cached per lat/lon)
                    lat = float(weather_df["lat"].iloc[0]) if "lat" in weather_df.columns else None
                    lon = float(weather_df["lon"].iloc[0]) if "lon" in weather_df.columns else None
                    if lat is None or lon is None:
                        print(f"  [SKIP] {grower_slug}/{farm_slug}/{fid}/{year} — no lat/lon in weather")
                        total_skipped += 1
                        continue

                    elev_key = (round(lat, 4), round(lon, 4))
                    if elev_key not in elev_cache:
                        print(f"    Fetching elevation for {lat},{lon}...")
                        elev_cache[elev_key] = _power_elevation(lat, lon)
                    elev = elev_cache[elev_key]

                    # Compute ETc
                    try:
                        etc_df = _compute_etc(weather_df, lat, elev, rm_val)
                    except Exception as exc:
                        print(f"    [ERROR] ETc computation failed: {exc}")
                        total_skipped += 1
                        continue

                    total_etc = float(etc_df["etc_mm"].sum())
                    total_precip = float(etc_df["PRECTOTCORR"].sum())
                    water_balance = total_etc - total_precip
                    category = "deficit" if water_balance > 0 else "surplus"
                    stage_starts = [int(s) if s is not None else None for s in etc_df.attrs.get("stage_starts", [])]
                    stage_ends = [int(s) if s is not None else None for s in etc_df.attrs.get("stage_ends", [])]

                    etc_entry = {
                        "rm_value": round(rm_val, 1) if rm_val else None,
                        "rm_band": round(rm_band, 0) if rm_band else None,
                        "total_etc_mm": round(total_etc, 1),
                        "total_precip_mm": round(total_precip, 1),
                        "water_balance_mm": round(water_balance, 1),
                        "water_balance_category": category,
                        "daily_count": len(etc_df),
                    }

                    # Add to summary JSON
                    summary = _load_json(summary_path)
                    summary["etc"] = etc_entry
                    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

                    out = etc_df[["etc_mm", "eto_mm", "kc"]].copy()
                    out["date"] = weather_df["date"].values
                    out["field_id"] = fid
                    out["PRECTOTCORR"] = weather_df["PRECTOTCORR"].values
                    out["deficit_mm"] = out["etc_mm"] - out["PRECTOTCORR"]
                    out["gdd"] = weather_df["gdd_daily"].values
                    out["cum_gdd"] = weather_df["gdd_cum"].values
                    daily_records.append(out[["date", "field_id", "eto_mm", "kc",
                                               "etc_mm", "PRECTOTCORR", "deficit_mm",
                                               "gdd", "cum_gdd"]])

                    print(f"  [OK]   {grower_slug}/{farm_slug}/{fid}/{year}  "
                          f"ETc={total_etc:.0f}mm  Precip={total_precip:.0f}mm  "
                          f"{category}={abs(water_balance):.0f}mm")
                    total_processed += 1

    print()
    print(f"Done. Processed: {total_processed}, Skipped: {total_skipped}")

    if daily_records:
        combined = pd.concat(daily_records, ignore_index=True)
        out_path = _DATA_PIPELINE_ROOT / "eda" / "plant-health" / "output" / "daily_etc.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(out_path, index=False)
        print(f"Wrote daily ETc CSV: {out_path} ({len(combined)} rows)")


if __name__ == "__main__":
    main()
