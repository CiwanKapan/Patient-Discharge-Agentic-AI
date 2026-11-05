"""Input/output contract tests for the case model and inter-agent messages."""
import pytest
from pydantic import ValidationError

from scripts.export_schemas import ROOT, SCHEMAS, render
from src.models.case import TRACKED_FIELD_NAMES, FieldReading, PatientCase, read_field
from src.models.messages import (
    BarrierDetection,
    BarrierDetectionMessage,
    BarrierFinding,
    TaskDetection,
    TaskDetectionMessage,
    check_against_taxonomy,
)

S07 = dict(
    barrier_id="B-S07", urgency="medium", cross_field=True, rationale="pickup after discharge window",
    evidence=[{"field": "tracked_fields.transport_status"}, {"field": "planned_discharge_window"}],
)


@pytest.mark.parametrize("rel", sorted(SCHEMAS))
def test_committed_schemas_match_models(rel):
    assert (ROOT / rel).read_text() == render(SCHEMAS[rel]), "run: python scripts/export_schemas.py"


def test_case_model_covers_every_taxonomy_evidence_field(taxonomy):
    top_level = {"planned_discharge_window"} | {f"free_text_notes.{n}" for n in ("case_manager_note",)}
    for entry in taxonomy["barriers"] + taxonomy["tasks"]:
        for f in entry["evidence_fields"]:
            assert f in TRACKED_FIELD_NAMES or f in top_level, f"{entry['id']} cites unknown field {f}"


@pytest.mark.parametrize(
    "field, reading",
    [
        (None, FieldReading.MISSING),
        ({"status": "unknown"}, FieldReading.MISSING),
        ({"status": "not_started"}, FieldReading.INCOMPLETE),
        ({"status": "in_progress"}, FieldReading.INCOMPLETE),
        ({"status": "complete"}, FieldReading.SATISFIED),
        ({"status": "not_applicable"}, FieldReading.SATISFIED),
    ],
)
def test_missing_is_never_read_as_incomplete(field, reading):
    fields = {} if field is None else {"followup_appointment_status": field}
    case = PatientCase(case_id="t", condition="HF", difficulty="easy", tracked_fields=fields)
    assert read_field(case.field("followup_appointment_status")) is reading


def test_unknown_case_field_is_rejected():
    with pytest.raises(ValidationError):
        PatientCase(case_id="t", condition="HF", difficulty="easy", tracked_fields={"follow_up": {"status": "complete"}})


def test_cross_field_barrier_must_cite_two_fields():
    with pytest.raises(ValidationError):
        BarrierFinding(**{**S07, "evidence": [{"field": "tracked_fields.transport_status"}]})


def test_barrier_and_gap_cannot_overlap():
    with pytest.raises(ValidationError):
        BarrierDetection(
            barriers=[S07],
            information_gaps=[{"field": "tracked_fields.transport_status", "candidate_barrier_id": "B-S07", "question": "?"}],
        )


def test_envelope_rejects_payload_from_wrong_stage():
    with pytest.raises(ValidationError):
        TaskDetectionMessage(run_id="r", case_id="c", stage="03_barrier_detector", payload={})
    msg = BarrierDetectionMessage(run_id="r", case_id="c", stage="03_barrier_detector", payload={"barriers": [S07]})
    assert msg.schema_version == "1.0"


def test_taxonomy_checks(taxonomy):
    barriers = BarrierDetection(barriers=[S07])
    ok = TaskDetection(tasks=[{"task_id": "T-CM04", "resolves": ["B-S07"], "owner_role": "case_manager", "urgency": "medium",
                               "evidence": [{"field": "tracked_fields.transport_status"}], "rationale": "rebook pickup"}])
    assert check_against_taxonomy(taxonomy, "HF", barriers, ok) == []

    bad = TaskDetection(tasks=[{"task_id": "T-CM01", "resolves": ["B-S07"], "owner_role": "nurse", "urgency": "high",
                                "evidence": [{"field": "tracked_fields.transport_status"}], "rationale": "x"}])
    errors = check_against_taxonomy(taxonomy, "HF", barriers, bad)
    assert any("cannot resolve B-S07" in e for e in errors)
    assert any("owner nurse" in e for e in errors)

    wrong_flag = BarrierDetection(barriers=[{**S07, "cross_field": False}])
    assert any("cross_field" in e for e in check_against_taxonomy(taxonomy, "HF", wrong_flag))


def test_taxonomy_counts_match_its_contents(taxonomy):
    """meta.counts is written by scripts/build_config.py; a direct edit to the taxonomy must keep it in step."""
    counts = taxonomy["meta"]["counts"]
    assert counts["barriers"] == len(taxonomy["barriers"])
    assert counts["tasks"] == len(taxonomy["tasks"])
