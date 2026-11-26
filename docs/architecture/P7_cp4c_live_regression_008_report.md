# Phase 7 — CP4C Live Regression 008 Report

**To:** Project Lead  
**From:** Sole Employee  
**Date:** November 26, 2026  
**Status:** Live Regression Complete — Report & Concrete Diagnosis

---

## 1. Executive Summary & Verification Evidence

Exactly one controlled live regression run (`p7_live_validation_008`) was executed following authorized testing procedures.

### Lifecycle & Process Verification
- **Working Directory:** `C:\Lakshya\Coding\EquinoLabs\ai-animation-studio`
- **Environment:** `backend/.venv` (Python 3.13.2)
- **Root Configuration:** Root `.env` loaded with valid Tavily, Firecrawl, and Ollama configuration without credential exposure.
- **Backend Process Started:**
  - PID: `26432`
  - Command: `backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`
  - Start Time: `11/26/2026 5:19:54 PM`
- **Post-Run Teardown:**
  - Process PID `26432` was forcibly stopped (`Stop-Process -Id 26432 -Force`).
  - Process termination confirmed (PID `26432` no longer exists).
  - Port `8000` verified released (no listening sockets; transient `TIME_WAIT` only).

### Run Identifiers
- **Project ID:** `p7_live_validation_008`
- **Topic:** "Why do humans form habits, and how does habit formation work?"
- **Research Plan ID:** `rplan_3a1625ff7f38`
- **Research Run ID:** `rrun_073c1ab8aa15`
- **Outline ID:** `out_f2bac542`
- **Script Versions:** `v001` (draft), `v002` (post-repair)
- **Fact-Check Reports:** `fcr_aeee768db5cc` (v1), `fcr_ed8387924e75` (v2)

---

## 2. Validation of the Three Targeted Defects

### A. Research Relevance & Evidence Coverage (Successful Behavior)
- **Resource Consumption:**
  - Search calls: 3/3 (Tavily).
  - Extraction calls: 3/3 (Firecrawl).
  - Extracted volume: 30,591 characters (under 45,000 budget).
- **Topic-Anchor Gating Performance:**
  - **Irrelevant Source Elimination:** The defect from run 007 (where Prepp.in exam prep for child development was selected due to generic keyword overlap) was **completely eliminated**. Zero irrelevant or exam-prep sources were admitted.
  - **Retained Evidence Quality:** Both verified evidence excerpts were 100% on-topic:
    - `evi_bdd7dbfe8e6e`: Basal ganglia, striatum, caudate nucleus, putamen from GlobalRPH.
    - `evi_15e37370d56a`: APA report on environmental design and habit formation from Behavixo.
  - **No False-Negative Starvation:** The primary topic anchor gate successfully passed technical neuroanatomy and environmental psychology content without over-restricting valid evidence.

### B. Evidence-Constrained Script Generation & Interception
- **Duration & Structure:**
  - Word count: 712 words (compliant with 5-minute target range: 600-750 words).
  - Section count: 5 sections strictly following outline.
- **Narrative Content vs. Evidence:**
  - The script generator correctly grounded the basal ganglia, caudate nucleus, and putamen in `evi_bdd7dbfe8e6e` and environmental space design in `evi_15e37370d56a`.
  - However, in section 3, the generator still introduced unevidenced terms from pretrained knowledge: "prefrontal cortex" and "dopamine release in the reward center".
- **Deterministic Interception:**
  - The fact-checking and validation layer intercepted every unsupported element:
    - `clm_01` (35,000 decisions daily): classified `unsupported`.
    - `clm_04` (prefrontal cortex): application validation rejected LLM attempts to pass it and enforced `unsupported`:
      `Batch response failed application validation: Claim 'clm_04' fails factual grounding: Claim asserts specific entity or mechanism 'prefrontal cortex' which is absent from the cited evidence.`
    - `clm_06` (dopamine release): classified `unsupported`.
    - `clm_09` (conserve mental energy): classified `partially_supported`.
    - `clm_10` (early humans focus on innovation): classified `partially_supported`.
  - Version 1 Report: 4 supported, 2 partially supported, 4 unsupported. `is_ready: false`.

### C. ScriptRepairer Reliability (Diagnosis of Confirmed Defect)
- **Schema & Deserialization Safety (Successful Behavior):**
  - In run 007, the repairer crashed during Pydantic deserialization due to missing required fields.
  - In run 008, deserialization succeeded cleanly. No Pydantic schema crashes occurred.
- **Exact-Span Validation & Failure Mode (Confirmed Defect):**
  - When Qwen2.5:7b was prompted to repair `sec_03`, instead of extracting the actual narration sentence into `target_span`, it copied the **synthesized claim proposition** into `target_span`.
    - Claim `clm_03` proposition: `"Triggers or sensory cues initiate a habit sequence."`
    - Narration text: `"A trigger or sensory cue—like the sound of your coffee machine or seeing an empty mug on the counter—initiates a habit sequence."`
    - Qwen returned: `target_span = "Triggers or sensory cues initiate a habit sequence."`
  - In `_validate_section_repair_response`, `section.narration.count(target)` returned `0` because the proposition does not verbatim appear in the narration.
  - Validation failed and executed a corrective retry.
  - On corrective retry, Qwen repeated the exact same error, returning the proposition text as `target_span` again.
  - **Graceful Degradation:** The repairer did not crash and did not silently accept the invalid repair. It caught `ScriptRepairMalformedError`, logged the exact failure, preserved the original section narration and claims as unresolved, and updated the version history.
  - Version 2 Report: `is_ready: false` (10 total claims, 4 supported, 2 partially supported, 4 unsupported).

---

## 3. Explicit Distinction of Outcomes

1. **Successful Behaviors Demonstrated in Run 008:**
   - Topic-anchor gating completely blocked irrelevant exam-prep/child-development excerpts.
   - Pydantic deserialization in `ScriptRepairer` handled model outputs safely without schema crashes.
   - Deterministic grounding validation intercepted ungrounded claims (prefrontal cortex, dopamine, early humans) and prevented false-positive approvals.
   - Failed repairs were safely rejected without silent corruption, retaining unresolved claims and correctly marking `is_ready: false`.
2. **Confirmed Defect:**
   - **Target Span Proposition Confusion in Repairer:** Qwen2.5:7b confuses the claim's abstract *proposition text* with the section's verbatim *narration sentence*, populating `target_span` with the proposition rather than the sentence from the narration. Because exact verbatim string matching is strictly required, the repair fails validation.
3. **Concerns Not Tested:**
   - Multi-iteration conversational fact challenges in frontend UI.
   - Downstream Phase 8 audio/voice/video pipelines (deliberately out of scope).

---

## 4. Smallest Recommended Correction for Project Lead Review

The repair prompt gives both `claim_id` and the proposition text, but does not explicitly provide the *original narration sentence* associated with the claim, leading the model to copy the proposition.

**Smallest Recommended Remedy:**
1. In `_build_section_repair_prompt`, explicitly provide the candidate narration sentence for each target claim, clearly labeled:
   - `Sentence in Narration: "<verbatim sentence>"`
   - `Claim Proposition to Repair: "<proposition>"`
   - `CRITICAL: Your 'target_span' MUST EXACTLY MATCH 'Sentence in Narration', NOT the Proposition!`
2. In `_validate_section_repair_response`, if `target_span` is the proposition text rather than the narration sentence, automatically detect this mismatch in the validation error message so the corrective retry specifically instructs: `"You submitted the claim proposition text as target_span instead of the verbatim narration sentence. target_span must be copied verbatim from the narration."`
