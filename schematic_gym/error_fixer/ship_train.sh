#!/bin/bash
# Ship GNN training to Jetson Thor and monitor via WandB
#
# Usage:
#   ./ship_train.sh                    # 10K episodes, default settings
#   ./ship_train.sh --episodes 50000   # longer training
#   ./ship_train.sh --hidden-dim 256   # bigger model
#
# Dashboard: https://wandb.ai/ethanmarreel-oregon-state-university/schematic-gym-gnn

set -e

THOR_IP="192.168.177.193"
THOR_USER="drail-thor"
THOR_PASS="robotcassie"
REMOTE_DIR="/tmp/gnn_train"
LOCAL_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Get wandb API key from local config
WANDB_KEY=$(python3 -c "import wandb; print(wandb.api.api_key)" 2>/dev/null)
if [ -z "$WANDB_KEY" ]; then
    echo "ERROR: wandb not authenticated locally. Run: wandb login"
    exit 1
fi

echo "=== Shipping GNN training to Jetson Thor ($THOR_IP) ==="

# 1. Bundle code (exclude macOS resource forks)
echo "[1/4] Bundling code..."
cd "$LOCAL_ROOT"
COPYFILE_DISABLE=1 tar czf /tmp/schematic_gym_train.tar.gz \
  --exclude='._*' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='renders' --exclude='wandb' --exclude='*.npy' \
  schematic_gym/__init__.py \
  schematic_gym/env.py \
  schematic_gym/core/ \
  schematic_gym/erc/ \
  schematic_gym/reward/ \
  schematic_gym/io/ \
  schematic_gym/library/ \
  schematic_gym/rendering/ \
  schematic_gym/observations/ \
  schematic_gym/actions/ \
  schematic_gym/curriculum/ \
  schematic_gym/scenarios/ \
  schematic_gym/error_fixer/__init__.py \
  schematic_gym/error_fixer/env.py \
  schematic_gym/error_fixer/injectors.py \
  schematic_gym/error_fixer/multi_step_env.py \
  schematic_gym/error_fixer/rl_policy.py \
  schematic_gym/error_fixer/gnn_policy.py \
  schematic_gym/error_fixer/train_wandb.py \
  schematic_gym/pyproject.toml \
  2>/dev/null
echo "   Bundle: $(du -h /tmp/schematic_gym_train.tar.gz | cut -f1)"

# 2. Upload to Thor
echo "[2/4] Uploading to Thor..."
sshpass -p "$THOR_PASS" ssh -o StrictHostKeyChecking=no "$THOR_USER@$THOR_IP" "mkdir -p $REMOTE_DIR" 2>/dev/null
sshpass -p "$THOR_PASS" scp /tmp/schematic_gym_train.tar.gz "$THOR_USER@$THOR_IP:$REMOTE_DIR/" 2>/dev/null

# 3. Extract and fix
echo "[3/4] Setting up on Thor..."
sshpass -p "$THOR_PASS" ssh "$THOR_USER@$THOR_IP" "
cd $REMOTE_DIR
tar xzf schematic_gym_train.tar.gz 2>/dev/null
find . -name '._*' -delete 2>/dev/null
rm -f schematic_gym/scenarios/curriculum.json 2>/dev/null

# Fix __init__.py for training-only imports
cat > schematic_gym/error_fixer/__init__.py << 'INITEOF'
from .env import ERCFixerEnv, FixerAction
from .injectors import inject_errors, Fault
INITEOF

# Fix encoding in library loader
sed -i 's/read_text(encoding=\"utf-8\")/read_text(encoding=\"utf-8\", errors=\"replace\")/g' schematic_gym/library/loader.py 2>/dev/null

echo 'Setup complete'
python3 -c 'import torch; print(f\"PyTorch {torch.__version__}, CUDA: {torch.cuda.is_available()}\")' 2>/dev/null || echo 'No torch'
" 2>/dev/null

# 4. Launch training
echo "[4/4] Launching training on Thor GPU..."
sshpass -p "$THOR_PASS" ssh "$THOR_USER@$THOR_IP" "
cd $REMOTE_DIR
export WANDB_API_KEY=$WANDB_KEY
nohup python3 schematic_gym/error_fixer/train_wandb.py \
  --device cuda \
  --output-dir $REMOTE_DIR/output \
  --wandb-project schematic-gym-gnn \
  --run-name thor-gnn-\$(date +%H%M) \
  $@ \
  > $REMOTE_DIR/train.log 2>&1 &
echo \"PID: \$!\"
sleep 5
tail -3 $REMOTE_DIR/train.log
" 2>/dev/null

echo ""
echo "=== Training launched on Jetson Thor ==="
echo "Dashboard: https://wandb.ai/ethanmarreel-oregon-state-university/schematic-gym-gnn"
echo "Monitor:   sshpass -p $THOR_PASS ssh $THOR_USER@$THOR_IP 'tail -f $REMOTE_DIR/train.log'"
echo "Stop:      sshpass -p $THOR_PASS ssh $THOR_USER@$THOR_IP 'pkill -f train_wandb'"
echo "Fetch model: sshpass -p $THOR_PASS scp $THOR_USER@$THOR_IP:$REMOTE_DIR/output/gnn_policy_best.pt ."
