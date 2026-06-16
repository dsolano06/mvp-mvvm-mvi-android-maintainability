"""
select_and_clone.py — bridges mining and analysis.

Picks candidate repos per pattern from data/corpus_candidates.csv (eligible, non-sample,
sorted by stars), clones them in full (history needed for change-proneness), then runs
classify.py to CONFIRM the architecture from code. Final pattern = code-confirmed label.

Writes data/corpus.csv : the actual study corpus (repo dir, declared vs confirmed pattern,
confidence, compose flag, stars). Repos whose code doesn't confirm a single clear pattern
are kept in the CSV but flagged (included=False) so selection stays transparent.

Usage:
    python analysis/select_and_clone.py --per-pattern 3          # pilot
    python analysis/select_and_clone.py --per-pattern 15 --min-stars 100
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

from classify import classify_repo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPOS = ROOT / "repos"
REPOS.mkdir(exist_ok=True)


# Manually verified exclusions: repos that pass the automated app checks but are in fact
# architecture LIBRARIES/FRAMEWORKS (they bundle a demo/sample app and apply their publish
# plugin via build-logic convention plugins, evading is_published_library). MSR studies
# routinely include such manual validation; documenting it keeps selection reproducible.
MANUAL_EXCLUDE = {
    "uber/RIBs", "kymjs/TheMVP", "slackhq/circuit", "freeletics/FlowRedux",
    "freeletics/khonshu", "vivid-money/elmslie", "ggrell/RxReactor", "tunjid/Mutator",
    "tunjid/Tiler", "airbnb/mavericks", "badoo/MVICore", "arkivanov/MVIKotlin",
    "spotify/mobius", "orbit-mvi/orbit-mvi", "cashapp/molecule",
    "kioba/anchor",                 # MVI library (docs site, published), not an app
    "Popalay/Tracktor-ComposeUI",   # Square Workflow framework, not classic MVI
}


def dirname(full_name: str) -> str:
    return full_name.replace("/", "__")


def is_android_app(repo: Path) -> bool:
    """A real Android *application* (not a library/framework): has an AndroidManifest and
    applies the Android application Gradle plugin. Matches both the literal id
    `com.android.application` and version-catalog aliases (`libs.plugins.android.application`,
    and the `android.application` plugin entry in libs.versions.toml)."""
    has_manifest = any(repo.rglob("AndroidManifest.xml"))
    if not has_manifest:
        return False
    files = (list(repo.rglob("build.gradle")) + list(repo.rglob("build.gradle.kts"))
             + list(repo.rglob("libs.versions.toml")))
    for gradle in files:
        parts = {x.lower() for x in gradle.parts}
        if "build" in parts:
            continue
        try:
            txt = gradle.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "android.application" in txt:   # covers com.android.application + catalog aliases
            return True
    return False


# Markers of a consumable/published LIBRARY (which may bundle a demo app module and thus
# pass is_android_app). Real end-user apps do not publish artifacts to Maven.
LIBRARY_PUBLISH = ("maven-publish", "vanniktech.maven.publish", "com.gradle.plugin-publish",
                   "publishToMavenCentral", "publishtosonatype")


def is_published_library(repo: Path) -> bool:
    files = (list(repo.rglob("build.gradle")) + list(repo.rglob("build.gradle.kts")))
    for gradle in files:
        if "build" in {x.lower() for x in gradle.parts}:
            continue
        try:
            txt = gradle.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        if any(m.lower() in txt for m in LIBRARY_PUBLISH):
            return True
    return False


def clone(full_name: str, url: str) -> Path | None:
    dest = REPOS / dirname(full_name)
    if dest.exists():
        return dest
    print(f"  cloning {full_name} ...", file=sys.stderr)
    r = subprocess.run(["git", "clone", "--single-branch", "--quiet", url, str(dest)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    clone failed: {r.stderr.strip()[:160]}", file=sys.stderr)
        return None
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-pattern", type=int, default=3)
    ap.add_argument("--min-stars", type=int, default=0,
                    help="override; default uses per-pattern adaptive thresholds")
    ap.add_argument("--patterns", nargs="*", default=["MVP", "MVVM", "MVI"])
    args = ap.parse_args()

    cand = list(csv.DictReader((DATA / "corpus_candidates.csv").open(encoding="utf-8")))

    selected: list[dict] = []
    for pattern in args.patterns:
        picked = 0
        for c in cand:  # already sorted by stars desc
            if picked >= args.per_pattern:
                break
            declared = c["matched_patterns"].split("|")
            if pattern not in declared:
                continue
            if c["full_name"] in MANUAL_EXCLUDE:
                continue
            if c["eligible_core"] != "True" or c["sample_like"] == "True":
                continue
            if args.min_stars and int(c["stars"]) < args.min_stars:
                continue
            dest = clone(c["full_name"], c["clone_url"])
            if dest is None:
                continue
            android_app = is_android_app(dest)
            published_lib = is_published_library(dest)
            info = classify_repo(dest)
            # inclusion: a real Android app (not a published library), code confirms the
            # declared pattern with reasonable confidence, non-trivial (not a single-screen sample)
            included = (android_app and not published_lib
                        and info["assigned_pattern"] == pattern
                        and info["confidence"] >= 0.4
                        and info["n_source_files"] >= 15)
            row = {
                "repo": dest.name, "full_name": c["full_name"],
                "declared_pattern": pattern, "stars": c["stars"],
                "language": c["language"], "android_app": android_app,
                "published_lib": published_lib,
                "confirmed_pattern": info["assigned_pattern"],
                "confidence": info["confidence"],
                "uses_compose": info["uses_compose"],
                "n_source_files": info["n_source_files"],
                "score_MVP": info["score_MVP"], "score_MVVM": info["score_MVVM"],
                "score_MVI": info["score_MVI"],
                "included": included,
                "html_url": c["html_url"],
            }
            selected.append(row)
            if included:
                picked += 1
            print(f"  [{pattern}] {c['full_name']}: app={android_app} "
                  f"confirmed={info['assigned_pattern']} conf={info['confidence']} "
                  f"files={info['n_source_files']} -> included={included}", file=sys.stderr)

    out = DATA / "corpus.csv"
    fields = ["repo", "full_name", "declared_pattern", "confirmed_pattern", "confidence",
              "included", "android_app", "published_lib", "uses_compose", "stars",
              "language", "n_source_files", "score_MVP", "score_MVVM", "score_MVI", "html_url"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(selected)
    inc = sum(1 for r in selected if r["included"])
    print(f"\nWrote {len(selected)} repos ({inc} included) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
