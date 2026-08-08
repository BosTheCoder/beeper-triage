"""`beeper watch` — poll named chats and report new inbound messages.

Design spec: ``docs/superpowers/specs/2026-08-07-beeper-watch-design.md``.

The engine half of this module is deliberately pure: ``scan`` and ``nag_pass``
take a chat list and a state dict and return events. Every bug this feature
exists to prevent lives in that state machine, so it is testable without a
network. ``run`` is the loop.

This module is deliberately free of typer, requests and any surface concern:
the only intra-package import is ``beeper_client``, which is itself stdlib-only
until a client is constructed. That is what lets ``beeper-inbox`` vendor it into
a container that has no CLI dependencies. The Typer wiring lives in
``watch_cli.py``.

Three constraints from the spec are load-bearing and easy to undo by accident:

* Polls always pass ``use_cache=False``. ``list_chats`` defaults to a 6-hour
  cache, and a watcher reading its own cache goes quiet in a way that is
  indistinguishable from "nothing happened".
* The re-raise is capped. ``open`` means "their message was last", which is not
  the same as "you owe a reply" — the item may have been handled by phone or in
  person, and this tool cannot see that. See spec §5.3 before raising the cap.
* A failed poll logs to stderr and keeps going. A monitor that dies silently
  looks exactly like one with nothing to report.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .beeper_client import BeeperChat

CONFIG_DIR = Path("~/.config/beeper-watches").expanduser()
STATE_DIR = Path("~/.local/state/beeper-watch").expanduser()
PREVIEW_CHARS = 160

_WATCH_KEYS = {"chat", "title_match", "text_match", "sender_match", "label", "priority"}
_TOP_KEYS = {"name", "poll_seconds", "state", "nag", "filters", "watch", "notify"}
_NOTIFY_KEYS = {"sink", "target", "kinds", "priorities"}
EVENT_KINDS = frozenset({"reply", "activity", "unanswered"})


class WatchConfigError(ValueError):
    """A watch config is missing, unreadable, or malformed."""


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@dataclass
class WatchSpec:
    """One ``[[watch]]`` entry: which chat, and optionally which content."""

    label: str
    chat_id: Optional[str] = None
    title_match: Optional[re.Pattern] = None
    text_match: Optional[re.Pattern] = None
    sender_match: Optional[re.Pattern] = None
    priority: Optional[str] = None

    def matches(self, chat: BeeperChat) -> bool:
        """Whether this spec *claims* the chat. Content filters are not consulted.

        ``text_match`` and ``sender_match`` narrow which messages inside a claimed
        chat are worth reporting; they never select the chat, or a group would
        appear and disappear from ``watch check`` depending on who spoke last.
        """
        if self.chat_id and chat.chat_id == self.chat_id:
            return True
        if self.title_match and self.title_match.search(chat.title or ""):
            return True
        return False


@dataclass
class NotifySpec:
    """Where a fired event should be pushed, and which events qualify.

    The engine validates this and hands it on; it never delivers. Delivery needs
    a network client and a long-lived process, and this module has neither by
    design (see the module docstring) — so ``beeper-inbox`` owns the sinks and
    looks one up by ``sink`` name. Adding a sink is therefore a change in the
    host, not here, and the config stays a plain description of intent.
    """

    sink: str
    target: Optional[str] = None
    kinds: Optional[frozenset] = None       # None = every kind
    priorities: Optional[frozenset] = None  # None = every priority

    def wants(self, event: "Event") -> bool:
        if self.kinds is not None and event.kind not in self.kinds:
            return False
        if self.priorities is not None and (event.priority or "") not in self.priorities:
            return False
        return True


@dataclass
class WatchConfig:
    name: str
    state_path: Path
    watches: list[WatchSpec]
    poll_seconds: int = 180
    nag_after_seconds: int = 1800
    nag_count: int = 1
    inbound_only: bool = True
    notify: Optional[NotifySpec] = None
    path: Optional[Path] = None


def resolve_config_path(source: str) -> Path:
    """Resolve ``--config`` — a path if it looks like one, else a name in CONFIG_DIR."""
    candidate = Path(source).expanduser()
    if os.sep in source or source.endswith(".toml") or candidate.exists():
        if not candidate.exists():
            raise WatchConfigError(f"No watch config at {candidate}.")
        return candidate
    named = CONFIG_DIR / f"{source}.toml"
    if not named.exists():
        raise WatchConfigError(f"No watch config named {source!r} (looked in {CONFIG_DIR}).")
    return named


def _compile(pattern: Any, key: str, label: str) -> re.Pattern:
    if not isinstance(pattern, str):
        raise WatchConfigError(f"[[watch]] {label}: {key} must be a string.")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise WatchConfigError(f"[[watch]] {label}: bad {key} regex {pattern!r} — {exc}") from exc


def _parse_watch(entry: Any, index: int) -> WatchSpec:
    if not isinstance(entry, dict):
        raise WatchConfigError(f"[[watch]] #{index + 1} is not a table.")
    unknown = sorted(set(entry) - _WATCH_KEYS)
    if unknown:
        # A typo'd key would otherwise silently disable the watch, which is the
        # exact failure `watch check` exists to catch — so fail at load instead.
        raise WatchConfigError(
            f"[[watch]] #{index + 1}: unknown key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_WATCH_KEYS))}."
        )
    chat_id = entry.get("chat")
    raw_title = entry.get("title_match")
    if not chat_id and not raw_title:
        raise WatchConfigError(
            f"[[watch]] #{index + 1}: needs a chat id (chat) or a title_match regex."
        )
    label = str(entry.get("label") or chat_id or raw_title)
    return WatchSpec(
        label=label,
        chat_id=str(chat_id) if chat_id else None,
        title_match=_compile(raw_title, "title_match", label) if raw_title else None,
        text_match=(
            _compile(entry["text_match"], "text_match", label)
            if entry.get("text_match")
            else None
        ),
        sender_match=(
            _compile(entry["sender_match"], "sender_match", label)
            if entry.get("sender_match")
            else None
        ),
        priority=str(entry["priority"]) if entry.get("priority") else None,
    )


def _parse_notify(raw: Any, source: str) -> Optional[NotifySpec]:
    if not raw:
        return None
    if not isinstance(raw, Mapping):
        raise WatchConfigError(f"{source}: 'notify' must be a table/object.")
    unknown = sorted(set(raw) - _NOTIFY_KEYS)
    if unknown:
        raise WatchConfigError(
            f"{source}: notify has unknown key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_NOTIFY_KEYS))}."
        )
    sink = raw.get("sink")
    if not sink or not isinstance(sink, str):
        raise WatchConfigError(f"{source}: notify.sink must be a non-empty string.")

    def _set(key: str, allowed: Optional[frozenset] = None) -> Optional[frozenset]:
        value = raw.get(key)
        if value is None:
            return None
        if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
            raise WatchConfigError(f"{source}: notify.{key} must be a list of strings.")
        items = frozenset(str(v) for v in value)
        if not items:
            # An empty list reads as "none of them", which would silently mute the
            # watch — almost certainly not what was meant. Omit the key for "all".
            raise WatchConfigError(
                f"{source}: notify.{key} is empty; omit it to allow everything."
            )
        if allowed is not None and not items <= allowed:
            bad = ", ".join(sorted(items - allowed))
            raise WatchConfigError(
                f"{source}: notify.{key} has unknown value(s) {bad}; "
                f"expected any of {', '.join(sorted(allowed))}."
            )
        return items

    return NotifySpec(
        sink=sink,
        target=str(raw["target"]) if raw.get("target") else None,
        kinds=_set("kinds", EVENT_KINDS),
        priorities=_set("priorities"),
    )


def parse_config(
    data: Mapping[str, Any],
    *,
    default_name: str = "watch",
    source: str = "config",
    state_dir: "str | Path | None" = None,
    state_override: "str | Path | None" = None,
) -> WatchConfig:
    """Validate an already-decoded config mapping into a WatchConfig.

    Split out from ``load_config`` so a watch registered over HTTP (a JSON body)
    is validated by exactly the same rules, and fails with the same messages, as
    one read from a TOML file. Only the decoding differs.
    """
    if not isinstance(data, Mapping):
        raise WatchConfigError(f"{source}: config must be a table/object.")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise WatchConfigError(
            f"{source}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_TOP_KEYS))}."
        )

    entries = data.get("watch") or []
    if not isinstance(entries, (list, tuple)):
        raise WatchConfigError(f"{source}: 'watch' must be a list of tables.")
    if not entries:
        raise WatchConfigError(f"{source}: no [[watch]] entries — nothing to watch.")
    watches = [_parse_watch(entry, i) for i, entry in enumerate(entries)]

    name = str(data.get("name") or default_name)
    nag = data.get("nag") or {}
    filters = data.get("filters") or {}

    try:
        poll_seconds = int(data.get("poll_seconds", 180))
        nag_after = int(nag.get("after_seconds", 1800))
        nag_count = int(nag.get("count", 1))
    except (TypeError, ValueError) as exc:
        raise WatchConfigError(f"{source}: poll_seconds and nag values must be numbers.") from exc
    if poll_seconds < 1:
        raise WatchConfigError(f"{source}: poll_seconds must be at least 1.")

    if state_override is not None:
        state_path = Path(state_override).expanduser()
    elif data.get("state"):
        state_path = Path(str(data["state"])).expanduser()
    else:
        state_path = Path(state_dir).expanduser() if state_dir else STATE_DIR
        state_path = state_path / f"{name}.json"

    return WatchConfig(
        name=name,
        state_path=state_path,
        watches=watches,
        poll_seconds=poll_seconds,
        nag_after_seconds=nag_after,
        nag_count=nag_count,
        inbound_only=bool(filters.get("inbound_only", True)),
        notify=_parse_notify(data.get("notify"), source),
    )


def load_config(
    path: "str | Path", *, state_override: "str | Path | None" = None
) -> WatchConfig:
    """Read and validate a watch TOML file."""
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - Python 3.10 only
        raise WatchConfigError(
            "Reading watch configs needs tomllib (Python 3.11+) or the 'tomli' package."
        ) from exc

    path = Path(path).expanduser()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise WatchConfigError(f"Cannot read watch config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise WatchConfigError(f"Invalid TOML in {path}: {exc}") from exc

    config = parse_config(
        data, default_name=path.stem, source=str(path), state_override=state_override
    )
    config.path = path
    return config


def match_watch(chat: BeeperChat, watches: Iterable[WatchSpec]) -> Optional[WatchSpec]:
    """First spec that claims this chat, or None. Order in the file is priority."""
    for spec in watches:
        if spec.matches(chat):
            return spec
    return None


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


@dataclass
class ChatState:
    """Per-chat memory between observations. See spec §5.1.

    ``label``/``priority`` are cached here so the re-raise pass can render a line
    for a chat that did not appear in this poll's list at all.

    ``last_msg_id`` exists only for the push transport. The socket re-delivers
    the same message as its send status advances (PENDING → sent → seen), and
    those repeats carry an identical timestamp, so the ``ts`` guard alone would
    mostly cover it — but two different messages *can* share a millisecond in a
    busy group, and there the id is the only thing that tells them apart.
    """

    last: int = 0          # lastActivity ms of the newest message seen
    emitted: float = 0.0   # wall-clock secs when we last reported it
    open: bool = False     # last message was inbound and matched
    nags: int = 0          # re-raises already spent
    label: str = ""
    priority: Optional[str] = None
    last_msg_id: str = ""  # push transport only; see above


def load_state(path: "str | Path") -> dict[str, ChatState]:
    """Read the state file. A missing or corrupt file means "start fresh"."""
    try:
        with open(Path(path).expanduser(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    known = {f for f in ChatState.__dataclass_fields__}
    out: dict[str, ChatState] = {}
    for chat_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        out[str(chat_id)] = ChatState(**{k: v for k, v in entry.items() if k in known})
    return out


def save_state(path: "str | Path", state: dict[str, ChatState]) -> None:
    """Write the state file atomically (temp + os.replace).

    Rows come from ``asdict`` rather than a hand-written field list, so a field
    added to ChatState cannot be silently dropped on save — the exact bug that
    was live in BeeperClient._save_cache.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = {chat_id: asdict(st) for chat_id, st in state.items()}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "reply" | "activity" | "unanswered"
    chat_id: str
    label: str
    text: str = ""
    ts: int = 0
    priority: Optional[str] = None
    minutes: int = 0
    nag: int = 0
    final: bool = False

    def _tag(self) -> str:
        return f"[{self.priority}] " if self.priority else ""

    def line(self) -> str:
        if self.kind == "unanswered":
            suffix = ", final reminder" if self.final else ""
            return f"STILL UNANSWERED ({self.minutes}m{suffix}): {self._tag()}{self.label}"
        verb = "REPLY" if self.kind == "reply" else "ACTIVITY"
        return f"{verb}: {self._tag()}{self.label} | {self.text}"

    def payload(self) -> dict:
        base = {
            "event": self.kind,
            "chat": self.chat_id,
            "label": self.label,
            "priority": self.priority,
        }
        if self.kind == "unanswered":
            return {**base, "minutes": self.minutes, "nag": self.nag, "final": self.final}
        return {**base, "text": self.text, "ts": self.ts}


