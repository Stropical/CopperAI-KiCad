# A Graph-Conditioned Language Model for Local Schematic Reasoning: Architecture and Rationale

**Technical report — PCB FT Q35 / Qwen Schematic V1**

---

## Abstract

We describe the design, data pipeline, and training methodology of a schematic-reasoning system that conditions a small causal language model (Qwen3.5-0.8B) on *local schematic windows* represented as heterogeneous graphs. The system replaces the vision pathway of a multimodal decoder with a dedicated graph encoder and linear projector, producing a fixed budget of “graph tokens” that are prepended to the text sequence. Training targets structured JSON outputs for three task families: candidate ranking, layout critique, and one-step edit suggestion. This document explains how the repository implements that pipeline end-to-end and the rationale for each major design choice.

**Keywords:** schematic reasoning, graph neural networks, graph-conditioned language models, electronic design automation, local-window reasoning.

---

## 1. Introduction

### 1.1 Motivation

Schematic layout quality—placement of symbols, routing of nets, and placement of decoupling capacitors and labels—affects both readability and electrical behavior. Automating *critique* (identifying issues) and *guidance* (suggesting concrete edits) in a tool-integrable way requires a model that (1) understands the *structure* of a local region (symbols, pins, wires, junctions, labels) and (2) produces *structured*, machine-parseable outputs rather than free-form text. Vision-only approaches treat the schematic as an image and lose explicit topology and connectivity; we instead represent each local window as a *heterogeneous graph* and condition a language model on graph-derived tokens.

### 1.2 Scope and Non-Goals

The repository implements **V1** of this idea with strict boundaries:

- **In scope:** Local-window tasks only; strict JSON outputs; task types: ranking (choose best among variants), critique (issue list with taxonomy), one-step edit suggestion (action, target, relation).
- **Out of scope (V1):** Full-sheet generation, open-ended prose as default output, PCB routing optimization.

These constraints keep the problem tractable and the model size (0.8B parameters) sufficient for integration and latency targets.

### 1.3 Contribution of This Document

We provide an academic-style exposition of (1) the *graph schema* and windowing strategy, (2) the *model architecture* (graph encoder → projector → decoder fusion), (3) the *data pipeline* from schematic sources to training samples, and (4) the *training protocol* and loss design. The goal is to make the repository self-documenting for replication and extension.

---

## 2. Problem Formulation

### 2.1 Sample Unit

Each training or evaluation sample is a **local schematic window** plus a **task type** and **target output**:

- **Window:** A subgraph defined by a center reference (e.g., component refdes `U1`) and a radius in millimeters. All nodes within that radius, and edges between them, form the window.
- **Task:** One of `critique`, `ranking`, or `edit` (in the codebase also surfaced as `tag_issues`, `suggest_repair`, `score_block`, `rank_candidates`).
- **Target:** A JSON object conforming to a fixed schema (e.g., `{"ok": false, "issues": [...]}` for critique; `{"best": 2, "reason": "..."}` for ranking; `{"action", "target", "relation"}` for edit).

The model receives the window as a graph and (optionally) a text prompt describing the task; it must generate the target JSON.

### 2.2 Why Graphs Rather Than Pixels

Schematics are inherently relational: symbols have pins, pins connect to wires, wires meet at junctions, and labels attach to nets. Spatial and topological relations (left_of, near, same_block) matter for layout quality. A graph representation preserves:

- **Typed entities** (symbol, pin, junction, net_label, power_symbol, wire_segment, block).
- **Typed relations** (symbol_has_pin, pin_connected_to_wire, wire_connected_to_junction, label_attached_to_net, plus spatial: left_of, right_of, above, below, near, inline_with, same_block).
- **Geometry in a structured form:** positions and extents are bucketed (quantized) into integers so the encoder sees discrete bins rather than raw floats, improving generalization and stability.

This design follows the principle that *structure should be explicit* when the downstream task is structural (e.g., “which symbol is too far from the decoupler?”).

---

## 3. Input Representation: Graph Schema and Windowing

### 3.1 Schema (Data Layer)

The module `src/data/graph_schema.py` defines the canonical types and records:

- **Node types** (enum): `symbol`, `pin`, `junction`, `net_label`, `power_symbol`, `wire_segment`, `block`.
- **Edge types**: structural (e.g., `symbol_has_pin`, `pin_connected_to_wire`, `wire_connected_to_junction`, `label_attached_to_net`) and spatial (`left_of`, `right_of`, `above`, `below`, `near`, `inline_with`, `same_block`).

