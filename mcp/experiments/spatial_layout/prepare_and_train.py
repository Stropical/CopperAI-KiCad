import argparse
from pathlib import Path
import sys

# Add experiment root to path so we can import sch2py and spatial_layout
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sch2py.src.ir import extract_ir
from spatial_layout.dataset import build_vocab_tokens_from_records, write_vocab_sidecar
from spatial_layout.compress_to_parquet import write_parquet_shards
from spatial_layout.json_to_tokens import build_training_record
from spatial_layout.train import train


def _cleanup_legacy_json(data_dir: Path) -> None:
    for pattern in ("*.tokens.json", "*.ir.json"):
        for path in data_dir.rglob(pattern):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _safe_relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument(
        "--batch_size",
        type=int,
        default=16,
        help="Increase when you have spare VRAM (e.g. 24–32 on 8GB cards with this model).",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=4,
        help="DataLoader worker processes; 0 uses the main thread only.",
    )
    parser.add_argument(
        "--no_pin_memory",
        action="store_true",
        help="Pass through to training (CUDA pin_memory).",
    )
    parser.add_argument(
        "--no_persistent_workers",
        action="store_true",
        help="Pass through to training (disable persistent DataLoader workers).",
    )
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--parquet_shard_size", type=int, default=4096)
    parser.add_argument(
        "--data_dir",
        type=str,
        default=str(REPO_ROOT / "spatial_layout" / "data_all"),
        help="Prepared dataset destination (writes vocab and parquet under this directory).",
    )
    parser.add_argument(
        "--dataset_fraction",
        type=float,
        default=1.0,
        help="Fraction of samples to use after shuffle, before train/val split (e.g. 0.1 for quick experiments).",
    )
    parser.add_argument(
        "--checkpoint_dir",
        type=str,
        default=".",
        help="Where training checkpoints should be written.",
    )
    parser.add_argument(
        "--source_root",
        type=str,
        default=None,
        help="Directory to scan for .kicad_sch files. Defaults to the experiments tree.",
    )
    parser.add_argument(
        "--max_files",
        type=int,
        default=0,
        help="Optional cap on the number of schematic files to tokenize (0 means all files).",
    )
    parser.add_argument("--skip_parquet", action="store_true")
    parser.add_argument("--prepare_only", action="store_true")
    parser.add_argument(
        "--train_augmentation",
        action="store_true",
        help="Enable train-split token augmentation during training.",
    )
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", dest="wandb_project", type=str, default="spatial-layout")
    parser.add_argument("--wandb-entity", dest="wandb_entity", type=str, default="")
    parser.add_argument("--wandb-run-name", dest="wandb_run_name", type=str, default="")
    parser.add_argument("--wandb-tags", dest="wandb_tags", type=str, default="")
    parser.add_argument("--wandb-mode", dest="wandb_mode", type=str, default="online")
    parser.add_argument("--wandb-base-url", dest="wandb_base_url", type=str, default="")
    parser.add_argument("--wandb-resume-id", dest="wandb_resume_id", type=str, default="")
    parser.add_argument("--wandb-log-checkpoints", dest="wandb_log_checkpoints", action="store_true")
    args = parser.parse_args()

    # 1. Find all .kicad_sch files
    source_root = Path(args.source_root).resolve() if args.source_root else REPO_ROOT
    print(f"Searching for .kicad_sch files under {source_root}...")
    all_sch_files = list(source_root.rglob("*.kicad_sch"))
    if args.max_files and args.max_files > 0:
        all_sch_files = all_sch_files[: args.max_files]
    print(f"Found {len(all_sch_files)} .kicad_sch files.")

    # 2. Prepare data directory
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Preparing tokenized data in {data_dir}...")

    ok = 0
    skipped = 0
    errors = 0
    parquet_records = []

    for i, sch_path in enumerate(all_sch_files, 1):
        try:
            # Extract IR
            ir_data = extract_ir(str(sch_path))
            
            if not ir_data.get("components"):
                skipped += 1
                continue
            
            ir_minimal = dict(ir_data)
            parquet_records.append(
                build_training_record(
                    ir_minimal,
                    source_path=_safe_relative_path(sch_path, source_root),
                )
            )

            ok += 1
        except Exception as e:
            errors += 1
            # print(f"Error processing {sch_path}: {e}")

        if i % 100 == 0 or i == len(all_sch_files):
            print(f"  [{i}/{len(all_sch_files)}]  ok={ok}  skipped={skipped}  errors={errors}")

    print(f"Data preparation complete. {ok} files ready for training.")

    if ok > 0 and not args.skip_parquet:
        write_vocab_sidecar(data_dir, build_vocab_tokens_from_records(parquet_records))
        parquet_dir = data_dir / "parquet"
        try:
            written = write_parquet_shards(
                parquet_records,
                parquet_dir,
                shard_size=args.parquet_shard_size,
            )
            print(f"Wrote {len(written)} Parquet shard(s) to {parquet_dir}.")
            _cleanup_legacy_json(data_dir)
        except ImportError as exc:
            print(f"Skipping Parquet export: {exc}")
        except Exception as exc:
            print(f"Skipping Parquet export due to error: {exc}")

    if ok < 10:
        print("Not enough data to train. Exiting.")
        return

    if args.prepare_only:
        print("Prepare-only mode requested; skipping training.")
        return

    # 3. Start training
    print(f"Starting training for {args.epochs} epochs...")
    
    # We need to mock the args for the train function
    train_args = argparse.Namespace(
        data_dir=str(data_dir),
        dataset_fraction=args.dataset_fraction,
        epochs=args.epochs,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        no_pin_memory=args.no_pin_memory,
        no_persistent_workers=args.no_persistent_workers,
        lr=args.lr,
        weight_decay=0.05,
        dropout=0.2,
        save_interval=5,
        block_size=1536,
        early_stop_patience=5,
        resume=None,
        checkpoint_dir=args.checkpoint_dir,
        n_embd=128,
        n_head=4,
        n_layer=4,
        warmup_steps=200,
        scheduled_sampling_prob=0.15,
        label_smoothing=0.05,
        masked_refine_prob=0.25,
        masked_refine_weight=0.25,
        masked_refine_every=2,
        spatial_token_weight=1.0,
        coord_loss_weight=0.5,
        coord_scale=10.0,
        rel_max_bin=30,
        geometry_alignment_weight=0.10,
        pairwise_geometry_weight=0.05,
        overlap_loss_weight=0.10,
        grammar_mask=True,
        prompt_len=10,
        max_gen_tokens=256,
        val_metric_max_batches=64,
        max_val_forward_batches=0,
        val_samples_per_batch=2,
        curriculum_epochs=3,
        min_curriculum_len=256,
        train_augmentation=args.train_augmentation,
        coord_feat_dropout=0.5,
        no_amp=False,
        amp_dtype="bf16",
        compile_model=False,
        cudnn_benchmark=True,
        progress_interval=25,
        max_train_batches=0,
        wandb=args.wandb,
        wandb_project=args.wandb_project,
        wandb_entity=args.wandb_entity,
        wandb_run_name=args.wandb_run_name,
        wandb_tags=args.wandb_tags,
        wandb_mode=args.wandb_mode,
        wandb_base_url=args.wandb_base_url,
        wandb_resume_id=args.wandb_resume_id,
        wandb_log_checkpoints=args.wandb_log_checkpoints,
    )
    
    train(train_args)

if __name__ == "__main__":
    main()
