"""Quality and duration regression tests for Script Builder (Quality Pass 1)."""

import pytest
from backend.app.config import Settings
from backend.app.domain.script import (
    Script,
    ScriptSection,
    compute_duration_word_targets,
    count_words,
    estimate_duration_seconds,
    populate_script_duration_metrics,
)
from backend.app.llm.base import LLMResponseMalformedError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.scriptwriter.service import (
    ScriptFinalizationError,
    ScriptService,
    ScriptValidationError,
)


@pytest.fixture
def temp_project(tmp_path, monkeypatch):
    settings = Settings(PROJECTS_DIR=tmp_path / "projects", WORDS_PER_MINUTE=140)
    proj_service = ProjectService(projects_root=tmp_path / "projects")
    project = proj_service.create_project(title="Quality Pass Test Project")

    orig_start = ScriptService.start_script
    def patched_start(self, project_id, topic, **kwargs):
        if "allow_unresearched" not in kwargs and "research_run_id" not in kwargs:
            kwargs["allow_unresearched"] = True
        return orig_start(self, project_id, topic, **kwargs)
    monkeypatch.setattr(ScriptService, "start_script", patched_start)

    return {
        "settings": settings,
        "project_service": proj_service,
        "project_id": project.metadata.project_id,
        "root": tmp_path,
    }


def test_word_count_and_duration_estimation_formulas():
    """Verify word count, speaking rate math, and planning range calculations."""
    # 1. Word counting
    assert count_words("") == 0
    assert count_words(None) == 0
    assert count_words("   \n\t  ") == 0
    assert count_words("Hello world! This is five words.") == 6
    assert count_words("Multiple   spaces    and\nnewlines   handled.") == 5

    # 2. Duration estimation at default 140 WPM
    # 140 words at 140 wpm = 60.0s
    assert estimate_duration_seconds(140, words_per_minute=140) == 60.0
    # 70 words at 140 wpm = 30.0s
    assert estimate_duration_seconds(70, words_per_minute=140) == 30.0
    # Configurable WPM
    assert estimate_duration_seconds(120, words_per_minute=120) == 60.0
    assert estimate_duration_seconds(150, words_per_minute=150) == 60.0

    # 3. Soft target planning ranges
    targets_5m = compute_duration_word_targets(5.0, words_per_minute=140)
    assert targets_5m["target_words"] == 700
    assert targets_5m["min_words"] == 600  # 5 * 120
    assert targets_5m["max_words"] == 750  # 5 * 150
    assert targets_5m["middle_words"] > targets_5m["hook_words"]
    assert targets_5m["middle_words"] > targets_5m["setup_words"]

    # 1 minute short video
    targets_1m = compute_duration_word_targets(1.0, words_per_minute=140)
    assert targets_1m["target_words"] == 140
    assert targets_1m["min_words"] == 120
    assert targets_1m["max_words"] == 150


def test_populate_script_duration_metrics():
    """Verify duration metrics are accurately populated on sections and root script."""
    script = Script(
        title="Test Script",
        topic="Testing",
        target_duration_minutes=2.0,
        sections=[
            ScriptSection(id="s1", type="hook", heading="H1", narration="One two three four five six seven."),  # 7 words
            ScriptSection(id="s2", type="setup", heading="H2", narration="Eight nine ten eleven twelve thirteen fourteen."),  # 7 words
            ScriptSection(id="s3", type="middle", heading="H3", narration="Fifteen sixteen seventeen eighteen nineteen twenty."),  # 6 words
            ScriptSection(id="s4", type="climax", heading="H4", narration="Twenty-one twenty-two twenty-three twenty-four."),  # 4 words
            ScriptSection(id="s5", type="takeaway", heading="H5", narration="Twenty-five twenty-six."),  # 2 words
        ],
    )
    populate_script_duration_metrics(script, words_per_minute=140)

    assert script.total_word_count == 26
    # 26 words / 140 wpm * 60s = 11.14s -> 11.1s
    assert script.estimated_duration_seconds == 11.1
    assert script.sections[0].target_duration_seconds == 3  # 7 / 140 * 60 = 3.0
    assert script.sections[4].target_duration_seconds == 1  # 2 / 140 * 60 = 0.857 -> round to 1s


def test_substantially_underlength_draft_detection_and_bounded_expansion(temp_project):
    """Test that a substantially under-length draft triggers the bounded expansion step."""
    mock_llm = MockLLMProvider(words_per_minute=140, enable_auto_expansion=True)
    service = ScriptService(
        project_service=temp_project["project_service"],
        llm_provider=mock_llm,
        settings=temp_project["settings"],
    )

    # For 5.0 minutes, min_expected is 600 words, threshold is 360 words.
    # Default mock script has ~88 words (< 360).
    script = service.start_script(
        project_id=temp_project["project_id"],
        topic="How Did Humans Invent Currency?",
        target_duration_minutes=5.0,
    )

    # Confirm expand_script was called
    actions = [c.get("action") for c in mock_llm.calls]
    assert "expand_script" in actions
    # Confirm it expanded beyond initial mock ~88 words
    assert script.total_word_count > 150
    assert script.estimated_duration_seconds is not None
    assert all(s.target_duration_seconds is not None for s in script.sections)


def test_expansion_failure_gracefully_preserves_initial_draft(temp_project):
    """If the expansion step encounters an error, initial draft must be safely preserved."""
    mock_llm = MockLLMProvider(words_per_minute=140, enable_auto_expansion=False)
    # Manually trigger expansion failure simulation through base provider logic
    initial_script = mock_llm.generate_script(
        topic="Black Holes",
        target_duration_minutes=5.0,
    )
    initial_words = initial_script.total_word_count

    # Simulate expansion step error
    mock_llm.set_error(RuntimeError("Ollama network timeout during expansion"))
    try:
        mock_llm.expand_script(initial_script, topic="Black Holes", target_duration_minutes=5.0)
        pytest.fail("Should have raised error")
    except RuntimeError:
        pass

    # The initial script remains completely uncorrupted
    assert initial_script.total_word_count == initial_words
    assert len(initial_script.sections) == 5


