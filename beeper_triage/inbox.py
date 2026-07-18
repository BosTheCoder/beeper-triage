"""Surface-agnostic triage engine: queue -> drafts -> resolve.

Everything the web UI (or a future CLI/TUI) needs, with no I/O framework
coupling. Pure functions over a `BeeperClient` and an `OpenRouterClient` so the
whole flow is unit-testable with fakes.
"""

from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Optional

from .beeper_client import BeeperChat, BeeperClient, BeeperMessage
from .openrouter_client import OpenRouterClient
from .prompts import (
    OPENER_TYPES,
    REPLY_TYPES,
    build_event_prompt,
    build_opener_prompt,
    build_options_prompt,
)


# ----------------------------- queue building -----------------------------

@dataclass
class QueueFilters:
    """What counts as 'in the inbox'. Defaults = 1:1s you owe a reply to."""

    groups: bool = False
    include_muted: bool = False
    networks: Optional[list[str]] = None  # lowercase slugs; None = all
    include_archived: bool = False  # surface archived chats too (e.g. auto-archived SMS)

    def visible(self, chat: BeeperChat) -> bool:
        """Passes the cheap filters (archive / group / muted / network).

        Does NOT decide 'owe a reply' — that's verified per chat against the
        last real message, because Beeper's preview flips to a reaction and
        makes replied chats look unreplied (see _owes_reply)."""
        if chat.is_archived and not self.include_archived:
            return False
        if not self.groups and chat.is_group:
            return False
        if not self.include_muted and chat.is_muted:
            return False
        if self.networks and _network_slug(chat.network) not in self.networks:
            return False
        return True

    # kept for callers/tests that want the cheap preview-based check
    def wants(self, chat: BeeperChat) -> bool:
        return self.visible(chat) and _needs_reply(chat)


def _needs_reply(chat: BeeperChat) -> bool:
    """Cheap heuristic: the preview's last message is not from us. Unreliable
    when the other side reacted (preview flips) — use _owes_reply to confirm."""
    return not chat.preview_is_sender


def _owes_reply(client: BeeperClient, chat_id: str) -> Optional[bool]:
    """True if the last NON-reaction message is from them. None on error.

    This is the reliable 'do I owe a reply' check: it ignores reaction-only
    activity that makes an already-replied chat look unanswered (#3)."""
    try:
        msgs = client.list_messages(chat_id, limit=8)
    except Exception:
        return None
    for m in reversed(_oldest_first(msgs)):
        # Ignore reactions and system/membership events ("You joined the chat"),
        # which otherwise masquerade as the last real message.
        if m.msg_type in ("REACTION", "SYSTEM"):
            continue
        return not m.is_sender
    return False


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
    is_pinned: bool = False

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
            is_pinned=chat.is_pinned,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def build_queue(
    client: BeeperClient,
    filters: Optional[QueueFilters] = None,
    *,
    use_cache: bool = False,
    limit: int = 200,
    verify: bool = True,
    verify_cap: int = 150,
) -> list[QueuedChat]:
    """Return the ordered conveyor belt of chats to triage (recent first).

    When ``verify`` is on, each visible chat's 'owe a reply' status is confirmed
    against its last real (non-reaction) message in parallel, so reaction-only
    activity doesn't surface already-replied chats (#3). Falls back to the cheap
    preview heuristic if a per-chat check errors or verification is off.
    """
    filters = filters or QueueFilters()
    chats = client.list_chats(use_cache=use_cache)
    visible = [c for c in chats if filters.visible(c)]
    visible.sort(key=lambda c: c.last_activity_ms, reverse=True)

    if not verify:
        kept = [c for c in visible if _needs_reply(c)]
        return [QueuedChat.from_chat(c) for c in kept[:limit]]

    candidates = visible[:verify_cap]
    verdicts: dict[str, Optional[bool]] = {}
    # This per-chat fan-out (one list_messages each) gates first paint, so run it
    # wide — the Beeper bridge is local and handles the concurrency fine. Fewer
    # waves = a noticeably faster queue load when many chats are visible.
    workers = min(24, max(1, len(candidates)))
    if candidates:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_owes_reply, client, c.chat_id): c.chat_id
                for c in candidates
            }
            for fut in as_completed(futures):
                verdicts[futures[fut]] = fut.result()

    kept = []
    for c in candidates:
        owed = verdicts.get(c.chat_id)
        if owed is None:  # verification errored — fall back to the preview
            owed = _needs_reply(c)
        if owed:
            kept.append(c)
    return [QueuedChat.from_chat(c) for c in kept[:limit]]


