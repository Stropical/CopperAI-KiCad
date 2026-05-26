# RL Model Architecture

## Scope

This document describes the current cleanup-placement policy used in `experiments/pcb_ft_q35/src/rl`.

The model is not a full sequence model and not a graph neural network. It is a small candidate-ranking policy over a finite action set produced by `RealKiCadCleanupEnv`.

## Environment Contract

The runtime environment is [`RealKiCadCleanupEnv`](/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/src/rl/real_kicad_cleanup_env.py), which wraps `schematic_gym` and exposes one-step cleanup episodes on real KiCad schematics.

Each episode contains:

- A `structured` global observation vector flattened into `state_vec`
- Up to `max_actions=8` candidate placement repairs
- A boolean candidate mask
- Oracle candidate outcomes computed by simulating every candidate once

The current best task definition is `difficulty=hard`, which uses:

- Larger perturbations
- Secondary perturbed connected symbols
- Cluster repair actions like `cluster_inverse` and `cluster_half_inverse`

## Observation Shape

The policy consumes:

- `state_vec`: dense flattened structured observation
- `candidate_features`: shape `[A, F]`
- `candidate_mask`: shape `[A]`

Current dimensions in the full hard runs:

- `state_dim = 2894`
- `candidate_dim = 16`
- `max_actions = 8`

`state_vec` is derived from the `schematic_gym` structured observation and includes counts, connection progress, pin/connectivity summaries, and readability/electrical scores.

`candidate_features` encode each proposed repair, including:

- move deltas
- candidate kind
- selected-vs-peer flags
- attachment totals
- connectedness cues
- distance-to-clean geometry features

## Policy Architecture

The policy is [`CandidateActorCritic`](/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/src/rl/policy.py).

### State Encoder

```text
Linear(state_dim -> hidden_dim)
LayerNorm(hidden_dim)
Tanh
Linear(hidden_dim -> hidden_dim)
Tanh
```

### Candidate Head

For each candidate:

```text
concat(state_hidden, candidate_features)
Linear(hidden_dim + candidate_dim -> hidden_dim)
Tanh
Linear(hidden_dim -> 1)
```

This produces one logit per candidate. Invalid candidates are masked to `-1e9`.

### Value Head

```text
Linear(hidden_dim -> hidden_dim / 2)
Tanh
Linear(hidden_dim / 2 -> 1)
```

The value head is used in RL training and can also be trained against oracle reward in supervised runs.

## Training Modes

### 1. Reward-Only RL

Current RL training is in [`train_real_kicad_cleanup.py`](/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/src/rl/train_real_kicad_cleanup.py).

It uses:

- one-step actor-critic updates
- sampled actions from a categorical over candidate logits
- policy loss from immediate reward advantage
- MSE value loss
- entropy bonus

This setup was sufficient to beat the inverse heuristic on held-out hard episodes, but it appears to have saturated.

### 2. Oracle-Supervised Selector

The next iteration adds:

- explicit oracle-labeled datasets exported from mined hard episodes
- supervised cross-entropy against `oracle_best_action_idx`
- optional value regression against oracle best reward

This path keeps the same policy architecture so the resulting checkpoints can be evaluated by the same runtime.

## Current Best RL Hyperparameters

Best hard RL run before saturation:

- `hidden_dim = 256`
- `learning_rate = 3e-4`
- `entropy_coef = 0.003`
- `value_coef = 0.5`
- `max_grad_norm = 1.0`
- `episodes = 800`
- `difficulty = hard`
- `min_quality_score = 0.55`

## Known Limits

- The model only ranks a small hand-built candidate set.
- It does not synthesize new geometry directly.
- It sees a flattened structured state, not the full graph.
- Longer RL fine-tuning improved train metrics without changing held-out decisions.

## Why This Architecture Still Matters

Even with those limits, this policy is useful as an agent-side placement primitive because it:

- works on real KiCad schematics
- reasons over cluster repairs, not only inverse recovery
- can be called as a fast reranker inside a larger agent loop

The main next step is better supervision, not a larger MLP.
