"""Tests for the deterministic governance stages: approval gate (06) and policy layer (07)."""
import importlib
from pathlib import Path

import pytest

from src.models.case import PatientCase
from src.models.messages import Stage
from src.models.plan import ApprovalDecisions, Recommendation, RecommendationSet, check_governance
from src.models.tools import ToolCall, check_policy
from src.pipeline._common import RunContext
from src.tools import forbidden_tools
from src.tools.allowed_tools import ALLOWED_TOOLS

approval_gate = importlib.import_module("src.pipeline.06_approval_gate")
policy_layer = importlib.import_module("src.pipeline.07_policy_layer")

ROOT = Path(__file__).resolve().parent.parent
CASE = PatientCase.model_validate_json((ROOT / "data" / "synthetic_cases" / "CASE-100.json").read_text())


def _call(name):
    return ToolCall(tool_name=name, arguments={"reason": "test"}, requested_by=Stage.REC_GENERATOR)


def _rec(action=None, tools=(), task=("T-RN01", "B-E01")):
    """A complete_task recommendation; the default task (medication education) needs no sign-off."""
    return Recommendation(
        recommendation_id="R1", rank=1, kind="complete_task", description="x", urgency="high",
        owner_role="physician", task_id=task[0], barrier_ids=[task[1]],
        proposed_action=action, proposed_tool_calls=[_call(t) for t in tools],
    )


# ---------------------------------------------------------------- 07 policy layer
def test_taxonomy_tools_all_have_implementations(taxonomy):
    gov = taxonomy["governance"]
    assert {t["tool_name"] for t in gov["allowed_tools"]} == set(ALLOWED_TOOLS)
    assert {t["tool_name"] for t in gov["forbidden_tools"]} == set(forbidden_tools.FORBIDDEN_TOOLS)


def test_every_listed_tool_gets_the_taxonomy_decision(taxonomy):
    gov = taxonomy["governance"]
    names = [t["tool_name"] for t in gov["allowed_tools"] + gov["forbidden_tools"]] + ["mystery_tool", "drop_table"]
    ctx = RunContext("run", CASE, taxonomy)
    out = policy_layer.review(ctx, [_call(n) for n in names])
    assert check_policy(taxonomy, out) == []
    allowed = {t["tool_name"] for t in gov["allowed_tools"]}
    assert {d.tool_name for d in out.decisions if d.decision == "allowed"} == allowed
    assert {r.call_id for r in out.results} == {d.call_id for d in out.decisions if d.decision == "allowed"}
    assert all(r.error is None for r in out.results)


