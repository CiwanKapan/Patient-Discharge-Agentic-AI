"""Baseline 2 - single-prompt LLM.

One model call reads the case and returns barriers, information gaps, tasks,
and ranked recommendations, each flagged by the model itself as needing human
approval or not. It is told exactly what the pipeline's stages are told about
the output, and nothing about the scaffolding:

- the same model, settings, taxonomy, chart-reading rules, and governance list
- the same output rules, word for word (SUMMARY_, BARRIER_, TASK_, and
  RECOMMENDATION_OUTPUT_RULES in src/pipeline/_common.py), and the same output
  schema: the pipeline's own item models (BarrierFinding, InformationGap,
  TaskFinding, the recommendation draft) with the same schema-level checks
- the same format retry: a response that does not parse against that schema is
  re-asked exactly as the pipeline re-asks (see baselines/_single_prompt.py)

What B2 lacks is the scaffolding:

- no staged decomposition and no deterministic field readings
- no taxonomy or evidence validation, so nothing is re-asked for content
- no approval gate: the model's own requires_human_approval flag is used as is
  (the one output field the pipeline does not have)
- no policy layer: proposed tool calls are recorded but nothing reviews them,
  so a forbidden one counts as a governance failure
- no audit trail and no sign-off request

To score it with the same tools as the pipeline, the output is normalized into
a ``DischargePlan``. Items the schema allows but the plan cannot represent
(duplicate IDs, a recommendation that breaks its kind's rules) are dropped,
each with a note in ``BaselineResult.notes``. Nothing is corrected.

Model, effort, max_tokens, and attempts come from config/run_config.json.

    python -m baselines.baseline_2_single_llm data/synthetic_cases/ --out runs/b2 [--batch]
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from baselines._common import BaselineResult, load_case, main
from baselines._single_prompt import Answer, ask, ask_batch
from src.models.case import PatientCase
from src.models.messages import BarrierDetection, BarrierFinding, InformationGap, Stage, TaskFinding
from src.models.plan import ApprovalClassification, DischargePlan, PlannedAction, Recommendation, task_approval
from src.models.tools import ToolCall
from src.pipeline._common import (
    BARRIER_OUTPUT_RULES,
    CHART_READING_RULES,
    RECOMMENDATION_OUTPUT_RULES,
    SUMMARY_OUTPUT_RULES,
    TASK_OUTPUT_RULES,
    barrier_reference,
    case_view,
    governance_reference,
    load_taxonomy,
    task_reference,
)
from src.utils.llm import LLMClient
from src.utils.run_config import check_environment, load_run_config

rec_generator = importlib.import_module("src.pipeline.05_rec_generator")

SYSTEM = "baseline_2_single_llm"
PROMPT_VERSION = "v6"  # v6: T-RN01 description no longer names a sign-off (taxonomy 2.5). v5: taxonomy 2.4 (B-C02 adds OR; documented-status barrier wording)


# ------------------------------------------------------------------ LLM-facing output schema
# The pipeline's own output models, so both arms meet the same schema.
class SingleRecommendation(rec_generator.DraftRecommendation):
    requires_human_approval: bool = Field(
        description="B2 only (it has no approval gate): true for any step that needs clinician sign-off.")


class SinglePromptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clinical_summary: str
    social_summary: str
    barriers: list[BarrierFinding]
    information_gaps: list[InformationGap]
    tasks: list[TaskFinding]
    recommendations: list[SingleRecommendation]

    @model_validator(mode="after")
    def _same_checks_as_the_pipeline_schema(self) -> "SinglePromptOutput":
        try:
            BarrierDetection(barriers=self.barriers, information_gaps=self.information_gaps)
        except ValidationError as e:
            raise ValueError(e.errors()[0]["msg"].removeprefix("Value error, ")) from None
        return self


def system_prompt(taxonomy: dict) -> str:
    return f"""You are a hospital discharge coordinator reviewing one synthetic patient case. In a \
single response, find the discharge barriers and information gaps, the pending tasks that resolve the \
barriers, and a ranked list of recommended next steps. You only propose; clinicians decide and act.

## Barrier taxonomy
{barrier_reference(taxonomy)}

## Task taxonomy
{task_reference(taxonomy)}

{CHART_READING_RULES}

## Governance
{governance_reference(taxonomy)}

## Output rules

### Summary
{SUMMARY_OUTPUT_RULES}

### Barriers and information gaps
{BARRIER_OUTPUT_RULES}

### Tasks
{TASK_OUTPUT_RULES}

