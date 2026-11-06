# Phase 7 Checkpoint 4C Remediation: Fact-Checker Reliability & Section-by-Section Generation

**Document:** `docs/architecture/P7_cp4c_remediation_fact_check_and_section_generation.md`  
**Author:** Sole Employee  
**Reviewed by:** Project Lead  
**Status:** Approved & Implemented  
**Date:** November 2026  

---

## 1. Executive Summary

During the Phase 7 Checkpoint 4C controlled live validation run (`p7_live_validation_002`), two primary blockers were identified:

1. **Fact-Checker Schema Formatting Flakiness:** Local Ollama (`qwen2.5:7b`) failed Pydantic validation because the prompt omitted the explicit JSON schema template and field definitions. The model returned a JSON structure matching the previous stage (`evidence_ids` and `recommended_action`) but omitted the mandatory `status`, `explanation`, and `supporting_evidence_ids` fields.
2. **Script Generation Length Shortfall:** When tasked with generating a 5-section, 5-minute script (~700 words) in a single monolithic LLM prompt, the model compressed every section into a single paragraph (~70 words per section), generating only 340 total spoken words (~2.4 minutes).

This architecture document details the approved remediation implemented to resolve both issues safely and deterministically without weakening Phase 5 factual grounding contracts or relying on external API calls.

---

## 2. Workstream A: Fact-Checker Prompt & Schema Reliability

### 2.1 Problem Diagnosis
In `backend/app/services/scriptwriter/fact_checker.py`, `_build_batch_prompt` previously constructed batch fact-check prompts that listed claims and permitted evidence, concluding with:
```text
Respond with valid JSON containing the assessments list.
```
Without a concrete schema template or field definitions in the prompt, smaller open-weight models (`qwen2.5:7b`) guessed field keys based on prior prompt vocabulary, emitting:
```json
{
  "assessments": [
    {
      "claim_id": "clm_01",
      "recommended_action": "keep",
      "explanation": "",
      "evidence_ids": ["evi_01"]
    }
  ]
}
```
This was rejected by Pydantic validation because `status` was missing (`status: Field required`). When a retry was attempted, the retry prompt also omitted the schema template.

### 2.2 Implemented Architectural Solution

1. **Explicit Complete Schema in Initial Prompt (`_build_batch_prompt`):**
   The prompt now contains a concrete JSON schema template with all 7 fields explicitly enumerated:
   ```json
   {
     "assessments": [
       {
         "claim_id": "clm_01",
         "status": "supported",
         "explanation": "Explain precisely how the supplied excerpt supports the proposition...",
         "supporting_evidence_ids": ["evi_01"],
         "contradicting_evidence_ids": [],
         "contextual_evidence_ids": [],
         "recommended_action": "keep"
       }
     ]
   }
   ```
2. **Explicit Field Definitions:**
   The prompt details the exact semantic and structural requirements of each field:
   - `claim_id`: Must match the claim ID being evaluated.
   - `status`: **MANDATORY.** Permitted values: `"supported"`, `"unsupported"`, `"contradicted"`, `"unverifiable"`. Must NEVER be omitted.
   - `explanation`: Non-empty concise factual explanation citing specific excerpt text.
   - `supporting_evidence_ids`: Array of evidence IDs directly supporting the claim.
   - `contradicting_evidence_ids`: Array of evidence IDs directly contradicting the claim.
   - `contextual_evidence_ids`: Array of evidence IDs providing background context.
   - `recommended_action`: Permitted values: `"keep"`, `"repair"`, `"remove"`, `"qualify"`.
3. **Status Neutrality Directives:**
   The prompt explicitly states:
   - The JSON example uses `"status": "supported"` purely as a structural demonstration; it does NOT mean all claims are supported.
   - Excerpt titles, URLs, or metadata alone do NOT establish factual support.
   - Claims with no supporting evidence must be marked `"unsupported"`.