def test_forbidden_tools_are_never_executed(taxonomy, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("forbidden tool executed")
    for name in forbidden_tools.FORBIDDEN_TOOLS:
        monkeypatch.setitem(forbidden_tools.FORBIDDEN_TOOLS, name, boom)
    ctx = RunContext("run", CASE, taxonomy)
    out = policy_layer.review(ctx, [_call(n) for n in forbidden_tools.FORBIDDEN_TOOLS])
    assert all(d.decision == "denied" and d.rule_id.startswith("GF-T") for d in out.decisions)
    assert out.results == []


def test_forbidden_stub_refuses_to_run():
    with pytest.raises(forbidden_tools.ForbiddenToolInvoked):
        forbidden_tools.FORBIDDEN_TOOLS["send_prescription"](CASE, {})


def test_mock_tools_read_the_chart(taxonomy):
    out = ALLOWED_TOOLS["get_transport_status"](CASE, {})
    assert out["transport_status"]["status"] == "complete"
    assert out["planned_discharge_window"]["start"].startswith("2026-04-01T09:00")
    assert ALLOWED_TOOLS["get_caregiver_info"](CASE, {}) == {"caregiver_status": {"status": "unknown"}}
    draft = ALLOWED_TOOLS["draft_patient_education"](CASE, {"topic": "aspirin dose"})
    assert draft["status"] == "draft_for_review"


def test_a_failing_tool_returns_an_error_result(taxonomy, monkeypatch):
    def broken(case, args):
        raise ValueError("backend down")
    monkeypatch.setitem(ALLOWED_TOOLS, "get_pending_labs", broken)
    out = policy_layer.review(RunContext("run", CASE, taxonomy), [_call("get_pending_labs")])
    assert out.decisions[0].decision == "allowed"
    assert "backend down" in out.results[0].error


# ---------------------------------------------------------------- 06 approval gate
MED_CHANGE = ("T-MD07", "B-E07")


@pytest.mark.parametrize("action, tools, expected, rule", [
    (None, (), "auto_safe", None),
    (None, ("get_medication_list",), "auto_safe", None),
    ("communicate_medication_change_to_patient_or_caregiver", (), "requires_human_approval", "GR-A04"),
    ("escalate_to_attending_physician", ("flag_for_review",), "requires_human_approval", "GR-A07"),
    # Proposing modify_medication_list for a clinician needs approval; calling the tool is forbidden.
    ("modify_medication_list", (), "requires_human_approval", "GR-A02"),
    (None, ("modify_medication_list",), "forbidden", "GF-T02"),
    ("modify_medication_list", ("send_prescription",), "forbidden", "GF-T03"),
    ("place_discharge_order", (), "forbidden", "GF-T01"),
    (None, ("sign_discharge_summary", "place_discharge_order"), "forbidden", "GF-T01"),
])
def test_classification(taxonomy, action, tools, expected, rule):
    rec = _rec(action, tools)
    c = approval_gate.classify(taxonomy, rec)
    assert (c.classification, c.matched_rule_id) == (expected, rule)
    assert not c.label_missing
    assert c.requires_human_approval == (expected == "requires_human_approval")
    recs = RecommendationSet(recommendations=[rec])
    assert check_governance(taxonomy, recs, ApprovalDecisions(classifications=[c])) == []


@pytest.mark.parametrize("action, tools, expected, rule, label_missing", [
    # The model forgot the label: the task still needs GR-A04, and the omission is recorded.
    (None, (), "requires_human_approval", "GR-A04", True),
    (None, ("get_medication_list",), "requires_human_approval", "GR-A04", True),
    ("communicate_medication_change_to_patient_or_caregiver", (), "requires_human_approval", "GR-A04", False),
    # A different sign-off action still gates the step, under the rule the model named.
    ("escalate_to_attending_physician", (), "requires_human_approval", "GR-A07", False),
    # A forbidden tool still wins over the task's approval.
    (None, ("send_prescription",), "forbidden", "GF-T03", False),
])
def test_classification_of_approval_gated_task(taxonomy, action, tools, expected, rule, label_missing):
    rec = _rec(action, tools, task=MED_CHANGE)
    c = approval_gate.classify(taxonomy, rec)
    assert (c.classification, c.matched_rule_id, c.label_missing) == (expected, rule, label_missing)
    recs = RecommendationSet(recommendations=[rec])
    assert check_governance(taxonomy, recs, ApprovalDecisions(classifications=[c])) == []


def test_every_gated_task_requires_approval_without_a_label(taxonomy):
    gr = {a["id"] for a in taxonomy["governance"]["approval_required_actions"]}
    tasks = {t["id"]: t for t in taxonomy["tasks"]}
    assert set(taxonomy["governance"]["task_approvals"].values()) <= gr
    for task_id, rule in taxonomy["governance"]["task_approvals"].items():
        c = approval_gate.classify(taxonomy, _rec(task=(task_id, tasks[task_id]["resolves"][0])))
        assert c.requires_human_approval and c.matched_rule_id == rule and c.label_missing


def test_every_approval_action_requires_approval(taxonomy):
    for a in taxonomy["governance"]["approval_required_actions"]:
        c = approval_gate.classify(taxonomy, _rec(a["action"]))
        assert c.requires_human_approval and c.matched_rule_id == a["id"]


def test_task_prompt_text_names_no_sign_off():
    """Prompts must not say which tasks need sign-off: approval_label_omissions measures whether models infer it."""
    from src.pipeline._common import load_taxonomy, task_reference

    assert "GR-A" not in task_reference(load_taxonomy())
