"""Deterministic, offline mock LLM provider for testing and local development."""

import copy
import re
from typing import Any, Dict, List, Optional, Type, TypeVar
import uuid

from pydantic import BaseModel

from backend.app.domain.fact_check import (
    BatchFactCheckResponsePayload,
    ClaimAssessmentPayload,
)

from backend.app.domain.research import (
    GeneratedPlanPayload,
    PlannedQuery,
    PlannedQueryPayload,
    ResearchPlan,
)
from backend.app.domain.script import (
    DEFAULT_WORDS_PER_MINUTE,
    GeneratedOutlinePayload,
    GeneratedScriptPayload,
    OutlineSectionPayload,
    Script,
    ScriptClaimPayload,
    ScriptSection,
    ScriptSectionPayload,
    count_words,
    populate_script_duration_metrics,
)
from backend.app.llm.base import LLMProvider, LLMProviderError, LLMResponseMalformedError

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider(LLMProvider):
    """Configurable in-memory mock LLM provider for deterministic tests."""

    def __init__(
        self,
        words_per_minute: int = DEFAULT_WORDS_PER_MINUTE,
        enable_auto_expansion: bool = False,
    ) -> None:
        super().__init__(words_per_minute=words_per_minute)
        self.enable_auto_expansion = enable_auto_expansion
        self.calls: List[Dict[str, Any]] = []
        self._preset_response: Optional[Any] = None
        self._preset_error: Optional[Exception] = None
        self._raw_response: Optional[Any] = None
        self._response_queue: List[Any] = []

    def set_response(self, response: Any) -> None:
        """Set a preset response (Script or dict) to be returned on subsequent calls."""
        self._preset_response = response

    def set_error(self, error: Exception) -> None:
        """Configure an exception to be raised on the next call."""
        self._preset_error = error

    def set_raw_response(self, raw: Any) -> None:
        """Set raw response data to test schema validation / malformed JSON handling."""
        self._raw_response = raw

    def queue_response(self, response_or_error: Any) -> None:
        """Queue a sequential response or exception for subsequent calls."""
        self._response_queue.append(response_or_error)

    def queue_responses(self, items: List[Any]) -> None:
        """Queue multiple sequential responses or exceptions."""
        self._response_queue.extend(items)

    def clear(self) -> None:
        """Reset mock state and call logs."""
        self.calls.clear()
        self._preset_response = None
        self._preset_error = None
        self._raw_response = None
        self._response_queue.clear()

    def generate_json(self, prompt: str, schema: Type[T]) -> T:
        """Generate structured output adhering to schema."""
        self.calls.append({"action": "generate_json", "prompt": prompt, "schema": schema.__name__})

        # 1. Check response queue first
        if self._response_queue:
            item = self._response_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, schema):
                return item
            if isinstance(item, dict):
                try:
                    return schema.model_validate(item)
                except Exception as exc:
                    raise LLMResponseMalformedError(f"Mock queued output failed schema validation: {exc}") from exc
            if isinstance(item, str):
                import json
                try:
                    parsed = json.loads(item)
                    return schema.model_validate(parsed)
                except Exception as exc:
                    raise LLMResponseMalformedError(f"Mock queued string failed validation: {exc}") from exc
            raise LLMResponseMalformedError(f"Mock queued response type {type(item)} invalid for {schema}")

        # 2. Check preset error
        if self._preset_error:
            err = self._preset_error
            self._preset_error = None
            raise err

        # 3. Check raw response
        if self._raw_response is not None:
            raw = self._raw_response
            self._raw_response = None
            if isinstance(raw, Exception):
                raise raw
            try:
                return schema.model_validate(raw)
            except Exception as exc:
                raise LLMResponseMalformedError(f"Mock raw output failed schema validation: {exc}") from exc

        # 4. Check preset response
        if self._preset_response is not None:
            if isinstance(self._preset_response, schema):
                resp = self._preset_response
                self._preset_response = None
                return resp
            if isinstance(self._preset_response, Script) and (schema is GeneratedScriptPayload or schema.__name__ == "GeneratedScriptPayload"):
                resp = self._preset_response
                self._preset_response = None
                return schema(
                    title=resp.title,
                    target_duration_minutes=resp.target_duration_minutes,
                    sections=[
                        ScriptSectionPayload(
                            id=s.id,
                            type=s.type,
                            heading=s.heading,
                            narration=s.narration,
                            notes=s.notes,
                        )
                        for s in resp.sections
                    ],
                )
            if isinstance(self._preset_response, dict):
                resp = self._preset_response
                self._preset_response = None
                return schema.model_validate(resp)
            if not (isinstance(self._preset_response, Script) and (schema is GeneratedOutlinePayload or schema.__name__ == "GeneratedOutlinePayload")):
                resp = self._preset_response
                self._preset_response = None
                raise LLMResponseMalformedError(f"Mock response type {type(resp)} invalid for {schema}")

        # 5. Fallback deterministic generator for research plan models
        if schema is GeneratedPlanPayload or schema.__name__ == "GeneratedPlanPayload":
            return schema(
                research_objective="Investigate foundational mechanisms, historical context, and critical transitions.",
                research_questions=[
                    "What were the initial limitations or bottlenecks that necessitated this development?",
                    "What specific mechanisms and transitional stages enabled the breakthrough?",
                    "What empirical evidence verifies the modern impact and lasting significance?",
                ],
                planned_queries=[
                    PlannedQueryPayload(
                        query_id="qry_01",
                        query_text="historical origins and early mechanisms",
                        purpose="Identify initial conditions and early historical bottlenecks",
                        target_question="What were the initial limitations or bottlenecks that necessitated this development?",
                        tool_category="web_search",
                    ),
                    PlannedQueryPayload(
                        query_id="qry_02",
                        query_text="breakthrough mechanisms and innovations",
                        purpose="Document the exact sequence of technical or cultural innovations",
                        target_question="What specific mechanisms and transitional stages enabled the breakthrough?",
                        tool_category="web_search",
                    ),
                    PlannedQueryPayload(
                        query_id="qry_03",
                        query_text="modern impact empirical evidence",
                        purpose="Verify modern statistics and legacy implications",
                        target_question="What empirical evidence verifies the modern impact and lasting significance?",
                        tool_category="web_search",
                    ),
                ],
                approved_tool_categories=["web_search", "web_extract"],
            )

        # 6. Fallback deterministic generator for outline payloads
        if schema is GeneratedOutlinePayload or schema.__name__ == "GeneratedOutlinePayload":
            return schema(
                narrative_structure="chronological",
                sections=[
                    OutlineSectionPayload(
                        type="hook",
                        heading="The Unsolved Puzzle",
                        target_words=85,
                        factual_milestones=["A compelling paradox opens the investigation"],
                        visual_concept="A solitary pendulum swings against a dark void",
                        evidence_ids=[],
                    ),
                    OutlineSectionPayload(
                        type="setup",
                        heading="Foundational Context",
                        target_words=140,
                        factual_milestones=["Historical baseline established with evidence"],
                        visual_concept="Map diagram showing key trade and research centers",
                        evidence_ids=[],
                    ),
                    OutlineSectionPayload(
                        type="middle",
                        heading="The Breakthrough Mechanism",
                        target_words=250,
                        factual_milestones=["First key mechanism verified", "Second developmental milestone"],
                        visual_concept="Exploded schematic view of the internal mechanism",
                        evidence_ids=[],
                    ),
                    OutlineSectionPayload(
                        type="climax",
                        heading="The Turning Point",
                        target_words=140,
                        factual_milestones=["The critical discovery that changed the outcome"],
                        visual_concept="High-contrast motion path tracking dynamic particles",
                        evidence_ids=[],
                    ),
                    OutlineSectionPayload(
                        type="takeaway",
                        heading="Enduring Legacy",
                        target_words=85,
                        factual_milestones=["Modern ramifications and lasting influence"],
                        visual_concept="Modern laboratory transitioning back into historic sketch",
                        evidence_ids=[],
                    ),
                ],
            )

        # 7. Fallback deterministic generator for script draft payloads
        if schema is GeneratedScriptPayload or schema.__name__ == "GeneratedScriptPayload":
            # Extract evidence IDs if present in prompt
            evidence_ids = []
            ev_matches = re.findall(r"\b(evi_[a-zA-Z0-9_-]+)\b", prompt)
            if ev_matches:
                evidence_ids = [ev_matches[0]]

            # Check if prompt specifies unresearched mode
            if "UNRESEARCHED MODE DIRECTIVE" in prompt:
                evidence_ids = []

            # Check if prompt includes multi-section breakdown
            sections = []
            sec_types = ["hook", "setup", "middle", "climax", "takeaway"]
            # Detect if outline sections were mentioned in prompt
            matches = re.findall(r'(\d+)\.\s*"(\w+)"\s*\(ID:\s*\'(\w+)\'', prompt)
            if matches:
                for idx, sec_type, sec_id in matches:
                    sections.append(
                        ScriptSectionPayload(
                            id=sec_id,
                            type=sec_type,
                            heading=f"Heading for {sec_type.capitalize()}",
                            narration=f"Spoken voiceover narration describing {sec_type} and causal mechanisms in detail.",
                            notes="Dynamic 2D animation staging notes.",
                        )
                    )
            else:
                sections = [
                    ScriptSectionPayload(
                        id="sec_01",
                        type="hook",
                        heading="The Dilemma",
                        narration="Imagine a world where everything we understood was completely flipped on its head.",
                        notes="Slow dolly into a character staring at an anomaly.",
                    ),
                    ScriptSectionPayload(
                        id="sec_02",
                        type="setup",
                        heading="Foundations",
                        narration="To comprehend how we arrived here, we must trace back to the initial limitations.",
                        notes="Historical timeline unfolds across the lower third.",
                    ),
                    ScriptSectionPayload(
                        id="sec_03",
                        type="middle",
                        heading="The Mechanism",
                        narration="Step by step, the core engine processes the inputs through precise sequential stages.",
                        notes="Exploded schematic view of the technical elements.",
                    ),
                    ScriptSectionPayload(
                        id="sec_04",
                        type="climax",
                        heading="The Breakthrough",
                        narration="At the critical inflection point, the experiments finally demonstrated unequivocal success.",
                        notes="High-energy particle burst illuminating the frame.",
                    ),
                    ScriptSectionPayload(
                        id="sec_05",
                        type="takeaway",
                        heading="Legacy",
                        narration="Today, this fundamental principle underpins our modern understanding.",
                        notes="Camera pulls out to reveal the wider interconnected system.",
                    ),
                ]

            claims = []
            if evidence_ids:
                claims.append(
                    ScriptClaimPayload(
                        claim_text="The core mechanism achieved stability under extreme conditions.",
                        section_id=sections[min(2, len(sections)-1)].id or "sec_03",
                        evidence_ids=evidence_ids,
                    )
                )

            if self.enable_auto_expansion:
                self.calls.append({
                    "action": "expand_script",
                    "topic": "expanded",
                    "target_duration_minutes": 5.0,
                })
                for s in sections:
                    s.narration = s.narration + " " + "Furthermore, detailed empirical analysis confirms this critical step with additional depth and explanatory clarity. " * 3

            return schema(
                title="Explainer Documentary Draft",
                sections=sections,
                claims=claims,
            )

        # 8. Fallback deterministic generator for fact-check batch payloads
        if schema is BatchFactCheckResponsePayload or schema.__name__ == "BatchFactCheckResponsePayload":
            claim_blocks = re.findall(
                r"CLAIM ID:\s*([a-zA-Z0-9_-]+).*?(?:PROPOSITION:\s*\"(.*?)\").*?(?:RISK LEVEL:\s*(\w+)).*?PERMITTED EVIDENCE:\s*(.*?)(?=\n--- CLAIM|\n=== END BATCH|\Z)",
                prompt,
                re.DOTALL,
            )
            assessments = []
            for cid, prop, risk, ev_text in claim_blocks:
                eids = re.findall(r"- ID:\s*([a-zA-Z0-9_-]+)", ev_text)
                is_creative = (risk == "creative_flexibility")
                status = "supported" if (eids or is_creative) else "unsupported"
                action = "keep" if (eids or is_creative) else "qualify"
                assessments.append(
                    ClaimAssessmentPayload(
                        claim_id=cid,
                        status=status,
                        explanation=f"Mock assessment: proposition is {status}.",
                        supporting_evidence_ids=eids[:1] if (eids and status == "supported") else [],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action=action,
                    )
                )
            return schema(assessments=assessments)

        # 9. Fallback deterministic generator for section repair payloads
        if schema.__name__ == "SectionRepairPayload":
            sec_match = re.search(r"SECTION ID:\s*([a-zA-Z0-9_-]+)", prompt)
            sec_id = sec_match.group(1) if sec_match else "sec_01"

            narration_match = re.search(r'CURRENT SECTION NARRATION:\s*"(.*?)"', prompt, re.DOTALL)
            cur_narration = narration_match.group(1) if narration_match else "Repaired narration."

            claim_blocks = re.findall(
                r"CLAIM ID:\s*([a-zA-Z0-9_-]+).*?(?:CURRENT PROPOSITION:\s*\"(.*?)\").*?(?:FACT-CHECK STATUS:\s*(\w+)).*?PERMITTED EVIDENCE FOR REPAIR:\s*(.*?)(?=\n--- CLAIM|\n=== END REQUEST|\Z)",
                prompt,
                re.DOTALL,
            )
            repairs_list = []
            used_spans = set()
            for cid, prop, status, ev_text in claim_blocks:
                eids = re.findall(r"- ID:\s*([a-zA-Z0-9_-]+)", ev_text)
                if status == "partially_supported":
                    action = "qualify"
                    rev_text = f"{prop.rstrip('.')} under verified conditions."
                    chosen_eids = eids[:1] if eids else []
                elif status == "contradicted":
                    if eids:
                        action = "rephrase"
                        rev_text = f"Alternative verified fact: {prop.rstrip('.')} corrected."
                        chosen_eids = eids[:1]
                    else:
                        action = "remove"
                        rev_text = None
                        chosen_eids = []
                elif status == "unsupported":
                    if eids:
                        action = "rephrase"
                        rev_text = f"Supported correction: {prop.rstrip('.')}."
                        chosen_eids = eids[:1]
                    else:
                        action = "remove"
                        rev_text = None
                        chosen_eids = []
                else:
                    action = "keep"
                    rev_text = prop
                    chosen_eids = eids[:1] if eids else []

                target_span = prop if (prop and cur_narration.count(prop) == 1 and prop not in used_spans) else None
                if not target_span:
                    # Find a sentence in cur_narration that appears once and hasn't been used yet
                    sentences = [s.strip() for s in cur_narration.split(".") if s.strip()]
                    for s in sentences:
                        full_s = s + "." if (s + ".") in cur_narration else s
                        if cur_narration.count(full_s) == 1 and full_s not in used_spans:
                            target_span = full_s
                            break
                    if not target_span and cur_narration.strip() and cur_narration.strip() not in used_spans:
                        target_span = cur_narration.strip()

                if target_span:
                    used_spans.add(target_span)

                if action in ("qualify", "rephrase"):
                    replacement_span = rev_text if (rev_text and rev_text.strip()) else "Verified replacement statement."
                else:
                    replacement_span = None

                repairs_list.append(
                    {
                        "claim_id": cid,
                        "action": action,
                        "target_span": target_span,
                        "replacement_span": replacement_span,
                        "revised_claim_text": rev_text,
                        "evidence_ids": chosen_eids,
                        "explanation": f"Deterministic mock repair for {cid} ({action}).",
                    }
                )

            return schema.model_validate({
                "section_id": sec_id,
                "revised_narration": cur_narration,
                "repairs": repairs_list,
            })

        raise NotImplementedError("Direct generate_json on MockLLMProvider requires preset response or use generate_script/revise_script.")

    def generate_script(
        self,
        topic: str,
        target_duration_minutes: float = 5.0,
        title: Optional[str] = None,
    ) -> Script:
        """Deterministically generate a structured Script draft."""
        self.calls.append({
            "action": "generate_script",
            "topic": topic,
            "target_duration_minutes": target_duration_minutes,
            "title": title,
        })

        if self._response_queue:
            item = self._response_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, Script):
                return populate_script_duration_metrics(item, self.words_per_minute)
            if isinstance(item, dict):
                return populate_script_duration_metrics(Script.model_validate(item), self.words_per_minute)

        if self._preset_error:
            err = self._preset_error
            self._preset_error = None
            raise err

        if self._raw_response is not None:
            raw = self._raw_response
            self._raw_response = None
            try:
                return Script.model_validate(raw)
            except Exception as exc:
                raise LLMResponseMalformedError(f"Mock raw output failed script validation: {exc}") from exc

        if self._preset_response is not None:
            resp = self._preset_response
            self._preset_response = None
            if isinstance(resp, Script):
                res_script = resp
            elif isinstance(resp, dict):
                res_script = Script.model_validate(resp)
            else:
                raise LLMResponseMalformedError(f"Mock response type {type(resp)} invalid for Script")
        else:
            # Default deterministic anime-style explainer script
            script_title = title or f"The World of {topic.strip().title()}"
            res_script = Script(
                script_id=f"scr_{uuid.uuid4().hex[:12]}",
                project_id="proj_default",
                version=1,
                status="draft",
                title=script_title,
                topic=topic,
                target_duration_minutes=target_duration_minutes,
                sections=[
                    ScriptSection(
                        id="sec_01_hook",
                        type="hook",
                        heading="The Hidden Phenomenon",
                        narration=f"Have you ever wondered what really happens beneath the surface when we encounter {topic}?",
                        notes="Close-up of character looking thoughtfully at the horizon.",
                    ),
                    ScriptSection(
                        id="sec_02_setup",
                        type="setup",
                        heading="Laying the Groundwork",
                        narration=f"To understand {topic}, we need to break down the foundational principles that define it.",
                        notes="Camera pans out to reveal blackboard diagram.",
                    ),
                    ScriptSection(
                        id="sec_03_middle",
                        type="middle",
                        heading="The Core Mechanics",
                        narration=f"Step by step, the pieces begin to connect. When {topic} is put to work, each component plays a precise role.",
                        notes="Animated cutaway showing the interacting elements.",
                    ),
                    ScriptSection(
                        id="sec_04_climax",
                        type="climax",
                        heading="The Turning Point",
                        narration=f"And that brings us to the crucial breakthrough: {topic} transforms how we solve this challenge completely.",
                        notes="Dynamic dramatic lighting shift with character in confident pose.",
                    ),
                    ScriptSection(
                        id="sec_05_takeaway",
                        type="takeaway",
                        heading="Summary & Perspective",
                        narration=f"So the next time you think about {topic}, remember this core insight. It changes everything.",
                        notes="Wide shot with character smiling and nodding to camera.",
                    ),
                ],
            )

        if self.enable_auto_expansion:
            actual_words = sum(count_words(s.narration) for s in res_script.sections)
            min_expected_words = int(target_duration_minutes * 120)
            if actual_words < int(0.60 * min_expected_words):
                try:
                    res_script = self.expand_script(
                        current_script=res_script,
                        topic=topic,
                        target_duration_minutes=target_duration_minutes,
                    )
                except Exception:
                    pass

        return populate_script_duration_metrics(res_script, words_per_minute=self.words_per_minute)

    def expand_script(
        self,
        current_script: Script,
        topic: str,
        target_duration_minutes: float,
    ) -> Script:
        """Deterministically expand an under-length script draft for testing."""
        self.calls.append({
            "action": "expand_script",
            "topic": topic,
            "target_duration_minutes": target_duration_minutes,
        })

        if self._response_queue:
            item = self._response_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, Script):
                return populate_script_duration_metrics(item, self.words_per_minute)
            if isinstance(item, dict):
                return populate_script_duration_metrics(Script.model_validate(item), self.words_per_minute)

        if self._preset_error:
            err = self._preset_error
            self._preset_error = None
            raise err

        if self._preset_response is not None:
            resp = self._preset_response
            self._preset_response = None
            if isinstance(resp, Script):
                return populate_script_duration_metrics(resp, self.words_per_minute)
            if isinstance(resp, dict):
                expanded_model = Script.model_validate(resp)
                return populate_script_duration_metrics(expanded_model, self.words_per_minute)

        # Clone and substantially expand narrative content for each section
        cloned = copy.deepcopy(current_script)
        for sec in cloned.sections:
            if sec.type == "hook":
                sec.narration = (
                    f"{sec.narration} This creates a profound puzzle that challenged early human thinkers "
                    f"for generations, raising questions that traditional intuition could not readily resolve."
                )
            elif sec.type == "setup":
                sec.narration = (
                    f"{sec.narration} In the earliest stages, communities operated under severe logistical constraints. "
                    f"Every transaction required precise matching of mutual needs, creating friction and limiting scale."
                )
            elif sec.type == "middle":
                sec.narration = (
                    f"{sec.narration} To overcome these systemic bottlenecks, standardized physical representations "
                    f"emerged through empirical trial and error. Durable tokens and accounting tallies replaced ad-hoc "
                    f"exchanges, establishing verifiable trust across distant trade routes."
                )
            elif sec.type == "climax":
                sec.narration = (
                    f"{sec.narration} The decisive breakthrough occurred when standardized units were officially "
                    f"authenticated, unifying diverse markets under a universally recognized medium of exchange."
                )
            elif sec.type == "takeaway":
                sec.narration = (
                    f"{sec.narration} Today, these foundational principles continue to underpin our global economic "
                    f"architecture, demonstrating how a simple human invention reshaped civilization."
                )
        return populate_script_duration_metrics(cloned, words_per_minute=self.words_per_minute)

    def revise_script(
        self,
        current_script: Script,
        instruction: str,
        conversation_context: Optional[str] = None,
    ) -> Script:
        """Deterministically revise an existing Script draft."""
        self.calls.append({
            "action": "revise_script",
            "current_script_version": current_script.version,
            "instruction": instruction,
            "conversation_context": conversation_context,
        })

        if self._response_queue:
            item = self._response_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, Script):
                return populate_script_duration_metrics(item, self.words_per_minute)
            if isinstance(item, dict):
                return populate_script_duration_metrics(Script.model_validate(item), self.words_per_minute)

        if self._preset_error:
            err = self._preset_error
            self._preset_error = None
            raise err

        if self._raw_response is not None:
            raw = self._raw_response
            self._raw_response = None
            try:
                return Script.model_validate(raw)
            except Exception as exc:
                raise LLMResponseMalformedError(f"Mock raw output failed script validation: {exc}") from exc

        if self._preset_response is not None:
            resp = self._preset_response
            self._preset_response = None
            if isinstance(resp, Script):
                return populate_script_duration_metrics(resp, self.words_per_minute)
            if isinstance(resp, dict):
                return populate_script_duration_metrics(Script.model_validate(resp), self.words_per_minute)

        # Clone and apply deterministic modifications
        cloned_sections = []
        instruction_lower = instruction.lower()

        for sec in current_script.sections:
            sec_copy = sec.model_copy()
            if "hook" in instruction_lower and sec.type == "hook":
                sec_copy.narration = f"Updated hook: {instruction}. {sec.narration}"
                sec_copy.notes = f"Revised hook notes: {instruction}"
            elif "climax" in instruction_lower and sec.type == "climax":
                sec_copy.narration = f"Enhanced climax: {instruction}. {sec.narration}"
            elif "takeaway" in instruction_lower and sec.type == "takeaway":
                sec_copy.narration = f"Refined takeaway: {instruction}. {sec.narration}"
            elif "middle" in instruction_lower and sec.type == "middle":
                sec_copy.narration = f"Expanded middle explanation: {instruction}. {sec.narration}"
            cloned_sections.append(sec_copy)

        # If general instruction not matching a specific section role, update middle section
        if not any(k in instruction_lower for k in ["hook", "setup", "middle", "climax", "takeaway"]):
            for sec in cloned_sections:
                if sec.type == "middle":
                    sec.narration = f"{sec.narration} (Note: {instruction})"
                    break

        new_title = current_script.title
        if "title" in instruction_lower:
            new_title = f"{current_script.title} (Revised)"

        cloned_claims = [c.model_copy() for c in current_script.claims]

        # Deterministic targeted fact challenge investigation handling
        chal_match = re.search(r"FACT CHALLENGE:.*?Claim ([a-zA-Z0-9_]+)", instruction)
        if chal_match:
            target_cid = chal_match.group(1)
            new_eids = re.findall(r"evi_[a-zA-Z0-9_]+", instruction)
            for c in cloned_claims:
                if c.claim_id == target_cid:
                    if new_eids:
                        c.evidence_ids = list(dict.fromkeys(new_eids))
                        c.link_status = "linked"
                    c.claim_text = f"{c.claim_text} (Investigated & Grounded)"

        revised = Script(
            script_id=current_script.script_id,
            project_id=current_script.project_id,
            version=current_script.version + 1,
            status="draft",
            title=new_title,
            topic=current_script.topic,
            target_duration_minutes=current_script.target_duration_minutes,
            sections=cloned_sections,
            claims=cloned_claims,
            research_run_id=current_script.research_run_id,
            outline=current_script.outline,
            evidence_brief_path=current_script.evidence_brief_path,
        )
        return populate_script_duration_metrics(revised, words_per_minute=self.words_per_minute)
