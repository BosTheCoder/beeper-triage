"""Prompt helpers."""

from __future__ import annotations

from .openrouter_client import OpenRouterMessage


SYSTEM_PROMPT = (
    "You are a concise, friendly texting assistant. "
    "Write a single draft reply with no preamble or labels. "
    "If the conversation covers multiple topics, address each one in your reply."
)

_GUIDANCE_INSTRUCTIONS: dict[str, str] = {
    "close": "Wrap things up naturally without inviting further back-and-forth.",
    "going": "Reply with the same energy to keep the conversation flowing naturally.",
    "rekindle": "Re-engage the conversation — reference something recent or ask a relevant question.",
    "decline": "Decline politely without making it obvious you are declining.",
    "schedule": "Focus your reply on arranging or scheduling something.",
}

_TODO_SYSTEM = (
    "You are a concise assistant. Output exactly two parts separated by a line containing only '---':\n"
    "Part 1: A brief, friendly acknowledgment reply (e.g. 'I'll take a look at this!').\n"
    "Part 2: A structured todo item with: what needs to be done, key details from the conversation, "
    "and who is involved.\n"
    "No extra commentary or labels."
)

_ANALYSE_SYSTEM = (
    "You are a concise assistant. Analyse this conversation and suggest the best next steps. "
    "Be direct and actionable. Use a short bullet list. No preamble."
)


def build_prompt(
    transcript: str, guidance_key: str = "", user_guidance: str = ""
) -> list[OpenRouterMessage]:
    """Build chat completion messages from transcript with optional guidance."""
    base_instruction = (
        "Here is the chat transcript. Draft one concise, friendly reply. "
        "Address all topics that haven't been responded to yet. "
        "Do not include quotes or extra commentary."
    )

    instruction = _GUIDANCE_INSTRUCTIONS.get(guidance_key, "")
    if instruction:
        user_prompt = f"{base_instruction}\n\nGuidance: {instruction}\n\n{transcript}"
    elif user_guidance:
        user_prompt = f"{base_instruction}\n\nUser guidance: {user_guidance}\n\n{transcript}"
    else:
        user_prompt = f"{base_instruction}\n\n{transcript}"

    return [
        OpenRouterMessage(role="system", content=SYSTEM_PROMPT),
        OpenRouterMessage(role="user", content=user_prompt),
    ]


def build_todo_prompt(transcript: str) -> list[OpenRouterMessage]:
    """Build prompt for acknowledge + todo flow."""
    return [
        OpenRouterMessage(role="system", content=_TODO_SYSTEM),
        OpenRouterMessage(role="user", content=transcript),
    ]


def build_analyse_prompt(transcript: str) -> list[OpenRouterMessage]:
    """Build prompt for next steps analysis (no reply)."""
    return [
        OpenRouterMessage(role="system", content=_ANALYSE_SYSTEM),
        OpenRouterMessage(role="user", content=transcript),
    ]