# ------------------------------- chat view --------------------------------

@dataclass
class ChatMessage:
    message_id: str
    sender: str
    is_me: bool
    text: str
    timestamp_ms: int
    kind: str = "text"  # text / voice / image / video / file
    reactions: list = field(default_factory=list)
    editable: bool = False  # my own text messages can be edited/unsent
    media_src: Optional[str] = None  # raw attachment src_url (e.g. an image), for display
    caption: str = ""  # AI vision description of an image — feeds the prompt, hidden in UI by default


@dataclass
class ChatView:
    chat_id: str
    messages: list[ChatMessage]

    def transcript(self) -> str:
        # Fold the image caption back in for the model only (the UI keeps it
        # hidden): the AI needs to know what a photo contains to reply to it.
        def _line(m: ChatMessage) -> str:
            if m.kind == "image":
                own = f"{m.text} " if m.text else ""
                return f"{own}[image: {m.caption or 'a photo'}]"
            if m.kind == "video":
                own = f"{m.text} " if m.text else ""
                return f"{own}[video]".strip()
            return m.text

        return format_transcript(
            BeeperMessage(
                message_id="",
                sender_name=m.sender,
                is_sender=m.is_me,
                text=_line(m),
                timestamp_ms=m.timestamp_ms,
            )
            for m in self.messages
        )

    def to_dict(self) -> dict:
        return {"chat_id": self.chat_id, "messages": [asdict(m) for m in self.messages]}


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_LI_RE = re.compile(r"<li[^>]*>", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"</(p|ul|ol|li|div)\s*>", re.IGNORECASE)
# Invisible chars Beeper injects (word-joiner, zero-width space/joiners, BOM).
_INVISIBLE_RE = re.compile(r"[⁠​‌‍﻿]")


def clean_text(raw: Optional[str]) -> str:
    """Turn Beeper's HTML-ish message body into plain display text.

    Handles '<p>hi<br><br>there</p>' paragraphs AND '<ul><li>..</li></ul>'
    bullet lists (which otherwise mash together). Null-text messages
    (attachments, system events) arrive as the literal 'None' -> empty.
    """
    if not raw or raw == "None":
        return ""
    text = _BR_RE.sub("\n", raw)
    text = _LI_RE.sub("\n• ", text)          # <li> -> "• "
    text = _BLOCK_END_RE.sub("\n", text)          # end of p/ul/ol/li/div -> newline
    text = _TAG_RE.sub("", text)                  # strip any remaining tags
    text = html.unescape(text)
    text = _INVISIBLE_RE.sub("", text)            # drop word-joiners etc
    # tidy bullets: "•  x" -> "• x", strip trailing empty bullets
    text = re.sub(r"•[ \t]+", "• ", text)
    text = _WS_RE.sub("\n", text).strip()
    text = re.sub(r"\n•\s*(?=\n|$)", "", text)  # drop empty bullet lines
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fmt_duration(seconds) -> str:
    try:
        s = int(round(float(seconds)))
        return f"{s // 60}:{s % 60:02d}"
    except (TypeError, ValueError):
        return ""


_ATTACH_LABEL = {"image": "📷 Photo", "video": "🎥 Video", "audio": "🔊 Audio", "file": "📎 File"}


def _is_image(att: dict, msg_type: str) -> bool:
    return bool(
        att.get("kind") == "image"
        or (att.get("mime") or "").startswith("image/")
        or msg_type == "IMAGE"
    )


def _is_video(att: dict, msg_type: str) -> bool:
    return bool(
        att.get("kind") == "video"
        or (att.get("mime") or "").startswith("video/")
        or msg_type == "VIDEO"
    )


