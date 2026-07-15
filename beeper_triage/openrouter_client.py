"""OpenRouter chat completions client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter API calls fail."""


@dataclass
class OpenRouterMessage:
    """Chat completion message payload.

    Set ``cache=True`` to mark this message's content as a prompt-cache
    breakpoint (Anthropic ``cache_control: ephemeral`` via OpenRouter). Use it on
    the stable prefix (system prompt + style profile) so repeat calls in a triage
    session read it at ~0.1x cost instead of re-sending it every time. Only worth
    it above ~1024 tokens; below that OpenRouter silently ignores the marker.
    """

    role: str
    content: str
    cache: bool = False

    def to_payload(self) -> dict:
        if self.cache:
            return {
                "role": self.role,
                "content": [
                    {
                        "type": "text",
                        "text": self.content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        return {"role": self.role, "content": self.content}


class OpenRouterClient:
    """Minimal OpenRouter client for chat completions."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def create_chat_completion(self, model: str, messages: Iterable[OpenRouterMessage]) -> str:
        payload = {
            "model": model,
            "messages": [m.to_payload() for m in messages],
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
