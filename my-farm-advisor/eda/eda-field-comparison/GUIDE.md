---
name: eda-field-comparison
description: Compare field boundaries, crop land use (CDL), weather patterns, and geospatial imagery across growers and fields. Produce static visualizations including histograms, box plots, stacked bars, scatter plots, and satellite basemaps.
version: 1.0.0
author: Boreal Bytes
tags: [comparison, boundaries, cdl, weather, geospatial, fields, growers]
---

# Workflow: eda-field-comparison

## Description

Compare agricultural field attributes across multiple growers using the runtime data pipeline. Analyze field boundary sizes, crop composition from CDL (Cropland Data Layer), weather patterns from NASA POWER, and geospatial basemaps from Esri World Imagery. All outputs are static PNG files and a single summary CSV — no interactive dashboards.

## When to Use This Workflow

- **Field size comparison**: Compare acreage distributions across growers
- **Crop rotation analysis**: See corn/soybean dominance and diversity per field
- **Weather comparison**: Compare temperature and precipitation across regions
- **Geospatial overview**: Generate satellite basemaps with field boundaries overlaid
- **Cross-grower reporting**: Produce a consistent set of visualizations for multiple growers

## Prerequisites

```bash
pip install pandas geopandas matplotlib seaborn contextily
```

## Quick Start

```bash
cd scripts/
python boundaries.py \
  --growers iowa-north,minnesota-north,nebraska-grower \
  --data-root /home/coder/my-farm-advisor-runtime \
  --output-dir /home/coder/my-farm-advisor-runtime/data-pipeline/eda/field-comparison/output
```

## Common Tasks

### Task 1: Generate Field Boundary Analysis

**What**: Field size histograms per grower and a cross-grower box plot.

**When to use**: Understand how field sizes vary within and across growers.

```bash
python scripts/boundaries.py \
  --growers iowa-north,minnesota-north,nebraska-grower \
  --data-root ${DATA_PIPELINE_DATA_ROOT} \
  --output-dir ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-comparison/output
```

**Produces**:
- `{grower}_field_size_histogram.png` (3 files)
- `across_growers_field_size_boxplot.png`
- `field_size_summary.csv`

### Task 2: Generate CDL Analysis

**What**: Crop composition, rotation diversity, and corn-vs-soybean comparison.

**When to use**: Understand what crops are planted and how they change over time.

```bash
python scripts/cdl_analysis.py \
  --growers iowa-north,minnesota-north,nebraska-grower \
  --data-root ${DATA_PIPELINE_DATA_ROOT} \
  --output-dir ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-comparison/output
```

**Produces**:
- `{grower}_crop_composition_stacked.png` (3 files)
- `{grower}_rotation_diversity.png` (3 files)
- `{grower}_corn_vs_soybean_scatter.png` (3 files)
- `{latest_year}_state_crop_split.png` (1 file — cross-grower state-level stacked bar)

### Task 3: Generate Weather Analysis

**What**: Annual temperature, precipitation, and correlation visualizations.

**When to use**: Compare climate conditions across growers.

```bash
python scripts/weather_analysis.py \
  --growers iowa-north,minnesota-north,nebraska-grower \
  --data-root ${DATA_PIPELINE_DATA_ROOT} \
  --output-dir ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-comparison/output
```

**Produces**:
- `{grower}_annual_temp_boxplot.png` (3 files)
- `{grower}_annual_precip_bar.png` (3 files)
- `{grower}_temp_vs_precip_scatter.png` (3 files)
- `{latest_year}_state_gdd_comparison.png` (1 file — cross-grower cumulative GDD with frost dates)

### Task 4: Generate Geospatial Basemaps

**What**: Satellite imagery with field boundary overlays.

**When to use**: Visualize field locations and shapes on a real map.

```bash
python scripts/geospatial.py \
  --growers iowa-north,minnesota-north,nebraska-grower \
  --data-root ${DATA_PIPELINE_DATA_ROOT} \
  --output-dir ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-comparison/output
```

**Produces**:
- `{grower}_satellite_basemap.png` (3 files)

## Complete Example

### Run All Four Scripts