def _render_message(
    m: BeeperMessage,
    transcribe_fn: Optional[Callable] = None,
    caption_fn: Optional[Callable] = None,
) -> Optional[ChatMessage]:
    """Turn a raw message into a display ChatMessage, or None to skip it."""
    kind = "text"
    text = clean_text(m.text)
    att = m.attachment or {}
    media_src = None
    img_caption = ""

    if m.msg_type == "REACTION":
        return None  # reactions surface on their target message's .reactions

    if m.msg_type == "SYSTEM" and not att:
        return None  # system/membership event ("You joined the chat") — not a message

    if m.is_deleted:
        # A retracted message is context: the AI should know something was said
        # and then unsent. Show the original text if the network kept it.
        content = clean_text(m.text)
        return ChatMessage(
            message_id=m.message_id,
            sender=m.sender_name,
            is_me=m.is_sender,
            text=(f'🚫 Deleted: "{content}"' if content else "🚫 Message deleted"),
            timestamp_ms=m.timestamp_ms,
            kind="deleted",
            reactions=list(m.reactions or []),
            editable=False,
        )

    if att.get("is_voice_note") or att.get("kind") == "voice" or m.msg_type == "VOICE":
        kind = "voice"
        dur = _fmt_duration(att.get("duration"))
        label = f"🎤 Voice note ({dur})" if dur else "🎤 Voice note"
        transcript = ""
        if transcribe_fn and att.get("src_url"):
            try:
                transcript = (transcribe_fn(att["src_url"], m.message_id) or "").strip()
            except Exception:
                transcript = ""
        text = f'{label} — "{transcript}"' if transcript else label
    elif att and _is_image(att, m.msg_type):
        kind = "image"
        media_src = att.get("src_url")
        # `text` stays as the sender's own words (may be empty); the vision
        # description goes in `caption` — hidden in the UI, fed to the prompt.
        if caption_fn and media_src:
            try:
                img_caption = (caption_fn(media_src, m.message_id) or "").strip()
            except Exception:
                img_caption = ""
    elif att and _is_video(att, m.msg_type):
        # Checked BEFORE the `not text` branch so a video WITH a caption isn't
        # swallowed as a plain text message (which dropped the video entirely).
        # `text` keeps the sender's caption (may be empty); the UI renders it inline.
        kind = "video"
        media_src = att.get("src_url")
    elif not text and att:
        kind = att.get("kind", "file")
        text = _ATTACH_LABEL.get(kind, "📎 Attachment")
        media_src = att.get("src_url")
    elif not text:
        return None  # empty system/event message

    return ChatMessage(
        message_id=m.message_id,
        sender=m.sender_name,
        is_me=m.is_sender,
        text=text,
        timestamp_ms=m.timestamp_ms,
        kind=kind,
        reactions=list(m.reactions or []),
        editable=(m.is_sender and kind == "text"),
        media_src=media_src,
        caption=img_caption,
    )


def chat_view(
    client: BeeperClient,
    chat_id: str,
    *,
    limit: int = 20,
    since_ms: Optional[int] = None,
    transcribe_fn: Optional[Callable] = None,
    caption_fn: Optional[Callable] = None,
) -> ChatView:
    """Recent messages for a chat, oldest-first for display + prompting.

    `transcribe_fn(src_url, message_id) -> str` (optional) transcribes voice
    notes; `caption_fn(src_url, message_id) -> str` (optional) describes images,
    so both reach the display and the prompt.
    """
    msgs = client.list_messages(chat_id, limit=limit, since_ms=since_ms)
    msgs = _oldest_first(msgs)
    out = []
    for m in msgs:
        cm = _render_message(m, transcribe_fn, caption_fn)
        if cm is not None:
            out.append(cm)
    return ChatView(chat_id=chat_id, messages=out)


def _oldest_first(msgs: list[BeeperMessage]) -> list[BeeperMessage]:
    if len(msgs) >= 2 and msgs[0].timestamp_ms > msgs[-1].timestamp_ms:
        return list(reversed(msgs))
    return list(msgs)


_GAP_MARK_MS = 3 * 60 * 60 * 1000  # mark a break of 3h+ between messages


def humanize_gap(ms: int) -> str:
    """Public alias: a rough '2 weeks'/'2 months' label for a millisecond gap."""
    return _humanize_gap(ms)


def _humanize_gap(ms: int) -> str:
    """A rough '2 weeks later' style label for a millisecond time gap."""
    s = ms / 1000
    if s < 3600:
        return f"{int(round(s / 60))} min later"
    h = s / 3600
    if h < 24:
        return f"{int(round(h))} hour{'s' if round(h) != 1 else ''} later"
    d = h / 24
    if d < 7:
        return f"{int(round(d))} day{'s' if round(d) != 1 else ''} later"
    if d < 30:
        w = int(round(d / 7))
        return f"{w} week{'s' if w != 1 else ''} later"
    if d < 365:
        mo = int(round(d / 30))
        return f"{mo} month{'s' if mo != 1 else ''} later"
    y = int(round(d / 365))
    return f"{y} year{'s' if y != 1 else ''} later"


