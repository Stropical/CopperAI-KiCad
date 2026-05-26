# Build from parent `mcp/experiments` (not from this folder alone): COPY expects pyproject.toml,
# spatial_layout/, and sch2py/ as siblings — e.g.
#   docker build -f spatial_layout/Dockerfile -t spatial-layout:dense-corpus .
# when cwd is mcp/experiments.
FROM pytorch/pytorch:2.5.0-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    TRAIN_DATA_DIR=/opt/spatial_layout/spatial_layout/data_all \
    CHECKPOINT_DIR=/checkpoints

WORKDIR /opt/spatial_layout

RUN python -m pip install --upgrade pip setuptools wheel
RUN pip install "pyarrow>=14" "wandb>=0.22.3"

COPY pyproject.toml /opt/spatial_layout/pyproject.toml
COPY spatial_layout /opt/spatial_layout/spatial_layout
COPY sch2py /opt/spatial_layout/sch2py

RUN pip install -e . --no-deps

# Entrypoint (build context is mcp/experiments — see spatial_layout/scripts/train.sh).
COPY spatial_layout/scripts/docker-entrypoint.sh /usr/local/bin/spatial-layout-entrypoint
RUN chmod +x /usr/local/bin/spatial-layout-entrypoint

VOLUME ["/checkpoints"]

ENTRYPOINT ["/usr/local/bin/spatial-layout-entrypoint"]
CMD ["--epochs", "100", "--batch_size", "8", "--lr", "0.001", "--weight_decay", "0.01", "--dropout", "0.1", "--save_interval", "5", "--block_size", "512", "--n_embd", "128", "--n_head", "4", "--n_layer", "4", "--warmup_steps", "200", "--scheduled_sampling_prob", "0.15", "--label_smoothing", "0.05", "--masked_refine_prob", "0.25", "--masked_refine_weight", "0.25", "--prompt_len", "10", "--max_gen_tokens", "256", "--val_metric_max_batches", "64", "--val_samples_per_batch", "2"]
