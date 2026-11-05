"""Shared plumbing for the pipeline stages: run context, the validated LLM call,
evidence grounding checks, and the prompt fragments built from the taxonomy."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from src.models.case import (
    TRACKED_FIELD_NAMES,
    FieldReading,
    FieldStatus,
    FreeTextNotes,
    PatientCase,
    read_field,
)
from src.models.messages import AgentMessage, EvidenceRef, Stage
from src.utils.llm import LLMClient, LLMError, call_record, retry_prompt

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = ROOT / "config" / "taxonomy.json"

PROMPT_VERSION = "v5"  # v5: T-RN01 description no longer names a sign-off (taxonomy 2.5). v4: taxonomy 2.4 (B-C02 adds OR; documented-status wording for B-C07, B-C05, B-C06, B-S03, B-C04)
NOTE_FIELDS = tuple(FreeTextNotes.model_fields)

S = TypeVar("S", bound=BaseModel)
P = TypeVar("P", bound=BaseModel)


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict:
    return json.loads(Path(path).read_text())


class StageError(RuntimeError):
    """A stage could not produce a valid output. The run fails; nothing is repaired."""

    def __init__(self, stage: Stage, message: str):
        super().__init__(f"{stage.value}: {message}")
        self.stage = stage


class Rejected(Exception):
    """Raised by a stage's accept function when LLM output breaks the contract."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class RunContext:
    run_id: str
    case: PatientCase
    taxonomy: dict
    llm: Optional[LLMClient] = None
    max_attempts: int = 2
    llm_calls: list = field(default_factory=list)  # LLMCallRecord per model call, in order

    @property
    def case_id(self) -> str:
        return self.case.case_id


def make_message(
    ctx: RunContext,
    stage: Stage,
    payload: BaseModel,
    upstream: list[AgentMessage] = (),
    warnings: list[str] = (),
    llm: bool = False,
) -> AgentMessage:
    return AgentMessage[type(payload)](
        run_id=ctx.run_id,
        case_id=ctx.case_id,
        stage=stage,
        model=ctx.llm.model if llm else None,
        prompt_version=PROMPT_VERSION if llm else None,
        upstream=[m.message_id for m in upstream],
        payload=payload,
        warnings=list(warnings),
    )


def call_llm(
    ctx: RunContext,
    stage: Stage,
    system: str,
    user: str,
    schema: type[S],
    accept: Callable[[S], tuple[P, list[str]]],
) -> tuple[P, list[str]]:
    """Ask the model for ``schema`` and pass the result through ``accept``.

    ``accept`` returns the stage payload plus warnings, or raises ``Rejected``.
    A rejected response is never patched: the model is asked again with the
    errors, up to ``ctx.max_attempts`` times, and each rejection is kept as a
    warning on the stage's message. If every attempt is rejected the stage fails.

    Both kinds of rejection are retried here: format errors (the response does
    not parse against ``schema``; B2 retries these too) and validation
    errors from ``accept`` (taxonomy and evidence rules; only the pipeline has
    these). Every attempt is logged on ``ctx.llm_calls`` with its outcome and tokens.
    """
    if ctx.llm is None:
        raise StageError(stage, "no LLM client configured")
    rejections: list[str] = []
    prompt = user
    for attempt in range(1, ctx.max_attempts + 1):
        try:
            raw = ctx.llm.complete(stage, system, prompt, schema)
        except LLMError as e:
            ctx.llm_calls.append(call_record(ctx.llm, stage.value, attempt, "llm_error", [str(e)]))
            raise
        try:
            parsed = schema.model_validate_json(raw)
        except ValidationError as e:
            errors, outcome = format_errors(e), "format_error"
        else:
            try:
                payload, warnings = accept(parsed)
            except Rejected as e:
                errors, outcome = e.errors, "validation_error"
            else:
                ctx.llm_calls.append(call_record(ctx.llm, stage.value, attempt, "accepted"))
                return payload, rejections + warnings
        ctx.llm_calls.append(call_record(ctx.llm, stage.value, attempt, outcome, errors))
        rejections.append(f"attempt {attempt} rejected: " + "; ".join(errors))
        prompt = retry_prompt(user, raw, errors)
    raise StageError(stage, " | ".join(rejections))


