# Phase 7 — CP4C Repair Target-Span Remediation

**To:** Project Lead  
**From:** Sole Employee  
**Date:** November 26, 2026  
**Status:** Offline Implementation & Test Verification Complete  

---

## 1. Context & Problem Diagnosis

During controlled live regression `p7_live_validation_008`, the ScriptRepairer schema deserialization defect was resolved, and the fact-checking gate correctly intercepted ungrounded claims. However, surgical section repair failed due to **target span proposition confusion**:
- In `_build_section_repair_prompt`, the LLM received the section narration followed by `CLAIM ID: clm_03` and `CURRENT PROPOSITION: "Triggers or sensory cues initiate a habit sequence."`.
- The prompt omitted the corresponding narration sentence from the per-claim prompt block.
- Qwen2.5:7b populated `target_span` with the abstract claim proposition (`"Triggers or sensory cues initiate a habit sequence."`) rather than the verbatim sentence in the script (`"A trigger or sensory cue—like the sound of your coffee machine or seeing an empty mug on the counter—initiates a habit sequence."`).
- The validator correctly detected `count == 0` (proposition was not verbatim in narration) and triggered a corrective retry.
- On corrective retry, because the error message did not explain the difference between the proposition and the narration sentence, Qwen repeated the exact same error, resulting in a validation failure and preserving original narration/claims as unresolved.

---

## 2. Implemented Architecture & Remediation

### A. Deterministic Sentence Resolution (`_resolve_claim_narration_sentence`)
Before calling the LLM, the application deterministically identifies the narration sentence corresponding to each target claim proposition:
1. **Sentence Segmentation:** Narration is split into sentences using punctuation lookbehinds (`re.split(r"(?<=[.!?])\s+", narration)`).
2. **Exact Inclusion:** Checks whether the claim text is identical to or contained within any narration sentence.
3. **Content Root Overlap:**
   - Filters out grammatical proposition stopwords.
   - Normalizes substantive words using suffix stemming (`ing`, `tion`, `ions`, `ment`, `ness`, `ies`, `es`, `ed`, `s`).
   - Computes overlap between proposition roots and sentence roots.
   - Enforces a minimum threshold of `>= 2` distinct substantive root matches.
4. **Accuracy Verification:** Tested across all 34 historical claims in runs `008`, `007`, and `006`: achieved **100% resolution accuracy** without a single misidentification.

### B. Explicit Prompt Separation in `_build_section_repair_prompt`
For every target claim in the section, the prompt now explicitly separates and provides:
1. `TARGET NARRATION SENTENCE (Must match target_span verbatim): "<associated_sentence>"`
2. `CURRENT PROPOSITION (ABSTRACT CLAIM PROPOSITION to repair): "<claim_text>"`
3. **Mandatory Rule:**
   - `"target_span MUST be copied verbatim from TARGET NARRATION SENTENCE (or CURRENT SECTION NARRATION)."`
   - `"CRITICAL: DO NOT use the ABSTRACT CLAIM PROPOSITION as 'target_span'! The proposition is the abstract factual meaning being evaluated, NOT the sentence in the script."`

### C. Actionable Proposition Confusion Detection in `_validate_section_repair_response`
When `target_span` does not occur in narration (`count == 0`), the validator evaluates whether `target_span` was submitted as the abstract proposition (or has `>= 70%` token overlap with it).
When proposition confusion is detected, validation emits an actionable diagnostic error:
```
Claim '{r.claim_id}' target_span was submitted as the abstract claim proposition ('{target}'),
which does not occur verbatim in the section narration. The claim proposition is not the narration sentence.
The target span must be copied exactly from the supplied narration text.
The exact narration sentence to copy into target_span is: "{associated_sent}".
```

### D. Safe Bounded Corrective Retry & Claim Preservation
- The corrective prompt delivers this explicit feedback to the model.
- If the model provides the exact narration sentence on retry, validation succeeds and surgical replacement proceeds.
- If the model repeats invalid output, `_execute_section_corrective_retry` raises `ScriptRepairMalformedError`, and `_repair_section` safely retains the original section narration and original claims as unresolved without silent acceptance or corruption.

---

## 3. Offline Verification & Test Outcomes

1. **Focused Repairer & Lifecycle Test Suite:**
   - Command: `backend/.venv/Scripts/python -m pytest backend/tests/test_script_repairer.py backend/tests/test_phase5_checkpoint4_lifecycle.py`
   - Outcome: **54 passed**, 0 failed.
   - Tests Added:
     - `test_claim_proposition_rejected_as_target_span_with_actionable_feedback`: Verifies proposition rejection with specific diagnostic feedback and sentence hint.
     - `test_corrective_retry_recovering_from_proposition_confusion_regression`: Verifies full end-to-end recovery where call 1 fails on proposition confusion, retry succeeds using narration sentence, and recheck marks claim supported.
     - `test_repeated_proposition_confusion_safely_preserves_unresolved_claim`: Verifies that repeating proposition confusion preserves original narration and claims as unresolved without infinite retries or corruptions.
     - `test_resolve_claim_narration_sentence_unit_tests`: Verifies sentence resolution across edge cases, exact inclusion, complex neuroanatomy propositions, and non-matching claims.
2. **Full Backend Test Suite:**
   - Command: `backend/.venv/Scripts/python -m pytest backend/tests`
   - Outcome: **503 passed**, 0 failed in 46.27s.

---

## 4. Operational Boundaries

All changes remain strictly offline:
- Zero live provider calls (Tavily/Firecrawl).
- Zero background servers or daemons active.
- Ready for Project Lead review and authorization before any subsequent controlled live regression.
