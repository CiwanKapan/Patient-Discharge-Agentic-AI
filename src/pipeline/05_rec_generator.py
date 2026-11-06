"""Stage 05 - Recommendation Generator (LLM).

Turns tasks and information gaps into ranked next steps. The model may attach a
clinical action that needs sign-off (``proposed_action``) and read-only tool
calls; it never runs anything itself. Every proposed tool call, including one
the model should not have proposed, goes to the approval gate and the policy
layer, which classify and deny it there so the attempt is measured and logged.

Rejected here: recommendations that point at tasks or gaps the earlier stages
did not report, tasks or gaps left without a recommendation or given more than
one, and action names outside the governance vocabulary.
"""
from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.models.messages import AgentMessage, BarrierDetection, OwnerRole, Stage, TaskDetection, Urgency
from src.models.plan import Recommendation, RecommendationSet
from src.models.tools import ToolCall
from src.pipeline._common import (
    RECOMMENDATION_OUTPUT_RULES,
    Rejected,
    RunContext,
    call_llm,
    case_view,
    governance_reference,
    make_message,
)

STAGE = Stage.REC_GENERATOR


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedToolCall(_Strict):
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    reason: str = Field(description="Why this call helps; passed to the tool as its argument.")


class DraftRecommendation(_Strict):
    """LLM-facing form of ``Recommendation``. Tool calls get their IDs and provenance in code."""

    recommendation_id: str
    rank: int
    kind: Literal["complete_task", "verify_information"]
    description: str
    urgency: Urgency
    owner_role: OwnerRole
    task_id: Optional[str] = None
    barrier_ids: list[str] = Field(default_factory=list)
    information_gap_field: Optional[str] = None
    proposed_action: Optional[str] = None
    proposed_tool_calls: list[ProposedToolCall] = Field(default_factory=list)


class DraftRecommendationSet(_Strict):
    recommendations: list[DraftRecommendation]


def system_prompt(taxonomy: dict) -> str:
    return f"""You are the recommendation generator in a supervised hospital discharge-coordination \
pipeline. You turn the pending tasks and information gaps for a synthetic patient case into a ranked \
list of next steps for the care team. You only propose; clinicians decide and act.

## Governance
{governance_reference(taxonomy)}

## Output rules
{RECOMMENDATION_OUTPUT_RULES}"""


def run(
    ctx: RunContext,
    barriers: AgentMessage[BarrierDetection],
    tasks: AgentMessage[TaskDetection],
) -> AgentMessage[RecommendationSet]:
    gov = ctx.taxonomy["governance"]
    vocabulary = {a["action"] for a in gov["approval_required_actions"]} | {t["tool_name"] for t in gov["forbidden_tools"]}
    task_resolves = {t.task_id: set(t.resolves) for t in tasks.payload.tasks}
    gap_fields = {g.field for g in barriers.payload.information_gaps}

    upstream = [barriers, tasks]
    if not task_resolves and not gap_fields:
        return make_message(ctx, STAGE, RecommendationSet(), upstream, ["no tasks or information gaps; nothing to recommend"])

    user = (
        f"Condition: {ctx.case.condition}\n\n"
        f"## Patient case\n{case_view(ctx.case)}\n\n"
        f"## Flagged barriers\n{barriers.payload.model_dump_json(include={'barriers'}, indent=1)}\n\n"
        f"## Information gaps\n{barriers.payload.model_dump_json(include={'information_gaps'}, indent=1)}\n\n"
        f"## Pending tasks\n{tasks.payload.model_dump_json(indent=1)}"
    )

    def accept(draft: DraftRecommendationSet):
        errors = []
        recs = []
        for d in draft.recommendations:
            where = d.recommendation_id
            if d.proposed_action is not None and d.proposed_action not in vocabulary:
                errors.append(f"{where}: proposed_action {d.proposed_action!r} is not a governance action")
            if d.kind == "complete_task":
                if d.task_id not in task_resolves:
                    errors.append(f"{where}: task {d.task_id} was not detected")
                elif not set(d.barrier_ids) <= task_resolves[d.task_id]:
                    errors.append(f"{where}: {d.task_id} does not resolve {sorted(set(d.barrier_ids) - task_resolves[d.task_id])}")
            elif d.information_gap_field not in gap_fields:
                errors.append(f"{where}: {d.information_gap_field} is not a reported information gap")
            calls = [
                ToolCall(tool_name=c.tool_name, arguments={"reason": c.reason}, requested_by=STAGE)
                for c in d.proposed_tool_calls
            ]
            try:
                recs.append(Recommendation(**d.model_dump(exclude={"proposed_tool_calls"}), proposed_tool_calls=calls))
            except ValueError as e:
                errors.append(f"{where}: {e}")
        # a recommendation missing its ID is already an error above; counting None would break the sorts below
        task_recs = Counter(d.task_id for d in draft.recommendations if d.kind == "complete_task" and d.task_id)
        gap_recs = Counter(d.information_gap_field for d in draft.recommendations
                           if d.kind == "verify_information" and d.information_gap_field)
        errors += [f"task {t} has no recommendation" for t in sorted(set(task_resolves) - set(task_recs))]
        errors += [f"information gap {g} has no recommendation" for g in sorted(gap_fields - set(gap_recs))]
        errors += [f"task {t} has {n} recommendations; write exactly one" for t, n in sorted(task_recs.items()) if n > 1]
        errors += [f"information gap {g} has {n} recommendations; write exactly one"
                   for g, n in sorted(gap_recs.items()) if n > 1]
        if errors:
            raise Rejected(errors)
        try:
            return RecommendationSet(recommendations=recs), []
        except ValueError as e:
            raise Rejected([str(e)]) from e

    payload, warnings = call_llm(ctx, STAGE, system_prompt(ctx.taxonomy), user, DraftRecommendationSet, accept)
    return make_message(ctx, STAGE, payload, upstream, warnings, llm=True)
