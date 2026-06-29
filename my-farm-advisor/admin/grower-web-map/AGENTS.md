# Local Instructions

## Purpose

This folder owns the grower-level interactive web map workflow. It aggregates all farms under a grower into a single self-contained Leaflet.js HTML map with field boundaries, soil data, crop history, NDVI metrics, and farm-level statistics.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly asks for a broader skill change. Do not change parent `SKILL.md`, sibling workflows, or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first, then `examples/README.md` for usage patterns. If routing context is needed, read `../INDEX.md` and `../../SKILL.md`.

## Local validation

Run the pipeline generate script against a real grower:
```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
python /home/coder/my-farm-advisor-runtime/data-pipeline/src/scripts/reporting/generate_grower_web_map.py --grower-slug plwr
```
Verify the output HTML opens in a browser and shows all farms with interactive layers.

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or root files. Do not duplicate root-wide asset, vendor, or validation policy here except this pointer to `../../../AGENTS.md`.
