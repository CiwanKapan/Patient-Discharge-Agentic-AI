"""The independent key check: subset selection, blinding, and agreement statistics."""
import csv
import json

import pytest

from evaluation import key_agreement as ka
from scripts import make_key_review_materials as mk
from src.pipeline._common import load_taxonomy
from src.utils.run_records import case_path, key_path


def _cases(tmp_path, ids):
    p = tmp_path / "cases.txt"
    p.write_text("# test cases\n" + "\n".join(ids) + "\n")
    return str(p)


def _real_cases():
    return ka.read_cases(ka.CASES_FILE)


def _wrong_condition_barrier(cid):
    cond = json.loads(case_path(cid).read_text())["condition"]
    return next(b["id"] for b in load_taxonomy()["barriers"] if cond not in b["conditions"])


def _write(tmp_path, rows):
    p = tmp_path / "annotations.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["annotator", "case_id", "item_type", "item_id", "evidence", "notes"])
        w.writerows(rows)
    return p


def _rows_from_key(who, cid):
    k = json.loads(key_path(cid).read_text())
    return ([[who, cid, "barrier", b["barrier_id"], "", ""] for b in k["barriers"]]
            + [[who, cid, "information_gap", g["candidate_barrier_id"], "", ""] for g in k["information_gaps"]]
            + [[who, cid, "task", t["task_id"], "", ""] for t in k["tasks"]])


def test_kappa_matches_hand_computation():
    # 2x2: both 2, only_first 1, only_second 1, neither 6 -> po 0.8, pe 0.58, kappa 0.5238
    pairs = [(True, True)] * 2 + [(True, False)] + [(False, True)] + [(False, False)] * 6
    r = ka.agreement(pairs)
    assert r["observed"] == pytest.approx(0.8)
    assert r["kappa"] == pytest.approx((0.8 - 0.58) / 0.42)
    assert r["positive_agreement"] == pytest.approx(4 / 6)


def test_annotator_identical_to_the_key_agrees_perfectly(tmp_path, capsys):
    cases = ["CASE-151", "CASE-152", "CASE-154"]
    path = _write(tmp_path, [row for c in cases for row in _rows_from_key("ann1", c)])
    assert ka.main([str(path), "--cases", _cases(tmp_path, cases)]) == 0
    rows = list(csv.DictReader((tmp_path / "agreement.csv").open()))
    barrier = next(r for r in rows if r["item_type"] == "barrier")
    assert float(barrier["kappa"]) == 1.0 and float(barrier["positive_agreement"]) == 1.0
    assert not (tmp_path / "disagreements.csv").exists()


def test_disagreements_are_listed_and_annotator_pairs_compared(tmp_path):
    a1 = _rows_from_key("ann1", "CASE-151")
    a2 = [r for r in _rows_from_key("ann2", "CASE-151") if not (r[2] == "barrier" and r[3] == "B-E07")]
    path = _write(tmp_path, a1 + a2 + [["ann2", "CASE-151", "barrier", "B-S02", "", ""]])
    ka.main([str(path), "--cases", _cases(tmp_path, ["CASE-151"])])
    rows = list(csv.DictReader((tmp_path / "agreement.csv").open()))
    assert {(r["first"], r["second"]) for r in rows} == {("key", "ann1"), ("key", "ann2"), ("ann1", "ann2")}
    dis = list(csv.DictReader((tmp_path / "disagreements.csv").open()))
    assert {(d["second"], d["item_id"]) for d in dis if d["first"] == "key"} == {("ann2", "B-E07"), ("ann2", "B-S02")}


def test_subset_is_stratified_and_covers_the_hard_parts():
    rows = mk.manifest_rows()
    ids = mk.choose_subset(rows)
    assert len(ids) == 20 and len(set(ids)) == 20
    from collections import Counter
    assert Counter(rows[c]["condition"] for c in ids) == {c: 4 for c in mk.CONDS}
    assert Counter(rows[c]["difficulty"] for c in ids) == {"easy": 6, "medium": 10, "hard": 4}
    assert mk.covered(ids, rows)
    assert ids == mk.choose_subset(rows)  # seeded


