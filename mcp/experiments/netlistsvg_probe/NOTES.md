# netlistsvg probe

## What it is actually doing

`netlistsvg` is not computing schematic placement itself in an analog/KiCad sense.
It does this pipeline:

1. Parse a Yosys `write_json` netlist.
2. Flatten it into `cells` and `wires`.
3. Build an ELK graph from those cells/wires.
4. Hand that graph to ELK for layered layout.
5. Render the ELK output graph back into SVG.

The key code path is:

- `lib/index.ts`: `render()` and `dumpLayout()` call `buildElkGraph(...)` and then `elk.layout(...)`.
- `lib/elkGraph.ts`: creates ELK `children` and `edges`.
- `lib/Cell.ts`: node sizes and fixed port positions come from the SVG skin.
- `lib/Skin.ts`: reads `s:layoutEngine` and other layout properties from the skin SVG.

## Layout behavior

### ELK is doing the dynamic placement

- `lib/index.ts:32-45` exports the graph before/after ELK.
- `lib/index.ts:48-63` calls `elk.layout(kgraph, { layoutOptions: layoutProps.layoutEngine })`.

### Node geometry comes from the skin

- `lib/Cell.ts:193-236`
  - generic/join/split cells get width from the skin and height from `getGenericHeight()`
  - layout uses `org.eclipse.elk.portConstraints = FIXED_POS`
- `lib/Cell.ts:238-255`
  - normal cells use explicit port `x/y` from the SVG skin

### Wiring graph generation

- `lib/elkGraph.ts:87-173`
  - converts wires to ELK edges
  - inserts dummy nodes for fanout/fanin cases
- `lib/elkGraph.ts:193-233`
  - emits ELK extended edges
  - sets layered priority and edge thickness

### Default layout options

- `lib/default.svg:5-10`
  - layered layout
  - node spacing between layers = `35`
  - node-node spacing = `35`
  - layering strategy = `LONGEST_PATH`
- `lib/analog.svg:4-12`
  - disables constants and split/join insertion
  - uses `org.eclipse.elk.direction="DOWN"`
  - tighter inter-layer spacing = `5`

## What I tested

## Installed locally

```bash
cd mcp/experiments/netlistsvg_probe/netlistsvg
npm install --legacy-peer-deps
```

## Working built-in examples

```bash
node bin/netlistsvg.js test/digital/up3down5.json -o ../out_up3down5.svg
node bin/netlistsvg.js test/analog/resistor_divider.json -o ../out_resistor_divider.svg --skin lib/analog.svg
node bin/exportLayout.js test/digital/up3down5.json -o ../out_up3down5_layout.json --pre
node bin/exportLayout.js test/digital/up3down5.json -o ../out_up3down5_layout_post.json
```

Artifacts created:

- `out_up3down5.svg`
- `out_up3down5.png`
- `out_resistor_divider.svg`
- `out_resistor_divider.png`
- `out_up3down5_layout.json`
- `out_up3down5_layout_post.json`

## Data-folder examples I tried

### KiCad `.net`

Command:

```bash
node bin/netlistsvg.js /Users/ethanmarreel/Downloads/kicad-9.0.7/qa/data/eeschema/netlists/bus_entries/bus_entries.net -o ../out_bus_entries.svg
```

Result:

- Fails immediately because the file is not JSON.
- Error starts at byte 1 with `invalid character '('`.

### sch2py JSON circuit artifact

Command:

```bash
node bin/netlistsvg.js /Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/sch2py/ir/devbisme__skidl/master/tests/examples/netlist_to_skidl/kicad_project/resistor_divider.json -o ../out_skidl_resistor_divider.svg
```

Result:

- Fails schema validation.
- `netlistsvg` requires a top-level `modules` property because it expects Yosys JSON, not a KiCad-style `{components, nets, labels}` structure.

## Bottom line

`netlistsvg` is laying out components dynamically by delegating to **ELK layered layout**.
Its "dynamic" behavior comes from:

- graph construction in `lib/elkGraph.ts`
- skin-defined node/port geometry in `lib/Cell.ts` and the skin SVG
- ELK layout options embedded in the skin SVG

It will only work directly on **Yosys JSON** inputs. The KiCad data-folder circuits I tried are not in that format, so they are not directly renderable by `netlistsvg` without a conversion step.