Each **node** carries: `node_id`, `node_type`, `x_mm`, `y_mm`, `width_mm`, `height_mm`, `rotation_deg`, and optional semantic fields (`refdes`, `text`, `symbol_class`, `electrical_role`, `pin_side`, `pin_order`, `window_id`). Each **edge** carries: `src_node_id`, `dst_node_id`, `edge_type`, and optional `distance_mm`, `angle_deg`, `is_orthogonal`, `net_class`.

**Bucketing** is deterministic and shared across data generation and any online preprocessing:

- Geometry (x, y, w, h): linear buckets of size `GEOMETRY_BUCKET_SIZE_MM` (default 1.0 mm), clamped to `[GEOMETRY_MIN_BUCKET, GEOMETRY_MAX_BUCKET]`.
- Distance: interval buckets via `DISTANCE_BUCKET_BOUNDS_MM` (e.g., 0.5, 1, 2, 4, 8, 16, 32, 64 mm).
- Angle: 16 buckets over 360°; rotation: four 90° buckets.

This ensures that the same physical layout always maps to the same discrete feature indices, which is important for reproducibility and for the encoder’s embedding tables.

### 3.2 Windowing (Sampling)

`src/data/window_sampler.py` provides `sample_window(center_ref, radius_mm, nodes, edges, ...)`:

1. Resolve `center_ref` to a node (by `node_id`, `refdes`, or `text`).
2. Retain all nodes whose Euclidean distance from the center node’s `(x_mm, y_mm)` is ≤ `radius_mm`.
3. Retain only edges whose endpoints are both in the retained node set.
4. Return a `GraphWindowSample` (or a dict) with deterministic ordering (e.g., nodes sorted by `node_id`, edges by `(src, dst, type)`).

Thus each sample is a *connected* local subgraph; the radius controls context size (e.g., 35 mm for a “normal” window). A placeholder `collapse_trivial_wire_chains` exists for future simplification of degree-2 wire segments.

### 3.3 Validation

`validate_sample(sample)` in `graph_schema.py` checks: non-empty `sample_id` and `center_ref`, positive `radius_mm`, valid `quality_score` in [0,1], unique node IDs, valid node/edge types, presence of `center_ref` in node refdes, finite numeric fields, and that edge endpoints refer to existing nodes. This supports dataset hygiene before training.

---

## 4. Model Architecture

The model is a **graph-conditioned causal LM**: schematic graph → graph encoder → projector → sequence of graph tokens; these are concatenated with text token embeddings and fed to the Qwen decoder. No image or vision pathway is used.

### 4.1 High-Level Data Flow

1. **Graph batch** (dict or `GraphBatch`): `node_features` [B, N, Dn], `edge_index` [B, 2, E], `edge_features` [B, E, De], `node_type_ids`, `edge_type_ids`, `node_mask`.
2. **Graph encoder** maps the batch to **node embeddings** of shape [B, N, H] (e.g., H = 384).
3. **Projector** maps [B, N, H] → [B, N, Hq] (Hq = 1024, the Qwen hidden size).
4. These **graph token embeddings** are prepended to the **text token embeddings**; the combined sequence and a combined attention mask are passed to the decoder.
5. **Loss** is standard causal LM loss (e.g., cross-entropy on the text part; graph token positions can be masked out from the loss).

So the decoder “sees” a sequence of the form `[graph_1, …, graph_N, text_1, …, text_T]` and predicts the next token over the text (and optionally EOS).

### 4.2 Graph Encoder (`SchematicGraphEncoder`)

Implemented in `src/models/graph_encoder.py`:

- **Input projection:** Raw node features and edge features are projected to hidden dimension H (default 384). Node and edge *type* IDs are embedded and added to the projected features, so the model has explicit type embeddings (node_type_emb, edge_type_emb).
- **Layers:** A small number of *graph transformer* layers (configurable; default 2 in the encoder class, 6 in the top-level `Qwen35GraphModel` config). Each layer performs message passing over the graph: for each edge (src → dst), key and value come from the source node (and edge), query from the destination node; attention scores (here implemented as a sigmoid over scaled dot-product) gate the message; messages are aggregated per node, then a residual and a feed-forward block with LayerNorm complete the layer. Masking by `node_mask` ensures padded nodes do not contribute.
- **Output:** LayerNorm over node states; shape [B, N, H]. There is no global virtual node or token reduction in the encoder itself—the *entire* set of node embeddings is passed to the projector, and the decoder receives one token per node (up to a future token budget/capping step).