@dataclass
class WatchMessage:
    """One message off the push transport, normalised.

    Deliberately a plain record rather than the SDK's Message: the socket is
    documented experimental, so the shape it hands us is the thing most likely
    to change, and keeping the parse in one place (`from_ws_entry`) means a
    change there does not reach the state machine.
    """

    chat_id: str
    message_id: str
    ts: int                       # epoch ms
    is_sender: bool
    text: Optional[str] = None
    sender_id: str = ""
    sender_name: str = ""

    @classmethod
    def from_ws_entry(cls, entry: Mapping[str, Any], chat_id: str = "") -> "WatchMessage":
        """Parse one element of a `message.upserted` frame's `entries` array."""
        return cls(
            chat_id=str(entry.get("chatID") or chat_id),
            message_id=str(entry.get("id") or ""),
            ts=parse_ts(entry.get("timestamp")),
            is_sender=bool(entry.get("isSender")),
            text=entry.get("text"),
            sender_id=str(entry.get("senderID") or ""),
            sender_name=str(entry.get("senderName") or ""),
        )


@dataclass
class _TitledChat:
    """The two fields `match_watch` reads, for a chat we only know by id."""

    chat_id: str
    title: str


def parse_ts(value: Any) -> int:
    """Epoch ms from the socket's ISO-8601 timestamp (or a number, or nothing)."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value:
        try:
            text = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def collapse(text: Optional[str], limit: int = PREVIEW_CHARS) -> str:
    """One line, whitespace-normalised, truncated to `limit` chars."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# the state machine (spec §5.2)
