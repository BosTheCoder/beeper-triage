"""Prompt helpers."""

from __future__ import annotations

from typing import Iterable

from .openrouter_client import OpenRouterMessage


SYSTEM_PROMPT = (
    "You are a concise, friendly texting assistant. "
    "Write a single draft reply with no preamble or labels."
)


def build_prompt(transcript: str) -> list[OpenRouterMessage]:
    """Build chat completion messages from transcript."""

    user_prompt = (
        "Here is the chat transcript. Draft one concise, friendly reply. "
        "Do not include quotes or extra commentary.\n\n"
        f"{transcript}"
    )
    return [
        OpenRouterMessage(role="system", content=SYSTEM_PROMPT),
        OpenRouterMessage(role="user", content=user_prompt),
    ]
