import os
import torch

DEFAULT_MODEL_KWARGS = {
    "n_embd": 128,
    "n_head": 4,
    "n_layer": 4,
    "block_size": 512,
    "dropout": 0.1,
    "rel_max_bin": 4,
}


def infer_rel_max_bin_from_state_dict(state_dict, default: int = 4) -> int:
    if not isinstance(state_dict, dict):
        return default
    weight = state_dict.get("rel_bias.weight")
    if weight is None or not hasattr(weight, "shape") or len(weight.shape) < 1:
        return default
    size = int(weight.shape[0])
    if size <= 0:
        return default
    grid = int(round(size ** 0.5))
    if grid * grid != size:
        return default
    return max(0, (grid - 1) // 2)

def save_training_checkpoint(path, model_state, vocab, pad_idx, config_dict):
    checkpoint = {
        "model_state_dict": model_state,
        "vocab": vocab,
        "pad_idx": pad_idx,
        "config": config_dict
    }
    torch.save(checkpoint, path)

def resolve_checkpoint_path(path: str, extra_search_dirs=None):
    """
    Find a .pt checkpoint. Checks: as-given, cwd-relative, abspath, this package directory,
    optional extra dirs (e.g. --checkpoint_dir), and basename lookups in those dirs.
    Returns (resolved_path_or_None, list of absolute paths tried).
    """
    if not path or not str(path).strip():
        return None, []
    raw = os.path.expanduser(str(path).strip())
    tried = []
    seen = set()

    def add(p):
        ap = os.path.abspath(os.path.normpath(p))
        if ap not in seen:
            seen.add(ap)
            tried.append(ap)

    add(raw)
    if not os.path.isabs(raw):
        add(os.path.join(os.getcwd(), raw))
        pkg = os.path.dirname(os.path.abspath(__file__))
        add(os.path.join(pkg, raw))
        add(os.path.join(pkg, os.path.basename(raw)))
        add(os.path.join(os.getcwd(), os.path.basename(raw)))
    if extra_search_dirs:
        for d in extra_search_dirs:
            if not d:
                continue
            ad = os.path.abspath(os.path.expanduser(d))
            add(os.path.join(ad, raw))
            add(os.path.join(ad, os.path.basename(raw)))
    for p in tried:
        if os.path.isfile(p):
            return p, tried
    return None, tried


def _torch_load(path, map_location=None):
    # Full training checkpoints contain vocab (dict), not tensors only — PyTorch 2.6+ defaults
    # weights_only=True and would reject them.
    try:
        return torch.load(path, map_location=map_location, weights_only=False)
    except TypeError:
        return torch.load(path, map_location=map_location)