The encoder accepts either a `GraphBatch` or a dictionary (or keyword arguments) and normalizes edge index and feature layouts (e.g., [2,E] vs [B,2,E]) so that training and inference can assume a uniform [B, N, …] and [B, 2, E] layout.

### 4.3 Projector (`GraphToQwenProjector`)

Implemented in `src/models/projector.py`:

- Two-layer MLP: `Linear(in_dim=384, hidden_dim=1024) → GELU → Dropout → Linear(1024, out_dim=1024)`.
- Maps each node embedding from 384-d to 1024-d so that graph tokens lie in the same space as the decoder’s input embeddings (Qwen hidden size 1024).

No extra LayerNorm is applied in the projector in the default implementation; the decoder’s own normalization handles the fused sequence.

### 4.4 Decoder Fusion (`QwenSchematicModel` and `Qwen35GraphModel`)

- **QwenSchematicModel** (`src/models/qwen_schematic_model.py`): Given `graph_batch` and optionally `input_ids` or `inputs_embeds`, it (1) runs the graph encoder and projector to get graph token embeddings, (2) gets text embeddings from the decoder’s embedding layer if `input_ids` are provided, (3) concatenates graph tokens then text on the sequence dimension, (4) builds a combined attention mask (ones for graph positions, then the text mask), (5) merges labels so that graph positions are filled with -100 (ignored in loss), and (6) calls the decoder with `inputs_embeds` and the combined mask/labels. So the decoder performs causal attention over `[graph | text]` and is trained only to predict the text (and EOS).

- **Qwen35GraphModel** (`src/models/qwen35_graph_model.py`): Top-level wrapper that holds the decoder, graph encoder, projector, and tokenizer. It constructs `QwenSchematicModel` internally and disables any vision-related attributes on the decoder (e.g., `visual`, `vision_tower`, `multi_modal_projector`) so that the graph path is the only non-text conditioner. It provides `from_pretrained` (load Qwen3.5-2B and attach new encoder/projector), `save_pretrained` / `load_pretrained` (decoder, tokenizer, graph_encoder.pt, graph_projector.pt, graph_model_config.json), `freeze_decoder` / `unfreeze_decoder`, `trainable_parameter_groups`, and `generate` (prepend graph tokens then call decoder.generate with fused embeddings and mask).

This design mirrors common “vision encoder + projector + LLM” architectures (e.g., LLaVA-style), with the vision encoder replaced by a graph encoder and no image tokens.

### 4.5 Decoder variants (Unsloth, LoRA, QLoRA)

The default decoder is **Qwen3.5-2B-Base**. Optionally, the decoder can be loaded via **Unsloth** with **16-bit LoRA** (recommended for Qwen3.5) or **4-bit QLoRA** (supported but not recommended for Qwen3.5 per Unsloth, due to higher quantization differences). When using Unsloth, the base decoder is loaded in 4-bit or 16-bit and LoRA adapters are added; only the adapter parameters (and the graph encoder and projector) are trainable unless the decoder is unfrozen. The graph encoder and projector are unchanged and always trainable regardless of decoder variant. Use trainer flags `--use-unsloth`, `--load-in-16bit` (recommended), or `--load-in-4bit` to enable these paths.

---

## 5. Data Pipeline

Training data is produced by a **multi-stage pipeline** implemented under `src/data_pipeline/`. Jobs are invoked via the CLI (`src/data_pipeline/cli.py`) and pass JSONL between stages.

### 5.1 Job Sequence

Conceptually:

1. **mine** / **repo_miner**: Discovers schematic files (e.g., from a repo or directory).
2. **parse** / **schematic_parser**: Parses schematic files into an intermediate representation (symbols, wires, junctions, labels).
3. **extract** / **block_extractor**: Identifies logical “blocks” (e.g., functional regions) and their node/edge sets.
4. **cluster**: Groups or filters blocks (e.g., by similarity or usage).
5. **perturb** / **perturbation_engine**: Generates variants (e.g., move decoupler, add crossing, misalign label) with machine-readable reason tags.
6. **score** / **scorer_ranker**: Scores layouts (e.g., by rules or heuristics) and optionally ranks candidates.
7. **geometry**: Converts blocks/windows into **geometry task** records: each record has `task`, `split`, `input` (with `graph_tensor`), and `target`. The `graph_tensor` contains `node_features`, `edge_index`, `edge_features` in the format expected by the training collator (numeric node/edge type indices, quantized geometry).
8. **split**: Ensures train/val/test splits (e.g., by design or block family to avoid leakage).
9. **benchmark**: Optional evaluation/benchmarking on held-out data.

