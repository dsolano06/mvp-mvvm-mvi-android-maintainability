"""
extract_metrics.py — Stage 3. Uniform Kotlin+Java static metrics via tree-sitter.

One toolchain for both languages (Android is mostly Kotlin; Java-only tools would bias
the corpus). Per class it computes, from the real grammar node-types discovered with
probe_grammar.py:

  SLOC  non-blank, non-comment source lines spanning the class
  NOM   number of methods
  WMC   sum of per-method McCabe complexity (1 + decision points)
  CBO   distinct external types referenced  (syntactic coupling proxy; validated vs CK)
  LCOM  Henderson-Sellers LCOM-HS from method<->field access  (NaN if <2 methods or 0 fields)

Layer scoping: only PRESENTATION-layer classes (Presenter / ViewModel / MVI state-intent)
feed the per-repo aggregates, for an equitable cross-pattern comparison. Tests and
build/generated dirs are skipped.

Inputs : data/corpus.csv, repos/*
Outputs: data/metrics_raw.csv  (per class)
         data/metrics_per_repo.csv  (per repo: median over presentation classes + pattern)

Usage:  python analysis/extract_metrics.py
"""
from __future__ import annotations

import csv
import math
import re
import statistics as st
from pathlib import Path

import pandas as pd
from tree_sitter import Parser, Node
from tree_sitter_language_pack import get_language

from classify import pattern_of_file

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPOS = ROOT / "repos"

LANGS = {".kt": "kotlin", ".java": "java"}
PARSERS = {name: Parser(get_language(name)) for name in ("kotlin", "java")}

# --- per-language grammar vocabulary (from probe_grammar.py) ----------------
SPEC = {
    "kotlin": {
        "class": {"class_declaration", "object_declaration"},
        "method": {"function_declaration"},
        "name_field": "type_identifier",      # class name = first child of this type
        "method_name": "simple_identifier",
        "decision": {"if_expression", "for_statement", "while_statement",
                     "do_while_statement", "catch_block", "conjunction_expression",
                     "disjunction_expression", "elvis_expression"},
        "when_entry": "when_entry", "when_cond": "when_condition",
        "type_ref": {"type_identifier"},
        "ident": "simple_identifier",
        "primitives": {"Int", "Long", "Short", "Byte", "Double", "Float", "Boolean",
                       "Char", "Unit", "Nothing"},
    },
    "java": {
        "class": {"class_declaration", "interface_declaration", "enum_declaration",
                  "record_declaration"},
        "method": {"method_declaration", "constructor_declaration"},
        "name_field": "identifier",
        "method_name": "identifier",
        "decision": {"if_statement", "for_statement", "enhanced_for_statement",
                     "while_statement", "do_statement", "catch_clause",
                     "ternary_expression"},
        "switch_label": "switch_label",
        "binary": "binary_expression",
        "type_ref": {"type_identifier", "scoped_type_identifier"},
        "ident": "identifier",
        "primitives": set(),   # Java primitives are integral_type/void_type, not type_identifier
    },
}


def children_by_type(node: Node, types) -> list[Node]:
    return [c for c in node.children if c.type in types]


def descend(node: Node, stop: set[str]):
    """Yield all descendants without entering subtrees whose type is in `stop`."""
    for c in node.children:
        yield c
        if c.type not in stop:
            yield from descend(c, stop)


def first_named(node: Node, type_: str) -> Node | None:
    for c in node.children:
        if c.type == type_:
            return c
    return None


def class_name(node: Node, spec) -> str:
    n = first_named(node, spec["name_field"])
    return n.text.decode() if n else "<anon>"


def collect_methods(class_node: Node, spec) -> list[Node]:
    body = first_named(class_node, "class_body") or first_named(class_node, "enum_body")
    if body is None:
        return []
    out = []
    for d in descend(body, spec["class"]):       # stop at nested classes
        if d.type in spec["method"]:
            out.append(d)
    return out


