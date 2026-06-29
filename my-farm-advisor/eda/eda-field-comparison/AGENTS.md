# Local Instructions

## Purpose

This folder owns field-level comparison workflows for analyzing boundaries, crop classification, weather, and geospatial patterns across growers, farms, and individual fields.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly asks for a broader skill change. Do not change parent `SKILL.md`, sibling EDA workflows, or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. If routing context is needed, read `../INDEX.md` and `../../SKILL.md`.

## Local validation

Run scripts against the smallest available sample when dependencies are available. If a script fails, check that the runtime data root contains the expected `growers/<grower>/farms/<farm>/` hierarchy.

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or root files. Do not duplicate root-wide asset, vendor, or validation policy here except this pointer to `../../../AGENTS.md`.
