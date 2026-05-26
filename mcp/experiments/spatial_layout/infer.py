import torch
import json
from .transformer_model import SpatialTransformer
from .grammar import parse_token_stream
from .json_to_tokens import extract_placement_segment
from .checkpoint import DEFAULT_MODEL_KWARGS, _torch_load, infer_rel_max_bin_from_state_dict

def infer(model_path, vocab, prompt_tokens, max_new_tokens=200, relation_context=None):
    device = torch.device("mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu"))
    
    stoi = vocab
    itos = {i: t for t, i in stoi.items()}
    vocab_size = len(stoi)
    
    blob = _torch_load(model_path, map_location=device)
    
    config = DEFAULT_MODEL_KWARGS.copy()
    if isinstance(blob, dict) and "config" in blob:
        config.update(blob["config"])
    state_dict = blob["model_state_dict"] if isinstance(blob, dict) and "model_state_dict" in blob else blob
    config["rel_max_bin"] = config.get(
        "rel_max_bin",
        infer_rel_max_bin_from_state_dict(state_dict, default=DEFAULT_MODEL_KWARGS["rel_max_bin"]),
    )
        
    pad_idx = vocab.get("<PAD>", 0)
    if isinstance(blob, dict) and "pad_idx" in blob:
        pad_idx = blob["pad_idx"]

    model = SpatialTransformer(
        vocab_size,
        pad_idx=pad_idx,
        n_embd=config["n_embd"],
        n_head=config["n_head"],
        n_layer=config["n_layer"],
        block_size=config["block_size"],
        dropout=config.get("dropout", 0.1),
        rel_max_bin=config.get("rel_max_bin", DEFAULT_MODEL_KWARGS["rel_max_bin"]),
    ).to(device)
    
    if isinstance(blob, dict) and "model_state_dict" in blob:
        model.load_state_dict(blob["model_state_dict"])
    else:
        model.load_state_dict(blob)
        
    model.eval()
    
    unk = stoi.get("<UNK>", pad_idx)
    input_ids = torch.tensor(
        [stoi.get(t, unk) for t in prompt_tokens], dtype=torch.long
    ).unsqueeze(0).to(device)

    eos_id = vocab.get("EOS")
    output_ids = model.generate(
        input_ids,
        max_new_tokens=max_new_tokens,
        pad_idx=pad_idx,
        eos_id=eos_id,
        relation_context=relation_context,
    )
    
    output_tokens = [itos[i.item()] for i in output_ids[0]]
    return output_tokens

def reconstruct_objects(tokens):
    blocks = parse_token_stream(extract_placement_segment(tokens))
    results = []
    for b in blocks:
        for obj in b.objects:
            results.append({
                "ref": obj.ref,
                "type": obj.type,
                "role": obj.role,
                "topology": obj.topology,
                "anchor_ref": obj.anchor_ref,
                "anchor_pin": obj.anchor_pin,
                "block_anchor_ref": b.anchor_ref,
                "block_anchor_pin": b.anchor_pin,
                "page_grid_x": b.page_grid_x,
                "page_grid_y": b.page_grid_y,
                "value_bin": obj.value_bin,
                "package_bin": obj.package_bin,
                "net_role": obj.net_role,
                "pose": {
                    "side": obj.pose.side,
                    "dx_bin": obj.pose.dx_bin,
                    "dy_bin": obj.pose.dy_bin,
                    "dist": obj.pose.dist,
                    "rotation": obj.pose.rotation,
                    "mirror": obj.pose.mirror
                }
            })
    return results
