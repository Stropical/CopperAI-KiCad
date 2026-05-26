# Qwen Schematic V1 Planning Docs

This repo contains planning artifacts for building a graph-conditioned schematic assistant.

## Documents

- `docs/qwen-schematic-v1/SPEC.md` - product and technical specification.
- `docs/qwen-schematic-v1/IMPLEMENTATION_PLAN.md` - phase plan, milestones, and sequencing.
- `docs/qwen-schematic-v1/TASK_LIST.md` - actionable engineering backlog with acceptance checks.
- `docs/qwen-schematic-v1/DATASET_SPEC.md` - training data schema and generation strategy.
- `docs/qwen-schematic-v1/EVAL_PLAN.md` - evaluation protocol, metrics, and release gates.

## V1 Focus

- Local schematic-window reasoning only.
- Tasks: ranking, critique, and single-step edit suggestion.
- Architecture: graph encoder -> projector -> decoder-only LM.

## Training and inference

### Training

- **Dataset:** JSONL with `split`, `task`, `input.graph_tensor` (node_features, edge_index, edge_features), and `target`. See `docs/qwen-schematic-v1/DATASET_SPEC.md` and the geometry job output format.
- **Run training:** `./train.sh` or:
  ```bash
  uv run python -m src.train.train_qwen35_graph \
    --model google/gemma-3-270m \
    --dataset path/to/geometry_tasks_split.jsonl \
    --output-dir path/to/output \
    --epochs 1 --batch-size 1 --freeze-decoder --device auto
  ```
- Training writes `output-dir/final/` (decoder, tokenizer, graph_encoder.pt, graph_projector.pt, graph_model_config.json). Use `--save-every N` to save checkpoints during training.

### Inference

- **Python API:** Load model and run a fix pass:
  ```python
  from src.inference import fix_placement_pass, load_model_and_tokenizer, run_one, schematic_to_graph_tensors

  result = fix_placement_pass(
      schematic_state="/path/to/schematic.kicad_sch",  # or {"nodes": [...], "edges": [...]}
      window_centers=["U1", "U2"],
      radius_mm=35,
      checkpoint_dir="/path/to/checkpoint/final",
      task="suggest_repair",
      max_edits=10,
  )
  # result["edits"] is a list of RepairLabel-shaped dicts
  ```
- **CLI:** `uv run python -m src.inference --checkpoint path/to/final --schematic path/to/file.kicad_sch --centers U1,U2 [--radius 35] [--output edits.json]`
- **A2A server:** `uv run python -m src.inference.a2a_server --checkpoint path/to/final [--port 8000]`. Then configure the parent (e.g. Gemini CLI) with `agent_card_url: http://host:8000/.well-known/agent.json` (see [Remote Subagents](https://geminicli.com/docs/core/remote-agents/)).

### Tests

- **Unit tests:** `uv run python -m pytest tests/ -v`
- **Inference tests:** `tests/test_inference.py` covers `pack_graph_batch`, `schematic_to_graph_tensors`, `run_one` (with a dummy model), and `fix_placement_pass` error handling. To run end-to-end inference with a real checkpoint: set `CHECKPOINT_DIR` to a saved `final/` dir and run pytest; the test will load the model and call `fix_placement_pass` on an in-memory graph.
