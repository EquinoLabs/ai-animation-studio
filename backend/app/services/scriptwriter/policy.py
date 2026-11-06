"""Writing policy, storytelling structures, and prompt-engineering layer for Script Builder.

Implements the internal Writing Skill policy layer per TechSpec Section 8 and
Script_Builder_Research_and_Writing_Design.md Section 8:
- Topic-sensitive narrative structure adaptation (chronological, mechanism_breakdown, problem_solution).
- Hook architecture formulas (question_paradox, bold_promise, pattern_interrupt).
- Tone calibration and spoken voiceover standards.
- Strict factual restraint and anti-filler directives.
- Pacing calibration with soft duration targets.
- 2D visual staging cue standards.
- Modular prompt builders for outline, script generation, expansion, and revision.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Literal, Optional

from backend.app.domain.script import (
    DEFAULT_WORDS_PER_MINUTE,
    SECTION_TYPES,
    compute_duration_word_targets,
)

NarrativeStructure = Literal["chronological", "mechanism_breakdown", "problem_solution"]
HookFormula = Literal["question_paradox", "bold_promise", "pattern_interrupt"]
ScriptTone = Literal["educational", "authoritative", "casual", "energetic"]

NARRATIVE_STRUCTURES: List[str] = ["chronological", "mechanism_breakdown", "problem_solution"]
HOOK_FORMULAS: List[str] = ["question_paradox", "bold_promise", "pattern_interrupt"]
SCRIPT_TONES: List[str] = ["educational", "authoritative", "casual", "energetic"]


# ---------------------------------------------------------------------------
# Narrative Structure Detection & Guidance
# ---------------------------------------------------------------------------

_CHRONOLOGICAL_KEYWORDS = re.compile(
    r"\b(history|origins?|evolution|rise|fall|century|war|chronolog\w*|timeline|"
    r"invent(ed|ion)?|discover(ed|y)?|first|early|decade|revolution|ancient|past)\b",
    re.IGNORECASE,
)

_MECHANISM_KEYWORDS = re.compile(
    r"\b(how\b[\w\s]{1,40}\bworks?|mechanis\w*|physics|biolog\w*|brain|quantum|fusion|nuclear|"
    r"engine|neural|atom\w*|inside|chemistr\w*|algorithm|process|cellular|dna|circuit|machin\w*)\b",
    re.IGNORECASE,
)

_PROBLEM_SOLUTION_KEYWORDS = re.compile(
    r"\b(problem|solv\w*|crisis|challenge|future|climate|bottleneck|dilemma|"
    r"fix|revolutioniz\w*|solution|engineer\w*|obstacle|disrupt\w*|breakthrough)\b",
    re.IGNORECASE,
)


def detect_narrative_structure(topic: str, context: Optional[str] = None) -> NarrativeStructure:
    """Infer the most natural narrative structure for a given topic and optional context."""
    text = f"{topic or ''} {context or ''}"

    chrono_hits = len(_CHRONOLOGICAL_KEYWORDS.findall(text))
    mech_hits = len(_MECHANISM_KEYWORDS.findall(text))
    prob_hits = len(_PROBLEM_SOLUTION_KEYWORDS.findall(text))

    if chrono_hits >= mech_hits and chrono_hits >= prob_hits and chrono_hits > 0:
        return "chronological"
    if mech_hits >= chrono_hits and mech_hits >= prob_hits and mech_hits > 0:
        return "mechanism_breakdown"
    if prob_hits > 0:
        return "problem_solution"

    # Default to chronological for human/historical topics, mechanism for technical
    return "chronological"


def get_structure_guidance(structure: NarrativeStructure) -> Dict[str, str]:
    """Return section-by-section structural directives for the chosen narrative arc."""
    if structure == "mechanism_breakdown":
        return {
            "hook": "Establish an observable mystery, paradox, or counter-intuitive effect in how the system functions.",
            "setup": "Lay out the macro-level architecture and starting baseline before examining internal components.",
            "middle": "Deconstruct the core engine: step-by-step mechanisms, component interactions, and causal chains.",
            "climax": "Reveal the critical breakthrough or interaction where the individual pieces synthesize into the full phenomenon.",
            "takeaway": "Connect the mechanical principle to broader technological, scientific, or modern applications.",
        }
    elif structure == "problem_solution":
        return {
            "hook": "Highlight a critical bottleneck, urgent crisis, or seemingly insurmountable dilemma.",
            "setup": "Explain why existing or traditional approaches failed and the escalating cost of the unresolved problem.",
            "middle": "Explore the arduous path of experimentation, engineering dead-ends, and developing the countermeasure.",
            "climax": "Detail the decisive solution or innovative paradigm shift that successfully overcame the bottleneck.",
            "takeaway": "Examine the permanent transformation, legacy, and what this breakthrough unlocks for the future.",
        }
    else:  # chronological
        return {
            "hook": "Introduce a striking historical paradox or dramatic contrast between past limitations and present reality.",
            "setup": "Establish foundational baseline conditions, historical context, and the pressing limitations of earlier eras.",
            "middle": "Chronicle the evolutionary developments, sequential trials, pivotal conflicts, and evolving attempts.",
            "climax": "Detail the decisive turning point, breakthrough trial, or historic moment where the transformation succeeded.",
            "takeaway": "Synthesize the enduring legacy, modern ramifications, and historical perspective for today's audience.",
        }


def get_multi_beat_guidance(
    section_type: str,
    target_words: int,
    structure: NarrativeStructure = "chronological",
) -> List[str]:
    """Return recommended multi-beat milestone directives calibrated to section role, word count, and structure.

    Ensures that each section's target length is supported by 2-4 concrete factual milestones / narrative beats,
    rather than a single superficial bullet.
    """
    stype = (section_type or "").lower().strip()

    if stype == "hook":
        return [
            "Establish the central puzzle, paradox, or counter-intuitive dilemma that halts the audience.",
            "Formulate the high-stakes inquiry or core question that this documentary resolves.",
        ]
    elif stype == "setup":
        if structure == "mechanism_breakdown":
            return [
                "Establish baseline operational state and macro-system boundaries before component breakdown.",
                "Identify foundational constraints, input variables, and the primary obstacle to efficiency.",
            ]
        elif structure == "problem_solution":
            return [
                "Establish historical or operational baseline under traditional approaches.",
                "Detail the compounding failures, bottlenecks, and escalating costs of the unresolved crisis.",
            ]
        else:  # chronological
            return [
                "Establish historical context, socio-technical baseline conditions, and pre-existing paradigms.",
                "Examine the specific operational limitations, bottlenecks, or friction points confronting innovators.",
            ]
    elif stype == "middle":
        # Middle sections carry the core explanatory weight; scale beats with target words
        if target_words >= 200:
            if structure == "mechanism_breakdown":
                return [
                    "Deconstruct the primary component architecture and initial mechanical inputs.",
                    "Trace the internal causal chain: secondary interactions, energy transfers, and feedback loops.",
                    "Examine critical technical trade-offs, failure modes, and stabilization countermeasures.",
                ]
            elif structure == "problem_solution":
                return [
                    "Investigate early experimental attempts, dead-ends, and why initial countermeasures failed.",
                    "Analyze the technical paradigm shift or alternative mechanism that challenged prevailing methods.",
                    "Document rigorous iterative testing, progressive refinement, and overcoming secondary obstacles.",
                ]
            else:  # chronological
                return [
                    "Document early incremental trials, foundational experiments, and initial setbacks.",
                    "Examine pivotal transitions, shifting methodologies, and intermediate breakthroughs.",
                    "Detail the convergence of evidence, emerging consensus, and preparatory work before the turning point.",
                ]
        else:
            if structure == "mechanism_breakdown":
                return [
                    "Deconstruct primary component interactions and initial mechanical inputs.",
                    "Detail sequential causal mechanisms and internal feedback loops.",
                ]
            elif structure == "problem_solution":
                return [
                    "Examine early experimental trials and the nature of initial obstacles.",
                    "Detail the innovative approach engineered to overcome the core bottleneck.",
                ]
            else:
                return [
                    "Detail sequential evolutionary trials and emerging intermediate discoveries.",
                    "Examine the critical transitional stage leading toward resolution.",
                ]
    elif stype == "climax":
        if structure == "mechanism_breakdown":
            return [
                "Reveal the definitive systemic synthesis where internal mechanisms produce the macro phenomenon.",
                "Highlight the empirical verification or observable breakthrough confirming the principle.",
            ]
        elif structure == "problem_solution":
            return [
                "Detail the decisive breakthrough test or paradigm realization that conquered the bottleneck.",
                "Demonstrate verifiable performance gains or systemic resolution achieved.",
            ]
        else:
            return [
                "Detail the decisive historic moment, pivotal trial, or turning point where breakthrough succeeded.",
                "Examine immediate verifiable outcomes and subsequent validation.",
            ]
    elif stype == "takeaway":
        return [
            "Synthesize lasting principles, broader implications, and modern real-world applications.",
            "Deliver a memorable closing perspective on how this transformation reshapes our understanding.",
        ]
    else:
        return [
            "Primary explanatory beat detailing core mechanisms.",
            "Secondary explanatory beat addressing implications and evidence.",
        ]


# ---------------------------------------------------------------------------
# Hook Architecture Formulas
# ---------------------------------------------------------------------------

def get_hook_formula_guidance(formula: HookFormula) -> str:
    """Return directives for crafting an opening hook under the chosen formula."""
    if formula == "bold_promise":
        return (
            "Hook Formula: Bold Statement / Value Promise.\n"
            "Make an intriguing, confident claim about what the viewer will understand or see transformed by the end. "
            "Stop the scroll immediately by challenging conventional wisdom. "
            "Do NOT open with generic greetings, channel intros, or topic restatements."
        )
    elif formula == "pattern_interrupt":
        return (
            "Hook Formula: Pattern Interrupt.\n"
            "Immediately shatter standard narrative expectations with a sharp contrast, shocking fact, or unexpected frame. "
            "Grab attention by defying typical explanations before establishing the core question. "
            "Do NOT open with generic greetings, channel intros, or topic restatements."
        )
    else:  # question_paradox
        return (
            "Hook Formula: Question / Paradox.\n"
            "Present a compelling paradox or counter-intuitive dilemma that seems impossible on its face. "
            "Establish curiosity by highlighting the clash between what seemed logical and what actually occurred. "
            "Do NOT open with generic greetings, channel intros, or topic restatements."
        )


# ---------------------------------------------------------------------------
# Tone Calibration & Spoken Voiceover
# ---------------------------------------------------------------------------

def get_tone_guidance(tone: ScriptTone) -> str:
    """Return stylistic directives calibrated to the requested voiceover tone."""
    if tone == "authoritative":
        return (
            "Tone: Authoritative & Rigorous.\n"
            "- Confident, measured documentary delivery with precise historical/technical terminology.\n"
            "- Ground explanations in concrete evidence and clear reasoning.\n"
            "- Maintain gravitas without sounding dry, pedantic, or detached."
        )
    elif tone == "casual":
        return (
            "Tone: Casual & Accessible.\n"
            "- Conversational, relatable spoken rhythm; talk with the viewer, not at them.\n"
            "- Use natural phrasing and relatable analogies to demystify complex concepts.\n"
            "- Keep energy warm and friendly while maintaining strict factual accuracy."
        )
    elif tone == "energetic":
        return (
            "Tone: Energetic & Dynamic.\n"
            "- Punchy sentences, vibrant vocabulary, and high momentum.\n"
            "- Drive pacing forward with dynamic rhetorical rhythm and vivid active verbs.\n"
            "- Avoid hyperbole or artificial hype; let genuine fascination drive the energy."
        )
    else:  # educational
        return (
            "Tone: Educational & Engaging.\n"
            "- Lucid, patient, and thoroughly illuminating spoken-word documentary voice.\n"
            "- Break down intricate concepts into intuitive, digestible mental models.\n"
            "- Balance intellectual curiosity with narrative warmth."
        )


# ---------------------------------------------------------------------------
# Anti-Filler & Factual Restraint Directives
# ---------------------------------------------------------------------------

ANTI_FILLER_DIRECTIVES = """
STRICT ANTI-FILLER & FACTUAL RESTRAINT DIRECTIVES:
1. ZERO GENERIC FILLER:
   - NEVER use vague, circular boilerplate (e.g. BANNED: "Step by step, the pieces connect",
     "When put to work, each component plays a role", "Now let us delve deeper into this journey",
     "The story of [topic] is one of wonder and mystery").
   - Every single spoken sentence must convey specific, concrete information unique to this topic.
