"""
validate_ck.py — construct-validity check for our tree-sitter extractor.

Runs CK (Mauricio Aniche's established Java metrics tool) on the JAVA classes in the
corpus and Spearman-correlates CK's CBO/WMC/LCOM against ours for the same classes.
A high correlation evidences that our custom extractor measures the same constructs
as a peer-reviewed tool — mirroring how the course exemplars validate their frameworks.

CK is Java-only, which is exactly why it can only *validate* a subset and not be the
primary tool (the corpus is largely Kotlin).

Requires: tools/ck.jar  (jar-with-dependencies). If missing, prints how to obtain it.

Inputs : data/corpus.csv, data/metrics_raw.csv, repos/*
Output : data/ck_validation.csv  + console summary

Usage:  python analysis/validate_ck.py
"""
from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPOS = ROOT / "repos"
CK_JAR = ROOT / "tools" / "ck.jar"

# CK class.csv column -> our metrics_raw.csv column
PAIRS = {"cbo": "CBO", "wmc": "WMC", "lcom": "LCOM"}


def run_ck(repo: Path, outdir: Path) -> Path | None:
    # java -jar ck.jar <dir> <useJars> <maxFiles> <varsAndFields> <outdir/prefix>
    prefix = str(outdir / "ck_")
    r = subprocess.run(
        ["java", "-jar", str(CK_JAR), str(repo), "false", "0", "false", prefix],
        capture_output=True, text=True)
    cls = outdir / "ck_class.csv"
    if not cls.exists():
        print(f"  CK produced no output for {repo.name}: {r.stderr.strip()[:160]}",
              file=sys.stderr)
        return None
    return cls


def main() -> int:
    if not CK_JAR.exists():
        print("tools/ck.jar not found. Obtain the CK jar-with-dependencies, e.g.:\n"
              "  download ck-*-jar-with-dependencies.jar from\n"
              "  https://github.com/mauricioaniche/ck/releases  ->  tools/ck.jar")
        return 1

    ours = pd.read_csv(DATA / "metrics_raw.csv")
    ours = ours[ours["language"] == "java"].copy()
    ours["key"] = ours["file"].apply(lambda p: Path(p).name) + "::" + ours["class"].astype(str)

    corpus = list(csv.DictReader((DATA / "corpus.csv").open(encoding="utf-8")))
    ck_rows = []
    for row in corpus:
        if row.get("included") != "True":
            continue
        repo = REPOS / row["repo"]
        if not repo.exists():
            continue
        with tempfile.TemporaryDirectory() as td:
            cls = run_ck(repo, Path(td))
            if cls is None:
                continue
            df = pd.read_csv(cls)
            for _, r in df.iterrows():
                ck_rows.append({
                    "key": Path(str(r["file"])).name + "::" + str(r["class"]).split(".")[-1],
                    "cbo": r.get("cbo"), "wmc": r.get("wmc"), "lcom": r.get("lcom")})

    if not ck_rows:
        print("No CK rows collected (no Java classes, or CK failed).")
        return 1
    ck = pd.DataFrame(ck_rows)

    # match on simplified key: filename + simple class name
    ours["skey"] = ours["file"].apply(lambda p: Path(p).name) + "::" + \
        ours["class"].apply(lambda c: str(c).split(".")[-1])
    merged = ours.merge(ck, left_on="skey", right_on="key", suffixes=("_ours", "_ck"))

    results = []
    for ck_col, our_col in PAIRS.items():
        sub = merged[[our_col, ck_col]].dropna()
        if len(sub) >= 5:
            rho, p = spearmanr(sub[our_col], sub[ck_col])
            results.append({"metric": our_col, "n_classes": len(sub),
                            "spearman_rho": round(rho, 3), "p_value": round(p, 5)})
    res = pd.DataFrame(results)
    res.to_csv(DATA / "ck_validation.csv", index=False)
    print(f"Matched {len(merged)} Java classes against CK.")
    print(res.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
