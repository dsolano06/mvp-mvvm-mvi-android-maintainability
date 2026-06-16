"""
classify.py — Stage 2 of the pipeline.

Confirms a repository's architectural pattern from *code signals*, not just the
README/topic label, and tags presentation-layer files. Operates on a cloned repo
directory. Also detects Jetpack Compose usage (for RQ3).

The same signal definitions are reused by extract_metrics.py for layer scoping, so
keep this the single source of truth for "what is a Presenter/ViewModel/MVI class".

API:
    classify_repo(repo_dir) -> dict   # scores, assigned pattern, confidence, compose flag
    layer_of(path, text)    -> str    # 'presentation' | 'other'  (per-file)
    pattern_of_file(path, text) -> set # which patterns' presentation markers a file shows

CLI:
    python analysis/classify.py repos/<owner__name>
    python analysis/classify.py --all           # classify everything under repos/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS = ROOT / "repos"

SRC_EXT = {".kt", ".java"}

# --- Pattern signals -------------------------------------------------------
# Filename markers (basename without extension) that denote presentation-layer
# classes for each pattern.
NAME_MARKERS = {
    "MVP":  (re.compile(r"Presenter$"),),
    "MVVM": (re.compile(r"ViewModel$"),),
    "MVI":  (re.compile(r"(Intent|Reducer|Store|StateMachine)$"),),
}

# Content markers: regexes searched inside source text. Weighted signals used to
# score the whole repo (not per-file).
CONTENT_SIGNALS = {
    "MVP": [
        re.compile(r"\bclass\s+\w*Presenter\b"),
        re.compile(r"\binterface\s+\w*(View|Contract)\b"),
        re.compile(r"\b\w*Presenter\s*\("),
    ],
    "MVVM": [
        re.compile(r":\s*(Android)?ViewModel\b"),          # extends ViewModel
        re.compile(r"\bextends\s+\w*ViewModel\b"),           # java
        re.compile(r"\b(MutableLiveData|LiveData|MutableStateFlow|StateFlow)\b"),
        re.compile(r"\bviewModelScope\b"),
        re.compile(r"\bby\s+viewModels?\b"),
    ],
    "MVI": [
        re.compile(r"\b(orbit|mvikotlin|mavericks|mobius|ballast)\b", re.I),
        re.compile(r"\bsealed\s+(class|interface)\s+\w*(Intent|Action|State|Event|Effect)\b"),
        re.compile(r"\bfun\s+reduce\b"),
        re.compile(r"\bintent\s*\{"),                         # orbit-mvi DSL
        re.compile(r"\bcontainer\s*<"),                       # orbit container<State, SideEffect>
    ],
}

COMPOSE_SIGNAL = re.compile(r"@Composable|androidx\.compose")

# Dependency-file MVI hints (strong signal of an intentional MVI architecture).
DEP_FILES = ("build.gradle", "build.gradle.kts", "libs.versions.toml")
DEP_MVI = re.compile(r"orbit-mvi|mvikotlin|mavericks|spotify\.mobius|ballast", re.I)


def _iter_sources(repo_dir: Path):
    for p in repo_dir.rglob("*"):
        if p.suffix in SRC_EXT and p.is_file():
            # skip generated/build/test-resource noise
            parts = {x.lower() for x in p.parts}
            if "build" in parts or ".gradle" in parts:
                continue
            yield p


def pattern_of_file(path: Path, text: str) -> set[str]:
    """Patterns for which this file looks like a presentation-layer class."""
    stem = path.stem
    out = set()
    for pat, regs in NAME_MARKERS.items():
        if any(r.search(stem) for r in regs):
            out.add(pat)
    # MVVM: a class extending ViewModel even if not named *ViewModel
    if "MVVM" not in out and (re.search(r":\s*(Android)?ViewModel\b", text)
                              or re.search(r"\bextends\s+\w*ViewModel\b", text)):
        out.add("MVVM")
    # MVP: a class named *Presenter handled by NAME_MARKERS; also View contracts
    return out


def layer_of(path: Path, text: str) -> str:
    return "presentation" if pattern_of_file(path, text) else "other"


def classify_repo(repo_dir: Path) -> dict:
    scores = {"MVP": 0, "MVVM": 0, "MVI": 0}
    pres_files = {"MVP": 0, "MVVM": 0, "MVI": 0}
    compose_files = 0
    n_src = 0
    for p in _iter_sources(repo_dir):
        n_src += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, regs in CONTENT_SIGNALS.items():
            for r in regs:
                if r.search(text):
                    scores[pat] += 1
        for pat in pres_files:
            if pat in pattern_of_file(p, text):
                pres_files[pat] += 1
        if COMPOSE_SIGNAL.search(text):
            compose_files += 1

    # dependency-declared MVI is a strong, intentional signal
    dep_mvi = False
    for dep in DEP_FILES:
        for f in repo_dir.rglob(dep):
            try:
                if DEP_MVI.search(f.read_text(encoding="utf-8", errors="ignore")):
                    dep_mvi = True
            except Exception:
                pass
    if dep_mvi:
        scores["MVI"] += 5

    # Pattern precedence. MVI on Android is implemented ON TOP OF ViewModel+StateFlow,
    # so MVVM signals always co-occur with MVI; argmax would mislabel MVI apps as MVVM.
    # Distinctive MVI markers (sealed Intent/State + reduce, or an MVI library) therefore
    # take precedence. Genuine MVVM/MVP apps score ~0-1 on MVI; >=3 is a reliable cutoff.
    mvp, mvvm, mvi = scores["MVP"], scores["MVVM"], scores["MVI"]
    if mvi >= 3:
        assigned = "MVI"
    elif mvp == 0 and mvvm == 0:
        assigned = "UNKNOWN"
    elif mvp >= mvvm:
        assigned = "MVP"
    else:
        assigned = "MVVM"
    total = sum(scores.values()) or 1
    confidence = round(scores.get(assigned, 0) / total, 3)
    return {
        "repo": repo_dir.name,
        "n_source_files": n_src,
        "score_MVP": scores["MVP"],
        "score_MVVM": scores["MVVM"],
        "score_MVI": scores["MVI"],
        "pres_MVP": pres_files["MVP"],
        "pres_MVVM": pres_files["MVVM"],
        "pres_MVI": pres_files["MVI"],
        "compose_files": compose_files,
        "uses_compose": compose_files > 0,
        "dep_mvi": dep_mvi,
        "assigned_pattern": assigned,
        "confidence": confidence,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", help="path to a cloned repo dir")
    ap.add_argument("--all", action="store_true", help="classify every dir under repos/")
    args = ap.parse_args()

    targets = []
    if args.all:
        targets = [d for d in REPOS.iterdir() if d.is_dir()]
    elif args.repo:
        targets = [Path(args.repo)]
    else:
        ap.error("give a repo dir or --all")

    for d in targets:
        print(json.dumps(classify_repo(d), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
