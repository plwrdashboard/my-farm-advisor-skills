# geoadmin-admin

_Repo-native skill scaffold for shared geoadmin assets._

---

## 📋 Purpose

This workflow owns shared geoadmin data handling for the repository, including canonical Level 0, Level 1, and Level 2 admin roots under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared/geoadmin/`.

## 📦 Scope

- Shared geoadmin root discovery
- County/state/country admin layout helpers
- Future standardization and source-vintage metadata work

## 🔗 Integration

- Code lives under `my-farm-advisor/admin/geoadmin-admin/src/`
- Shared outputs live under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared/geoadmin/`
- Annual maturity scripts consume this skill through repo-native path helpers and bootstrap code
