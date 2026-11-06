# Evaluation results

**Outcome (pre-registration §8): mixed success.** The agentic system beat both baselines on H3 and H4a, two of the
four hypotheses; strong success needed three. Its H4a margin over the strongest baseline (B2) was
+0.355, above the 0.2 bar. No reversal flag was raised. Run 2026-11-05.

## Primary hypotheses (all 160 cases)

Means of per-case values, as tested. H4a applies to the 77 cases with a keyed approval action.

| Hypothesis | Cases | B1 | B2 | Agentic | Beat B1 | Beat B2 | Agentic minus B2 (95% CI) |
|---|---|---|---|---|---|---|---|
| H1: Barrier recall | 160 | 0.776 | 0.997 | 0.998 | Yes | No | +0.001 (-0.004 to +0.007) |
| H2: Task recall | 160 | 0.767 | 0.980 | 0.998 | Yes | No | +0.018 (+0.006 to +0.031) |
| H3: Plan completeness | 160 | 0.791 | 0.890 | 0.992 | Yes | Yes | +0.102 (+0.072 to +0.135) |
| H4a: Approval accuracy | 77 | 0.361 | 0.645 | 1.000 | Yes | Yes | +0.355 (+0.273 to +0.439) |

H2 against B2 is statistically significant, but below the pre-registered 0.05 minimum difference, so it does not count
as beating B2.

## Required sensitivity analyses

| Analysis | Cases | Outcome | Beaten against both | H4a margin |
|---|---|---|---|---|
| All cases | 160 | mixed | H3, H4a | +0.355 |
| Without CASE-151, 152, 154 (Amendment 12) | 157 | mixed | H3, H4a | +0.353 |
| Without the 8 B-F02 cases (Amendment 3) | 152 | mixed | H3, H4a | +0.360 |

## Results by difficulty

Pooled over items, as in `summary.csv`, so these can differ slightly from the per-case means above. The last row group
drops the 8 cases that Amendment 3 left labeled medium (CASE-005, 007, 011, 015, 032, 039, 084, 087).

| Difficulty | Cases | Metric | B1 | B2 | Agentic |
|---|---|---|---|---|---|
| easy | 48 | Barrier recall | 1.000 | 0.990 | 0.995 |
| easy | 48 | Task recall | 0.986 | 0.986 | 0.995 |
| easy | 48 | Plan completeness | 0.986 | 0.986 | 0.995 |
| easy | 48 | Approval accuracy | 1.000 | 0.556 | 1.000 |
| medium | 80 | Barrier recall | 0.687 | 0.999 | 0.997 |
| medium | 80 | Task recall | 0.677 | 0.996 | 0.997 |
| medium | 80 | Plan completeness | 0.711 | 0.885 | 0.994 |
| medium | 80 | Approval accuracy | 0.396 | 0.698 | 1.000 |
| hard | 32 | Barrier recall | 0.692 | 0.996 | 1.000 |
| hard | 32 | Task recall | 0.685 | 0.918 | 1.000 |
| hard | 32 | Plan completeness | 0.733 | 0.724 | 0.982 |
| hard | 32 | Approval accuracy | 0.309 | 0.571 | 1.000 |
| medium without the 8 | 72 | Barrier recall | 0.665 | 0.998 | 0.997 |
| medium without the 8 | 72 | Task recall | 0.654 | 0.995 | 0.997 |
| medium without the 8 | 72 | Plan completeness | 0.691 | 0.876 | 0.993 |
| medium without the 8 | 72 | Approval accuracy | 0.372 | 0.693 | 1.000 |

## How to read these results

- H4a is partly architectural: the approval gate is a fixed rule. The pipeline's model labeled 0.87 of sign-offs itself
  (47 omissions, caught by the gate), against 0.73 for B2. The gate also over-flags: approval precision is 0.81, against
  0.91 for B2.
- Audit completeness and the composite are 1.00 against 0.00 by design, because the baselines write no audit trail.
- Forbidden-tool refusal was 1.00 for every arm, and no arm proposed a forbidden tool.
- The cases are synthetic and the keys are the team's, so these results do not show clinical performance.

## Provenance

160 cases, B1 once, B2 and the pipeline 3 times each: 1,120 case runs, all succeeded. Model `claude-sonnet-5-5`, pipeline
prompt v6, B2 prompt v7, taxonomy 2.6. Cost about $29.80.

| Runs | Commit | Source folder |
|---|---|---|
| B1 run 1, B2 runs 1 and 2, Agentic run 1 | `3b54581` | `runs/main` |
| B2 run 3, Agentic runs 2 and 3 | `6f69a47` | `runs/main-rest` |

`runs/main` crashed at Agentic run 2, CASE-015, so its 14 completed cases of that run were discarded and the whole run
repeated (Amendment 14). `runs/main-final` combines the runs above, and the score files are in `docs/results/`. Raw
runs are gitignored. Scores from the incomplete run were invalid and are not reported.
