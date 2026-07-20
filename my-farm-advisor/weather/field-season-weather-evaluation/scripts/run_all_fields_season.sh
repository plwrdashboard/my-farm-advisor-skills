#!/usr/bin/env bash
# Batch runner: field-season-weather-evaluation across all runtime fields.
#
# Scans ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/
# dynamically and runs field_season_dashboard.py for each (field, year) in the
# target window (default 2021-2025). Idempotent: skips (field, year) when both
# the dashboard PNG and summary JSON already exist.
#
# Usage:
#   DATA_PIPELINE_DATA_ROOT=/path ./run_all_fields_season.sh [years...]
#
# Defaults point at the canonical my-farm-advisor-runtime data root and years
# 2021-2025. Re-running resumes cheaply — completed (field, year) pairs are
# skipped via output-file presence checks.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD="${SCRIPT_DIR}/field_season_dashboard.py"

export DATA_PIPELINE_DATA_ROOT="${DATA_PIPELINE_DATA_ROOT:-/home/coder/my-farm-advisor-runtime}"
PY="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python"
ROOT="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers"

# Default target years (2021-2025); override via positional args.
if [ "$#" -gt 0 ]; then
  TARGET_YEARS=("$@")
else
  TARGET_YEARS=(2021 2022 2023 2024 2025)
fi

TS=$(date +%Y%m%d_%H%M%S)
LOG="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/runs_field_season_${TS}.log"
echo "Batch run ${TS}" | tee "$LOG"
echo "Data root:   ${DATA_PIPELINE_DATA_ROOT}" | tee -a "$LOG"
echo "Target years: ${TARGET_YEARS[*]}" | tee -a "$LOG"
echo "Dashboard:   ${DASHBOARD}" | tee -a "$LOG"
echo "Python:      ${PY}" | tee -a "$LOG"
echo "Repo:        https://github.com/plwrdashboard/my-farm-advisor-skills" | tee -a "$LOG"

if [ ! -x "${PY:-}" ] && [ ! -f "${PY}" ]; then
  echo "ERROR: venv python not found at ${PY}" | tee -a "$LOG"
  exit 2
fi
if [ ! -f "$DASHBOARD" ]; then
  echo "ERROR: dashboard script not found at $DASHBOARD" | tee -a "$LOG"
  exit 2
fi

ok=0; fail=0; skipped_resume=0; skipped_nodata=0; total=0
declare -a FAILURES

for grower_dir in "$ROOT"/*/; do
  [ -d "$grower_dir" ] || continue
  grower=$(basename "$grower_dir")
  farms_dir="$grower_dir/farms"
  [ -d "$farms_dir" ] || continue
  for farm_dir in "$farms_dir"/*/; do
    [ -d "$farm_dir" ] || continue
    farm=$(basename "$farm_dir")
    fields_dir="$farm_dir/fields"
    [ -d "$fields_dir" ] || continue
    for field_dir in "$fields_dir"/*/; do
      [ -d "$field_dir" ] || continue
      field=$(basename "$field_dir")
      weather_csv="$field_dir/weather/daily_weather.csv"
      sat_dir="$field_dir/satellite/sentinel"
      reports_dir="$field_dir/derived/reports"
      summaries_dir="$field_dir/derived/summaries"

      # Determine weather years from date column (col 4: field_id,lat,lon,date,...)
      w_years=()
      if [ -f "$weather_csv" ] && [ "$(wc -l < "$weather_csv")" -gt 1 ]; then
        w_years=( $(awk -F, 'NR>1{print substr($4,1,4)}' "$weather_csv" | sort -u) )
      fi
      # Determine sentinel years (numeric subdirs only)
      s_years=()
      if [ -d "$sat_dir" ]; then
        s_years=( $(ls "$sat_dir" 2>/dev/null | grep -E '^[0-9]{4}$' | sort -u) )
      fi

      for year in "${TARGET_YEARS[@]}"; do
        total=$((total+1))
        has_w=0; has_s=0
        for y in "${w_years[@]+"${w_years[@]}"}"; do [ "$y" = "$year" ] && has_w=1; done
        for y in "${s_years[@]+"${s_years[@]}"}"; do [ "$y" = "$year" ] && has_s=1; done

        if [ "$has_w" -eq 0 ] && [ "$has_s" -eq 0 ]; then
          skipped_nodata=$((skipped_nodata+1))
          echo "SKIP (no data)   $grower/$farm/$field $year" | tee -a "$LOG"
          continue
        fi

        png="$reports_dir/field_season_dashboard_${year}.png"
        json="$summaries_dir/field_season_summary_${year}.json"
        if [ -f "$png" ] && [ -f "$json" ]; then
          skipped_resume=$((skipped_resume+1))
          echo "SKIP (resume)    $grower/$farm/$field $year" | tee -a "$LOG"
          continue
        fi

        echo "RUN              $grower/$farm/$field $year" | tee -a "$LOG"
        if "$PY" "$DASHBOARD" --grower "$grower" --farm "$farm" --field "$field" --year "$year" >>"$LOG" 2>&1; then
          ok=$((ok+1))
        else
          fail=$((fail+1))
          FAILURES+=("$grower/$farm/$field $year")
          echo "  FAILED (exit $?) -> $grower/$farm/$field $year" | tee -a "$LOG"
        fi
      done
    done
  done
done

echo "---------------------------" | tee -a "$LOG"
echo "Total tasks:        $total" | tee -a "$LOG"
echo "Successful:         $ok" | tee -a "$LOG"
echo "Skipped (resume):   $skipped_resume" | tee -a "$LOG"
echo "Skipped (no data):  $skipped_nodata" | tee -a "$LOG"
echo "Failed:             $fail" | tee -a "$LOG"
if [ "$fail" -gt 0 ]; then
  echo "Failures:" | tee -a "$LOG"
  for f in "${FAILURES[@]}"; do echo "  - $f" | tee -a "$LOG"; done
fi
echo "Log: $LOG" | tee -a "$LOG"
echo "Batch complete: ${TS}" | tee -a "$LOG"