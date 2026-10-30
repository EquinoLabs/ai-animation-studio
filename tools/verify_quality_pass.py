"""Real Ollama Quality Pass 1 Verification Script:
Generates a script for "How Did Humans Invent Currency, and Why Was It Needed?" with a 5.0 minute target,
and compares metrics against the previous draft from projects/proj_220bac8461bd.
"""

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(".").resolve()))

from backend.app.config import Settings
from backend.app.domain.script import count_words, estimate_duration_seconds
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.scriptwriter.service import ScriptService

if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run_comparison():
    print("=" * 80)
    print("QUALITY PASS 1: REAL OLLAMA LIVE GENERATION & BEFORE/AFTER COMPARISON")
    print("=" * 80)

    # 1. Inspect Previous Draft ("Before") from proj_220bac8461bd without mutating it
    before_path = Path("projects/proj_220bac8461bd/script/current.json")
    if not before_path.exists():
        print("ERROR: Previous draft not found at", before_path)
        return

    before_data = json.loads(before_path.read_text(encoding="utf-8"))
    before_sections = before_data["sections"]
    before_counts = {s["type"]: count_words(s["narration"]) for s in before_sections}
    before_total = sum(before_counts.values())
    before_est_sec = estimate_duration_seconds(before_total, words_per_minute=140)
    before_est_min = before_est_sec / 60.0

    print("\n--- [BEFORE] Previous Draft Metrics (proj_220bac8461bd) ---")
    print(f"Target Duration: {before_data.get('target_duration_minutes', 5.0):.1f} min")
    print(f"Total Narration Words: {before_total} words")
    print(f"Estimated Spoken Runtime @ 140 WPM: {before_est_sec:.1f}s (~{before_est_min:.2f} min)")
    for s in before_sections:
        words = count_words(s["narration"])
        print(f"  - [{s['type'].upper():8s}] {s['heading'][:45]:45s} | {words:3d} words")

    # 2. Generate New Draft ("After") in isolated directory
    temp_dir = Path("projects/_quality_pass_verification")
    temp_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        PROJECTS_DIR=temp_dir,
        LLM_SCRIPT_PROVIDER="ollama",
        OLLAMA_MODEL="qwen2.5:7b",
        OLLAMA_HOST="http://localhost:11434",
        WORDS_PER_MINUTE=140,
    )

    proj_service = ProjectService(projects_root=temp_dir)
    test_proj = proj_service.create_project(title="Currency Explainer Quality Pass")
    pid = test_proj.metadata.project_id

    provider = OllamaLLMProvider(settings=settings)
    script_service = ScriptService(
        project_service=proj_service,
        llm_provider=provider,
        settings=settings,
    )

    topic = "How Did Humans Invent Currency, and Why Was It Needed?"
    print(f"\n--- [GENERATING AFTER] Running Real Ollama ({settings.OLLAMA_MODEL}) ---")
    print(f"Topic: '{topic}'")
    print(f"Target Duration: 5.0 minutes")
    print("Generating initial draft with narrative depth & soft duration target...")

    t0 = time.time()
    after_script = script_service.start_script(
        project_id=pid,
        topic=topic,
        target_duration_minutes=5.0,
    )
    elapsed = time.time() - t0

    after_counts = {s.type: count_words(s.narration) for s in after_script.sections}
    after_total = after_script.total_word_count or sum(after_counts.values())
    after_est_sec = after_script.estimated_duration_seconds or estimate_duration_seconds(after_total, 140)
    after_est_min = after_est_sec / 60.0

    print(f"\nGeneration completed in {elapsed:.1f}s.")
    print("\n--- [AFTER] Quality Pass 1 Draft Metrics ---")
    print(f"Title: '{after_script.title}'")
    print(f"Total Narration Words: {after_total} words")
    print(f"Estimated Spoken Runtime @ 140 WPM: {after_est_sec:.1f}s (~{after_est_min:.2f} min)")
    for s in after_script.sections:
        words = count_words(s.narration)
        sec_time = s.target_duration_seconds or estimate_duration_seconds(words, 140)
        print(f"  - [{s.type.upper():8s}] {s.heading[:45]:45s} | {words:3d} words (~{sec_time:.0f}s)")

    # 3. Side-by-Side Comparison Table
    print("\n" + "=" * 80)
    print("SIDE-BY-SIDE BEFORE vs. AFTER COMPARISON TABLE")
    print("=" * 80)
    print(f"{'Metric':<25} | {'Before (Phase 2)':<22} | {'After (Quality Pass 1)':<24} | {'Change':<12}")
    print("-" * 88)
    print(f"{'Total Narration Words':<25} | {before_total:<22} | {after_total:<24} | +{after_total - before_total} words")
    print(f"{'Estimated Runtime':<25} | {before_est_min:.2f} min ({before_est_sec:.0f}s)       | {after_est_min:.2f} min ({after_est_sec:.0f}s)         | +{after_est_min - before_est_min:.2f} min")
    print(f"{'Target Runtime':<25} | 5.00 min               | 5.00 min                 | Soft Target")
    print(f"{'Target Alignment':<25} | {before_total / 700 * 100:.1f}% of 700w target   | {after_total / 700 * 100:.1f}% of 700w target     | Enhanced")

    print("-" * 88)
    for role in ["hook", "setup", "middle", "climax", "takeaway"]:
        b_w = before_counts.get(role, 0)
        a_w = after_counts.get(role, 0)
        diff = a_w - b_w
        sign = "+" if diff >= 0 else ""
        print(f"{'  - ' + role.capitalize():<25} | {b_w:<22} | {a_w:<24} | {sign}{diff} words")
    print("=" * 88)

    print("\n--- NARRATION EXCERPTS (AFTER) ---")
    for s in after_script.sections:
        print(f"\n[{s.type.upper()}] - {s.heading} ({count_words(s.narration)} words):")
        print(f"Narration: {s.narration}")
        if s.notes:
            print(f"Visual Notes: {s.notes}")


if __name__ == "__main__":
    run_comparison()
