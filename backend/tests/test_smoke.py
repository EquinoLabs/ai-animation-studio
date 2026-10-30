"""Smoke tests for backend initialization, configuration, and contracts."""

from backend.app.config import get_settings
from backend.app.domain.contracts import (
    AnimationRunManifest,
    Asset,
    AssetManifest,
    Beat,
    CharacterRef,
    Scene,
    SceneConfig,
    ScenePlan,
    Script,
    Section,
)
from backend.app.main import create_app


def test_settings_load_defaults():
    settings = get_settings()
    assert settings.FPS == 24
    assert settings.WIDTH == 1080
    assert settings.HEIGHT == 1920
    assert settings.ENCODER in ("auto", "nvenc", "x264")


def test_provider_precedence_and_dotenv_resolution(tmp_path):
    """Verify that .env loads properly and environment variable overrides take precedence."""
    import os
    from pydantic_settings import SettingsConfigDict
    from backend.app.config import Settings

    env_file = tmp_path / ".env"
    env_file.write_text("ANIM_LLM_SCRIPT_PROVIDER=ollama\n", encoding="utf-8")

    class TestSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="ANIM_",
            env_file=str(env_file),
            extra="ignore",
        )

    old_env = os.environ.pop("ANIM_LLM_SCRIPT_PROVIDER", None)
    try:
        s1 = TestSettings()
        assert s1.LLM_SCRIPT_PROVIDER == "ollama"

        # Explicit override takes precedence over .env
        os.environ["ANIM_LLM_SCRIPT_PROVIDER"] = "mock"
        s2 = TestSettings()
        assert s2.LLM_SCRIPT_PROVIDER == "mock"
    finally:
        if old_env is not None:
            os.environ["ANIM_LLM_SCRIPT_PROVIDER"] = old_env


def test_contracts_serialization_and_validation():
    # 1. Script
    script = Script(
        title="Sample Video",
        status="draft",
        version=1,
        sections=[Section(name="hook", narration="Did you know?")],
    )
    assert script.title == "Sample Video"
    assert script.status == "draft"

    # 2. Asset Manifest
    manifest = AssetManifest(
        version="1.0.0",
        assets=[
            Asset(
                id="bg_room_01",
                type="background",
                tags=["indoor", "room"],
                file_path="assets/backgrounds/room_01.png",
            )
        ],
    )
    assert len(manifest.assets) == 1

    # 3. Scene Plan
    plan = ScenePlan(
        version=1,
        scenes=[
            Scene(
                id="scene_01",
                beats=[
                    Beat(
                        id="s01_b01",
                        narration="Did you know?",
                        scene=SceneConfig(
                            layout="single",
                            background="bg_room_01",
                            characters=[
                                CharacterRef(id="narrator", pose="idle", expression="neutral")
                            ],
                        ),
                    )
                ],
            )
        ],
    )
    assert plan.scenes[0].beats[0].scene.characters[0].id == "narrator"

    # 4. Run manifest
    run_manifest = AnimationRunManifest(run_id="run_test_01")
    assert run_manifest.run_id == "run_test_01"


def test_fastapi_app_initialization():
    app = create_app()
    assert app.title == "AI Animation Studio API"