# ---------------------------------------------------------------------------


def observe(
    spec: WatchSpec,
    chat_id: str,
    *,
    ts: int,
    is_sender: bool,
    text: Optional[str],
    config: WatchConfig,
    state: dict[str, ChatState],
    now: float,
    seed: bool = False,
    msg_id: str = "",
    sender_id: str = "",
    sender_name: str = "",
    warn: Callable[[str], None] = lambda _m: None,
) -> Optional[Event]:
    """The state machine for one observation of one watched chat (spec §5.2).

    Both transports funnel through here — the poll extracts (ts, is_sender,
    text) from a chat's preview, the socket reads them off the message itself —
    so there is exactly one copy of the decision, and the rules cannot drift
    apart between them.

    `msg_id` is supplied only by the push transport, which re-delivers the same
    message as its send status advances. When present, an exact repeat is
    dropped and a same-millisecond *different* message is still let through;
    without it the poll keeps its original, proven `ts <= last` behaviour.
    """
    st = state.setdefault(chat_id, ChatState())
    st.label, st.priority = spec.label, spec.priority

    if msg_id:
        if msg_id == st.last_msg_id or ts < st.last:
            return None
        st.last_msg_id = msg_id
    elif ts <= st.last:
        return None
    st.last = max(st.last, ts)

    if seed:
        st.open, st.nags, st.emitted = False, 0, 0.0
        return None

    if is_sender and config.inbound_only:
        st.open, st.nags = False, 0
        return None

    if spec.sender_match is not None:
        # Both fields, because they carry different things on different networks:
        # in a WhatsApp group senderName is frequently the raw LID handle rather
        # than a display name, and on others the id is opaque but the name is
        # right. Matching either means one regex covers the chat wherever it is.
        candidates = [c for c in (sender_name, sender_id) if c]
        if not candidates:
            warn(
                f"{spec.label}: sender_match is set but no sender is known for "
                f"{chat_id} — treating as no match."
            )
        if not any(spec.sender_match.search(c) for c in candidates):
            st.open, st.nags = False, 0
            return None

    if spec.text_match is not None:
        if text is None:
            warn(
                f"{spec.label}: text_match is set but there is no message text "
                f"for {chat_id} — treating as no match."
            )
        if not spec.text_match.search(text or ""):
            st.open, st.nags = False, 0
            return None

    if is_sender:
        # Our own message is worth reporting when inbound_only is off, but it
        # never means someone is waiting on us.
        st.open, st.nags = False, 0
    else:
        st.open, st.nags, st.emitted = True, 0, now

    return Event(
        kind="activity" if is_sender else "reply",
        chat_id=chat_id,
        label=spec.label,
        text=collapse(text),
        ts=ts,
        priority=spec.priority,
    )


