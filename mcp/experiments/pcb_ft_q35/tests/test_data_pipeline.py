import importlib
import json
import sys
import types
from collections.abc import Iterable
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record))
            handle.write("\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _install_job_module(monkeypatch: pytest.MonkeyPatch, module_name: str, run_fn):
    module = types.ModuleType(module_name)
    module.run = run_fn
    monkeypatch.setitem(sys.modules, module_name, module)


def _import_cli_module():
    last_error = None
    for module_name in ("data_pipeline.cli", "src.data_pipeline.cli"):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            last_error = exc
    raise AssertionError(f"Could not import data pipeline CLI module: {last_error}")


def _job_module_name(cli_module, job_name: str) -> str:
    if not cli_module.__package__:
        raise AssertionError("CLI module has no package context")
    return f"{cli_module.__package__}.jobs.{job_name}"


@pytest.mark.parametrize(
    "job_name",
    ["mine", "parse", "extract", "cluster", "perturb", "score", "geometry", "split", "harden", "benchmark"],
)
def test_cli_wires_each_subcommand_to_matching_job_module(
    job_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cli = _import_cli_module()

    calls: list[tuple[list[dict], dict]] = []

    def _run(records, **kwargs):
        captured = [dict(item) for item in records]
        calls.append((captured, dict(kwargs)))
        return [{"job": job_name, "n": len(captured), "seed": kwargs.get("seed")}]

    _install_job_module(monkeypatch, _job_module_name(cli, job_name), _run)

    input_path = tmp_path / f"{job_name}_in.jsonl"
    output_path = tmp_path / f"{job_name}_out.jsonl"
    _write_jsonl(input_path, [{"id": 1}, {"id": 2}])

    exit_code = cli.main(
        [
            job_name,
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--seed",
            "7",
            "--batch-size",
            "4",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == [{"id": 1}, {"id": 2}]
    assert calls[0][1]["seed"] == 7
    assert calls[0][1]["batch_size"] == 4

    assert _read_jsonl(output_path) == [{"job": job_name, "n": 2, "seed": 7}]


def test_perturb_is_deterministic_for_same_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cli = _import_cli_module()

    def _run(records, seed=None, **kwargs):
        seed = 0 if seed is None else seed
        output = []
        for idx, record in enumerate(records):
            clone = json.loads(json.dumps(record))
            nodes = clone["graph"]["nodes"]
            pick = nodes[(seed + idx) % len(nodes)]["id"]
            clone["perturbed_node"] = pick
            output.append(clone)
        return output

    _install_job_module(monkeypatch, _job_module_name(cli, "perturb"), _run)

    records = [
        {
            "graph_id": "g1",
            "graph": {
                "nodes": [{"id": "U1"}, {"id": "R1"}, {"id": "C1"}],
                "edges": [{"source": "U1", "target": "R1"}],
            },
        },
        {
            "graph_id": "g2",
            "graph": {
                "nodes": [{"id": "U2"}, {"id": "R2"}, {"id": "C2"}],
                "edges": [{"source": "U2", "target": "C2"}],
            },
        },
    ]
    input_path = tmp_path / "perturb_in.jsonl"
    out_a = tmp_path / "perturb_out_a.jsonl"
    out_b = tmp_path / "perturb_out_b.jsonl"
    out_c = tmp_path / "perturb_out_c.jsonl"
    _write_jsonl(input_path, records)

    assert (
        cli.main(["perturb", "-i", str(input_path), "-o", str(out_a), "--seed", "11"]) == 0
    )
    assert (
        cli.main(["perturb", "-i", str(input_path), "-o", str(out_b), "--seed", "11"]) == 0
    )
    assert (
        cli.main(["perturb", "-i", str(input_path), "-o", str(out_c), "--seed", "12"]) == 0
    )

    assert _read_jsonl(out_a) == _read_jsonl(out_b)
    assert _read_jsonl(out_a) != _read_jsonl(out_c)


def test_score_is_deterministic_for_same_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cli = _import_cli_module()

    def _run(records, seed=None, **kwargs):
        seed = 0 if seed is None else seed
        scored = []
        for idx, record in enumerate(records):
            block = record["block"]
            nets = block.get("nets", [])
            score = float(seed + idx + len(nets)) / 10.0
            scored.append({"block_id": record["block_id"], "score": score})
        return scored

    _install_job_module(monkeypatch, _job_module_name(cli, "score"), _run)

    records = [
        {"block_id": "b1", "block": {"nets": ["VDD", "GND"], "components": ["U1", "R1"]}},
        {"block_id": "b2", "block": {"nets": ["CLK"], "components": ["U2"]}},
    ]
    input_path = tmp_path / "score_in.jsonl"
    out_a = tmp_path / "score_out_a.jsonl"
    out_b = tmp_path / "score_out_b.jsonl"
    out_c = tmp_path / "score_out_c.jsonl"
    _write_jsonl(input_path, records)

    assert cli.main(["score", "-i", str(input_path), "-o", str(out_a), "--seed", "3"]) == 0
    assert cli.main(["score", "-i", str(input_path), "-o", str(out_b), "--seed", "3"]) == 0
    assert cli.main(["score", "-i", str(input_path), "-o", str(out_c), "--seed", "4"]) == 0

    assert _read_jsonl(out_a) == _read_jsonl(out_b)
    assert _read_jsonl(out_a) != _read_jsonl(out_c)


def _import_jobs_module():
    last_error = None
    for module_name in ("data_pipeline.jobs", "src.data_pipeline.jobs"):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            last_error = exc
    raise AssertionError(f"Could not import data pipeline jobs module: {last_error}")


def test_job_registry_loads_real_modules():
    jobs = _import_jobs_module()
    assert {"cluster", "geometry", "split", "harden", "benchmark"}.issubset(set(jobs.JOB_NAMES))
    for job_name in jobs.JOB_NAMES:
        module = jobs.get_job_module(job_name)
        assert callable(getattr(module, "run", None))


def test_harden_expands_curated_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = importlib.import_module("src.data_pipeline.jobs.harden")

    schematic_path = tmp_path / "demo.kicad_sch"
    schematic_path.write_text("(kicad_sch)", encoding="utf-8")

    class FakeInstance:
        def __init__(self, reference):
            self.reference = reference

    class FakeSheet:
        def __init__(self):
            self.instances = [FakeInstance("U1"), FakeInstance("U2"), FakeInstance("U3")]

    class FakeBaseEnv:
        def __init__(self):
            self._sheet = FakeSheet()

    class FakeAttachment:
        wire_endpoints = [(0, 0)]
        junction_indices = []
        label_indices = []
        global_label_indices = []
        power_symbol_indices = []

    class FakeEnv:
        def __init__(self, *args, **kwargs):
            self.difficulty = kwargs.get("difficulty", "hard")
            self.base_env = FakeBaseEnv()

        def reset(self, *, seed=None, options=None):
            seed = 0 if seed is None else int(seed)
            options = options or {}
            # Respect preferred_reference from options if provided
            pref_ref = options.get("preferred_reference")
            if pref_ref:
                selected_reference = pref_ref
            else:
                selected_reference = f"U{seed % 3 + 1}"
            secondary_reference = f"C{seed % 2 + 1}"
            info = {
                "selected_reference": selected_reference,
                "secondary_reference": secondary_reference,
                "requested_perturb_dx_mm": options.get("preferred_perturb_dx_mm", 10.16 + float(seed % 2) * 2.54),
                "requested_perturb_dy_mm": options.get("preferred_perturb_dy_mm", 0.0),
                "secondary_perturb_dx_mm": 0.0,
                "secondary_perturb_dy_mm": -10.16,
                "selected_attachment_total": 4,
                "secondary_attachment_total": 2,
                "candidate_count": 8,
                "candidate_kind_cluster_inverse_count": 1,
                "candidate_kind_cluster_half_inverse_count": 1,
                "candidate_kind_cluster_compact_cw_count": 1,
                "perturb_move_magnitude_mm": 12.7,
                "required_connections": 6,
                "num_instances": 12,
            }
            return {}, info

        def _collect_movable_instances(self):
            return [
                (0, FakeAttachment()),
                (1, FakeAttachment()),
                (2, FakeAttachment()),
            ]

        def close(self):
            return None

    monkeypatch.setattr(module, "RealKiCadCleanupEnv", FakeEnv)

    records = [
        {
            "schematic_path": str(schematic_path),
            "project_id": "demo",
            "quality_score": 0.92,
            "quality_bucket": "high",
            "rl_eligible": True,
        }
    ]
    expanded = module.run(
        records,
        attempts_per_schematic=4,
        max_episodes_per_schematic=3,
        min_quality_score=0.5,
        min_hardness_score=0.1,
        difficulty="hard",
        seed=7,
    )

    assert len(expanded) == 3
    assert all(row["difficulty"] == "hard" for row in expanded)
    assert all(row["episode_kind"] == "hard_placement" for row in expanded)
    assert all(row["preferred_reference"].startswith("U") for row in expanded)
    assert all(row["preferred_secondary_reference"].startswith("C") for row in expanded)
    assert all(float(row["hardness_score"]) >= 0.1 for row in expanded)


def test_real_perturb_then_score_pipeline():
    jobs = _import_jobs_module()
    extracted = [
        {
            "block_id": "blk:test:0001",
            "family_id": "fam:test",
            "project_id": "proj_demo",
            "project_root": "demo",
            "schematic_path": "demo.kicad_sch",
            "anchor_node_id": "U1",
            "block_type": "mixed_signal",
            "nodes": [
                {"node_id": "U1", "node_type": "symbol", "x": 0.0, "y": 0.0, "label": "U1", "symbol_class": "ic"},
                {"node_id": "C1", "node_type": "symbol", "x": 2.0, "y": 0.0, "label": "C1", "symbol_class": "passive"},
                {"node_id": "C2", "node_type": "symbol", "x": 2.0, "y": 2.0, "label": "C2", "symbol_class": "passive"},
            ],
            "edges": [
                {"src": "U1", "dst": "C1", "edge_type": "wire_segment"},
                {"src": "U1", "dst": "C2", "edge_type": "wire_segment"},
            ],
        }
    ]

    perturbed = jobs.run_job("perturb", extracted, seed=7, max_variants=2)
    assert isinstance(perturbed, list)
    assert len(perturbed) == 2
    assert all("repair_label" in item for item in perturbed)

    scored = jobs.run_job("score", perturbed, seed=7)
    assert isinstance(scored, list)
    assert len(scored) == len(perturbed)
    assert all(0.0 <= float(item["score"]) <= 1.0 for item in scored)
    assert all(item["preferred_candidate"] in (0, 1) for item in scored)


def _as_record_list(result):
    if result is None:
        return []
    if isinstance(result, dict):
        nested = result.get("records")
        if isinstance(nested, Iterable) and not isinstance(nested, (str, bytes, dict)):
            return list(nested)
        return [result]
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        return list(result)
    return [result]


def _collect_geometry_task_names(records: list[dict]) -> set[str]:
    expected = {"rank_candidates", "tag_issues", "suggest_repair", "score_block"}
    found: set[str] = set()

    def _walk(value):
        if isinstance(value, dict):
            for nested in value.values():
                _walk(nested)
            return
        if isinstance(value, list):
            for nested in value:
                _walk(nested)
            return
        if isinstance(value, str) and value in expected:
            found.add(value)

    _walk(records)
    return found


def test_real_score_to_geometry_emits_expected_tasks_and_is_seed_deterministic():
    jobs = _import_jobs_module()
    score_records = [
        {
            "sample_type": "ranking",
            "block_id": "blk:geom:0001:var1",
            "family_id": "fam:geom:0001",
            "source_project": "proj_demo",
            "score": 0.26,
            "clean_score": 0.87,
            "issue_tags": ["alignment_break", "distance_increase"],
            "preferred_candidate": 0,
            "repair_action": {
                "action": "move_symbol",
                "target_node_ids": ["C1"],
                "delta_x_mm": -1.4,
                "delta_y_mm": 0.0,
                "delta_rotation_deg": 0.0,
                "issue_tags": ["alignment_break"],
            },
            "critique_target": "alignment_break",
            "normalized_graph": {
                "block_id": "blk:geom:0001:var1",
                "project_metadata": {"project_id": "proj_demo"},
                "source_metadata": {"source_id": "src_demo", "source_type": "unit_test"},
                "anchor_node_id": "U1",
                "block_type": "mixed_signal",
                "metadata": {"family_id": "fam:geom:0001"},
                "nodes": [
                    {"node_id": "U1", "node_type": "symbol", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 0.0},
                    {"node_id": "C1", "node_type": "symbol", "x_mm": 15.0, "y_mm": 3.2, "rotation_deg": 23.0},
                    {"node_id": "R1", "node_type": "symbol", "x_mm": 10.0, "y_mm": 0.0, "rotation_deg": 0.0},
                ],
                "edges": [
                    {"edge_id": "e1", "src_node_id": "U1", "dst_node_id": "C1", "edge_type": "wire_segment"},
                    {"edge_id": "e2", "src_node_id": "U1", "dst_node_id": "R1", "edge_type": "wire_segment"},
                ],
            },
            "ranking": {
                "sample_id": "rank_geom_0001",
                "clean_block_id": "blk:geom:0001:clean",
                "perturbed_block_id": "blk:geom:0001:var1",
                "clean_score": 0.87,
                "perturbed_score": 0.26,
                "preferred_index": 0,
                "pairwise_label": 1,
                "issue_tags": ["alignment_break", "distance_increase"],
                "reason": "alignment_break",
                "metadata": {"seed": 19},
            },
        },
        {
            "sample_type": "ranking",
            "block_id": "blk:geom:0002:var1",
            "family_id": "fam:geom:0002",
            "source_project": "proj_demo",
            "score": 0.41,
            "clean_score": 0.91,
            "issue_tags": ["rotation_awkwardness", "spread_group"],
            "preferred_candidate": 0,
            "repair_action": {
                "action": "rotate_symbol",
                "target_node_ids": ["U2"],
                "delta_x_mm": 0.0,
                "delta_y_mm": 0.0,
                "delta_rotation_deg": -90.0,
                "issue_tags": ["rotation_awkwardness"],
            },
            "critique_target": "rotation_awkwardness",
            "normalized_graph": {
                "block_id": "blk:geom:0002:var1",
                "project_metadata": {"project_id": "proj_demo"},
                "source_metadata": {"source_id": "src_demo", "source_type": "unit_test"},
                "anchor_node_id": "U2",
                "block_type": "power",
                "metadata": {"family_id": "fam:geom:0002"},
                "nodes": [
                    {"node_id": "U2", "node_type": "symbol", "x_mm": 0.0, "y_mm": 0.0, "rotation_deg": 45.0},
                    {"node_id": "L1", "node_type": "symbol", "x_mm": 20.0, "y_mm": 16.0, "rotation_deg": 0.0},
                    {"node_id": "C2", "node_type": "symbol", "x_mm": 26.0, "y_mm": 18.0, "rotation_deg": 0.0},
                ],
                "edges": [
                    {"edge_id": "e3", "src_node_id": "U2", "dst_node_id": "L1", "edge_type": "wire_segment"},
                    {"edge_id": "e4", "src_node_id": "L1", "dst_node_id": "C2", "edge_type": "wire_segment"},
                ],
            },
            "ranking": {
                "sample_id": "rank_geom_0002",
                "clean_block_id": "blk:geom:0002:clean",
                "perturbed_block_id": "blk:geom:0002:var1",
                "clean_score": 0.91,
                "perturbed_score": 0.41,
                "preferred_index": 0,
                "pairwise_label": 1,
                "issue_tags": ["rotation_awkwardness", "spread_group"],
                "reason": "rotation_awkwardness",
                "metadata": {"seed": 19},
            },
        },
    ]

    run_a = _as_record_list(jobs.run_job("geometry", score_records, seed=19))
    run_b = _as_record_list(jobs.run_job("geometry", score_records, seed=19))

    assert run_a == run_b
    assert len(run_a) > 0
    emitted_tasks = _collect_geometry_task_names(run_a)
    assert {"rank_candidates", "tag_issues", "suggest_repair", "score_block"}.issubset(
        emitted_tasks
    )


def test_split_is_deterministic_and_group_consistent():
    jobs = _import_jobs_module()
    records = [
        {"sample_id": "s1", "task": "score_block", "family_id": "fam:a", "source_project": "p1"},
        {"sample_id": "s2", "task": "tag_issues", "family_id": "fam:a", "source_project": "p2"},
        {"sample_id": "s3", "task": "suggest_repair", "family_id": "fam:b", "source_project": "p1"},
        {"sample_id": "s4", "task": "rank_candidates", "family_id": "fam:c", "source_project": "p3"},
    ]

    run_a = _as_record_list(jobs.run_job("split", records, seed=42, split_by="family"))
    run_b = _as_record_list(jobs.run_job("split", records, seed=42, split_by="family"))
    assert run_a == run_b

    by_family = {}
    for row in run_a:
        fam = row["family_id"]
        by_family.setdefault(fam, set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_family.values())


def test_benchmark_computes_expected_metrics():
    jobs = _import_jobs_module()
    eval_rows = [
        {"task": "rank_candidates", "target": {"best_index": 1}, "prediction": {"best_index": 1}},
        {"task": "rank_candidates", "target": {"best_index": 0}, "prediction": {"best_index": 1}},
        {"task": "tag_issues", "target": {"issues": ["a", "b"]}, "prediction": {"issues": ["a", "c"]}},
        {"task": "suggest_repair", "target": {"action": {"action": "move_symbol", "target_node_ids": ["C1"]}}, "prediction": {"action": {"action": "move_symbol", "target_node_ids": ["C1"]}}},
        {"task": "score_block", "target": {"score": 0.9}, "prediction": {"score": 0.7}},
    ]

    summary_rows = _as_record_list(jobs.run_job("benchmark", eval_rows, use_oracle_if_missing=False))
    assert len(summary_rows) == 1
    summary = summary_rows[0]
    metrics = summary["metrics"]

    assert metrics["rank_top1_accuracy"] == pytest.approx(0.5)
    assert metrics["repair_action_accuracy"] == pytest.approx(1.0)
    assert metrics["score_mae"] == pytest.approx(0.2)
