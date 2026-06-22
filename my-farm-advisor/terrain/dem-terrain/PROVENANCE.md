# DEM Terrain Provenance

This file records the source hierarchy and policy decisions for the My Farm Advisor DEM terrain package. It is a documentation and contract record only. Provider adapters, downloads, raster processing, and runtime manifests are later tasks.

## Source Hierarchy

### USGS 3DEP and TNMAccess

- Role: primary United States root source for field-level DEM terrain work.
- Policy: prefer USGS TNM 3DEP 1 meter direct GeoTIFF discovery and download when AOI coverage exists. Fall back to USGS 10 meter, then USGS 30 meter when finer coverage is unavailable.
- Rationale: best first source for U.S. farm fields because it is direct, no-auth, public, and generally better suited to field-scale terrain derivatives than global 30 meter products.
- Runtime policy: downloaded GeoTIFFs and derived products belong under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`, never in this repository.

### Illinois Height Modernization Program and ISGS

- Role: Illinois-specific high-quality source candidate.
- Policy: include Illinois ILHMP/ISGS when the AOI intersects Illinois and the candidate is equal or better by resolution, recency, and surface type.
- Rationale: Illinois farm fields can have state or program products that match or improve on generic national coverage.
- Caveat: programmatic access can vary by dataset and service. Adapters must preserve URLs, citation text, license notes, acquisition dates, and any coverage limits.

### NASADEM

- Role: global fallback.
- Policy: use NASADEM after registered national or regional providers when no better local source exists.
- Rationale: NASADEM provides broad global elevation coverage and is a useful fallback for fields outside U.S. provider coverage.
- Warning: 30 meter global data can be too coarse for some field-level terrain products. Selections at this scale need quality warnings.

### Copernicus GLO-30

- Role: global DSM fallback.
- Policy: use Copernicus GLO-30 after NASADEM when it is the best available candidate for the AOI.
- Rationale: it provides broad global coverage and can be useful when better DTM or DEM sources are missing.
- Warning: it is a DSM fallback. Elevations may include vegetation, buildings, or other above-ground objects and must not be represented as bare-earth DTM.

### ALOS AW3D30

- Role: global DSM fallback.
- Policy: use ALOS AW3D30 after Copernicus GLO-30 when it is the best available candidate for the AOI.
- Rationale: it provides another global 30 meter source for areas where other sources are unavailable or unsuitable.
- Warning: it is a DSM fallback. Elevations may include vegetation, buildings, or other above-ground objects and must not be represented as bare-earth DTM.

### OpenTopography

- Role: optional configured convenience source.
- Policy: do not require OpenTopography for default operation. Include it only when explicitly configured and allowed by the resolver policy.
- Rationale: OpenTopography can simplify access to some elevation products, but the core skill should not depend on a convenience portal or required credentials.
- Runtime policy: preserve source dataset provenance, not only the portal URL, when an adapter uses it.

### Open-Elevation GitHub Materials

- Role: research-only reference.
- Policy: do not use the hosted Open-Elevation API for discovery, ranking, download, or elevation sampling.
- Rationale: the project bundle is based on CGIAR SRTM 250 meter data, which is too coarse for the field-level DEM terrain target. Its GPL-2.0 code must not be copied, vendored, or partially imported into this Apache-2.0 skill catalog.
- Allowed use: read public materials for background research only, with no code import and no hosted API dependency.

## Policy Decisions

- Prefer bare-earth DTM, then DEM, then DSM.
- Prefer finer resolution after AOI coverage and surface type are considered.
- Preserve candidate provenance for all ranked sources, not just the selected source.
- Do not fail solely because only 30 meter coverage exists. Return the best raster candidate with a quality warning.
- Mark DSM fallbacks clearly so downstream farm analysis does not mistake vegetation or structure elevations for ground terrain.
- Keep all generated and downloaded DEM assets out of Git.

## Validation

Run from the repository root after documentation or routing changes:

```bash
./scripts/validate.sh
```