2. LEGITIMATE NARRATIVE DEPTH WITHOUT FILLER:
   - To satisfy pacing and target duration legitimately without empty filler:
     * Break down causes, mechanisms, cognitive/biological steps, and real-world consequences in detail.
     * Contrast mechanisms with everyday examples, analogies (e.g. 'Think of it like...'), and alternative systems.
     * Explain *why* and *how* something happens, rather than only summarizing that it happened.
3. HONEST FACTUAL BOUNDARIES:
   - Do NOT invent historical anecdotes, fabricated quotes, imaginary dialogue, or fake statistics.
   - Distinct Narrative Layers: Clearly distinguish between factual assertions and creative framing.
     When using metaphors, analogies, or thought experiments, frame them explicitly as analogies
     (e.g. "Think of it like...", "Imagine a system where..."), never as literal historical facts.
4. RETRIEVED DOES NOT EQUAL VERIFIED:
   - When evidence from research is provided, cite declared evidence IDs accurately.
   - Declaring an evidence ID indicates an association with a retrieved source excerpt; it does
     NOT establish that the record exists in storage or that the claim is verified.
   - Do NOT fabricate evidence IDs, source URLs, or citations.
   - If an assertion is made that lacks supporting research, state it plainly without inventing evidence.
5. ACTIONABLE VISUAL STAGING AS GUIDANCE:
   - Pair narration with 2D animation staging suggestions in the 'notes' field where visual depiction
     clarifies the concept (e.g. '[Cutaway: diagram of cable core]', '[Character pose: explain, expression: focused]').
   - Visual cues are creative guidance to support story clarity; do NOT force artificial or repetitive
     staging onto sections where simple, clean presentation is more effective.
