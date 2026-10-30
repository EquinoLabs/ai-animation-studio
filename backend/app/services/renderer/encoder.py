"""FFmpeg encoder selection and fallback abstraction.

Implements NVENC detection, probe-encoding validation, and deterministic fallback
to libx264 per TechSpec Section 6 & 8.
"""

import logging
import subprocess
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

NVENC_ENCODER = "h264_nvenc"
LIBX264_ENCODER = "libx264"
DEFAULT_PROBE_TIMEOUT_S = 5.0

SubprocessRunner = Callable[[List[str], float], Tuple[int, str, str]]


class EncoderSelectionError(RuntimeError):
    """Raised when no operational video encoder can be selected or executed."""


def default_subprocess_runner(args: List[str], timeout_s: float) -> Tuple[int, str, str]:
    """Execute a command safely using an argument array with bounded timeout."""
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        logger.warning("Subprocess timed out after %.1fs: %s", timeout_s, args[:3])
        return -1, "", f"Timed out after {timeout_s}s"
    except Exception as exc:
        logger.warning("Subprocess execution error: %s", exc)
        return -1, "", str(exc)


class EncoderManager:
    """Manages encoder capability checks, probing, and fallback selection."""

    def __init__(
        self,
        runner: Optional[SubprocessRunner] = None,
        ffmpeg_bin: str = "ffmpeg",
        probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    ):
        self._runner = runner or default_subprocess_runner
        self._ffmpeg_bin = ffmpeg_bin
        self._probe_timeout_s = probe_timeout_s

    def check_encoder_listed(self, encoder_name: str) -> bool:
        """Check whether FFmpeg reports support for the given encoder name."""
        cmd = [self._ffmpeg_bin, "-hide_banner", "-encoders"]
        code, stdout, stderr = self._runner(cmd, self._probe_timeout_s)
        if code != 0:
            logger.debug("Failed listing ffmpeg encoders: %s", stderr)
            return False
        # Look for the encoder string in the encoders listing table
        return encoder_name in stdout

    def probe_encode(self, encoder_name: str) -> bool:
        """Perform a quick 0.1-second probe encode to verify driver and hardware readiness.
        
        Listing an encoder does not guarantee the GPU driver or hardware is operational.
        """
        cmd = [
            self._ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.1",
            "-c:v",
            encoder_name,
            "-f",
            "null",
            "-",
        ]
        code, stdout, stderr = self._runner(cmd, self._probe_timeout_s)
        if code != 0:
            logger.debug("Probe encode failed for %s: %s", encoder_name, stderr)
            return False
        return True

    def select_encoder(self, preference: str = "auto") -> str:
        """Select an operational video encoder according to preference and capability.
        
        - "nvenc": Forces NVENC; raises EncoderSelectionError if unsupported or fails probe.
        - "x264": Forces libx264; raises EncoderSelectionError if probe fails.
        - "auto": Prefers NVENC; safely falls back to libx264 on failure.
        """
        if preference == "nvenc":
            if self.check_encoder_listed(NVENC_ENCODER) and self.probe_encode(NVENC_ENCODER):
                return NVENC_ENCODER
            raise EncoderSelectionError(
                f"NVENC encoder '{NVENC_ENCODER}' was explicitly requested but is not operational."
            )

        if preference == "x264":
            if self.check_encoder_listed(LIBX264_ENCODER) and self.probe_encode(LIBX264_ENCODER):
                return LIBX264_ENCODER
            raise EncoderSelectionError(
                f"Software encoder '{LIBX264_ENCODER}' was requested but is not operational."
            )

        # "auto" mode: probe NVENC first
        if self.check_encoder_listed(NVENC_ENCODER) and self.probe_encode(NVENC_ENCODER):
            return NVENC_ENCODER

        logger.info(
            "Hardware encoder '%s' unavailable or probe failed; falling back to software '%s'",
            NVENC_ENCODER,
            LIBX264_ENCODER,
        )

        # Fallback to libx264
        if self.check_encoder_listed(LIBX264_ENCODER) and self.probe_encode(LIBX264_ENCODER):
            return LIBX264_ENCODER

        raise EncoderSelectionError(
            f"Neither hardware encoder '{NVENC_ENCODER}' nor software fallback '{LIBX264_ENCODER}' is operational."
        )

    def execute_with_fallback(
        self,
        command_builder: Callable[[str], List[str]],
        timeout_s: float = 60.0,
        preferred_encoder: str = "auto",
    ) -> Tuple[str, int, str]:
        """Execute an FFmpeg encode with runtime fallback from NVENC to libx264 if needed.
        
        Returns (encoder_used, return_code, stderr).
        """
        encoder = self.select_encoder(preference=preferred_encoder)
        cmd = command_builder(encoder)
        code, stdout, stderr = self._runner(cmd, timeout_s)

        # If primary NVENC fails during execution and auto was requested, attempt libx264 fallback
        if code != 0 and encoder == NVENC_ENCODER and preferred_encoder == "auto":
            logger.warning(
                "Encode failed with '%s' (code %d). Attempting fallback to '%s'. Error: %s",
                NVENC_ENCODER,
                code,
                LIBX264_ENCODER,
                stderr[-300:] if len(stderr) > 300 else stderr,
            )
            fallback_encoder = LIBX264_ENCODER
            if self.probe_encode(fallback_encoder):
                fallback_cmd = command_builder(fallback_encoder)
                fb_code, fb_stdout, fb_stderr = self._runner(fallback_cmd, timeout_s)
                if fb_code == 0:
                    return fallback_encoder, fb_code, fb_stderr
                raise EncoderSelectionError(
                    f"Fallback encode with '{fallback_encoder}' also failed (code {fb_code}): {fb_stderr}"
                )

        if code != 0:
            raise EncoderSelectionError(
                f"Encoding failed with '{encoder}' (code {code}): {stderr}"
            )

        return encoder, code, stderr
