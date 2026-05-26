# Training client image for pcb_ft_q35 (single- or multi-node DDP).
# Base: NGC PyTorch with CUDA and NCCL for multi-node GPU training.
ARG PYTORCH_IMAGE=nvcr.io/nvidia/pytorch:24.06-py3
FROM ${PYTORCH_IMAGE}

WORKDIR /app

# Project and dependencies (transformers for Qwen3.5 and tokenizer)
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . "transformers>=4.45"

# Optional: CPU-only base for CI (build with --build-arg PYTORCH_IMAGE=pytorch/pytorch:2.5.1-cpu)
# Then use backend=gloo and no GPU.

# Entrypoint runs torchrun with env-driven DDP; pass trainer args after image.
# Env: MASTER_ADDR, MASTER_PORT, NNODES, NODE_RANK, NPROC_PER_NODE (default 1)
# Mounts: -v /path/to/data:/data -v /path/to/output:/output
COPY scripts/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV MASTER_ADDR=127.0.0.1
ENV MASTER_PORT=29500
ENV NNODES=1
ENV NODE_RANK=0
ENV NPROC_PER_NODE=1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD [
  "--model", "Qwen/Qwen3.5-0.8B",
  "--dataset", "/data/geometry_tasks_split.jsonl",
  "--output-dir", "/output/run1",
  "--freeze-decoder"
]
