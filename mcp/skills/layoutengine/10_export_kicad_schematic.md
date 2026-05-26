**Tool:** `export_kicad_schematic`

- Requires prior placements (`auto_place` or `relayout_target`). Export will fail if placements are empty.
- Pass **`assetBaseUrl`** when the relay does not set `LAYOUTENGINE_ASSET_BASE_URL` (symbol HTTP root, usually `…/public/kicad-symbols-upstream/`).
- After export, tell the user to **open or reload** the `.kicad_sch` in KiCad and re-save if they continue editing live.
- If export is blocked for missing symbols, fix the symbol tree under the asset root or adjust `assetBaseUrl`—do not silently switch libraries.
