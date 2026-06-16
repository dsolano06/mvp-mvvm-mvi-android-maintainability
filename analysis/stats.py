"""
stats.py — Stage 6. Statistical comparison of patterns + LaTeX/CSV table emission.

Samples are INDEPENDENT (different repos per pattern), so we use:
  - Kruskal-Wallis H  across the 3 patterns (omnibus, non-parametric)
  - pairwise Mann-Whitney U with Holm correction (post-hoc)
  - Cliff's delta effect size (non-parametric), with Romano magnitude labels
This corrects the proposal's "Wilcoxon signed-rank", which assumes *paired* data.

Analysis unit = one repository (per-repo medians), avoiding pseudo-replication from
treating classes within a repo as independent.

Inputs : data/metrics_per_repo.csv  (repo, pattern, CBO, LCOM, WMC, SLOC, NOM, ...)
         data/churn.csv             (repo, median_commits_per_month, median_churn_per_sloc, ...)
Outputs: data/descriptive.csv, data/stats_rq1.csv, data/stats_rq2.csv
         tables/*.tex  (IEEE-style tabular, generated from the same numbers)

Usage:  python analysis/stats.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kruskal, mannwhitneyu
from cliffs_delta import cliffs_delta

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TABLES = ROOT / "tables"
TABLES.mkdir(exist_ok=True)

PATTERNS = ["MVP", "MVVM", "MVI"]
ALPHA = 0.05

# Lower-is-better for all of these (coupling, cohesion-deficit, complexity, churn).
RQ1_METRICS = [("CBO", "Coupling Between Objects"),
               ("LCOM", "Lack of Cohesion (LCOM-HS)"),
               ("WMC", "Weighted Methods per Class")]
RQ2_METRICS = [("median_commits_per_month", "Change frequency (commits/month)"),
               ("median_churn_per_sloc", "Churn per SLOC")]


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = (m - rank) * pvals[i]
        running = max(running, val)
        adj[i] = min(running, 1.0)
    return adj


def cliff_magnitude(d: float) -> str:
    a = abs(d)
    if a < 0.147:
        return "negligible"
    if a < 0.33:
        return "small"
    if a < 0.474:
        return "medium"
    return "large"


def load() -> pd.DataFrame:
    m = pd.read_csv(DATA / "metrics_per_repo.csv")
    churn_path = DATA / "churn.csv"
    if churn_path.exists():
        c = pd.read_csv(churn_path)
        m = m.merge(c, on="repo", how="left")
    return m


def descriptive(df: pd.DataFrame, metrics) -> pd.DataFrame:
    rows = []
    for col, _label in metrics:
        for pat in PATTERNS:
            s = df.loc[df["pattern"] == pat, col].dropna()
            if len(s):
                rows.append({"metric": col, "pattern": pat, "n": len(s),
                             "median": round(s.median(), 3),
                             "mean": round(s.mean(), 3),
                             "iqr": round(s.quantile(.75) - s.quantile(.25), 3),
                             "min": round(s.min(), 3), "max": round(s.max(), 3)})
    return pd.DataFrame(rows)


def analyze(df: pd.DataFrame, metrics) -> pd.DataFrame:
    out_rows = []
    for col, label in metrics:
        groups = [df.loc[df["pattern"] == p, col].dropna().values for p in PATTERNS]
        if any(len(g) < 2 for g in groups):
            continue
        H, p_kw = kruskal(*groups)
        pairs = [("MVP", "MVVM"), ("MVP", "MVI"), ("MVVM", "MVI")]
        raw_p, recs = [], []
        for a, b in pairs:
            ga = df.loc[df["pattern"] == a, col].dropna().values
            gb = df.loc[df["pattern"] == b, col].dropna().values
            U, p = mannwhitneyu(ga, gb, alternative="two-sided")
            d, _ = cliffs_delta(ga, gb)
            raw_p.append(p)
            recs.append({"a": a, "b": b, "U": U, "p_raw": p,
                         "cliffs_delta": round(d, 3), "magnitude": cliff_magnitude(d)})
        adj = holm(raw_p)
        for r, pa in zip(recs, adj):
            out_rows.append({
                "metric": col, "label": label,
                "kruskal_H": round(H, 3), "kruskal_p": round(p_kw, 4),
                "kruskal_sig": p_kw < ALPHA,
                "pair": f"{r['a']} vs {r['b']}",
                "mwu_U": r["U"], "p_raw": round(r["p_raw"], 4),
                "p_holm": round(pa, 4), "sig_holm": pa < ALPHA,
                "cliffs_delta": r["cliffs_delta"], "magnitude": r["magnitude"],
            })
    return pd.DataFrame(out_rows)


def latex_descriptive(desc: pd.DataFrame, metrics, fname: str, caption: str, label: str):
    cols = [c for c, _ in metrics]
    lines = [r"\begin{table}[!t]", r"\centering", f"\\caption{{{caption}}}",
             f"\\label{{{label}}}",
             r"\begin{tabular}{|l|" + "r|" * len(PATTERNS) + "}", r"\hline",
             "\\textbf{Metric} & " + " & ".join(f"\\textbf{{{p}}}" for p in PATTERNS)
             + r" \\ \hline"]
    for col in cols:
        cells = []
        for p in PATTERNS:
            row = desc[(desc.metric == col) & (desc.pattern == p)]
            cells.append(f"{row['median'].iloc[0]:.2f}" if len(row) else "--")
        lines.append(f"{col} (median) & " + " & ".join(cells) + r" \\ \hline")
    lines += [r"\end{tabular}", r"\end{table}"]
    (TABLES / fname).write_text("\n".join(lines), encoding="utf-8")


# Clean LaTeX labels for metrics whose CSV names contain underscores.
NICE_METRIC = {
    "median_commits_per_month": "Commits/month",
    "median_churn_per_sloc": "Churn/SLOC",
}


def latex_tests(res: pd.DataFrame, fname: str, caption: str, label: str):
    lines = [r"\begin{table}[!t]", r"\centering", f"\\caption{{{caption}}}",
             f"\\label{{{label}}}", r"\resizebox{\columnwidth}{!}{%",
             r"\begin{tabular}{|l|l|r|r|r|l|}", r"\hline",
             r"\textbf{Metric} & \textbf{Pair} & \textbf{$p_{KW}$} & "
             r"\textbf{$p_{Holm}$} & \textbf{$\delta$} & \textbf{Effect} \\ \hline"]
    for _, r in res.iterrows():
        star = "*" if r["sig_holm"] else ""
        metric = NICE_METRIC.get(r["metric"], r["metric"])
        lines.append(
            f"{metric} & {r['pair']} & {r['kruskal_p']:.3f} & "
            f"{r['p_holm']:.3f}{star} & {r['cliffs_delta']:.2f} & {r['magnitude']} "
            r"\\ \hline")
    lines += [r"\end{tabular}}",
              r"\\[2pt] \footnotesize{* significant at $\alpha=0.05$ after Holm correction.}",
              r"\end{table}"]
    (TABLES / fname).write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    df = load()
    print(f"Loaded {len(df)} repos: " +
          ", ".join(f"{p}={sum(df.pattern==p)}" for p in PATTERNS))

    desc1 = descriptive(df, RQ1_METRICS)
    desc2 = descriptive(df, RQ2_METRICS)
    pd.concat([desc1, desc2]).to_csv(DATA / "descriptive.csv", index=False)

    rq1 = analyze(df, RQ1_METRICS)
    rq2 = analyze(df, RQ2_METRICS)
    rq1.to_csv(DATA / "stats_rq1.csv", index=False)
    rq2.to_csv(DATA / "stats_rq2.csv", index=False)

    latex_descriptive(desc1, RQ1_METRICS, "tab_desc_rq1.tex",
                      "Presentation-layer structural metrics by pattern (per-repo medians)",
                      "tab:desc_rq1")
    if not rq1.empty:
        latex_tests(rq1, "tab_tests_rq1.tex",
                    "RQ1 -- Kruskal-Wallis and pairwise Mann-Whitney (Holm)", "tab:tests_rq1")
    if not rq2.empty:
        latex_tests(rq2, "tab_tests_rq2.tex",
                    "RQ2 -- Change-proneness tests", "tab:tests_rq2")
    print("Wrote stats CSVs to data/ and LaTeX tables to tables/")
    if not rq1.empty:
        print("\nRQ1 summary:\n", rq1.to_string(index=False))
    if not rq2.empty:
        print("\nRQ2 summary:\n", rq2.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
