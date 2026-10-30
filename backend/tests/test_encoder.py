"""Unit tests for FFmpeg encoder selection and fallback logic.

Runs entirely deterministically without requiring an NVIDIA GPU or physical encoding.
"""

from typing import List, Tuple
import pytest
from backend.app.services.renderer.encoder import (
    EncoderManager,
    EncoderSelectionError,
    LIBX264_ENCODER,
    NVENC_ENCODER,
)


class MockSubprocessRunner:
    """Mock runner recording executed commands and providing controllable responses."""

    def __init__(self):
        self.calls: List[Tuple[List[str], float]] = []
        self.encoder_list_output = f" V..... {LIBX264_ENCODER}   libx264\n V..... {NVENC_ENCODER}   h264_nvenc"
        self.nvenc_probe_code = 0
        self.libx264_probe_code = 0
        self.execution_codes: List[int] = []

    def __call__(self, args: List[str], timeout_s: float) -> Tuple[int, str, str]:
        self.calls.append((args, timeout_s))

        # 1. Encoders listing
        if "-encoders" in args:
            return 0, self.encoder_list_output, ""

        # 2. Probe encode
        if "-f" in args and "lavfi" in args:
            enc_idx = args.index("-c:v") + 1
            encoder = args[enc_idx]
            if encoder == NVENC_ENCODER:
                return self.nvenc_probe_code, "", "NVENC probe mock output"
            if encoder == LIBX264_ENCODER:
                return self.libx264_probe_code, "", "libx264 probe mock output"

        # 3. Regular execution
        if self.execution_codes:
            code = self.execution_codes.pop(0)
            return code, "", f"Execution return {code}"

        return 0, "", "OK"


def test_nvenc_selected_when_supported():
    """1. NVENC is selected when both listed and probe succeeds."""
    mock = MockSubprocessRunner()
    mock.nvenc_probe_code = 0
    manager = EncoderManager(runner=mock)

    selected = manager.select_encoder(preference="auto")
    assert selected == NVENC_ENCODER


def test_software_encoding_selected_when_nvenc_unavailable():
    """2. Software encoding (libx264) is selected when NVENC probe fails."""
    mock = MockSubprocessRunner()
    mock.nvenc_probe_code = 1  # Hardware or driver probe fails
    mock.libx264_probe_code = 0
    manager = EncoderManager(runner=mock)

    selected = manager.select_encoder(preference="auto")
    assert selected == LIBX264_ENCODER


def test_nvenc_failure_triggers_fallback_during_execution():
    """3. NVENC runtime failure during execution triggers fallback to libx264."""
    mock = MockSubprocessRunner()
    mock.nvenc_probe_code = 0
    mock.libx264_probe_code = 0
    # First execution with NVENC fails (code 1), fallback with libx264 succeeds (code 0)
    mock.execution_codes = [1, 0]
    manager = EncoderManager(runner=mock)

    def cmd_builder(encoder: str) -> List[str]:
        return ["ffmpeg", "-c:v", encoder, "out.mp4"]

    encoder_used, code, _ = manager.execute_with_fallback(
        command_builder=cmd_builder,
        timeout_s=30.0,
        preferred_encoder="auto",
    )

    assert encoder_used == LIBX264_ENCODER
    assert code == 0


def test_failure_of_software_fallback_surfaces_as_error():
    """4. A failure of both primary and software fallback raises an explicit error."""
    mock = MockSubprocessRunner()
    mock.nvenc_probe_code = 1
    mock.libx264_probe_code = 1  # Software also unusable
    manager = EncoderManager(runner=mock)

    with pytest.raises(EncoderSelectionError) as exc_info:
        manager.select_encoder(preference="auto")

    assert "Neither hardware encoder" in str(exc_info.value)


def test_subprocess_calls_use_argument_arrays_and_bounded_timeouts():
    """5. All subprocess calls strictly use argument arrays (list) and finite timeouts."""
    mock = MockSubprocessRunner()
    manager = EncoderManager(runner=mock, probe_timeout_s=4.5)

    manager.select_encoder(preference="auto")

    assert len(mock.calls) > 0
    for args, timeout in mock.calls:
        # Must be a list (not a shell string)
        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)
        # Timeout must be bounded and strictly positive
        assert isinstance(timeout, float)
        assert 0 < timeout <= 60.0
