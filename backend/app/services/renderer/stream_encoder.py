"""FFmpeg stream encoder accepting raw RGBA bytes on stdin.

Streams frames directly from Skia to FFmpeg without intermediate disk writes per TechSpec Section 6.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
from typing import Callable, Iterable, List, Optional, Tuple, Union
from backend.app.services.renderer.encoder import (
    EncoderManager,
    EncoderSelectionError,
    LIBX264_ENCODER,
    NVENC_ENCODER,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamResult:
    """Outcome metadata of a completed stream encode."""

    encoder_used: str
    frame_count: int
    output_path: Path
    file_size_bytes: int
    duration_s: float


FrameSource = Union[Iterable[bytes], Callable[[], Iterable[bytes]]]


class StreamEncoder:
    """Manages raw-frame streaming to FFmpeg subprocess with automatic fallback."""

    def __init__(
        self,
        encoder_manager: Optional[EncoderManager] = None,
        ffmpeg_bin: str = "ffmpeg",
    ):
        self.encoder_manager = encoder_manager or EncoderManager(ffmpeg_bin=ffmpeg_bin)
        self.ffmpeg_bin = ffmpeg_bin

    def build_ffmpeg_args(
        self,
        encoder: str,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
    ) -> List[str]:
        """Build argument array for FFmpeg rawvideo input and H.264 encode."""
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-c:v",
            encoder,
        ]

        if encoder == NVENC_ENCODER:
            cmd.extend(["-preset", "p4", "-cq", "19"])
        else:
            cmd.extend(["-preset", "fast", "-crf", "19"])

        cmd.extend(["-pix_fmt", "yuv420p", str(output_path)])
        return cmd

    def _execute_stream(
        self,
        encoder: str,
        frames: Iterable[bytes],
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        timeout_s: float,
    ) -> Tuple[int, int, str]:
        """Pipe frames to FFmpeg stdin and capture return code and diagnostics."""
        cmd = self.build_ffmpeg_args(encoder, output_path, width, height, fps)
        expected_bytes = width * height * 4
        frame_count = 0

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stderr_output = ""
        try:
            assert proc.stdin is not None
            for frame_data in frames:
                if len(frame_data) != expected_bytes:
                    raise ValueError(
                        f"Frame size mismatch: expected {expected_bytes} bytes, got {len(frame_data)}"
                    )
                proc.stdin.write(frame_data)
                frame_count += 1

            proc.stdin.close()
            _, stderr_bytes = proc.communicate(timeout=timeout_s)
            stderr_output = stderr_bytes.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            _, stderr_bytes = proc.communicate()
            stderr_output = (
                stderr_bytes.decode("utf-8", errors="replace")
                + f"\nTimed out after {timeout_s}s"
            )
            return -1, frame_count, stderr_output
        except Exception as exc:
            proc.kill()
            _, stderr_bytes = proc.communicate()
            stderr_output = (
                stderr_bytes.decode("utf-8", errors="replace")
                + f"\nStream write exception: {exc}"
            )
            return -1, frame_count, stderr_output

        return proc.returncode, frame_count, stderr_output

    def encode(
        self,
        frame_source: FrameSource,
        output_path: Path,
        width: int = 1080,
        height: int = 1920,
        fps: int = 24,
        preferred_encoder: str = "auto",
        timeout_s: float = 120.0,
    ) -> StreamResult:
        """Encode raw RGBA frame stream into an MP4 video with fallback handling."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        encoder = self.encoder_manager.select_encoder(preference=preferred_encoder)

        def get_frames_iter() -> Iterable[bytes]:
            if callable(frame_source):
                return frame_source()
            return frame_source

        frames_iter = get_frames_iter()
        code, frame_count, stderr = self._execute_stream(
            encoder=encoder,
            frames=frames_iter,
            output_path=output_path,
            width=width,
            height=height,
            fps=fps,
            timeout_s=timeout_s,
        )

        # Fallback handling if NVENC failed during stream and callable generator exists
        if code != 0 and encoder == NVENC_ENCODER and preferred_encoder == "auto" and callable(frame_source):
            logger.warning(
                "NVENC stream encode failed (code %d). Attempting fallback to %s. Diagnostics: %s",
                code,
                LIBX264_ENCODER,
                stderr[-300:] if len(stderr) > 300 else stderr,
            )
            # Remove incomplete or corrupt file
            if output_path.exists():
                output_path.unlink()

            encoder = LIBX264_ENCODER
            frames_iter = get_frames_iter()
            code, frame_count, stderr = self._execute_stream(
                encoder=encoder,
                frames=frames_iter,
                output_path=output_path,
                width=width,
                height=height,
                fps=fps,
                timeout_s=timeout_s,
            )

        if code != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            if output_path.exists():
                output_path.unlink()
            raise EncoderSelectionError(
                f"Video stream encode failed with encoder '{encoder}' (exit code {code}). FFmpeg stderr: {stderr}"
            )

        file_size = output_path.stat().st_size
        duration_s = frame_count / fps if fps > 0 else 0.0

        return StreamResult(
            encoder_used=encoder,
            frame_count=frame_count,
            output_path=output_path,
            file_size_bytes=file_size,
            duration_s=duration_s,
        )