### Recommendations
{RECOMMENDATION_OUTPUT_RULES}
- requires_human_approval: set to true for any step that needs clinician sign-off."""


# ------------------------------------------------------------------ normalization
def _approval(taxonomy: dict, rec: Recommendation, flagged: bool) -> ApprovalClassification:
    """Record the model's own approval flag, mapped onto the approval-classification contract.

    There is no gate, so a forbidden tool or an unflagged sign-off action is left
    as the model classified it and ``check_governance`` reports the failure. A
    flagged step that names no action but whose task needs approval is recorded
    under the task's rule, with ``label_missing`` set, as the pipeline gate does.
    """
    gr = {a["action"]: a["id"] for a in taxonomy["governance"]["approval_required_actions"]}
    if flagged and rec.proposed_action in gr:
        return ApprovalClassification(
            recommendation_id=rec.recommendation_id, classification="requires_human_approval",
            requires_human_approval=True, matched_rule_id=gr[rec.proposed_action],
            reason="Model flagged this step for human approval.",
        )
    needed = task_approval(taxonomy, rec.task_id)
    if flagged and needed is not None:
        return ApprovalClassification(
            recommendation_id=rec.recommendation_id, classification="requires_human_approval",
            requires_human_approval=True, matched_rule_id=needed["id"], label_missing=True,
            reason=f"Model flagged this step for human approval but did not name {needed['action']}.",
        )
    reason = ("Model flagged approval but named no approval-required action." if flagged
              else "Model did not flag this step for approval.")
    return ApprovalClassification(recommendation_id=rec.recommendation_id, classification="auto_safe",
                                  requires_human_approval=False, reason=reason)


def normalize(taxonomy: dict, case: PatientCase, out: SinglePromptOutput) -> tuple[DischargePlan, list[str]]:
    notes = []

    barriers, seen = [], set()
    for b in out.barriers:
        if b.barrier_id in seen:
            notes.append(f"dropped duplicate barrier {b.barrier_id}")
            continue
        barriers.append(b)
        seen.add(b.barrier_id)

    tasks, seen_t = [], set()
    for t in out.tasks:
        if t.task_id in seen_t:
            notes.append(f"dropped duplicate task {t.task_id}")
            continue
        tasks.append(t)
        seen_t.add(t.task_id)

    # Ranks are compressed to 1..n with ties kept, and IDs renumbered in that order,
    # so the plan's rules hold without changing which steps the model put first.
    ordered = sorted(out.recommendations, key=lambda r: r.rank)
    dense = {r: i for i, r in enumerate(sorted({r.rank for r in ordered}), 1)}
    actions = []
    for r in ordered:
        rid = f"R{len(actions) + 1}"
        try:
            calls = [ToolCall(tool_name=c.tool_name, arguments={"reason": c.reason}, requested_by=Stage.REC_GENERATOR)
                     for c in r.proposed_tool_calls]
            rec = Recommendation(**r.model_dump(exclude={"recommendation_id", "rank", "proposed_tool_calls",
                                                         "requires_human_approval"}),
                                 recommendation_id=rid, rank=dense[r.rank], proposed_tool_calls=calls)
        except ValidationError as e:
            notes.append(f"dropped recommendation ranked {r.rank} ({r.description[:40]}): {e.errors()[0]['msg']}")
            continue
        actions.append(PlannedAction(recommendation=rec, approval=_approval(taxonomy, rec, r.requires_human_approval)))
    ranks = sorted({a.recommendation.rank for a in actions})  # a dropped item can leave a hole
    for a in actions:
        a.recommendation.rank = ranks.index(a.recommendation.rank) + 1

    plan = DischargePlan(case_id=case.case_id, summary=f"{out.clinical_summary}\n\n{out.social_summary}",
                         barriers=barriers, information_gaps=list(out.information_gaps), tasks=tasks,
                         actions=actions)
    return plan, notes


def user_prompt(case: PatientCase) -> str:
    return f"Condition: {case.condition}\n\n## Patient case\n{case_view(case)}"


def finish(taxonomy: dict, case: PatientCase, answer: Answer) -> BaselineResult:
    common = dict(raw_output=answer.raw, llm_calls=answer.calls)
    if answer.out is None:
        return BaselineResult(SYSTEM, case.case_id, "failed", error=answer.error, error_kind=answer.error_kind, **common)
    plan, notes = normalize(taxonomy, case, answer.out)
    return BaselineResult(SYSTEM, case.case_id, "succeeded", plan=plan, notes=notes, **common)


def run_case(path: str | Path, llm: LLMClient, taxonomy: dict | None = None, max_attempts: int = 2) -> BaselineResult:
    taxonomy = taxonomy or load_taxonomy()
    case, failed = load_case(path, SYSTEM)
    if failed:
        return failed
    answer = ask(llm, SYSTEM, system_prompt(taxonomy), user_prompt(case), SinglePromptOutput, max_attempts)
    return finish(taxonomy, case, answer)


def run_cases_batch(paths: list[Path], llm, taxonomy: dict | None = None, max_attempts: int = 2,
                    **batch_kw) -> list[BaselineResult]:
    """The same as run_case for every path, through the Batch API."""
    taxonomy = taxonomy or load_taxonomy()
    loaded = [load_case(p, SYSTEM) for p in paths]
    cases = {c.case_id: c for c, _ in loaded if c is not None}
    system = system_prompt(taxonomy)
    answers = ask_batch(llm, SYSTEM, {cid: (system, user_prompt(c)) for cid, c in cases.items()},
                        SinglePromptOutput, max_attempts, **batch_kw)
    return [failed if c is None else finish(taxonomy, c, answers[c.case_id]) for c, failed in loaded]


def _cli(argv=None) -> int:
    from src.utils.llm import AnthropicLLM
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    settings = load_run_config().llm
    check_environment(settings)
    llm = AnthropicLLM.from_settings(settings)
    taxonomy = load_taxonomy()

    def tag(r: BaselineResult) -> BaselineResult:
        r.llm_settings = settings.model_dump()
        return r

    return main(SYSTEM, lambda p: tag(run_case(p, llm, taxonomy, settings.max_attempts)), argv,
                batch=lambda ps: [tag(r) for r in run_cases_batch(ps, llm, taxonomy, settings.max_attempts)],
                llm_settings=settings.model_dump(), prompt_version=PROMPT_VERSION)


if __name__ == "__main__":
    sys.exit(_cli())