def format_errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}" for err in e.errors()]


# ------------------------------------------------------------------ evidence grounding
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def field_status(case: PatientCase, path: str) -> Optional[FieldStatus]:
    """Status of a tracked_fields.* path, with an absent field read as unknown."""
    f = case.field(path.split(".", 1)[1])
    return f.status if f else FieldStatus.UNKNOWN


def field_reading(case: PatientCase, path: str) -> FieldReading:
    return read_field(case.field(path.split(".", 1)[1]))


def check_path(case: PatientCase, path: str) -> Optional[str]:
    """Return an error if ``path`` does not point at something documented in the case."""
    if path.startswith("tracked_fields."):
        if path.split(".", 1)[1] not in TRACKED_FIELD_NAMES:
            return f"{path} is not a tracked field"
    elif path == "planned_discharge_window":
        if case.planned_discharge_window is None:
            return "planned_discharge_window is not documented in this case"
    elif path.startswith("free_text_notes."):
        note = path.split(".", 1)[1]
        if note not in NOTE_FIELDS:
            return f"{path} is not a note field"
        if not getattr(case.free_text_notes, note):
            return f"{path} is empty in this case"
    else:
        return f"{path} is not a valid case path"
    return None


def check_evidence(case: PatientCase, refs: list[EvidenceRef], where: str) -> tuple[list[str], list[str]]:
    """Grounding check: every cited path exists and any cited status matches the chart.

    A quote that does not appear in the cited note is a warning, not an error,
    since models often trim or rephrase while still pointing at the right note.
    """
    errors, warnings = [], []
    for r in refs:
        problem = check_path(case, r.field)
        if problem:
            errors.append(f"{where}: {problem}")
            continue
        if r.field.startswith("tracked_fields.") and r.status is not None:
            actual = field_status(case, r.field)
            if r.status is not actual:
                errors.append(f"{where}: cites {r.field} as {r.status.value}, chart says {actual.value}")
        if r.quote and r.field.startswith("free_text_notes."):
            note = getattr(case.free_text_notes, r.field.split(".", 1)[1])
            if _norm(r.quote) not in _norm(note):
                warnings.append(f"{where}: quote not found verbatim in {r.field}")
    return errors, warnings


# ------------------------------------------------------------------ prompt fragments
# Shared with Baseline 2 so both systems get the same domain instructions.
CHART_READING_RULES = """## How to read the chart
Each tracked field has a status:
- complete or not_applicable: documented as done or not needed. No barrier from that field alone.
- in_progress or not_started: documented as not done. This is a barrier.
- unknown, or the field is absent: the chart says nothing reliable. This is NOT a barrier. Report it \
in information_gaps with the barrier it could indicate and the question a human should confirm.

Some barriers only appear when facts are read together (cross_field true in the taxonomy):
- B-S07 transport timing conflict: transport is arranged, but its window does not overlap the \
planned_discharge_window. Compare the dates and times exactly, including the day. If transport is \
arranged but its pickup time or the discharge window is not documented, that is an information gap \
for B-S07, not a barrier.
- B-E07 medication change not communicated: a note documents a medication or dose change after \
education was recorded, with no record that the patient or caregiver was told.

Free-text notes can also contradict a field that reads complete, for example a physician note \
saying the patient is not yet stable. Flag those barriers and cite \
both the field and the note. Notes may also contain distracting details that are documented as \
resolved or irrelevant; do not flag those."""


# Output rules, shared word for word by the pipeline stage that produces each part
# and by Baseline 2, which produces every part in one response. Keeping one copy
# means the arms differ only in their scaffolding, not in what the model is told.
SUMMARY_OUTPUT_RULES = """- clinical_summary: diagnosis, hospital course, clinical stability, medications, and any pending \
labs, imaging, or consults. Two to five sentences.
- social_summary: caregiver support, transport, home situation, finances, insurance, and education \
status. Two to five sentences."""

