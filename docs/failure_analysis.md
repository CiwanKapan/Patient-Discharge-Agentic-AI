# Failure analysis

This follows the methodology's plan: identify the failure mode for the 20 cases where the agentic system did worst, and
categorize them. It is **exploratory and was done after the results were known**. It does not change the outcome in
`docs/results.md`, and no key, case, or score changed because of it.

**Main finding:** of the 20 worst cases, 6 are clear misses or additions against the chart-reading rules. The other 14
are items the system flagged beyond the keys: 8 are the known late follow-up limitation, and 6 are judgment calls that
a reviewer could defend.

## How the cases were chosen

Each case was scored by the mean of barrier F1, task F1, and plan completeness over the three Agentic runs. 25 of the
160 cases scored below 1, so the agentic system was perfect on 135. The 20 lowest are below.

## The 20 worst Agentic cases

| Failure mode | Cases | Case runs affected | Type |
|---|---|---|---|
| Flagged a follow-up as too late (B-F01, T-CM01) | CASE-005, 007, 011, 015, 032, 039, 084, 087 | 23 of 24 | Out of scope, known limitation |
| Inferred a caregiver problem from a COVID note (B-F03, T-CM02) | CASE-022, 030, 105, 154 | 11 of 12 | Judgment call |
| Flagged repeat imaging as pending (B-C03, T-MD02) | CASE-060, 126 | 5 of 6 | Judgment call, possible key gap |
| Missed the transport information gap (B-S07) | CASE-078, 114, 138 | 9 of 9 | Clear miss |
| Missed pending insurance authorization (B-S04, T-CM06) | CASE-010, 071 | 3 of 6 | Clear miss |
| Added T-PH02 beside T-PH03 for prior authorization | CASE-063 | 3 of 3 | Clear extra task |

### Follow-up flagged as too late

The chart books a visit, and a physician note requires follow-up sooner than that date. The system compares the dates
and flags B-F01, for example a PCP visit about 24 days out against a 7-day requirement. B2 did the same in 17 of 24
case runs. These are the 8 cases left by Amendment 3, recorded as a limitation in pre-registration §9. Judging whether
a date is too late is out of the system's scope, so the flags count as false positives. The reasoning is sensible, but
the behavior goes beyond what the system should decide.

### COVID-positive caregiver

The notes say a relative tested positive and cannot pick the patient up. The key treats this as a transport barrier
(B-S02). The system adds a caregiver barrier, reasoning that the same person cannot stay with the patient, although the
chart still reads complete and does not say that. B2 did the same in 8 of 12 case runs. It is an inference beyond the
chart, but a cautious reader might make it.

### Repeat imaging

A physician note says "repeat imaging ordered" while `pending_imaging` reads complete. The key has B-C07 here. The
system also flags B-C03, and B2 did so in all 3 runs of CASE-126 and none of CASE-060. The chart-reading rules allow a note to
contradict a complete field, so **this may be a gap in the key rather than a system error**. The reviewer accepted these
keys, so the team should decide.

Both notes also contain a timing error from the generator: they are stamped earlier in the day than the event they
describe (CASE-060 at 10:00 and CASE-126 at 11:00, describing arm drift at 13:00). Nothing depends on it.

### Transport information gap

Transport reads complete, but no pickup time is documented, so the timing cannot be checked. The rules say this is an
information gap for B-S07. It is the **only information-gap type the agentic system missed**: 14 of 18 case runs across
5 of the 6 cases with this gap. B2 missed it in 7 of 18. A complete field with a missing detail is easy to pass over,
because the field looks finished.

### Insurance authorization

`insurance_authorization_status` is in progress, which is a plain field-status barrier. The system missed it twice in
CASE-010 and once in CASE-071. Those are its only three missed barriers in 480 case runs. In CASE-010, a separate prior authorization problem with a medication may have drawn attention
away, but the logs do not show that.

### Extra prescription task

When a drug needs prior authorization, the key has T-PH03 only. The system sometimes adds T-PH02 (fill prescriptions) as
well: 9 case runs in all, and B2 also 9. Filling a prescription is a natural next step, so this is a mild error.

## Sign-off behavior

Counts are across three runs, with 303 keyed sign-off actions per arm.

| Error | Agentic | B2 |
|---|---|---|
| Required sign-off missing from the final plan | 0 | 110 |
| Model left the sign-off label off | 47, all caught by the gate | n/a, no gate |
| Sign-off on a step that does not need it | 74 | 18 |

**B2's 110 misses are concentrated:**

| Task | Misses | Why |
|---|---|---|
| T-PH01, medication reconciliation (GR-A02) | 51 of 51 | Recommended, never labeled |
| T-MD07, communicate a medication change (GR-A04) | 31 | Not recommended |
| T-PH02, fill prescriptions (GR-A03) | 19 | 18 recommended without a label, 1 not recommended |
| Others | 9 | Mostly not recommended |

**Agentic's 47 label omissions** are the same pattern: T-PH01 36 and T-PH02 11. Neither arm's model reliably sees
medication reconciliation as a change that needs sign-off. The taxonomy records T-PH01 to GR-A02 as a judgment call.

As an exploratory check only, removing T-PH01 from both arms raises B2's pooled approval accuracy from 0.64 to 0.77
and leaves Agentic at 1.00, a margin of about 0.23. So the H4a result does not depend on that one judgment call, though
its size does. This is not a pre-registered analysis.

**Over-gating:** half of Agentic's 74 extra sign-offs (37) are on T-RN05, wound assessment, where the model proposed
escalating to the attending. Agentic's approval precision is lowest on easy cases (0.53). That is the cost of its
caution: clinicians would see more sign-off requests.

## Why B2 lost on plan completeness

B2 usually found the problem but did not carry it into the plan.

| Keyed items not in the plan (three runs) | Agentic | B2 |
|---|---|---|
| Tasks, out of 1,371 | 3 | 147 |
| Of those, detected but not recommended | 0 | 102 |
| Information gaps, out of 231 | 14 | 114 |
| Of those, never detected | 14 | 95 |

Only 8 of B2's 147 missing tasks had their barrier covered by another action, so it was rarely merging tasks. The
pipeline must write one recommendation for every detected task and gap, and rejects a plan that does not, which B2 has
no equivalent of. This is the clearest case for the architecture: **the gain on H3 comes from the enforced steps, more
than from better detection.**

## Rule-based baseline

B1 missed what requires reading across fields or notes: all 42 keyed B-S07 and all 42 keyed B-E07, plus note-based
barriers such as B-C07 (13). Its cross-field recall is 0.00, as expected of a field checklist.

## Implications for the paper

1. Report the worst-case result as it stands, and separate clear errors from flags beyond the keys. Do not reclassify any
   as correct in the headline numbers.
2. Present H3 as the main architectural finding, because it comes from enforced coverage.
3. Present H4a with the gate's role, the label omissions, and the over-gating cost together.
4. Do not change keys and re-score. The repeat-imaging question should be reported as a possible key gap.

## Reproducing this

Computed from the plans in `runs/main-final` against `data/answer_keys`, with `docs/results/all_160/per_case.csv` for the
ranking. Reasons for model behavior are inferred from run rationales and charts; they were not tested.
