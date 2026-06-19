# Grower Web Map — Local Instructions

## Purpose

This subskill generates lightweight, self-contained interactive HTML maps for each grower in the data pipeline. The map displays all field polygons from all farms under a grower, rendered on a Leaflet.js basemap with click popups and a zoom-to-farm sidebar.

## Safe edit scope

Edits should stay in this folder and its parent `data-pipeline/` children unless explicitly requested. Do not change parent `SKILL.md`, sibling workflows, or root policy.

## Read nearby docs first

Read `GUIDE.md` first for usage examples and CLI reference. Review `../src/scripts/reporting/generate_grower_web_map.py` for the implementation.

## Command runbook

Generate a web map for a specific grower:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --grower-slug iowa-north
```

Generate web maps for all growers:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --all-growers
```

## Output

- `growers/<grower_slug>/derived/reports/grower_web_map.html`
- Self-contained HTML with embedded GeoJSON (no external files)
- Leaflet.js and tiles loaded from CDN (internet required for tiles)

## Map features

- OpenStreetMap basemap
- Field polygons colored by farm
- Click popup: grower, farm, field ID, area (acres), crop
- Sidebar with farm legend and zoom-to-farm buttons
- Auto-fit bounds to all fields

## Local validation

Run the script against existing growers and open the generated HTML in a browser:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
python scripts/reporting/generate_grower_web_map.py --all-growers
```

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or root files. Do not duplicate root-wide asset, vendor, or validation policy here except this pointer to `../../../AGENTS.md`.
