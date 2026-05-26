"""Data-pipeline package for Part A geometry data generation."""

from .jobs.block_extractor import extract_blocks_from_record
from .jobs.benchmark import run as run_benchmark
from .jobs.cluster import run as run_cluster
from .jobs.geometry import run as run_geometry
from .jobs.repo_miner import discover_projects, run as run_repo_miner
from .jobs.schematic_parser import parse_schematic_file, parse_schematic_text
from .jobs.split import run as run_split

__all__ = [
    "discover_projects",
    "extract_blocks_from_record",
    "parse_schematic_file",
    "parse_schematic_text",
    "run_benchmark",
    "run_cluster",
    "run_geometry",
    "run_repo_miner",
    "run_split",
]