def scan(
    chats: Iterable[BeeperChat],
    config: WatchConfig,
    state: dict[str, ChatState],
    *,
    now: float,
    seed: bool = False,
    warn: Callable[[str], None] = lambda _m: None,
) -> list[Event]:
    """Fold one poll's chat list into `state` (mutated) and return its events.

    With ``seed=True`` the state adopts current reality and nothing is emitted —
    that is ``--dry-run``, and it is what stops arming a watch on a busy account
    from firing once per chat that merely happens to end on an inbound message.
    """
    events: list[Event] = []
    for chat in chats:
        spec = match_watch(chat, config.watches)
        if spec is None:
            continue
        event = observe(
            spec, chat.chat_id,
            ts=chat.last_activity_ms or 0,
            is_sender=bool(chat.preview_is_sender),
            text=chat.preview_text,
            sender_id=chat.preview_sender_id or "",
            sender_name=chat.preview_sender_name or "",
            config=config, state=state, now=now, seed=seed, warn=warn,
        )
        if event is not None:
            events.append(event)
    return events


def apply_message(
    message: "WatchMessage",
    config: WatchConfig,
    state: dict[str, ChatState],
    *,
    now: float,
    titles: Optional[Mapping[str, str]] = None,
    warn: Callable[[str], None] = lambda _m: None,
) -> Optional[Event]:
    """Run one pushed message through the same state machine `scan` uses.

    The socket payload has no chat title, so `titles` supplies the chatID → title
    map that `title_match` needs. An id missing from the map can still match a
    watch pinned to an explicit `chat` id — the common case — but cannot match a
    title regex, so that gap is warned about rather than passed over silently.
    """
    title = (titles or {}).get(message.chat_id)
    if title is None and any(w.title_match for w in config.watches):
        warn(
            f"no title known for {message.chat_id} — it can match a chat id, "
            f"but not a title_match, until the next reconcile"
        )
    probe = _TitledChat(message.chat_id, title or "")
    spec = match_watch(probe, config.watches)
    if spec is None:
        return None
    return observe(
        spec, message.chat_id,
        ts=message.ts, is_sender=message.is_sender, text=message.text,
        msg_id=message.message_id,
        sender_id=message.sender_id, sender_name=message.sender_name,
        config=config, state=state, now=now, warn=warn,
    )