""".strip()


# ---------------------------------------------------------------------------
# Writing Policy Model
# ---------------------------------------------------------------------------

@dataclass
class WritingPolicy:
    """Configurable writing and storytelling policy for Script Builder."""

    narrative_structure: NarrativeStructure = "chronological"
    hook_formula: HookFormula = "question_paradox"
    tone: ScriptTone = "educational"
    words_per_minute: int = DEFAULT_WORDS_PER_MINUTE
    custom_directives: Optional[str] = None

    @classmethod
    def from_topic(
        cls,
        topic: str,
        context: Optional[str] = None,
        hook_formula: Optional[HookFormula] = None,
        tone: Optional[ScriptTone] = None,
        words_per_minute: int = DEFAULT_WORDS_PER_MINUTE,
        custom_directives: Optional[str] = None,
    ) -> "WritingPolicy":
        """Factory creating a WritingPolicy with topic-adapted narrative structure."""
        structure = detect_narrative_structure(topic, context)
        return cls(
            narrative_structure=structure,
            hook_formula=hook_formula or "question_paradox",
            tone=tone or "educational",
            words_per_minute=words_per_minute if words_per_minute > 0 else DEFAULT_WORDS_PER_MINUTE,
            custom_directives=custom_directives,
        )


# ---------------------------------------------------------------------------
# Prompt Builders
# ---------------------------------------------------------------------------

class ScriptPromptBuilder:
    """Modular system prompt builder generating prompts aligned with the Writing Policy."""

    def __init__(self, policy: Optional[WritingPolicy] = None) -> None:
        self.policy = policy or WritingPolicy()

    def build_outline_prompt(
        self,
        topic: str,
        target_duration_minutes: float,
        evidence_brief_text: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Construct system prompt for generating a topic-sensitive ScriptOutline."""
        wpm = self.policy.words_per_minute
        metrics = compute_duration_word_targets(target_duration_minutes, words_per_minute=wpm)
        guidance = get_structure_guidance(self.policy.narrative_structure)
        hook_guide = get_hook_formula_guidance(self.policy.hook_formula)
        tone_guide = get_tone_guidance(self.policy.tone)

        brief_section = ""
        if evidence_brief_text and evidence_brief_text.strip():
            brief_section = (
                f"\n--- RESEARCH EVIDENCE BRIEF ---\n"
                f"{evidence_brief_text.strip()}\n"
                f"--- END RESEARCH EVIDENCE BRIEF ---\n\n"
                f"EVIDENCE UTILIZATION RULES:\n"
                f"- Map relevant evidence IDs from the brief to the appropriate outline sections in 'evidence_ids'.\n"
                f"- Permitted evidence IDs strictly start with 'evi_' (e.g. 'evi_...'). Do NOT use question numbers or integers.\n"
                f"- Plan factual milestones informed by the retrieved evidence items in the brief.\n"
                f"- Citing an evidence ID in 'evidence_ids' declares a reference to a retrieved research excerpt; it does not certify absolute factual truth.\n"
                f"- Do NOT invent evidence IDs not present in the research brief.\n"
            )

        prompt = f"""You are a master documentary director and lead educational scriptwriter.
Your task is to create a comprehensive narrative outline for an explainer video on '{topic}'.

Video Specifications:
- Topic: {topic}
- Target Duration: ~{target_duration_minutes:.1f} minutes (soft creative guideline)
- Target Narration Words: ~{metrics['target_words']} words ({metrics['min_words']}–{metrics['max_words']} range at {wpm} WPM)
- Chosen Narrative Structure: {self.policy.narrative_structure} (use as structural guidance; adapt naturally to topic)

Storytelling Architecture:
{hook_guide}

{tone_guide}

Multi-Beat Narrative Outline Requirements:
- Each section's target words require 2–4 concrete factual milestones / narrative beats, NOT a single superficial bullet.
- Substantial sections (~150–250+ words, such as 'middle') MUST specify at least 3 distinct mechanical steps, causal transitions, or sequential developments to ensure sufficient explanatory depth.
- Each milestone must describe a specific, concrete concept or causal mechanism rather than a generic summary.

Section Roles & Structural Milestones:
1. "hook" (~{metrics['hook_words']} words target):
   {guidance['hook']}
2. "setup" (~{metrics['setup_words']} words target):
   {guidance['setup']}
3. "middle" (~{metrics['middle_words']} words target):
   {guidance['middle']}
4. "climax" (~{metrics['climax_words']} words target):
   {guidance['climax']}
5. "takeaway" (~{metrics['takeaway_words']} words target):
   {guidance['takeaway']}
{brief_section}
{ANTI_FILLER_DIRECTIVES}

Application Schema Validation Note:
Output will be validated strictly by Pydantic against domain contracts. Pacing and word targets are soft guidelines.

Output strictly valid JSON matching this schema:
{{
  "outline_id": "out_01",
  "project_id": "proj_default",
  "topic": "{topic}",
  "target_duration_minutes": {target_duration_minutes},
  "narrative_structure": "{self.policy.narrative_structure}",
  "sections": [
    {{
      "type": "hook",
      "heading": "Descriptive Hook Heading",
      "target_words": {metrics['hook_words']},
      "factual_milestones": ["Key paradox or opening milestone"],
      "visual_concept": "Actionable 2D staging concept for hook",
      "evidence_ids": []
    }},
    {{
      "type": "setup",
      "heading": "Descriptive Setup Heading",
      "target_words": {metrics['setup_words']},
      "factual_milestones": ["Baseline condition or historical starting point"],
      "visual_concept": "Actionable 2D staging concept for setup",
      "evidence_ids": []
    }},
    {{
      "type": "middle",
      "heading": "Descriptive Middle Heading",
      "target_words": {metrics['middle_words']},
      "factual_milestones": ["Core mechanical step 1", "Core mechanical step 2"],
      "visual_concept": "Actionable 2D staging concept for middle",
      "evidence_ids": []
    }},
    {{
      "type": "climax",
      "heading": "Descriptive Climax Heading",
      "target_words": {metrics['climax_words']},
      "factual_milestones": ["Decisive turning point or breakthrough insight"],
      "visual_concept": "Actionable 2D staging concept for climax",
      "evidence_ids": []
    }},
    {{
      "type": "takeaway",
      "heading": "Descriptive Takeaway Heading",
      "target_words": {metrics['takeaway_words']},
      "factual_milestones": ["Modern legacy or enduring principle"],
      "visual_concept": "Actionable 2D staging concept for takeaway",
      "evidence_ids": []
    }}
  ]
}}
"""
        if title:
            prompt += f"\nPreferred Title: {title}\n"
        if self.policy.custom_directives:
            prompt += f"\nCreator Directives:\n{self.policy.custom_directives}\n"

        return prompt

    def build_script_generation_prompt(
        self,
        topic: str,
        target_duration_minutes: float,
        outline_json: Optional[str] = None,
        evidence_brief_text: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """Construct system prompt for generating a complete, evidence-grounded Script draft."""
        wpm = self.policy.words_per_minute
        metrics = compute_duration_word_targets(target_duration_minutes, words_per_minute=wpm)
        guidance = get_structure_guidance(self.policy.narrative_structure)
        hook_guide = get_hook_formula_guidance(self.policy.hook_formula)
        tone_guide = get_tone_guidance(self.policy.tone)

        outline_context = ""
        if outline_json and outline_json.strip():
            outline_context = (
                f"\n--- NARRATIVE OUTLINE ROADMAP ---\n"
                f"{outline_json.strip()}\n"
                f"--- END NARRATIVE OUTLINE ROADMAP ---\n\n"
                f"Follow the sections, headings, target words, and factual milestones planned in the outline above.\n"
            )

        evidence_context = ""
        if evidence_brief_text and evidence_brief_text.strip():
            evidence_context = (
                f"\n--- RESEARCH EVIDENCE BRIEF ---\n"
                f"{evidence_brief_text.strip()}\n"
                f"--- END RESEARCH EVIDENCE BRIEF ---\n\n"
                f"GROUNDING & CLAIM ATTRIBUTION INSTRUCTIONS:\n"
                f"- Ground factual statements in the retrieved source excerpts from the evidence brief.\n"
                f"- Citing an evidence ID in 'evidence_ids' declares a reference to a retrieved source excerpt. "
                f"It does NOT imply that the claim has been storage-validated or substantively verified (which is assessed downstream).\n"
                f"- In the 'claims' array, list every discrete factual assertion made in the narration.\n"
                f"- Set 'claim_id' to unique IDs ('clm_01', 'clm_02', etc.).\n"
                f"- Set 'section_id' to the section where the assertion appears ('sec_01', 'sec_02', etc.).\n"
                f"- Reference declared evidence IDs in 'evidence_ids' (e.g. ['evi_01', 'evi_02']). If an assertion lacks retrieved evidence, leave 'evidence_ids' empty.\n"
                f"- Also populate 'claim_ids' on each section with the IDs of claims appearing in that section.\n"
                f"- Do NOT invent evidence IDs or URLs not present in the brief.\n"
            )

        section_breakdown = ""
        schema_sections_example = ""
        num_sections = 5

        if outline_json and outline_json.strip():
            try:
                parsed_outline = json.loads(outline_json)
                outline_sections = parsed_outline.get("sections", [])
                if outline_sections:
                    num_sections = len(outline_sections)
                    breakdown_lines = [f"Section Breakdown (planned {num_sections} sections from outline):"]
                    example_secs = []
                    for idx, s in enumerate(outline_sections):
                        s_id = f"sec_{idx+1:02d}"
                        s_type = s.get("type", "middle")
                        s_head = s.get("heading", f"Section {idx+1}")
                        s_words = s.get("target_words", 100)
                        breakdown_lines.append(f"{idx+1}. \"{s_type}\" (ID: '{s_id}', Heading: \"{s_head}\", ~{s_words} words target)")
                        example_secs.append(
                            f'    {{\n'
                            f'      "id": "{s_id}",\n'
                            f'      "type": "{s_type}",\n'
                            f'      "heading": "{s_head}",\n'
                            f'      "narration": "Spoken voiceover narration for {s_type}...",\n'
                            f'      "notes": "Actionable 2D staging cues...",\n'
                            f'      "claim_ids": ["clm_01"]\n'
                            f'    }}'
                        )
                    section_breakdown = "\n".join(breakdown_lines)
                    schema_sections_example = ",\n".join(example_secs)
            except Exception:
                pass

        if not section_breakdown:
            section_breakdown = f"""Section Breakdown (standard 5 sections):
1. "hook" (~{metrics['hook_words']} words):
   {guidance['hook']}
2. "setup" (~{metrics['setup_words']} words):
   {guidance['setup']}
3. "middle" (~{metrics['middle_words']} words):
   {guidance['middle']}
4. "climax" (~{metrics['climax_words']} words):
   {guidance['climax']}
5. "takeaway" (~{metrics['takeaway_words']} words):
   {guidance['takeaway']}"""
            schema_sections_example = f"""    {{
      "id": "sec_01",
      "type": "hook",
      "heading": "Descriptive Hook Headline",
      "narration": "Spoken voiceover text seizing curiosity with the dilemma...",
      "notes": "Actionable 2D animation staging cues and character poses...",
      "claim_ids": ["clm_01"]
    }},
    {{
      "id": "sec_02",
      "type": "setup",
      "heading": "Descriptive Setup Headline",
      "narration": "Spoken voiceover text establishing baseline conditions...",
      "notes": "Actionable 2D animation staging cues and character poses...",
      "claim_ids": []
    }},
    {{
      "id": "sec_03",
      "type": "middle",
      "heading": "Descriptive Middle Headline",
      "narration": "Spoken voiceover text unpacking step-by-step mechanisms...",
      "notes": "Actionable 2D animation staging cues and character poses...",
      "claim_ids": []
    }},
    {{
      "id": "sec_04",
      "type": "climax",
      "heading": "Descriptive Climax Headline",
      "narration": "Spoken voiceover text detailing the breakthrough turning point...",
      "notes": "Actionable 2D animation staging cues and character poses...",
      "claim_ids": []
    }},
    {{
      "id": "sec_05",
      "type": "takeaway",
      "heading": "Descriptive Takeaway Headline",
      "narration": "Spoken voiceover text synthesizing modern legacy and significance...",
      "notes": "Actionable 2D animation staging cues and character poses...",
      "claim_ids": []
    }}"""

        prompt = f"""You are a senior educational scriptwriter for engaging animated explainer documentaries.
Your goal is to write a compelling, deeply informative, and fact-rich explainer script for '{topic}'.

Video Specifications:
- Topic: {topic}
- Target Duration: ~{target_duration_minutes:.1f} minutes (soft creative guideline)
- Pacing & Length: Aim for ~{metrics['target_words']} spoken words ({metrics['min_words']}–{metrics['max_words']} range at {wpm} WPM).
  Duration is a flexible guideline. Do NOT pad with filler to reach a word count, and do NOT truncate meaningful content.
- Chosen Narrative Structure: {self.policy.narrative_structure} (structural guidance; adapt naturally to topic)

Storytelling Architecture:
{hook_guide}

{tone_guide}

{section_breakdown}
{outline_context}{evidence_context}
{ANTI_FILLER_DIRECTIVES}

Application Schema Validation Note:
Output will be validated strictly by Pydantic against domain contracts.
Every section must have a unique 'id' ('sec_01' through 'sec_{num_sections:02d}').
Every entry in 'claims' must specify 'claim_text', 'section_id', and 'evidence_ids'.
Claims must reference valid section 'id's.

Output strictly valid JSON matching this schema:
{{
  "title": "Descriptive, engaging script title",
  "topic": "{topic}",
  "target_duration_minutes": {target_duration_minutes},
  "sections": [
{schema_sections_example}
  ],
  "claims": [
    {{
      "claim_id": "clm_01",
      "claim_text": "Discrete factual assertion made in the narration",
      "section_id": "sec_01",
      "evidence_ids": ["evi_01"]
    }}
  ]
}}
"""
        if title:
            prompt += f"\nPreferred Title: {title}\n"
        if self.policy.custom_directives:
            prompt += f"\nCreator Directives:\n{self.policy.custom_directives}\n"

        return prompt

    def build_script_revision_prompt(
        self,
        current_script_json: str,
        instruction: str,
        conversation_context: Optional[str] = None,
        evidence_brief_text: Optional[str] = None,
    ) -> str:
        """Construct system prompt for revising a Script draft based on user instructions."""
        wpm = self.policy.words_per_minute
        tone_guide = get_tone_guidance(self.policy.tone)

        brief_context = ""
        if evidence_brief_text and evidence_brief_text.strip():
            brief_context = (
                f"\n--- RESEARCH EVIDENCE BRIEF ---\n"
                f"{evidence_brief_text.strip()}\n"
                f"--- END RESEARCH EVIDENCE BRIEF ---\n\n"
                f"When modifying factual passages, reference retrieved evidence items where applicable.\n"
            )

        prompt = f"""You are an expert scriptwriter revising an educational explainer video script based on creator feedback.

Current Script:
{current_script_json}

Creator Revision Instruction:
"{instruction}"

Context / Recent Conversation History:
{conversation_context or "No previous revision history."}

{tone_guide}

Revision Guidelines:
1. FAITHFULLY APPLY INSTRUCTION: Address the creator's exact request (e.g. adjusting explanations, refining tone, expanding depth).
2. PRESERVE VALUABLE MATERIAL: Retain all existing accurate explanations, well-written passages, and claim mappings that are NOT targeted by the instruction. Do NOT unnecessarily rewrite untouched sections.
3. REVISION INTEGRITY & CLAIM MAPPING RETENTION:
   - Untouched sections: Retain existing claim IDs, section associations, and declared evidence references.
   - Materially changed factual propositions: If a revision modifies a factual proposition (e.g. changing dates, numbers, causal mechanisms, or key conclusions), that proposition is altered. It MUST NOT automatically inherit the previous claim's evidence linkage merely because the section or claim ID remains the same. Only cite evidence IDs if the new wording is directly supported by retrieved evidence in the brief; otherwise leave 'evidence_ids' empty.
   - New factual claims: If new factual assertions are introduced, create a new claim entry. If no retrieved evidence supports it, leave 'evidence_ids' empty.
4. NO FILLER: Never replace concrete explanations with vague summaries or filler.
{brief_context}
{ANTI_FILLER_DIRECTIVES}

Application Schema Validation Note:
Output will be validated strictly by Pydantic against domain contracts. Ensure section IDs and claim section_id links remain consistent.

Output strictly valid JSON matching the 5-section Script schema (hook, setup, middle, climax, takeaway).
"""
        return prompt

    def build_single_section_prompt(
        self,
        topic: str,
        target_duration_minutes: float,
        section_index: int,
        total_sections: int,
        section_type: str,
        heading: str,
        target_words: int,
        factual_milestones: List[str],
        visual_concept: Optional[str] = None,
        evidence_brief_text: Optional[str] = None,
        preceding_sections_summary: Optional[str] = None,
        permitted_evidence_ids: Optional[List[str]] = None,
        is_unresearched: bool = False,
    ) -> str:
        """Construct prompt for generating a single, richly developed section of a script draft."""
        tone_guide = get_tone_guidance(self.policy.tone)
        guidance = get_structure_guidance(self.policy.narrative_structure)
        role_description = guidance.get(section_type, "Advance the core narrative with factual clarity.")

        milestones_formatted = "\n".join(f"  * {m}" for m in (factual_milestones or ["Explain the core concept in depth."]))

        continuity_section = ""
        if preceding_sections_summary and preceding_sections_summary.strip():
            continuity_section = (
                f"\n--- NARRATIVE CONTINUITY & CONTEXT ---\n"
                f"Preceding sections have already covered:\n"
                f"{preceding_sections_summary.strip()}\n"
                f"Instruction: Ensure a seamless narrative transition from the preceding section. "
                f"Propel the story forward without repeating explanations or introductory throat-clearing.\n"
                f"--- END NARRATIVE CONTINUITY ---\n"
            )

        evidence_section = ""
        if is_unresearched:
            evidence_section = (
                f"\n--- UNRESEARCHED MODE DIRECTIVE ---\n"
                f"This section is being generated in UNRESEARCHED mode without external research grounding.\n"
                f"- Do NOT cite any evidence IDs in 'evidence_ids' (must be empty list [] for all claims).\n"
                f"- Do NOT hallucinate sources or evidence identifiers.\n"
                f"--- END UNRESEARCHED MODE DIRECTIVE ---\n"
            )
        else:
            brief_snippet = ""
            if evidence_brief_text and evidence_brief_text.strip():
                brief_snippet = f"\nRelevant Evidence Excerpts:\n{evidence_brief_text.strip()}\n"

            permitted = sorted(list(set(permitted_evidence_ids or [])))
            evidence_section = (
                f"\n--- RESEARCH EVIDENCE & GROUNDING ---\n"
                f"{brief_snippet}"
                f"GROUNDING & CLAIM ATTRIBUTION INSTRUCTIONS:\n"
                f"- Ground factual statements in the provided source excerpts.\n"
                f"- Permitted evidence IDs: {permitted}\n"
                f"- In the 'claims' array, list every discrete factual assertion made in this section's narration.\n"
                f"- For each claim, set 'claim_text' and reference declared evidence IDs in 'evidence_ids' (e.g. ['evi_...']).\n"
                f"- Only cite evidence IDs from the permitted list above. If an assertion lacks retrieved evidence, leave 'evidence_ids' as [].\n"
                f"- Citing an evidence ID declares a reference to a retrieved research excerpt; it does not certify absolute factual truth.\n"
                f"- Do NOT invent evidence IDs not listed above.\n"
                f"--- END RESEARCH EVIDENCE & GROUNDING ---\n"
            )

        prompt = f"""You are a senior educational scriptwriter for engaging animated explainer documentaries.
Write spoken voiceover narration for Section {section_index + 1} of {total_sections} of a script on '{topic}'.

Section Specifications:
- Overall Topic: {topic}
- Section Role: "{section_type}" ({role_description})
- Section Heading: "{heading}"
- Target Word Count: ~{target_words} spoken words (aim for substantial explanatory depth matching ~{target_words} words; do not abbreviate or write superficially).
- Section Visual Staging Concept: {visual_concept or 'Actionable 2D staging cues'}

Factual Milestones & Narrative Beats to Cover:
{milestones_formatted}
{continuity_section}
{tone_guide}
{evidence_section}
{ANTI_FILLER_DIRECTIVES}

Application Schema Validation Note:
Output will be validated strictly by Pydantic against GeneratedSectionPayload.
Ensure 'heading' and 'narration' are non-empty. Every claim must have 'claim_text' and 'evidence_ids'.

Output strictly valid JSON matching this schema:
{{
  "heading": "{heading}",
  "narration": "Full spoken voiceover text covering the milestones in ~{target_words} words...",
  "notes": "Actionable 2D animation staging cues and character poses...",
  "claims": [
    {{
      "claim_id": "clm_01",
      "claim_text": "Discrete factual assertion made in the narration",
      "evidence_ids": []
    }}
  ]
}}
"""
        return prompt

    def build_section_expansion_prompt(
        self,
        topic: str,
        section_type: str,
        heading: str,
        target_words: int,
        current_narration: str,
        current_word_count: int,
        factual_milestones: List[str],
        evidence_brief_text: Optional[str] = None,
        permitted_evidence_ids: Optional[List[str]] = None,
        is_unresearched: bool = False,
    ) -> str:
        """Construct prompt for targeted expansion of an underdeveloped script section."""
        tone_guide = get_tone_guidance(self.policy.tone)
        milestones_formatted = "\n".join(f"  * {m}" for m in (factual_milestones or ["Explain the core concept in depth."]))

        evidence_section = ""
        if is_unresearched:
            evidence_section = (
                f"\n--- UNRESEARCHED MODE DIRECTIVE ---\n"
                f"Do NOT cite any evidence IDs in 'evidence_ids' (must be empty list [] for all claims).\n"
            )
        elif permitted_evidence_ids:
            permitted = sorted(list(set(permitted_evidence_ids)))
            evidence_section = (
                f"\n--- PERMITTED EVIDENCE IDS ---\n"
                f"Permitted evidence IDs: {permitted}\n"
                f"If new assertions are made, cite only permitted evidence IDs, or leave 'evidence_ids' as [].\n"
            )

        prompt = f"""You are a senior educational scriptwriter expanding an underdeveloped script section.

The current draft narration for section '{heading}' is significantly shorter than required:
- Current Length: {current_word_count} words
- Target Length: ~{target_words} words
- Section Type: {section_type}
- Overall Topic: {topic}

Current Narration:
\"\"\"
{current_narration}
\"\"\"

Required Factual Milestones:
{milestones_formatted}

EXPANSION DIRECTIVES:
1. Deepen the explanation by unpacking specific causal mechanisms, step-by-step physical or historical processes, and concrete examples.
2. Maintain natural spoken voiceover flow and narrative propulsion.
3. STRICT ANTI-FILLER RULE: Do NOT pad with repetitive summaries, rhetorical throat-clearing, or generic adjectives. Every added sentence must convey substantive informational value.
4. Aim for ~{target_words} spoken words total.
{tone_guide}
{evidence_section}
{ANTI_FILLER_DIRECTIVES}

Application Schema Validation Note:
Output will be validated strictly by Pydantic against GeneratedSectionPayload.

Output strictly valid JSON matching this schema:
{{
  "heading": "{heading}",
  "narration": "Expanded spoken voiceover text providing rich explanatory depth (~{target_words} words)...",
  "notes": "Actionable 2D staging cues...",
  "claims": [
    {{
      "claim_id": "clm_01",
      "claim_text": "Discrete factual assertion made in the narration",
      "evidence_ids": []
    }}
  ]
}}
"""
        return prompt
