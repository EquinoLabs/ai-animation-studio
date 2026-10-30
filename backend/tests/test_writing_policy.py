"""Focused unit tests for the Writing Policy, storytelling structures, and prompt-engineering layer."""

import pytest

from backend.app.services.scriptwriter.policy import (
    ANTI_FILLER_DIRECTIVES,
    HOOK_FORMULAS,
    NARRATIVE_STRUCTURES,
    SCRIPT_TONES,
    ScriptPromptBuilder,
    WritingPolicy,
    detect_narrative_structure,
    get_hook_formula_guidance,
    get_structure_guidance,
    get_tone_guidance,
)


def test_detect_narrative_structure_adapts_to_topic():
    """Verify that narrative structure detection correctly classifies historical, mechanical, and problem topics."""
    # Historical / chronological topics
    assert detect_narrative_structure("The History of the Transatlantic Telegraph Cable") == "chronological"
    assert detect_narrative_structure("The Rise and Fall of the Roman Republic") == "chronological"
    assert detect_narrative_structure("Timeline of the 19th Century Steam Engine Evolution") == "chronological"

    # Mechanism / scientific topics
    assert detect_narrative_structure("How Does Quantum Computing Work: Inside Qubits") == "mechanism_breakdown"
    assert detect_narrative_structure("The Cellular Biology of Photosynthesis and Chloroplasts") == "mechanism_breakdown"
    assert detect_narrative_structure("Inside Neural Networks: The Physics of Backpropagation Algorithms") == "mechanism_breakdown"

    # Problem-solution / engineering challenges
    assert detect_narrative_structure("Solving the Urban Gridlock Crisis: Innovative Traffic Engineering") == "problem_solution"
    assert detect_narrative_structure("Overcoming the Battery Storage Bottleneck for Clean Energy") == "problem_solution"
    assert detect_narrative_structure("How Modern Engineers Fixed the Catastrophic Resonance Dilemma") == "problem_solution"

    # Default fallback
    assert detect_narrative_structure("General Concepts of Information") in NARRATIVE_STRUCTURES


def test_structure_guidance_covers_all_five_sections():
    """Verify that all narrative structures provide detailed directives for hook, setup, middle, climax, and takeaway."""
    for structure in NARRATIVE_STRUCTURES:
        guidance = get_structure_guidance(structure)
        assert set(guidance.keys()) == {"hook", "setup", "middle", "climax", "takeaway"}
        for section, directive in guidance.items():
            assert isinstance(directive, str)
            assert len(directive.strip()) > 20, f"Directive for {structure}:{section} is too brief"


def test_hook_formulas_guidance_forbids_generic_openings():
    """Verify that each hook formula provides distinct guidance and explicitly bans generic greetings."""
    for formula in HOOK_FORMULAS:
        guide = get_hook_formula_guidance(formula)
        assert "Do NOT open with generic greetings" in guide
        assert formula.replace("_", " ").lower() in guide.lower() or formula.split("_")[0] in guide.lower()


def test_tone_guidance_covers_all_tones():
    """Verify that all supported tones provide actionable voiceover instructions."""
    for tone in SCRIPT_TONES:
        guide = get_tone_guidance(tone)
        assert tone.capitalize() in guide or tone in guide.lower()
        assert len(guide.strip()) > 30


def test_anti_filler_directives_enforce_boundaries():
    """Verify that ANTI_FILLER_DIRECTIVES includes all required constraints."""
    assert "ZERO GENERIC FILLER" in ANTI_FILLER_DIRECTIVES
    assert "HONEST FACTUAL BOUNDARIES" in ANTI_FILLER_DIRECTIVES
    assert "RETRIEVED DOES NOT EQUAL VERIFIED" in ANTI_FILLER_DIRECTIVES
    assert "ACTIONABLE VISUAL STAGING" in ANTI_FILLER_DIRECTIVES
    assert "Step by step, the pieces connect" in ANTI_FILLER_DIRECTIVES


def test_writing_policy_factory_and_defaults():
    """Verify WritingPolicy initialization and factory method."""
    # Default
    policy = WritingPolicy()
    assert policy.narrative_structure == "chronological"
    assert policy.hook_formula == "question_paradox"
    assert policy.tone == "educational"
    assert policy.words_per_minute == 140

    # from_topic factory
    p2 = WritingPolicy.from_topic(
        topic="How Does Nuclear Fusion Work",
        hook_formula="pattern_interrupt",
        tone="authoritative",
        words_per_minute=130,
        custom_directives="Focus on magnetic confinement.",
    )
    assert p2.narrative_structure == "mechanism_breakdown"
    assert p2.hook_formula == "pattern_interrupt"
    assert p2.tone == "authoritative"
    assert p2.words_per_minute == 130
    assert p2.custom_directives == "Focus on magnetic confinement."


