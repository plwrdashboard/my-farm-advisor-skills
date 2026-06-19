---
name: grower-web-map
description: Generate lightweight, self-contained interactive HTML maps for each grower in the My Farm Advisor data pipeline. Displays all field polygons from all farms under a grower with Leaflet.js, click popups, and a zoom-to-farm sidebar.
version: 1.0.0
author: Boreal Bytes
tags: [web-map, visualization, leaflet, geospatial, grower, interactive]
---

# Workflow: grower-web-map

## Description

Generate a grower-level interactive web map that displays all field polygons from all farms under a single grower. The output is a single, self-contained HTML file that can be opened directly in any web browser, shared via email, or deployed to a static web server.

**Key Features:**

- **Self-contained**: Single HTML file with embedded GeoJSON data
- **Grower-level scope**: Shows all farms and all fields for one grower
- **Farm color coding**: Each farm gets a distinct color for its field polygons
- **Click popups**: Field metadata including grower, farm, field ID, area (acres), and crop type
- **Zoom sidebar**: Collapsible panel with farm legend and zoom-to-farm buttons
- **Auto-fit bounds**: Map centers on all fields combined
- **Lightweight**: No embedded imagery; tiles load from the internet

## Output Example

A single `grower_web_map.html` file (typically 50–500 KB for 3–20 fields) that:

- Opens in any modern web browser
- Requires no installation or server
- Can be emailed as an attachment
- Can be hosted on any static web server

## Prerequisites

The data pipeline runtime must already have grower data with field boundary GeoJSON files:

```
growers/<grower_slug>/farms/<farm_slug>/boundary/field_boundaries.geojson
```

To view maps: **Any modern web browser** (Chrome, Firefox, Safari, Edge)

## Quick Start

### Generate for a single grower

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --grower-slug iowa-north
```

### Generate for all growers

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --all-growers
```

### Open the result

```bash
open "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/iowa-north/derived/reports/grower_web_map.html"
```

## Map Features

### Basemap

OpenStreetMap tiles loaded from `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`.

### Field Polygons

Each field polygon is styled with:
- **Color**: Farm-specific color (green, blue, red, orange, purple, teal)
- **Weight**: 2px border
- **Fill opacity**: 0.3

### Click Popups

Clicking any field shows:
- **Grower**: grower slug
- **Farm**: farm display name
- **Field**: field ID
- **Area**: acres (from GeoJSON properties)
- **Crop**: crop type (from GeoJSON properties)

### Sidebar Controls

- **Farm legend**: color-coded farm list
- **Zoom buttons**: click a farm name to zoom to its fields
- **Info panel**: usage instructions

## CLI Reference

```
python scripts/reporting/generate_grower_web_map.py [options]

Options:
  --grower-slug <slug>   Generate map for a specific grower
  --all-growers          Generate maps for all growers in the runtime
```

## Output Path

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower_slug>/derived/reports/grower_web_map.html
```

## Integration with Farm Pipeline

The grower web map is **not** automatically run as part of the 13-step farm pipeline. It is a standalone reporting tool that can be invoked after the pipeline has generated field boundaries.

To run after a farm pipeline completes:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
python scripts/reporting/generate_grower_web_map.py --all-growers
```

## Styling

Farm colors are defined in `generate_grower_web_map.py` as:

```python
FARM_COLORS = [
    "#2E7D32",  # green
    "#1565C0",  # blue
    "#C62828",  # red
    "#F57C00",  # orange
    "#6A1B9A",  # purple
    "#00838F",  # teal
]
```

Up to 6 farms per grower are supported with distinct colors. Additional farms cycle through the palette.

## Performance

- Handles 200+ fields smoothly
- GeoJSON is embedded as a compact JSON string
- No external data files required
- Tile rendering is handled by the browser

## Responsive Design

- Sidebar is fixed at 300px width on desktop
- Map area fills remaining space
- Works on tablets and mobile devices

## When to Use

- **Sharing results**: Send a complete interactive map to collaborators
- **Field work**: Load maps on tablets for offline reference (cached tiles)
- **Reports**: Include interactive maps in presentations
- **Monitoring**: Create dashboards for tracking field boundaries

## Troubleshooting

**No farms found**: Ensure the grower has farms with `boundary/field_boundaries.geojson` files.

**No fields found**: Ensure the GeoJSON has `features` with valid Polygon/MultiPolygon geometries.

**Map is blank**: Check internet connection for tile loading.
