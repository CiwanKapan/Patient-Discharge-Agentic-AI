# Independent key check: how to fill in your answers

Work alone. Open only this folder: `packet/`, `reference.md` and `annotations_template.csv`. Do not open
`data/answer_keys/` or `data/key_reviews/full_review/`, which show the answers.

1. Copy `annotations_template.csv` to `annotations_<your name>.csv`.
2. For each case, read the packet, apply the rules in `reference.md`, and write one row per item. Put your name in the
   `annotator` column of every row you use.
3. **One ID per row.** If a case needs more rows, copy that case's row as many times as you need. Never put two IDs
   in one cell.
   - `item_type` is `barrier`, `information_gap` or `task`. Write the type in full.
   - `item_id` is a barrier ID (B-...) for a barrier or an information gap (the barrier the gap could indicate),
     and a task ID (T-...) for a task. Write it exactly as it appears in `reference.md`.
   - `evidence` is the field or note that supports it. One task per barrier.
4. **If you find nothing in a case**, write one row for it with `item_type` = `none` and an empty `item_id`.
5. Every one of the 20 cases needs at least one row from you. A case with no rows is an error, not "nothing found".
   Template rows you never touch (no name) are ignored.

When the scoring script finds a problem, it stops and names the row to fix. It never skips a row.

## Example (made-up case, not one of the 20; do not copy it into your file)

| annotator | case_id | item_type | item_id | evidence | notes |
|---|---|---|---|---|---|
| Sam | CASE-000 | barrier | B-F03 | caregiver_status: not_started | |
| Sam | CASE-000 | task | T-CM02 | for B-F03 | |
| Sam | CASE-000 | information_gap | B-S07 | transport booked, no pickup time | |
| Sam | CASE-999 | none | | | reviewed; nothing found |
