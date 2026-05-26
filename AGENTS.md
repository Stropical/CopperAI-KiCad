# Training Note

For `mcp/experiments/spatial_layout`, do not run training locally on macOS/MPS.

Use the Windows Docker host with the RTX 4070 instead:

```bash
DOCKER_HOST=tcp://10.0.0.43:2375 docker run --rm --gpus all spatial-layout:iter3 ...
```

Preferred training pattern:

```bash
DOCKER_HOST=tcp://10.0.0.43:2375 docker run --rm --gpus all spatial-layout:iter3 \
  --data_dir /opt/spatial_layout/spatial_layout/data_full_cached_v2 \
  --checkpoint_dir /tmp/checkpoints \
  ...
```

For the original corpus instead of the precomputed one:

```bash
--data_dir /opt/spatial_layout/spatial_layout/data_full
```

If code or training data changes, rebuild the image first:

```bash
DOCKER_HOST=tcp://10.0.0.43:2375 docker build -t spatial-layout:iter3 \
  -f mcp/experiments/spatial_layout/Dockerfile \
  mcp/experiments
```

Verification command:

```bash
DOCKER_HOST=tcp://10.0.0.43:2375 docker run --rm --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Current benchmark reference with the same quick-test settings:

- Local Mac MPS, `data_full`: about `347s` for 3 epochs.
- 4070 worker, `data_full`: about `64s` for 3 epochs.

Conclusion: use the 4070 worker for all future training and training benchmarks.
