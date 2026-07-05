# Local Instructions

## Purpose

This folder owns the Field Season Weather Evaluation workflow — a multi‑source dashboard that combines CDL, Sentinel‑2 NDVI, and NASA POWER daily weather into a single‑year field season report.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly asks for a broader skill change. Do not change parent `SKILL.md`, sibling weather workflows, or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. If routing context is needed, read `../INDEX.md` and `../../SKILL.md`.

## Local validation

Run `./scripts/validate.sh` from the repository root after structural changes. To smoke‑test the dashboard against the example field:

```bash
cd ../..
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
/home/coder/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
  weather/field-season-weather-evaluation/scripts/field_season_dashboard.py \
  --year 2022
```

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or root files. Do not duplicate root-wide asset, vendor, or validation policy here except this pointer to `../../../AGENTS.md`.
