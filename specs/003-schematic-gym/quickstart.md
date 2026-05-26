# Quickstart: SchematicGym-KiCad

## Install

```bash
# From repository root
pip install -e ./schematic_gym

# Or with all dependencies
pip install -e "./schematic_gym[all]"
```

### System Dependencies (for Cairo rendering)

**macOS**:
```bash
brew install cairo
```

**Ubuntu/Debian (or Docker)**:
```bash
apt-get install -y libcairo2 fontconfig fonts-dejavu-core
fc-cache -fv
```

### Docker

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 fontconfig fonts-dejavu-core && \
    fc-cache -fv && rm -rf /var/lib/apt/lists/*
COPY schematic_gym/ /app/schematic_gym/
RUN pip install /app/schematic_gym
```

---

## Basic Usage

```python
import gymnasium as gym

# Create environment
env = gym.make("SchematicGym-v0", render_mode="rgb_array")

# Load a scenario and reset
obs, info = env.reset(options={"scenario": "schematic_gym/scenarios/03_connect_two_pins.json"})

# Take an action (connect_pins: wire R1.2 to R2.1)
action = (5, {"pin_a_instance": 0, "pin_a_num": 1, "pin_b_instance": 1, "pin_b_num": 0})
obs, reward, terminated, truncated, info = env.step(action)

# Check reward breakdown
print(info["reward_breakdown"])
# {"total": 0.35, "electrical": 0.33, "readability": 0.8, "erc_penalty": -0.02, ...}

# Render
image = env.render()  # np.ndarray (512, 512, 3)

# Continue until done
while not (terminated or truncated):
    action = agent.predict(obs)  # your agent
    obs, reward, terminated, truncated, info = env.step(action)

env.close()
```

---

## Using the Tool API (for LLM agents)

```python
env = gym.make("SchematicGym-v0")
obs, info = env.reset(options={"scenario": "path/to/scenario.json"})

# Query environment without consuming a step
open_nets = env.unwrapped.get_open_nets()
violations = env.unwrapped.get_erc_violations()
candidates = env.unwrapped.get_candidate_connections("R1.2")

# Export current state
env.unwrapped.export_kicad("output.kicad_sch")
env.unwrapped.export_svg("output.svg")
```

---

## Running with Curriculum

```python
env = gym.make("SchematicGym-v0")

# Start at level 1
obs, info = env.reset(options={"curriculum_level": 1})

for episode in range(100):
    done = False
    while not done:
        action = agent.predict(obs)
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

    # Auto-advances curriculum level on success
    obs, info = env.reset()
    print(f"Episode {episode}: level={info.get('curriculum_level')}")
```

---

## Running Tests

```bash
cd schematic_gym
pytest tests/ -v

# Performance benchmarks
pytest tests/benchmarks/ --benchmark-only
```

---

## Project Layout

```
schematic_gym/
├── env.py              # Main Gymnasium environment
├── core/               # State model (symbols, wires, nets)
├── erc/                # Electrical rules check engine
├── reward/             # Scoring (electrical + readability)
├── observations/       # Structured, graph, image observations
├── actions/            # Action space and handlers
├── rendering/          # Cairo renderer
├── io/                 # KiCad import/export, scenario loader
├── library/            # Curated symbol library
├── scenarios/          # Built-in task scenarios
└── curriculum/         # Curriculum progression
```
