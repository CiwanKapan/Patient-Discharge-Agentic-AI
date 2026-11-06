"""How the pipeline handles model responses that are incomplete rather than malformed.

Each test starts from the oracle (a correct answer built from the answer key) and
removes or bends one part of it, the way a real model might: a recommendation
without its sign-off label, a barrier with no task, a task listed twice, a task
given to the wrong role. The tests pin down what the pipeline does: gate the
step anyway and record the omission, report the gap in the plan, or reject the
response and ask again.
"""
import json
from pathlib import Path

import pytest

from evaluation.metrics import score_case
from src.models.answer_key import AnswerKey
from src.models.case import PatientCase
from src.models.messages import BarrierDetection, Stage, TaskDetection
from src.models.plan import DischargePlan, task_approval
from src.pipeline import run_case
from src.utils.audit_logger import read_audit_log

from oracle_llm import OracleLLM, ScriptedLLM

ROOT = Path(__file__).resolve().parent.parent
CASES = sorted((ROOT / "data" / "synthetic_cases").glob("*.json"))


def _load(path):
    case = PatientCase.model_validate_json(path.read_text())
    key = AnswerKey.model_validate_json((ROOT / "data" / "answer_keys" / f"{case.case_id}.key.json").read_text())
    return case, key


def _score(key, plan, taxonomy):
    return score_case(key, BarrierDetection(barriers=plan.barriers, information_gaps=plan.information_gaps),
                      TaskDetection(tasks=plan.tasks), actions=plan.actions, taxonomy=taxonomy).summary()


def _gated(key, taxonomy):
    return {t.task_id for t in key.tasks if task_approval(taxonomy, t.task_id)}


def _warnings(run, stage):
    return next(m for m in run.messages if m.stage is stage).warnings


def _renumber(recs):
    for i, r in enumerate(recs, 1):
        r["recommendation_id"] = f"R{i}"
    return recs


def _cases_with_gated_tasks(taxonomy_path=ROOT / "config" / "taxonomy.json"):
    gated = json.loads(taxonomy_path.read_text())["governance"]["task_approvals"]
    return [p for p in CASES if any(t.task_id in gated for t in _load(p)[1].tasks)]


GATED_CASES = _cases_with_gated_tasks()


