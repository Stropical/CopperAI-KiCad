# Training on multiple computers

This repo’s trainer (`python -m spatial_layout.train`) is **single-process, single-GPU**. There is no built-in multi-node API. You can still use several machines in three practical ways:

## 1. Embarrassingly parallel (simplest)

Run **different experiments** on each box (different learning rate, batch size, dropout, augmentation, etc.), each with its own `--checkpoint_dir`, and compare `val/ce_loss` / `METRICS` in logs or W&B.

- **Pros**: No code changes, linear scaling of your hyperparameter search.
- **Cons**: Each run is independent; not one large training job.

## 2. Data / config parity across nodes (same recipe)

Mount or copy the **same** `data_dir` (e.g. `corpus.dense.bin` on shared storage, or rsync the file) so every machine sees identical data. Use the same CLI flags except `--wandb-run-name` / seed. Aggregate results in W&B or spreadsheets.

## 3. True multi-GPU / multi-node (advanced, not implemented here)

To train **one** model with **multiple GPUs or hosts**, you would add **PyTorch DistributedDataParallel (DDP)** and launch with `torchrun`:

- Single machine, multi-GPU: `torchrun --nproc_per_node=N -m spatial_layout.train ...` (requires wrapping the script for DDP).
- Multi-node: same, plus `MASTER_ADDR`, `MASTER_PORT`, world size/rank env vars and a shared filesystem or synchronized checkpoints.

That implies code changes: wrap the model in `DistributedDataParallel`, use a `DistributedSampler` on the training loader, and run validation only on rank 0.

**Pragmatic recommendation** until DDP exists: use **(1)** or **(2)** with `--num_workers` and larger `--batch_size` on each 8GB GPU first; multi-node DDP is only worth it when single-GPU training is clearly bottlenecked and you control the cluster networking.
