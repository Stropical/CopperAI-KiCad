# KiCad Schematic Rendering Reference

Consolidated from deep analysis of [kicanvas](https://github.com/theacodes/kicanvas) source code and KiCad 9 .kicad_sch files.

---

## 1. Coordinate Systems

### Two coordinate systems coexist

| System | Y direction | Used by |
|--------|-------------|---------|
| **Library space** | Y-up (positive Y = upward) | Symbol definitions in `lib_symbols`, pin positions, graphic primitives |
| **Schematic space** | Y-down (positive Y = downward) | Instance positions, wire endpoints, label positions, property positions |

**Units**: Everything in the .kicad_sch file is in **millimeters**. KiCad internally uses Internal Units (IU) at 10,000 IU per mm for precision, but the file format stores mm.

### The critical Y-flip

The 0-degree symbol transform is NOT identity — it includes a Y-flip to convert from library Y-up to schematic Y-down:

```
0°:   [1,  0,  0, -1]   (NOT identity)
90°:  [0, -1, -1,  0]
180°: [-1, 0,  0,  1]
270°: [0,  1,  1,  0]
```

This is a 2×2 matrix `[x1, y1, x2, y2]` applied as:
```
world_x = x1 * local_x + y1 * local_y + instance_x
world_y = x2 * local_x + y2 * local_y + instance_y
```

### Mirror (applied after rotation)

- `mirror "y"` (horizontal flip): negate x1 and y1
- `mirror "x"` (vertical flip): negate x2 and y2

---

## 2. .kicad_sch File Structure

```
(kicad_sch
  (version 20250114)
  (generator "eeschema")
  (uuid "...")
  (paper "A4")                    # A4=297×210, A3=420×297, Letter=279.4×215.9

  (lib_symbols                    # Complete symbol definitions (self-contained cache)
    (symbol "Device:R" ...)
    (symbol "power:GND" (power) ...)
  )

  (junction (at x y) (diameter 0) (uuid "..."))
  (wire (pts (xy x1 y1) (xy x2 y2)) (stroke ...) (uuid "..."))
  (label "name" (at x y rot) (effects ...) (uuid "..."))
  (global_label "name" (shape input) (at x y rot) ... (uuid "..."))
  (symbol (lib_id "Device:R") (at x y rot) (mirror x) (unit 1) ...
    (property "Reference" "R1" (at rx ry ra) (effects ...))
    (property "Value" "10k" (at vx vy va) (effects ...))
    (pin "1" (uuid "..."))
    (pin "2" (uuid "..."))
    (instances (project "" (path "/" (reference "R1") (unit 1))))
  )

  (sheet_instances (path "/" (page "1")))
)
```

### Key parsing details

- **Rotations**: Only 0, 90, 180, 270 degrees. No arbitrary rotation.
- **Properties have their own position**: Each `(property ...)` has `(at x y angle)` and `(effects ...)` storing the exact text position, font size, justification, and visibility.
- **Power symbols**: Regular symbols with `(power)` flag in their lib_symbol definition. Net name = Value property.
- **Connectivity**: Purely geometric — wire endpoint == pin position == connected. No explicit netlist.
- **lib_symbols section**: Complete local cache. Every symbol used on the sheet has its full definition embedded.

---

## 3. Pin Geometry

### Pin position meaning

The `(at x y angle)` on a pin definition gives the **connection endpoint** — where wires attach. The pin extends from this point toward the symbol body by `length` mm.

### Pin orientation (the `angle` in pin's `at`)

The angle describes which direction the pin points **from the connection end toward the body**:

| Angle | Direction | Body end (from connection point) |
|-------|-----------|----------------------------------|
| 0°    | Right     | body_x = pin_x + length |
| 90°   | Up        | body_y = pin_y + length (in lib Y-up) |
| 180°  | Left      | body_x = pin_x - length |
| 270°  | Down      | body_y = pin_y - length (in lib Y-up) |

### Pin electrical types

```
input, output, bidirectional, tri_state, passive, free,
unspecified, power_in, power_out, open_collector, open_emitter, no_connect
```

### Pin graphic styles

```
line, inverted, clock, inverted_clock, input_low, clock_low,
output_low, edge_clock_high, non_logic
```

---

## 4. Default Dimensions (from KiCad source)

| Item | mm | mils |
|------|-----|------|
| Wire width | 0.1524 | 6 |
| Bus width | 0.3048 | 12 |
| Symbol outline width | 0.1524 | 6 |
| Junction diameter | 0.9144 | 36 |
| No-connect X half-size | 0.6096 | 24 |
| Pin length (default) | 2.54 | 100 |
| Pin symbol size (inversion bubble) | 0.635 | 25 |
| Pin name offset from body end | 0.508 | 20 |
| Default text size | 1.27 | 50 |
| Text offset ratio | 0.15 | — |
| Label size ratio | 0.375 | — |
| Dangling symbol size | 0.3048 | 12 |

---

## 5. KiCad Default Color Scheme

From kicanvas `kicad-default.ts`:

| Element | RGB | Hex | Normalized |
|---------|-----|-----|-----------|
| Background | 245, 244, 239 | #F5F4EF | 0.961, 0.957, 0.937 |
| Wire | 0, 150, 0 | #009600 | 0.0, 0.588, 0.0 |
| Bus | 0, 0, 132 | #000084 | 0.0, 0.0, 0.518 |
| Symbol outline (component_outline) | 132, 0, 0 | #840000 | 0.518, 0.0, 0.0 |
| Symbol fill (component_body) | 255, 255, 194 | #FFFFC2 | 1.0, 1.0, 0.761 |
| Pin / pin stub | 132, 0, 0 | #840000 | 0.518, 0.0, 0.0 |
| Pin name text | 0, 100, 100 | #006464 | 0.0, 0.392, 0.392 |
| Pin number text | 169, 0, 0 | #A90000 | 0.663, 0.0, 0.0 |
| Junction | 0, 150, 0 | #009600 | 0.0, 0.588, 0.0 |
| No-connect | 0, 0, 132 | #000084 | 0.0, 0.0, 0.518 |
| Net label (local) | 15, 15, 15 | #0F0F0F | 0.059, 0.059, 0.059 |
| Global label | 132, 0, 0 | #840000 | 0.518, 0.0, 0.0 |
| Hierarchical label | 114, 86, 0 | #725600 | 0.447, 0.337, 0.0 |
| Reference designator | 0, 100, 100 | #006464 | 0.0, 0.392, 0.392 |
| Value | 0, 100, 100 | #006464 | 0.0, 0.392, 0.392 |
| Other fields | 132, 0, 132 | #840084 | 0.518, 0.0, 0.518 |
| Grid | 181, 181, 181 | #B5B5B5 | 0.710, 0.710, 0.710 |
| ERC error | 230, 9, 13 | #E6090D | 0.902, 0.035, 0.051 |

---

## 6. Rendering Layer Order (back to front)

From kicanvas, layers are rendered in this order (first = bottom, last = top):

1. **Grid** — dots at 2.54mm spacing (NOT lines)
2. **Drawing sheet** — page border and title block
3. **Symbol background** — filled shapes (component_body color #FFFFC2)
4. **Symbol pin** — pin stub lines and decorations (inverted circles, clock marks)
5. **Bitmap** — embedded images
6. **Notes** — standalone text, polylines, rectangles (non-electrical)
7. **Symbol foreground** — symbol outlines, pin names, pin numbers, lib text
8. **Wire** — wires and buses
9. **Junction** — filled circles, no-connects, bus entries
10. **Label** — net labels, global labels, hierarchical labels
11. **Symbol field** — Reference, Value, other properties
12. **Marks** — DNP (Do Not Populate) X marks

---

## 7. Pin Name and Number Positioning

### Pin name — "place_inside" (default, when pin_name_offset > 0)

The name is drawn **inside the symbol body**, starting from just past the body end of the pin.

For a **right-oriented pin** (canonical orientation):
```
name_offset_x = pin_name_offset - name_thickness/2 + pin_length
              = 0.508 - 0.0762 + 2.54 = 2.9718 mm (from connection point)
name_offset_y = 0
h_align = "left"
v_align = "center"
```

### Pin number — "place_above"

The number is drawn **above the pin stub**, centered on the midpoint.

For a **right-oriented pin**:
```
text_margin = 0.6096 * 0.15 = 0.09144 mm
num_offset_x = pin_length / 2 = 1.27 mm
num_offset_y = -(text_margin + pin_thickness/2 + num_thickness/2) = -0.244 mm
h_align = "center"
v_align = "bottom"
```

### Orientation transform (orient_label)

All offsets are computed for a right-oriented pin, then transformed:

| Orientation | Offset transform | h_align change |
|-------------|-----------------|----------------|
| Right | (ox, oy) — no change | no change |
| Left | (-ox, oy) | left↔right |
| Up | (oy, -ox) | no change |
| Down | (oy, ox) | left↔right |

### Vertical text

Up/down pins have text rotated 90° (vertical). Left/right pins have horizontal text.

### Skip conditions

- Pin name hidden if: `pin_names.hide` or name is "~" or empty
- Pin number hidden if: `pin_numbers.hide` or number is "~" or empty
- All pin text hidden for power symbols (`is_power = true`)

---

## 8. Net Label Positioning

### Spin style from rotation

| Label rotation | Text angle | h_align | v_align |
|---------------|-----------|---------|---------|
| 0° | 0 | left | bottom |
| 90° | 90 | left | bottom |
| 180° | 0 | right | bottom |
| 270° | 90 | right | bottom |

### Text offset from attachment point

```
dist = round(text_offset_ratio * text_width + effective_thickness)
     ≈ round(0.15 * 12700 + 1588) = 3493 IU ≈ 0.35 mm
```

- Horizontal text (0°/180°): offset = (0, -0.35mm) — text above wire
- Vertical text (90°/270°): offset = (-0.35mm, 0) — text left of wire

---

## 9. Global Label Shape

Global labels have a flag-shaped outline around the text. The shape depends on the `shape` attribute:

### Shape construction (in IU space, divided by 10000 for mm)

```
margin = 0.375 * text_height
half_size = text_height/2 + margin
symbol_length = text_box_width + 2 * margin
x = symbol_length + stroke_width + 3
y = half_size + stroke_width + 3
```

Base 7-point rectangle:
```
(0,0), (0,-y), (-x,-y), (-x,0), (-x,y), (0,y), (0,0)
```

Shape modifications:
- **input**: pts[0].x and pts[6].x += half_size (pointed right end)
- **output**: pts[3].x -= half_size (pointed left end)
- **bidirectional/tri_state**: both ends pointed
- **passive**: no modification (rectangle)

Points rotated by `(rotation + 180)°`, translated to text_pos, divided by 10000.

---

## 10. Property (Reference/Value) Positioning

Properties have their own explicit `(at x y angle)` in the .kicad_sch file. The stored position is in **schematic world coordinates**.

### Color assignment

- "Reference" → theme.reference (#006464 teal)
- "Value" → theme.value (#006464 teal)
- Other fields → theme.fields (#840084 purple)

### Text angle adjustment for rotated symbols

If the parent symbol is rotated 90° or 270° (detected by checking the transform matrix), the property's text angle is swapped: 0↔90. This keeps text readable regardless of symbol rotation.

---

## 11. ERC Pin Compatibility Matrix

The exact 12×12 matrix from KiCad source (`erc_settings.cpp`):

```
         I    O    Bi   3S   Pas  NIC  UnS  PwrI PwrO OC   OE   NC
I        OK   OK   OK   OK   OK   OK   WAR  OK   OK   OK   OK   ERR
O        OK   ERR  OK   WAR  OK   OK   WAR  OK   ERR  ERR  ERR  ERR
Bi       OK   OK   OK   OK   OK   OK   WAR  OK   WAR  OK   WAR  ERR
3S       OK   WAR  OK   OK   OK   OK   WAR  WAR  ERR  WAR  WAR  ERR
Pas      OK   OK   OK   OK   OK   OK   WAR  OK   OK   OK   OK   ERR
NIC      OK   OK   OK   OK   OK   OK   OK   OK   OK   OK   OK   ERR
UnS      WAR  WAR  WAR  WAR  WAR  OK   WAR  WAR  WAR  WAR  WAR  ERR
PwrI     OK   OK   OK   WAR  OK   OK   WAR  OK   OK   OK   OK   ERR
PwrO     OK   ERR  WAR  ERR  OK   OK   WAR  OK   ERR  ERR  ERR  ERR
OC       OK   ERR  OK   WAR  OK   OK   WAR  OK   ERR  OK   OK   ERR
OE       OK   ERR  WAR  WAR  OK   OK   WAR  OK   ERR  OK   OK   ERR
NC       ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR  ERR
```

Key rules:
- Output-to-Output = **ERR** (two drivers)
- PowerOut-to-PowerOut = **ERR**
- NC to anything = **ERR**
- Passive-to-anything (except NC) = **OK**
- Unspecified = **WAR** for almost everything

### Drive rules

- **Driving pin types** (can drive a normal net): Output, PowerOut, Passive, TriState, Bidirectional
- **Power-driving pin types** (can drive a power net): **Only PowerOut**
- **Driven pin types** (need a driver): Input, PowerInput

---

## 12. Connectivity Resolution

KiCad derives connectivity entirely from **geometric coincidence**:

1. **Pin-to-wire**: Pin world position must exactly match a wire endpoint
2. **Wire-to-wire**: Two wire endpoints at same coordinate are connected
3. **T-junctions**: Three or more wires meeting at a point need a `(junction ...)` element
4. **Labels**: Labels at a wire endpoint/pin assign a net name; all labels with same text on same sheet are connected
5. **Global labels**: Connect across all sheets
6. **Power symbols**: Create implicit global nets; net name = Value property
7. **No-connect**: Marks a pin as intentionally unconnected

Positions must be exact — even sub-IU misalignment breaks connectivity. Default grid is 1.27mm (50 mil).

---

## 13. Symbol Library Format (.kicad_sym)

### Sub-symbol naming convention

Inside lib_symbols, sub-symbols encode unit and body style:
- `"R_0_1"` — common graphics (unit 0), body style 1
- `"R_1_1"` — unit 1, body style 1 (pins live here)
- `"R_2_1"` — unit 2, body style 1
- `"R_0_2"` / `"R_1_2"` — body style 2 (DeMorgan alternate)

### Pin names/numbers visibility

```
(pin_names (offset 0.508) hide)   # offset controls name placement, hide suppresses names
(pin_numbers hide)                 # suppresses pin numbers
```

### Power symbol detection

```
(symbol "power:GND"
  (power)                          # THIS flag marks it as a power symbol
  ...
```

### Font size quirk

KiCad stores font size as `(size height width)`, but width comes first internally. The values need to be swapped.

### Graphic primitives

Each sub-symbol can contain:
- `(polyline (pts (xy x y) ...) (stroke ...) (fill (type outline|background|none)))`
- `(rectangle (start x y) (end x y) (stroke ...) (fill ...))`
- `(circle (center x y) (radius r) (stroke ...) (fill ...))`
- `(arc (start x y) (mid x y) (end x y) (stroke ...) (fill ...))`
- `(text "string" (at x y angle) (effects ...))`

Fill types:
- `none` — stroke only
- `outline` — fill with outline color
- `background` — fill with background/body color
