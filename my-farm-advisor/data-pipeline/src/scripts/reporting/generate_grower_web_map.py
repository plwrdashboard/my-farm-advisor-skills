#!/usr/bin/env python3
"""generate_grower_web_map.py — Grower-level interactive web map generator.

Generates a self-contained, lightweight HTML file with an interactive Leaflet
map showing all field polygons for all farms under a single grower.
Also embeds NDVI yearly composite layers as selectable, per-field overlays.

Usage:
    python scripts/reporting/generate_grower_web_map.py --grower-slug <slug>
    python scripts/reporting/generate_grower_web_map.py --all-growers

Output:
    growers/<grower_slug>/derived/reports/grower_web_map.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import struct
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

import numpy as np
from PIL import Image

# Add script parent to path so we can import lib modules when run standalone
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))

from paths import (
    GROWERS_ROOT,
    farm_boundary_path,
    grower_dir,
)

# Farm colors — distinct warm/cool palette for up to 6 farms
FARM_COLORS = [
    "#2E7D32",  # green
    "#1565C0",  # blue
    "#C62828",  # red
    "#F57C00",  # orange
    "#6A1B9A",  # purple
    "#00838F",  # teal
]

# Standard NDVI color ramp (brown → tan → yellow → green → dark green)
# Each entry: (max_val, (R, G, B))
NDVI_RAMP = [
    (0.0, (139, 69, 19)),     # Brown
    (0.2, (210, 180, 140)),   # Tan
    (0.4, (255, 255, 0)),     # Yellow
    (0.6, (144, 238, 144)),   # Light green
    (0.8, (34, 139, 34)),     # Green
    (1.0, (0, 100, 0)),       # Dark green
]

# Soil component color palette — distinguishable colors for map units
SOIL_COLORS = [
    "#E6194B", "#3CB44B", "#FFE119", "#4363D8", "#F58231",
    "#911EB4", "#42D4F4", "#F032E6", "#BFEF45", "#FABED4",
    "#469990", "#DCBEFF", "#9A6324", "#FFFAC8", "#800000",
    "#AAFFC3", "#808000", "#FFD8B1", "#000075", "#A9A9A9",
]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_farms(grower_slug: str) -> list[dict[str, str]]:
    """Discover all farms under a grower that have boundary GeoJSON files."""
    farms_dir = grower_dir(grower_slug) / "farms"
    if not farms_dir.exists():
        return []
    farms: list[dict[str, str]] = []
    for farm_dir in sorted(farms_dir.iterdir()):
        if not farm_dir.is_dir():
            continue
        farm_slug = farm_dir.name
        boundary = farm_boundary_path(grower_slug, farm_slug)
        if not boundary.exists():
            continue
        farm_json_path = farm_dir / "farm.json"
        farm_meta = _load_json(farm_json_path)
        farm_name = farm_meta.get("display_name") or farm_meta.get("farm_name") or farm_slug.replace("-", " ").title()
        farms.append({
            "farm_slug": farm_slug,
            "farm_name": farm_name,
            "boundary_path": str(boundary),
        })
    return farms


def _load_geojson_features(boundary_path: Path, farm_index: int, farm_slug: str, farm_name: str, grower_slug: str) -> list[dict]:
    """Read a farm boundary GeoJSON and return its features with injected metadata."""
    try:
        data = json.loads(boundary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  WARN: could not read {boundary_path}: {exc}")
        return []

    features = data.get("features", [])
    color = FARM_COLORS[farm_index % len(FARM_COLORS)]

    enriched: list[dict] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = dict(feat.get("properties", {}))
        props["_grower_slug"] = grower_slug
        props["_farm_slug"] = farm_slug
        props["_farm_name"] = farm_name
        props["_farm_color"] = color
        enriched.append({
            "type": "Feature",
            "geometry": feat.get("geometry"),
            "properties": props,
        })
    return enriched


def _build_combined_geojson(grower_slug: str, farms: list[dict[str, str]]) -> dict:
    """Combine all farm boundary features into one GeoJSON FeatureCollection."""
    all_features: list[dict] = []
    for idx, farm in enumerate(farms):
        features = _load_geojson_features(
            Path(farm["boundary_path"]),
            farm_index=idx,
            farm_slug=farm["farm_slug"],
            farm_name=farm["farm_name"],
            grower_slug=grower_slug,
        )
        all_features.extend(features)
        print(f"  {farm['farm_name']}: {len(features)} fields")
    return {
        "type": "FeatureCollection",
        "features": all_features,
    }


def _compute_bounds(geojson: dict) -> tuple[float, float, float, float]:
    """Compute bounding box from all polygon coordinates."""
    lons: list[float] = []
    lats: list[float] = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        coords = geom.get("coordinates")
        if not coords:
            continue
        rings = coords
        if geom.get("type") == "Polygon":
            rings = [coords]
        elif geom.get("type") == "MultiPolygon":
            rings = coords
        else:
            continue
        for poly in rings:
            for ring in poly:
                for pt in ring:
                    if len(pt) >= 2:
                        lons.append(float(pt[0]))
                        lats.append(float(pt[1]))
    if not lons:
        return (-93.5, 44.5, -93.5, 44.5)
    return (min(lons), min(lats), max(lons), max(lats))


# ---------------------------------------------------------------------------
# NDVI asset discovery and processing
# ---------------------------------------------------------------------------

def _fix_bounds(bounds: list[list[float]]) -> list[list[float]]:
    """Ensure bounds are in Leaflet-format [[south, west], [north, east]]."""
    (ymin, xmin), (ymax, xmax) = bounds
    sw = [min(ymin, ymax), min(xmin, xmax)]
    ne = [max(ymin, ymax), max(xmin, xmax)]
    return [sw, ne]


def _read_geotiff_bounds(tif_path: Path) -> list[list[float]] | None:
    """Extract geographic bounds from a GeoTIFF using TIFF tags."""
    try:
        with open(tif_path, "rb") as f:
            header = f.read(8)
            if header[:2] == b"II":
                endian = "<"
            elif header[:2] == b"MM":
                endian = ">"
            else:
                return None

            f.seek(4 if endian == "<" else 4)
            ifd_offset = struct.unpack(endian + "I", f.read(4))[0]
            f.seek(ifd_offset)
            num_entries = struct.unpack(endian + "H", f.read(2))[0]

            width = height = None
            scale_offset = tiepoint_offset = None

            for _ in range(num_entries):
                tag = struct.unpack(endian + "H", f.read(2))[0]
                type_ = struct.unpack(endian + "H", f.read(2))[0]
                count = struct.unpack(endian + "I", f.read(4))[0]
                value_raw = f.read(4)
                value = struct.unpack(endian + "I", value_raw)[0]

                if tag == 256:
                    width = value
                elif tag == 257:
                    height = value
                elif tag == 33550:
                    scale_offset = value
                elif tag == 33922:
                    tiepoint_offset = value

            if None in (width, height, scale_offset, tiepoint_offset):
                return None

            f.seek(scale_offset)
            scale = struct.unpack(endian + "3d", f.read(24))

            f.seek(tiepoint_offset)
            tp = struct.unpack(endian + "6d", f.read(48))

            xmin = tp[3]
            ymax = tp[4]
            xmax = tp[3] + scale[0] * width
            ymin = tp[4] - scale[1] * height

            return _fix_bounds([[ymin, xmin], [ymax, xmax]])
    except Exception:
        return None


def _ndvi_color_ramp(value: float) -> tuple[int, int, int, int]:
    """Map NDVI value [-1, 1] to RGBA color. Values outside range are transparent."""
    if np.isnan(value) or value < -1.0 or value > 1.0:
        return (0, 0, 0, 0)

    if value < 0.0:
        r, g, b = 139, 69, 19  # Brown
    elif value >= 0.8:
        r, g, b = 0, 100, 0  # Dark green
    elif value >= 0.6:
        r, g, b = 34, 139, 34  # Green
    elif value >= 0.4:
        r, g, b = 144, 238, 144  # Light green
    elif value >= 0.2:
        r, g, b = 255, 255, 0  # Yellow
    else:
        r, g, b = 210, 180, 140  # Tan

    return (r, g, b, 255)


def _read_tiff_pixels(tif_path: Path) -> tuple[np.ndarray, list[list[float]]] | None:
    """Read GeoTIFF pixel data and return (ndvi_array_2d, bounds)."""
    bounds = _read_geotiff_bounds(tif_path)
    if not bounds:
        return None

    try:
        img = Image.open(tif_path)
        arr = np.array(img, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
    except Exception:
        return None

    return arr, bounds


def _ndvi_array_to_png_base64(arr: np.ndarray) -> str | None:
    """Convert an NDVI float array to a base64-encoded RGBA PNG."""
    h, w = arr.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    for y in range(h):
        for x in range(w):
            rgba[y, x] = _ndvi_color_ramp(float(arr[y, x]))

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _read_png_pixels(png_path: Path) -> np.ndarray | None:
    """Read an existing PNG preview and return as float32 grayscale array (0-1)."""
    try:
        img = Image.open(png_path).convert("L")
        arr = np.array(img, dtype=np.float32) / 255.0
        return arr
    except Exception:
        return None


def _process_ndvi_asset(
    asset_path: Path, asset_type: str,
    fallback_bounds: list[list[float]] | None,
) -> dict | None:
    """Process an NDVI asset (TIFF or PNG) and return base64 + bounds for embedding."""
    bounds = None
    arr = None

    if asset_type == "tif":
        result = _read_tiff_pixels(asset_path)
        if result:
            arr, bounds = result
        else:
            # Fallback: read as plain image, use field boundary bounds
            png_path = asset_path.with_suffix(".png")
            if png_path.exists():
                arr = _read_png_pixels(png_path)
                bounds = fallback_bounds
    elif asset_type == "png":
        arr = _read_png_pixels(asset_path)
        # Try to get bounds from matching TIFF
        tif_path = asset_path.with_suffix(".tif")
        if tif_path.exists():
            result = _read_tiff_pixels(tif_path)
            if result:
                _, bounds = result
        if not bounds:
            bounds = fallback_bounds

    if arr is None or bounds is None:
        return None

    b64 = _ndvi_array_to_png_base64(arr)
    if b64 is None:
        return None

    return {
        "image": f"data:image/png;base64,{b64}",
        "bounds": bounds,
    }


def _discover_ndvi_assets(
    grower_slug: str, farms: list[dict[str, str]], geojson: dict
) -> dict[str, dict[str, dict]]:
    """Discover NDVI assets for all fields and process them.

    Returns:
        {field_id: {year_str: {image: "data:...", bounds: [[lat,lng],[lat,lng]]}}}
    """
    # Build field_id -> feature mapping for fallback bounds
    feat_map: dict[str, dict] = {}
    for feat in geojson.get("features", []):
        p = feat.get("properties", {})
        fid = p.get("field_id")
        if fid:
            feat_map[fid] = feat

    ndvi_data: dict[str, dict[str, dict]] = {}

    for farm in farms:
        farm_dir_path = grower_dir(grower_slug) / "farms" / farm["farm_slug"]
        fields_path = farm_dir_path / "fields"
        if not fields_path.exists():
            continue

        for field_dir in sorted(fields_path.iterdir()):
            if not field_dir.is_dir():
                continue
            field_id = field_dir.name
            features_dir = field_dir / "derived" / "features"
            if not features_dir.exists():
                continue

            years: dict[str, dict] = {}

            # Look for year composite TIFFs
            for tif_path in sorted(features_dir.glob("ndvi_year_*_composite.tif")):
                year = tif_path.stem.replace("ndvi_year_", "").replace("_composite", "")
                if not year.isdigit():
                    continue

                # Get fallback bounds from field boundary
                fallback = feat_map.get(field_id, {}).get("geometry")
                fb_bounds = None
                if fallback:
                    fb_bounds = _compute_bounds({"features": [feat_map[field_id]]}) if field_id in feat_map else None
                    if fb_bounds:
                        fb_bounds = _fix_bounds([[fb_bounds[1], fb_bounds[0]], [fb_bounds[3], fb_bounds[2]]])

                processed = _process_ndvi_asset(tif_path, "tif", fb_bounds)
                if processed:
                    years[year] = processed
                    continue

                # Try PNG fallback
                png_path = tif_path.with_suffix(".png")
                if png_path.exists():
                    processed = _process_ndvi_asset(png_path, "png", fb_bounds)
                    if processed:
                        years[year] = processed

            if years:
                ndvi_data[field_id] = years
                print(f"    NDVI layers for {field_id}: {', '.join(sorted(years.keys()))}")

    return ndvi_data


def _build_ndvi_data_js(ndvi_data: dict[str, dict[str, dict]]) -> str:
    """Build a JavaScript object string from NDVI data."""
    if not ndvi_data:
        return "{}"

    parts: list[str] = []
    for field_id in sorted(ndvi_data.keys()):
        year_parts: list[str] = []
        for year in sorted(ndvi_data[field_id].keys()):
            info = ndvi_data[field_id][year]
            img = info["image"]
            bounds = info["bounds"]
            year_parts.append(
                f'    "{year}": {{'
                f'image: "{img}", '
                f'bounds: [[{bounds[0][0]},{bounds[0][1]}],[{bounds[1][0]},{bounds[1][1]}]]'
                f"}}"
            )
        parts.append(f'  "{field_id}": {{\n' + ",\n".join(year_parts) + "\n  }")

    return "{\n" + ",\n".join(parts) + "\n}"


# ---------------------------------------------------------------------------
# SSURGO asset discovery and enrichment
# ---------------------------------------------------------------------------

def _discover_ssurgo(grower_slug: str) -> dict[str, dict]:
    """Discover SSURGO GeoJSON files for all fields and enrich with CSV properties.

    Returns:
        {field_id: {geojson: FeatureCollection, colorMap: {component: "#hex"}}}
    """
    ssurgo_data: dict[str, dict] = {}
    grower_path = grower_dir(grower_slug)
    farms_dir = grower_path / "farms"
    if not farms_dir.exists():
        return ssurgo_data

    for farm_dir in sorted(farms_dir.iterdir()):
        if not farm_dir.is_dir():
            continue
        fields_path = farm_dir / "fields"
        if not fields_path.exists():
            continue

        for field_dir in sorted(fields_path.iterdir()):
            if not field_dir.is_dir():
                continue
            field_id = field_dir.name

            geojson_path = field_dir / "soil" / "ssurgo_soil_types.geojson"
            csv_path = field_dir / "soil" / "ssurgo_full.csv"

            if not geojson_path.exists():
                continue

            try:
                geo_data = json.loads(geojson_path.read_text(encoding="utf-8"))
            except Exception:
                print(f"    WARN: could not read SSURGO GeoJSON for {field_id}")
                continue

            # Read CSV and build a lookup: mukey -> best row
            csv_lookup: dict[str, dict] = {}
            if csv_path.exists():
                try:
                    with open(csv_path, encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            mukey = row.get("mukey", "")
                            if mukey and (mukey not in csv_lookup):
                                csv_lookup[mukey] = row
                except Exception:
                    print(f"    WARN: could not read SSURGO CSV for {field_id}")

            # Enrich GeoJSON features with CSV properties
            component_colors: dict[str, str] = {}
            for feat in geo_data.get("features", []):
                props = feat.get("properties", {})
                if not isinstance(props, dict):
                    continue
                mukey = props.get("mukey", "")
                csv_row = csv_lookup.get(mukey, {})
                compname = csv_row.get("compname", "Unknown")
                props["_component"] = compname
                props["_drainage"] = csv_row.get("drainagecl", "N/A")
                props["_om"] = csv_row.get("om_r", "N/A")
                props["_ph"] = csv_row.get("ph1to1h2o_r", "N/A")
                props["_clay"] = csv_row.get("claytotal_r", "N/A")
                props["_sand"] = csv_row.get("sandtotal_r", "N/A")
                props["_silt"] = csv_row.get("silttotal_r", "N/A")
                props["_awc"] = csv_row.get("awc_r", "N/A")
                props["_cec"] = csv_row.get("cec7_r", "N/A")
                props["_comppct"] = csv_row.get("comppct_r", "N/A")

                # Assign color by component name
                if compname not in component_colors:
                    color_idx = len(component_colors) % len(SOIL_COLORS)
                    component_colors[compname] = SOIL_COLORS[color_idx]

                props["_color"] = component_colors[compname]

            if geo_data.get("features"):
                ssurgo_data[field_id] = {
                    "geojson": geo_data,
                    "colorMap": component_colors,
                }
                comps = ", ".join(component_colors.keys())
                print(f"    SSURGO layers for {field_id}: {comps}")

    return ssurgo_data


def _build_ssurgo_data_js(ssurgo_data: dict[str, dict]) -> str:
    """Build a JavaScript object string from SSURGO data."""
    if not ssurgo_data:
        return "{}"

    parts: list[str] = []
    for field_id in sorted(ssurgo_data.keys()):
        info = ssurgo_data[field_id]
        geojson_str = json.dumps(info["geojson"], separators=(",", ":"))
        color_map_pairs = [
            f'"{comp}": "{color}"' for comp, color in info["colorMap"].items()
        ]
        color_map_str = "{" + ",".join(color_map_pairs) + "}"
        parts.append(
            f'  "{field_id}": {{'
            f'geojson: {geojson_str}, '
            f"colorMap: {color_map_str}"
            f"}}"
        )

    return "{\n" + ",\n".join(parts) + "\n}"


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _build_html(
    grower_slug: str,
    geojson: dict,
    farm_list: list[dict[str, str]],
    ndvi_data_js: str = "{}",
    ssurgo_data_js: str = "{}",
) -> str:
    """Build a self-contained Leaflet HTML string with multi-select fields and NDVI overlays."""
    bounds = _compute_bounds(geojson)
    geojson_str = json.dumps(geojson, separators=(",", ":"))

    # Build a farm color legend HTML
    legend_items = []
    for idx, farm in enumerate(farm_list):
        color = FARM_COLORS[idx % len(FARM_COLORS)]
        legend_items.append(
            f"""<div class="legend-item"><div class="legend-color" style="background:{color}"></div><span>{farm['farm_name']}</span></div>"""
        )
    legend_html = "\n".join(legend_items)

    # Build farm JS array
    field_list_js = []
    for idx, farm in enumerate(farm_list):
        color = FARM_COLORS[idx % len(FARM_COLORS)]
        field_list_js.append(
            f"""{{name: "{farm['farm_name']}", color: "{color}", slug: "{farm['farm_slug']}"}}"""
        )
    farms_js = "[\n" + ",\n".join(field_list_js) + "\n]"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grower Map — {grower_slug}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; overflow: hidden; }}
        #container {{ display: flex; height: 100vh; width: 100vw; }}
        #sidebar {{
            width: 320px; background: #fff; border-right: 1px solid #ddd;
            padding: 16px; overflow-y: auto; box-sizing: border-box;
            display: flex; flex-direction: column;
        }}
        #sidebar h1 {{ font-size: 1.2em; margin: 0 0 4px; color: #1B5E20; }}
        #sidebar h2 {{ font-size: 0.85em; margin: 0 0 12px; color: #666; font-weight: normal; }}
        .panel {{ margin: 8px 0; padding: 10px; background: #f8f9fa; border-radius: 6px; flex-shrink: 0; }}
        .panel h3 {{ font-size: 0.75em; margin: 0 0 8px; color: #333; text-transform: uppercase; letter-spacing: 0.5px; }}
        .legend-item {{ display: flex; align-items: center; margin: 4px 0; font-size: 0.8em; }}
        .legend-color {{ width: 14px; height: 14px; border-radius: 3px; margin-right: 6px; border: 1px solid rgba(0,0,0,0.1); }}
        .field-btn {{
            display: block; width: 100%; padding: 6px; margin: 3px 0;
            background: #fff; border: 1px solid #ddd; border-radius: 4px;
            cursor: pointer; font-size: 0.8em; text-align: left;
            transition: background 0.15s;
        }}
        .field-btn:hover {{ background: #e8f5e9; }}
        .field-btn.active {{ background: #c8e6c9; border-color: #2E7D32; }}
        #field-list-container {{ flex: 1; overflow-y: auto; min-height: 100px; }}
        .field-block {{
            margin-bottom: 6px; padding: 6px 0;
            border-bottom: 1px solid #eee;
        }}
        .field-item {{
            display: flex; align-items: center; padding: 3px 0;
            font-size: 0.8em; cursor: pointer;
        }}
        .field-item:hover {{ background: #f0f0f0; }}
        .field-item input {{ margin-right: 6px; cursor: pointer; }}
        .field-item .dot {{ font-size: 0.6em; margin-right: 4px; }}
        .field-item .area {{ color: #888; margin-left: auto; font-size: 0.9em; }}
        .field-item.selected {{ background: #fffde7; }}
        .ndvi-row {{
            display: flex; align-items: center; flex-wrap: wrap;
            margin-top: 3px; padding-left: 20px; font-size: 0.78em;
        }}
        .ndvi-row label {{ color: #666; font-weight: 500; margin-right: 4px; font-size: 0.85em; }}
        .ndvi-year-btn {{
            display: inline-block; padding: 1px 5px; margin: 0 2px 2px 0;
            border: 1px solid #ccc; border-radius: 3px;
            cursor: pointer; font-size: 0.85em; background: #fff;
            transition: all 0.12s; line-height: 1.4;
        }}
        .ndvi-year-btn:hover {{ background: #e8f5e9; border-color: #2E7D32; }}
        .ndvi-year-btn.active {{ background: #2E7D32; color: #fff; border-color: #1B5E20; }}
        .ndvi-year-btn.none-btn {{ color: #888; }}
        .ndvi-year-btn.none-btn.active {{ background: #666; color: #fff; border-color: #555; }}
        .opacity-row {{
            display: flex; align-items: center; flex-wrap: wrap;
            margin-top: 2px; padding-left: 20px; font-size: 0.75em;
        }}
        .opacity-row label {{ color: #666; margin-right: 4px; width: 46px; font-size: 0.85em; }}
        .opacity-row input[type="range"] {{
            flex: 1; min-width: 60px; height: 4px;
            cursor: pointer; accent-color: #2E7D32;
        }}
        .opacity-row .opacity-val {{
            margin-left: 4px; color: #666; width: 28px; text-align: right; font-size: 0.9em;
        }}
        .ssurgo-row {{
            display: flex; align-items: center; flex-wrap: wrap;
            margin-top: 3px; padding-left: 20px; font-size: 0.78em;
        }}
        .ssurgo-row label {{ color: #666; font-weight: 500; margin-right: 4px; font-size: 0.85em; }}
        .ssurgo-btn {{
            display: inline-block; padding: 1px 5px; margin: 0 2px 2px 0;
            border: 1px solid #ccc; border-radius: 3px;
            cursor: pointer; font-size: 0.85em; background: #fff;
            transition: all 0.12s; line-height: 1.4;
        }}
        .ssurgo-btn:hover {{ background: #e8f5e9; border-color: #1565C0; }}
        .ssurgo-btn.active {{ background: #1565C0; color: #fff; border-color: #0D47A1; }}
        .ssurgo-btn.off-btn {{ color: #888; }}
        .ssurgo-btn.off-btn.active {{ background: #666; color: #fff; border-color: #555; }}
        #selection-panel {{ background: #e8f5e9; border-left: 3px solid #2E7D32; }}
        #sel-details {{ font-size: 0.8em; line-height: 1.5; margin: 6px 0; }}
        #map {{ flex: 1; min-width: 0; }}
        .leaflet-popup-content {{ font-size: 0.9em; line-height: 1.5; }}
        .leaflet-popup-content b {{ color: #1B5E20; }}
    </style>
</head>
<body>
    <div id="container">
        <div id="sidebar">
            <h1>🌾 {grower_slug.replace('-', ' ').title()}</h1>
            <h2>Grower Interactive Map</h2>
            <div class="panel">
                <h3>Farms</h3>
                {legend_html}
            </div>
            <div class="panel">
                <h3>Zoom to Farm</h3>
                <div id="farm-buttons"></div>
            </div>
            <div class="panel" id="selection-panel">
                <h3>Selection (<span id="sel-count">0</span>)</h3>
                <div id="sel-details">No fields selected</div>
                <button class="field-btn" onclick="clearSelection()">Clear All</button>
            </div>
            <div class="panel" id="field-list-container">
                <h3>Fields</h3>
                <div id="field-list"></div>
            </div>
        </div>
        <div id="map"></div>
    </div>
    <script>
        var geojsonData = {geojson_str};
        var farmList = {farms_js};

        var bounds = [[{bounds[1]}, {bounds[0]}], [{bounds[3]}, {bounds[2]}]];
        var map = L.map('map').fitBounds(bounds);

        var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            attribution: 'Esri',
            maxZoom: 19
        }});

        var labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            attribution: 'Esri',
            maxZoom: 19
        }});

        var osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19
        }});

        satellite.addTo(map);
        labels.addTo(map);

        L.control.layers({{"Satellite": satellite, "OpenStreetMap": osm}}, {{"Labels": labels}}).addTo(map);

        // Multi-select state
        var selectedFields = new Set();
        var fieldLayers = {{}};

        // NDVI overlay state
        var ndviData = {ndvi_data_js};
        var ndviOverlays = {{}};

        // SSURGO overlay state
        var ssurgoData = {ssurgo_data_js};
        var ssurgoLayers = {{}};

        function defaultStyle(feature) {{
            return {{
                color: feature.properties._farm_color,
                weight: 2,
                fillOpacity: 0.3,
                fillColor: feature.properties._farm_color
            }};
        }}

        function selectedStyle(feature) {{
            return {{
                color: '#FFD700',
                weight: 4,
                fillOpacity: 0.5,
                fillColor: feature.properties._farm_color
            }};
        }}

        function updateFieldVisuals(fieldId) {{
            var layer = fieldLayers[fieldId];
            if (!layer) return;
            var feat = layer.feature;
            if (selectedFields.has(fieldId)) {{
                layer.setStyle(selectedStyle(feat));
            }} else {{
                layer.setStyle(defaultStyle(feat));
            }}
        }}

        function updateSelectionPanel() {{
            var count = selectedFields.size;
            document.getElementById('sel-count').textContent = count;
            if (count === 0) {{
                document.getElementById('sel-details').innerHTML = 'No fields selected';
                return;
            }}
            var totalAcres = 0;
            selectedFields.forEach(function(id) {{
                var feat = geojsonData.features.find(function(f) {{
                    return f.properties.field_id === id;
                }});
                if (feat) totalAcres += (feat.properties.area_acres || 0);
            }});
            document.getElementById('sel-details').innerHTML =
                '<b>Fields:</b> ' + count + '<br>' +
                '<b>Total Acres:</b> ' + totalAcres.toFixed(1) + '<br>' +
                '<b>Avg Acres:</b> ' + (totalAcres / count).toFixed(1);
        }}

        function zoomToSelectedFields() {{
            if (selectedFields.size === 0) {{
                map.fitBounds(bounds, {{animate: false}});
                return;
            }}
            var minLat = Infinity, maxLat = -Infinity;
            var minLng = Infinity, maxLng = -Infinity;
            selectedFields.forEach(function(id) {{
                var feat = geojsonData.features.find(function(f) {{
                    return f.properties.field_id === id;
                }});
                if (!feat) return;
                var geom = feat.geometry;
                if (!geom) return;
                var coords = geom.coordinates;
                if (!coords) return;
                var rings = coords;
                if (geom.type === 'Polygon') rings = [coords];
                else if (geom.type === 'MultiPolygon') rings = coords;
                else return;
                for (var p = 0; p < rings.length; p++) {{
                    for (var r = 0; r < rings[p].length; r++) {{
                        for (var pt = 0; pt < rings[p][r].length; pt++) {{
                            var c = rings[p][r][pt];
                            if (c.length >= 2) {{
                                if (c[0] < minLng) minLng = c[0];
                                if (c[0] > maxLng) maxLng = c[0];
                                if (c[1] < minLat) minLat = c[1];
                                if (c[1] > maxLat) maxLat = c[1];
                            }}
                        }}
                    }}
                }}
            }});
            if (!isFinite(minLat)) return;
            var latSpan = maxLat - minLat;
            var lngSpan = maxLng - minLng;
            var pad = 0.15;
            var zoomBounds = [
                [minLat - latSpan * pad / 2, minLng - lngSpan * pad / 2],
                [maxLat + latSpan * pad / 2, maxLng + lngSpan * pad / 2]
            ];
            map.fitBounds(zoomBounds, {{maxZoom: 16, animate: false}});
        }}

        function toggleFieldSelection(fieldId) {{
            if (selectedFields.has(fieldId)) {{
                selectedFields.delete(fieldId);
            }} else {{
                selectedFields.add(fieldId);
            }}
            updateFieldVisuals(fieldId);
            updateSelectionPanel();
            zoomToSelectedFields();
            var chk = document.getElementById('chk-' + fieldId);
            if (chk) {{
                chk.checked = selectedFields.has(fieldId);
                var row = chk.closest('.field-item');
                if (row) {{
                    row.classList.toggle('selected', selectedFields.has(fieldId));
                }}
            }}
        }}

        function clearSelection() {{
            var ids = Array.from(selectedFields);
            selectedFields.clear();
            ids.forEach(function(id) {{
                updateFieldVisuals(id);
                var chk = document.getElementById('chk-' + id);
                if (chk) {{
                    chk.checked = false;
                    var row = chk.closest('.field-item');
                    if (row) row.classList.remove('selected');
                }}
            }});
            updateSelectionPanel();
            zoomToSelectedFields();
        }}

        // NDVI overlay functions
        function setNdviYear(fieldId, year) {{
            if (ndviOverlays[fieldId]) {{
                map.removeLayer(ndviOverlays[fieldId]);
                delete ndviOverlays[fieldId];
            }}

            // Update NDVI year buttons
            var ndviContainer = document.getElementById('ndvi-' + fieldId);
            if (ndviContainer) {{
                ndviContainer.querySelectorAll('.ndvi-year-btn').forEach(function(b) {{
                    b.classList.toggle('active', b.dataset.year === year);
                }});
            }}

            // Enable/disable opacity slider
            var opacityRow = document.getElementById('opacity-' + fieldId);
            if (opacityRow) {{
                var slider = opacityRow.querySelector('input');
                var valSpan = opacityRow.querySelector('.opacity-val');
                if (year === 'none') {{
                    slider.disabled = true;
                    valSpan.textContent = '0%';
                }} else {{
                    slider.disabled = false;
                    valSpan.textContent = slider.value + '%';
                }}
            }}

            if (year === 'none') return;

            var info = ndviData[fieldId] && ndviData[fieldId][year];
            if (!info) return;

            var opacityVal = 0.75;
            var opacityRowEl = document.getElementById('opacity-' + fieldId);
            if (opacityRowEl) {{
                var slider = opacityRowEl.querySelector('input');
                opacityVal = parseInt(slider.value) / 100;
            }}

            ndviOverlays[fieldId] = L.imageOverlay(info.image, info.bounds, {{
                opacity: opacityVal,
                interactive: false
            }}).addTo(map);

            // Keep SSURGO above NDVI if active
            if (ssurgoLayers[fieldId]) {{
                ssurgoLayers[fieldId].bringToFront();
            }}
            // Keep polygon on top of everything
            var polyLayer = fieldLayers[fieldId];
            if (polyLayer) polyLayer.bringToFront();
        }}

        function updateNdviOpacity(fieldId) {{
            var overlay = ndviOverlays[fieldId];
            if (!overlay) return;

            var opacityRow = document.getElementById('opacity-' + fieldId);
            if (!opacityRow) return;

            var slider = opacityRow.querySelector('input');
            var valSpan = opacityRow.querySelector('.opacity-val');
            var val = parseInt(slider.value);
            valSpan.textContent = val + '%';
            overlay.setOpacity(val / 100);
        }}

        // SSURGO overlay functions
        function ssurgoStyle(feature) {{
            var color = feature.properties._color || '#999';
            return {{
                fillColor: color,
                weight: 1,
                opacity: 0.5,
                color: '#333',
                fillOpacity: 0.6
            }};
        }}

        function ssurgoPopup(feature, layer) {{
            var p = feature.properties;
            var html = '';
            if (p._component) html += '<b>Component:</b> ' + p._component + '<br>';
            if (p._comppct) html += '<b>Composition:</b> ' + p._comppct + '%<br>';
            if (p._drainage) html += '<b>Drainage:</b> ' + p._drainage + '<br>';
            var props = [];
            if (p._ph && p._ph !== 'N/A') props.push('pH: ' + p._ph);
            if (p._om && p._om !== 'N/A') props.push('OM: ' + p._om + '%');
            if (p._clay && p._clay !== 'N/A') props.push('Clay: ' + p._clay + '%');
            if (p._sand && p._sand !== 'N/A') props.push('Sand: ' + p._sand + '%');
            if (p._awc && p._awc !== 'N/A') props.push('AWC: ' + p._awc + '"');
            if (p._cec && p._cec !== 'N/A') props.push('CEC: ' + p._cec);
            if (props.length) html += '<b>Properties:</b> ' + props.join(', ') + '<br>';
            var mukey = feature.properties.mukey;
            if (mukey) html += '<b>Map Unit:</b> ' + mukey;
            layer.bindPopup(html);
        }}

        function toggleSsurgo(fieldId, enabled) {{
            // Remove existing SSURGO layer
            if (ssurgoLayers[fieldId]) {{
                map.removeLayer(ssurgoLayers[fieldId]);
                delete ssurgoLayers[fieldId];
            }}

            // Update button UI
            var ssurgoRow = document.getElementById('ssurgo-' + fieldId);
            if (ssurgoRow) {{
                ssurgoRow.querySelectorAll('.ssurgo-btn').forEach(function(b) {{
                    b.classList.toggle('active', b.dataset.state === enabled);
                }});
            }}

            // Enable/disable opacity slider
            var ssurgoOpacityRow = document.getElementById('ssurgo-opacity-' + fieldId);
            if (ssurgoOpacityRow) {{
                var slider = ssurgoOpacityRow.querySelector('input');
                var valSpan = ssurgoOpacityRow.querySelector('.opacity-val');
                if (!enabled) {{
                    slider.disabled = true;
                    valSpan.textContent = '0%';
                }} else {{
                    slider.disabled = false;
                    valSpan.textContent = slider.value + '%';
                }}
            }}

            if (!enabled) return;

            var info = ssurgoData[fieldId];
            if (!info) return;

            var opacityVal = 0.6;
            if (ssurgoOpacityRow) {{
                var slider = ssurgoOpacityRow.querySelector('input');
                opacityVal = parseInt(slider.value) / 100;
            }}

            ssurgoLayers[fieldId] = L.geoJSON(info.geojson, {{
                style: ssurgoStyle,
                onEachFeature: function(feature, layer) {{
                    ssurgoPopup(feature, layer);
                }}
            }}).addTo(map);

            ssurgoLayers[fieldId].setStyle(function(f) {{
                var s = ssurgoStyle(f);
                s.fillOpacity = opacityVal;
                return s;
            }});

            // Ensure SSURGO is above NDVI but below field polygons
            var polyLayer = fieldLayers[fieldId];
            if (polyLayer) polyLayer.bringToFront();
        }}

        function updateSsurgoOpacity(fieldId) {{
            var layer = ssurgoLayers[fieldId];
            if (!layer) return;

            var opacityRow = document.getElementById('ssurgo-opacity-' + fieldId);
            if (!opacityRow) return;

            var slider = opacityRow.querySelector('input');
            var valSpan = opacityRow.querySelector('.opacity-val');
            var val = parseInt(slider.value);
            valSpan.textContent = val + '%';

            layer.setStyle(function(f) {{
                var s = ssurgoStyle(f);
                s.fillOpacity = val / 100;
                return s;
            }});
        }}

        // Build field list with checkboxes and NDVI controls
        function buildFieldList() {{
            var container = document.getElementById('field-list');
            container.innerHTML = '';
            geojsonData.features.forEach(function(f) {{
                var p = f.properties;
                var block = document.createElement('div');
                block.className = 'field-block';

                // Field row
                var div = document.createElement('div');
                div.className = 'field-item';
                div.id = 'row-' + p.field_id;
                div.innerHTML =
                    '<input type="checkbox" id="chk-' + p.field_id + '" onchange="toggleFieldSelection(\\'' + p.field_id + '\\')">' +
                    '<span class="dot" style="color:' + p._farm_color + '">●</span>' +
                    '<span>' + p.field_id + '</span>' +
                    '<span class="area">' + (p.area_acres ? p.area_acres.toFixed(1) + ' ac' : '') + '</span>';
                div.onclick = function(e) {{
                    if (e.target.tagName !== 'INPUT') {{
                        toggleFieldSelection(p.field_id);
                    }}
                }};
                block.appendChild(div);

                // NDVI controls if data exists
                var fieldNdvi = ndviData[p.field_id];
                if (fieldNdvi) {{
                    var ndviRow = document.createElement('div');
                    ndviRow.className = 'ndvi-row';
                    ndviRow.id = 'ndvi-' + p.field_id;
                    ndviRow.innerHTML = '<label>NDVI:</label>';

                    var noneBtn = document.createElement('span');
                    noneBtn.className = 'ndvi-year-btn none-btn active';
                    noneBtn.dataset.year = 'none';
                    noneBtn.textContent = 'None';
                    noneBtn.onclick = function() {{ setNdviYear(p.field_id, 'none'); }};
                    ndviRow.appendChild(noneBtn);

                    var years = Object.keys(fieldNdvi).sort();
                    years.forEach(function(year) {{
                        var btn = document.createElement('span');
                        btn.className = 'ndvi-year-btn';
                        btn.dataset.year = year;
                        btn.textContent = year;
                        btn.onclick = function() {{ setNdviYear(p.field_id, year); }};
                        ndviRow.appendChild(btn);
                    }});
                    block.appendChild(ndviRow);

                    var opacityRow = document.createElement('div');
                    opacityRow.className = 'opacity-row';
                    opacityRow.id = 'opacity-' + p.field_id;
                    opacityRow.innerHTML =
                        '<label>Opacity:</label>' +
                        '<input type="range" min="10" max="100" value="75" oninput="updateNdviOpacity(\\'' + p.field_id + '\\')" disabled>' +
                        '<span class="opacity-val">0%</span>';
                    block.appendChild(opacityRow);
                }}

                // SSURGO controls if data exists
                var fieldSsurgo = ssurgoData[p.field_id];
                if (fieldSsurgo) {{
                    var ssurgoRow = document.createElement('div');
                    ssurgoRow.className = 'ssurgo-row';
                    ssurgoRow.id = 'ssurgo-' + p.field_id;
                    ssurgoRow.innerHTML = '<label>Soil:</label>';

                    var offBtn = document.createElement('span');
                    offBtn.className = 'ssurgo-btn off-btn active';
                    offBtn.dataset.state = 'false';
                    offBtn.textContent = 'Off';
                    offBtn.onclick = function() {{ toggleSsurgo(p.field_id, false); }};
                    ssurgoRow.appendChild(offBtn);

                    var onBtn = document.createElement('span');
                    onBtn.className = 'ssurgo-btn';
                    onBtn.dataset.state = 'true';
                    onBtn.textContent = 'On';
                    onBtn.onclick = function() {{ toggleSsurgo(p.field_id, true); }};
                    ssurgoRow.appendChild(onBtn);

                    block.appendChild(ssurgoRow);

                    var ssurgoOpacityRow = document.createElement('div');
                    ssurgoOpacityRow.className = 'opacity-row';
                    ssurgoOpacityRow.id = 'ssurgo-opacity-' + p.field_id;
                    ssurgoOpacityRow.innerHTML =
                        '<label>Opacity:</label>' +
                        '<input type="range" min="10" max="100" value="60" oninput="updateSsurgoOpacity(\\'' + p.field_id + '\\')" disabled>' +
                        '<span class="opacity-val">0%</span>';
                    block.appendChild(ssurgoOpacityRow);
                }}

                container.appendChild(block);
            }});
        }}
        buildFieldList();

        // Track layers per farm for zoom-to behavior
        var farmLayers = {{}};
        farmList.forEach(function(f) {{ farmLayers[f.slug] = L.featureGroup(); }});

        var geoLayer = L.geoJSON(geojsonData, {{
            style: function(feature) {{
                return defaultStyle(feature);
            }},
            onEachFeature: function(feature, layer) {{
                var p = feature.properties;
                fieldLayers[p.field_id] = layer;

                layer.on('click', function(e) {{
                    L.DomEvent.stopPropagation(e);
                    toggleFieldSelection(p.field_id);
                }});

                var popup = '<b>Grower:</b> ' + (p._grower_slug || '') + '<br>' +
                            '<b>Farm:</b> ' + (p._farm_name || '') + '<br>' +
                            '<b>Field:</b> ' + (p.field_id || '') + '<br>' +
                            '<b>Area:</b> ' + (p.area_acres ? p.area_acres.toFixed(1) + ' acres' : 'N/A') + '<br>' +
                            '<b>Crop:</b> ' + (p.crop_name || 'N/A');
                layer.bindPopup(popup);

                // Add to farm layer group
                for (var key in farmLayers) {{
                    if (p._farm_slug === key) {{
                        farmLayers[key].addLayer(layer);
                        break;
                    }}
                }}
            }}
        }}).addTo(map);

        // Build zoom-to buttons
        var btnContainer = document.getElementById('farm-buttons');
        farmList.forEach(function(f) {{
            var btn = document.createElement('button');
            btn.className = 'field-btn';
            btn.id = 'btn-' + f.slug;
            btn.innerHTML = '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + f.color + ';margin-right:6px;"></span>' + f.name;
            btn.onclick = function() {{
                var grp = farmLayers[f.slug];
                if (grp && grp.getLayers().length > 0) {{
                    map.fitBounds(grp.getBounds(), {{ padding: [50, 50], maxZoom: 16 }});
                }}
            }};
            btnContainer.appendChild(btn);
        }});
    </script>
</body>
</html>
"""
    return html


