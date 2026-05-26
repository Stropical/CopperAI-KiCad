# OCS Tooling Notes

Date: 2026-03-17
Project: TestProject (KiCad)

## What worked
- `skip.Schematic(...)` was reliable for reading and editing `.kicad_sch`.
- Pin-level checks with `symbol.<ref>.pin.<pin>.attached_wires` were useful to validate actual connectivity.
- `schem.write(path)` requires an explicit file path in this environment.
- Adding/modifying global labels and wires via `skip` worked for net-clarity cleanup.

## What did not work
- Copper AI MCP endpoints responded with `no handler available` for:
  - open-docs
  - schematic summary
  - ERC/dangling report
- Result: Copper MCP was unavailable for live schematic ops in this run.

## Practical cautions
- `skip` object fields are not all settable the same way; setting `fields_autoplaced.value = True` caused a KeyError here.
- `skip` pin attribute names can vary (e.g., not always `n1`/`n2` for all symbols). Use iteration when uncertain.
- Validate edited power paths by checking wires on each critical pin (connector input, protection device, regulator VIN/GND/VOUT).

## Verified fix pattern used
- Reworked diode from incorrect shunt-like usage into true series input protection.
- Removed obsolete shunt branch wire and stale junction.
- Added explicit power net labels for professionalism/readability (`VIN_RAW`, `VIN`, `VOUT_12V`).