The **geometry** job is central for training: it maps the abstract graph + metadata into the tensor format (node feature matrix, COO-style edge index, edge feature matrix, node/edge type IDs) and attaches the appropriate JSON target for the task (e.g., `tag_issues`, `suggest_repair`, `score_block`, `rank_candidates`). Node and edge type dictionaries in the geometry job (e.g., `_NODE_TYPE_TO_ID`, `_EDGE_TYPE_TO_ID`, `_SYMBOL_CLASS_TO_ID`) align with the encoder’s embedding sizes; quantization (e.g., `_quantize` for positions and rotation) matches the schema’s bucketing philosophy.

### 5.2 Training Dataset Format

The trainer (`src/train/train_qwen35_graph.py`) reads **JSONL** where each line is an object with:

- `split`: `"train"` or `"val"` (or `"test"` if used for eval).
- `task`: one of the supported task names (e.g., `tag_issues`, `suggest_repair`, `score_block`, `rank_candidates`).
- `input`: dict containing at least one `graph_tensor` (for single-graph tasks) or `candidates` (for ranking), each candidate having a `graph_tensor`.
- `target`: the JSON object that the model must generate (e.g., issue list, repair suggestion, score, or `best` index + reason).

The **GeometryTaskDataset** loads these records, filters by split and task, and for each sample extracts the prompt text (a JSON-serialized description of the task and input) and the target text (compact JSON). The **GeometryCollator** then:

- Encodes prompt and target with the model’s tokenizer (with a `<task>` / `<answer>` wrapper and BOS/EOS).
- Packs multiple `graph_tensor` dicts into a single batched graph (padding nodes/edges per graph, stacking into [B, N_max, Dn], [B, 2, E_max], etc., with `node_mask` for valid positions).

So the model always receives a batch of (graph_batch, input_ids, attention_mask, labels) where labels are -100 on prompt and graph positions and the actual target token IDs on the answer span.

---

## 6. Training Protocol

### 6.1 Stages (Spec vs. Current Code)

The specification envisions:

- **Stage 1 (alignment):** Graph encoder + projector trained; decoder frozen. Tasks: graph-to-JSON summaries and relation checks to align graph tokens with the decoder’s embedding space.
- **Stage 2 (instruction tuning):** Encoder + projector + optional LoRA on top decoder layers. Tasks: ranking, critique, edit suggestion; JSON schema enforced.
- **Stage 3 (optional):** Heuristic reward or preference tuning (e.g., crossings, decoupler proximity).

The current `train_qwen35_graph.py` script supports a single pipeline with optional `--freeze-decoder`. So one can run Stage-1-style runs (freeze decoder) or full fine-tuning; staged scripts (`train_stage1.py`, `train_stage2.py`) may implement the formal stage split. Loss is **next-token cross-entropy** on the answer tokens; the spec also mentions a mix of 70% JSON generation, 20% ranking loss, 10% auxiliary (relation/role/geometry) losses, which can be added in future.

### 6.2 Optimization and Checkpointing

- Optimizer: AdamW with parameter groups (higher learning rate for graph encoder + projector, lower for decoder when unfrozen).
- Schedule: Linear warmup then linear decay over total steps (warmup ratio configurable).
- Gradient clipping by max norm (default 1.0).
- Checkpoints: best validation loss checkpoint saved under `output_dir/best`; periodic checkpoints every N steps or every N seconds; final model under `output_dir/final`. Trainer state (optimizer, scheduler, global_step, best_val) can be saved for resume.

### 6.3 Run Configuration

`train.sh` illustrates a minimal run: set `MODEL`, `DATASET`, `OUTPUT`, `EPOCHS`, `BATCH`, `MAX_TRAIN`, `MAX_VAL`, `DEVICE`, `SAVE_INTERVAL` and call `train_qwen35_graph` with `--freeze-decoder` for alignment-only training. The dataset path points to a geometry JSONL produced by the data pipeline (e.g., after geometry + split jobs).

---

## 7. Why This Design

