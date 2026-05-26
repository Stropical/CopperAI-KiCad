**Skill:** `datasheet_research` — Datasheet research  
**Tools:** `fetch_component_datasheets`, `batch_get_component_data`, `get_component_data`

- Pull datasheets when pinouts, limits, ratings, compensation/stability, startup behavior, protocol compatibility, or recommended support circuitry must match documentation.
- Datasheet confirmation is expected before finalizing a new or replacement IC, regulator, power path, interface transceiver, protection device, or any request where the wrong part would likely be electrically wrong.
- For simple local passives, do not block on datasheet mining when the value and function are already clear from the surrounding circuit or an existing design pattern.
- Read only the critical facts needed to proceed: operating range, required external components, pin usage, and one or two application-circuit constraints. Do not mine PDFs aimlessly.
- Use `batch_get_component_data` / `get_component_data` for symbol pin and field facts after you know which ref or lib:symbol you care about.
- For ref-specific datasheet requests (for example "U2"), verify that the referenced component exists before fetching PDFs; if it does not exist, stop immediately and report that exact ref was not found.
- Do not block simple placement tasks on datasheet mining unless the user required verified parameters.
