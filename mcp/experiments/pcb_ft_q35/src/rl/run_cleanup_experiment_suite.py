"""Launch a small tracked suite of cleanup RL experiments."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.rl.eval_real_kicad_cleanup import build_argparser as build_eval_argparser
from src.rl.eval_real_kicad_cleanup import evaluate
from src.rl.train_real_kicad_cleanup import build_argparser as build_train_argparser
from src.rl.train_real_kicad_cleanup import train


DEFAULT_EXPERIMENTS = [
    {"name": "baseline", "learning_rate": 3e-4, "entropy_coef": 0.01, "hidden_dim": 256},
    {"name": "low_entropy", "learning_rate": 3e-4, "entropy_coef": 0.003, "hidden_dim": 256},
    {"name": "small_hidden", "learning_rate": 3e-4, "entropy_coef": 0.01, "hidden_dim": 128},
]


def _namespace_from_parser(parser: argparse.ArgumentParser) -> argparse.Namespace:
    defaults: dict[str, Any] = {}
    for action in parser._actions:
        if not getattr(action, "dest", None) or action.dest == "help":
            continue
        defaults[action.dest] = action.default
    return argparse.Namespace(**defaults)


def _run_id(suite_name: str, experiment_name: str) -> str:
    token = uuid.uuid4().hex[:8]
    return f"{suite_name}-{experiment_name}-{token}"


def run_suite(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    suite_name = args.suite_name or f"cleanup-suite-{time.strftime('%Y%m%d-%H%M%S')}"

    train_defaults = _namespace_from_parser(build_train_argparser())
    eval_defaults = _namespace_from_parser(build_eval_argparser())
    suite_rows: list[dict[str, Any]] = []

    for experiment in DEFAULT_EXPERIMENTS:
        exp_name = str(experiment["name"])
        exp_dir = output_root / exp_name
        train_dir = exp_dir / "train"
        eval_dir = exp_dir / "eval"
        run_id = _run_id(suite_name, exp_name)

        train_args = argparse.Namespace(**vars(train_defaults))
        train_args.corpus_root = args.corpus_root
        train_args.corpus_index = args.corpus_index
        train_args.difficulty = args.difficulty
        train_args.output_dir = str(train_dir)
        train_args.episodes = args.train_episodes
        train_args.learning_rate = float(experiment["learning_rate"])
        train_args.entropy_coef = float(experiment["entropy_coef"])
        train_args.hidden_dim = int(experiment["hidden_dim"])
        train_args.seed = args.seed
        train_args.device = args.device
        train_args.max_actions = args.max_actions
        train_args.min_instances = args.min_instances
        train_args.max_instances = args.max_instances
        train_args.max_files = args.train_max_files
        train_args.file_offset = args.train_file_offset
        train_args.min_quality_score = args.min_quality_score
        train_args.log_every = args.log_every
        train_args.render_every = args.render_every
        train_args.checkpoint_every = args.checkpoint_every
        train_args.metric_window = args.metric_window
        train_args.image_width = args.image_width
        train_args.image_height = args.image_height
        train_args.wandb = bool(args.wandb)
        train_args.wandb_project = args.wandb_project
        train_args.wandb_entity = args.wandb_entity
        train_args.wandb_run_name = f"{suite_name}-{exp_name}"
        train_args.wandb_tags = ",".join(filter(None, [args.wandb_tags, "suite", exp_name, args.difficulty]))
        train_args.wandb_mode = args.wandb_mode
        train_args.wandb_resume_id = run_id

        train(train_args)

        eval_args = argparse.Namespace(**vars(eval_defaults))
        eval_args.checkpoint = str(train_dir / "policy_final.pt")
        eval_args.corpus_root = args.corpus_root
        eval_args.corpus_index = args.corpus_index
        eval_args.difficulty = args.difficulty
        eval_args.output_dir = str(eval_dir)
        eval_args.episodes = args.eval_episodes
        eval_args.device = args.device
        eval_args.seed = args.eval_seed
        eval_args.max_actions = args.max_actions
        eval_args.min_instances = args.min_instances
        eval_args.max_instances = args.max_instances
        eval_args.max_files = args.eval_max_files
        eval_args.file_offset = args.eval_file_offset
        eval_args.min_quality_score = args.min_quality_score
        eval_args.hidden_dim = int(experiment["hidden_dim"])
        eval_args.render_every = args.eval_render_every
        eval_args.bootstrap_samples = args.bootstrap_samples
        eval_args.image_width = args.image_width
        eval_args.image_height = args.image_height
        eval_args.wandb = bool(args.wandb)
        eval_args.wandb_project = args.wandb_project
        eval_args.wandb_entity = args.wandb_entity
        eval_args.wandb_run_name = f"{suite_name}-{exp_name}-eval"
        eval_args.wandb_tags = ",".join(filter(None, [args.wandb_tags, "suite", exp_name, args.difficulty, "eval"]))
        eval_args.wandb_mode = args.wandb_mode
        eval_args.wandb_resume_id = run_id

        evaluate(eval_args)

        eval_summary_path = eval_dir / "eval_summary.json"
        eval_summary = json.loads(eval_summary_path.read_text(encoding="utf-8")) if eval_summary_path.exists() else {}
        suite_rows.append(
            {
                "experiment": exp_name,
                "run_id": run_id,
                "train_dir": str(train_dir),
                "eval_dir": str(eval_dir),
                "learning_rate": float(experiment["learning_rate"]),
                "entropy_coef": float(experiment["entropy_coef"]),
                "hidden_dim": int(experiment["hidden_dim"]),
                "difficulty": args.difficulty,
                **eval_summary,
            }
        )

    suite_summary_path = output_root / "suite_summary.json"
    suite_summary_path.write_text(json.dumps(suite_rows, indent=2), encoding="utf-8")
    print(json.dumps({"suite_name": suite_name, "suite_summary": str(suite_summary_path)}))
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a small suite of cleanup RL experiments.")
    parser.add_argument("--corpus-root", type=str, default="data/scraped_schematics/files")
    parser.add_argument("--corpus-index", type=str, default="")
    parser.add_argument("--difficulty", type=str, default="medium")
    parser.add_argument("--output-root", type=str, required=True)
    parser.add_argument("--suite-name", type=str, default="")
    parser.add_argument("--train-episodes", type=int, default=120)
    parser.add_argument("--eval-episodes", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--eval-seed", type=int, default=123)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--min-instances", type=int, default=2)
    parser.add_argument("--max-instances", type=int, default=24)
    parser.add_argument("--train-max-files", type=int, default=400)
    parser.add_argument("--eval-max-files", type=int, default=120)
    parser.add_argument("--train-file-offset", type=int, default=0)
    parser.add_argument("--eval-file-offset", type=int, default=400)
    parser.add_argument("--min-quality-score", type=float, default=0.45)
    parser.add_argument("--log-every", type=int, default=20)
    parser.add_argument("--render-every", type=int, default=0)
    parser.add_argument("--eval-render-every", type=int, default=0)
    parser.add_argument("--checkpoint-every", type=int, default=60)
    parser.add_argument("--metric-window", type=int, default=50)
    parser.add_argument("--bootstrap-samples", type=int, default=500)
    parser.add_argument("--image-width", type=int, default=1024)
    parser.add_argument("--image-height", type=int, default=768)
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", type=str, default="pcb-ft-q35-rl-cleanup")
    parser.add_argument("--wandb-entity", type=str, default="")
    parser.add_argument("--wandb-tags", type=str, default="")
    parser.add_argument("--wandb-mode", type=str, default="online")
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
