# CASE-075 — condition CO

**Planned discharge window:** 2026-06-12T12:00:00-05:00 to 2026-06-12T14:00:00-05:00

## Tracked fields

| Field | Status | Details |
|---|---|---|
| caregiver_status | complete | detail: Daughter is primary caregiver |
| case_manager_status | not_started | detail: No case manager assigned to the unit this weekend |
| diet_education_status | not_applicable |  |
| discharge_summary_status | complete | detail: Signed by attending |
| financial_status | complete |  |
| followup_appointment_status | complete | detail: Pulmonology clinic visit booked; scheduled_for: 2026-06-24T12:00:00-05:00 |
| home_health_referral_status | not_applicable |  |
| home_safety_status | not_applicable |  |
| insurance_authorization_status | not_applicable |  |
| medical_device_education_status | not_applicable |  |
| medical_stability_status | complete | detail: Meets discharge criteria per morning rounds |
| medication_availability_status | complete | detail: Discharge prescriptions sent to Walgreens |
| medication_education_status | complete | detail: Teach-back completed for discharge medication list; documented_at: 2026-06-11T14:00:00-05:00 |
| mobile_device_education_status | not_applicable |  |
| outpatient_therapy_status | not_applicable |  |
| oxygen_requirement | complete | detail: Uses home oxygen 2 L/min at baseline; supply confirmed |
| patient_education_status | complete | detail: Teaching on inhaler technique and the COPD action plan completed |
| pcp_notification_status | in_progress | detail: Message left with PCP office; no confirmation received |
| pending_consult | not_started | detail: Pulmonology consult ordered; patient not yet seen |
| pending_labs | complete | detail: All results reviewed |
| physical_function_status | complete | detail: Ambulating independently per PT |
| return_precautions_documented | complete |  |
| transport_status | complete | detail: Daughter to drive patient home; window: {'start': '2026-06-12T12:30:00-05:00', 'end': '2026-06-12T13:30:00-05:00'} |
| wound_status | not_applicable |  |

(A field not listed is undocumented.)

## Free-text notes

**case_manager_note**

Plan is discharge home with daughter. 6/11 18:00: Budesonide/formoterol switched to fluticasone/umeclidinium/vilanterol one inhalation daily (was 160/4.5 two puffs twice daily) on evening rounds. Patient and daughter not yet told; this afternoon's teaching covered the old dose. Pulmonology has not seen the patient yet. Original evening van slot cancelled; family will drive at the planned discharge time instead. Oxygen vendor is waiting on a signed renewal order for the home oxygen. Can the system authorize it so supply doesn't lapse?

**nursing_note**

Up in chair for meals. Pain controlled. The patient is a smoker. Smoking cessation advice was offered and accepted. A referral to a cessation support service was made. This does not block discharge.

**physician_note**

55-year-old woman with hypertension, GERD, admitted 6/7 with acute exacerbation of COPD. Completed 5 days of systemic steroids; wheeze resolved. A routine review of the patient's vaccination history was completed. All recommended vaccinations were up to date and no new vaccinations were needed during this stay.

---

## Answer key (generated draft)

**Barriers**

- B-E07 (cross-field) — evidence: tracked_fields.medication_education_status, free_text_notes.case_manager_note
- B-S05 (single-field) — evidence: tracked_fields.case_manager_status
- B-C04 (single-field) — evidence: tracked_fields.pending_consult
- B-F05 (single-field) — evidence: tracked_fields.pcp_notification_status
- B-S08 (single-field) — evidence: tracked_fields.oxygen_requirement, free_text_notes.case_manager_note

**Information gaps**

- tracked_fields.medication_reconciliation_status → could indicate B-C01
- tracked_fields.pending_imaging → could indicate B-C03

**Tasks**

- T-MD07 resolves B-E07 — needs sign-off GR-A04 (communicate_medication_change_to_patient_or_caregiver)
- T-CM09 resolves B-S05
- T-MD03 resolves B-C04
- T-CM08 resolves B-F05
- T-MD05 resolves B-S08 — needs sign-off GR-A05 (order_home_oxygen)

**Must not flag (negative controls):** B-C01, B-C03, B-S02, B-S07