def nag_pass(config: WatchConfig, state: dict[str, ChatState], *, now: float) -> list[Event]:
    """Re-raise still-open chats, at most `nag.count` times each. See spec §5.3."""
    if config.nag_count <= 0 or config.nag_after_seconds <= 0:
        return []
    events: list[Event] = []
    for chat_id, st in state.items():
        if not st.open or st.nags >= config.nag_count:
            continue
        elapsed = now - st.emitted
        if elapsed <= config.nag_after_seconds:
            continue
        st.nags += 1
        events.append(
            Event(
                kind="unanswered",
                chat_id=chat_id,
                label=st.label or chat_id,
                priority=st.priority,
                minutes=int(elapsed // 60),
                nag=st.nags,
                final=st.nags >= config.nag_count,
            )
        )
    return events


def run(
    client: Any,
    config: WatchConfig,
    *,
    max_polls: Optional[int] = None,
    seed: bool = False,
    json_mode: bool = False,
    out: Any = None,
    err: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, ChatState]:
    """Poll until `max_polls` is reached (None = forever). Returns the final state.

    One event per line on `out`, flushed as it happens; everything else on `err`.
    A poll that raises is logged and skipped — the loop and the on-disk state both
    survive it.
    """
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    state = load_state(config.state_path)
    polls = 0

    def warn(message: str) -> None:
        print(f"[watch] {message}", file=err, flush=True)

    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            chats = client.list_chats(use_cache=False)
        except Exception as exc:  # a dead poll must never kill the loop
            warn(f"poll failed: {type(exc).__name__}: {exc}")
        else:
            now = now_fn()
            events = scan(chats, config, state, now=now, seed=seed, warn=warn)
            events += nag_pass(config, state, now=now)
            for event in events:
                print(
                    json.dumps(event.payload()) if json_mode else event.line(),
                    file=out,
                    flush=True,
                )
            save_state(config.state_path, state)
            if seed:
                break
        if max_polls is not None and polls >= max_polls:
            break
        sleep(config.poll_seconds)
    return state
