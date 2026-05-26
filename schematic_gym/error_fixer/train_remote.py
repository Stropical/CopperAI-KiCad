#!/usr/bin/env python3
"""Kick off GNN training on Thor from your laptop. No SSH session needed.

Usage:
    python -m schematic_gym.error_fixer.train_remote                 # 10K episodes
    python -m schematic_gym.error_fixer.train_remote --episodes 50000
    python -m schematic_gym.error_fixer.train_remote --fetch          # download trained model

How it works:
    1. Syncs code to Thor via scp (one-shot, no persistent SSH)
    2. Starts training via a single ssh command (nohup, detaches immediately)
    3. WandB streams metrics to your dashboard in real-time
    4. --fetch downloads the best model when training is done

Dashboard: https://wandb.ai/ethanmarreel-oregon-state-university/schematic-gym-gnn
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


THOR_IP = "192.168.177.193"
THOR_USER = "drail-thor"
THOR_PASS = "robotcassie"
REMOTE_DIR = "/tmp/gnn_train"
WANDB_PROJECT = "schematic-gym-gnn"
WANDB_ENTITY = "ethanmarreel-oregon-state-university"


def _scp(local: str, remote: str) -> None:
    subprocess.run(
        ["sshpass", "-p", THOR_PASS, "scp", "-o", "StrictHostKeyChecking=no",
         "-r", local, f"{THOR_USER}@{THOR_IP}:{remote}"],
        check=True, capture_output=True,
    )


def _ssh(cmd: str, capture: bool = False, timeout: int = 30) -> str:
    result = subprocess.run(
        ["sshpass", "-p", THOR_PASS, "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ServerAliveInterval=5",
         f"{THOR_USER}@{THOR_IP}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    if capture:
        return result.stdout.strip()
    print(result.stdout.strip())
    if result.stderr.strip():
        # Filter out Ubuntu MOTD noise
        for line in result.stderr.strip().split("\n"):
            if not any(skip in line for skip in ("Welcome to", "Documentation", "Management",
                       "Support", "updates can", "security update", "Enable ESM", "ubuntu.com",
                       "Expanded Security", "apt list", "Pseudo-terminal", "pro status",
                       "see these")):
                print(f"  stderr: {line}")
    return result.stdout.strip()


def sync_code():
    """Sync training code to Thor."""
    print("[1/3] Syncing code to Thor...")
    local_root = Path(__file__).resolve().parent.parent.parent

    # Bundle without macOS resource forks
    bundle = tempfile.mktemp(suffix=".tar.gz")
    subprocess.run(
        ["tar", "czf", bundle,
         "--exclude=._*", "--exclude=__pycache__", "--exclude=*.pyc",
         "--exclude=renders", "--exclude=wandb", "--exclude=*.npy",
         "schematic_gym/__init__.py", "schematic_gym/env.py",
         "schematic_gym/core", "schematic_gym/erc", "schematic_gym/reward",
         "schematic_gym/io", "schematic_gym/library", "schematic_gym/rendering",
         "schematic_gym/observations", "schematic_gym/actions",
         "schematic_gym/curriculum", "schematic_gym/scenarios",
         "schematic_gym/error_fixer/__init__.py",
         "schematic_gym/error_fixer/env.py",
         "schematic_gym/error_fixer/injectors.py",
         "schematic_gym/error_fixer/multi_step_env.py",
         "schematic_gym/error_fixer/rl_policy.py",
         "schematic_gym/error_fixer/gnn_policy.py",
         "schematic_gym/error_fixer/train_wandb.py",
         "schematic_gym/pyproject.toml"],
        cwd=str(local_root), check=True, capture_output=True,
        env={**os.environ, "COPYFILE_DISABLE": "1"},
    )
    size = os.path.getsize(bundle)
    print(f"   Bundle: {size // 1024}KB")

    _ssh(f"mkdir -p {REMOTE_DIR}")
    _scp(bundle, f"{REMOTE_DIR}/code.tar.gz")
    _ssh(f"cd {REMOTE_DIR} && tar xzf code.tar.gz 2>/dev/null && "
         f"find . -name '._*' -delete 2>/dev/null && "
         f"rm -f schematic_gym/scenarios/curriculum.json 2>/dev/null && "
         f"sed -i \"s/read_text(encoding=\\\"utf-8\\\")/read_text(encoding=\\\"utf-8\\\", errors=\\\"replace\\\")/g\" "
         f"schematic_gym/library/loader.py 2>/dev/null && "
         f"echo 'from .env import ERCFixerEnv, FixerAction' > schematic_gym/error_fixer/__init__.py && "
         f"echo 'from .injectors import inject_errors, Fault' >> schematic_gym/error_fixer/__init__.py")
    os.unlink(bundle)
    print("   Done")


def start_training(args: argparse.Namespace):
    """Launch training on Thor (detached, streams to WandB)."""
    # Get WandB key
    try:
        import wandb
        wandb_key = wandb.api.api_key
        assert wandb_key
    except Exception:
        print("ERROR: wandb not authenticated. Run: wandb login")
        sys.exit(1)

    print("[2/3] Launching training on Thor...")
    run_name = args.run_name or f"thor-gnn-{args.episodes // 1000}k"

    train_cmd = (
        f"cd {REMOTE_DIR} && "
        f"export WANDB_API_KEY={wandb_key} && "
        f"export PATH=$HOME/.local/bin:$PATH && "
        f"nohup python3 schematic_gym/error_fixer/train_wandb.py "
        f"--device cuda "
        f"--episodes {args.episodes} "
        f"--hidden-dim {args.hidden_dim} "
        f"--heads {args.heads} "
        f"--lr {args.lr} "
        f"--max-steps {args.max_steps} "
        f"--difficulty {args.difficulty} "
        f"--num-faults {args.num_faults} "
        f"--output-dir {REMOTE_DIR}/output "
        f"--wandb-project {WANDB_PROJECT} "
        f"--run-name {run_name} "
        f"--log-interval 200 "
        f"--save-interval 2000 "
        f"</dev/null > {REMOTE_DIR}/train.log 2>&1 & disown"
    )
    try:
        _ssh(train_cmd, timeout=15)
    except subprocess.TimeoutExpired:
        pass  # Expected — nohup process started but SSH didn't return in time
    import time
    time.sleep(3)
    pid = _ssh(f"pgrep -f train_wandb || echo 'not found'", capture=True)

    print(f"   PID: {pid}")
    print("[3/3] Waiting for WandB init...")

    import time
    for _ in range(10):
        time.sleep(3)
        log = _ssh(f"tail -5 {REMOTE_DIR}/train.log 2>/dev/null", capture=True)
        if "View run at" in log:
            for line in log.split("\n"):
                if "View run" in line or "View project" in line:
                    print(f"   {line.strip()}")
            break
        if "episode" in log.lower():
            print("   Training started (WandB syncing)")
            break

    print(f"\n{'='*60}")
    print(f"Training running on Jetson Thor ({THOR_IP})")
    print(f"Dashboard: https://wandb.ai/{WANDB_ENTITY}/{WANDB_PROJECT}")
    print(f"Fetch model: python -m schematic_gym.error_fixer.train_remote --fetch")
    print(f"Stop: python -m schematic_gym.error_fixer.train_remote --stop")


def fetch_model():
    """Download the best/final model from Thor."""
    print("Fetching trained model from Thor...")
    out = Path("schematic_gym/renders/gnn_trained")
    out.mkdir(parents=True, exist_ok=True)
    for fname in ["gnn_policy_best.pt", "gnn_policy_final.pt", "metrics.jsonl"]:
        try:
            _scp(f"{REMOTE_DIR}/output/{fname}", str(out / fname))
            print(f"  Downloaded: {out / fname}")
        except subprocess.CalledProcessError:
            print(f"  Not found: {fname}")


def stop_training():
    """Kill training on Thor."""
    _ssh(f"pkill -f train_wandb 2>/dev/null; echo 'Training stopped'")


def status():
    """Check if training is running on Thor."""
    pid = _ssh(f"pgrep -f train_wandb 2>/dev/null || echo 'not running'", capture=True)
    if "not running" in pid:
        print("No training running on Thor")
    else:
        print(f"Training running (PID: {pid})")
        log = _ssh(f"tail -3 {REMOTE_DIR}/train.log 2>/dev/null", capture=True)
        print(log)


def main():
    parser = argparse.ArgumentParser(description="Remote GNN training on Jetson Thor via WandB")
    parser.add_argument("--episodes", type=int, default=10000)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--difficulty", default="layout_training")
    parser.add_argument("--num-faults", type=int, default=4)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--fetch", action="store_true", help="Download trained model")
    parser.add_argument("--stop", action="store_true", help="Stop training")
    parser.add_argument("--status", action="store_true", help="Check training status")
    parser.add_argument("--no-sync", action="store_true", help="Skip code sync")

    args = parser.parse_args()

    if args.fetch:
        fetch_model()
    elif args.stop:
        stop_training()
    elif args.status:
        status()
    else:
        if not args.no_sync:
            sync_code()
        start_training(args)


if __name__ == "__main__":
    main()
