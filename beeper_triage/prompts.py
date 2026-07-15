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


# Reply "types" the model may choose from, with what each one is for. These
# mirror the interactive guidance presets so the web UI feels like BPT.
REPLY_TYPES: dict[str, str] = {
    "going": "Keep the conversation flowing with the same energy.",
    "schedule": "Arrange or nail down a time/place for something.",
    "close": "Wrap things up warmly without inviting more back-and-forth.",
    "rekindle": "Re-engage a cooled-off thread; reference something recent or ask a question.",
    "decline": "Politely decline without making it obvious you're declining.",
    "todo": "Acknowledge briefly ('on it!') when the message is really a task.",
}

_OPTIONS_SYSTEM = (
    "You are a fast, friendly texting assistant helping someone clear their "
    "message backlog. Given a chat transcript, choose the reply approaches that "
    "genuinely fit THIS conversation and write one concise, natural draft reply "
    "for each — in the user's own casual voice, no preamble, no labels inside "
    "the text, no emoji unless the thread already uses them. Address every open "
    "topic the user hasn't responded to yet.\n\n"
    "Return ONLY a JSON array (no markdown fence, no prose) of objects with keys "
    '"type" and "text". Use only these types where they fit:\n'
    + "\n".join(f'- "{k}": {v}' for k, v in REPLY_TYPES.items())
    + "\n\nOrder the array best-fit first. Do not invent types. Aim to give the "
    "requested number of options: when only one or two approaches truly fit, add "
    "meaningfully different variations of them (shorter/warmer/more direct) rather "
    "than forcing an approach that doesn't fit — you may reuse a type up to twice "
    "for genuinely distinct drafts. Never pad with near-identical or irrelevant replies."
)


def build_options_prompt(
    transcript: str, count: int = 5, hint: str = ""
) -> list[OpenRouterMessage]:
    """Prompt for N type-tagged draft replies as a JSON array."""
    user = (
        f"Draft up to {count} distinct reply options for the conversation below. "
        "Each must be send-ready.\n"
    )
    if hint:
        user += f"\nExtra steer from the user: {hint}\n"
    user += f"\n{transcript}"
    return [
        OpenRouterMessage(role="system", content=_OPTIONS_SYSTEM),
        OpenRouterMessage(role="user", content=user),
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
