"""
figures.py — Stage 7. Static vector figures for the LaTeX paper.

Reads the same data/*.csv as everything else and writes per-metric boxplots
(pattern on x-axis) to figures/*.pdf (vector, as the IEEE prefers) and *.png.

Usage: python analysis/figures.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)

PATTERNS = ["MVP", "MVVM", "MVI"]
METRICS = [("CBO", "Coupling Between Objects (CBO)"),
           ("LCOM", "Lack of Cohesion (LCOM-HS)"),
           ("WMC", "Weighted Methods per Class (WMC)"),
           ("median_commits_per_month", "Change frequency (commits/month)"),
           ("median_churn_per_sloc", "Churn per SLOC")]


def load() -> pd.DataFrame:
    df = pd.read_csv(DATA / "metrics_per_repo.csv")
    cp = DATA / "churn.csv"
    if cp.exists():
        df = df.merge(pd.read_csv(cp), on="repo", how="left")
    return df


def boxplot(df: pd.DataFrame, col: str, title: str, fname: str):
    data = [df.loc[df.pattern == p, col].dropna().values for p in PATTERNS]
    if all(len(d) == 0 for d in data):
        return
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    bp = ax.boxplot(data, tick_labels=PATTERNS, patch_artist=True, showmeans=True)
    for patch, color in zip(bp["boxes"], ["#8ecae6", "#ffb703", "#90be6d"]):
        patch.set_facecolor(color)
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("Architectural pattern", fontsize=8)
    ax.tick_params(labelsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(FIG / f"{fname}.pdf")
    fig.savefig(FIG / f"{fname}.png", dpi=150)
    plt.close(fig)


def main() -> int:
    df = load()
    for col, title in METRICS:
        if col in df.columns:
            boxplot(df, col, title, f"box_{col}")
    print(f"Wrote figures to {FIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
