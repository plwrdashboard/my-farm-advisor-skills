# Field Season Weather Evaluation — Examples

This skill works with any field that has the canonical data pipeline structure. Example usage:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime

# 2022 — Corn year for field osm-1491018233
/home/coder/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
  ../scripts/field_season_dashboard.py --year 2022

# 2021 — Soybean year for the same field
/home/coder/my-farm-advisor-runtime/data-pipeline/.venv/bin/python \
  ../scripts/field_season_dashboard.py --year 2021
```

See `GUIDE.md` for the full workflow.
