"""OpenRouter chat completions client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter API calls fail."""


@dataclass
class OpenRouterMessage:
    """Chat completion message payload."""

    role: str
    content: str


class OpenRouterClient:
    """Minimal OpenRouter client for chat completions."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def create_chat_completion(self, model: str, messages: Iterable[OpenRouterMessage]) -> str:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise OpenRouterError("Failed to call OpenRouter API.") from exc

        if response.status_code >= 400:
            raise OpenRouterError(
                f"OpenRouter API error {response.status_code}: {response.text.strip()}"
            )

        data: dict[str, Any] = response.json()
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            raise OpenRouterError("OpenRouter response missing content.") from exc
