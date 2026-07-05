---
name: field-season-weather-evaluation
description: Generate a single-year field season dashboard combining CDL crop classification, Sentinel-2 NDVI time series, and NASA POWER daily weather metrics (GDD, precipitation, heat/cold stress, heavy rain, dry spells) into a unified multi-panel report. Use when the user needs a holistic season assessment for one field and one year.
version: 1.0.0
author: Boreal Bytes
tags: [weather, ndvi, cdl, gdd, precipitation, stress, dashboard, field-evaluation]
---

# Field Season Weather Evaluation

_Dashboard combining CDL, NDVI, and daily weather for a single field and year._

---

## What this skill covers

This workflow produces a vertical-stack dashboard with a shared DOY axis across all time-series panels:

| Panel | Source | Content |
|-------|--------|---------|
| **Header** | — | Field ID, year, CDL crop percentages |
| **NDVI Season Curve** | Sentinel-2 NDVI scenes | Mean NDVI per scene across the season, ±1 std envelope, peak annotation |
| **GDD Accumulation** | NASA POWER daily weather | Cumulative Growing Degree Days (base 10°C, cap 30°C) |
| **Daily Precipitation** | NASA POWER daily weather | Bar chart with heavy rain threshold (>25 mm) highlighted |
| **Season Stress Summary** | Derived from weather | Heat days, cold nights, heavy rain days, dry spells (≥5 d <1 mm) |

## Prerequisites

The field must have pipeline-generated data under the runtime data root:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/<field>/
├── boundary/field_boundary.geojson
├── weather/daily_weather.csv
├── satellite/sentinel/<year>/
│   ├── sentinel_<date>_ndvi.tif          (one per scene)
│   └── ...
└── ../derived/tables/<farm>_<year>_cdl.csv   (farm-level CDL table)
```

Python dependencies: `rasterio`, `geopandas`, `matplotlib`, `pandas`, `numpy`.

## Quick Start

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime

python weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --grower minnesota-north \
  --farm minnesota-north-minnesota \
  --field osm-1491018233 \
  --year 2022
```

## Example Outputs

After running the dashboard, generated artifacts are written to the field's derived directories:

- `derived/reports/field_season_dashboard_<year>.png` — multi-panel dashboard figure
- `derived/summaries/field_season_summary_<year>.json` — machine-readable metrics

## Reusability

The script accepts `--grower`, `--farm`, `--field`, and `--year` arguments. Any field with the same canonical data structure can be evaluated — just change the parameters.

## Common Tasks

### Run for a different year

```bash
python weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --grower minnesota-north \
  --farm minnesota-north-minnesota \
  --field osm-1491018233 \
  --year 2021
```

### Run for a different farm

```bash
python weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --grower <other-grower> \
  --farm <other-farm> \
  --field <other-field> \
  --year 2024
```

## Understanding the Dashboard

- **DOY (Day of Year)** is the shared x-axis across all panels, making it easy to cross-reference NDVI dips with dry spells or GDD plateaus.
- **CDL percentages** come from the farm-level CDL CSV table clipped to the target field — they reflect the dominant crop classes in the field for that year.
- **GDD** uses a base temperature of 10°C with a 30°C cap, standard for corn in the US Corn Belt. The crop-appropriate base can be adjusted in the script.
- **Dry spells** are filtered to the growing season window (DOY 90–300) to exclude normal winter dry periods.
- **NDVI** is computed from Sentinel-2 Level-2A scenes (B04 red / B08 NIR) and masked to the field boundary before averaging.

## Output Directory Structure

```
fields/<field>/
├── derived/
│   ├── reports/
│   │   └── field_season_dashboard_2022.png
│   └── summaries/
│       └── field_season_summary_2022.json
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "No CDL data" | CDL table not found for this farm/year | Check `derived/tables/<farm>_<year>_cdl.csv` exists |
| "No NDVI scenes" | Satellite directory missing or empty | Verify Sentinel-2 pipeline ran for this field/year |
| "No weather data" | Weather CSV missing or year not in range | Check `weather/daily_weather.csv` covers the target year |
| `rasterio` import error | Runtime venv not activated | Use the data-pipeline `.venv` Python interpreter |
