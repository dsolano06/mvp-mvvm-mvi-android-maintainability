"""Quick static lint of MiPaper.tex (no LaTeX engine needed): citation/label/brace
checks and detection of raw underscores leaking into typeset text."""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
tex = (ROOT / "MiPaper.tex").read_text(encoding="utf-8")


def expand(m):
    p = pathlib.Path(m.group(1).strip())
    if not p.suffix:
        p = p.with_suffix(".tex")
    fp = ROOT / p
    return fp.read_text(encoding="utf-8") if fp.exists() else ""


full = re.sub(r"\\input\{([^}]+)\}", expand, tex)

cites = set()
for grp in re.findall(r"\\cite\{([^}]+)\}", full):
    cites |= {c.strip() for c in grp.split(",")}
bibs = set(re.findall(r"\\bibitem\{([^}]+)\}", full))
print("CITES missing bibitem:", cites - bibs or "none")
print("BIBITEMS unused     :", bibs - cites or "none")

labels = set(re.findall(r"\\label\{([^}]+)\}", full))
refs = set(re.findall(r"\\ref\{([^}]+)\}", full))
print("REFS missing label  :", refs - labels or "none")

print("brace balance (0=ok):", full.count("{") - full.count("}"))
print("begin/end counts    :", len(re.findall(r"\\begin\{", full)),
      len(re.findall(r"\\end\{", full)))

t = re.sub(r"%.*", "", full)
t = re.sub(r"\$[^$]*\$", "", t)
t = re.sub(r"\\(label|ref|cite|input|includegraphics|graphicspath)\{[^}]*\}", "", t)
bad = [ln.strip() for ln in t.splitlines() if "_" in ln]
print("raw underscores in text:", len(bad))
for b in bad[:6]:
    print("   >", b[:90])
