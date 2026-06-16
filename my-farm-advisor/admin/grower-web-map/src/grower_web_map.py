from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd


class GrowerWebMapSkill:
    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)

    def build_grower_map(
        self,
        grower_slug: str,
        output_path: str | Path = "",
        title: str | None = None,
    ) -> Path:
        output = Path(output_path) if output_path else self._default_output(grower_slug)
        output.parent.mkdir(parents=True, exist_ok=True)
        title = title or f"{grower_slug.title()} — Grower Portfolio Map"

        grower_root = self.data_root / "growers" / grower_slug
        farms = self._discover_farms(grower_root)

        combined_features: list[dict[str, Any]] = []
        farm_stats: list[dict[str, Any]] = []

        for fm in farms:
            farm_slug = fm["farm_slug"]
            farm_name = fm.get("farm_name", farm_slug)
            farm_dir = grower_root / "farms" / farm_slug

            boundaries = self._load_boundaries(farm_dir, farm_slug, farm_name)
            soil_df = self._load_soil_summary(farm_dir, farm_slug)
            cdl_df = self._load_cdl_composition(farm_dir, farm_slug)
            ndvi_map = self._load_ndvi_summaries(farm_dir, farm_slug)

            for feat in boundaries:
                fid = feat["properties"].get("field_id", "")
                feat["properties"]["farm_slug"] = farm_slug
                feat["properties"]["farm_name"] = farm_name

                soil_row = soil_df[soil_df["field_id"] == fid]
                if not soil_row.empty:
                    r = soil_row.iloc[0]
                    feat["properties"]["soil_om_pct"] = (
                        round(float(r["avg_om_pct"]), 1) if pd.notna(r["avg_om_pct"]) else None
                    )
                    feat["properties"]["soil_ph"] = (
                        round(float(r["avg_ph"]), 1) if pd.notna(r["avg_ph"]) else None
                    )
                    feat["properties"]["soil_clay_pct"] = (
                        round(float(r["avg_clay_pct"]), 1) if pd.notna(r["avg_clay_pct"]) else None
                    )
                    feat["properties"]["soil_dominant"] = str(r.get("dominant_soil", ""))
                    feat["properties"]["soil_drainage"] = str(r.get("drainage_class", ""))

                field_cdl = cdl_df[cdl_df["field_id"] == fid]
                cdl_history: dict[int, str] = {}
                if not field_cdl.empty:
                    for _, cr in field_cdl.iterrows():
                        y = int(cr["year"])
                        cdl_history[y] = str(cr["crop_name"])
                    latest_year = max(cdl_history.keys())
                    feat["properties"]["crop_latest"] = cdl_history.get(latest_year, "")
                    feat["properties"]["crop_latest_year"] = latest_year
                feat["properties"]["cdl_history"] = json.dumps(cdl_history)

                ndvi_data = ndvi_map.get(fid, {})
                if ndvi_data:
                    for card_key in ("corn", "soybean"):
                        info = ndvi_data.get(card_key, {})
                        if info.get("status") == "available" and info.get("mean_ndvi") is not None:
                            feat["properties"][f"ndvi_{card_key}_mean"] = round(float(info["mean_ndvi"]), 3)
                            feat["properties"][f"ndvi_{card_key}_years"] = info.get("years", [])

                combined_features.append(feat)

            total_acres = sum(
                f["properties"].get("area_acres", 0) or 0 for f in boundaries
            )
            om_vals = [
                f["properties"].get("soil_om_pct")
                for f in boundaries
                if f["properties"].get("soil_om_pct") is not None
            ]
            ph_vals = [
                f["properties"].get("soil_ph")
                for f in boundaries
                if f["properties"].get("soil_ph") is not None
            ]
            farm_stats.append(
                {
                    "farm_slug": farm_slug,
                    "farm_name": farm_name,
                    "field_count": len(boundaries),
                    "total_acres": round(total_acres, 1),
                    "avg_om": round(sum(om_vals) / len(om_vals), 1) if om_vals else None,
                    "avg_ph": round(sum(ph_vals) / len(ph_vals), 1) if ph_vals else None,
                }
            )

        collection = {
            "type": "FeatureCollection",
            "features": combined_features,
        }
        geojson_str = json.dumps(collection)
        farm_stats_json = json.dumps(farm_stats)

        center = self._compute_center(collection)
        html = self._build_html(
            title=title,
            grower_slug=grower_slug,
            geojson_str=geojson_str,
            farm_stats_json=farm_stats_json,
            center_lat=center[0],
            center_lon=center[1],
        )
        output.write_text(html, encoding="utf-8")
        print(f"  Grower map saved → {output}  ({len(combined_features)} fields, {len(farm_stats)} farms)")
        return output

    def _default_output(self, grower_slug: str) -> Path:
        return (
            self.data_root
            / "growers"
            / grower_slug
            / "derived"
            / "reports"
            / f"{grower_slug}_grower_map.html"
        )

    def _discover_farms(self, grower_root: Path) -> list[dict[str, str]]:
        manifest_path = grower_root / "manifests" / "pipeline_schedule.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return [
                {"farm_slug": f["farm_slug"], "farm_name": f.get("farm_name", f["farm_slug"])}
                for f in data.get("farms", [])
            ]
        farms_dir = grower_root / "farms"
        if not farms_dir.exists():
            return []
        return [
            {"farm_slug": p.name, "farm_name": p.name.replace("-", " ").title()}
            for p in sorted(farms_dir.iterdir())
            if p.is_dir() and (p / "boundary" / "field_boundaries.geojson").exists()
        ]

    def _load_boundaries(self, farm_dir: Path, farm_slug: str, farm_name: str) -> list[dict[str, Any]]:
        path = farm_dir / "boundary" / "field_boundaries.geojson"
        if not path.exists():
            return []
        gdf = gpd.read_file(path)
        if gdf.empty:
            return []
        if "field_id" not in gdf.columns:
            gdf["field_id"] = [f"{farm_slug}_{i}" for i in range(len(gdf))]
        records = json.loads(gdf.to_json())
        return records.get("features", [])

    def _load_soil_summary(self, farm_dir: Path, farm_slug: str) -> pd.DataFrame:
        path = farm_dir / "derived" / "tables" / f"{farm_slug}_ssurgo_summary.csv"
        if path.exists():
            return pd.read_csv(path)
        return pd.DataFrame()

    def _load_cdl_composition(self, farm_dir: Path, farm_slug: str) -> pd.DataFrame:
        path = farm_dir / "derived" / "tables" / f"{farm_slug}_cdl_2021_2025_full_composition.csv"
        if path.exists():
            df = pd.read_csv(path)
            if "field_id" in df.columns and "year" in df.columns and "crop_name" in df.columns:
                idx = df.groupby(["field_id", "year"])["pct"].idxmax()
                return df.loc[idx].reset_index(drop=True)
            return df
        return pd.DataFrame()

    def _load_ndvi_summaries(self, farm_dir: Path, farm_slug: str) -> dict[str, dict[str, Any]]:
        fields_dir = farm_dir / "fields"
        if not fields_dir.exists():
            return {}
        result: dict[str, dict[str, Any]] = {}
        for field_dir in sorted(fields_dir.iterdir()):
            if not field_dir.is_dir():
                continue
            summary_path = field_dir / "derived" / "summaries" / "ndvi_card_summary.json"
            if summary_path.exists():
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                fid = data.get("field_id", field_dir.name)
                result[fid] = data.get("cards", {})
        return result

    def _compute_center(self, collection: dict[str, Any]) -> tuple[float, float]:
        coords: list[tuple[float, float]] = []
        for feat in collection.get("features", []):
            geom = feat.get("geometry", {})
            if geom.get("type") == "Polygon":
                ring = geom["coordinates"][0]
                for pt in ring:
                    coords.append((pt[1], pt[0]))
            elif geom.get("type") == "MultiPolygon":
                for poly in geom["coordinates"]:
                    ring = poly[0]
                    for pt in ring:
                        coords.append((pt[1], pt[0]))
        if not coords:
            return (40.0, -95.0)
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        return ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)

    def _build_html(
        self,
        title: str,
        grower_slug: str,
        geojson_str: str,
        farm_stats_json: str,
        center_lat: float,
        center_lon: float,
    ) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; min-height: 0; }}
