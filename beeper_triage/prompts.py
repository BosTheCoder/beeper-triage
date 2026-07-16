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
    "tentative": (
        "Soft yes / hold. For an invite you're keen on but can't commit to yet: "
        "show interest, say you can't lock it in now, and that you'll confirm "
        "closer to the time. NOT a decline — you may well go."
    ),
    "todo": "Acknowledge briefly ('on it!') when the message is really a task.",
    "reconnect": (
        "Restart after a long silence. Warmly and lightly own the gap ('ah this "
        "got buried, sorry!' / 'been way too long') and reopen — breezy, NOT "
        "grovelling or over-apologising. Only when the user is replying weeks/"
        "months late."
    ),
}

_OPTIONS_SYSTEM = (
    "You are a fast, friendly texting assistant helping someone clear their "
    "message backlog. Given a chat transcript, produce a set of send-ready draft "
    "replies the user can pick from. Every draft must be in the user's own casual "
    "voice, with no preamble, no labels inside the text, and no emoji unless the "
    "thread already uses them.\n\n"
    "WEIGHT RECENCY. Messages are in time order and a line like '[2 weeks later]' "
    "marks a big gap. Focus your reply on the most RECENT message(s). If an older "
    "cluster sits before a large gap, treat it as likely stale — don't force a "
    "point-by-point reply to it; a light nod is plenty if it still matters. The "
    "fresh message is usually the one the user actually wants to answer.\n\n"
    "Return ONLY a JSON array (no markdown fence, no prose) of objects with keys "
    '"type" and "text". Use only these types:\n'
    + "\n".join(f'- "{k}": {v}' for k, v in REPLY_TYPES.items())
    + "\n\nVARIETY IS THE POINT. The user wants a genuine spread of options to "
    "choose from, so the drafts must differ in SUBSTANCE and ANGLE — not just "
    "wording. Ways to make two drafts genuinely different: a different tone "
    "(warm vs. brief-and-breezy), a different length (one-liner vs. a couple of "
    "lines), a different move (just reply / reply + ask a question back / propose "
    "a concrete plan / gently defer), or a different type entirely.\n\n"
    "Order the array best-fit first. Do not invent types. A type may repeat ONLY "
    "when the two drafts are genuinely different in substance (e.g. two 'schedule' "
    "drafts proposing different times or framings) — never repeat a type just to "
    "reword the same message. Two drafts that a person would read as 'the same "
    "reply' are a failure; drop one and offer a real alternative instead.\n\n"
    "NEVER pad with near-identical or irrelevant replies. But DO reach for the "
    "requested count by offering real alternatives the user would actually weigh "
    "up — a brief version, a warmer version, and one that also asks a question is "
    "a good default spread. Only fall short of the count if you truly cannot write "
    "another distinct, useful, on-topic reply."
)

_STYLE_PREFIX = "Here is how the user texts — match this voice exactly:\n"


def build_options_prompt(
    transcript: str, count: int = 5, hint: str = "", style: str = "", reply_delay: str = ""
) -> list[OpenRouterMessage]:
    """Prompt for N type-tagged draft replies as a JSON array.

    ``style`` is an optional texting-style profile for the user; when provided
    it is injected so every draft matches their voice. ``reply_delay`` is a human
    string (e.g. "2 months") for how long the user is replying AFTER the other
    person's last message — when set, one draft acknowledges the gap.
    """
    system = _OPTIONS_SYSTEM
    if style.strip():
        system = f"{_OPTIONS_SYSTEM}\n\n{_STYLE_PREFIX}{style.strip()}"

    user = (
        f"Draft {count} distinct reply options for the conversation below, ordered "
        "best-fit first. Each must be send-ready and meaningfully different from the "
        "others (different angle/tone/length, not a reworded duplicate). Aim for all "
        f"{count}; only give fewer if you genuinely cannot add another useful, "
        "on-topic option.\n"
    )
    if reply_delay:
        user += (
            f"\nTIMING: the user is replying about {reply_delay} after the other "
            "person's last message — a long silence they let slip. Include exactly "
            "ONE draft of type 'reconnect' that lightly owns the gap and restarts "
            "warmly (breezy 'sorry, this got buried' energy — never grovelling or "
            "over-apologising). Keep the other drafts normal.\n"
        )
    if hint:
        user += f"\nExtra steer from the user: {hint}\n"
    user += f"\n{transcript}"
    return [
        # The system prompt + style profile is identical across every draft call,
        # so cache it: repeat calls in a session read it at ~0.1x cost. The
        # transcript stays in the (uncached) user message.
        OpenRouterMessage(role="system", content=system, cache=True),
        OpenRouterMessage(role="user", content=user),
    ]


