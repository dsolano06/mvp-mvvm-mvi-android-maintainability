# MVP / MVVM / MVI — Empirical Android Maintainability Study

Reproducible mining-and-analysis pipeline and paper for *"Empirical Analysis of the
Impact of MVP, MVVM and MVI Patterns on Android Code Maintainability through Repository
Mining"* (Daniel Solano Ávila, UNA).

## Deliverables
- **`MiPaper.tex` / `MiPaper.pdf`** — the IEEE conference paper (covers cronograma
  Entregables #2 and #3: Related Work, Background, Methodology, Results, Conclusions).
- **`results/report.html`** — self-contained, auto-reproducible visual report (open in any
  browser; Plotly inlined, no network needed).
- **`data/*.csv`** — all mined data and statistical outputs (single source of truth).
- **`figures/*.pdf`**, **`tables/*.tex`** — paper figures/tables, generated from the CSVs.

## What the study does
Mines open-source Android apps from GitHub; confirms each app's architecture from code
signals; measures presentation-layer **CBO, LCOM-HS, WMC** (uniform Kotlin+Java tree-sitter
extractor, validated against **CK**, ρ≈0.83–0.85 over 1,204 Java classes) plus git-history
**change-proneness**; compares patterns with Kruskal–Wallis + Mann–Whitney (Holm) and
Cliff's δ. Final corpus: 30 apps (MVVM=12, MVI=12, MVP=6); 28 analysed.

## Reproduce
```bash
pip install -r requirements.txt          # Python 3.12; needs git + Java (for CK)
# optional: export GITHUB_TOKEN=...       # lifts API rate limits

python analysis/mine_repos.py --max-pages 8      # -> data/corpus_candidates.csv
python analysis/select_and_clone.py --per-pattern 12   # clone + classify -> data/corpus.csv (+ repos/)
python analysis/extract_metrics.py               # -> data/metrics_raw.csv, metrics_per_repo.csv
python analysis/validate_ck.py                   # CK cross-check -> data/ck_validation.csv
python analysis/change_proneness.py --all        # -> data/churn.csv
python analysis/stats.py                         # -> data/stats_*.csv, tables/*.tex
python analysis/figures.py                       # -> figures/*.pdf
python analysis/build_report.py                  # -> results/report.html
```
Selection applies inclusion/exclusion criteria, an MVI-precedence classifier, an
Android-app check (incl. version-catalog plugin aliases), a published-library filter, and a
documented manual-exclusion list (`MANUAL_EXCLUDE` in `select_and_clone.py`).

## Build the PDF
No local LaTeX needed for editing; the bundled `tools/tectonic_bin/tectonic.exe` compiles it:
```bash
tools/tectonic_bin/tectonic.exe -o . MiPaper.tex
```
Or upload `MiPaper.tex` + `tables/` + `figures/` to Overleaf (IEEEtran is built in).

## Notes
- `repos/` (clones) and the CK jar are git-ignored.
- Runtime performance (CPU/memory) is out of scope (not mineable); see the paper's Future Work
  for the emulator-benchmark path.