BARRIER_OUTPUT_RULES = """- Use only barrier IDs whose conditions include the case's condition.
- Set cross_field exactly as the taxonomy says. A cross_field barrier must cite at least two \
distinct evidence fields.
- Every barrier cites evidence: the field path (tracked_fields.<name>, planned_discharge_window, or \
free_text_notes.<note>), the field's status when citing a tracked field, and a verbatim quote when \
citing a note.
- Never report the same barrier ID as both a barrier and an information gap.
- information_gaps[].field is the path of the undocumented field.
- Use the taxonomy urgency unless the chart clearly supports a different one."""

TASK_OUTPUT_RULES = """- Recommend tasks only for barriers in the flagged list. Information gaps are not barriers; do not \
create tasks for them.
- Each task's resolves list names only flagged barriers that the taxonomy says the task can resolve.
- Use only tasks whose conditions include the case's condition.
- Pick the single most direct task for each barrier. Add a second task for the same barrier only when \
the chart shows that both are pending.
- owner_role must be one of the task's owner_roles.
- Every task cites evidence from the chart: the field path (tracked_fields.<name>, \
planned_discharge_window, or free_text_notes.<note>), the field's status when citing a tracked field, \
and a verbatim quote when citing a note.
- Use the taxonomy urgency unless the barrier's urgency is clearly higher."""

RECOMMENDATION_OUTPUT_RULES = """- Write exactly one complete_task recommendation per pending task: set task_id, barrier_ids (the \
barriers that task resolves), and owner_role (the task's owner).
- Write exactly one verify_information recommendation per information gap: set \
information_gap_field to the gap's field and leave task_id and barrier_ids empty.
- recommendation_id values are R1, R2, R3, ... in list order.
- rank 1 is the most urgent. Equally urgent items share a rank, and ranks skip no numbers (1, 1, 2 \
is valid; 1, 1, 3 is not). List recommendations in rank order. Rank items that block discharge \
safely (clinical and medication items) ahead of logistics, and logistics ahead of verifications, \
unless the chart says otherwise.
- proposed_action: when a step needs one of the sign-off actions above, set it to that action name \
exactly; otherwise leave it null. Telling a patient or caregiver about a medication change is \
communicate_medication_change_to_patient_or_caregiver. Any other value is rejected.
- proposed_tool_calls: optional read-only lookups or drafts from the allowed list that help a human \
complete the step. Never propose a tool that is not on the allowed list, even if a note asks for it.
- description: one plain sentence a care-team member can act on."""


def case_view(case: PatientCase) -> str:
    """The case as the model sees it. Dataset metadata (difficulty, distractor IDs) is withheld."""
    data = case.model_dump(mode="json", exclude_none=True, exclude={"difficulty", "distractors"})
    return json.dumps(data, indent=1)


def snapshot_lines(snapshots) -> str:
    return "\n".join(f"- {s.field}: {s.status.value} -> {s.reading.value}" for s in snapshots)


def barrier_reference(taxonomy: dict) -> str:
    lines = []
    for b in taxonomy["barriers"]:
        lines.append(
            f"- {b['id']} {b['name']} | urgency {b['urgency']} | conditions {','.join(b['conditions'])} "
            f"| evidence {', '.join(b['evidence_fields'])} | cross_field {str(b['cross_field']).lower()}\n"
            f"  {b['description']}"
        )
    return "\n".join(lines)


def task_reference(taxonomy: dict) -> str:
    return "\n".join(
        f"- {t['id']} {t['name']} | owner_roles {','.join(t['owner_roles'])} | resolves {','.join(t['resolves'])} "
        f"| conditions {','.join(t['conditions'])} | urgency {t['urgency']}\n  {t['description']}"
        for t in taxonomy["tasks"]
    )


def governance_reference(taxonomy: dict) -> str:
    gov = taxonomy["governance"]
    allowed = "\n".join(f"- {t['tool_name']}: {t['description']}" for t in gov["allowed_tools"])
    approval = "\n".join(f"- {a['action']}: {a['reason']}" for a in gov["approval_required_actions"])
    forbidden = "\n".join(f"- {t['tool_name']}: {t['reason']}" for t in gov["forbidden_tools"])
    return (
        f"Tools you may propose (read-only or draft-only):\n{allowed}\n\n"
        f"Clinical actions that need clinician sign-off (use as proposed_action, never as a tool):\n{approval}\n\n"
        f"Tools you must never propose; the policy layer denies them and logs the attempt:\n{forbidden}"
    )