#sidebar {{ width: 360px; min-width: 360px; background: #f8fafc; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; min-height: 0; }}
#sidebar-header {{ padding: 16px 20px; background: #1e293b; color: white; flex-shrink: 0; }}
#sidebar-header h1 {{ font-size: 18px; font-weight: 600; }}
#sidebar-header p {{ font-size: 12px; opacity: 0.8; margin-top: 2px; }}
#sidebar-tabs {{ display: flex; border-bottom: 2px solid #e2e8f0; flex-shrink: 0; }}
.sidebar-tab {{ flex: 1; padding: 10px; text-align: center; cursor: pointer; font-size: 13px; font-weight: 500; color: #64748b; background: none; border: none; border-bottom: 3px solid transparent; transition: all 0.2s; }}
.sidebar-tab:hover {{ background: #f1f5f9; }}
.sidebar-tab.active {{ color: #1e293b; border-bottom-color: #3b82f6; background: white; }}
#sidebar-content {{ flex: 1; overflow-y: auto; padding: 16px 20px; min-height: 0; }}
.tab-pane {{ display: none; }}
.tab-pane.active {{ display: block; }}
.farm-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; cursor: pointer; transition: all 0.2s; }}
.farm-card:hover {{ border-color: #3b82f6; box-shadow: 0 2px 8px rgba(59,130,246,0.15); }}
.farm-card h3 {{ font-size: 14px; color: #1e293b; margin-bottom: 4px; }}
.farm-card .stats {{ font-size: 12px; color: #64748b; display: flex; gap: 12px; flex-wrap: wrap; }}
.stat-label {{ color: #94a3b8; }}
.stat-value {{ font-weight: 600; color: #1e293b; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }}
.legend-swatch {{ width: 18px; height: 18px; border-radius: 3px; border: 1px solid #cbd5e1; flex-shrink: 0; }}
.data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.data-table th {{ background: #f1f5f9; color: #475569; font-weight: 600; padding: 6px 8px; text-align: left; border-bottom: 2px solid #e2e8f0; position: sticky; top: 0; }}
.data-table td {{ padding: 4px 8px; border-bottom: 1px solid #f1f5f9; }}
.data-table tr:hover {{ background: #f8fafc; }}
#map-container {{ flex: 1; display: flex; flex-direction: column; min-height: 0; }}
#map {{ flex: 1; min-height: 0; }}
.leaflet-popup-content {{ font-size: 13px; line-height: 1.5; min-width: 200px; }}
.popup-field {{ font-weight: 600; color: #1e293b; font-size: 15px; margin-bottom: 4px; }}
.popup-row {{ display: flex; justify-content: space-between; padding: 1px 0; }}
.popup-label {{ color: #64748b; }}
.popup-value {{ font-weight: 500; color: #0f172a; }}
.layer-control {{ position: absolute; top: 10px; right: 10px; z-index: 1000; background: white; padding: 8px 12px; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); font-size: 13px; max-height: 60vh; overflow-y: auto; min-width: 160px; }}
.layer-control label {{ display: flex; align-items: center; gap: 6px; padding: 3px 0; cursor: pointer; }}
.layer-control input {{ cursor: pointer; }}
.layer-title {{ font-weight: 600; color: #1e293b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; margin: 6px 0 4px; }}
#map-error {{ display: none; padding: 40px 20px; text-align: center; font-family: sans-serif; }}
#map-error h2 {{ color: #dc2626; margin-bottom: 8px; }}
#map-error p {{ color: #64748b; }}
</style>
</head>
<body>
<div id="sidebar">
  <div id="sidebar-header">
    <h1>{grower_slug.title()}</h1>
    <p id="grower-summary">Loading...</p>
  </div>
  <div id="sidebar-tabs">
    <button class="sidebar-tab active" onclick="switchTab('farms')">Farms</button>
    <button class="sidebar-tab" onclick="switchTab('legend')">Legend</button>
    <button class="sidebar-tab" onclick="switchTab('data')">Data</button>
  </div>
  <div id="sidebar-content">
    <div id="pane-farms" class="tab-pane active"></div>
    <div id="pane-legend" class="tab-pane"></div>
    <div id="pane-data" class="tab-pane"></div>
  </div>
</div>
<div id="map-container">
  <div id="map"></div>
  <div id="map-error">
    <h2>Map Error</h2>
    <p id="map-error-msg">The map could not be initialized.</p>
  </div>
</div>
<div id="layer-control" class="layer-control"></div>

<script>
(function() {{
try {{
const fieldData = {geojson_str};
const farmStats = {farm_stats_json};

const CROP_COLORS = {{
  "Corn": "#2E7D32", "Soybeans": "#F9A825", "Wheat": "#E65100",
  "Cotton": "#1565C0", "Rice": "#00ACC1", "Open Water": "#42A5F5",
  "Forest": "#66BB6A", "Fallow/Idle": "#A1887F", "Grass/Pasture": "#8BC34A", "Default": "#BDBDBD"
}};

function getCropColor(crop) {{ return CROP_COLORS[crop] || CROP_COLORS["Default"]; }}

const map = L.map('map', {{ renderer: L.canvas() }}).setView([{center_lat}, {center_lon}], 12);

const basemaps = {{
  "OpenStreetMap": L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ attribution: '&copy; OpenStreetMap contributors' }}),
  "Satellite": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{ attribution: '&copy; Esri' }}),
}};
basemaps["OpenStreetMap"].addTo(map);
L.control.layers(basemaps, null, {{ position: 'bottomleft' }}).addTo(map);

const farmsGeoJSON = {{}};
const farmNames = {{}};
const farmGroups = {{}};
const activeLayers = new Set();

fieldData.features.forEach(function(f) {{
  const fs = f.properties.farm_slug;
  if (!farmsGeoJSON[fs]) {{ farmsGeoJSON[fs] = []; farmNames[fs] = f.properties.farm_name; }}
  farmsGeoJSON[fs].push(f);
}});

function fieldStyle(feature) {{
  const crop = feature.properties.crop_latest;
  return {{
    color: '#1e293b', weight: 1.5, fillColor: getCropColor(crop),
    fillOpacity: 0.5, opacity: 0.8,
  }};
}}

Object.keys(farmsGeoJSON).forEach(function(fs) {{
  const collection = {{ type: 'FeatureCollection', features: farmsGeoJSON[fs] }};
  if (!collection.features.length) return;
  const layer = L.geoJSON(collection, {{
    style: fieldStyle,
    onEachFeature: function(feature, lyr) {{
      const p = feature.properties;
      let html = '<div class="popup-field">' + (p.field_id || 'Field') + '</div>';
      html += '<div class="popup-row"><span class="popup-label">Farm:</span><span class="popup-value">' + p.farm_name + '</span></div>';
      html += '<div class="popup-row"><span class="popup-label">Acres:</span><span class="popup-value">' + (p.area_acres ? p.area_acres.toFixed(1) : 'N/A') + '</span></div>';
      if (p.crop_latest) html += '<div class="popup-row"><span class="popup-label">Crop ' + p.crop_latest_year + ':</span><span class="popup-value" style="color:' + getCropColor(p.crop_latest) + '">' + p.crop_latest + '</span></div>';
      if (p.soil_om_pct != null) html += '<div class="popup-row"><span class="popup-label">Soil OM:</span><span class="popup-value">' + p.soil_om_pct + '%</span></div>';
      if (p.soil_ph != null) html += '<div class="popup-row"><span class="popup-label">Soil pH:</span><span class="popup-value">' + p.soil_ph + '</span></div>';
      if (p.soil_clay_pct != null) html += '<div class="popup-row"><span class="popup-label">Clay:</span><span class="popup-value">' + p.soil_clay_pct + '%</span></div>';
      if (p.ndvi_soybean_mean != null) html += '<div class="popup-row"><span class="popup-label">NDVI Soybean:</span><span class="popup-value">' + p.ndvi_soybean_mean.toFixed(3) + '</span></div>';
      if (p.ndvi_corn_mean != null) html += '<div class="popup-row"><span class="popup-label">NDVI Corn:</span><span class="popup-value">' + p.ndvi_corn_mean.toFixed(3) + '</span></div>';
      lyr.bindPopup(html);
    }}
  }});
  farmsGeoJSON[fs] = layer;
  farmGroups[fs] = L.layerGroup([layer]).addTo(map);
  activeLayers.add(fs);
}});

if (Object.keys(farmGroups).length > 0) {{
  const layers = Object.values(farmGroups).map(function(g) {{ var l = g.getLayers(); return l.length ? l[0] : null; }}).filter(function(x) {{ return x; }});
  if (layers.length) {{
    const allBounds = L.featureGroup(layers).getBounds();
    if (allBounds.isValid()) map.fitBounds(allBounds.pad(0.15));
  }}
}}

map.invalidateSize();

let currentHighlight = null;
function highlightField(fieldId) {{
  if (currentHighlight) {{ map.removeLayer(currentHighlight); currentHighlight = null; }}
  fieldData.features.forEach(function(f) {{
    if (f.properties.field_id === fieldId) {{
      const highlight = L.geoJSON(f, {{ style: {{ color: '#ef4444', weight: 3, fillOpacity: 0 }} }}).addTo(map);
      currentHighlight = highlight;
      var b = highlight.getBounds();
      if (b.isValid()) map.fitBounds(b.pad(0.3));
    }}
  }});
}}

function renderFarms() {{
  let html = '';
  let totalFields = 0, totalAcres = 0;
  farmStats.forEach(function(f) {{
    totalFields += f.field_count; totalAcres += f.total_acres;
    html += '<div class="farm-card" onclick="focusFarm(\\'' + f.farm_slug + '\\')">';
    html += '<h3>' + f.farm_name + '</h3>';
    html += '<div class="stats"><span class="stat"><span class="stat-label">Fields: </span><span class="stat-value">' + f.field_count + '</span></span>';
    html += '<span class="stat"><span class="stat-label">Acres: </span><span class="stat-value">' + f.total_acres.toFixed(1) + '</span></span>';
    if (f.avg_om != null) html += '<span class="stat"><span class="stat-label">OM: </span><span class="stat-value">' + f.avg_om + '%</span></span>';
    if (f.avg_ph != null) html += '<span class="stat"><span class="stat-label">pH: </span><span class="stat-value">' + f.avg_ph + '</span></span>';
    html += '</div></div>';
  }});
  document.getElementById('grower-summary').textContent = totalFields + ' fields \u00b7 ' + totalAcres.toFixed(1) + ' acres';
  document.getElementById('pane-farms').innerHTML = html;
}}

function focusFarm(farmSlug) {{
  var g = farmGroups[farmSlug];
  if (g) {{ var layers = g.getLayers(); if (layers.length) {{ var b = layers[0].getBounds(); if (b.isValid()) map.fitBounds(b.pad(0.15)); }} }}
}}

function renderLegend() {{
  const allCrops = new Set();
  fieldData.features.forEach(function(f) {{ if (f.properties.crop_latest) allCrops.add(f.properties.crop_latest); }});
  let html = '<div class="layer-title">Crop Colors</div>';
  allCrops.forEach(function(c) {{
    html += '<div class="legend-item"><div class="legend-swatch" style="background:' + getCropColor(c) + '"></div>' + c + '</div>';
  }});
  html += '<div class="layer-title" style="margin-top:12px">Soil pH</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#1565C0"></div>Acidic (&lt;6.0)</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#2E7D32"></div>Neutral (6.0\u20137.5)</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#C62828"></div>Alkaline (&gt;7.5)</div>';
  html += '<div class="layer-title" style="margin-top:12px">NDVI</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#8B4513"></div>&lt; 0.2 (Bare)</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#FFD700"></div>0.2\u20130.4</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#9ACD32"></div>0.4\u20130.6</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#228B22"></div>0.6\u20130.8</div>';
  html += '<div class="legend-item"><div class="legend-swatch" style="background:#006400"></div>&gt; 0.8</div>';
  document.getElementById('pane-legend').innerHTML = html;
}}

function renderData() {{
  const rows = [];
  fieldData.features.forEach(function(f) {{
    const p = f.properties;
    rows.push([
      p.field_id || '', p.farm_name || '', (p.area_acres || 0).toFixed(1),
      p.crop_latest || '', p.soil_om_pct != null ? p.soil_om_pct + '%' : '',
      p.soil_ph != null ? String(p.soil_ph) : '', p.soil_clay_pct != null ? p.soil_clay_pct + '%' : '',
      p.soil_dominant || '', p.soil_drainage || '',
      p.ndvi_soybean_mean != null ? p.ndvi_soybean_mean.toFixed(3) : '',
      p.ndvi_corn_mean != null ? p.ndvi_corn_mean.toFixed(3) : '',
    ]);
  }});
  const headers = ['Field', 'Farm', 'Acres', 'Crop', 'OM', 'pH', 'Clay', 'Soil Type', 'Drainage', 'NDVI Soy', 'NDVI Corn'];
  let html = '<table class="data-table"><thead><tr>';
  headers.forEach(function(h) {{ html += '<th>' + h + '</th>'; }});
  html += '</tr></thead><tbody>';
  rows.forEach(function(r) {{
    html += '<tr onclick="highlightField(\\'' + r[0] + '\\')">';
    r.forEach(function(v) {{ html += '<td>' + v + '</td>'; }});
    html += '</tr>';
  }});
  html += '</tbody></table>';
  document.getElementById('pane-data').innerHTML = html;
}}

function renderLayerControls() {{
  let html = '<div class="layer-title">Farms</div>';
  Object.keys(farmGroups).forEach(function(fs) {{
    const checked = activeLayers.has(fs) ? 'checked' : '';
    html += '<label><input type="checkbox" ' + checked + ' onchange="toggleFarm(\\'' + fs + '\\', this.checked)"> ' + (farmNames[fs] || fs) + '</label>';
  }});
  document.getElementById('layer-control').innerHTML = html;
}}

function toggleFarm(fs, visible) {{
  if (visible) {{ map.addLayer(farmGroups[fs]); activeLayers.add(fs); }} else {{ map.removeLayer(farmGroups[fs]); activeLayers.delete(fs); }}
}}

function switchTab(name) {{
  document.querySelectorAll('.sidebar-tab').forEach(function(t) {{ t.classList.remove('active'); }});
  document.querySelectorAll('.tab-pane').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelector('.sidebar-tab[onclick*="' + name + '"]').classList.add('active');
  document.getElementById('pane-' + name).classList.add('active');
}}

renderFarms();
renderLegend();
renderData();
renderLayerControls();

}} catch(e) {{
  console.error('Grower map error:', e);
  document.getElementById('map').style.display = 'none';
  var errDiv = document.getElementById('map-error');
  errDiv.style.display = 'block';
  document.getElementById('map-error-msg').textContent = e.message || String(e);
}}
}})();
</script>
</body>
</html>"""
