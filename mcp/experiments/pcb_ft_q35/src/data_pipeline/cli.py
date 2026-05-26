"""Command-line entrypoint for the data pipeline jobs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, TextIO

from .jobs import JOB_NAMES, run_job


@contextmanager
def _open_text(path: str, mode: str) -> Iterator[TextIO]:
    if path == "-":
        stream = sys.stdin if "r" in mode else sys.stdout
        yield stream
        return

    with open(path, mode, encoding="utf-8") as handle:
        yield handle


def read_jsonl(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with _open_text(path, "r") as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} line {lineno}: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected object per JSONL line in {path} line {lineno}")
            records.append(value)
    return records


def _as_output_records(result: Any) -> Iterable[dict[str, Any]]:
    if result is None:
        return ()

    if isinstance(result, dict):
        nested = result.get("records")
        if isinstance(nested, Iterable) and not isinstance(nested, (str, bytes, dict)):
            return nested
        return (result,)

    if isinstance(result, (str, bytes)):
        raise TypeError("Job output must be mapping-like records, not a string")

    if isinstance(result, Iterable):
        return result

    raise TypeError("Job output must be a record, iterable of records, or {'records': [...]} ")


def write_jsonl(path: str, records: Iterable[dict[str, Any]]) -> None:
    with _open_text(path, "w") as handle:
        for record in records:
            if not isinstance(record, dict):
                raise TypeError("Each output record must be a JSON object")
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def _coerce_cli_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        return value


def parse_extra_args(tokens: list[str]) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            raise ValueError(f"Unexpected token '{token}'. Extra args must use --key value")

        key = token[2:].replace("-", "_")
        if not key:
            raise ValueError("Unexpected empty flag name")

        if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
            extras[key] = True
            i += 1
            continue

        extras[key] = _coerce_cli_value(tokens[i + 1])
        i += 2

    return extras


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run data pipeline jobs over JSONL input")
    subparsers = parser.add_subparsers(dest="job_name", required=True)

    for job_name in JOB_NAMES:
        sub = subparsers.add_parser(job_name, help=f"Run the {job_name} job")
        sub.add_argument("--input", "-i", required=True, help="Input JSONL path (or - for stdin)")
        sub.add_argument("--output", "-o", required=True, help="Output JSONL path (or - for stdout)")
        sub.add_argument("--seed", type=int, default=None, help="Optional deterministic seed")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)

    try:
        job_kwargs = parse_extra_args(unknown)
    except ValueError as exc:
        parser.error(str(exc))

    if args.seed is not None:
        job_kwargs["seed"] = args.seed

    input_records = read_jsonl(args.input)
    result = run_job(args.job_name, input_records, **job_kwargs)
    write_jsonl(args.output, _as_output_records(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