- **Local windows:** Keep context bounded and tasks well-defined; avoid full-sheet complexity and reduce graph size for the encoder.
- **Heterogeneous graph + types:** Matches the domain (symbols, pins, wires, junctions, labels) and allows the encoder to use different embeddings for different roles and relations.
- **Bucketing:** Stable, discrete features avoid overfitting to exact coordinates and improve robustness to small shifts.
- **Graph tokens as prefix:** Same pattern as vision-language models; the decoder attends over graph then text and only predicts text, which simplifies training and keeps the decoder’s generation behavior unchanged.
- **Strict JSON outputs:** Enables programmatic use (ranking index, issue list, edit triple) and avoids free-form parsing; validation and schema checks in the data pipeline and (optionally) at decoding time keep quality high.
- **Small decoder (0.8B):** Keeps inference cheap and suitable for tool integration; the heavy structural reasoning is in the graph encoder and the decoder mainly maps graph+task to JSON.
- **Frozen decoder in Stage 1:** Reduces risk of “forgetting” and aligns graph tokens to the existing embedding space before fine-tuning the decoder on task-specific generation.

---

## 8. Integration with an Agent for Suggesting and Fixing Placements / Wires

The model in this repo is a **reasoning head**: given a local schematic window (as a graph) and a task, it outputs structured JSON (critique, ranking, or one-step edit). It does not open files, move symbols, or redraw wires. To **suggest and fix** placements and wires in a real schematic, a second **orchestrating agent** is needed. That agent is responsible for: (1) obtaining the schematic (e.g., from KiCad or another EDA), (2) turning it into graph form and selecting windows, (3) calling this model for critique/edit/ranking, and (4) **applying** the suggested edits via EDA tools. This section describes how to incorporate this repo with such an agent.

### 8.1 Roles

| Role | Who | Responsibility |
|------|-----|----------------|
| **Schematic reasoning** | This repo (Qwen35GraphModel) | Input: graph batch + task prompt. Output: JSON (issues, best index, or edit triple). |
| **Orchestration** | External agent (LLM + tools, or rule-based controller) | Decide which windows to analyze, call the reasoning model, interpret JSON, decide whether to apply edits, invoke EDA tools. |
| **Graph extraction** | Shared / agent-side | Parse schematic (e.g., KiCad .kicad_sch) → nodes/edges → window sampling → `graph_tensor` in the format expected by the collator (see §5.2). |
| **Edit execution** | EDA / KiCad integration | Map edit JSON (e.g. `move_symbol`, `target`, `anchor`, `relation`) to concrete operations (move symbol, reroute wire, rotate, etc.). |

The agent can be an MCP-capable LLM that has tools like “get_schematic_window”, “suggest_fixes”, “apply_edit”; a client that talks to this system via the **Agent-to-Agent (A2A) protocol** as a remote subagent (see §8.7); or a Python script that loops over windows and calls the model then a KiCad API.

### 8.2 Data Flow (Suggest and Fix Loop)

1. **Obtain schematic**  
   Agent gets the current schematic (file path, or content from an MCP resource / KiCad plugin).

2. **Parse → graph**  
   Use the same pipeline as training: parse (e.g. `data_pipeline/jobs/parse` / schematic_parser) to get symbols, wires, junctions, labels; optionally extract blocks; build node/edge lists. Convert to the same schema as `graph_schema.py` (NodeRecord, EdgeRecord).

3. **Choose windows**  
   For “fix placements/wires” the agent typically centers windows on components of interest (e.g. every IC, or every block with a decoupler). For each center refdes and radius (e.g. 35 mm), call `sample_window(center_ref, radius_mm, nodes, edges)` to get a `GraphWindowSample`.

4. **Graph → tensor**  
   Convert each `GraphWindowSample` into the `graph_tensor` dict format expected by the trainer’s collator: `node_features`, `edge_index`, `edge_features`, with node/edge type IDs and bucketed geometry (same bucketing as in `graph_schema.py` and `data_pipeline/jobs/geometry`). This conversion is currently done inside the data pipeline (geometry job); for inference you need a **runtime path** that does the same (schema → bucketed features → tensors) so the model sees consistent input.

5. **Call the model**  
   Load `Qwen35GraphModel` (e.g. `load_pretrained(checkpoint_dir)`). For each window:
   - Build a batch of one (or more) graph(s) in the same dict structure as `GeometryCollator._pack_graph` output.
   - Build a text prompt (e.g. `{"task": "critique", ...}` or `{"task": "suggest_repair", ...}`) and tokenize.
   - Call `model.generate(graph_batch=graph_batch, input_ids=prompt_ids, attention_mask=..., max_new_tokens=...)`.
   - Parse the generated token ids to text, then `json.loads(...)` to get the structured output.

