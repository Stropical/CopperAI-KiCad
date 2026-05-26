# Contract: Gymnasium Environment Interface

**Entity**: `SchematicGymEnv(gymnasium.Env)`
**Registration**: `gymnasium.register(id="SchematicGym-v0", entry_point="schematic_gym.env:SchematicGymEnv")`

---

## Constructor

```python
SchematicGymEnv(
    render_mode: str | None = None,    # "rgb_array" | "human" | None
    observation_modes: list[str] = ["structured"],  # ["structured", "graph", "image"]
    grid_size: float = 2.54,           # mm
    image_size: tuple[int, int] = (512, 512),  # render resolution
    max_steps: int = 200,              # default step budget
    scoring_config: dict | None = None,  # override reward weights
)
```

**metadata**:
```python
metadata = {
    "render_modes": ["human", "rgb_array"],
    "render_fps": 30,
}
```

---

## reset()

```python
def reset(
    self,
    *,
    seed: int | None = None,
    options: dict | None = None,
) -> tuple[dict, dict]
```

**options keys**:
- `"scenario"`: str -- path to scenario JSON file
- `"scenario_data"`: dict -- inline scenario (alternative to file path)
- `"curriculum_level"`: int -- load specific curriculum level

**Returns**: `(observation, info)`
- `observation`: Dict matching `observation_space`
- `info`: `{"scenario_id": str, "step_budget": int, "objectives": list[dict]}`

---

## step()

```python
def step(self, action: tuple[int, dict]) -> tuple[dict, float, bool, bool, dict]
```

**action**: `(action_type_index, action_params)` -- from OneOf space

**Returns**: `(observation, reward, terminated, truncated, info)`
- `reward`: float, composite score delta from previous step
- `terminated`: True if all objectives met or unrecoverable failure
- `truncated`: True if step budget exceeded
- `info`: Contains `reward_breakdown`, `erc_violations`, `nets_completed`, `step_num`

---

## render()

```python
def render(self) -> np.ndarray | None
```

- `"rgb_array"`: returns `np.ndarray` shape `(H, W, 3)` dtype `uint8`
- `"human"`: returns `None` (renders to window if available)

---

## Tool API (Additional Methods)

Beyond the Gymnasium interface, the environment exposes read-only query methods:

```python
def get_symbol(self, instance_id: str) -> dict
def get_pin(self, instance_id: str, pin_num: str) -> dict
def get_open_nets(self) -> list[dict]
def get_erc_violations(self) -> list[dict]
def get_candidate_connections(self, pin_id: str) -> list[dict]
def get_alignment_suggestions(self) -> list[dict]
def resolve_netlist_view(self) -> dict
def export_kicad(self, path: str) -> None
def export_svg(self, path: str) -> None
```

These methods do NOT consume a step. They are read-only introspection for agentic planners.