def format_transcript(messages: Iterable[BeeperMessage]) -> str:
    """Render the transcript, inserting a '[2 weeks later]' marker on big time
    gaps so the model can tell a stale backlog from a fresh message."""
    lines: list[str] = []
    prev_ts: Optional[int] = None
    for m in messages:
        text = (m.text or "").strip()
        if not text:
            continue
        if prev_ts and m.timestamp_ms and (m.timestamp_ms - prev_ts) >= _GAP_MARK_MS:
            lines.append(f"[{_humanize_gap(m.timestamp_ms - prev_ts)}]")
        who = "Me" if m.is_sender else (m.sender_name or "Them")
        lines.append(f"{who}: {text}")
        if m.timestamp_ms:
            prev_ts = m.timestamp_ms
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
    style: str = "",
    reply_delay: str = "",
    lessons: str = "",
) -> list[Draft]:
    """One OpenRouter call -> up to `count` type-tagged drafts.

    ``style`` is an optional texting-style profile injected so drafts match the
    user's voice. ``reply_delay`` (human string, e.g. "2 months") makes one draft
    acknowledge a long silence when the user is replying very late. ``lessons`` is
    the user's approved do/don't corrections (#8), injected as hard rules."""
    if not transcript.strip():
        return []
    messages = build_options_prompt(
        transcript, count=count, hint=hint, style=style,
        reply_delay=reply_delay, lessons=lessons,
    )
    raw = orc.create_chat_completion(model, messages)
    return _parse_drafts(raw, count=count)


def opener_options(
    orc: OpenRouterClient,
    model: str,
    *,
    name: str,
    context: str = "",
    count: int = 5,
    style: str = "",
    history: str = "",
    reply_delay: str = "",
) -> list[Draft]:
    """One OpenRouter call -> up to `count` opener messages (the user is reaching
    out first, not replying). Same {type,text} shape as reply drafts."""
    messages = build_opener_prompt(
        name, context, count=count, style=style, history=history, reply_delay=reply_delay
    )
    raw = orc.create_chat_completion(model, messages)
    return _parse_drafts(raw, count=count, valid_types=OPENER_TYPES, fallback="opener")


_OBJ_RE = re.compile(r"\{[^{}]*\}")


def _parse_drafts(
    raw: str, *, count: int, valid_types: dict = REPLY_TYPES, fallback: str = "going"
) -> list[Draft]:
    """Robustly pull a JSON array of {type,text} out of the model output.

    Tolerates a ```json fence and raw newlines inside string values (the model
    often emits multi-line reply text, which strict JSON rejects). Falls back to
    plucking individual {type,text} objects, then — only if the output is clearly
    NOT JSON — to treating the whole thing as one draft. Never surfaces raw JSON.
    ``valid_types``/``fallback`` swap the type vocabulary for openers vs replies."""
    payload = _extract_json_array(raw)
    items = payload if isinstance(payload, list) else _salvage_objects(raw)

    drafts: list[Draft] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        t = str(item.get("type", "")).strip().lower()
        if t not in valid_types:
            t = fallback
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        drafts.append(Draft(type=t, text=text))

    if not drafts and raw.strip() and not _looks_like_json(raw):
        # Model wrote plain prose instead of JSON — use it as a single draft.
        drafts.append(Draft(type=fallback, text=raw.strip()))
    return drafts[:count]


def _looks_like_json(raw: str) -> bool:
    s = raw.strip().lstrip("`").lstrip("json").strip()
    return s.startswith("[") or s.startswith("{")


def _salvage_objects(raw: str) -> list:
    """Pluck individual {type,text} objects when the whole-array parse fails
    (trailing comma, stray text, etc.). Each is parsed leniently."""
    out = []
    for chunk in _OBJ_RE.findall(raw):
        try:
            obj = json.loads(chunk, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _extract_json_array(raw: str):
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        # strict=False: allow raw newlines/tabs inside strings (multi-line replies)
        return json.loads(cleaned, strict=False)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_json_object(raw: str):
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned, strict=False)
    except (json.JSONDecodeError, ValueError):
        return None


# ------------------------------- events -----------------------------------

@dataclass
class Event:
    """A calendar event pulled from a conversation."""

    found: bool
    title: str = ""
    date: str = ""        # YYYY-MM-DD
    start_time: str = ""  # HH:MM (24h) or ""
    end_time: str = ""
    all_day: bool = False
    location: str = ""
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def extract_event(
    orc: OpenRouterClient, model: str, transcript: str, *, today: str = ""
) -> Event:
    """One OpenRouter call -> a single calendar Event (found=False if none)."""
    if not transcript.strip():
        return Event(found=False)
    raw = orc.create_chat_completion(model, build_event_prompt(transcript, today=today))
    data = _extract_json_object(raw)
    if not isinstance(data, dict) or not data.get("found"):
        return Event(found=False)
    return Event(
        found=True,
        title=str(data.get("title", "")).strip(),
        date=str(data.get("date", "")).strip(),
        start_time=str(data.get("start_time", "")).strip(),
        end_time=str(data.get("end_time", "")).strip(),
        all_day=bool(data.get("all_day", False)),
        location=str(data.get("location", "")).strip(),
        details=str(data.get("details", "")).strip(),
    )


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


