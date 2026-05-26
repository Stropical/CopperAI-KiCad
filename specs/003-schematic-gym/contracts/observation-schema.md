# Contract: Observation Space Schema

**Space type**: `gymnasium.spaces.Dict`
**Active modes**: Controlled by `observation_modes` constructor param

---

## Structured Observation (`"structured"`)

**Space**: `gymnasium.spaces.Dict`

```python
{
    # Sheet metadata
    "sheet_width": Box(0, 1000, shape=(), dtype=float32),
    "sheet_height": Box(0, 1000, shape=(), dtype=float32),
    "step_num": Discrete(max_steps),
    "steps_remaining": Discrete(max_steps),

    # Counts
    "num_instances": Discrete(MAX_INSTANCES),
    "num_wires": Discrete(MAX_WIRES),
    "num_nets": Discrete(MAX_NETS),
    "num_open_pins": Discrete(MAX_PINS),
    "num_erc_errors": Discrete(100),
    "num_erc_warnings": Discrete(100),

    # Symbol instances (padded fixed-size arrays)
    "instance_positions": Box(-inf, inf, shape=(MAX_INSTANCES, 2), dtype=float32),
    "instance_rotations": MultiDiscrete([4] * MAX_INSTANCES),
    "instance_symbol_ids": MultiDiscrete([N_SYMBOLS] * MAX_INSTANCES),
    "instance_mask": MultiBinary(MAX_INSTANCES),  # 1 = slot occupied

    # Pin positions (all pins from all instances)
    "pin_positions": Box(-inf, inf, shape=(MAX_TOTAL_PINS, 2), dtype=float32),
    "pin_types": MultiDiscrete([12] * MAX_TOTAL_PINS),  # PinType enum
    "pin_connected": MultiBinary(MAX_TOTAL_PINS),  # 1 = has net
    "pin_mask": MultiBinary(MAX_TOTAL_PINS),

    # Wire segments
    "wire_endpoints": Box(-inf, inf, shape=(MAX_WIRES, 4), dtype=float32),  # x1,y1,x2,y2
    "wire_mask": MultiBinary(MAX_WIRES),

    # Objective progress
    "connections_required": Discrete(100),
    "connections_completed": Discrete(100),

    # Scores
    "electrical_score": Box(0, 1, shape=(), dtype=float32),
    "readability_score": Box(0, 1, shape=(), dtype=float32),
}
```

**MAX constants** (configurable, defaults):
- MAX_INSTANCES = 64
- MAX_TOTAL_PINS = 256
- MAX_WIRES = 256
- MAX_NETS = 128
- N_SYMBOLS = len(symbol_catalog)

---

## Graph Observation (`"graph"`)

**Space**: `gymnasium.spaces.Graph`

```python
Graph(
    node_space=Box(-inf, inf, shape=(NODE_FEATURE_DIM,), dtype=float32),
    edge_space=Discrete(NUM_EDGE_TYPES),
)
```

### Node Features (NODE_FEATURE_DIM = 16)

| Index | Feature | Type | Description |
|-------|---------|------|-------------|
| 0 | node_type | float | 0=pin, 1=junction, 2=label, 3=power |
| 1-2 | position_x, position_y | float | Normalized to [0, 1] within sheet |
| 3 | electrical_type | float | PinType enum / 12 (normalized) |
| 4 | is_connected | float | 0 or 1 |
| 5 | connection_degree | float | Number of connections / MAX_DEGREE |
| 6 | symbol_class | float | Symbol category enum / N_CATEGORIES |
| 7 | pin_direction | float | 0=input-facing, 1=output-facing |
| 8-9 | symbol_center_x, symbol_center_y | float | Parent symbol center (normalized) |
| 10 | net_id_hash | float | Hash of net_id normalized to [0, 1] |
| 11 | is_power_net | float | 0 or 1 |
| 12-15 | reserved | float | Future use (padding to 16) |

### Edge Types (NUM_EDGE_TYPES = 5)

| Value | Type | Description |
|-------|------|-------------|
| 0 | CONNECTED_BY_WIRE | Direct wire connection |
| 1 | BELONGS_TO_SYMBOL | Pin → symbol membership |
| 2 | EQUIVALENT_NET_LABEL | Connected via shared label name |
| 3 | PROXIMITY | Within N grid units (spatial neighbor) |
| 4 | SAME_NET | On the same resolved net |

### GraphInstance Return

```python
GraphInstance(
    nodes=np.ndarray,       # shape (num_nodes, 16), float32
    edges=np.ndarray,       # shape (num_edges,), int64
    edge_links=np.ndarray,  # shape (num_edges, 2), int64 (source, target)
)
```

---

## Image Observation (`"image"`)

**Space**: `gymnasium.spaces.Box(0, 255, shape=(H, W, 3), dtype=np.uint8)`

Default resolution: 512x512. Configurable via `image_size` constructor param.

### Rendering Content

The image contains:
- Light gray background with subtle grid
- Black symbol outlines and graphics
- Green (or blue in dark mode) wire segments
- Filled green junction dots
- Red pin markers for unconnected required pins
- Blue pin markers for connected pins
- Text: reference designators, values, pin names/numbers, net labels
- Orange ERC violation markers (small triangles with "!")
- Selection/highlight overlay (optional, for agent debug)

### Coordinate Mapping

The full sheet is fitted to the image with uniform scaling and centering. A 5% margin is added on each side. The mapping from sheet coordinates to pixel coordinates is:

```
scale = min((W * 0.9) / sheet_width, (H * 0.9) / sheet_height)
offset_x = (W - sheet_width * scale) / 2
offset_y = (H - sheet_height * scale) / 2
pixel_x = x_mm * scale + offset_x
pixel_y = y_mm * scale + offset_y
```