```bash
DATA_ROOT=/home/coder/my-farm-advisor-runtime
OUTDIR=${DATA_ROOT}/data-pipeline/eda/field-comparison/output
GROWERS="iowa-north,minnesota-north,nebraska-grower"

mkdir -p ${OUTDIR}

python scripts/boundaries.py    --growers ${GROWERS} --data-root ${DATA_ROOT} --output-dir ${OUTDIR}
python scripts/cdl_analysis.py  --growers ${GROWERS} --data-root ${DATA_ROOT} --output-dir ${OUTDIR}
python scripts/weather_analysis.py --growers ${GROWERS} --data-root ${DATA_ROOT} --output-dir ${OUTDIR}
python scripts/geospatial.py    --growers ${GROWERS} --data-root ${DATA_ROOT} --output-dir ${OUTDIR}

ls -R ${OUTDIR}
```

## Data Loading Patterns

### Discovering Farm Slugs

Each grower has one farm. Scripts discover it automatically:

```python
import os

def get_farm_slug(data_root, grower):
    farms_dir = os.path.join(data_root, "growers", grower, "farms")
    farm_slugs = os.listdir(farms_dir)
    if not farm_slugs:
        raise ValueError(f"No farms found for {grower}")
    return farm_slugs[0]
```

### Loading Boundaries

```python
import geopandas as gpd

farm_slug = get_farm_slug(data_root, grower)
bfile = os.path.join(data_root, "growers", grower, "farms", farm_slug, "boundary", "field_boundaries.geojson")
gdf = gpd.read_file(bfile)
```

### Loading CDL

```python
import glob

tables = os.path.join(data_root, "growers", grower, "farms", farm_slug, "derived", "tables")
cdl_files = glob.glob(os.path.join(tables, "*full_composition.csv"))
df = pd.read_csv(cdl_files[0])
```

### Loading Weather

```python
weather_dfs = []
for fid in gdf["field_id"]:
    wfile = os.path.join(data_root, "growers", grower, "farms", farm_slug, "fields", fid, "weather", "daily_weather.csv")
    weather_dfs.append(pd.read_csv(wfile))
weather = pd.concat(weather_dfs, ignore_index=True)
```

## Output Directory Structure

```
output/
├── boundaries/
│   ├── iowa-north_field_size_histogram.png
│   ├── minnesota-north_field_size_histogram.png
│   ├── nebraska-grower_field_size_histogram.png
│   ├── across_growers_field_size_boxplot.png
│   └── field_size_summary.csv
├── cdl/
│   ├── iowa-north_crop_composition_stacked.png
│   ├── iowa-north_rotation_diversity.png
│   ├── iowa-north_corn_vs_soybean_scatter.png
│   ├── ... (repeat for minnesota, nebraska)
│   └── 2025_state_crop_split.png
├── weather/
│   ├── iowa-north_annual_temp_boxplot.png
│   ├── iowa-north_annual_precip_bar.png
│   ├── iowa-north_temp_vs_precip_scatter.png
│   ├── ... (repeat for minnesota, nebraska)
│   └── 2025_state_gdd_comparison.png
└── geospatial/
    ├── iowa-north_satellite_basemap.png
    ├── minnesota-north_satellite_basemap.png
    └── nebraska-grower_satellite_basemap.png
```

## Best Practices

### Always use boundary field_ids for iteration

Some growers may have extra field directories without boundary geometry. Filter weather and satellite loading by the `field_id` column from `field_boundaries.geojson`.

### Handle missing tiles gracefully

If `contextily` cannot fetch tiles (no internet), the geospatial script prints a clear error and skips the basemap layer.

### Consistent figure styling

All scripts use:
- `dpi=300` for publication quality
- `bbox_inches="tight"` to prevent label cutoff
- `plt.tight_layout()` before saving
- `plt.close()` after saving to free memory

## Resources

- [GeoPandas Documentation](https://geopandas.org/)
- [Contextily Documentation](https://contextily.readthedocs.io/)
- [Seaborn Documentation](https://seaborn.pydata.org/)
- [Matplotlib Documentation](https://matplotlib.org/)