def collect_fields(class_node: Node, lang: str, spec) -> set[str]:
    fields: set[str] = set()
    if lang == "kotlin":
        # primary-constructor val/var params are properties
        pc = first_named(class_node, "primary_constructor")
        if pc:
            for cp in (c for c in pc.children if c.type == "class_parameter"):
                if any(ch.type == "binding_pattern_kind" for ch in cp.children):
                    sid = first_named(cp, "simple_identifier")
                    if sid:
                        fields.add(sid.text.decode())
        body = first_named(class_node, "class_body")
        if body:
            for d in descend(body, spec["class"]):
                if d.type == "property_declaration":
                    vd = first_named(d, "variable_declaration")
                    sid = first_named(vd, "simple_identifier") if vd else None
                    if sid:
                        fields.add(sid.text.decode())
    else:  # java
        body = first_named(class_node, "class_body")
        if body:
            for d in descend(body, spec["class"]):
                if d.type == "field_declaration":
                    for vdec in (c for c in d.children if c.type == "variable_declarator"):
                        idn = first_named(vdec, "identifier")
                        if idn:
                            fields.add(idn.text.decode())
    return fields


def method_complexity(method: Node, lang: str, spec) -> int:
    cc = 1
    for d in descend(method, spec["class"]):
        if d.type in spec["decision"]:
            cc += 1
        elif lang == "kotlin" and d.type == spec["when_entry"]:
            if first_named(d, spec["when_cond"]) is not None:   # non-else branch
                cc += 1
        elif lang == "java" and d.type == spec.get("switch_label"):
            if d.text.decode().strip().startswith("case"):
                cc += 1
        elif lang == "java" and d.type == spec.get("binary"):
            op = d.child_by_field_name("operator")
            if op is not None and op.type in ("&&", "||"):
                cc += 1
    return cc


def method_field_access(method: Node, fields: set[str], spec) -> set[str]:
    used = set()
    for d in descend(method, spec["class"]):
        if d.type == spec["ident"]:
            t = d.text.decode()
            if t in fields:
                used.add(t)
    return used


def class_cbo(class_node: Node, own: str, lang: str, spec) -> int:
    types: set[str] = set()
    for d in descend(class_node, spec["class"]):
        if d.type in spec["type_ref"]:
            t = d.text.decode()
            if d.type == "scoped_type_identifier":
                t = t.split(".")[-1]
            types.add(t)
        # Kotlin object creation: call_expression -> Capitalized simple_identifier
        if lang == "kotlin" and d.type == "call_expression":
            callee = d.children[0] if d.children else None
            if callee is not None and callee.type == "simple_identifier":
                name = callee.text.decode()
                if name[:1].isupper():
                    types.add(name)
    types.discard(own)
    types -= spec["primitives"]
    return len(types)


def class_sloc(class_node: Node, source: bytes) -> int:
    text = source[class_node.start_byte:class_node.end_byte].decode("utf-8", "ignore")
    n = 0
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith(("//", "*", "/*", "*/")):
            n += 1
    return n


def lcom_hs(methods, field_sets, fields) -> float:
    """Henderson-Sellers LCOM-HS over DECLARED attributes (unused fields count, mu=0)."""
    m = len(methods)
    a = len(fields)
    if m <= 1 or a == 0:
        return math.nan
    total = sum(sum(1 for fs in field_sets if f in fs) for f in fields)
    mean_mu = total / a
    return (m - mean_mu) / (m - 1)


PRES_NAME = re.compile(r"(Presenter|ViewModel|Reducer|StateMachine|Store|Controller)$")
PRES_BASE = re.compile(r"(ViewModel|Presenter)$")


def class_supertypes(class_node: Node, lang: str) -> list[str]:
    names: list[str] = []
    if lang == "kotlin":
        for ds in (c for c in class_node.children if c.type == "delegation_specifier"):
            for d in descend(ds, set()):
                if d.type == "type_identifier":
                    names.append(d.text.decode())
    else:  # java
        for c in class_node.children:
            if c.type in ("superclass", "super_interfaces"):
                for d in descend(c, set()):
                    if d.type in ("type_identifier", "scoped_type_identifier"):
                        names.append(d.text.decode().split(".")[-1])
    return names


