#!/usr/bin/env python3
"""Scrape modern KiCad design files directly from GitHub repos.

v2: Expanded discovery with more topics, search queries, and GitHub API
code search. Targets 2000+ repos for broader schematic diversity.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

USER_AGENT = "pcb-ft-q35-github-kicad-scraper/2.0"
REPO_LINK_RE = re.compile(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"')

BLOCKED_OWNERS = {
    "about",
    "account",
    "blog",
    "collections",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "issues",
    "login",
    "marketplace",
    "new",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "readme",
    "search",
    "security",
    "sessions",
    "settings",
    "signup",
    "site",
    "sponsors",
    "topics",
    "trending",
    "users",
}

# v2: Expanded from 5 to 18 topics + targeted search queries
DEFAULT_TOPICS: Sequence[str] = (
    "kicad",
    "pcb",
    "electronics",
    "open-hardware",
    "hardware-design",
    # v2 additions
    "pcb-design",
    "circuit-board",
    "schematic",
    "eda",
    "electronic-design",
    "kicad-library",
    "kicad-footprint",
    "kicad-symbol",
    "fpga",
    "arduino-shield",
    "raspberry-pi-hat",
    "breakout-board",
    "esp32",
)

# v2: Additional targeted search queries beyond topic pages
SEARCH_QUERIES: Sequence[str] = (
    "kicad_sch",
    "kicad schematic",
    "kicad pcb design",
    "kicad hardware",
    "kicad project",
    "eeschema",
    "kicad 6 schematic",
    "kicad 7 schematic",
    "kicad 8 schematic",
    "open source hardware kicad",
    "stm32 kicad",
    "esp32 kicad",
    "raspberry pi kicad",
    "arduino kicad",
    "power supply kicad",
    "motor driver kicad",
    "sensor board kicad",
    "oshpark kicad",
    "jlcpcb kicad",
)


@dataclass(frozen=True)
class RepoJob:
    repo: str


def fetch_text(url: str, timeout_s: float = 30.0, retries: int = 4) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(req, timeout=timeout_s) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(0.8 * attempt)
    raise RuntimeError(f"fetch_text failed for {url}: {last_exc}")


def extract_repo_slugs(html: str) -> Set[str]:
    repos: Set[str] = set()
    for slug in REPO_LINK_RE.findall(html):
        if slug.count("/") != 1:
            continue
        owner, name = slug.split("/", 1)
        if owner.lower() in BLOCKED_OWNERS:
            continue
        if not owner or not name:
            continue
        repos.add(f"{owner}/{name}")
    return repos


def discover_repos(
    topics: Sequence[str],
    search_queries: Sequence[str],
    topic_pages: int,
    search_pages: int,
    workers: int,
    excluded_repos: Set[str] | None = None,
) -> List[str]:
    urls: List[str] = []
    for topic in topics:
        for page in range(1, topic_pages + 1):
            urls.append(f"https://github.com/topics/{quote_plus(topic)}?page={page}")
    # Standard repo search
    for page in range(1, search_pages + 1):
        urls.append(f"https://github.com/search?q=kicad&type=repositories&p={page}")
    # v2: Additional targeted search queries
    for query in search_queries:
        for page in range(1, min(search_pages, 10) + 1):
            urls.append(
                f"https://github.com/search?q={quote_plus(query)}&type=repositories&p={page}"
            )

    repos: Set[str] = set()
    lock = threading.Lock()
    excluded = excluded_repos or set()

    def worker(url: str) -> Set[str]:
        try:
            html = fetch_text(url)
            return {repo for repo in extract_repo_slugs(html) if repo not in excluded}
        except Exception:
            return set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, url) for url in urls]
        for fut in as_completed(futures):
            try:
                found = fut.result()
            except Exception:
                continue
            with lock:
                repos.update(found)

    return sorted(repos)


def download_tarball(repo: str, ref: str = "HEAD", timeout_s: float = 90.0, retries: int = 5) -> Path:
    url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(req, timeout=timeout_s) as resp:
                if getattr(resp, "status", 200) >= 400:
                    raise HTTPError(url, getattr(resp, "status", 500), "bad status", hdrs=None, fp=None)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
                    shutil.copyfileobj(resp, tmp)
                    return Path(tmp.name)
        except HTTPError as exc:
            last_exc = exc
            if exc.code in (404, 451):
                raise
            time.sleep(1.0 * attempt)
        except (URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(1.0 * attempt)
    raise RuntimeError(f"download_tarball failed for {repo}: {last_exc}")


def sanitize_repo(repo: str) -> str:
    return repo.replace("/", "__")


def extract_design_files(
    tar_path: Path,
    repo: str,
    ref: str,
    out_root: Path,
    extensions: Set[str],
) -> List[Dict]:
    file_rows: List[Dict] = []
    base_repo_dir = out_root / sanitize_repo(repo) / ref
    base_repo_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tar_path, mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            member_lower = member.name.lower()
            if not any(member_lower.endswith(ext) for ext in extensions):
                continue
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue
            rel_path = Path(*parts[1:])
            destination = (base_repo_dir / rel_path).resolve()
            safe_root = base_repo_dir.resolve()
            try:
                destination.relative_to(safe_root)
            except ValueError:
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with destination.open("wb") as f:
                shutil.copyfileobj(src, f)

            file_rows.append(
                {
                    "repo": repo,
                    "ref": ref,
                    "path": str(rel_path),
                    "local_path": str(destination),
                    "bytes": destination.stat().st_size,
                    "extension": destination.suffix.lower(),
                }
            )
    return file_rows


def process_repo(job: RepoJob, out_root: Path, extensions: Set[str]) -> Tuple[Dict, List[Dict]]:
    repo = job.repo
    ref = "HEAD"
    tar_path: Path | None = None
    try:
        tar_path = download_tarball(repo=repo, ref=ref)
        files = extract_design_files(
            tar_path=tar_path,
            repo=repo,
            ref=ref,
            out_root=out_root,
            extensions=extensions,
        )
        ext_counts: Dict[str, int] = {}
        for row in files:
            ext = row["extension"]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        summary = {
            "repo": repo,
            "ref": ref,
            "status": "ok",
            "design_files": len(files),
            "by_extension": ext_counts,
        }
        return summary, files
    except HTTPError as exc:
        return (
            {
                "repo": repo,
                "ref": None,
                "status": "failed",
                "design_files": 0,
                "error": f"http_error:{exc.code}",
            },
            [],
        )
    except (URLError, OSError, tarfile.TarError, RuntimeError) as exc:
        return (
            {
                "repo": repo,
                "ref": None,
                "status": "failed",
                "design_files": 0,
                "error": f"error:{exc}",
            },
            [],
        )
    finally:
        if tar_path and tar_path.exists():
            tar_path.unlink(missing_ok=True)


def write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape .kicad_sch/.kicad_pcb/.kicad_pro files directly from GitHub repos.")
    p.add_argument("--out-dir", default="data/scraped_schematics/github_repo", help="Output root directory.")
    p.add_argument("--topic-pages", type=int, default=15, help="Pages per GitHub topic (v2: raised from 12).")
    p.add_argument("--search-pages", type=int, default=25, help="Pages of GitHub repository search (v2: raised from 20).")
    p.add_argument("--discover-workers", type=int, default=16, help="Workers for discovery page fetch.")
    p.add_argument("--repo-workers", type=int, default=8, help="Workers for repo tarball extraction.")
    p.add_argument("--max-repos", type=int, default=2000, help="Cap repositories to process (v2: raised from 700).")
    p.add_argument(
        "--extensions",
        default=".kicad_sch,.kicad_pcb,.kicad_pro",
        help="Comma-separated file extensions to extract.",
    )
    p.add_argument("--skip-existing", action="store_true", help="Skip repos already downloaded.")
    p.add_argument(
        "--exclude-repos-file",
        type=str,
        default="",
        help="Optional newline-delimited list of repo slugs to exclude from discovery/download.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    t0 = time.time()
    out_dir = Path(args.out_dir)
    files_root = out_dir / "files"
    meta_root = out_dir / "metadata"
    meta_root.mkdir(parents=True, exist_ok=True)
    excluded_repos: Set[str] = set()
    if args.exclude_repos_file:
        exclude_path = Path(args.exclude_repos_file)
        if exclude_path.exists():
            excluded_repos = {
                line.strip()
                for line in exclude_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }

    # Check for already-downloaded repos to skip
    existing_repos: Set[str] = set()
    if args.skip_existing and files_root.exists():
        for entry in files_root.iterdir():
            if entry.is_dir() and "__" in entry.name:
                existing_repos.add(entry.name.replace("__", "/", 1))

    repos = discover_repos(
        topics=DEFAULT_TOPICS,
        search_queries=SEARCH_QUERIES,
        topic_pages=args.topic_pages,
        search_pages=args.search_pages,
        workers=args.discover_workers,
        excluded_repos=excluded_repos,
    )

    if existing_repos:
        new_repos = [r for r in repos if r not in existing_repos]
        print(f"Discovered {len(repos)} repos, {len(existing_repos)} already downloaded, {len(new_repos)} new")
        repos = new_repos

    repos = repos[: args.max_repos]
    extensions = {
        ext.strip().lower()
        for ext in args.extensions.split(",")
        if ext.strip()
    }

    repo_rows: List[Dict] = []
    file_rows: List[Dict] = []
    with ThreadPoolExecutor(max_workers=args.repo_workers) as pool:
        futures = [
            pool.submit(process_repo, RepoJob(repo=repo), files_root, extensions)
            for repo in repos
        ]
        for fut in as_completed(futures):
            repo_row, files = fut.result()
            repo_rows.append(repo_row)
            file_rows.extend(files)

    write_jsonl(meta_root / "repos.jsonl", sorted(repo_rows, key=lambda r: r["repo"]))
    write_jsonl(meta_root / "files.jsonl", sorted(file_rows, key=lambda r: (r["repo"], r["path"])))

    success_repos = sum(1 for r in repo_rows if r["status"] == "ok")
    failed_repos = sum(1 for r in repo_rows if r["status"] != "ok")
    nonempty_repos = sum(1 for r in repo_rows if r.get("design_files", 0) > 0)
    total_files = len(file_rows)
    total_bytes = sum(int(r.get("bytes", 0)) for r in file_rows)
    by_ext: Dict[str, int] = {}
    for row in file_rows:
        ext = row["extension"]
        by_ext[ext] = by_ext.get(ext, 0) + 1

    summary = {
        "discovered_repos": len(repos) + len(existing_repos),
        "new_repos_processed": len(repos),
        "previously_downloaded": len(existing_repos),
        "processed_repos_ok": success_repos,
        "processed_repos_failed": failed_repos,
        "repos_with_design_files": nonempty_repos,
        "design_files_total": total_files,
        "by_extension": by_ext,
        "bytes_total": total_bytes,
        "duration_seconds": round(time.time() - t0, 3),
        "output_root": str(out_dir),
        "timestamp_unix": int(time.time()),
    }
    (meta_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
