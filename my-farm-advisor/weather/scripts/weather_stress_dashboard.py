"""Corn Weather Stress Dashboard — interactive Plotly Dash app.

Launches a Dash server with cascading grower → field selection,
a Dash Leaflet map showing the selected field boundary on ESRI
satellite imagery, and 5 weather stress indicator panels.

Usage:
    python weather_stress_dashboard.py
    python weather_stress_dashboard.py --port 8050 --debug
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import traceback

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback, dcc, html
from dash_leaflet import Map, Marker, Polygon, Popup, TileLayer
from plotly.subplots import make_subplots
from shapely.geometry import shape

_DATA_PIPELINE_ROOT = Path(
    os.environ.get(
        "DATA_PIPELINE_DATA_ROOT",
        "/home/coder/my-farm-advisor-runtime/data-pipeline",
    )
)
_GROWERS_ROOT = _DATA_PIPELINE_ROOT / "growers"
_ETC_CSV = _DATA_PIPELINE_ROOT / "eda" / "plant-health" / "output" / "daily_etc.csv"

ESRI_SATELLITE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_LABELS = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)

YEAR_OPTIONS = [{"label": str(y), "value": y} for y in range(2021, 2026)]

GDD_BASE_C = 10.0
GDD_CAP_C = 30.0
HEAT_THRESHOLD_C = 32.0
COLD_THRESHOLD_C = 0.0
HEAVY_RAIN_MM = 25.0
DRY_SPELL_MIN_DAYS = 5
DRY_SPELL_MAX_PRECIP_MM = 1.0
GROWING_SEASON_DOY_START = 90
GROWING_SEASON_DOY_END = 300
XAXIS_DOY_MIN = 60
XAXIS_DOY_MAX = 334
MONTH_TICKS = {60: "Mar", 91: "Apr", 121: "May", 152: "Jun",
               182: "Jul", 213: "Aug", 244: "Sep", 274: "Oct", 305: "Nov"}

_CHART_THEME = {
    "paper_bgcolor": "#fafaf9",
    "plot_bgcolor": "#fafaf9",
    "font": {"color": "#333", "size": 11},
    "title_font": {"size": 13, "color": "#1B5E20"},
    "margin": {"l": 50, "r": 50, "t": 40, "b": 40},
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def discover_growers() -> list[dict]:
    growers: list[dict] = []
    if not _GROWERS_ROOT.exists():
        return growers
    for gdir in sorted(_GROWERS_ROOT.iterdir()):
        if not gdir.is_dir():
            continue
        meta = _load_json(gdir / "grower.json")
        slug = meta.get("grower_slug") or gdir.name
        display = meta.get("display_name") or slug.replace("-", " ").title()
        growers.append({"slug": slug, "display_name": display})
    return growers


def discover_fields(grower_slug: str) -> list[dict]:
    fields: list[dict] = []
    farms_dir = _GROWERS_ROOT / grower_slug / "farms"
    if not farms_dir.exists():
        return fields

    for fdir in sorted(farms_dir.iterdir()):
        if not fdir.is_dir():
            continue
        farm_slug = fdir.name
        farm_meta = _load_json(fdir / "farm.json")
        farm_display = farm_meta.get("display_name", farm_slug)

        inventory_csv = fdir / "manifests" / "field-inventory.csv"
        if not inventory_csv.exists():
            continue

        df = pd.read_csv(inventory_csv)
        for _, row in df.iterrows():
            fid = str(row["field_id"])
            fslug = str(row.get("field_slug", fid))
            geojson_path = fdir / "fields" / fslug / "boundary" / "field_boundary.geojson"
            weather_path = fdir / "fields" / fslug / "weather" / "daily_weather.csv"

            geo = _load_json(geojson_path)
            props = {}
            if geo.get("features"):
                props = geo["features"][0].get("properties", {})

            fields.append(
                {
                    "field_id": fid,
                    "field_slug": fslug,
                    "farm_slug": farm_slug,
                    "farm_display": farm_display,
                    "grower_slug": grower_slug,
                    "area_acres": props.get("area_acres", 0),
                    "county": props.get("county_name", ""),
                    "crop": props.get("crop_name", ""),
                    "irrigation": props.get("irrigation", ""),
                    "geojson_path": str(geojson_path),
                    "weather_path": str(weather_path),
                }
            )
    return fields


def load_boundary_geojson(geojson_path: str) -> list[list[float]] | None:
    geo = _load_json(Path(geojson_path))
    if not geo.get("features"):
        return None
    feat = geo["features"][0]
    geom = feat.get("geometry")
    if not geom:
        return None
    coords = geom.get("coordinates")
    if not coords:
        return None

    rings: list[list[list[float]]] = []
    if geom["type"] == "Polygon":
        rings = coords
    elif geom["type"] == "MultiPolygon":
        rings = max(coords, key=lambda p: len(p[0]))
    else:
        return None

    if not rings:
        return None
    return rings[0]


def compute_padded_bounds(
    coords: list[list[float]], padding: float = 0.05
) -> tuple[float, float, float, float]:
    lngs = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    lng_pad = (max_lng - min_lng) * padding
    lat_pad = (max_lat - min_lat) * padding
    if lng_pad < 0.001:
        lng_pad = 0.001
    if lat_pad < 0.001:
        lat_pad = 0.001
    return (
        min_lat - lat_pad,
        min_lng - lng_pad,
        max_lat + lat_pad,
        max_lng + lng_pad,
    )


def load_weather(field: dict, year: int) -> pd.DataFrame:
    path = Path(field.get("weather_path", ""))
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[df["date"].dt.year == year].copy()
    if df.empty:
        return df
    df["doy"] = df["date"].dt.dayofyear
    df["gdd_daily"] = df.apply(
        lambda r: max(0.0, min((r["T2M_MAX"] + r["T2M_MIN"]) / 2, GDD_CAP_C) - GDD_BASE_C),
        axis=1,
    )
    df["gdd_cum"] = df["gdd_daily"].cumsum()
    df["precip_mm"] = df["PRECTOTCORR"].clip(lower=0)
    df["precip_in"] = df["precip_mm"] / 25.4
    df["T2M_F"] = df["T2M"] * 9 / 5 + 32
    df["T2M_MAX_F"] = df["T2M_MAX"] * 9 / 5 + 32
    df["T2M_MIN_F"] = df["T2M_MIN"] * 9 / 5 + 32
    df["gdd_cum_F"] = df["gdd_cum"] * 9 / 5
    return df


def _cdl_table_prefix(farm_slug: str) -> str:
    norm = farm_slug.strip().replace("-", "_")
    if norm.endswith("_farm"):
        norm = norm[: -len("_farm")]
    return norm


def load_cdl_crop(grower_slug: str, farm_slug: str, field_id: str, year: int) -> str:
    prefix = _cdl_table_prefix(farm_slug)
    cdl_path = (_GROWERS_ROOT / grower_slug / "farms" / farm_slug
                / "derived" / "tables" / f"{prefix}_{year}_cdl.csv")
    if not cdl_path.exists():
        return ""
    df = pd.read_csv(cdl_path)
    field_cdl = df[df["field_id"] == field_id].copy()
    if field_cdl.empty:
        return ""
    field_cdl = field_cdl.sort_values("pct", ascending=False)
    crops = [f"{r['crop_name']} ({r['pct']:.0f}%)" for _, r in field_cdl.head(3).iterrows()]
    return ", ".join(crops)


def _filter_corn_fields(fields: list[dict], year: int) -> list[dict]:
    """Keep only fields where corn covers >50% of the field in the CDL year."""
    by_farm: dict[str, list[dict]] = {}
    for f in fields:
        by_farm.setdefault(f["farm_slug"], []).append(f)

    corn_ids: set[str] = set()
    for farm_slug, farm_fields in by_farm.items():
        if not farm_fields:
            continue
        grower = farm_fields[0]["grower_slug"]
        prefix = _cdl_table_prefix(farm_slug)
        cdl_path = (_GROWERS_ROOT / grower / "farms" / farm_slug
                    / "derived" / "tables" / f"{prefix}_{year}_cdl.csv")
        if not cdl_path.exists():
            continue
        try:
            cdl = pd.read_csv(cdl_path)
            cdl_corn = cdl[cdl["crop_name"].str.lower() == "corn"]
            corn_pct = cdl_corn.groupby("field_id")["pct"].sum()
            for fid, pct in corn_pct.items():
                if pct > 50:
                    corn_ids.add(fid)
        except Exception:
            continue

    return [f for f in fields if f["field_id"] in corn_ids]


def _parse_date_from_filename(path: Path) -> datetime | None:
    m = re.search(r"(\d{4})(\d{2})(\d{2})", path.stem)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def find_ndvi_scenes(field_dir: Path, year: int) -> list[tuple[datetime, Path]]:
    sat_dir = field_dir / "satellite" / "sentinel" / str(year)
    if not sat_dir.exists():
        return []
    results = []
    for scene_dir in sorted(sat_dir.iterdir()):
        if not scene_dir.is_dir():
            continue
        for ndvi_path in scene_dir.glob("*_ndvi.tif"):
            dt = _parse_date_from_filename(ndvi_path)
            if dt is not None:
                results.append((dt, ndvi_path))
    results.sort(key=lambda x: x[0])
    return results


def compute_ndvi_timeseries(
    scenes: list[tuple[datetime, Path]], boundary_path: Path
) -> pd.DataFrame:
    import geopandas as gpd
    import rasterio
    from rasterio.mask import mask

    if not boundary_path.exists():
        return pd.DataFrame()

    fields = gpd.read_file(boundary_path)
    field_geom = shape(fields.geometry.iloc[0])

    rows = []
    for dt, ndvi_path in scenes:
        try:
            with rasterio.open(ndvi_path) as src:
                out_image, _ = mask(src, [field_geom.__geo_interface__], crop=True, nodata=np.nan)
                data = out_image[0]
            valid = data[~np.isnan(data)]
            if valid.size == 0:
                continue
            rows.append({
                "date": dt,
                "doy": dt.timetuple().tm_yday,
                "mean_ndvi": float(np.mean(valid)),
                "min_ndvi": float(np.min(valid)),
                "max_ndvi": float(np.max(valid)),
                "std_ndvi": float(np.std(valid)),
                "pixel_count": int(valid.size),
            })
        except Exception:
            continue
    return pd.DataFrame(rows)


def load_etc(field_id: str, year: int) -> pd.DataFrame:
    if not _ETC_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(_ETC_CSV, parse_dates=["date"])
    df = df[df["field_id"] == field_id].copy()
    if df.empty:
        return df
    df = df[df["date"].dt.year == year].copy()
    if df.empty:
        return df
    df["doy"] = df["date"].dt.dayofyear
    return df


def _apply_xaxis(fig: go.Figure):
    fig.update_xaxes(
        range=[XAXIS_DOY_MIN, XAXIS_DOY_MAX],
        tickvals=sorted(MONTH_TICKS.keys()),
        ticktext=[MONTH_TICKS[v] for v in sorted(MONTH_TICKS.keys())],
        showgrid=True, gridcolor="#e0e0e0",
    )


def _frost_dates(df: pd.DataFrame) -> tuple[int | None, int | None]:
    below = df[df["T2M_MIN"] < 0]
    spring = below[below["doy"] <= 180]
    fall = below[below["doy"] > 180]
    last_frost = int(spring["doy"].max()) if not spring.empty else None
    first_frost = int(fall["doy"].min()) if not fall.empty else None
    return last_frost, first_frost


def _compute_dry_spells(df: pd.DataFrame) -> list[dict]:
    dry = df["precip_mm"] < DRY_SPELL_MAX_PRECIP_MM
    spells = []
    start = None
    for i, is_dry in enumerate(dry):
        if is_dry and start is None:
            start = i
        elif not is_dry and start is not None:
            length = i - start
            if length >= DRY_SPELL_MIN_DAYS:
                spells.append({"start_doy": int(df.iloc[start]["doy"]),
                               "end_doy": int(df.iloc[i - 1]["doy"]),
                               "duration_days": length})
            start = None
    if start is not None:
        length = len(df) - start
        if length >= DRY_SPELL_MIN_DAYS:
            spells.append({"start_doy": int(df.iloc[start]["doy"]),
                           "end_doy": int(df.iloc[-1]["doy"]),
                           "duration_days": length})
    return spells


# ---------------------------------------------------------------------------
# Plotly figure builders
# ---------------------------------------------------------------------------


def build_ndvi_figure(ndvi_df: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_CHART_THEME,
        title=f"NDVI Season Curve — {year}",
        xaxis_title="Month", yaxis_title="Mean NDVI",
        hovermode="x unified",
        height=250,
    )
    if ndvi_df.empty:
        fig.add_annotation(text="No NDVI data available", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    fig.add_trace(go.Scatter(
        x=ndvi_df["doy"], y=ndvi_df["mean_ndvi"],
        mode="lines+markers", name="Mean NDVI",
        line=dict(color="#2ca02c", width=2),
        marker=dict(size=7, color="#2ca02c", line=dict(color="white", width=1.5)),
    ))
    fig.add_trace(go.Scatter(
        x=ndvi_df["doy"], y=ndvi_df["mean_ndvi"] + ndvi_df["std_ndvi"],
        mode="lines", name="+1σ",
        line=dict(width=0), showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=ndvi_df["doy"], y=ndvi_df["mean_ndvi"] - ndvi_df["std_ndvi"],
        mode="lines", name="-1σ",
        line=dict(width=0), fillcolor="rgba(44,160,44,0.15)",
        fill="tonexty", showlegend=False,
    ))

    peak = ndvi_df.loc[ndvi_df["mean_ndvi"].idxmax()]
    fig.add_annotation(
        x=peak["doy"], y=peak["mean_ndvi"],
        text=f"Peak: {peak['mean_ndvi']:.3f}",
        showarrow=True, arrowhead=2, ax=10, ay=-30,
        font=dict(size=9, color="darkgreen"),
    )

    fig.update_xaxes(range=[XAXIS_DOY_MIN, XAXIS_DOY_MAX],
                     tickvals=sorted(MONTH_TICKS.keys()),
                     ticktext=[MONTH_TICKS[v] for v in sorted(MONTH_TICKS.keys())],
                     showgrid=True, gridcolor="#e0e0e0")
    fig.update_yaxes(range=[-0.1, 1.0], zeroline=True, zerolinecolor="#ccc")
    return fig


def build_gdd_figure(weather_df: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_CHART_THEME,
        title=f"GDD Accumulation (Base 50°F) — {year}",
        xaxis_title="Month", yaxis_title="Cumulative GDD (F-days)",
        hovermode="x unified",
        height=250,
    )
    if weather_df.empty:
        fig.add_annotation(text="No weather data", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    total = weather_df["gdd_cum_F"].iloc[-1]
    fig.add_trace(go.Scatter(
        x=weather_df["doy"], y=weather_df["gdd_cum_F"],
        mode="lines", name="GDD",
        line=dict(color="#d62728", width=2),
        fill="tozeroy", fillcolor="rgba(214,39,40,0.1)",
    ))

    last_frost, first_frost = _frost_dates(weather_df)
    ymax = weather_df["gdd_cum_F"].max() * 1.1
    if last_frost:
        fig.add_vline(x=last_frost, line_dash="dash", line_color="#9467bd", line_width=1)
        fig.add_annotation(x=last_frost, y=ymax * 0.95,
                           text=f"Last Frost DOY {last_frost}",
                           font=dict(size=9, color="#9467bd"), showarrow=False)
    if first_frost:
        fig.add_vline(x=first_frost, line_dash="dash", line_color="#9467bd", line_width=1)
        fig.add_annotation(x=first_frost, y=ymax * 0.85,
                           text=f"First Frost DOY {first_frost}",
                           font=dict(size=9, color="#9467bd"), showarrow=False)

    _apply_xaxis(fig)
    fig.update_yaxes(zeroline=True, zerolinecolor="#ccc")
    return fig


def build_temp_figure(weather_df: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_CHART_THEME,
        title=f"Daily Temperature — {year}",
        xaxis_title="Month", yaxis_title="Temperature (°F)",
        hovermode="x unified",
        height=250,
    )
    if weather_df.empty:
        fig.add_annotation(text="No weather data", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    fig.add_trace(go.Scatter(
        x=weather_df["doy"], y=weather_df["T2M_MAX_F"],
        mode="lines", name="Max",
        line=dict(width=0), showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=weather_df["doy"], y=weather_df["T2M_MIN_F"],
        mode="lines", name="Min-Max range",
        line=dict(width=0),
        fillcolor="rgba(230,85,13,0.15)",
        fill="tonexty",
    ))
    fig.add_trace(go.Scatter(
        x=weather_df["doy"], y=weather_df["T2M_F"],
        mode="lines", name="Mean",
        line=dict(color="#e6550d", width=2),
    ))

    fig.add_hline(y=90, line_dash="dot", line_color="#3182bd", line_width=0.8)

    heat = weather_df[weather_df["T2M_MAX_F"] > 90]
    if not heat.empty:
        fig.add_trace(go.Scatter(
            x=heat["doy"], y=heat["T2M_MAX_F"],
            mode="markers", name="Heat >90°F",
            marker=dict(color="red", size=5, symbol="triangle-down"),
        ))

    cold = weather_df[weather_df["T2M_MIN_F"] < 32]
    if not cold.empty:
        fig.add_trace(go.Scatter(
            x=cold["doy"], y=cold["T2M_MIN_F"],
            mode="markers", name="Cold <32°F",
            marker=dict(color="#3182bd", size=5, symbol="triangle-up"),
        ))

    _apply_xaxis(fig)
    fig.update_yaxes(zeroline=True, zerolinecolor="#ccc")
    return fig


def build_precip_figure(weather_df: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_CHART_THEME,
        title=f"Precipitation & Season Stress — {year}",
        xaxis_title="Month",
        hovermode="x unified",
        height=280,
    )
    if weather_df.empty:
        fig.add_annotation(text="No weather data", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    total_precip = weather_df["precip_in"].sum()

    spells = _compute_dry_spells(weather_df)
    for s in spells:
        fig.add_vrect(x0=s["start_doy"], x1=s["end_doy"],
                      fillcolor="orange", opacity=0.1, line_width=0,
                      annotation_text=f"Dry {s['duration_days']}d",
                      annotation_position="top left",
                      annotation=dict(font_size=8))

    fig.add_trace(go.Bar(
        x=weather_df["doy"], y=weather_df["precip_in"],
        name="Daily Precip", marker_color="#1f77b4", opacity=0.8,
    ))

    fig.add_trace(go.Scatter(
        x=weather_df["doy"], y=weather_df["precip_in"].cumsum(),
        mode="lines", name="Cumulative",
        line=dict(color="#e6550d", width=2),
        yaxis="y2",
    ))

    heavy = weather_df[weather_df["precip_in"] > 1.0]
    if not heavy.empty:
        fig.add_trace(go.Scatter(
            x=heavy["doy"], y=heavy["precip_in"],
            mode="markers+text", name="Heavy >1in",
            marker=dict(color="red", size=8, symbol="circle"),
            text=[f"{v:.1f}" for v in heavy["precip_in"]],
            textposition="top center",
            textfont=dict(size=8, color="darkred"),
        ))

    fig.update_layout(
        yaxis=dict(title="Precipitation (in)", zeroline=True, zerolinecolor="#ccc"),
        yaxis2=dict(title="Cumulative Precip (in)", overlaying="y", side="right",
                    tickfont=dict(color="#e6550d")),
    )
    _apply_xaxis(fig)
    return fig


def build_etc_figure(etc_df: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **_CHART_THEME,
        title=f"ETc & Water Balance (FAO-56) — {year}",
        xaxis_title="Month",
        hovermode="x unified",
        height=280,
    )
    if etc_df.empty:
        fig.add_annotation(text="No ETc data for this field/year", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    total_etc = etc_df["etc_mm"].sum()
    total_precip = etc_df["PRECTOTCORR"].sum()
    deficit = total_etc - total_precip

    stage_bands = [
        ("VE-VT", 134, 234),
        ("R1-R3", 234, 271),
        ("R4-R5", 271, None),
    ]
    stage_colors = {"VE-VT": "#2e7d32", "R1-R3": "#1565c0", "R4-R5": "#e65100"}

    for label, s_doy, e_doy in stage_bands:
        end = e_doy if e_doy else XAXIS_DOY_MAX
        fig.add_vrect(x0=s_doy, x1=end, fillcolor=stage_colors[label],
                      opacity=0.06, line_width=0,
                      annotation_text=label, annotation_position="top left",
                      annotation=dict(font=dict(size=8, color=stage_colors[label])))

    fig.add_trace(go.Bar(
        x=etc_df["doy"], y=etc_df["etc_mm"],
        name="Daily ETc", marker_color="#2e7d32", opacity=0.55,
    ))

    fig.add_trace(go.Scatter(
        x=etc_df["doy"], y=etc_df["etc_mm"].cumsum(),
        mode="lines", name="Cumulative ETc",
        line=dict(color="#1b5e20", width=2),
        yaxis="y2",
    ))

    fig.add_trace(go.Scatter(
        x=etc_df["doy"], y=etc_df["PRECTOTCORR"].cumsum(),
        mode="lines", name="Cumulative Precip",
        line=dict(color="#1565c0", width=2, dash="dash"),
        yaxis="y2",
    ))

    deficit_color = "darkred" if deficit > 0 else "darkgreen"
    deficit_label = f"Water deficit: +{deficit:.0f} mm" if deficit > 0 else f"Water surplus: {abs(deficit):.0f} mm"
    fig.add_annotation(
        xref="paper", yref="paper", x=0.98, y=0.95,
        text=deficit_label, showarrow=False,
        font=dict(size=10, color=deficit_color),
        bgcolor="white", bordercolor=deficit_color, borderwidth=1,
    )

    fig.update_layout(
        yaxis=dict(title="Daily ETc (mm)", zeroline=True, zerolinecolor="#ccc"),
        yaxis2=dict(title="Cumulative (mm)", overlaying="y", side="right",
                    tickfont=dict(color="#333")),
    )
    _apply_xaxis(fig)
    return fig


# ---------------------------------------------------------------------------
# Dash app
# ---------------------------------------------------------------------------

app = Dash(__name__, title="Corn Weather Stress Dashboard")

app.layout = html.Div(
    style={
        "fontFamily": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif",
        "margin": "0", "padding": "0", "background": "#f5f5f0", "minHeight": "100vh",
    },
    children=[
        dcc.Store(id="growers-store"),
        dcc.Store(id="fields-store"),
        dcc.Store(id="boundary-store"),

        html.Div(
            style={
                "background": "linear-gradient(135deg, #1B5E20, #2E7D32)",
                "padding": "1.2rem 2rem", "color": "white",
            },
            children=[
                html.H1("Corn Weather Stress Dashboard",
                        style={"margin": "0", "fontSize": "1.5rem", "fontWeight": 600}),
                html.P("Select a grower and field to visualize weather stress indicators",
                       style={"margin": "0.3rem 0 0", "fontSize": "0.85rem", "opacity": 0.85}),
            ],
        ),

        html.Div(
            style={
                "position": "relative", "zIndex": 2000,
                "display": "flex", "gap": "1rem",
                "padding": "1rem 2rem", "background": "white",
                "borderBottom": "1px solid #e0e0e0",
                "flexWrap": "wrap", "alignItems": "flex-end",
            },
            children=[
                html.Div(style={"flex": "1", "minWidth": "200px"}, children=[
                    html.Label("Grower", style={"fontSize": "0.75rem", "fontWeight": 600,
                               "color": "#555", "marginBottom": "0.25rem", "display": "block"}),
                    dcc.Dropdown(id="grower-dropdown", placeholder="Select grower..."),
                ]),
                html.Div(style={"flex": "2", "minWidth": "250px"}, children=[
                    html.Label("Field", style={"fontSize": "0.75rem", "fontWeight": 600,
                               "color": "#555", "marginBottom": "0.25rem", "display": "block"}),
                    dcc.Dropdown(id="field-dropdown", placeholder="Select field..."),
                ]),
                html.Div(style={"flex": "0", "minWidth": "120px"}, children=[
                    html.Label("Year", style={"fontSize": "0.75rem", "fontWeight": 600,
                               "color": "#555", "marginBottom": "0.25rem", "display": "block"}),
                    dcc.Dropdown(id="year-dropdown", options=YEAR_OPTIONS,
                                 value=2025, clearable=False),
                ]),
            ],
        ),

        html.Div(
            style={"padding": "1rem 2rem"},
            children=[
                html.Div(
                    style={"position": "relative"},
                    children=[
                        html.Div(id="crop-header", style={
                            "position": "absolute", "top": "6px", "left": "6px",
                            "right": "6px", "zIndex": 500,
                            "background": "rgba(0,0,0,0.55)", "color": "white",
                            "padding": "6px 14px", "fontSize": "0.85rem",
                            "borderRadius": "6px", "textAlign": "center",
                            "pointerEvents": "none",
                        }, children=""),
                        html.Div(id="map-container", style={
                            "borderRadius": "8px", "overflow": "hidden",
                            "boxShadow": "0 2px 12px rgba(0,0,0,0.12)",
                        }),
                    ],
                ),
            ],
        ),

        html.Div(id="field-info", style={
            "padding": "0.5rem 2rem 0", "fontSize": "0.85rem", "color": "#555",
        }, children="No field selected"),

        html.Div(
            id="stress-panels",
            style={"padding": "1rem 2rem 2rem"},
            children=[
                html.Div(
                    style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "1rem"},
                    children=[
                        html.Div(children=[dcc.Loading(dcc.Graph(id="ndvi-graph"))]),
                        html.Div(children=[dcc.Loading(dcc.Graph(id="gdd-graph"))]),
                        html.Div(children=[dcc.Loading(dcc.Graph(id="temp-graph"))]),
                        html.Div(children=[dcc.Loading(dcc.Graph(id="precip-graph"))]),
                    ],
                ),
                html.Div(
                    style={"marginTop": "1rem"},
                    children=[dcc.Loading(dcc.Graph(id="etc-graph"))],
                ),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@app.callback(
    Output("growers-store", "data"),
    Input("growers-store", "id"),
    prevent_initial_call=False,
)
def _load_growers(_) -> list[dict]:
    return discover_growers()


@app.callback(
    Output("grower-dropdown", "options"),
    Output("grower-dropdown", "value"),
    Input("growers-store", "data"),
)
def _populate_growers(growers: list[dict]) -> tuple[list, str | None]:
    if not growers:
        return [], None
    return [{"label": g["display_name"], "value": g["slug"]} for g in growers], growers[0]["slug"]


@app.callback(
    Output("fields-store", "data"),
    Output("field-dropdown", "options"),
    Output("field-dropdown", "value"),
    Input("grower-dropdown", "value"),
    Input("year-dropdown", "value"),
    prevent_initial_call=True,
)
def _load_fields_for_grower(grower_slug: str, year: int) -> tuple[list, list, str | None]:
    if not grower_slug or not year:
        return [], [], None
    fields = discover_fields(grower_slug)
    corn_fields = _filter_corn_fields(fields, year)
    if not corn_fields:
        return fields, [], None
    options = []
    for f in corn_fields:
        label = f["field_id"]
        extras = []
        if f.get("farm_display"):
            extras.append(f["farm_display"])
        if f.get("area_acres"):
            extras.append(f"{f['area_acres']:.1f} ac")
        if extras:
            label += f"  ({', '.join(extras)})"
        options.append({"label": label, "value": f["field_id"]})
    default = options[0]["value"] if options else None
    return corn_fields, options, default


@app.callback(
    Output("map-container", "children"),
    Output("field-info", "children"),
    Output("boundary-store", "data"),
    Output("crop-header", "children"),
    Input("field-dropdown", "value"),
    Input("year-dropdown", "value"),
    State("fields-store", "data"),
    prevent_initial_call=True,
)
def _update_map(field_id: str | None, year: int | None, fields: list[dict] | None) -> tuple:
    if not field_id or not fields or not year:
        return (Map(center=[42.0, -95.0], zoom=5,
                    children=[TileLayer(url=ESRI_SATELLITE, attribution="Esri"),
                              TileLayer(url=ESRI_LABELS, attribution="Esri")],
                    style={"width": "100%", "height": "550px"}),
                "No field selected", None, "")

    field = next((f for f in fields if f["field_id"] == field_id), None)
    if not field:
        return (Map(center=[42.0, -95.0], zoom=5,
                    children=[TileLayer(url=ESRI_SATELLITE, attribution="Esri")],
                    style={"width": "100%", "height": "550px"}),
                f"Field {field_id} not found", None, "")

    coords = load_boundary_geojson(field["geojson_path"])
    if not coords:
        return (Map(center=[42.0, -95.0], zoom=5,
                    children=[TileLayer(url=ESRI_SATELLITE, attribution="Esri")],
                    style={"width": "100%", "height": "550px"}),
                f"Could not load boundary for {field_id}", None, "")

    latlng_coords = [[c[1], c[0]] for c in coords]
    min_lat, min_lng, max_lat, max_lng = compute_padded_bounds(coords)
    center = [(min_lat + max_lat) / 2, (min_lng + max_lng) / 2]

    boundary = Polygon(positions=latlng_coords, color="#FFD700", weight=4,
                       fillColor="#FFD700", fillOpacity=0.25)

    centroid_lat = sum(c[1] for c in coords) / len(coords)
    centroid_lng = sum(c[0] for c in coords) / len(coords)

    m = Map(
        center=center, zoom=15,
        children=[
            TileLayer(url=ESRI_SATELLITE, attribution="Esri"),
            TileLayer(url=ESRI_LABELS, attribution="Esri"),
            boundary,
            Marker(position=[centroid_lat, centroid_lng],
                   children=[Popup(children=[html.Div([
                       html.B(field["field_id"]), html.Br(),
                       f"Farm: {field['farm_display']}", html.Br(),
                       f"Acres: {field['area_acres']:.1f}", html.Br(),
                       f"Crop: {field['crop']}", html.Br(),
                       f"Irrigation: {field['irrigation']}", html.Br(),
                       f"County: {field['county']}",
                   ])])]),
        ],
        style={"width": "100%", "height": "550px"},
        bounds=[[min_lat, min_lng], [max_lat, max_lng]],
    )

    info = html.Div([
        html.Span(f"Field: {field['field_id']}",
                  style={"fontWeight": 600, "color": "#1B5E20"}),
        html.Span(f"  |  Farm: {field['farm_display']}"),
        html.Span(f"  |  Area: {field['area_acres']:.1f} ac"),
        html.Span(f"  |  County: {field['county']}"),
        html.Span(f"  |  Irrigation: {field['irrigation']}"),
    ])

    crop_label = load_cdl_crop(field["grower_slug"], field["farm_slug"], field["field_id"], year)
    if crop_label:
        dominant = crop_label.split(",")[0].strip()
        crop_icon = "🌽" if dominant.startswith("Corn") else "🌱"
        crop_header = f"{crop_icon} {crop_label}"
    else:
        crop_header = "No CDL data"

    return m, info, field, crop_header


@app.callback(
    Output("ndvi-graph", "figure"),
    Output("gdd-graph", "figure"),
    Output("temp-graph", "figure"),
    Output("precip-graph", "figure"),
    Output("etc-graph", "figure"),
    Input("grower-dropdown", "value"),
    Input("field-dropdown", "value"),
    Input("year-dropdown", "value"),
    State("fields-store", "data"),
    prevent_initial_call=True,
)
def _update_stress_panels(
    grower_slug: str | None,
    field_id: str | None,
    year: int | None,
    fields: list[dict] | None,
) -> tuple:
    def _empty_fig(text: str = "Select a field and year"):
        fig = go.Figure()
        fig.update_layout(**_CHART_THEME, height=250)
        fig.add_annotation(text=text, showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    if not grower_slug or not field_id or not year or not fields:
        return (_empty_fig(), _empty_fig(), _empty_fig(), _empty_fig(), _empty_fig())

    field = next((f for f in fields if f["field_id"] == field_id), None)
    if not field:
        return (_empty_fig(), _empty_fig(), _empty_fig(), _empty_fig(), _empty_fig())

    try:
        weather_df = load_weather(field, year)
    except Exception:
        print(f"  [ERROR] load_weather: {field_id}/{year}")
        traceback.print_exc()
        weather_df = pd.DataFrame()

    try:
        field_dir = Path(field["geojson_path"]).parent.parent
        if field_dir.exists():
            scenes = find_ndvi_scenes(field_dir, year)
            ndvi_df = compute_ndvi_timeseries(scenes, Path(field["geojson_path"])) if scenes else pd.DataFrame()
        else:
            ndvi_df = pd.DataFrame()
    except Exception:
        print(f"  [ERROR] NDVI: {field_id}/{year}")
        traceback.print_exc()
        ndvi_df = pd.DataFrame()

    try:
        etc_df = load_etc(field_id, year)
    except Exception:
        print(f"  [ERROR] load_etc: {field_id}/{year}")
        traceback.print_exc()
        etc_df = pd.DataFrame()

    try:
        return (
            build_ndvi_figure(ndvi_df, year),
            build_gdd_figure(weather_df, year),
            build_temp_figure(weather_df, year),
            build_precip_figure(weather_df, year),
            build_etc_figure(etc_df, year),
        )
    except Exception:
        print(f"  [ERROR] build_figures: {field_id}/{year}")
        traceback.print_exc()
        return (_empty_fig("Error loading data"), _empty_fig("Error loading data"),
                _empty_fig("Error loading data"), _empty_fig("Error loading data"),
                _empty_fig("Error loading data"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Corn Weather Stress Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Dash server port")
    parser.add_argument("--debug", action="store_true", default=False, help="Enable debug mode")
    args = parser.parse_args()

    print(f"  Data root: {_DATA_PIPELINE_ROOT}")
    print(f"  Starting Corn Weather Stress Dashboard on port {args.port}...")
    print(f"  Open http://127.0.0.1:{args.port} in your browser")
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
