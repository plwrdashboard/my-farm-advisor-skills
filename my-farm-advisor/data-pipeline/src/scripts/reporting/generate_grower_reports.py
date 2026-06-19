#!/usr/bin/env python3
"""generate_grower_reports.py — Generate grower-level aggregated reports.

Produces:
    growers/<grower_slug>/derived/reports/grower_summary.csv
    growers/<grower_slug>/derived/reports/grower_report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))

from paths import GROWERS_ROOT, grower_dir


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_fields(grower_slug: str) -> list[dict]:
    """Discover all fields under a grower and collect their data."""
    farms_dir = grower_dir(grower_slug) / "farms"
    if not farms_dir.exists():
        return []

    fields: list[dict] = []
    for farm_dir in sorted(farms_dir.iterdir()):
        if not farm_dir.is_dir():
            continue
        farm_slug = farm_dir.name

        # Try to read farm display name
        farm_json_path = farm_dir / "farm.json"
        farm_meta = _load_json(farm_json_path)
        farm_name = farm_meta.get("display_name") or farm_meta.get("farm_name") or farm_slug.replace("-", " ").title()

        fields_dir = farm_dir / "fields"
        if not fields_dir.exists():
            continue
        for field_dir in sorted(fields_dir.iterdir()):
            if not field_dir.is_dir():
                continue
            field_slug = field_dir.name

            field_record: dict = {
                "grower_slug": grower_slug,
                "farm_slug": farm_slug,
                "farm_name": farm_name,
                "field_slug": field_slug,
            }

            # Boundary / area
            boundary_path = field_dir / "boundary" / "field_boundary.geojson"
            if boundary_path.exists():
                try:
                    with open(boundary_path) as f:
                        geo = json.load(f)
                    if geo.get("features"):
                        props = geo["features"][0].get("properties", {})
                        field_record["area_acres"] = props.get("area_acres")
                        field_record["crop_name"] = props.get("crop_name", "N/A")
                        field_record["county_name"] = props.get("county_name", "N/A")
                        field_record["state_fips"] = props.get("state_fips", "N/A")
                        field_record["county_fips"] = props.get("county_fips", "N/A")
                except Exception:
                    pass

            # Soil summary
            soil_summary = field_dir / "soil" / "ssurgo_summary.csv"
            if soil_summary.exists():
                try:
                    with open(soil_summary) as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            field_record["n_mukeys"] = row.get("n_mukeys", "N/A")
                            field_record["avg_om_pct"] = row.get("avg_om_pct", "N/A")
                            field_record["avg_ph"] = row.get("avg_ph", "N/A")
                            field_record["total_aws_inches"] = row.get("total_aws_inches", "N/A")
                            field_record["avg_cec"] = row.get("avg_cec", "N/A")
                            field_record["avg_clay_pct"] = row.get("avg_clay_pct", "N/A")
                            field_record["avg_sand_pct"] = row.get("avg_sand_pct", "N/A")
                            field_record["dominant_soil"] = row.get("dominant_soil", "N/A")
                            field_record["drainage_class"] = row.get("drainage_class", "N/A")
                            field_record["erosion_risk"] = row.get("erosion_risk", "N/A")
                            break
                except Exception:
                    pass

            # Weather range
            weather_csv = field_dir / "weather" / "daily_weather.csv"
            if weather_csv.exists():
                try:
                    with open(weather_csv) as f:
                        reader = csv.DictReader(f)
                        dates = [row["date"] for row in reader if "date" in row]
                    if dates:
                        field_record["weather_start"] = min(dates)[:4]
                        field_record["weather_end"] = max(dates)[:4]
                except Exception:
                    pass

            # NDVI summary
            ndvi_summary = field_dir / "derived" / "summaries" / "ndvi_yearly_summary.json"
            if ndvi_summary.exists():
                try:
                    ndvi = _load_json(ndvi_summary)
                    years = ndvi.get("years", [])
                    field_record["ndvi_years"] = len(years)
                    if years:
                        crops = [y.get("crop_name", "Unknown") for y in years]
                        field_record["ndvi_crops"] = ", ".join(dict.fromkeys(crops))  # unique, ordered
                        field_record["ndvi_total_scenes"] = sum(y.get("scene_count", 0) for y in years)
                except Exception:
                    pass

            fields.append(field_record)

    return fields


def _write_csv(grower_slug: str, fields: list[dict]) -> Path:
    out_dir = grower_dir(grower_slug) / "derived" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grower_summary.csv"

    if not fields:
        out_path.write_text("")
        return out_path

    headers = [
        "grower_slug", "farm_slug", "farm_name", "field_slug",
        "area_acres", "crop_name", "county_name", "state_fips", "county_fips",
        "n_mukeys", "avg_om_pct", "avg_ph", "total_aws_inches", "avg_cec",
        "avg_clay_pct", "avg_sand_pct", "dominant_soil", "drainage_class", "erosion_risk",
        "weather_start", "weather_end", "ndvi_years", "ndvi_crops", "ndvi_total_scenes",
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for field in fields:
            writer.writerow({h: field.get(h, "") for h in headers})

    return out_path


def _write_md(grower_slug: str, fields: list[dict]) -> Path:
    out_dir = grower_dir(grower_slug) / "derived" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grower_report.md"

    total_acres = sum(f.get("area_acres", 0) or 0 for f in fields)
    farms = {f["farm_name"] for f in fields}
    counties = {f.get("county_name", "N/A") for f in fields if f.get("county_name")}
    total_fields = len(fields)

    # Aggregate soil
    soil_ph_values = [float(f["avg_ph"]) for f in fields if f.get("avg_ph") and f["avg_ph"] != "N/A"]
    soil_om_values = [float(f["avg_om_pct"]) for f in fields if f.get("avg_om_pct") and f["avg_om_pct"] != "N/A"]
    soil_aws_values = [float(f["total_aws_inches"]) for f in fields if f.get("total_aws_inches") and f["total_aws_inches"] != "N/A"]
    soil_clay_values = [float(f["avg_clay_pct"]) for f in fields if f.get("avg_clay_pct") and f["avg_clay_pct"] != "N/A"]

    lines = [
        f"# Grower Report — {grower_slug}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Overview",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Grower | `{grower_slug}` |",
        f"| Farms | {len(farms)} |",
        f"| Total Fields | {total_fields} |",
        f"| Total Acres | {total_acres:,.1f} |",
        f"| Counties | {', '.join(sorted(counties)) or 'N/A'} |",
        "",
        "## Fields",
        "",
        "| Field | Farm | Acres | Crop | County | Dominant Soil | Drainage | pH | OM % | AWS (in) | Clay % |",
        "|-------|------|-------|------|--------|---------------|----------|----|------|----------|--------|",
    ]

    for f in fields:
        lines.append(
            f"| {f['field_slug']} | {f['farm_name']} | "
            f"{f.get('area_acres', 'N/A')} | {f.get('crop_name', 'N/A')} | "
            f"{f.get('county_name', 'N/A')} | {f.get('dominant_soil', 'N/A')} | "
            f"{f.get('drainage_class', 'N/A')} | {f.get('avg_ph', 'N/A')} | "
            f"{f.get('avg_om_pct', 'N/A')} | {f.get('total_aws_inches', 'N/A')} | "
            f"{f.get('avg_clay_pct', 'N/A')} |"
        )

    lines.extend([
        "",
        "## Soil Summary",
        "",
    ])

    if soil_ph_values:
        lines.append(f"- **Average pH:** {sum(soil_ph_values)/len(soil_ph_values):.2f} (range {min(soil_ph_values):.2f}–{max(soil_ph_values):.2f})")
    if soil_om_values:
        lines.append(f"- **Average Organic Matter:** {sum(soil_om_values)/len(soil_om_values):.1f}% (range {min(soil_om_values):.1f}–{max(soil_om_values):.1f}%)")
    if soil_aws_values:
        lines.append(f"- **Average Available Water Storage:** {sum(soil_aws_values)/len(soil_aws_values):.2f}\" (range {min(soil_aws_values):.2f}\"–{max(soil_aws_values):.2f}\")")
    if soil_clay_values:
        lines.append(f"- **Average Clay Content:** {sum(soil_clay_values)/len(soil_clay_values):.1f}% (range {min(soil_clay_values):.1f}–{max(soil_clay_values):.1f}%)")

    lines.extend([
        "",
        "## Data Coverage",
        "",
    ])

    for f in fields:
        weather_range = ""
        if f.get("weather_start") and f.get("weather_end"):
            weather_range = f"{f['weather_start']}–{f['weather_end']}"
        ndvi_info = ""
        if f.get("ndvi_years"):
            ndvi_info = f"{f['ndvi_years']} years"
            if f.get("ndvi_crops"):
                ndvi_info += f" ({f['ndvi_crops']})"
            if f.get("ndvi_total_scenes"):
                ndvi_info += f", {f['ndvi_total_scenes']} scenes"
        lines.append(f"- **{f['field_slug']}**: Weather {weather_range or 'N/A'} | NDVI {ndvi_info or 'N/A'}")

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def generate_reports(grower_slug: str) -> list[Path]:
    print(f"Generating grower reports for: {grower_slug}")
    fields = _discover_fields(grower_slug)
    if not fields:
        print(f"  No fields found for grower: {grower_slug}")
        return []

    print(f"  Discovered {len(fields)} field(s)")

    csv_path = _write_csv(grower_slug, fields)
    md_path = _write_md(grower_slug, fields)

    print(f"  ✓ CSV: {csv_path}")
    print(f"  ✓ MD:  {md_path}")
    return [csv_path, md_path]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate grower-level aggregated reports")
    parser.add_argument("--grower-slug", default=None, help="Specific grower slug")
    parser.add_argument("--all-growers", action="store_true", help="Run for all growers in the runtime")
    args = parser.parse_args()

    if not args.grower_slug and not args.all_growers:
        parser.error("Provide --grower-slug or --all-growers")

    if args.grower_slug:
        generate_reports(args.grower_slug)
    elif args.all_growers:
        if not GROWERS_ROOT.exists():
            print(f"No growers directory found: {GROWERS_ROOT}")
            sys.exit(1)
        for grower_path in sorted(GROWERS_ROOT.iterdir()):
            if not grower_path.is_dir():
                continue
            generate_reports(grower_path.name)


if __name__ == "__main__":
    main()
