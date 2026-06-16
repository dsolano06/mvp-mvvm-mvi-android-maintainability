"""
mine_repos.py — Stage 1 of the empirical pipeline.

Searches GitHub for native Android repositories that declare an MVP, MVVM, or MVI
architecture, records metadata, and computes the inclusion/exclusion flags from the
paper's selection criteria (Sec. IV-A). It does NOT decide the final corpus; it
produces a candidate table that classify.py + the pilot/scaling steps draw from.

Output: data/corpus_candidates.csv

Auth: set GITHUB_TOKEN in the environment to lift the search rate limit
(10 -> 30 req/min) and the core limit (60 -> 5000 req/hr). Cloning later needs no auth.

Usage:
    python analysis/mine_repos.py                 # default: all patterns
    python analysis/mine_repos.py --max-pages 3   # fewer pages per query (faster)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

API = "https://api.github.com/search/repositories"

# Per-pattern search queries. We lean on GitHub *topics* (curated by maintainers)
# plus name/description keyword matches. Multiple queries per pattern widen recall;
# duplicates are merged. Language is constrained to the Android stack (Kotlin/Java).
QUERIES = {
    "MVP": [
        "topic:android topic:mvp",
        "topic:android-mvp",
        "topic:mvp-architecture",
        "topic:mvp-android",
        "android mvp architecture in:name,description language:kotlin",
        "android mvp architecture in:name,description language:java",
        "android mvp pattern in:name,description language:java",
        "android mvp clean in:name,description language:kotlin",
    ],
    "MVVM": [
        "topic:android topic:mvvm",
        "topic:android-mvvm",
        "android mvvm in:name,description language:kotlin",
        "android mvvm in:name,description language:java",
    ],
    "MVI": [
        "topic:android topic:mvi",
        "topic:android-mvi",
        "topic:mvi-architecture",
        "topic:android topic:orbit-mvi",
        "android mvi architecture in:name,description language:kotlin",
        "android mvi pattern in:name,description language:kotlin",
        "android mvi compose in:name,description language:kotlin",
    ],
}

# Keywords that suggest a teaching/sample repo rather than a maintained application.
SAMPLE_HINTS = (
    "sample", "samples", "example", "examples", "tutorial", "demo",
    "boilerplate", "template", "playground", "practice", "learn",
    "starter", "skeleton", "cookbook", "guide", "course", "study",
)

# Native-Android languages (paper inclusion criterion). Excludes C#/Dart/JS
# cross-platform frameworks (Avalonia, Uno, Flutter, React Native, ...).
ANDROID_LANGS = ("Kotlin", "Java")

# Topics that mark a repo as NOT a native Android *application* (libraries, SDKs,
# awesome-lists, cross-platform frameworks). Checked against the curated topic list.
NON_APP_TOPICS = {
    "awesome", "awesome-list", "lists", "library", "libraries", "framework",
    "sdk", "gradle-plugin", "plugin", "compose-multiplatform", "kotlin-multiplatform",
    "cross-platform", "flutter", "react-native", "dotnet", "xamarin",
}


def headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def get(url: str, params: dict) -> requests.Response:
    """GET with simple, polite rate-limit handling."""
    for attempt in range(6):
        r = requests.get(url, params=params, headers=headers(), timeout=30)
        if r.status_code == 200:
            return r
        if r.status_code in (403, 429):
            reset = r.headers.get("X-RateLimit-Reset")
            remaining = r.headers.get("X-RateLimit-Remaining")
            if remaining == "0" and reset:
                wait = max(0, int(reset) - int(time.time())) + 2
                print(f"  rate limited; sleeping {wait}s...", file=sys.stderr)
                time.sleep(min(wait, 90))
                continue
            # secondary rate limit / abuse detection -> back off
            time.sleep(8 * (attempt + 1))
            continue
        r.raise_for_status()
    r.raise_for_status()
    return r  # unreachable


def search_pattern(pattern: str, queries: list[str], max_pages: int) -> dict:
    """Run every query for a pattern; return {full_name: repo_record}."""
    found: dict[str, dict] = {}
    for q in queries:
        for page in range(1, max_pages + 1):
            params = {"q": q, "sort": "stars", "order": "desc",
                      "per_page": 100, "page": page}
            r = get(API, params)
            items = r.json().get("items", [])
            if not items:
                break
            for it in items:
                fn = it["full_name"]
                rec = found.get(fn)
                if rec is None:
                    rec = repo_record(it)
                    rec["matched_patterns"] = set()
                    found[fn] = rec
                rec["matched_patterns"].add(pattern)
            # polite pacing for the search endpoint
            time.sleep(2.2)
        print(f"  [{pattern}] query done: {q!r} -> running total {len(found)}",
              file=sys.stderr)
    return found


def repo_record(it: dict) -> dict:
    lic = (it.get("license") or {})
    return {
        "full_name": it["full_name"],
        "html_url": it["html_url"],
        "clone_url": it["clone_url"],
        "stars": it.get("stargazers_count", 0),
        "forks": it.get("forks_count", 0),
        "open_issues": it.get("open_issues_count", 0),
        "language": it.get("language") or "",
        "size_kb": it.get("size", 0),
        "created_at": it.get("created_at", ""),
        "pushed_at": it.get("pushed_at", ""),
        "archived": it.get("archived", False),
        "is_fork": it.get("fork", False),
        "license": lic.get("spdx_id") or "",
        "topics": ",".join(it.get("topics", []) or []),
        "description": (it.get("description") or "").replace("\n", " ").strip(),
    }


def iso(dt: str):
    if not dt:
        return None
    return datetime.fromisoformat(dt.replace("Z", "+00:00"))


def compute_flags(rec: dict, now: datetime) -> dict:
    created = iso(rec["created_at"])
    pushed = iso(rec["pushed_at"])
    active = bool(pushed and (now - pushed) <= timedelta(days=365))      # commits in last 12 mo
    old_enough = bool(created and (now - created) >= timedelta(days=180))  # >= 6 months old
    has_license = rec["license"] not in ("", "NOASSERTION")
    blob = f"{rec['full_name']} {rec['description']} {rec['topics']}".lower()
    sample_like = any(h in blob for h in SAMPLE_HINTS)
    is_android_lang = rec["language"] in ANDROID_LANGS
    topics = set(t.strip() for t in rec["topics"].split(",") if t.strip())
    non_app = bool(topics & NON_APP_TOPICS)
    eligible = (active and old_enough and has_license and is_android_lang
                and not non_app and not rec["archived"] and not rec["is_fork"])
    return {
        "active_12mo": active,
        "older_6mo": old_enough,
        "has_oss_license": has_license,
        "is_android_lang": is_android_lang,
        "non_app": non_app,
        "sample_like": sample_like,
        "eligible_core": eligible,        # criteria excluding the sample/star judgement
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=3,
                    help="pages (x100 results) per query")
    ap.add_argument("--patterns", nargs="*", default=list(QUERIES),
                    help="subset of patterns to mine")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    all_repos: dict[str, dict] = {}
    for pattern in args.patterns:
        print(f"Mining {pattern}...", file=sys.stderr)
        part = search_pattern(pattern, QUERIES[pattern], args.max_pages)
        for fn, rec in part.items():
            if fn in all_repos:
                all_repos[fn]["matched_patterns"] |= rec["matched_patterns"]
            else:
                all_repos[fn] = rec

    rows = []
    for rec in all_repos.values():
        rec["matched_patterns"] = "|".join(sorted(rec["matched_patterns"]))
        rec.update(compute_flags(rec, now))
        rows.append(rec)
    rows.sort(key=lambda r: r["stars"], reverse=True)

    out = DATA / "corpus_candidates.csv"
    fields = ["full_name", "matched_patterns", "stars", "language", "forks",
              "open_issues", "size_kb", "created_at", "pushed_at", "archived",
              "is_fork", "license", "active_12mo", "older_6mo", "has_oss_license",
              "is_android_lang", "non_app", "sample_like", "eligible_core",
              "topics", "description", "html_url", "clone_url"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    elig = sum(1 for r in rows if r["eligible_core"])
    print(f"\nWrote {len(rows)} candidates ({elig} eligible_core) to {out}")
    for p in args.patterns:
        n = sum(1 for r in rows if p in r["matched_patterns"].split("|"))
        ne = sum(1 for r in rows if p in r["matched_patterns"].split("|")
                 and r["eligible_core"] and not r["sample_like"])
        print(f"  {p}: {n} candidates, {ne} eligible & non-sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