def test_blinded_page_shows_no_key_or_labels():
    from src.models.case import PatientCase
    from src.utils.run_records import case_path
    case = PatientCase.model_validate_json(case_path("CASE-151").read_text())
    page = mk.render_case(case)
    assert "Answer key" not in page and "Must not flag" not in page
    assert '"difficulty"' not in page and "hard" not in page.split("\n")[0]
    assert "## Tracked fields" in page and "## Free-text notes" in page


# ---------------------------------------------------------------- input checks: nothing is dropped silently

def _stops(tmp_path, rows, cases):
    """Run the script on `rows`; it must stop with a message and write no results."""
    path = _write(tmp_path, rows)
    with pytest.raises(SystemExit) as stop:
        ka.main([str(path), "--cases", _cases(tmp_path, cases)])
    assert "nothing was scored" in str(stop.value)
    assert not (tmp_path / "agreement.csv").exists() and not (tmp_path / "disagreements.csv").exists()
    return str(stop.value)


@pytest.mark.parametrize("label, kind, item", [
    ("typo", "barrier", "B-F3"),
    ("lowercase", "barrier", "b-f03"),
    ("two IDs in one cell", "barrier", "B-C01, B-E01"),
    ("task ID in a barrier row", "barrier", "T-CM02"),
    ("barrier ID in a task row", "task", "B-F03"),
])
def test_an_invalid_id_stops_the_run_and_names_the_row(tmp_path, label, kind, item):
    good = _rows_from_key("ann1", "CASE-151")
    message = _stops(tmp_path, good + [["ann1", "CASE-151", kind, item, "", ""]], ["CASE-151"])
    assert f"row {len(good) + 2}" in message and repr(item) in message, label


def test_a_lowercase_id_gets_a_hint(tmp_path):
    good = _rows_from_key("ann1", "CASE-151")
    assert "did you mean B-F03" in _stops(tmp_path, good + [["ann1", "CASE-151", "barrier", "b-f03", "", ""]], ["CASE-151"])


def test_an_id_from_another_condition_stops_the_run(tmp_path):
    other = _wrong_condition_barrier("CASE-151")
    good = _rows_from_key("ann1", "CASE-151")
    message = _stops(tmp_path, good + [["ann1", "CASE-151", "barrier", other, "", ""]], ["CASE-151"])
    assert f"row {len(good) + 2}" in message and other in message and "condition" in message


def test_every_bad_row_is_listed_at_once(tmp_path):
    rows = [["ann1", "CASE-151", "barrier", "B-F3", "", ""], ["ann1", "CASE-151", "wound", "B-F03", "", ""],
            ["ann1", "CASE-151", "task", "", "", ""], ["", "CASE-151", "barrier", "B-F03", "", ""],
            ["ann1", "CASE-999", "barrier", "B-F03", "", ""]]
    message = _stops(tmp_path, rows, ["CASE-151"])
    assert "5 problem(s)" in message
    for line in range(2, 7):
        assert f"row {line}" in message


def test_a_row_with_only_a_name_is_an_error_not_a_silent_skip(tmp_path):
    good = _rows_from_key("ann1", "CASE-151")
    message = _stops(tmp_path, good + [["ann1", "CASE-152", "", "", "", ""]], ["CASE-151", "CASE-152"])
    assert "item_type" in message and "row" in message


def test_a_case_outside_the_cases_file_is_rejected(tmp_path):
    message = _stops(tmp_path, _rows_from_key("ann1", "CASE-151") + _rows_from_key("ann1", "CASE-152"), ["CASE-151"])
    assert "not one of the 1 cases" in message


def test_an_annotator_who_skips_a_case_stops_the_run(tmp_path):
    rows = _rows_from_key("ann1", "CASE-151") + _rows_from_key("ann1", "CASE-152") + _rows_from_key("ann2", "CASE-151")
    message = _stops(tmp_path, rows, ["CASE-151", "CASE-152"])
    assert "ann2: no rows for 1 of 2 cases (CASE-152)" in message and "ann1:" not in message


