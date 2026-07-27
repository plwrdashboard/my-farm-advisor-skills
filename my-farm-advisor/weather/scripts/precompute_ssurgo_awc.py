"""Pre-compute SSURGO Available Water Capacity (AWC) for all corn fields.

Queries NRCS SDA for each corn field's SSURGO soil data, computes
depth-weighted AWC over the 0-100cm profile, clips SSURGO polygon
geometries to the field boundary, and saves the result as a GeoJSON
file for fast loading by the weather stress dashboard.

Output (per field):
    {field_dir}/derived/features/ssurgo_awc.geojson
    {field_dir}/derived/summaries/ssurgo_awc_summary.json

Usage:
    python precompute_ssurgo_awc.py
    python precompute_ssurgo_awc.py --grower iowa-north
    python precompute_ssurgo_awc.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SSURGO_SRC = _HERE.parent.parent / "soil" / "ssurgo-soil" / "src"
sys.path.insert(0, str(_SSURGO_SRC))

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import wkt, geometry

import ssurgo_soil

DATA_PIPELINE_ROOT = Path(
    os.environ.get(
        "DATA_PIPELINE_DATA_ROOT",
        "/home/coder/my-farm-advisor-runtime/data-pipeline",
    )
)
GROWERS_ROOT = DATA_PIPELINE_ROOT / "growers"
MAX_DEPTH_CM = 100


SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def query_mupolygons_for_field(field_wkt: str, mukey_list: list[str]) -> gpd.GeoDataFrame:
    mukeys = [str(m) for m in mukey_list]
    if not mukeys:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    sql = f"""SELECT m.mukey, m.mupolygonkey, m.mupolygongeo.STAsText() AS wkt