# ---------------------------------------------------------------- missing approval label
@pytest.mark.parametrize("path", GATED_CASES[::8], ids=lambda p: p.stem)
def test_missing_action_label_is_still_gated(path, taxonomy, tmp_path):
    """The model drops every proposed_action. Each step whose task needs sign-off is
    still classified requires_human_approval, and the omission is counted, not hidden."""
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    recs = oracle._rec_generator()
    for r in recs["recommendations"]:
        r["proposed_action"] = None
    r = run_case(path, ScriptedLLM(oracle, {Stage.REC_GENERATOR: [recs]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert r.audit_errors == []

    gated = _gated(key, taxonomy)
    for a in r.plan.actions:
        needed = task_approval(taxonomy, a.recommendation.task_id)
        if needed:
            assert a.approval.classification == "requires_human_approval"
            assert a.approval.matched_rule_id == needed["id"] and a.approval.label_missing
        else:
            assert not a.approval.requires_human_approval

    m = _score(key, r.plan, taxonomy)
    assert m["approval_recall"] == m["approval_precision"] == 1.0
    assert m["approval_label_omissions"] == len(gated)

    logged = [e.details for e in read_audit_log(r.audit_path) if e.event_type == "approval_classification"]
    assert sum(d.label_missing for d in logged) == len(gated)


def test_labelled_run_records_no_omissions(taxonomy, tmp_path):
    path = GATED_CASES[0]
    case, key = _load(path)
    r = run_case(path, OracleLLM(case, key, taxonomy), taxonomy=taxonomy, audit_dir=tmp_path)
    m = _score(key, r.plan, taxonomy)
    assert m["approval_recall"] == 1.0 and m["approval_label_omissions"] == 0


def test_medication_change_without_label_is_gated(taxonomy, tmp_path):
    """The case from the question: a medication-change step with no action label."""
    path = next(p for p in CASES if any(t.task_id == "T-MD07" for t in _load(p)[1].tasks))
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    recs = oracle._rec_generator()
    med = next(x for x in recs["recommendations"] if x.get("task_id") == "T-MD07")
    med["proposed_action"] = None
    r = run_case(path, ScriptedLLM(oracle, {Stage.REC_GENERATOR: [recs]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    a = next(a for a in r.plan.actions if a.recommendation.task_id == "T-MD07")
    assert a.approval.requires_human_approval and a.approval.matched_rule_id == "GR-A04"
    assert a.approval.label_missing
    assert "did not name the action" in a.approval.reason


# ---------------------------------------------------------------- barrier without a task
def test_barrier_left_without_a_task_is_reported(taxonomy, tmp_path):
    """The model flags a barrier but proposes no task for it. The run still succeeds;
    the barrier is listed in unaddressed_barriers, warned about, and costs task recall."""
    path = next(p for p in CASES if len(_load(p)[1].tasks) >= 2)
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    tasks = oracle._task_detector()
    dropped = tasks["tasks"].pop(0)
    recs = oracle._rec_generator()
    recs["recommendations"] = _renumber([x for x in recs["recommendations"] if x.get("task_id") != dropped["task_id"]])
    llm = ScriptedLLM(oracle, {Stage.TASK_DETECTOR: [tasks], Stage.REC_GENERATOR: [recs]})
    r = run_case(path, llm, taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert r.audit_errors == []

    resolved = {b for t in key.tasks for b in t.resolves if t.task_id != dropped["task_id"]}
    assert r.plan.unaddressed_barriers == [b.barrier_id for b in key.barriers if b.barrier_id not in resolved]
    assert set(dropped["resolves"]) <= set(r.plan.unaddressed_barriers)
    warnings = _warnings(r, Stage.TASK_DETECTOR)
    assert any(dropped["resolves"][0] in w and "candidates" in w and dropped["task_id"] in w for w in warnings)
    assert _score(key, r.plan, taxonomy)["task_recall"] < 1.0


def test_no_tasks_at_all(taxonomy, tmp_path):
    """The model returns an empty task list. Every barrier is unaddressed; only
    information gaps get recommendations."""
    path = next(p for p in CASES if _load(p)[1].tasks and _load(p)[1].information_gaps)
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    recs = oracle._rec_generator()
    recs["recommendations"] = _renumber([x for x in recs["recommendations"] if x["kind"] == "verify_information"])
    for x in recs["recommendations"]:
        x["rank"] = 1
    llm = ScriptedLLM(oracle, {Stage.TASK_DETECTOR: [{"tasks": []}], Stage.REC_GENERATOR: [recs]})
    r = run_case(path, llm, taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert r.plan.tasks == []
    assert r.plan.unaddressed_barriers == [b.barrier_id for b in key.barriers]
    assert all(a.recommendation.kind == "verify_information" for a in r.plan.actions)
    assert len(_warnings(r, Stage.TASK_DETECTOR)) == len(key.barriers)


def test_every_barrier_has_a_task_for_each_of_its_conditions(taxonomy):
    for b in taxonomy["barriers"]:
        for cond in b["conditions"]:
            assert any(b["id"] in t["resolves"] and cond in t["conditions"] for t in taxonomy["tasks"]), (b["id"], cond)


def test_correct_run_leaves_nothing_unaddressed(taxonomy, tmp_path):
    path = next(p for p in CASES if any(b.barrier_id == "B-S05" for b in _load(p)[1].barriers))
    case, key = _load(path)
    r = run_case(path, OracleLLM(case, key, taxonomy), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert r.plan.unaddressed_barriers == []
    assert "T-CM09" in {t.task_id for t in r.plan.tasks}


def test_barrier_the_taxonomy_has_no_task_for(taxonomy, tmp_path):
    """If the taxonomy had no task for a barrier, a correct run would still leave it unaddressed and say why."""
    path = next(p for p in CASES if any(b.barrier_id == "B-S05" for b in _load(p)[1].barriers))
    case, key = _load(path)
    reduced = {**taxonomy, "tasks": [t for t in taxonomy["tasks"] if t["id"] != "T-CM09"]}
    key = key.model_copy(update={"tasks": [t for t in key.tasks if t.task_id != "T-CM09"]})
    r = run_case(path, OracleLLM(case, key, reduced), taxonomy=reduced, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert "B-S05" in r.plan.unaddressed_barriers
    assert any(w.startswith("B-S05 has no task; the taxonomy has none") for w in _warnings(r, Stage.TASK_DETECTOR))


def test_plan_derives_unaddressed_barriers(taxonomy, tmp_path):
    case, key = _load(CASES[0])
    r = run_case(CASES[0], OracleLLM(case, key, taxonomy), taxonomy=taxonomy, audit_dir=tmp_path)
    data = r.plan.model_dump()
    data["unaddressed_barriers"] = ["B-Z99"]
    assert DischargePlan.model_validate(data).unaddressed_barriers == r.plan.unaddressed_barriers


# ---------------------------------------------------------------- other incomplete or bent responses
def _duplicate_first_task(oracle):
    recs = oracle._rec_generator()
    first = next(x for x in recs["recommendations"] if x["kind"] == "complete_task")
    recs["recommendations"].insert(1, {**first, "proposed_action": None})
    _renumber(recs["recommendations"])
    return recs


def test_duplicate_task_recommendation_is_rejected(taxonomy, tmp_path):
    """Two recommendations for one task (here, one without its label) are rejected
    rather than gated separately."""
    path = GATED_CASES[0]
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    bad = _duplicate_first_task(oracle)
    r = run_case(path, ScriptedLLM(oracle, {Stage.REC_GENERATOR: [bad, bad]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "failed"
    assert "write exactly one" in r.error


def test_duplicate_task_recommendation_is_retried(taxonomy, tmp_path):
    path = GATED_CASES[0]
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    r = run_case(path, ScriptedLLM(oracle, {Stage.REC_GENERATOR: [_duplicate_first_task(oracle)]}),
                 taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert any("attempt 1 rejected" in w and "write exactly one" in w for w in _warnings(r, Stage.REC_GENERATOR))


def test_task_with_wrong_owner_role_is_rejected_then_retried(taxonomy, tmp_path):
    path = next(p for p in CASES if _load(p)[1].tasks)
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    bad = oracle._task_detector()
    owners = next(t for t in taxonomy["tasks"] if t["id"] == bad["tasks"][0]["task_id"])["owner_roles"]
    bad["tasks"][0]["owner_role"] = next(r for r in taxonomy["owner_roles"] if r not in owners)
    r = run_case(path, ScriptedLLM(oracle, {Stage.TASK_DETECTOR: [bad]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert any("attempt 1 rejected" in w and "owner" in w for w in _warnings(r, Stage.TASK_DETECTOR))


def test_task_citing_the_wrong_field_status_fails(taxonomy, tmp_path):
    path = next(p for p in CASES
                if any(t.resolves for t in _load(p)[1].tasks)
                and any(b.evidence_fields[0].startswith("tracked_fields.") for b in _load(p)[1].barriers
                        if b.barrier_id == _load(p)[1].tasks[0].resolves[0]))
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    bad = oracle._task_detector()
    ev = bad["tasks"][0]["evidence"][0]
    ev["status"] = "complete" if ev["status"] != "complete" else "not_started"
    r = run_case(path, ScriptedLLM(oracle, {Stage.TASK_DETECTOR: [bad, bad]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "failed"
    assert "04_task_detector" in r.error and "chart says" in r.error


def test_task_for_a_barrier_that_was_not_flagged_fails(taxonomy, tmp_path):
    path = next(p for p in CASES if len(_load(p)[1].barriers) >= 2 and _load(p)[1].tasks)
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    barriers = oracle._barrier_detector()
    task = oracle._task_detector()["tasks"][0]
    barriers["barriers"] = [b for b in barriers["barriers"] if b["barrier_id"] not in task["resolves"]]
    llm = ScriptedLLM(oracle, {Stage.BARRIER_DETECTOR: [barriers], Stage.TASK_DETECTOR: [{"tasks": [task]}] * 2})
    r = run_case(path, llm, taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "failed"
    assert "which was not flagged" in r.error


def _with_missing_id(oracle, kind, field):
    """The oracle's recommendations plus one that has no task ID or gap field."""
    recs = oracle._rec_generator()
    first = recs["recommendations"][0]
    recs["recommendations"].insert(1, {**first, "kind": kind, "task_id": None, "information_gap_field": None, field: None})
    _renumber(recs["recommendations"])
    return recs


@pytest.mark.parametrize("kind, field, message", [
    ("complete_task", "task_id", "was not detected"),
    ("verify_information", "information_gap_field", "is not a reported information gap"),
])
def test_recommendation_missing_its_id_is_retried(taxonomy, tmp_path, kind, field, message):
    """A missing ID is rejected for retry; it must not crash the run (the 2026-11-05 CASE-015 failure)."""
    path = GATED_CASES[0]
    case, key = _load(path)
    oracle = OracleLLM(case, key, taxonomy)
    bad = _with_missing_id(oracle, kind, field)
    r = run_case(path, ScriptedLLM(oracle, {Stage.REC_GENERATOR: [bad]}), taxonomy=taxonomy, audit_dir=tmp_path)
    assert r.status == "succeeded", r.error
    assert any("attempt 1 rejected" in w and message in w for w in _warnings(r, Stage.REC_GENERATOR))