def send(
    client: BeeperClient, chat_id: str, text: str, *, dry_run: bool = False,
    reply_to_message_id: Optional[str] = None,
) -> ActionResult:
    """Send a message. Does NOT archive — archiving right after a send is
    undone by the message re-activating the chat, so archive separately via
    ``archive_reliable`` after the send has propagated (#9).

    ``reply_to_message_id`` quotes an existing message (any message, including
    one of your own) so the reply threads off it."""
    if not (text or "").strip():
        raise ValueError("send requires non-empty text")
    if dry_run:
        return ActionResult("send", chat_id, True, sent_text=text, detail="dry-run: would send")
    client.send_message(chat_id, text=text, reply_to_message_id=reply_to_message_id)
    return ActionResult("send", chat_id, False, sent_text=text, detail="sent")


def archive_reliable(
    client: BeeperClient,
    chat_id: str,
    *,
    dry_run: bool = False,
    attempts: int = 4,
    delay: float = 5.0,
    sleep=time.sleep,
) -> ActionResult:
    """Archive a chat and confirm it stuck, retrying through the bridge's lag.

    A message sent moments earlier re-activates the chat, so the first archive
    often bounces back. We archive, wait, verify via a fresh chat read, and
    retry until it holds (#9). Meant to run off the request path (background)."""
    if dry_run:
        return ActionResult("archive", chat_id, True, archived=True, detail="dry-run: would archive")
    last_detail = "archive did not stick"
    for i in range(max(1, attempts)):
        try:
            client.archive(chat_id, archived=True)
        except Exception as exc:  # keep trying
            last_detail = f"archive error: {exc}"
        sleep(delay)
        if _is_archived(client, chat_id):
            return ActionResult("archive", chat_id, False, archived=True,
                                detail=f"archived (attempt {i + 1})")
    return ActionResult("archive", chat_id, False, archived=False, detail=last_detail)


def _is_archived(client: BeeperClient, chat_id: str) -> bool:
    try:
        for c in client.list_chats(use_cache=False):
            if c.chat_id == chat_id:
                return bool(c.is_archived)
    except Exception:
        pass
    return False


def react(
    client: BeeperClient, chat_id: str, message_id: str, emoji: str, *, dry_run: bool = False
) -> ActionResult:
    """Add an emoji reaction to a message."""
    if not emoji:
        raise ValueError("react requires an emoji")
    if dry_run:
        return ActionResult("react", chat_id, True, detail=f"dry-run: would react {emoji}")
    client.add_reaction(chat_id, message_id, emoji)
    return ActionResult("react", chat_id, False, detail=f"reacted {emoji}")


def edit(
    client: BeeperClient, chat_id: str, message_id: str, text: str, *, dry_run: bool = False
) -> ActionResult:
    """Edit one of your sent messages."""
    if not (text or "").strip():
        raise ValueError("edit requires non-empty text")
    if dry_run:
        return ActionResult("edit", chat_id, True, sent_text=text, detail="dry-run: would edit")
    client.edit_message(chat_id, message_id, text)
    return ActionResult("edit", chat_id, False, sent_text=text, detail="edited")


def unsend(
    client: BeeperClient, chat_id: str, message_id: str, *, dry_run: bool = False
) -> ActionResult:
    """Delete (unsend) one of your messages for everyone."""
    if dry_run:
        return ActionResult("unsend", chat_id, True, detail="dry-run: would unsend")
    client.delete_message(chat_id, message_id, for_everyone=True)
    return ActionResult("unsend", chat_id, False, detail="unsent")


def resolve(
    client: BeeperClient,
    chat_id: str,
    action: str,
    *,
    text: Optional[str] = None,
    dry_run: bool = False,
) -> ActionResult:
    """Convenience dispatcher over the primitives (send is send-only).

    - send:    send `text` (archive separately via archive_reliable)
    - archive: archive once (single attempt; use archive_reliable for retries)
    - skip:    no-op
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")
    if action == "skip":
        return ActionResult("skip", chat_id, dry_run, detail="skipped")
    if action == "send":
        return send(client, chat_id, text or "", dry_run=dry_run)
    # archive
    if dry_run:
        return ActionResult("archive", chat_id, True, archived=True, detail="dry-run: would archive")
    client.archive(chat_id, archived=True)
    return ActionResult("archive", chat_id, False, archived=True, detail="archived")