4. **Enhanced Corrective Retry (`_execute_corrective_retry`):**
   The retry prompt includes the previous validation error, lists any missing field names, re-injects the complete JSON schema template, and instructs the model to populate all required fields.
5. **Sanitized Diagnostic Logging:**
   On schema validation failure, the service logs a sanitized snippet (first 300 characters) of the model's response to simplify diagnosis without logging secrets or credentials.

---

## 3. Workstream B: Narrative Architecture & Section-by-Section Generation

### 3.1 Problem Diagnosis
Generating 5 complete sections in a single prompt causes 7B parameter models to budget tokens uniformly across sections, producing brief summaries (~60–70 words each) rather than multi-beat narrative depth. Single-pass generation lacks the capacity to balance macro-structure, evidence grounding, and rich narrative pacing simultaneously.

### 3.2 Implemented Architectural Solution

1. **Domain Contract Extension (`GeneratedSectionPayload`):**
   Defined in `backend/app/domain/script.py`:
   ```python
   class GeneratedSectionPayload(BaseModel):
       model_config = ConfigDict(extra="ignore")
       heading: str = Field(..., min_length=1)
       narration: str = Field(..., min_length=1)
       notes: Optional[str] = Field(None)
       claims: List[ScriptClaimPayload] = Field(default_factory=list)
   ```
   `ScriptClaimPayload`'s `section_id` was updated to `Optional[str] = None` so sections generate claims naturally, and the generator authoritatively assigns sequential `claim_id` and canonical `section_id`.

2. **Multi-Beat Outline Guidance (`get_multi_beat_guidance`):**
   Added in `backend/app/services/scriptwriter/policy.py`. It maps each section role (`hook`, `setup`, `middle`, `climax`, `takeaway`), target word count, and narrative structure (`chronological`, `mechanism_breakdown`, `problem_solution`) to 2–4 concrete factual milestones / narrative beats:
   - Substantial sections (~150–250+ words) receive at least 3 distinct mechanical steps or narrative beats.
   - `build_outline_prompt` incorporates explicit multi-beat milestone directives to ensure the outline roadmap has sufficient structural density before script generation begins.

3. **Section-by-Section Generation Service (`_generate_draft_section_by_section`):**
   Implemented in `backend/app/services/scriptwriter/generator.py`:
   - Iterates through `ScriptOutline.sections` sequentially.
   - Generates section-specific prompts using `build_single_section_prompt`.
   - Injects narrative continuity context: passes headings and a concise summary of preceding sections so the model produces smooth transitions and avoids repeating facts.
   - Maps permitted evidence IDs specifically relevant to each section.
   - Collects claims generated per section and establishes globally sequential claim IDs (`clm_01`, `clm_02`, ...).

4. **Bounded Section Expansion Fallback (`_expand_underdeveloped_section`):**
   - If a section's narration falls substantially below target (`< 50%` of outline target words and `< 60` words):
     * The generator executes a targeted expansion call using `build_section_expansion_prompt`.
     * The prompt provides the current narration, target words, missing factual milestones, and strict anti-filler instructions.
     * **Hard Bound:** Exactly ONE expansion attempt per section.
     * Fail-safe: If the expansion fails or fails to increase word count, the original section is retained without crashing.

5. **Generation Mode Selection & Backwards Compatibility:**
   - `DraftGeneratorInput.generation_mode`: `Literal["section_by_section", "monolithic"] = "section_by_section"`.
   - Default is `"section_by_section"`.
   - If `"monolithic"` is explicitly requested, or if mock presets in unit tests provide monolithic payloads, the system routes transparently to monolithic generation.

---

## 4. Verification & Testing

Offline regression test suite in `backend/tests/test_phase7_checkpoint4c_remediation.py` tests:
- Prompt JSON schema presence and field definitions in fact-checker original and retry prompts.
- Graceful fallback on fact-checker retry failure.
- Multi-beat guidance scaling.
- Section-by-section generation with sequential claim IDs and narrative continuity.
- Bounded section expansion triggers, limits, and fail-safe retention.
- Monolithic mode backwards compatibility.
