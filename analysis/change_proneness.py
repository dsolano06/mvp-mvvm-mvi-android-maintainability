"""
change_proneness.py — Stage 5 (RQ2, reframed).

Mines git history for *presentation-layer* files in each cloned repo and computes
change-proneness signals: how often those files are modified and how much they churn.
This is the mineable maintainability-in-practice proxy that replaces the
non-mineable runtime-performance RQ.

Per presentation file:
    commits        - number of commits that touched the file (--follow across renames)
    churn          - total lines added + deleted over its history
    age_months     - months between the file's first and last touching commit
Per repo (aggregated, median over presentation files; robust to outliers):
    commits_per_month  = commits / repo_active_months         (normalised frequency)
    churn_per_sloc     = churn   / current SLOC                (normalised volume)

Normalising by repo age/size keeps older or larger projects from dominating.

Output: data/churn.csv  (one row per repo)

Usage:
    python analysis/change_proneness.py --all
    python analysis/change_proneness.py repos/<owner__name>
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from classify import pattern_of_file, SRC_EXT  # single source of truth for layering

ROOT = Path(__file__).resolve().parent.parent
REPOS = ROOT / "repos"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="ignore").stdout


def repo_active_months(repo: Path) -> float:
    first = git(repo, "log", "--reverse", "--format=%ct").splitlines()
    last = git(repo, "log", "-1", "--format=%ct").splitlines()
    if not first or not last:
        return 0.0
    t0, t1 = int(first[0]), int(last[0])
    return max((t1 - t0) / (30 * 24 * 3600), 0.5)


def nonblank_sloc(path: Path) -> int:
    try:
        return sum(1 for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                   if ln.strip() and not ln.strip().startswith(("//", "*", "/*")))
    except Exception:
        return 0


def file_history(repo: Path, rel: str) -> tuple[int, int, float]:
    """Return (commits, churn, age_months) for one file, following renames."""
    out = git(repo, "log", "--follow", "--numstat", "--format=commit %ct", "--", rel)
    commits = 0
    churn = 0
    times: list[int] = []
    for line in out.splitlines():
        if line.startswith("commit "):
            commits += 1
            times.append(int(line.split()[1]))
        elif line.strip():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                churn += int(parts[0]) + int(parts[1])
    age_months = ((max(times) - min(times)) / (30 * 24 * 3600)) if len(times) > 1 else 0.5
    return commits, churn, max(age_months, 0.1)


def presentation_files(repo: Path) -> list[Path]:
    out = []
    for p in repo.rglob("*"):
        if p.suffix in SRC_EXT and p.is_file():
            parts = {x.lower() for x in p.parts}
            if "build" in parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if pattern_of_file(p, text):
                out.append(p)
    return out


def analyze_repo(repo: Path) -> dict | None:
    pres = presentation_files(repo)
    if not pres:
        return None
    months = repo_active_months(repo)
    commits_pm: list[float] = []
    churn_ps: list[float] = []
    raw_commits: list[int] = []
    for p in pres:
        rel = p.relative_to(repo).as_posix()
        c, ch, _age = file_history(repo, rel)
        if c == 0:
            continue
        raw_commits.append(c)
        commits_pm.append(c / months)
        sloc = nonblank_sloc(p) or 1
        churn_ps.append(ch / sloc)
    if not raw_commits:
        return None
    return {
        "repo": repo.name,
        "n_presentation_files": len(pres),
        "median_commits": round(st.median(raw_commits), 3),
        "median_commits_per_month": round(st.median(commits_pm), 4),
        "median_churn_per_sloc": round(st.median(churn_ps), 3),
        "repo_active_months": round(months, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    targets = ([d for d in REPOS.iterdir() if d.is_dir()] if args.all
               else [Path(args.repo)] if args.repo else None)
    if targets is None:
        ap.error("give a repo dir or --all")

    rows = []
    for d in targets:
        print(f"history: {d.name}", file=sys.stderr)
        r = analyze_repo(d)
        if r:
            rows.append(r)

    out = DATA / "churn.csv"
    fields = ["repo", "n_presentation_files", "median_commits",
              "median_commits_per_month", "median_churn_per_sloc",
              "repo_active_months"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} repos to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
