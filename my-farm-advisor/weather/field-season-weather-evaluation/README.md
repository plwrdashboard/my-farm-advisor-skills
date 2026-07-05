# Field Season Weather Evaluation

Generate a single-year, multi-panel field season dashboard that combines **CDL crop classification**, **Sentinel-2 NDVI time series**, and **NASA POWER daily weather** into one unified report for a single field and year.

---

## What this skill covers

The dashboard is a vertical-stack figure with a shared DOY (Day of Year) x-axis spanning March through November:

| Panel | Source | Content |
|-------|--------|---------|
| **Header** | — | Field ID, year, CDL crop percentages |
| **NDVI Season Curve** | Sentinel-2 NDVI scenes | Mean NDVI per scene, ±1 std envelope, peak annotation |
| **GDD Accumulation** | NASA POWER daily weather | Cumulative Growing Degree Days (base 10°C, cap 30°C) |
| **Daily Precipitation** | NASA POWER daily weather | Bar chart with heavy rain (>25 mm) highlighted |
| **Season Stress Summary** | Derived from weather | Heat days, cold nights, heavy rain days, dry spells (≥5 d <1 mm) |

---

## Prerequisites

The field must have pipeline-generated data under the runtime data root:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/<field>/
├── boundary/field_boundary.geojson
├── weather/daily_weather.csv
├── satellite/sentinel/<year>/
│   ├── sentinel_<date>_ndvi.tif
│   └── ...
└── ../derived/tables/<farm>_<year>_cdl.csv
```

Python dependencies: `rasterio`, `geopandas`, `matplotlib`, `pandas`, `numpy`.

---

## Quick Start

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime

python weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --grower minnesota-north \
  --farm minnesota-north-minnesota \
  --field osm-1491018233 \
  --year 2022
```

---

## Examples

### 2022 — Corn year for field osm-1491018233
```bash
/home/coder/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
  ../scripts/field_season_dashboard.py --year 2022
```

### 2021 — Soybean year for the same field
```bash
/home/coder/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
  ../scripts/field_season_dashboard.py --year 2021
```

### Run for a different farm
```bash
python weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --grower <grower> --farm <farm> --field <field> --year <YYYY>
```

---

## Outputs

After running the dashboard, artifacts are written to the field's derived directories:

- `derived/reports/field_season_dashboard_<year>.png` — multi-panel dashboard figure
- `derived/summaries/field_season_summary_<year>.json` — machine-readable metrics

JSON summary example:
```json
{
  "field": "osm-1491018233",
  "year": 2021,
  "cdl": [{"crop_name": "Soybeans", "pct": 72.92}],
  "weather": {"total_gdd": 1806.1, "total_precip_mm": 672.6},
  "ndvi": {"peak_ndvi": 0.89, "peak_doy": 224}
}
```

---

## Reusability

The script accepts `--grower`, `--farm`, `--field`, and `--year` arguments. Any field with the canonical data structure can be evaluated — just change the parameters.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "No CDL data" | CDL table missing | Check `derived/tables/<farm>_<year>_cdl.csv` exists |
| "No NDVI scenes" | Satellite directory empty | Verify Sentinel-2 pipeline ran for this field/year |
| "No weather data" | Weather CSV missing or year out of range | Check `weather/daily_weather.csv` covers the target year |
| `rasterio` import error | venv not activated | Use the data-pipeline `.venv` Python interpreter |

---

For full workflow details, see [GUIDE.md](GUIDE.md).
