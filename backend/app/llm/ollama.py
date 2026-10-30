"""Local Ollama client adapter for structured JSON generation."""

import json
import logging
from typing import Any, Dict, Optional, Type, TypeVar
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel

from backend.app.config import Settings, get_settings
from backend.app.domain.script import Script
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OllamaLLMProvider(LLMProvider):
    """Local Ollama client adapter per TechSpec Section 8 and Task P1 requirements."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        cfg = settings or get_settings()
        super().__init__(words_per_minute=getattr(cfg, "WORDS_PER_MINUTE", 140))
        self.host = (cfg.OLLAMA_HOST or "http://localhost:11434").rstrip("/")
        self.model = cfg.OLLAMA_MODEL or "qwen2.5:7b"
        self.timeout_seconds = float(cfg.OLLAMA_TIMEOUT_SECONDS or 60.0)
        self.max_retries = 1
        logger.info(
            "Initialized OllamaLLMProvider (host=%s, model=%s, timeout=%.1fs, wpm=%d)",
            self.host,
            self.model,
            self.timeout_seconds,
            self.words_per_minute,
        )

    def _post_generate(self, prompt: str) -> Dict[str, Any]:
        """Send generation request to Ollama /api/generate endpoint with bounded retry."""
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "keep_alive": 0,  # Free GPU/VRAM immediately after generation
            "options": {
                "temperature": 0.7,
                "repeat_penalty": 1.15,
                "top_p": 0.9,
                "num_predict": 4096,  # Generous token budget for complete 5-part script
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    if resp.status != 200:
                        raise LLMProviderError(f"Ollama returned HTTP status {resp.status}")
                    body = resp.read().decode("utf-8")
                    parsed = json.loads(body)
                    if parsed.get("done_reason") == "length":
                        raise LLMResponseMalformedError(
                            "Ollama generation exceeded token limit (done_reason='length'). "
                            "Output was truncated."
                        )
                    return parsed
            except urllib.error.HTTPError as http_err:
                error_body = ""
                try:
                    error_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                raise LLMProviderError(
                    f"Ollama HTTP error {http_err.code} from {url}: {error_body or http_err.reason}"
                ) from http_err
            except (urllib.error.URLError, TimeoutError, OSError) as conn_err:
                last_error = conn_err
                if attempt < self.max_retries:
                    logger.warning(
                        "Ollama request failed on attempt %d; retrying once...", attempt + 1
                    )
                    continue

        raise LLMUnavailableError(
            f"Unable to connect to Ollama service at {url} (model '{self.model}'): {last_error}. "
            "Ensure Ollama is installed, running ('ollama serve'), and the model is pulled."
        ) from last_error

    def generate_json(self, prompt: str, schema: Type[T]) -> T:
        """Generate structured JSON adhering to the target Pydantic schema."""
        raw_response = self._post_generate(prompt)

        # Ollama /api/generate returns response text in 'response' key
        response_text = raw_response.get("response", "")
        if not response_text:
            raise LLMResponseMalformedError("Ollama response did not contain a 'response' field.")

        try:
            parsed_json = json.loads(response_text)
        except json.JSONDecodeError as decode_err:
            raise LLMResponseMalformedError(
                f"Failed to parse Ollama JSON response: {decode_err}. Raw text: {response_text[:200]}"
            ) from decode_err

        try:
            return schema.model_validate(parsed_json)
        except Exception as val_err:
            raise LLMResponseMalformedError(
                f"Ollama output does not adhere to {schema.__name__} schema: {val_err}"
            ) from val_err
