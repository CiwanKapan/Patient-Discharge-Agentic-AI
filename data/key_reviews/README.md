# Answer-key reviews

All answer-key review happens **before the 160-case evaluation run** (team decision, 2026-10-29). There are
three parts. Do them in this order.

## 1. Independent key check — 20 cases (`independent_check/`, pre-registration Amendment 10)

The methodology requires answer keys authored independently of the cases, with agreement measured. The generated
keys are not independent (one script writes each case and its key from the same recipe), so two annotators write
keys for 20 cases from the case text alone.

- **Who:** two people who did not write the case generator. **They must not see any answer key first**, including
  the `full_review/` pages below, so this check comes before the full review (or uses different people).
  Annotators: Dominic and Julio. Dataset Lead (adjudicates): Ciwan. Give the annotators only the
  `independent_check/` folder (for example as a zip), not a clone of the repo: the keys are in the same repo.
- **Cases:** `independent_check/cases.txt` — 20 evaluation cases, seeded and stratified (4 per condition, 6/10/4
  easy/medium/hard), covering cross-field, note-only, information-gap, near-miss, temptation, and sign-off cases.
- **What they see:** `independent_check/packet/CASE-###.md` (each case exactly as the systems see it: no key, no
  difficulty or distractor labels) and `independent_check/reference.md` (the taxonomy and chart-reading rules the
  systems receive).
- **What they write:** in a copy of `annotations_template.csv`, one row per item: the barriers (with the
  supporting field or note in `evidence`), the information gaps (by the barrier each could indicate), and one task
  per barrier. Work separately. `independent_check/INSTRUCTIONS.md` says how to fill the CSV: one ID per row, and
  one row with `item_type` = `none` for a case where nothing is found, so that is not mistaken for a skipped case.
- **Input checks (added 2026-11-04, before any annotation):** combine the two annotators' files into one CSV with the
  same header, then run the script. It scores nothing until the file is clean: it stops, naming each row, on an ID that
  is not valid for the case's condition (a typo, lowercase, two IDs in one cell), a missing annotator name or unknown
  `item_type`, a case outside `cases.txt`, or a case an annotator has no row for. These checks only validate the input;
  the kappa, the 0.80 bar, the keys, and the cases are unchanged, so no amendment is needed.
- **Measure:** `python -m evaluation.key_agreement <annotations.csv>` — Cohen's kappa and positive agreement for
  barriers, information gaps, and tasks: each annotator against the generated keys, and annotator against
  annotator. `disagreements.csv` lists every item to adjudicate.
- **Decision rule (confirmed 2026-10-29, before any annotation; pre-registration Amendment 10):** barrier kappa
  against the generated keys of at least 0.80 for each annotator separately. Information-gap and task kappa, and
  annotator-vs-annotator agreement, are reported but do not gate. Every disagreement is adjudicated by the two annotators and the Dataset Lead.
  A disagreement that reveals a systematic generator error (as the prior-authorization one did) is fixed in the
  generator, the keys are regenerated (case files unchanged), and the change gets a review sheet below. If kappa
  is below 0.80, the full review of all 160 keys looks specifically for the error patterns found here.

## 2. Change review sheets (decide each row)

| Sheet | Change | Cases | Status |
|---|---|---|---|
| `2026-10-29_b-e02_prior_authorization.csv` | B-E02 (medication not available): when the chart says the drug needs prior authorization, the key's task is T-PH03 (obtain prior authorization) instead of T-PH02 (fill discharge prescriptions). T-PH03 needs no sign-off, so GR-A03 is no longer expected. | 9 evaluation keys, 1 development key | **Reviewed 2026-10-30 (Reviewer C): all 10 rows accepted** |

A rejected row is reverted for that case before the run. The reviewer must not be the case generator's author.

## 3. Full review of all 160 keys (`full_review/`)

`full_review/CASE-###.md` shows each case with its answer key; `full_review/review_sheet.csv` has one row per case
for the reviewer's name, decision (accept / change), the change needed, and the date. About 5 minutes per key.
Changes go through a dated review sheet in this folder, are applied by regenerating or editing the key (never the
case), and are recorded in the pre-registration's amendment log before the run.

Regenerate all materials with `python scripts/make_key_review_materials.py` (no model calls).

How the change for sheet 2 was made: `scripts/generate_cases.py` (`CaseBuilder.task_choices`) picks the B-E02 task
from the barrier's own evidence text. It reads the finished case only, so no case file changed.