def test_prompt_builder_outline_prompt():
    """Verify that ScriptPromptBuilder.build_outline_prompt constructs a complete, valid prompt."""
    policy = WritingPolicy.from_topic(
        topic="The History of the Transatlantic Telegraph Cable",
        hook_formula="question_paradox",
        tone="educational",
    )
    builder = ScriptPromptBuilder(policy=policy)

    prompt = builder.build_outline_prompt(
        topic="The History of the Transatlantic Telegraph Cable",
        target_duration_minutes=5.0,
        evidence_brief_text="# Brief: Cable laid in 1858. Failed after 3 weeks.",
        title="Voice Across the Abyss",
    )

    # Core elements verified
    assert "The History of the Transatlantic Telegraph Cable" in prompt
    assert "Target Duration: ~5.0 minutes" in prompt
    assert "Preferred Title: Voice Across the Abyss" in prompt
    assert "RESEARCH EVIDENCE BRIEF" in prompt
    assert "Cable laid in 1858" in prompt
    assert "EVIDENCE UTILIZATION RULES" in prompt
    assert "STRICT ANTI-FILLER & FACTUAL RESTRAINT DIRECTIVES" in prompt
    assert '"outline_id"' in prompt
    assert '"factual_milestones"' in prompt
    assert '"visual_concept"' in prompt


def test_prompt_builder_script_generation_prompt():
    """Verify that ScriptPromptBuilder.build_script_generation_prompt includes claims array and evidence rules."""
    policy = WritingPolicy.from_topic(
        topic="Solving the Urban Traffic Gridlock",
        hook_formula="bold_promise",
        tone="casual",
    )
    builder = ScriptPromptBuilder(policy=policy)

    outline_mock = '{"outline_id": "out_01", "sections": []}'
    evidence_brief_mock = "# Evidence Brief\n- evi_01: Traffic lights optimized by AI reduce idle time by 28%."

    prompt = builder.build_script_generation_prompt(
        topic="Solving the Urban Traffic Gridlock",
        target_duration_minutes=3.0,
        outline_json=outline_mock,
        evidence_brief_text=evidence_brief_mock,
        title="Red Light, Green City",
    )

    assert "Solving the Urban Traffic Gridlock" in prompt
    assert "Red Light, Green City" in prompt
    assert "NARRATIVE OUTLINE ROADMAP" in prompt
    assert "RESEARCH EVIDENCE BRIEF" in prompt
    assert "GROUNDING & CLAIM ATTRIBUTION INSTRUCTIONS" in prompt
    assert '"claims": [' in prompt
    assert '"claim_id": "clm_01"' in prompt
    assert '"claim_ids": ["clm_01"]' in prompt
    assert "STRICT ANTI-FILLER & FACTUAL RESTRAINT DIRECTIVES" in prompt


def test_prompt_builder_script_revision_prompt():
    """Verify that ScriptPromptBuilder.build_script_revision_prompt guides conversational revisions."""
    builder = ScriptPromptBuilder()

    current_script_mock = '{"title": "Cable Explainer", "sections": [{"id": "sec_01", "narration": "Text"}]}'
    instruction = "Deepen the explanation of how Lord Kelvin's mirror galvanometer worked."
    context = "- revision_instruction: explain mirror galvanometer"

    prompt = builder.build_script_revision_prompt(
        current_script_json=current_script_mock,
        instruction=instruction,
        conversation_context=context,
        evidence_brief_text="# Brief item: Galvanometer detected tiny currents.",
    )

    assert "Cable Explainer" in prompt
    assert "Deepen the explanation of how Lord Kelvin's mirror galvanometer worked." in prompt
    assert "REVISION INTEGRITY & CLAIM MAPPING RETENTION" in prompt
    assert "STRICT ANTI-FILLER & FACTUAL RESTRAINT DIRECTIVES" in prompt
    assert "Galvanometer detected tiny currents" in prompt


def test_prompt_builder_revision_integrity_and_evidence_semantics():
    """Verify that prompt builders distinguish declared references from verified claims and protect revision integrity."""
    builder = ScriptPromptBuilder()

    # 1. Revision prompt explicitly forbids materially changed claims from inheriting old evidence linkage
    rev_prompt = builder.build_script_revision_prompt(
        current_script_json='{"title": "Test"}',
        instruction="Change the completion year from 1858 to 1866.",
    )
    assert "Materially changed factual propositions" in rev_prompt
    assert "MUST NOT automatically inherit the previous claim's evidence linkage" in rev_prompt
    assert "Untouched sections: Retain existing claim IDs" in rev_prompt

    # 2. Generation prompt explicitly notes that citing evidence declares a reference, not verification
    gen_prompt = builder.build_script_generation_prompt(
        topic="Transatlantic Cable",
        target_duration_minutes=5.0,
        evidence_brief_text="# Brief",
    )
    assert "declares a reference to a retrieved source excerpt" in gen_prompt
    assert "does NOT imply that the claim has been storage-validated or substantively verified" in gen_prompt

    # 3. Outline prompt distinguishes declared references from verified facts
    out_prompt = builder.build_outline_prompt(
        topic="Transatlantic Cable",
        target_duration_minutes=5.0,
        evidence_brief_text="# Brief",
    )
    assert "declares a reference to a retrieved research excerpt; it does not certify absolute factual truth" in out_prompt

    # 4. Guidance note distinguishes model instructions from Pydantic application validation
    assert "Application Schema Validation Note" in gen_prompt
    assert "Application Schema Validation Note" in out_prompt
    assert "Output will be validated strictly by Pydantic against domain contracts" in gen_prompt

    # 5. Visual staging and structure are presented as adaptable guidance
    assert "ACTIONABLE VISUAL STAGING AS GUIDANCE" in gen_prompt
    assert "Visual cues are creative guidance" in gen_prompt