6. **Interpret and apply**  
   - **Critique:** Agent gets `{"ok": false, "issues": [{"target": "C3", "reason": "too_far_from_U1_VDD"}]}`. It can present these to the user or use them to decide where to suggest edits.
   - **Edit:** Agent gets `{"action": "move_symbol", "target": "C3", "anchor": "U1.23", "relation": "near_right"}`. It must map this to EDA operations: resolve `target` and `anchor` to schematic entities (e.g. symbol refdes, pin), then call KiCad (or another tool) to move the symbol, redraw wires, etc.
   - **Ranking:** If the agent has multiple candidate layouts (e.g. from a generator), it can call the model with `rank_candidates` and use `best` + `reason` to choose one.

7. **Apply edits (agent + EDA)**  
   The agent uses **external tools** to modify the schematic: KiCad’s Python API, MCP tools that wrap KiCad, or file-level edits to the schematic format. The mapping from edit JSON to concrete moves/wire changes is **outside this repo**; it should be implemented in the agent’s tool layer or in a shared “schematic editor” MCP server.

### 8.3 What Exists vs What to Add

- **Exists:** Model forward/generate, training collator that builds graph batches from `graph_tensor` dicts, data pipeline that produces `graph_tensor` from schematic sources (parse → extract → geometry), graph schema and window sampling.
- **To add for agent integration:**
  1. **Inference API or script**  
     A small module or script that: loads a saved checkpoint, takes one or more windows (as graph tensors or as raw nodes/edges and does schema→tensor conversion), runs `generate`, and returns parsed JSON. This can be a Python function, a CLI, or an MCP tool (e.g. `schematic_suggest_fixes` that accepts graph + task and returns JSON).
  2. **Runtime graph extraction**  
     Reuse or factor the pipeline so that, given a schematic (file or in-memory), the agent (or a tool it calls) can get nodes/edges and then `sample_window` + convert to `graph_tensor` without running the full offline pipeline. The same parsers and geometry encoding as in `data_pipeline/jobs` should be used so train and inference inputs match.
  3. **Edit executor (outside this repo)**  
     A layer that turns edit JSON into KiCad (or other EDA) operations: move symbol by refdes, place near anchor pin, reroute wires, etc. This belongs in the agent’s tool set or in an MCP server that the agent calls.

### 8.4 Example Integration Patterns

- **Pattern A — MCP server “schematic reasoner”**  
  One MCP server exposes tools: e.g. `critique_window(graph_tensor, task)`, `suggest_edit_window(graph_tensor)`, `rank_candidates(candidates_with_graph_tensors)`. The server loads the Qwen35GraphModel once and runs inference on demand. The **orchestrating agent** (another MCP client, or Cursor/IDE) gets the schematic from elsewhere (e.g. KiCad MCP or file), builds graph tensors (using shared parsing + windowing code), calls these tools, then uses **another** MCP server or KiCad API to apply the suggested edits.

- **Pattern B — Single agent with tools**  
  The agent has tools: “get_schematic_graph_windows(schematic_path, center_refs, radius_mm)”, “suggest_fixes(graph_tensor)”, “apply_edit(schematic_path, edit_json)”. The first and third tools are implemented by the EDA/MCP side; the second is implemented by loading this repo’s model and running `generate` with the appropriate task prompt. The agent loop: get windows → suggest_fixes for each → optionally apply_edit for each accepted suggestion.

- **Pattern C — Batch script**  
  A Python script runs outside an LLM: it reads a schematic, extracts windows, runs the model for critique and suggest_repair, writes a report (and optionally a list of edit JSONs). A human or a separate process then applies edits via KiCad. This is the simplest integration and requires only the inference + graph-extraction pieces above.