def is_presentation(name: str, supertypes: list[str]) -> bool:
    """A presentation-layer LOGIC HOLDER: named like a Presenter/ViewModel/Store/Reducer,
    or extending a ViewModel/Presenter base. Pure State/Intent *data* classes are excluded
    (they carry no presentation logic), keeping the cross-pattern comparison about behaviour."""
    if PRES_NAME.search(name):
        return True
    return any(PRES_BASE.search(s) for s in supertypes)


def analyze_file(path: Path, repo_pattern: str) -> list[dict]:
    lang = LANGS[path.suffix]
    spec = SPEC[lang]
    source = path.read_bytes()
    tree = PARSERS[lang].parse(source)
    rows = []
    # find class-like nodes anywhere (top-level + nested)
    stack = [tree.root_node]
    classes = []
    while stack:
        n = stack.pop()
        for c in n.children:
            if c.type in spec["class"]:
                classes.append(c)
            stack.append(c)
    for cnode in classes:
        name = class_name(cnode, spec)
        methods = collect_methods(cnode, spec)
        fields = collect_fields(cnode, lang, spec)
        field_sets = [method_field_access(mn, fields, spec) for mn in methods]
        wmc = sum(method_complexity(mn, lang, spec) for mn in methods)
        supertypes = class_supertypes(cnode, lang)
        rows.append({
            "file": str(path.relative_to(ROOT)),
            "class": name,
            "language": lang,
            "pattern": repo_pattern,
            "is_presentation": is_presentation(name, supertypes),
            "SLOC": class_sloc(cnode, source),
            "NOM": len(methods),
            "WMC": wmc,
            "CBO": class_cbo(cnode, name, lang, spec),
            "LCOM": round(lcom_hs(methods, field_sets, fields), 4),
        })
    return rows


def skip(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return bool(parts & {"build", "test", "androidtest", "generated", ".gradle"})


def main() -> int:
    corpus = list(csv.DictReader((DATA / "corpus.csv").open(encoding="utf-8")))
    included = [r for r in corpus if r.get("included") == "True"]
    print(f"Extracting metrics for {len(included)} included repos...")

    raw: list[dict] = []
    for r in included:
        repo_dir = REPOS / r["repo"]
        pattern = r["confirmed_pattern"]
        nfiles = 0
        for path in repo_dir.rglob("*"):
            if path.suffix in LANGS and path.is_file() and not skip(path):
                try:
                    raw.extend(analyze_file(path, pattern))
                    nfiles += 1
                except Exception as e:
                    print(f"   ! {path.name}: {e}")
        print(f"  {r['repo']} [{pattern}]: {nfiles} files")

    rawdf = pd.DataFrame(raw)
    rawdf.to_csv(DATA / "metrics_raw.csv", index=False)
    print(f"Wrote {len(rawdf)} classes to metrics_raw.csv")

    # aggregate presentation-layer classes per repo (median). Unit of analysis = a
    # presentation class that actually holds logic (>=1 method); empty marker/stub
    # ViewModels (NOM=0) carry no behaviour and would bias WMC/CBO toward zero.
    pres = rawdf[(rawdf["is_presentation"]) & (rawdf["NOM"] >= 1)].copy()
    pres["repo"] = pres["file"].apply(lambda p: Path(p).parts[1])  # repos/<repo>/...
    agg_rows = []
    for repo, g in pres.groupby("repo"):
        pattern = g["pattern"].iloc[0]
        row = {"repo": repo, "pattern": pattern, "n_pres_classes": len(g)}
        for col in ("CBO", "LCOM", "WMC", "SLOC", "NOM"):
            vals = g[col].dropna()
            row[col] = round(vals.median(), 3) if len(vals) else math.nan
        agg_rows.append(row)
    aggdf = pd.DataFrame(agg_rows)
    # attach compose flag from corpus
    comp = {r["repo"]: r.get("uses_compose") for r in included}
    aggdf["uses_compose"] = aggdf["repo"].map(comp)
    aggdf.to_csv(DATA / "metrics_per_repo.csv", index=False)
    print(f"Wrote {len(aggdf)} repos to metrics_per_repo.csv")
    print(aggdf.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
