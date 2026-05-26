#!/usr/bin/env python3
"""Scrape public KiCad schematic files from grep.app index in parallel.

This script:
1) Queries grep.app for KiCad signature strings.
2) Extracts repo/branch/path hits for true schematic extensions.
3) Downloads raw files from GitHub in parallel.
4) Writes a manifest and summary.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

GREP_API = "https://grep.app/api/search"
USER_AGENT = "pcb-ft-q35-kicad-scraper/1.0"


@dataclass(frozen=True)
class SearchSpec:
    name: str
    query: str
    extension: str


SEARCH_SPECS: Sequence[SearchSpec] = (
    SearchSpec(name="kicad_v6plus", query="(kicad_sch", extension=".kicad_sch"),
    SearchSpec(name="kicad_v6plus_alt", query="(lib_symbols", extension=".kicad_sch"),
    SearchSpec(
        name="kicad_legacy",
        query="EESchema Schematic File Version",
        extension=".sch",
    ),
    SearchSpec(name="kicad_legacy_alt", query="EELAYER 30 0", extension=".sch"),
)


def http_get_json(url: str, timeout_s: float = 30.0) -> Dict:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_page(spec: SearchSpec, page: int, retries: int = 5, backoff_s: float = 1.0) -> Dict:
    params = {"q": spec.query, "regexp": "false", "page": str(page)}
    url = f"{GREP_API}?{urlencode(params)}"
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return http_get_json(url)
        except HTTPError as exc:
            last_exc = exc
            retry_after = 0.0
            if exc.code == 429 and exc.headers is not None:
                try:
                    retry_after = float(exc.headers.get("Retry-After", "0"))
                except ValueError:
                    retry_after = 0.0
            jitter = random.uniform(0.0, 0.75)
            sleep_s = max(retry_after, backoff_s * (2 ** (attempt - 1)) + jitter)
            time.sleep(sleep_s)
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            jitter = random.uniform(0.0, 0.75)
            sleep_s = backoff_s * (2 ** (attempt - 1)) + jitter
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed page fetch after retries: spec={spec.name} page={page}: {last_exc}")


def raw_github_url(repo: str, branch: str, path: str) -> str:
    encoded_path = quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{encoded_path}"


def sanitize_repo(repo: str) -> str:
    return repo.replace("/", "__")


def file_destination(base_dir: Path, repo: str, branch: str, path: str) -> Path:
    safe_repo = sanitize_repo(repo)
    return base_dir / safe_repo / branch / path


def download_file(url: str, destination: Path, timeout_s: float = 30.0) -> Tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout_s) as resp:
        content = resp.read()
        status = getattr(resp, "status", 200)
    destination.write_bytes(content)
    return status, len(content)


def collect_candidates(
    specs: Sequence[SearchSpec],
    max_pages_per_query: int,
    page_workers: int,
    excluded_repos: set[str] | None = None,
) -> Tuple[List[Dict], Dict[str, Dict[str, int]]]:
    candidates: Dict[Tuple[str, str, str], Dict] = {}
    stats: Dict[str, Dict[str, int]] = {}
    lock = threading.Lock()
    excluded = excluded_repos or set()

    for spec in specs:
        first = fetch_page(spec, 1)
        total_hits = int(first.get("hits", {}).get("total", 0))
        pages = max(1, math.ceil(total_hits / 10))
        pages = min(pages, max_pages_per_query)
        stats[spec.name] = {
            "total_hits_reported": total_hits,
            "pages_fetched": pages,
            "hits_processed": 0,
            "candidates_kept": 0,
            "pages_failed": 0,
        }

        def process_payload(payload: Dict, page: int) -> None:
            hits = payload.get("hits", {}).get("hits", [])
            kept = 0
            for hit in hits:
                repo = str(hit.get("repo", "")).strip()
                branch = str(hit.get("branch", "")).strip()
                path = str(hit.get("path", "")).strip()
                if not repo or not branch or not path:
                    continue
                if repo in excluded:
                    continue
                if not path.lower().endswith(spec.extension.lower()):
                    continue
                key = (repo, branch, path)
                rec = {
                    "source_query": spec.name,
                    "repo": repo,
                    "branch": branch,
                    "path": path,
                    "download_url": raw_github_url(repo, branch, path),
                    "page": page,
                }
                with lock:
                    candidates.setdefault(key, rec)
                kept += 1
            with lock:
                stats[spec.name]["hits_processed"] += len(hits)
                stats[spec.name]["candidates_kept"] += kept

        process_payload(first, 1)

        if pages <= 1:
            continue

        with ThreadPoolExecutor(max_workers=page_workers) as pool:
            futures = {
                pool.submit(fetch_page, spec, page): page
                for page in range(2, pages + 1)
            }
            for fut in as_completed(futures):
                page = futures[fut]
                try:
                    payload = fut.result()
                except Exception:
                    stats[spec.name]["pages_failed"] += 1
                    continue
                process_payload(payload, page)

    return sorted(candidates.values(), key=lambda r: (r["repo"], r["branch"], r["path"])), stats


def download_candidates(
    candidates: Sequence[Dict],
    out_files_dir: Path,
    download_workers: int,
    skip_existing: bool,
) -> List[Dict]:
    results: List[Dict] = []
    lock = threading.Lock()

    def worker(rec: Dict) -> Dict:
        repo = rec["repo"]
        branch = rec["branch"]
        path = rec["path"]
        url = rec["download_url"]
        dst = file_destination(out_files_dir, repo, branch, path)
        row = dict(rec)
        row["local_path"] = str(dst)

        if skip_existing and dst.exists() and dst.stat().st_size > 0:
            row["status"] = "skipped_existing"
            row["http_status"] = 200
            row["bytes"] = dst.stat().st_size
            return row

        try:
            status, n_bytes = download_file(url=url, destination=dst)
            row["status"] = "downloaded"
            row["http_status"] = status
            row["bytes"] = n_bytes
            return row
        except HTTPError as exc:
            row["status"] = "http_error"
            row["http_status"] = int(exc.code)
            row["bytes"] = 0
            row["error"] = str(exc)
            return row
        except (URLError, TimeoutError, OSError) as exc:
            row["status"] = "error"
            row["http_status"] = 0
            row["bytes"] = 0
            row["error"] = str(exc)
            return row

    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        futures = [pool.submit(worker, rec) for rec in candidates]
        for fut in as_completed(futures):
            row = fut.result()
            with lock:
                results.append(row)
    return sorted(results, key=lambda r: (r["repo"], r["branch"], r["path"]))


def summarize(download_rows: Sequence[Dict], query_stats: Dict[str, Dict[str, int]]) -> Dict:
    total = len(download_rows)
    downloaded = sum(1 for r in download_rows if r.get("status") == "downloaded")
    skipped = sum(1 for r in download_rows if r.get("status") == "skipped_existing")
    failed = sum(1 for r in download_rows if r.get("status") not in {"downloaded", "skipped_existing"})
    bytes_total = sum(int(r.get("bytes", 0)) for r in download_rows)
    by_ext: Dict[str, int] = {}
    for r in download_rows:
        ext = Path(str(r["path"])).suffix.lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1
    return {
        "total_candidates": total,
        "downloaded": downloaded,
        "skipped_existing": skipped,
        "failed": failed,
        "bytes_total": bytes_total,
        "by_extension": by_ext,
        "query_stats": query_stats,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parallel public KiCad schematic scraper.")
    p.add_argument("--out-dir", default="data/scraped_schematics", help="Output dataset directory.")
    p.add_argument("--max-pages-per-query", type=int, default=200, help="Safety cap for search pages per query.")
    p.add_argument("--page-workers", type=int, default=12, help="Concurrent workers for search page fetches.")
    p.add_argument("--download-workers", type=int, default=24, help="Concurrent workers for file downloads.")
    p.add_argument("--skip-existing", action="store_true", help="Skip existing non-empty files.")
    p.add_argument(
        "--exclude-repos-file",
        type=str,
        default="",
        help="Optional newline-delimited list of repo slugs to exclude from download.",
    )
    return p.parse_args()


def write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    files_dir = out_dir / "files"
    metadata_dir = out_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    excluded_repos: set[str] = set()
    if args.exclude_repos_file:
        exclude_path = Path(args.exclude_repos_file)
        if exclude_path.exists():
            excluded_repos = {
                line.strip()
                for line in exclude_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }

    t0 = time.time()
    candidates, query_stats = collect_candidates(
        specs=SEARCH_SPECS,
        max_pages_per_query=args.max_pages_per_query,
        page_workers=args.page_workers,
        excluded_repos=excluded_repos,
    )
    write_jsonl(metadata_dir / "candidates.jsonl", candidates)

    download_rows = download_candidates(
        candidates=candidates,
        out_files_dir=files_dir,
        download_workers=args.download_workers,
        skip_existing=args.skip_existing,
    )
    write_jsonl(metadata_dir / "downloads.jsonl", download_rows)

    summary = summarize(download_rows=download_rows, query_stats=query_stats)
    summary["duration_seconds"] = round(time.time() - t0, 3)
    summary["dataset_root"] = str(out_dir)
    summary["timestamp_unix"] = int(time.time())

    summary_path = metadata_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Summary written to: {summary_path}")


if __name__ == "__main__":
    main()