- **Pattern D — A2A remote subagent**  
  This system is exposed as a **remote subagent** using the [Agent-to-Agent (A2A) protocol](https://geminicli.com/docs/core/remote-agents/). A parent agent (e.g. Gemini CLI or another A2A client) delegates schematic refinement to this service by connecting to its A2A agent card endpoint. The parent sends the current schematic state (or window graph tensors); the subagent returns structured edits (critique, suggest_repair, ranking). The parent then applies edits via EDA tools. See §8.7 for the A2A reference and configuration.

### 8.5 A2A response schema

The A2A sub-agent returns edits in a canonical, executor-friendly schema: each edit is an object with `action`, `target_node_ids`, optional `anchor_node_id`, `delta_x_mm`, `delta_y_mm`, `delta_rotation_deg`, and optional `reason` and `issue_tags`. The top-level response is `{ "edits": [ ... ] }`. This matches the pipeline’s `RepairLabel` in `src/data_pipeline/types.py` and is defined in [SPEC §9 A2A sub-agent edit output](docs/qwen-schematic-v1/SPEC.md).

### 8.6 Summary

To incorporate this repo with an agent that suggests and fixes placements and wires: treat the **model as a service** that consumes graph tensors and returns JSON; implement an **inference entrypoint** and **runtime graph extraction** so the agent can feed it real schematic windows; and implement **edit execution** in the agent’s environment (KiCad/MCP tools) that maps the model’s edit JSON to concrete schematic changes. The agent’s job is to choose windows, call the reasoner, and apply (or present) the suggested fixes.

### 8.7 A2A protocol (remote subagents)

**Reference:** [Remote Subagents (experimental) | Gemini CLI](https://geminicli.com/docs/core/remote-agents/).

The **Agent-to-Agent (A2A) protocol** allows a parent agent (e.g. Gemini CLI) to delegate tasks to remote subagents. To use this repo as an A2A remote subagent for schematic placement/wire refinement:

- **Expose an A2A-compliant service** that implements the protocol (e.g. an HTTP server exposing the agent's **agent card** and task endpoints). The agent card URL is what the parent uses to discover and connect to the subagent.
- **Configuration on the parent side** (e.g. Gemini CLI): remote subagents are defined in Markdown files (`.gemini/agents/*.md`) with YAML frontmatter. Example for a single schematic-fix subagent:

  ```yaml
  ---
  kind: remote
  name: schematic-fix-pass
  agent_card_url: https://your-service.example.com/agent-card
  ---
  ```

  The parent must have `experimental.enableAgents: true` in its settings to use remote subagents.
- **Subagent behaviour:** This repo's inference layer (once implemented) becomes the backend for that service: it receives tasks (e.g. "fix placement pass" with schematic state or graph tensors), runs the model, and returns the response (e.g. list of edits) in the format the A2A protocol expects.

Sample A2A agent implementations are referenced in the [Gemini CLI docs](https://geminicli.com/docs/core/remote-agents/) (e.g. ADK Samples, ADK Python Contributing Samples). Implementing the schematic reasoner as an A2A server (agent card + task endpoint) allows any A2A client (including Gemini CLI) to use it as a remote subagent for placement and wire-fix passes.

---

## 9. Repository Layout Summary

| Component | Path | Role |
|-----------|------|------|
| Graph schema & validation | `src/data/graph_schema.py` | Node/edge types, bucketing, `GraphWindowSample`, `validate_sample` |
| Window sampling | `src/data/window_sampler.py` | `sample_window`, radius-based subgraph extraction |
| Graph encoder | `src/models/graph_encoder.py` | `SchematicGraphEncoder`, `GraphBatch`, graph transformer layers |
| Projector | `src/models/projector.py` | `GraphToQwenProjector` 384→1024 MLP |
| Schematic fusion | `src/models/qwen_schematic_model.py` | Fuse graph + text embeddings, mask, labels; call decoder |
| Top-level model | `src/models/qwen35_graph_model.py` | Load/save, freeze, generate, from_pretrained |
| Training | `src/train/train_qwen35_graph.py` | Dataset, collator, training loop, checkpointing |
| Data pipeline | `src/data_pipeline/jobs/*.py`, `cli.py` | mine → parse → extract → … → geometry → split |
| Specs & plans | `docs/qwen-schematic-v1/*.md` | SPEC, DATASET_SPEC, IMPLEMENTATION_PLAN, etc. |

---

## 10. Distributed training and multi-device

The current trainer is single-device (one GPU or CPU per process). To use multiple machines (e.g. a 4070 laptop and a 3070 PC) you can either **run one training job across both** or **split work between them**.

### 10.1 Running one job across both GPUs (multi-node DDP)

**Implementation status:** DDP is implemented. The trainer detects distributed mode when `RANK` and `WORLD_SIZE` are set (e.g. by `torchrun`), initializes the process group, wraps the model in `DistributedDataParallel`, uses a `DistributedSampler` for the training set, and restricts checkpoint and metrics writes to rank 0. See **docs/DISTRIBUTED_TRAINING.md** for single-node multi-GPU launch, multi-node commands, and Docker client usage (build, env vars, volume mounts, example `docker run` for two nodes).

**Idea:** PyTorch Distributed Data Parallel (DDP) with two processes, one per machine, sharing gradients so you get a single logical training run with effective batch size = per-device batch × 2.

**Requirements:**

- Both machines on the same LAN with open ports for the process group (NCCL for GPU, or GLOO if you fall back to CPU for coordination).
- One machine acts as “master”: it runs first and advertises `MASTER_ADDR` and `MASTER_PORT`; the other uses those to join.
- Same codebase and dataset (or sharded dataset) on both; same Python/ PyTorch/CUDA versions recommended.

**Typical steps:**

1. **Master (e.g. 3070 PC):**
   ```bash
   export MASTER_ADDR=192.168.1.100   # this machine's IP
   export MASTER_PORT=29500
   torchrun --nnodes=2 --node_rank=0 --nproc_per_node=1 \
     --rdzv_backend=c10d --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
     -m src.train.train_qwen35_graph --model ... --dataset ... --output-dir ...
   ```
2. **Worker (e.g. 4070 laptop):**
   ```bash
   export MASTER_ADDR=192.168.1.100   # master's IP
   export MASTER_PORT=29500
   torchrun --nnodes=2 --node_rank=1 --nproc_per_node=1 \
     --rdzv_backend=c10d --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
     -m src.train.train_qwen35_graph --model ... --dataset ... --output-dir ...
   ```

The trainer now does the following when run under `torchrun`: wraps the model with `DistributedDataParallel`, uses a `DistributedSampler` for training (with `set_epoch` per epoch), initializes the process group (NCCL for CUDA, gloo otherwise), binds each process to one device via `LOCAL_RANK`, and saves checkpoints and metrics only from rank 0 (with a barrier before writes). This gives you **data parallelism across two GPUs** and lets you scale batch size or finish a run faster.

### 10.2 Splitting work between machines (no DDP)

**Idea:** Keep the current single-device trainer and use the 4070 and 3070 as **separate workers** for different jobs. No change to the codebase; you only orchestrate who runs what.

**Ways to split load:**

- **Different runs (seeds / configs):** Run multiple experiments in parallel, e.g. seed 42 on the 4070 and seed 123 on the 3070, or different `--lr-graph` / `--max-text-len`. Good for hyperparameter search or robustness checks.
- **Data splits:** Split the JSONL dataset into two files (e.g. by schematic or by hash of sample id). Train one model on the 4070 on part A and another on the 3070 on part B; later you can either keep two models or combine them (e.g. one as primary and one for ensemble or distillation—custom workflow).
- **Train vs everything else:** Use one machine for long training runs and the other for data generation (`geometry` job, `split`), evals, or inference. For example: 3070 runs `train_qwen35_graph` overnight; 4070 runs the data pipeline or `fix_placement_pass` tests.

**Practical setup:**

- Sync code and data via git and a shared drive, NAS, or cloud storage (e.g. S3, GCS, or a mounted folder).
- Use the same `--output-dir` layout per run (e.g. `output_dir/run_seed42/`, `output_dir/run_seed123/`) so you can copy final checkpoints to a single place.
- Optionally use a job queue (e.g. Celery, Redis Queue, or a simple script that SSHes into the other machine and runs a command) so one machine can enqueue “train with these args” on the other.

### 10.3 Recommendation for this repo

- **Fastest path:** Use **§10.2**. Run two independent training jobs (different seeds or data shards) on the 4070 and 3070; or use one for training and one for data/eval/inference. No code changes.
- **Best scaling for a single run:** Use **§10.1** (DDP is implemented): launch with `torchrun` or the Docker training client so one training run uses both GPUs with a larger effective batch size and faster wall-clock time. See **docs/DISTRIBUTED_TRAINING.md**.

---

## 11. Conclusion

This repository implements a **graph-conditioned language model** for local schematic reasoning: schematic windows are represented as heterogeneous graphs with typed nodes and edges and bucketed geometry; a graph transformer encoder and an MLP projector produce a fixed number of graph tokens that are prepended to the text input of Qwen3.5-0.8B; the decoder is trained (with optional freezing) to generate strict JSON for ranking, critique, and one-step edit tasks. The data pipeline turns schematic sources and perturbations into JSONL with `graph_tensor` and targets, and the training script batches these into graph + text sequences with causal LM loss. The design prioritizes explicit structure, bounded context, and machine-parseable outputs to support integration into schematic authoring and review tools.

---

## References

- [Remote Subagents (A2A protocol) | Gemini CLI](https://geminicli.com/docs/core/remote-agents/) — reference for Agent-to-Agent protocol and remote subagent configuration (agent card URL, `kind: remote`, parent-side setup).
- Qwen3.5 model card and tokenizer (Hugging Face).
- LLaVA / vision-language “encoder + projector + LLM” architecture.
- Graph transformers and message passing (e.g., relational bias, type embeddings).
- KiCad schematic file format and EDA conventions.
