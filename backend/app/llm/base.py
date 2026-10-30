"""Base interface and error hierarchy for LLM providers."""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel

from backend.app.domain.script import (
    DEFAULT_WORDS_PER_MINUTE,
    Script,
    ScriptSection,
    compute_duration_word_targets,
    count_words,
    populate_script_duration_metrics,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Base exception for all LLM provider failures."""


class LLMUnavailableError(LLMProviderError):
    """Raised when the LLM service endpoint is unreachable or unavailable."""


class LLMResponseMalformedError(LLMProviderError):
    """Raised when the LLM returns unparseable or schema-invalid JSON."""


SCRIPT_GENERATION_SYSTEM_PROMPT = """You are a senior educational scriptwriter for engaging animated explainer documentaries.
Your goal is to write a compelling, deeply informative, and fact-rich explainer script that thoroughly explains the topic.

Video Specifications:
- Topic: {topic}
- Target video duration: approximately {target_duration} minutes (use this as a soft creative guideline for pacing and depth, not a rigid constraint).
- Pacing & Length Guidelines:
  At standard explainer narration pace (120–150 words per minute), aim for approximately {min_words} to {max_words} total spoken narration words across the entire script (~{target_words} words).
  Treat this as a flexible planning range. A good script may run slightly shorter or longer when the subject requires it, but it must have sufficient depth to justify the target runtime.
  Do NOT add repetitive filler merely to reach a word count, and do NOT truncate meaningful content to force an exact length.

Narrative Arc & Section Breakdown (exactly 5 sections):
1. "hook" (~{hook_words} words):
   - Headline and narration must establish a specific paradox, historical dilemma, or counter-intuitive puzzle to immediately seize curiosity.
   - Do NOT give away the entire answer immediately; set up the core mystery.
2. "setup" (~{setup_words} words):
   - Establish necessary foundational context, historical starting conditions, or the initial system before the breakthrough.
   - Explain why previous or traditional methods were insufficient.
3. "middle" (~{middle_words} words):
   - The primary explanatory engine of the script (longest and most detailed section).
   - Walk through step-by-step mechanisms, causes, concrete examples, obstacles, and evolutionary developments in thorough detail.
   - Explain HOW and WHY things happened, avoiding high-level hand-waving.
4. "climax" (~{climax_words} words):
   - Deliver the key turning point, decisive invention, or breakthrough insight that revolutionized the system.
   - Explain precisely what made the breakthrough work and how it resolved the dilemmas described in the setup and middle.
5. "takeaway" (~{takeaway_words} words):
   - Synthesize the lasting significance and legacy for modern times.
   - Resolve the opening question and leave the audience with a memorable, thought-provoking perspective.

Content & Tone Standards:
- Write in a natural, engaging spoken documentary voiceover style.
- NO GENERIC FILLER: Never write vague, circular platitudes (e.g. NEVER write "When [topic] is put to work, each component plays a role" or "Step by step, the pieces connect"). Every sentence must convey substantive information specific to '{topic}'.
- Avoid abrupt transitions; ensure each section transitions logically into the next.
- Provide vivid 2D animation cues, visual metaphors, and staging suggestions in the 'notes' field for each section.

Output strictly valid JSON matching this schema:
{{
  "title": "Descriptive, engaging script title",
  "topic": "{topic}",
  "target_duration_minutes": {target_duration},
  "sections": [
    {{
      "id": "sec_01",
      "type": "hook",
      "heading": "Descriptive Hook Headline",
      "narration": "Full spoken narration establishing the specific dilemma or puzzle...",
      "notes": "Visual and animation staging cues..."
    }},
    {{
      "id": "sec_02",
      "type": "setup",
      "heading": "Descriptive Setup Headline",
      "narration": "Full spoken narration setting the historical or foundational baseline...",
      "notes": "Visual and animation staging cues..."
    }},
    {{
      "id": "sec_03",
      "type": "middle",
      "heading": "Descriptive Middle Headline",
      "narration": "Substantial, detailed explanation of the core mechanisms and developments...",
      "notes": "Visual and animation staging cues..."
    }},
    {{
      "id": "sec_04",
      "type": "climax",
      "heading": "Descriptive Climax Headline",
      "narration": "Full spoken narration detailing the breakthrough turning point...",
      "notes": "Visual and animation staging cues..."
    }},
    {{
      "id": "sec_05",
      "type": "takeaway",
      "heading": "Descriptive Takeaway Headline",
      "narration": "Full spoken narration synthesizing the modern legacy and conclusion...",
      "notes": "Visual and animation staging cues..."
    }}
  ]
}}
"""

SCRIPT_EXPANSION_SYSTEM_PROMPT = """You are a senior educational scriptwriter deepening an explainer documentary script.

The initial draft is conceptually on-topic, but at {current_word_count} spoken narration words it is substantially too brief for a {target_duration}-minute target video (soft planning range is ~{min_words} to {max_words} words at 120–150 words per minute).

Topic: {topic}

Current Draft:
{current_script_json}

Expansion Directives:
1. PRESERVE VALUABLE MATERIAL: Retain the existing accurate facts, structure, strong phrases, and section topics. Do NOT discard good content.
2. DEEPEN EXPLANATIONS: Elaborate thoroughly on the underlying mechanisms, cause-and-effect sequences, concrete historical or scientific examples, and pivotal details that were summarized too quickly.
3. EXPAND KEY SECTIONS:
   - "setup": Elaborate on the initial conditions, historical context, and limitations of early methods (~{setup_words} words).
   - "middle": Substantially unpack the mechanical steps, trials, failures, and evolution (~{middle_words} words).
   - "climax": Unpack the breakthrough moment, why it succeeded, and the direct transformation it caused (~{climax_words} words).
   - "hook" & "takeaway": Enrich and polish without becoming overly verbose (~{hook_words} and ~{takeaway_words} words).
4. ABSOLUTELY NO FILLER: Do NOT add generic padding, fluff, rhetorical repetition, or vague restatements. Every sentence must convey substantive information specific to '{topic}'.
5. VALID SCHEMA: Return valid JSON matching the 5-section Script schema (hook, setup, middle, climax, takeaway) with non-empty headings, expanded narration, and descriptive visual staging notes.
"""

SCRIPT_REVISION_SYSTEM_PROMPT = """You are an expert scriptwriter revising an educational explainer video script based on conversational user instructions.

Current Script:
{current_script_json}

User Revision Instruction:
"{instruction}"

Context / Conversation History:
{conversation_context}

Target Duration: approximately {target_duration} minutes (~{min_words}–{max_words} words soft planning range).

Revision Guidelines:
1. FAITHFULLY APPLY FEEDBACK: Carefully address the user's instructions (e.g. deepening a specific concept, adjusting pacing or length, shifting tone, adding vivid metaphors, expanding explanations).
2. PRESERVE GOOD MATERIAL: Retain all existing accurate facts, explanations, and well-written sections that are not targeted by the user's instruction. Do NOT unnecessarily rewrite or truncate untouched sections.
3. MAINTAIN SUBSTANTIVE DEPTH: Never replace detailed historical or technical explanations with vague summaries or filler. Keep narration spoken-word friendly, accurate, and rich in content.
4. PACING & EXPANSION: If the instruction asks to expand explanations or adjust duration, add concrete cause-and-effect mechanisms and real-world examples rather than fluff.
5. VALID STRUCTURE: Ensure all 5 narrative sections remain present (hook, setup, middle, climax, takeaway), each with non-empty headings, full narration, and visual staging notes.
6. Output valid JSON matching the Script schema.
"""


class LLMProvider(ABC):
    """Abstract interface for LLM client adapters per TechSpec Section 8."""

    def __init__(self, words_per_minute: int = DEFAULT_WORDS_PER_MINUTE) -> None:
        self.words_per_minute = words_per_minute if words_per_minute > 0 else DEFAULT_WORDS_PER_MINUTE

    @abstractmethod
    def generate_json(self, prompt: str, schema: Type[T]) -> T:
        """Generate structured output adhering to the provided Pydantic schema."""

    def generate_script(
        self,
        topic: str,
        target_duration_minutes: float = 5.0,
        title: Optional[str] = None,
    ) -> Script:
        """Generate a complete structured Script draft from a topic."""
        metrics = compute_duration_word_targets(
            target_duration_minutes, words_per_minute=self.words_per_minute
        )
        prompt = SCRIPT_GENERATION_SYSTEM_PROMPT.format(
            topic=topic,
            target_duration=target_duration_minutes,
            min_words=metrics["min_words"],
            max_words=metrics["max_words"],
            target_words=metrics["target_words"],
            hook_words=metrics["hook_words"],
            setup_words=metrics["setup_words"],
            middle_words=metrics["middle_words"],
            climax_words=metrics["climax_words"],
            takeaway_words=metrics["takeaway_words"],
        )
        if title:
            prompt += f"\nPreferred Title: {title}"
        script = self.generate_json(prompt, Script)

        # Enforce consistency
        script.topic = topic
        script.target_duration_minutes = target_duration_minutes
        if title:
            script.title = title

        # Check if draft is substantially under-length for the requested duration
        actual_words = sum(count_words(s.narration) for s in script.sections)
        min_expected_words = metrics["min_words"]
        underlength_threshold = int(0.60 * min_expected_words)

        if actual_words < underlength_threshold:
            logger.info(
                "Draft has %d words (< %d threshold for %.1f min). Executing bounded expansion step...",
                actual_words,
                underlength_threshold,
                target_duration_minutes,
            )
            try:
                expanded = self.expand_script(
                    current_script=script,
                    topic=topic,
                    target_duration_minutes=target_duration_minutes,
                )
                expanded_words = sum(count_words(s.narration) for s in expanded.sections)
                if expanded_words > actual_words:
                    logger.info(
                        "Bounded expansion increased narration from %d to %d words.",
                        actual_words,
                        expanded_words,
                    )
                    script = expanded
                else:
                    logger.warning(
                        "Bounded expansion did not increase word count (%d vs %d); retaining initial draft.",
                        expanded_words,
                        actual_words,
                    )
            except Exception as exp_err:
                logger.warning(
                    "Bounded expansion step encountered error: %s; retaining initial draft.",
                    exp_err,
                )

        populate_script_duration_metrics(script, words_per_minute=self.words_per_minute)
        return script

    def expand_script(
        self,
        current_script: Script,
        topic: str,
        target_duration_minutes: float,
    ) -> Script:
        """Expand an under-length script draft to meet target narrative depth."""
        metrics = compute_duration_word_targets(
            target_duration_minutes, words_per_minute=self.words_per_minute
        )
        current_words = sum(count_words(s.narration) for s in current_script.sections)
        prompt = SCRIPT_EXPANSION_SYSTEM_PROMPT.format(
            topic=topic,
            target_duration=target_duration_minutes,
            current_word_count=current_words,
            min_words=metrics["min_words"],
            max_words=metrics["max_words"],
            target_words=metrics["target_words"],
            hook_words=metrics["hook_words"],
            setup_words=metrics["setup_words"],
            middle_words=metrics["middle_words"],
            climax_words=metrics["climax_words"],
            takeaway_words=metrics["takeaway_words"],
            current_script_json=current_script.model_dump_json(indent=2),
        )
        expanded = self.generate_json(prompt, Script)
        expanded.topic = topic
        expanded.target_duration_minutes = target_duration_minutes
        expanded.project_id = current_script.project_id
        if current_script.title:
            expanded.title = current_script.title
        populate_script_duration_metrics(expanded, words_per_minute=self.words_per_minute)
        return expanded

    def revise_script(
        self,
        current_script: Script,
        instruction: str,
        conversation_context: Optional[str] = None,
    ) -> Script:
        """Revise an existing Script draft based on a natural-language instruction."""
        target_duration = current_script.target_duration_minutes or 5.0
        metrics = compute_duration_word_targets(
            target_duration, words_per_minute=self.words_per_minute
        )
        prompt = SCRIPT_REVISION_SYSTEM_PROMPT.format(
            current_script_json=current_script.model_dump_json(indent=2),
            instruction=instruction,
            conversation_context=conversation_context or "No previous revision history.",
            target_duration=target_duration,
            min_words=metrics["min_words"],
            max_words=metrics["max_words"],
        )
        revised = self.generate_json(prompt, Script)
        # Preserve original topic & project identity
        revised.topic = current_script.topic
        revised.target_duration_minutes = target_duration
        revised.project_id = current_script.project_id
        if current_script.research_run_id and not revised.research_run_id:
            revised.research_run_id = current_script.research_run_id
        if getattr(current_script, "outline", None) and not getattr(revised, "outline", None):
            revised.outline = current_script.outline
        if getattr(current_script, "evidence_brief_path", None) and not getattr(revised, "evidence_brief_path", None):
            revised.evidence_brief_path = current_script.evidence_brief_path
        populate_script_duration_metrics(revised, words_per_minute=self.words_per_minute)
        return revised
