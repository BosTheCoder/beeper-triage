"""Surface-agnostic triage engine: queue -> drafts -> resolve.

Everything the web UI (or a future CLI/TUI) needs, with no I/O framework
coupling. Pure functions over a `BeeperClient` and an `OpenRouterClient` so the
whole flow is unit-testable with fakes.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from .beeper_client import BeeperChat, BeeperClient, BeeperMessage
from .openrouter_client import OpenRouterClient
from .prompts import REPLY_TYPES, build_options_prompt


# ----------------------------- queue building -----------------------------

@dataclass
class QueueFilters:
    """What counts as 'in the inbox'. Defaults = 1:1s you owe a reply to."""

    groups: bool = False
    include_muted: bool = False
    networks: Optional[list[str]] = None  # lowercase slugs; None = all

    def wants(self, chat: BeeperChat) -> bool:
        if chat.is_archived:
            return False
        if not _needs_reply(chat):
            return False
        if not self.groups and chat.is_group:
            return False
        if not self.include_muted and chat.is_muted:
            return False
        if self.networks:
            if _network_slug(chat.network) not in self.networks:
                return False
        return True


def _needs_reply(chat: BeeperChat) -> bool:
    """We owe a reply when the last preview message is not from us."""
    return not chat.preview_is_sender


def _network_slug(network: Optional[str]) -> str:
    return (network or "").strip().lower()


@dataclass
class QueuedChat:
    chat_id: str
    title: str
    network: str
    is_group: bool
    is_muted: bool
    unread_count: int
    last_activity_ms: int

    @classmethod
    def from_chat(cls, chat: BeeperChat) -> "QueuedChat":
        return cls(
            chat_id=chat.chat_id,
            title=chat.title,
            network=_network_slug(chat.network) or "chat",
            is_group=chat.is_group,
            is_muted=chat.is_muted,
            unread_count=chat.unread_count,
            last_activity_ms=chat.last_activity_ms,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def build_queue(
    client: BeeperClient,
    filters: Optional[QueueFilters] = None,
    *,
    use_cache: bool = False,
    limit: int = 200,
) -> list[QueuedChat]:
    """Return the ordered conveyor belt of chats to triage (recent first)."""
    filters = filters or QueueFilters()
    chats = client.list_chats(use_cache=use_cache)
    kept = [c for c in chats if filters.wants(c)]
    kept.sort(key=lambda c: c.last_activity_ms, reverse=True)
    return [QueuedChat.from_chat(c) for c in kept[:limit]]


# ------------------------------- chat view --------------------------------

@dataclass
class ChatMessage:
    sender: str
    is_me: bool
    text: str
    timestamp_ms: int


@dataclass
class ChatView:
    chat_id: str
    messages: list[ChatMessage]

    def transcript(self) -> str:
        return format_transcript(
            BeeperMessage(
                message_id="",
                sender_name=m.sender,
                is_sender=m.is_me,
                text=m.text,
                timestamp_ms=m.timestamp_ms,
            )
            for m in self.messages
        )

    def to_dict(self) -> dict:
        return {"chat_id": self.chat_id, "messages": [asdict(m) for m in self.messages]}


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def clean_text(raw: Optional[str]) -> str:
    """Turn Beeper's HTML-ish message body into plain display text.

    Beeper returns bodies like '<p>hi<br><br>there</p>'; null-text messages
    (attachments, system events) arrive as the literal string 'None'. Both need
    to be tamed before display or prompting.
    """
    if not raw or raw == "None":
        return ""
    text = raw.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub("\n", text).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def chat_view(
    client: BeeperClient,
    chat_id: str,
    *,
    limit: int = 20,
    since_ms: Optional[int] = None,
) -> ChatView:
    """Recent messages for a chat, oldest-first for display + prompting."""
    msgs = client.list_messages(chat_id, limit=limit, since_ms=since_ms)
    msgs = _oldest_first(msgs)
    out = []
    for m in msgs:
        text = clean_text(m.text)
        if not text:
            continue
        out.append(
            ChatMessage(
                sender=m.sender_name, is_me=m.is_sender,
                text=text, timestamp_ms=m.timestamp_ms,
            )
        )
    return ChatView(chat_id=chat_id, messages=out)


def _oldest_first(msgs: list[BeeperMessage]) -> list[BeeperMessage]:
    if len(msgs) >= 2 and msgs[0].timestamp_ms > msgs[-1].timestamp_ms:
        return list(reversed(msgs))
    return list(msgs)


def format_transcript(messages: Iterable[BeeperMessage]) -> str:
    lines = []
    for m in messages:
        who = "Me" if m.is_sender else (m.sender_name or "Them")
        text = (m.text or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


# ------------------------------- drafting ---------------------------------

@dataclass
class Draft:
    type: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def draft_options(
    orc: OpenRouterClient,
    model: str,
    transcript: str,
    *,
    count: int = 5,
    hint: str = "",
) -> list[Draft]:
    """One OpenRouter call -> up to `count` type-tagged drafts."""
    if not transcript.strip():
        return []
    messages = build_options_prompt(transcript, count=count, hint=hint)
    raw = orc.create_chat_completion(model, messages)
    return _parse_drafts(raw, count=count)


def _parse_drafts(raw: str, *, count: int) -> list[Draft]:
    """Robustly pull a JSON array of {type,text} out of the model output."""
    payload = _extract_json_array(raw)
    drafts: list[Draft] = []
    seen: set[str] = set()
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            t = str(item.get("type", "")).strip().lower()
            if t not in REPLY_TYPES:
                t = "going"
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            drafts.append(Draft(type=t, text=text))
    if not drafts and raw.strip():
        # Model ignored the format — treat the whole thing as one draft.
        drafts.append(Draft(type="going", text=raw.strip()))
    return drafts[:count]


def _extract_json_array(raw: str):
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


# ------------------------------- resolving --------------------------------

VALID_ACTIONS = {"send", "archive", "skip"}


@dataclass
class ActionResult:
    action: str
    chat_id: str
    dry_run: bool
    sent_text: Optional[str] = None
    archived: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def resolve(
    client: BeeperClient,
    chat_id: str,
    action: str,
    *,
    text: Optional[str] = None,
    dry_run: bool = False,
) -> ActionResult:
    """The single mutation point for the whole UI.

    - send:    send `text`, then archive the chat (out of the inbox)
    - archive: archive only (no message)
    - skip:    no-op; the UI just advances
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")

    if action == "skip":
        return ActionResult(action, chat_id, dry_run, detail="skipped")

    if action == "send":
        if not (text or "").strip():
            raise ValueError("send requires non-empty text")
        if dry_run:
            return ActionResult(
                action, chat_id, dry_run=True, sent_text=text, archived=True,
                detail="dry-run: would send + archive",
            )
        client.send_message(chat_id, text=text)
        client.archive(chat_id, archived=True)
        return ActionResult(
            action, chat_id, dry_run=False, sent_text=text, archived=True,
            detail="sent + archived",
        )

    # action == "archive"
    if dry_run:
        return ActionResult(
            action, chat_id, dry_run=True, archived=True,
            detail="dry-run: would archive",
        )
    client.archive(chat_id, archived=True)
    return ActionResult(action, chat_id, dry_run=False, archived=True, detail="archived")