def generate_map(grower_slug: str) -> Path | None:
    """Generate the interactive web map for a single grower."""
    print(f"Generating grower web map for: {grower_slug}")

    farms = _discover_farms(grower_slug)
    if not farms:
        print(f"  No farms with boundaries found for grower: {grower_slug}")
        return None

    print(f"  Discovered {len(farms)} farm(s)")

    geojson = _build_combined_geojson(grower_slug, farms)
    if not geojson.get("features"):
        print(f"  No field features found for grower: {grower_slug}")
        return None

    # Discover and process NDVI assets
    print("  Discovering NDVI assets...")
    ndvi_data = _discover_ndvi_assets(grower_slug, farms, geojson)
    ndvi_data_js = _build_ndvi_data_js(ndvi_data)
    total_ndvi = sum(len(years) for years in ndvi_data.values())
    if total_ndvi > 0:
        print(f"  Embedded {total_ndvi} NDVI layer(s) across {len(ndvi_data)} field(s)")

    # Discover and process SSURGO assets
    print("  Discovering SSURGO assets...")
    ssurgo_data = _discover_ssurgo(grower_slug)
    ssurgo_data_js = _build_ssurgo_data_js(ssurgo_data)
    if ssurgo_data:
        total_soil = sum(len(d["colorMap"]) for d in ssurgo_data.values())
        print(f"  Embedded SSURGO layer(s) for {len(ssurgo_data)} field(s) ({total_soil} soil components)")

    html = _build_html(grower_slug, geojson, farms, ndvi_data_js, ssurgo_data_js)

    # Output path: growers/<grower>/derived/reports/grower_web_map.html
    out_dir = grower_dir(grower_slug) / "derived" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grower_web_map.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"  ✓ Saved: {out_path}")
    print(f"    Fields: {len(geojson['features'])}")
    print(f"    Farms: {len(farms)}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate grower-level interactive web maps")
    parser.add_argument("--grower-slug", default=None, help="Specific grower slug")
    parser.add_argument("--all-growers", action="store_true", help="Run for all growers in the runtime")
    args = parser.parse_args()

    if not args.grower_slug and not args.all_growers:
        parser.error("Provide --grower-slug or --all-growers")

    if args.grower_slug:
        generate_map(args.grower_slug)
    elif args.all_growers:
        if not GROWERS_ROOT.exists():
            print(f"No growers directory found: {GROWERS_ROOT}")
            sys.exit(1)
        for grower_dir_path in sorted(GROWERS_ROOT.iterdir()):
            if not grower_dir_path.is_dir():
                continue
            generate_map(grower_dir_path.name)


if __name__ == "__main__":
    main()
