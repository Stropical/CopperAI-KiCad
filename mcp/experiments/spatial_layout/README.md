# Spatial Layout Pipeline

This package implements a transformer-based spatial layout generation pipeline for KiCad schematics. It converts schematic IR into a token-based representation, trains a causal transformer on these tokens, and decodes predictions back into structured placement objects.

## Modules

- `token_model.py`: Defines the token vocabulary, dataclasses for objects/poses, and serialization logic.
- `grammar.py`: Token stream parser and sequence validator.
- `json_to_tokens.py`: Converts `sch2py` JSON IR into token sequences. Handles role inference, anchor selection, and quantization.
- `compress_to_parquet.py`: Optional Parquet exporter for tokenized examples.
- `transformer_model.py`: A small GPT-style causal transformer.
- `dataset.py`: PyTorch Dataset and DataLoader for tokenized sequences. Prefers Parquet shards when present.
- `train.py`: Training script for the transformer model.
- `infer.py`: Inference engine and reconstruction of structured objects from tokens.

## Workflow

1. **Convert JSON IR to Tokens**:
   Use `ir_to_tokens(ir)` from `json_to_tokens.py` on your `sch2py` output.
   Write the resulting examples as Parquet shards in the data directory.

2. **Train**:
   ```bash
   python3 -m spatial_layout.train --data_dir ./data --epochs 50
   ```

   If Parquet shards exist in the data directory, the dataset loader will use them automatically.

   To regenerate data without training, use `--prepare_only` on `prepare_and_train.py`.

   To log runs to Weights & Biases, including a self-hosted W&B server, set
   `WANDB_API_KEY` and either export `WANDB_BASE_URL` or pass `--wandb-base-url`:

   ```bash
   export WANDB_API_KEY=...
   export WANDB_BASE_URL=https://wandb.your-company.example
   python3 -m spatial_layout.train \
     --data_dir ./data_all \
     --epochs 50 \
     --checkpoint_dir ./checkpoints \
     --wandb \
     --wandb-project spatial-layout \
     --wandb-run-name spatial-layout-run-01 \
     --wandb-tags gpu,baseline
   ```

   `--wandb-mode` supports `online`, `offline`, and `disabled`. With
   `--wandb-log-checkpoints`, save-interval checkpoints are uploaded as model
   artifacts; the best checkpoint is always uploaded when W&B logging is enabled.

3. **Inference**:
   Use `infer()` and `reconstruct_objects()` from `infer.py` to generate new layouts from prompts.

4. **KiCad file + KiCanvas preview**:
   Turn reconstructed objects into a real `.kicad_sch` (via `sch2py`’s emitter) and view it in the browser with [KiCanvas](https://kicanvas.org/).

   ```bash
   cd mcp/experiments
   PYTHONPATH=. python -m spatial_layout.export_preview \
     --checkpoint /path/to/checkpoint.pt \
     --prompt-tokens BOS \
     --out spatial_layout/preview/preview.kicad_sch
   ```

   Or pass a JSON array of token strings (no model needed):

   ```bash
   PYTHONPATH=. python -m spatial_layout.export_preview \
     --tokens-json tokens.json \
     --out spatial_layout/preview/preview.kicad_sch
   ```

   Serve the preview folder over HTTP (KiCanvas loads the schematic by URL; `file://` is unreliable):

   ```bash
   cd mcp/experiments/spatial_layout/preview
   python3 -m http.server 8765
   ```

   Open `http://localhost:8765/kicanvas.html`. The page loads `kicanvas.js` from `kicanvas.org` and references `preview.kicad_sch` next to the HTML file.

   Placement is **preview geometry**: anchor at (80 mm, 80 mm) plus decoded `DX_*` / `DY_*` bins (inverse of `json_to_tokens` quantization). Nets are not reconstructed from tokens in this export path.

## Vocabulary Categories

- **Structural**: `BOS`, `EOS`, `OBJ_START`, `OBJ_END`, `ANCHOR_BLOCK`, `ANCHOR_BLOCK_END`
- **Type**: `TYPE_IC`, `TYPE_C`, `TYPE_R`, etc.
- **Role**: `ROLE_DECOUP`, `ROLE_PULLUP`, `ROLE_REGULATOR`, etc.
- **Topology**: `TOPO_SHUNT`, `TOPO_INLINE`, `TOPO_BLOCK`, etc.
- **Placement**: `SIDE_UP`, `DX_P1`, `DY_N2`, `DIST_NEAR`, `ROT_90`, etc.
- **Anchor**: `ANCHOR_REF`, `ANCHOR_PIN`

## Development

Run tests with:
```bash
PYTHONPATH=. pytest spatial_layout/tests/
```

## Corpus Build

To regenerate the training corpus as parquet, including legacy token rows plus newly
expanded schematic and block-level tokens from local examples and any scraped GitHub
KiCad repos already present under `mcp/experiments/pcb_ft_q35/data/scraped_schematics/`:

```bash
PYTHONPATH=. python -m spatial_layout.build_corpus --out_dir spatial_layout/data_all
```

The resulting parquet shards live under `spatial_layout/data_all/parquet/` and can
be passed directly to training by pointing `--data_dir` at `spatial_layout/data_all`
or any parent directory that contains it. The loader is parquet-only now; legacy
`*.tokens.json` and `*.ir.json` inputs are no longer consumed.

For the full streamed corpus, including the mirrored Hugging Face `open-schematics`
dataset, write a single dense binary file instead:

```bash
PYTHONPATH=. python -m spatial_layout.materialize_dense_corpus \
  --out_file spatial_layout/data_all/corpus.dense.bin
```

When `corpus.dense.bin` is present, training will prefer it over parquet shards and
stream samples from the binary file without loading the whole corpus into RAM.

## Docker

Build a self-contained training image from `mcp/experiments`:

```bash
cd mcp/experiments
docker build -t yourhub/spatial-layout-train:latest -f spatial_layout/Dockerfile .
```

Run it on a GPU host:

```bash
docker run --rm --gpus all \
  -v "$(pwd)/checkpoints:/checkpoints" \
  -e WANDB_API_KEY \
  -e WANDB_BASE_URL=https://wandb.your-company.example \
  yourhub/spatial-layout-train:latest
```

The baked dataset lives inside the image at `/opt/spatial_layout/data_all`, and training writes checkpoints to `/checkpoints`. Epoch summaries plus a structured `METRICS {...}` line are printed to stdout, so you can scrape logs directly from Docker Hub-backed runs or your GPU server's log collector.
