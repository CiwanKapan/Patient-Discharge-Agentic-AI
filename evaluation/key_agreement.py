"""Agreement between independent annotators and the generated answer keys (pre-registration Amendment 10).

    python -m evaluation.key_agreement data/key_reviews/independent_check/annotations.csv

The annotations CSV has one row per annotated item (one ID per row):
    annotator, case_id, item_type (barrier | information_gap | task | none), item_id, evidence, notes

A case where an annotator found nothing gets one row with item_type `none` and an empty item_id, so "reviewed, found
nothing" is not confused with "skipped". Nothing is scored until the input is clean: the run stops, naming each row, on a
missing annotator, a case outside the cases file, an unknown item_type, an empty item_id, an ID that is not valid for the
case's condition (a typo, lowercase, two IDs in one cell), or a `none` row beside items. It also stops if any annotator has
no row for a case in the cases file (`independent_check/cases.txt`, or --cases).

For each item type, every (case, ID) pair the taxonomy allows for the case's condition is a yes/no judgment:
does the annotator (or key) list it? Agreement is computed over those pairs, pooled across the cases:

    kappa           Cohen's kappa
    positive_agreement  2a / (2a + b + c): agreement on the items either side listed (an F1)
    observed        share of all pairs on which they agree (inflated by the many absent IDs; reported only)

Rows: each annotator against the generated key, and each pair of annotators. disagreements.csv lists every
(case, item) the two sides judged differently, for adjudication.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline._common import load_taxonomy  # noqa: E402
from src.utils.run_records import case_path, key_path  # noqa: E402

ITEM_TYPES = ("barrier", "information_gap", "task")
NONE = "none"  # item_type of the row that records "reviewed this case, found nothing"
CASES_FILE = ROOT / "data" / "key_reviews" / "independent_check" / "cases.txt"
MAX_ERRORS_SHOWN = 50


def universe(taxonomy: dict, condition: str, item_type: str) -> set[str]:
    pool = taxonomy["tasks"] if item_type == "task" else taxonomy["barriers"]
    return {x["id"] for x in pool if condition in x["conditions"]}


def key_items(case_id: str) -> dict[str, set[str]]:
    k = json.loads(key_path(case_id).read_text())
    return {"barrier": {b["barrier_id"] for b in k["barriers"]},
            "information_gap": {g["candidate_barrier_id"] for g in k["information_gaps"]},
            "task": {t["task_id"] for t in k["tasks"]}}


def read_cases(path: Path) -> list[str]:
    """The cases every annotator must cover: one ID per line, # comments allowed."""
    cases = [ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not cases:
        raise SystemExit(f"{path}: no case IDs found")
    return sorted(set(cases))


def read_annotations(path: Path, cases: list[str], taxonomy: dict,
                     conditions: dict[str, str]) -> dict[str, dict[str, dict[str, set[str]]]]:
    """annotator -> case_id -> item_type -> IDs, for every annotator and every case in `cases`.

    Stops (SystemExit listing every problem, nothing scored) rather than dropping anything: a row that cannot be
    counted would silently change the kappa that decides whether the keys pass."""
    errors: list[str] = []
    out: dict = defaultdict(lambda: defaultdict(lambda: {t: set() for t in ITEM_TYPES}))
    touched: dict[str, set[str]] = defaultdict(set)   # annotator -> cases with any row at all
    kinds: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # annotator -> case -> valid kinds
    with Path(path).open(newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            who, cid, item = (str(r.get(k) or "").strip() for k in ("annotator", "case_id", "item_id"))
            kind = str(r.get("item_type") or "").strip().lower()
            if not (who or kind or item):
                continue  # a template row nobody touched
            where = f"row {reader.line_num} ({who or 'no annotator'}, {cid or 'no case_id'})"
            if not who:
                errors.append(f"{where}: annotator is empty")
                continue
            if cid not in conditions:
                errors.append(f"{where}: case_id is not one of the {len(cases)} cases in the cases file")
                continue
            touched[who].add(cid)
            if kind == NONE:
                if item:
                    errors.append(f"{where}: item_type 'none' needs an empty item_id (got {item!r})")
                else:
                    kinds[who][cid].add(NONE)
                continue
            if kind not in ITEM_TYPES:
                errors.append(f"{where}: item_type {kind!r} must be one of {', '.join(ITEM_TYPES + (NONE,))}")
                continue
            if not item:
                errors.append(f"{where}: item_id is empty (use item_type 'none' if nothing was found)")
                continue
            valid = universe(taxonomy, conditions[cid], kind)
            if item not in valid:
                hint = f"; did you mean {item.upper()}?" if item.upper() in valid else ""
                errors.append(f"{where}: {item!r} is not a valid {'task' if kind == 'task' else 'barrier'} ID for "
                              f"condition {conditions[cid]} (one ID per row{hint})")
                continue
            out[who][cid][kind].add(item)
            kinds[who][cid].add(kind)
    for who in sorted(touched):
        missing = [c for c in cases if c not in touched[who]]
        if missing:
            errors.append(f"{who}: no rows for {len(missing)} of {len(cases)} cases ({', '.join(missing)}); add rows, "
                          "or a 'none' row for a case where nothing was found")
        errors += [f"{who}, {c}: has a 'none' row and also items; keep one or the other"
                   for c in cases if NONE in kinds[who][c] and len(kinds[who][c]) > 1]
    if errors:
        shown = errors[:MAX_ERRORS_SHOWN]
        more = [f"... and {len(errors) - len(shown)} more"] if len(errors) > len(shown) else []
        raise SystemExit(f"{path}: {len(errors)} problem(s); nothing was scored.\n"
                         + "\n".join(f"  {e}" for e in shown + more))
    for who in touched:  # include annotators who found nothing in some or all cases
        for cid in cases:
            out[who][cid]
    return out


def agreement(pairs: list[tuple[bool, bool]]) -> dict:
    a = sum(x and y for x, y in pairs)
    b = sum(x and not y for x, y in pairs)
    c = sum(y and not x for x, y in pairs)
    d = sum(not x and not y for x, y in pairs)
    n = a + b + c + d
    if not n:
        return {"n": 0, "kappa": None, "positive_agreement": None, "observed": None}
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / n ** 2
    return {"n": n, "both": a, "only_first": b, "only_second": c,
            "kappa": None if pe == 1 else (po - pe) / (1 - pe),
            "positive_agreement": 2 * a / (2 * a + b + c) if a + b + c else None,
            "observed": po}


def compare(first: dict, second: dict, cases: list[str], taxonomy: dict, conditions: dict):
    rows, disagreements = [], []
    for kind in ITEM_TYPES:
        pairs = []
        for cid in cases:
            for item in sorted(universe(taxonomy, conditions[cid], kind)):
                x, y = item in first[cid][kind], item in second[cid][kind]
                pairs.append((x, y))
                if x != y:
                    disagreements.append((cid, kind, item, x, y))
        rows.append({"item_type": kind, **agreement(pairs)})
    return rows, disagreements


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m evaluation.key_agreement", description=__doc__.split("\n")[0])
    p.add_argument("annotations", type=Path)
    p.add_argument("--out", type=Path, help="default: next to the annotations file")
    p.add_argument("--cases", type=Path, default=CASES_FILE,
                   help="cases every annotator must cover (default: independent_check/cases.txt)")
    a = p.parse_args(argv)
    taxonomy = load_taxonomy()
    cases = read_cases(a.cases)
    conditions = {cid: json.loads(case_path(cid).read_text())["condition"] for cid in cases}
    ann = read_annotations(a.annotations, cases, taxonomy, conditions)
    if not ann:
        p.error("no annotations found")
    sources = {"key": {cid: key_items(cid) for cid in cases}, **ann}
    results, disagreements = [], []
    for first, second in [("key", w) for w in ann] + list(itertools.combinations(sorted(ann), 2)):
        rows, dis = compare(sources[first], sources[second], cases, taxonomy, conditions)
        results += [{"first": first, "second": second, **r} for r in rows]
        disagreements += [{"first": first, "second": second, "case_id": c, "item_type": k, "item_id": i,
                           "listed_by_first": x, "listed_by_second": y} for c, k, i, x, y in dis]
    out = a.out or a.annotations.parent
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("agreement.csv", results), ("disagreements.csv", disagreements)):
        if rows:
            with open(out / name, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    print(f"{len(cases)} cases, annotators: {', '.join(sorted(ann))}")
    print(f"{'first':<10}{'second':<12}{'items':<17}{'pairs':>6}{'kappa':>8}{'pos.agr':>9}{'observed':>10}")
    fmt = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
    for r in results:
        print(f"{r['first']:<10}{r['second']:<12}{r['item_type']:<17}{r['n']:>6}{fmt(r['kappa']):>8}"
              f"{fmt(r['positive_agreement']):>9}{fmt(r['observed']):>10}")
    print(f"\nwrote {out / 'agreement.csv'} and {out / 'disagreements.csv'} ({len(disagreements)} disagreements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
