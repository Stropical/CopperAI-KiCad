Using part_finder MCP
- When: before locking schematics/PCB footprints or ordering parts; whenever unsure about package options or current stock/price; when swapping to an alternate vendor part.
- What to supply: part name/function, key params (voltage/current/frequency/tolerance), desired package, voltage rails, target qty, acceptable alternates/series, and must-have certifications.
- How to call: ask part_finder MCP with MPN or spec query; request package + supplier availability + lifecycle; prefer parametric filters to avoid broad matches.
- Footprint confirmation: use returned package info (e.g., SOIC-8, QFN-24 4x4) to select matching KiCad footprint; watch for pitch, exposed pad size, and pin-1 marking orientation.
- Stock sanity: check at least two distributors; note NCNR or factory lead time; capture pricing break relevant to build size.
- Alternates: shortlist pin-compatible parts in same package; record drop-in vs requires-passive-change notes.
- Record keeping: paste finder output into design notes/BOM draft; include chosen MPN, package, vendor SKU, and links for later ERC/BOM automation.