def test_soft_target_behavior_without_hard_capping(temp_project):
    """Verify scripts longer or shorter than the exact target are accepted without truncation."""
    mock_llm = MockLLMProvider(words_per_minute=140, enable_auto_expansion=False)
    service = ScriptService(
        project_service=temp_project["project_service"],
        llm_provider=mock_llm,
        settings=temp_project["settings"],
    )

    # Preset a rich draft that runs 850 words (longer than 750 max planning range)
    long_narration = "This is a detailed and substantive historical explanation of the concept. " * 30  # ~300 words
    preset_script = Script(
        title="Comprehensive Deep Dive",
        topic="Macroeconomics",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="s1", type="hook", heading="H", narration=long_narration),
            ScriptSection(id="s2", type="setup", heading="S", narration=long_narration),
            ScriptSection(id="s3", type="middle", heading="M", narration=long_narration),
            ScriptSection(id="s4", type="climax", heading="C", narration=long_narration),
            ScriptSection(id="s5", type="takeaway", heading="T", narration=long_narration),
        ],
    )
    mock_llm.set_response(preset_script)

    script = service.start_script(
        project_id=temp_project["project_id"],
        topic="Macroeconomics",
        target_duration_minutes=5.0,
    )

    # Verify no truncation occurred
    assert script.total_word_count >= 1500
    assert len(script.sections) == 5
    # Soft target preserves full text
    assert len(script.sections[2].narration) == len(long_narration.strip())


def test_empty_or_malformed_generation_handling(temp_project):
    """Verify validation rejects empty narration or malformed LLM responses."""
    mock_llm = MockLLMProvider()
    service = ScriptService(
        project_service=temp_project["project_service"],
        llm_provider=mock_llm,
        settings=temp_project["settings"],
    )

    # 1. Empty narration in section
    empty_narration_script = Script(
        title="Empty Narration Test",
        topic="Test",
        target_duration_minutes=3.0,
        sections=[
            ScriptSection(id="s1", type="hook", heading="H", narration="Valid hook"),
            ScriptSection(id="s2", type="middle", heading="M", narration="   "),  # Empty!
        ],
    )
    mock_llm.set_response(empty_narration_script)
    with pytest.raises(ScriptValidationError, match=r"empty.*(narration|blank)"):
        service.start_script(temp_project["project_id"], topic="Test Topic")

    # 2. Malformed JSON from LLM
    mock_llm.set_raw_response(RuntimeError("Unparseable JSON syntax"))
    with pytest.raises(Exception):
        service.start_script(temp_project["project_id"], topic="Test Topic 2")


def test_revision_preserves_unaffected_sections_and_updates_metrics(temp_project):
    """Verify conversational revision updates target sections, preserves others, and updates metrics."""
    mock_llm = MockLLMProvider(words_per_minute=140)
    service = ScriptService(
        project_service=temp_project["project_service"],
        llm_provider=mock_llm,
        settings=temp_project["settings"],
    )

    v1 = service.start_script(
        project_id=temp_project["project_id"],
        topic="Quantum Computing",
        target_duration_minutes=3.0,
    )
    original_hook = v1.sections[0].narration
    original_middle = v1.sections[2].narration

    # Revise specifically the middle section
    v2 = service.revise_script(
        project_id=temp_project["project_id"],
        instruction="In the middle section, explain quantum superposition and qubits in depth.",
    )

    assert v2.version == 2
    # Hook was untouched by revision instruction
    assert v2.sections[0].narration == original_hook
    # Middle was expanded
    assert v2.sections[2].narration != original_middle
    assert "superposition" in v2.sections[2].narration.lower()

    # Metrics recomputed
    assert v2.total_word_count is not None
    assert v2.estimated_duration_seconds is not None
    assert all(s.target_duration_seconds is not None for s in v2.sections)


def test_full_lifecycle_with_duration_metrics_and_immutability(temp_project):
    """Verify accept/reject/finalize lifecycle preserves duration metrics and immutability."""
    mock_llm = MockLLMProvider(words_per_minute=140)
    service = ScriptService(
        project_service=temp_project["project_service"],
        llm_provider=mock_llm,
        settings=temp_project["settings"],
    )
    pid = temp_project["project_id"]

    # 1. Start script
    v1 = service.start_script(pid, topic="The Solar System", target_duration_minutes=4.0)
    assert v1.version == 1

    # 2. Reject draft
    rejected = service.reject_draft(pid, reason="Pacing is too fast in setup")
    assert rejected.version == 1

    # Finalize should be blocked
    with pytest.raises(ScriptFinalizationError, match="rejected and cannot be finalized"):
        service.finalize_script(pid)

    # 3. Revise draft
    v2 = service.revise_script(pid, instruction="Slow down setup and add detail")
    assert v2.version == 2

    # Finalize still blocked without explicit acceptance
    with pytest.raises(ScriptFinalizationError, match="must be explicitly accepted"):
        service.finalize_script(pid)

    # 4. Accept draft
    accepted = service.accept_draft(pid)
    assert accepted.version == 2

    # 5. Finalize script
    finalized = service.finalize_script(pid)
    assert finalized.version == 2
    assert finalized.status == "final"
    assert finalized.total_word_count is not None
    assert finalized.estimated_duration_seconds is not None

    # Verify downstream resolution
    current = service.get_current_script(pid)
    assert current.status == "final"
    assert current.total_word_count == finalized.total_word_count
