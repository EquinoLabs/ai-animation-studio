# Architecture & Quality Specification: Script Builder Quality Pass 1

## 1. Overview & Objective

This pass addresses narrative depth and duration pacing in the Script Builder stage (`EquinoLabs/ai-animation-studio`).

Prior to this pass, requesting a 5.0-minute explainer draft for topics such as *"How Did Humans Invent Currency, and Why Was It Needed?"* produced approximately 300 words of narration (~2.1 minutes of spoken audio at 140 WPM). Although on-topic, the resulting drafts were brief, hurried over core mechanisms, and did not develop sufficient explanatory depth for the requested runtime.

This pass introduces:
1. Dynamic soft-target duration planning and word-budget allocation based on configurable speaking rates (WPM).
2. Deepened narrative prompts that require concrete cause-and-effect mechanisms, dilemmas, and historical/scientific developments.
3. Runtime duration estimation on both individual sections (`target_duration_seconds`) and total script (`estimated_duration_seconds`, `total_word_count`).
4. An automated bounded expansion step for drafts that fall substantially below the soft planning threshold (< 60% of minimum word target).
5. Non-breaking UI telemetry displaying estimated spoken runtime alongside target duration.

---

## 2. Root Cause Analysis

Before changing code, runtime generation logs, token limits, prompts, and output structures were analyzed:

1. **Token Budgets & Truncation:**
   - In `backend/app/llm/ollama.py`, `num_predict` was set to `3072`.
   - Inspection of `done_reason` confirmed generation terminated with `"stop"`, producing ~800 tokens total (~3,784 bytes of JSON).
   - **Conclusion:** Output was NOT truncated by token exhaustion.

2. **Prompt Specification & Schema Prior:**
   - The generation prompt specified `Target video duration: approximately {target_duration} minutes` as a generic instruction without words-per-minute (WPM) benchmarks, target word ranges, or section-by-section pacing distributions.
   - The few-shot schema example provided in the prompt used minimal 7-word placeholders for `"narration"`.
   - LLMs generate JSON objects by sampling from learned distributions; absent explicit per-section or total narration depth guidance, modern instruction models default to 2–3 sentences per section (~50–60 words). Multiplied across 5 sections, this naturally results in ~250–300 words.

3. **Absence of Duration Telemetry & Feedback:**
   - Neither the domain models nor the services calculated actual narration word counts or estimated speaking runtime.
   - `ScriptSection.target_duration_seconds` was undefined, leading the frontend to display blank durations (`~s`).
   - The backend had no feedback loop to detect severely under-length drafts.

---

## 3. Pacing Mathematics & Soft Target Ranges

Spoken explainer narration typically runs at **120 to 150 words per minute (WPM)**. The system adopts a documented default speaking rate of **140 WPM** (configurable via `ANIM_WORDS_PER_MINUTE`).

For a target duration $T$ minutes:
- **Planning Lower Bound:** $W_{min} = T \times 120$ words
- **Nominal Target:** $W_{target} = T \times 140$ words
- **Planning Upper Bound:** $W_{max} = T \times 150$ words

### Narrative Section Distribution
The 5-section narrative arc is allocated according to standard explainer pacing:

| Section Role | Runtime Allocation | 5-Minute Word Target | Narrative Function |
|---|---|---|---|
| **`hook`** | 12% (~35s) | ~84 words | Establish central paradox, puzzle, or counter-intuitive dilemma |
| **`setup`** | 20% (~60s) | ~140 words | Foundational context, initial system constraints, why early methods failed |
| **`middle`** | 36% (~108s) | ~252 words | Core mechanisms, cause-and-effect steps, real-world examples, trials |
| **`climax`** | 20% (~60s) | ~140 words | Decisive breakthrough invention or turning point that solved the dilemma |
| **`takeaway`** | 12% (~35s) | ~84 words | Synthesis of modern significance, lasting legacy, closing perspective |

---

## 4. Bounded Expansion Architecture

To protect generation quality without risking infinite loops or latency blowups:

1. When `generate_script` receives a draft, it computes `actual_words = sum(count_words(s.narration) for s in sections)`.
2. A draft is classified as **substantially under-length** if:
   $$\text{actual\_words} < 0.60 \times W_{min}$$
   *(For a 5-minute video, $0.60 \times 600 = 360$ words).*
3. If under-length, the provider triggers a single, bounded expansion pass via `expand_script`:
   - Instructs the model to preserve all accurate facts, strong phrasing, and section topics.
   - Directly elaborates on the mechanisms in `setup`, `middle`, and `climax`.
   - Explicitly forbids generic fluff or repetitive filler.
4. If the expanded draft yields a higher word count, it is adopted. If the expansion step fails or returns fewer words, the initial draft is safely retained.
5. Soft-target principle: Scripts exceeding the upper planning bound are never truncated or rejected.

---

## 5. Verification & Live Ollama Results

A live side-by-side run was conducted using local Ollama (`qwen2.5:7b`) on the identical topic: *"How Did Humans Invent Currency, and Why Was It Needed?"* (5.0 min target).

### Quantitative Comparison

| Metric | Before (Phase 2) | After (Quality Pass 1) | Delta |
|---|---|---|---|
| **Total Narration Words** | 300 words | 447 words | **+147 words (+49%)** |
| **Estimated Spoken Runtime** | 2.14 min (129s) | 3.19 min (192s) | **+1.05 min (+49%)** |
| **Hook** | 66 words | 50 words | -16 words (sharper focus) |
| **Setup** | 57 words | 104 words | **+47 words (+82%)** |
| **Middle** | 49 words | 117 words | **+68 words (+139%)** |
| **Climax** | 62 words | 95 words | **+33 words (+53%)** |
| **Takeaway** | 66 words | 81 words | **+15 words (+23%)** |

### Qualitative Analysis
- **Setup:** Replaced generic statements with the concrete economic friction of the barter system (farmer with grain seeking tools from a blacksmith who doesn't need grain; mutual desires unable to trade due to quantity/quality mismatches).
- **Middle:** Detailed the mechanical progression from tokens with cuneiform inscriptions to clay tablets and durable, divisible metals, highlighting the requirement for institutional trust.
- **Climax:** Detailed 600 BCE Lydia, King Croesus, and the introduction of the electrum stater with official seals guaranteeing consistent weight, leading to spread across Persia and Greece.
- **Zero Filler:** All phrases like *"Step by step, the pieces connect"* or *"When [topic] is put to work"* were absent.
