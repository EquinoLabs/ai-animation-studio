# Future Implementation Proposal

## Configurable Video Format & Writing Profile System

**Project:** EquinoLabs / AI Animation Studio\
**Status:** Future idea captured --- not authorized for implementation
yet\
**Timing:** Revisit after Phase 7 is accepted and the research-to-script
foundation is stable

------------------------------------------------------------------------

## 1. Purpose

Preserve the idea of building our own reusable video-writing profiles,
informed by the YouTube script-writing skill we researched, without
making the application dependent on that external skill or its runtime.

The goal is to let AI Animation Studio create scripts for different
kinds of videos through explicit, configurable writing policies rather
than one generic prompt.

This is a future product capability. It is not part of the current CP4C
grounding remediation.

## 2. Background and Current Understanding

We researched an external script-writing skill and reviewed its
instructions, format references, and supporting files. We selected
useful writing principles, but did not import its runtime or global
configuration behavior into our application.

Our existing Script Builder already has a writing-policy layer and a
default script structure:

**Hook → Setup → Middle → Climax → Takeaway**

The project has also implemented research-assisted writing, claim-level
fact-checking, and duration targeting. These foundations are still being
validated.

At present, we have **not confirmed** a complete, standalone, selectable
YouTube/video-format profile system in the application. This document
records the future direction so the idea is not lost.

## 3. Proposed Capability

Create a first-party **Video Format / Writing Profile** system.

A profile would describe how a particular video type should be
researched, structured, narrated, paced, and prepared for downstream
scene planning.

Potential initial profiles:

-   Educational explainer
-   Psychology / human-behavior explainer
-   Narrative documentary
-   List or countdown video
-   Short-form explainer

These are examples for future consideration, not a commitment to
implement every format in the first release.

## 4. What a Profile Could Define

Each profile may contain:

### Editorial identity

-   Video format and purpose
-   Intended audience and assumed knowledge
-   Tone, vocabulary, and narration personality
-   Desired level of depth and complexity

### Story structure

-   Section sequence and each section's purpose
-   Hook approach
-   Setup and context
-   Explanation or narrative progression
-   Climax, key insight, or payoff
-   Takeaway and ending

### Retention and pacing

-   Curiosity strategy, used honestly
-   Section pacing and transitions
-   Repetition limits
-   Optional pattern interrupts
-   Rules against artificial suspense, clickbait, and filler

### Visual-readiness

-   Whether the format expects visual examples, metaphors,
    demonstrations, or scene cues
-   How narration should leave room for visual storytelling
-   Guidance for later scene planning---not a replacement for the scene
    planner

### Duration

-   Target runtime and acceptable range
-   Approximate narration pace
-   Section-level time or word guidance
-   A policy for evidence-limited scripts: do not pad with unsupported
    or repetitive material merely to hit duration

### Quality and integrity rules

-   Research and evidence requirements
-   Claim-grounding expectations
-   Rules for uncertainty and qualification
-   Prohibition on fabricated studies, anecdotes, statistics, and
    quotations
-   Originality: use broad craft principles, not imitation of a specific
    creator or channel

## 5. Suggested Architecture

Keep the profile system separate from the research provider and
fact-checking implementations.

A future profile could be represented as versioned, validated
configuration rather than scattered prompt strings. The exact file
location and schema should be decided during a design audit.

Conceptual flow:

1.  User selects a video format/profile.
2.  Script Builder loads the profile and validates its settings.
3.  Research planning receives the profile's topic and evidence needs.
4.  Script generation follows its structure, tone, and pacing guidance.
5.  Fact-checking remains an independent integrity layer and can reject
    unsupported claims regardless of profile.
6.  The resulting script carries profile/version metadata.
7.  Scene planning can later use visual-readiness guidance from the same
    profile.

The profile should guide generation; it must not weaken research,
grounding, readiness, or finalization rules.

## 6. Design Principles

-   **First-party:** Our own implementation, not a runtime dependency on
    the external skill.
-   **Configurable:** Avoid hardcoding every format into one prompt.
-   **Composable:** Separate editorial style from evidence policy and
    technical pipeline behavior.
-   **Versioned:** Preserve which profile version produced a script.
-   **Testable:** Validate profile schema and test generated
    structure/policy adherence.
-   **Honest retention:** Encourage engagement through clarity, stakes,
    curiosity, and payoff---not exaggeration.
-   **Evidence-first:** If material is insufficient, report the gap or
    narrow the script instead of inventing content.
-   **Animation-aware:** Prepare scripts for visual storytelling while
    keeping scene planning as a separate stage.

## 7. Scope Boundaries

This proposal does **not** authorize or imply:

-   Immediate implementation
-   A new research provider or external skill dependency
-   Copying a particular YouTube channel's distinctive style
-   Automatic scene generation, voice, or video rendering
-   Changing the current Phase 7 acceptance criteria
-   Expanding the current CP4C remediation scope

## 8. Recommended Timing

Revisit this proposal only after:

1.  CP4C grounding remediation is reviewed and accepted.
2.  The controlled live regression is approved and passes.
3.  Phase 7 is completed, including manual UI acceptance.
4.  The next production-pipeline phase is planned.

At that point, audit the current writing-policy implementation and
decide whether to evolve it into this profile system, rather than
duplicating existing functionality.

## 9. Future Definition of Done

A first version could be considered complete when:

-   At least one profile is selectable and validated.
-   Profile instructions are applied consistently to script planning and
    generation.
-   The generated script follows the intended structure and tone within
    reasonable tolerances.
-   Duration guidance is honored without padding or unsupported claims.
-   Fact-checking and readiness remain profile-independent and
    enforceable.
-   Profile identity/version is saved with the script.
-   Tests cover profile loading, invalid configuration, generation
    integration, and integrity boundaries.

------------------------------------------------------------------------

**Decision recorded:** Finish the current Phase 7 work first. Preserve
this as a future design direction; do not implement it during CP4C
remediation.
