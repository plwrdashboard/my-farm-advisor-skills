---
name: grower-web-map
description: Generate self-contained Leaflet.js interactive web maps that aggregate all farms under a grower into a single portfolio dashboard with field boundaries, soil data, crop history, and NDVI metrics.
version: 1.0.0
author: Boreal Bytes
tags: [web-map, visualization, leaflet, grower, portfolio, dashboard, geospatial]
---

# Workflow: grower-web-map

## Description

Create a self-contained HTML interactive web map showing every farm and field across a grower's portfolio. The map embeds soil summaries, CDL crop history, and NDVI metrics per field, grouped by farm with toggle visibility.

**Key Features:**
- **Portfolio view**: All farms on one map, grouped as toggleable layer groups
- **Farm cards**: Click a farm card to zoom to its fields
- **Data table**: Sortable table of all fields with soil, crop, and NDVI columns
- **Click popups**: Per-field details (acres, crop, soil OM/pH/clay, NDVI)
- **Crop choropleth**: Fields color-coded by latest CDL crop classification
- **Basemap switcher**: OpenStreetMap, Satellite
- **Self-contained**: Single HTML file, opens in any browser

## Output

```
growers/{grower_slug}/derived/reports/{grower_slug}_grower_map.html
```

A self-contained HTML file (typically 200 KB–2 MB depending on field count) that:
- Opens in any modern web browser with no server required
- Can be emailed, shared, or deployed to static hosting

## Prerequisites

```bash
pip install pandas geopandas
```

The data pipeline must have been run at least once for each farm under the grower (so soil, CDL, and NDVI data exist in the canonical paths).

## Quick Start

### Via farm_dashboard.py (recommended)

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
python src/scripts/farm_dashboard.py map --grower-slug plwr
```

### Via standalone script

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
python src/scripts/reporting/generate_grower_web_map.py --grower-slug plwr
```

### Via Python API

```python
from grower_web_map import GrowerWebMapSkill

skill = GrowerWebMapSkill(data_root="/home/coder/my-farm-advisor-runtime")
output = skill.build_grower_map(grower_slug="plwr")
print(f"Map saved to: {output}")
```

## How It Works

The `GrowerWebMapSkill` class reads:

| Data Source | Path (relative to `{grower}/{farm}/`) |
|---|---|
| Field boundaries | `boundary/field_boundaries.geojson` |
| Soil summary | `derived/tables/{farm}_ssurgo_summary.csv` |
| CDL composition | `derived/tables/{farm}_cdl_2021_2025_full_composition.csv` |
| NDVI cards | `fields/{slug}/derived/summaries/ndvi_card_summary.json` |

Each field feature in the resulting map is enriched with:
- `farm_slug`, `farm_name` (from grower manifest)
- `soil_om_pct`, `soil_ph`, `soil_clay_pct`, `soil_dominant` (from SSURGO)
- `crop_latest`, `crop_latest_year`, `cdl_history` (from CDL)
- `ndvi_corn_mean`, `ndvi_soybean_mean` (from NDVI cards)

## Integration

The map script is callable from `farm_dashboard.py` after all farm pipelines complete:

```bash
# After refreshing all farms:
farm_dashboard.py refresh --scope grower --grower-slug plwr
farm_dashboard.py map --grower-slug plwr
```

Or chain them:
```bash
farm_dashboard.py refresh --scope grower --grower-slug plwr && farm_dashboard.py map --grower-slug plwr
```

## Styling Guidelines

### Crop Colors

```javascript
const CROP_COLORS = {
  Corn: "#2E7D32",
  Soybeans: "#F9A825",
  Wheat: "#E65100",
  Open Water: "#42A5F5",
  Forest: "#66BB6A",
  Fallow/Idle: "#A1887F",
  Default: "#BDBDBD",
};
```

### Soil pH Ramp

- **< 6.0** → `#1565C0` (acidic)
- **6.0–7.5** → `#2E7D32` (neutral)
- **> 7.5** → `#C62828` (alkaline)

### NDVI Ramp

- **< 0.2** → `#8B4513` (bare soil)
- **0.2–0.4** → `#FFD700` (sparse)
- **0.4–0.6** → `#9ACD32` (moderate)
- **0.6–0.8** → `#228B22` (vigorous)
- **> 0.8** → `#006400` (dense)

## Performance

For growers with 200+ fields, the map uses Leaflet Canvas rendering and embeds simplified GeoJSON. The HTML file may reach 2–5 MB for very large portfolios.

## Resources

- [Leaflet Documentation](https://leafletjs.com/)
- [ColorBrewer](https://colorbrewer2.org/) (color ramps)
