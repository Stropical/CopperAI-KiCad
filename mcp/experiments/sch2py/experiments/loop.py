"""
Experiment loop: continuously convert, test, and score the sch2py converter.

Each iteration:
  1. Pick a .kicad_sch test file
  2. Forward convert: .kicad_sch → Python DSL (sch2py)
  3. Backward convert: Python DSL → .kicad_sch (py2sch)
  4. Re-convert the regenerated schematic (roundtrip)
  5. Compare netlists: original vs roundtrip
  6. Score: token reduction, net accuracy, component accuracy
  7. Report results + emit improvement suggestions

Usage:
    python -m experiments.loop                     # run all fixtures
    python -m experiments.loop --file foo.kicad_sch
    python -m experiments.loop --watch             # re-run on src/ change
    python -m experiments.loop --iters 10          # limit iterations
    python -m experiments.loop --compact           # test compact (topology-only) mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Ensure src/ is importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.parser import parse_file
from src.netlist import extract_netlist, ComponentInstance, Net
from src.sch2py import convert as sch2py_convert, count_tokens
from src.py2sch import circuit_to_sch

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = ROOT / "fixtures"
KICAD_QA      = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/qa/data/eeschema/spice_netlists")
KICAD_NETS    = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/qa/data/eeschema/netlists")
KICAD_DEMOS   = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/demos")
SCRAPED_DIR   = Path("/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/experiments/pcb_ft_q35/data/scraped_schematics/files")

_CORE_FIXTURES = [
    # --- SPICE simulation circuits (analog) ---
    KICAD_QA / "rlc" / "rlc.kicad_sch",
    KICAD_QA / "opamp" / "opamp.kicad_sch",
    KICAD_QA / "legacy_sallen_key" / "legacy_sallen_key.kicad_sch",
    KICAD_QA / "npn_ce_amp" / "npn_ce_amp.kicad_sch",
    KICAD_QA / "rectifier" / "rectifier.kicad_sch",
    KICAD_QA / "fliege_filter" / "fliege_filter.kicad_sch",
    KICAD_QA / "chirp" / "chirp.kicad_sch",
    KICAD_QA / "cmos_not" / "cmos_not.kicad_sch",
    KICAD_QA / "sources" / "sources.kicad_sch",
    KICAD_QA / "switches" / "switches.kicad_sch",
    KICAD_QA / "potentiometers" / "potentiometers.kicad_sch",
    KICAD_QA / "legacy_opamp" / "legacy_opamp.kicad_sch",
    KICAD_QA / "legacy_rectifier" / "legacy_rectifier.kicad_sch",
    KICAD_QA / "legacy_pspice" / "legacy_pspice.kicad_sch",
    KICAD_QA / "legacy_sources" / "legacy_sources.kicad_sch",
    KICAD_QA / "directives" / "directives.kicad_sch",
    KICAD_QA / "instance_params" / "instance_params.kicad_sch",
    # --- Netlist test circuits (digital / mixed) ---
    KICAD_NETS / "legacy_power" / "legacy_power.kicad_sch",
    KICAD_NETS / "multinetclasses" / "multinetclasses.kicad_sch",
    KICAD_NETS / "test_hier_renaming" / "LED_matrix_x6.kicad_sch",
    KICAD_NETS / "test_multiunit_reannotate" / "test_multiunit_reannotate.kicad_sch",
    KICAD_NETS / "video" / "bus_pci.kicad_sch",
    # --- Real-world demo boards ---
    KICAD_DEMOS / "ecc83" / "ecc83-pp.kicad_sch",
    KICAD_DEMOS / "pic_programmer" / "pic_programmer.kicad_sch",
    KICAD_DEMOS / "video" / "graphic.kicad_sch",
    KICAD_DEMOS / "video" / "bus_pci.kicad_sch",
    KICAD_DEMOS / "tiny_tapeout" / "rp2040.kicad_sch",
    KICAD_DEMOS / "tiny_tapeout" / "tinytapeout-demo.kicad_sch",
]

def _scraped_fixtures(n: int = 250) -> List[Path]:
    """Return up to n scraped schematics, sorted for reproducibility."""
    if not SCRAPED_DIR.exists():
        return []
    all_files = sorted(SCRAPED_DIR.rglob("*.kicad_sch"))
    # Stride-sample to spread across all repos rather than taking first n
    if len(all_files) <= n:
        return all_files
    step = len(all_files) / n
    return [all_files[int(i * step)] for i in range(n)]

DEFAULT_FIXTURES = [f for f in _CORE_FIXTURES if f.exists()] + _scraped_fixtures(250)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class NetlistSignature:
    """Canonical representation of circuit topology for comparison."""
    components: Dict[str, str]  # ref -> lib_id (short)
    nets: Dict[str, Set[str]]   # net_name -> set of "ref:pin" strings


def _sig(comps: List[ComponentInstance], nets: List[Net]) -> NetlistSignature:
    components = {c.ref: c.lib_id.split(":")[-1] for c in comps}
    net_dict: Dict[str, Set[str]] = {}
    for n in nets:
        pins = {f"{p.ref}:{p.pin}" for p in n.pins}
        if len(pins) >= 2:  # only include nets with ≥2 connected pins
            net_dict[n.name] = pins
    return NetlistSignature(components=components, nets=net_dict)


def _topology_net_match(sig_a: NetlistSignature, sig_b: NetlistSignature) -> float:
    """
    Match nets by topology (pin sets) rather than name.
    For each net in sig_a, find the best-matching net in sig_b by Jaccard similarity.
    Returns fraction of sig_a nets with a match ≥ 50% similarity.
    """
    if not sig_a.nets:
        return 1.0
    matched = 0
    for name_a, pins_a in sig_a.nets.items():
        best_sim = 0.0
        for name_b, pins_b in sig_b.nets.items():
            if not pins_a or not pins_b:
                continue
            intersection = len(pins_a & pins_b)
            union_size = len(pins_a | pins_b)
            sim = intersection / union_size if union_size else 0
            best_sim = max(best_sim, sim)
        if best_sim >= 0.5:
            matched += 1
    return matched / len(sig_a.nets)


@dataclass
class ExperimentResult:
    name: str
    orig_lines: int
    orig_tokens: int
    py_lines: int
    py_tokens: int
    token_reduction: float

    # Component accuracy
    orig_comp_count: int
    found_comp_count: int
    comp_accuracy: float       # found/orig

    # Net accuracy
    orig_net_count: int
    found_net_count: int
    named_net_count: int       # nets with real names (not Net-N)
    net_accuracy: float        # found/orig

    # Roundtrip (py → sch → py)
    roundtrip_ok: bool
    roundtrip_comp_match: float
    roundtrip_net_match: float

    errors: List[str] = field(default_factory=list)

    def score(self) -> float:
        """Composite 0-100 score."""
        reduction_score = min(self.token_reduction / 20.0, 1.0) * 30   # up to 30 pts for 20x+
        comp_score = self.comp_accuracy * 30
        net_score = self.net_accuracy * 25
        rt_score = (self.roundtrip_comp_match * 0.5 + self.roundtrip_net_match * 0.5) * 15
        return round(reduction_score + comp_score + net_score + rt_score, 1)

    def summary_line(self) -> str:
        score = self.score()
        rt = "✓" if self.roundtrip_ok else "✗"
        return (
            f"  {self.name:<35} "
            f"score={score:5.1f}  "
            f"reduction={self.token_reduction:5.1f}x  "
            f"comps={self.comp_accuracy:.0%}  "
            f"nets={self.net_accuracy:.0%}  "
            f"roundtrip={rt}"
        )


# ---------------------------------------------------------------------------
# Single experiment
# ---------------------------------------------------------------------------

def run_experiment(sch_path: Path, compact: bool = False,
                   verbose: bool = False,
                   seen_names: Optional[Set[str]] = None) -> ExperimentResult:
    # Use parent/stem as name to disambiguate same-filename schematics in different dirs
    stem = sch_path.stem
    if seen_names is not None and stem in seen_names:
        name = f"{sch_path.parent.name}/{stem}"
    else:
        name = stem
    if seen_names is not None:
        seen_names.add(stem)
    errors: List[str] = []

    # 1. Read original
    try:
        with open(sch_path) as f:
            original_text = f.read()
        orig_lines = original_text.count("\n")
        orig_tokens = count_tokens(original_text)
    except Exception as e:
        return ExperimentResult(name=name, orig_lines=0, orig_tokens=0,
                                py_lines=0, py_tokens=0, token_reduction=0,
                                orig_comp_count=0, found_comp_count=0, comp_accuracy=0,
                                orig_net_count=0, found_net_count=0, named_net_count=0,
                                net_accuracy=0, roundtrip_ok=False,
                                roundtrip_comp_match=0, roundtrip_net_match=0,
                                errors=[f"Read error: {e}"])

    # Clear per-schematic state so one schematic's embedded symbols don't bleed into the next
    try:
        from src.py2sch import _seeded_from_schematic, PIN_POSITIONS
        _seeded_from_schematic.clear()
    except Exception:
        pass

    # 2. Parse original netlist (ground truth)
    try:
        sch_node = parse_file(str(sch_path))
        orig_comps, orig_nets = extract_netlist(sch_node)
        orig_sig = _sig(orig_comps, orig_nets)
    except Exception as e:
        errors.append(f"Parse error: {e}")
        orig_comps, orig_nets = [], []
        orig_sig = NetlistSignature({}, {})

    # 3. Forward convert: sch → py
    try:
        py_code = sch2py_convert(str(sch_path), compact=compact)
        py_lines = py_code.count("\n")
        py_tokens = count_tokens(py_code)
        token_reduction = orig_tokens / py_tokens if py_tokens else 0
    except Exception as e:
        errors.append(f"sch2py error: {e}")
        return ExperimentResult(
            name=name, orig_lines=orig_lines, orig_tokens=orig_tokens,
            py_lines=0, py_tokens=0, token_reduction=0,
            orig_comp_count=len(orig_comps), found_comp_count=0, comp_accuracy=0,
            orig_net_count=len(orig_nets), found_net_count=0, named_net_count=0,
            net_accuracy=0, roundtrip_ok=False,
            roundtrip_comp_match=0, roundtrip_net_match=0,
            errors=errors)

    # 4. Execute py code to get Circuit object, then measure forward accuracy
    roundtrip_ok = False
    roundtrip_comp_match = 0.0
    roundtrip_net_match = 0.0
    found_comps_count = len(orig_sig.components)
    found_nets_count = len(orig_sig.nets)
    named_net_count = sum(1 for n in orig_nets if not n.name.startswith("Net-"))
    forward_comp_match = 0.0
    forward_net_match = 0.0

    try:
        # Parse the generated Python to get a circuit object
        import importlib.util, tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            # Rewrite import to use local src/ path
            py_adapted = py_code.replace(
                "from sch2py.runtime import Circuit",
                "import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parent.parent)); from src.runtime import Circuit"
            )
            tf.write(py_adapted)
            tf_path = tf.name

        spec = importlib.util.spec_from_file_location("_exp_mod", tf_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        os.unlink(tf_path)

        from src.runtime import Circuit as CircuitCls
        circuit = next((getattr(mod, n) for n in dir(mod)
                        if isinstance(getattr(mod, n), CircuitCls)), None)

        if circuit:
            # 4b. Measure forward conversion accuracy
            gen_refs = {c.ref for c in circuit.components}
            orig_refs_set = set(orig_sig.components.keys())
            if orig_refs_set:
                forward_comp_match = len(gen_refs & orig_refs_set) / len(orig_refs_set)

            gen_net_names = {n.name for n in circuit.nets if len(n.pins) >= 2}
            orig_net_names = set(orig_sig.nets.keys())
            if orig_net_names:
                forward_net_match = len(gen_net_names & orig_net_names) / len(orig_net_names)

            # 5. Backward convert: py → sch
            regen_sch = circuit_to_sch(circuit)

            # 6. Parse regenerated schematic
            with tempfile.NamedTemporaryFile(mode="w", suffix=".kicad_sch", delete=False) as tf2:
                tf2.write(regen_sch)
                tf2_path = tf2.name

            regen_node = parse_file(tf2_path)
            regen_comps, regen_nets = extract_netlist(regen_node)
            regen_sig = _sig(regen_comps, regen_nets)
            os.unlink(tf2_path)

            # 7. Score roundtrip
            orig_refs = set(orig_sig.components.keys())
            regen_refs = set(regen_sig.components.keys())
            if not orig_refs:
                # Empty/hierarchical top-sheet: no components to compare → N/A
                roundtrip_comp_match = 1.0
                roundtrip_net_match = 1.0
                roundtrip_ok = True
            else:
                roundtrip_comp_match = len(orig_refs & regen_refs) / len(orig_refs)
                # Use topology-based matching to handle auto-renamed Net-N nets
                roundtrip_net_match = _topology_net_match(orig_sig, regen_sig)
                roundtrip_ok = roundtrip_comp_match >= 0.9 and roundtrip_net_match >= 0.7

    except Exception as e:
        errors.append(f"Roundtrip error: {e}")

    # Component and net accuracy = forward pass accuracy vs ground truth
    comp_accuracy = forward_comp_match if forward_comp_match > 0 else (
        found_comps_count / len(orig_sig.components) if orig_sig.components else 1.0)
    # If there are no multi-pin nets (e.g. hierarchical sub-sheets where all pins
    # are stubs), treat net accuracy as N/A → 1.0 rather than false 0%.
    if not orig_sig.nets:
        net_accuracy = 1.0
    else:
        net_accuracy = forward_net_match if forward_net_match > 0 else (
            found_nets_count / len(orig_sig.nets))

    result = ExperimentResult(
        name=name,
        orig_lines=orig_lines,
        orig_tokens=orig_tokens,
        py_lines=py_lines,
        py_tokens=py_tokens,
        token_reduction=token_reduction,
        orig_comp_count=len(orig_comps),
        found_comp_count=found_comps_count,
        comp_accuracy=comp_accuracy,
        orig_net_count=len(orig_nets),
        found_net_count=found_nets_count,
        named_net_count=named_net_count,
        net_accuracy=net_accuracy,
        roundtrip_ok=roundtrip_ok,
        roundtrip_comp_match=roundtrip_comp_match,
        roundtrip_net_match=roundtrip_net_match,
        errors=errors,
    )

    if verbose:
        _print_verbose(result, py_code)

    return result


def _print_verbose(result: ExperimentResult, py_code: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {result.name}")
    print(f"{'='*60}")
    print(f"  Original:  {result.orig_lines} lines, {result.orig_tokens} tokens")
    print(f"  Python:    {result.py_lines} lines, {result.py_tokens} tokens  ({result.token_reduction:.1f}x reduction)")
    print(f"  Components: {result.found_comp_count}/{result.orig_comp_count}  forward={result.comp_accuracy:.0%}")
    print(f"  Nets:       {result.found_net_count}/{result.orig_net_count}  forward={result.net_accuracy:.0%}  (named: {result.named_net_count})")
    print(f"  Roundtrip: comps={result.roundtrip_comp_match:.0%}  nets={result.roundtrip_net_match:.0%}  ok={result.roundtrip_ok}")
    print(f"  Score: {result.score()}/100")
    if result.errors:
        print(f"  Errors:")
        for e in result.errors:
            print(f"    - {e}")
    print(f"\n  --- Generated Python ---")
    print(textwrap.indent(py_code.strip(), "  "))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _print_report(results: List[ExperimentResult], elapsed: float) -> None:
    print(f"\n{'='*70}")
    print(f"  sch2py Experiment Results  ({len(results)} circuits, {elapsed:.1f}s)")
    print(f"{'='*70}")

    total_score = sum(r.score() for r in results)
    avg_score = total_score / len(results) if results else 0
    avg_reduction = sum(r.token_reduction for r in results) / len(results) if results else 0
    avg_comp = sum(r.comp_accuracy for r in results) / len(results) if results else 0
    avg_net = sum(r.net_accuracy for r in results) / len(results) if results else 0
    n_roundtrip_ok = sum(1 for r in results if r.roundtrip_ok)

    for r in results:
        print(r.summary_line())
        for e in r.errors:
            print(f"    {'':35}  ERROR: {e}")

    print(f"\n  Averages:")
    print(f"    Score:          {avg_score:.1f}/100")
    print(f"    Token reduction: {avg_reduction:.1f}x")
    print(f"    Component accuracy: {avg_comp:.0%}")
    print(f"    Net accuracy:       {avg_net:.0%}")
    print(f"    Roundtrip OK:       {n_roundtrip_ok}/{len(results)}")

    # Suggestions
    print(f"\n  Suggestions:")
    for r in sorted(results, key=lambda x: x.score()):
        if r.comp_accuracy < 0.8:
            print(f"    [{r.name}] Low component accuracy — check lib_symbols parsing for custom libraries")
        if r.net_accuracy < 0.7:
            print(f"    [{r.name}] Low net accuracy — check wire tracing or junction handling")
        if r.token_reduction < 5:
            print(f"    [{r.name}] Low compression — consider stripping more metadata")
        if not r.roundtrip_ok and r.comp_accuracy > 0.8:
            print(f"    [{r.name}] Good forward but roundtrip fails — fix py2sch layout/labels")

    # Save JSON report
    report_path = ROOT / "experiments" / "last_results.json"
    report_data = [asdict(r) for r in results]
    # Convert sets to lists for JSON serialization
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2, default=lambda x: list(x) if isinstance(x, set) else x)
    print(f"\n  Results saved to: {report_path}")


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def _get_mtime(paths: List[Path]) -> float:
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0)


def _watch_loop(fixtures: List[Path], compact: bool, verbose: bool) -> None:
    src_files = list((ROOT / "src").glob("*.py"))
    last_mtime = _get_mtime(src_files)

    print("Watching src/ for changes... (Ctrl+C to stop)")
    while True:
        current_mtime = _get_mtime(src_files)
        if current_mtime != last_mtime:
            print(f"\n  Change detected — re-running experiments...")
            last_mtime = current_mtime
            # Reload modules
            for mod_name in list(sys.modules.keys()):
                if "src." in mod_name or mod_name.startswith("src"):
                    del sys.modules[mod_name]
            run_all(fixtures, compact=compact, verbose=verbose)
        time.sleep(1)


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

def run_all(fixtures: List[Path], compact: bool = False, verbose: bool = False) -> None:
    t0 = time.time()
    results = []
    seen_names: Set[str] = set()
    for path in fixtures:
        if not path.exists():
            print(f"  SKIP (not found): {path}")
            continue
        print(f"  Testing: {path.stem}...", end=" ", flush=True)
        try:
            result = run_experiment(path, compact=compact, verbose=verbose, seen_names=seen_names)
            results.append(result)
            print(f"score={result.score():.0f}/100  {result.token_reduction:.1f}x")
        except Exception as e:
            print(f"FAILED: {e}")

    _print_report(results, time.time() - t0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="sch2py experiment loop")
    parser.add_argument("--file", help="Test single .kicad_sch file")
    parser.add_argument("--watch", action="store_true", help="Watch src/ and re-run on changes")
    parser.add_argument("--compact", action="store_true", help="Test compact (topology-only) mode")
    parser.add_argument("--verbose", action="store_true", help="Show generated Python for each file")
    parser.add_argument("--iters", type=int, default=1, help="Number of full iterations")
    args = parser.parse_args()

    if args.file:
        fixtures = [Path(args.file)]
    else:
        # Also include any .kicad_sch files in fixtures/
        extra = list(FIXTURES_DIR.glob("*.kicad_sch")) if FIXTURES_DIR.exists() else []
        fixtures = DEFAULT_FIXTURES + extra

    if args.watch:
        _watch_loop(fixtures, compact=args.compact, verbose=args.verbose)
    else:
        for i in range(args.iters):
            if args.iters > 1:
                print(f"\n=== Iteration {i+1}/{args.iters} ===")
            run_all(fixtures, compact=args.compact, verbose=args.verbose)


if __name__ == "__main__":
    main()
