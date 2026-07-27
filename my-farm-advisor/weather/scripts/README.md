# Corn Weather Stress Dashboard

Interactive **Plotly Dash** web app with cascading grower → field selection, an ESRI satellite map with field boundary and SSURGO AWC soil overlay, and 5 weather stress indicator panels.

---

## What this dashboard covers

| Panel / Feature | Source | Content |
|---------------|--------|---------|
| **Map + AWC** | ESRI satellite, SSURGO | Field boundary on satellite imagery, SSURGO soil map unit polygons colored by Available Water Capacity (0–100 cm) with tooltips |
| **NDVI Season Curve** | Sentinel-2 NDVI scenes | Mean NDVI per scene, ±1σ envelope, peak annotation |
| **GDD Accumulation** | NASA POWER daily weather | Cumulative Growing Degree Days (base 50°F), last/first frost date markers |
| **Daily Temperature** | NASA POWER daily weather | Max/min/mean temperatures, heat (>90°F) and cold (<32°F) stress markers |
| **Precipitation & Stress** | NASA POWER daily weather | Daily + cumulative precipitation, dry spell bands (≥5 d <1 mm), heavy rain (>1 in) markers |
| **ETc & Water Balance** | FAO-56 derived | Daily + cumulative ETc vs precipitation, corn growth stage bands (VE–VT, R1–R3, R4–R5), water deficit/surplus annotation |
| **Callout: Heat Stress** | Derived from weather | Count of days with max temp >90°F |
| **Callout: Water Deficit** | Derived from ETc vs precip | First date cumulative ETc exceeds cumulative precipitation |

---

## Prerequisites

The field must have pipeline-generated data under the runtime data root:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/<field>/
├── boundary/field_boundary.geojson
├── weather/daily_weather.csv
├── satellite/sentinel/<year>/
│   └── <scene>/*_ndvi.tif
└── derived/tables/<farm>_<year>_cdl.csv
```

Optional but recommended — SSURGO AWC overlay (adds soil map unit polygons):

```
{field_dir}/derived/features/ssurgo_awc.geojson
```

Pre-compute AWC data by running `precompute_ssurgo_awc.py` first (see its docstring for usage).

Python dependencies: `dash`, `dash_leaflet`, `plotly`, `pandas`, `numpy`, `shapely`, `rasterio`, `geopandas`.

---

## Quick Start

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime

python weather/scripts/weather_stress_dashboard.py
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050) in a browser.

---

## Usage

```
python weather/scripts/weather_stress_dashboard.py [--port PORT] [--debug]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | `8050` | Dash server port |
| `--debug` | `False` | Enable Dash debug mode |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No growers in dropdown | `DATA_PIPELINE_DATA_ROOT` not set or growers directory missing | Verify `$DATA_PIPELINE_DATA_ROOT/data-pipeline/growers/` exists |
| No fields for grower | No corn fields detected for selected year | Run CDL pipeline or check `derived/tables/*_<year>_cdl.csv` |
| Empty NDVI panel | No Sentinel-2 scenes for field/year | Verify `satellite/sentinel/<year>/` has NDVI `.tif` files |
| Empty weather panel | Weather CSV missing or year out of range | Check `weather/daily_weather.csv` covers the target year |
| No AWC overlay | SSURGO data not pre-computed | Run `precompute_ssurgo_awc.py` for this field |
| `dash_leaflet` import error | Dependency not installed | `pip install dash dash-leaflet plotly pandas numpy shapely` |

---

## Related scripts

- `precompute_ssurgo_awc.py` — Pre-computes SSURGO AWC polygons for all corn fields (feeds the AWC soil overlay)
- `run_etc_batch.py` — Batch FAO-56 ETc computation for all corn fields × years