def test_a_case_nobody_filled_in_stops_the_run(tmp_path):
    message = _stops(tmp_path, _rows_from_key("ann1", "CASE-151"), ["CASE-151", "CASE-152"])
    assert "ann1: no rows for 1 of 2 cases (CASE-152)" in message


def test_a_none_row_means_reviewed_and_found_nothing(tmp_path):
    cases = ["CASE-151", "CASE-152"]
    rows = (_rows_from_key("ann1", "CASE-151") + _rows_from_key("ann1", "CASE-152")
            + [["ann2", "CASE-151", "none", "", "", "found nothing"]] + _rows_from_key("ann2", "CASE-152"))
    path = _write(tmp_path, rows)
    assert ka.main([str(path), "--cases", _cases(tmp_path, cases)]) == 0
    dis = [d for d in csv.DictReader((tmp_path / "disagreements.csv").open()) if (d["first"], d["second"]) == ("key", "ann2")]
    key = json.loads(key_path("CASE-151").read_text())
    expected = {b["barrier_id"] for b in key["barriers"]}
    assert expected and {d["item_id"] for d in dis if d["item_type"] == "barrier"} == expected
    assert all(d["listed_by_first"] == "True" and d["listed_by_second"] == "False" for d in dis)
    assert all(d["case_id"] == "CASE-151" for d in dis)  # the other case agrees completely


def test_a_none_row_beside_items_is_an_error(tmp_path):
    rows = _rows_from_key("ann1", "CASE-151") + [["ann1", "CASE-151", "none", "", "", ""]]
    assert "'none' row and also items" in _stops(tmp_path, rows, ["CASE-151"])


def test_a_none_row_with_an_id_is_an_error(tmp_path):
    assert "'none' needs an empty item_id" in _stops(tmp_path, [["ann1", "CASE-151", "none", "B-F03", "", ""]], ["CASE-151"])


def test_untouched_template_rows_and_item_type_case_are_fine(tmp_path):
    rows = [["", "CASE-151", "", "", "", ""]] + [[r[0], r[1], r[2].upper(), *r[3:]] for r in _rows_from_key("ann1", "CASE-151")]
    assert ka.main([str(_write(tmp_path, rows)), "--cases", _cases(tmp_path, ["CASE-151"])]) == 0


# ---------------------------------------------------------------- the real 20-case check and its materials

def test_the_default_cases_file_is_the_real_20_and_all_must_be_covered(tmp_path):
    cases = _real_cases()
    assert len(cases) == 20
    rows = [r for c in cases[:-1] for r in _rows_from_key("ann1", c)]
    with pytest.raises(SystemExit) as stop:
        ka.main([str(_write(tmp_path, rows))])  # no --cases: the documented command
    assert f"no rows for 1 of 20 cases ({cases[-1]})" in str(stop.value)


def test_an_annotator_identical_to_the_key_passes_on_the_real_20(tmp_path):
    rows = [r for c in _real_cases() for r in _rows_from_key("ann1", c)]
    assert ka.main([str(_write(tmp_path, rows))]) == 0
    barrier = next(r for r in csv.DictReader((tmp_path / "agreement.csv").open()) if r["item_type"] == "barrier")
    assert float(barrier["kappa"]) == 1.0


def test_template_has_no_placeholder_and_one_row_per_case():
    ic = mk.OUT / "independent_check"
    rows = list(csv.DictReader((ic / "annotations_template.csv").open()))
    assert [r["case_id"] for r in rows] == _real_cases()
    assert all(not r["annotator"] and not r["item_type"] and not r["item_id"] for r in rows)
    assert "|" not in (ic / "annotations_template.csv").read_text()


def test_instructions_example_uses_made_up_cases_the_script_rejects(tmp_path):
    ic = mk.OUT / "independent_check"
    assert (ic / "INSTRUCTIONS.md").read_text() == mk.INSTRUCTIONS
    assert "CASE-000" in mk.INSTRUCTIONS and "CASE-999" in mk.INSTRUCTIONS
    assert not {"CASE-000", "CASE-999"} & set(_real_cases())
    assert "not one of the 20 cases" in _stops(tmp_path, [["Sam", "CASE-000", "barrier", "B-F03", "", ""]], _real_cases())