_EVENT_SYSTEM = (
    "You extract a single calendar event from a chat transcript, if one is being "
    "proposed or invited to (a party, dinner, meeting, trip, appointment). Return "
    "ONLY a JSON object (no markdown fence, no prose) with these keys:\n"
    '- "found": true only if there is a concrete event to add to a calendar\n'
    '- "title": short event name (e.g. "Rob\'s Birthday & Graduation")\n'
    '- "date": the event date as YYYY-MM-DD (resolve relative/partial dates using '
    "the provided today's date; pick the next future occurrence)\n"
    '- "start_time": "HH:MM" 24h, or "" if none given\n'
    '- "end_time": "HH:MM" 24h, or "" if none given\n'
    '- "all_day": true if no specific time is given\n'
    '- "location": place/address, or ""\n'
    '- "details": one or two lines of useful specifics (what to bring, RSVP date, '
    "parking, etc.), or \"\"\n"
    "If there is no concrete event, return {\"found\": false}. Never invent details "
    "that aren't in the transcript."
)


# ------------------------------- openers ----------------------------------
# Reaching OUT (initiating), not replying. Distinct type set so the UI can tell
# an opener from a reply, and so the model knows the intent is to start a thread.
OPENER_TYPES: dict[str, str] = {
    "opener": "A warm, natural first message to kick things off.",
    "reconnect": (
        "Acknowledge it's been a while and reopen warmly — for someone you've "
        "spoken to before. Light, not grovelling."
    ),
    "plan": "Propose doing something specific together (food, a call, meeting up).",
    "checkin": "A low-key 'how are you / been thinking of you' check-in.",
}

_OPENER_SYSTEM = (
    "You are helping the user START a conversation — they are reaching out FIRST, "
    "not replying to anything. Produce send-ready opener messages in the user's "
    "own casual voice, with no preamble, no labels inside the text, and no emoji "
    "unless that matches the user's style.\n\n"
    "Return ONLY a JSON array (no markdown fence, no prose) of objects with keys "
    '"type" and "text". Use only these types:\n'
    + "\n".join(f'- "{k}": {v}' for k, v in OPENER_TYPES.items())
    + "\n\nVARIETY IS THE POINT. Give a genuine spread — differ in tone, length, "
    "and move (a plain hello / a hello + specific plan / a warm check-in). Never "
    "two openers a person would read as 'the same message'. Order best-fit first."
)


def build_opener_prompt(
    name: str,
    context: str = "",
    *,
    count: int = 5,
    style: str = "",
    history: str = "",
    reply_delay: str = "",
) -> list[OpenRouterMessage]:
    """Prompt for N opener messages (the user is initiating contact).

    ``context`` is the user's one-line 'what it's about'; ``history`` is a recent
    transcript when there's an existing thread (else the person is new to them).
    """
    system = _OPENER_SYSTEM
    if style.strip():
        system = f"{_OPENER_SYSTEM}\n\n{_STYLE_PREFIX}{style.strip()}"

    who = name.strip() or "this person"
    user = (
        f"Draft {count} distinct opener messages the user can send to {who}, "
        "ordered best-fit first. Each must be send-ready and meaningfully "
        "different from the others.\n"
    )
    if context.strip():
        user += f"\nWhat it's about: {context.strip()}\n"
    if history.strip():
        user += (
            "\nThey've spoken before — recent history is below. Make the opener "
            "fit naturally with it.\n"
        )
        if reply_delay:
            user += (
                f"It's been about {reply_delay} since they last spoke, so a "
                "'reconnect' opener that lightly owns the gap fits well.\n"
            )
        user += f"\n{history.strip()}\n"
    else:
        user += (
            "\nThe user has no prior conversation with this person — this is a "
            "friendly first contact. Keep it natural, not overfamiliar.\n"
        )
    return [
        OpenRouterMessage(role="system", content=system, cache=True),
        OpenRouterMessage(role="user", content=user),
    ]


def build_event_prompt(transcript: str, today: str = "") -> list[OpenRouterMessage]:
    """Prompt to pull a single calendar event out of a transcript as JSON."""
    user = "Extract the calendar event from this conversation, if any.\n"
    if today:
        user += f"Today's date is {today} (use it to resolve dates like 'Saturday 1st Aug').\n"
    user += f"\n{transcript}"
    return [
        OpenRouterMessage(role="system", content=_EVENT_SYSTEM, cache=True),
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
