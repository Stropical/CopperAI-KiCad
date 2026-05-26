# Contract: Action Space Schema

**Space type**: `gymnasium.spaces.OneOf`
**Encoding**: `(action_type_index: int, params: dict)`

---

## Action Types

### 0: place_symbol

Place a new symbol instance on the sheet.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| symbol_id | int | Discrete(N_SYMBOLS) | Index into symbol catalog |
| x | float | Box(0, sheet_width) | World x position (mm) |
| y | float | Box(0, sheet_height) | World y position (mm) |
| rotation | int | Discrete(4) | 0=0, 1=90, 2=180, 3=270 degrees |

**Effects**: Creates SymbolInstance, assigns next available reference. Re-resolves connectivity.

### 1: move_symbol

Move an existing symbol instance.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| instance_idx | int | Discrete(MAX_INSTANCES) | Index of instance to move |
| x | float | Box(0, sheet_width) | New x position |
| y | float | Box(0, sheet_height) | New y position |

**Effects**: Updates position. Existing wires are NOT moved (may disconnect). Re-resolves connectivity.

### 2: rotate_symbol

Rotate a symbol in 90-degree increments.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| instance_idx | int | Discrete(MAX_INSTANCES) | Index of instance |
| direction | int | Discrete(2) | 0=CW 90, 1=CCW 90 |

**Effects**: Updates rotation. Pin world positions change. Re-resolves connectivity.

### 3: mirror_symbol

Mirror a symbol across an axis.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| instance_idx | int | Discrete(MAX_INSTANCES) | Index of instance |
| axis | int | Discrete(2) | 0=X axis, 1=Y axis |

### 4: draw_wire

Draw an orthogonal wire segment.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| x1 | float | Box(0, sheet_width) | Start x |
| y1 | float | Box(0, sheet_height) | Start y |
| x2 | float | Box(0, sheet_width) | End x |
| y2 | float | Box(0, sheet_height) | End y |

**Effects**: Snaps to grid. Must be horizontal or vertical (if diagonal, projects to nearest axis). Creates WireSegment. Re-resolves connectivity.

### 5: connect_pins

High-level: automatically wire two pins with Manhattan routing.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| pin_a_instance | int | Discrete(MAX_INSTANCES) | Instance index for pin A |
| pin_a_num | int | Discrete(MAX_PINS_PER_SYMBOL) | Pin index for pin A |
| pin_b_instance | int | Discrete(MAX_INSTANCES) | Instance index for pin B |
| pin_b_num | int | Discrete(MAX_PINS_PER_SYMBOL) | Pin index for pin B |

**Effects**: Generates 1-3 wire segments forming an L or Z path between pin world positions. Adds junctions if needed. Re-resolves connectivity.

### 6: add_junction

Place a junction dot at a wire intersection.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| x | float | Box(0, sheet_width) | Position x |
| y | float | Box(0, sheet_height) | Position y |

### 7: delete_wire

Remove a wire segment.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| wire_idx | int | Discrete(MAX_WIRES) | Index of wire to delete |

### 8: place_net_label

Place a local net label.

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| net_name_idx | int | Discrete(MAX_NET_NAMES) | Index into scenario's net name list |
| x | float | Box(0, sheet_width) | Position x |
| y | float | Box(0, sheet_height) | Position y |

### 9: place_power_symbol

Place a power symbol (VCC, GND, etc.).

| Param | Type | Space | Description |
|-------|------|-------|-------------|
| power_idx | int | Discrete(N_POWER_SYMBOLS) | Index into power symbol catalog |
| x | float | Box(0, sheet_width) | Position x |
| y | float | Box(0, sheet_height) | Position y |
| rotation | int | Discrete(4) | 0/90/180/270 |

### 10: no_op

Do nothing (useful for "I'm done" signal or padding).

No parameters.

---

## Action Validation

All actions are validated before execution:
- Out-of-bounds coordinates: snapped to sheet boundaries
- Invalid instance/wire indices: action is a no-op, small negative reward
- Diagonal wire: projected to nearest orthogonal axis
- Duplicate wire: action is a no-op

Invalid actions never crash the environment. They produce a small penalty and the episode continues.