FROM mupolygon m
WHERE m.mukey IN ({", ".join(mukeys)})
  AND m.mupolygonkey IN (
    SELECT * FROM SDA_Get_Mupolygonkey_from_intersection_with_WktWgs84('{field_wkt}')
  )"""
    try:
        resp = requests.post(SDA_URL, data={"query": sql, "format": "JSON"}, timeout=120)
        resp.raise_for_status()
        rows = resp.json().get("Table", [])
    except Exception:
        rows = []
    records = []
    for row in rows:
        try:
            records.append({
                "mukey": str(row[0]),
                "mupolygonkey": str(row[1]),
                "geometry": wkt.loads(str(row[2])),
            })
        except Exception:
            continue
    if not records:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(records, crs="EPSG:4326")


def discover_fields(grower_slug: str) -> list[dict]:
    fields: list[dict] = []
    farms_dir = GROWERS_ROOT / grower_slug / "farms"
    if not farms_dir.exists():
        return fields

    for fdir in sorted(farms_dir.iterdir()):
        if not fdir.is_dir():
            continue
        farm_slug = fdir.name

        inventory_csv = fdir / "manifests" / "field-inventory.csv"
        if not inventory_csv.exists():
            continue

        df = pd.read_csv(inventory_csv)
        for _, row in df.iterrows():
            fid = str(row["field_id"])
            fslug = str(row.get("field_slug", fid))
            geojson_path = fdir / "fields" / fslug / "boundary" / "field_boundary.geojson"
            if not geojson_path.exists():
                continue
            fields.append({
                "field_id": fid,
                "field_slug": fslug,
                "farm_slug": farm_slug,
                "grower_slug": grower_slug,
                "geojson_path": str(geojson_path),
            })
    return fields


def field_geojson_to_wkt(geojson_path: str) -> str | None:
    geo = load_json(Path(geojson_path))
    if not geo.get("features"):
        return None
    feat = geo["features"][0]
    geom = feat.get("geometry")
    if not geom:
        return None
    return geometry.shape(geom).wkt


def compute_mukey_awc(soil_df: pd.DataFrame) -> dict[str, dict]:
    """Compute depth-weighted AWC per mukey.

    Returns dict mapping mukey -> {awc_avg, aws_in, muname, compname, comppct_r}
    """
    result: dict[str, dict] = {}

    for mukey, grp in soil_df.groupby("mukey"):
        grp = grp.copy()
        for col in ["hzdept_r", "hzdepb_r", "awc_r", "comppct_r"]:
            grp[col] = pd.to_numeric(grp[col], errors="coerce")

        muname = grp["muname"].iloc[0] if "muname" in grp.columns else ""
        horizons = grp[
            grp["hzdept_r"].notna()
            & grp["hzdepb_r"].notna()
            & grp["awc_r"].notna()
        ].copy()

        if horizons.empty:
            result[mukey] = {"awc_avg": None, "aws_in": None,
                             "muname": muname, "compname": None, "comppct_r": None}
            continue

        horizons["thickness_cm"] = horizons["hzdepb_r"] - horizons["hzdept_r"]
        horizons["thickness_in"] = horizons["thickness_cm"] / 2.54
        horizons["aws_horizon_in"] = horizons["awc_r"] * horizons["thickness_in"]

        total_aws_in = 0.0
        total_weight = 0.0
        total_thickness_in = 0.0
        dominant_comp = None
        dominant_pct = 0.0

        for cokey, comp_grp in horizons.groupby("cokey"):
            comp_pct = comp_grp["comppct_r"].iloc[0]
            if pd.isna(comp_pct) or comp_pct <= 0:
                comp_pct = 1.0

            comp_aws = comp_grp["aws_horizon_in"].sum()
            comp_depth = comp_grp["thickness_in"].sum()

            total_aws_in += comp_aws * (comp_pct / 100.0)
            total_weight += comp_pct / 100.0
            total_thickness_in += comp_depth * (comp_pct / 100.0)

            if comp_pct > dominant_pct:
                dominant_pct = comp_pct
                dominant_comp = comp_grp["compname"].iloc[0]

        if total_weight <= 0 or total_thickness_in <= 0:
            result[mukey] = {"awc_avg": None, "aws_in": None,
                             "muname": muname, "compname": dominant_comp,
                             "comppct_r": dominant_pct}
            continue

        awc_avg = total_aws_in / total_thickness_in if total_thickness_in > 0 else None
        result[mukey] = {
            "awc_avg": round(awc_avg, 4) if awc_avg is not None else None,
            "aws_in": round(total_aws_in, 2) if total_aws_in is not None else None,
            "muname": muname,
            "compname": dominant_comp,
            "comppct_r": round(dominant_pct, 1),
        }
    return result


def process_field(field: dict, force: bool = False) -> bool:
    fid = field["field_id"]
    geojson_path = Path(field["geojson_path"])
    field_dir = geojson_path.parent.parent
    awc_geojson_path = field_dir / "derived" / "features" / "ssurgo_awc.geojson"
    awc_summary_path = field_dir / "derived" / "summaries" / "ssurgo_awc_summary.json"

    if not force and awc_geojson_path.exists():
        print(f"  [SKIP] {fid} — already cached")
        return True

    wkt_poly = field_geojson_to_wkt(field["geojson_path"])
    if not wkt_poly:
        print(f"  [SKIP] {fid} — could not load boundary")
        return False

    print(f"  [QUERY] {fid} — downloading SSURGO (0-{MAX_DEPTH_CM}cm)...", end=" ")
    try:
        sql = ssurgo_soil._build_full_ssurgo_query(wkt_poly, MAX_DEPTH_CM)
        rows = ssurgo_soil.query_sda_extended(sql)
        if not rows:
            print("no data")
            return False
        soil_df = pd.DataFrame(rows)
        for col in ["comppct_r", "hzdept_r", "hzdepb_r", "awc_r",
                     "om_r", "ph1to1h2o_r", "claytotal_r", "sandtotal_r",
                     "silttotal_r", "dbthirdbar_r", "cec7_r"]:
            if col in soil_df.columns:
                soil_df[col] = pd.to_numeric(soil_df[col], errors="coerce")
        print(f"{len(rows)} records")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        return False

    print(f"  [AWC]   {fid} — computing depth-weighted AWC...", end=" ")
    mukey_awc = compute_mukey_awc(soil_df)
    if not mukey_awc:
        print("no components")
        return False
    print(f"{len(mukey_awc)} mukeys")

    mukeys = [mk for mk, v in mukey_awc.items() if v.get("aws_in") is not None]
    if not mukeys:
        print("no mukeys with AWC data")
        return False
    print(f"  [POLY]  {fid} — querying polygon geometries...", end=" ")
    try:
        polys = query_mupolygons_for_field(wkt_poly, mukeys)
        if polys.empty:
            print("no polygons")
            return False
        print(f"{len(polys)} polygons")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        return False

    polys["mukey"] = polys["mukey"].astype(str)
    for col in ["awc_avg", "aws_in", "muname", "compname", "comppct_r"]:
        polys[col] = polys["mukey"].map(
            lambda mk: mukey_awc.get(mk, {}).get(col)
        )

    print(f"  [CLIP]  {fid} — clipping to field boundary...", end=" ")
    try:
        field_gdf = gpd.read_file(str(geojson_path))
        if field_gdf.crs is None:
            field_gdf = field_gdf.set_crs("EPSG:4326")
        field_gdf = field_gdf.to_crs("EPSG:4326")
        clipped = gpd.overlay(polys, field_gdf, how="intersection")
        if clipped.empty:
            print("empty after clip")
            return False
        print(f"{len(clipped)} features")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        return False

    # Compute dominant AWC for the field
    valid = clipped[clipped["aws_in"].notna()]
    if not valid.empty:
        field_aws_in = float(valid["aws_in"].mean())
        field_awc_avg = float(valid["awc_avg"].mean())
    else:
        field_aws_in = None
        field_awc_avg = None

    summary = {
        "field_id": fid,
        "aws_in": field_aws_in,
        "awc_avg": field_awc_avg,
        "n_mukeys": len(mukey_awc),
        "n_polygons": len(clipped),
    }
    awc_summary_path.parent.mkdir(parents=True, exist_ok=True)
    awc_summary_path.write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    # Save GeoJSON
    keep_cols = ["mukey", "muname", "compname", "comppct_r",
                 "awc_avg", "aws_in", "geometry"]
    out_cols = [c for c in keep_cols if c in clipped.columns]
    out = clipped[out_cols].copy()
    out["mukey"] = out["mukey"].astype(str)
    out["muname"] = out["muname"].fillna("").astype(str)
    out["compname"] = out["compname"].fillna("").astype(str)
    for c in ["comppct_r", "awc_avg", "aws_in"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    awc_geojson_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(str(awc_geojson_path), driver="GeoJSON")
    print(f"  [SAVE]  {fid} — {awc_geojson_path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-compute SSURGO AWC for corn fields")
    parser.add_argument("--grower", type=str, default=None, help="Single grower slug to process")
    parser.add_argument("--force", action="store_true", default=False, help="Re-download existing")
    args = parser.parse_args()

    if args.grower:
        growers = [{"slug": args.grower, "display_name": args.grower}]
    else:
        growers = []
        for gdir in sorted(GROWERS_ROOT.iterdir()):
            if gdir.is_dir():
                meta = load_json(gdir / "grower.json")
                slug = meta.get("grower_slug") or gdir.name
                growers.append({"slug": slug, "display_name": meta.get("display_name", slug)})

    if not growers:
        print("No growers found.")
        return

    total = 0
    ok = 0
    for g in growers:
        print(f"\nGrower: {g['display_name']}")
        fields = discover_fields(g["slug"])
        if not fields:
            print("  No fields found.")
            continue
        for f in fields:
            total += 1
            if process_field(f, force=args.force):
                ok += 1
            time.sleep(0.5)  # rate limit courtesy

    print(f"\nDone. {ok}/{total} fields processed.")


if __name__ == "__main__":
    main()
