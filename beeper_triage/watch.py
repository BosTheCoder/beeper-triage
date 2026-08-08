"""`beeper watch` — poll named chats and report new inbound messages.

Design spec: ``docs/superpowers/specs/2026-08-07-beeper-watch-design.md``.

The engine half of this module is deliberately pure: ``scan`` and ``nag_pass``
take a chat list and a state dict and return events. Every bug this feature
exists to prevent lives in that state machine, so it is testable without a
network. ``run`` is the loop; the Typer wiring is at the bottom.

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

import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import typer

from .beeper_client import BeeperChat, BeeperSDKError
from .output import emit, resolve_json_flag
from .verbs import build_client_or_exit

CONFIG_DIR = Path("~/.config/beeper-watches").expanduser()
STATE_DIR = Path("~/.local/state/beeper-watch").expanduser()
PREVIEW_CHARS = 160

_WATCH_KEYS = {"chat", "title_match", "text_match", "label", "priority"}
_TOP_KEYS = {"name", "poll_seconds", "state", "nag", "filters", "watch"}


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
    priority: Optional[str] = None

    def matches(self, chat: BeeperChat) -> bool:
        if self.chat_id and chat.chat_id == self.chat_id:
            return True
        if self.title_match and self.title_match.search(chat.title or ""):
            return True
        return False


@dataclass
class WatchConfig:
    name: str
    state_path: Path
    watches: list[WatchSpec]
    poll_seconds: int = 180
    nag_after_seconds: int = 1800
    nag_count: int = 1
    inbound_only: bool = True
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
        priority=str(entry["priority"]) if entry.get("priority") else None,
    )


def load_config(
    path: "str | Path", *, state_override: "str | Path | None" = None
) -> WatchConfig:
    """Parse a watch TOML file into a validated WatchConfig."""
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

    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise WatchConfigError(
            f"{path}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_TOP_KEYS))}."
        )

    entries = data.get("watch") or []
    if not entries:
        raise WatchConfigError(f"{path}: no [[watch]] entries — nothing to watch.")
    watches = [_parse_watch(entry, i) for i, entry in enumerate(entries)]

    name = str(data.get("name") or path.stem)
    nag = data.get("nag") or {}
    filters = data.get("filters") or {}

    if state_override is not None:
        state_path = Path(state_override).expanduser()
    elif data.get("state"):
        state_path = Path(str(data["state"])).expanduser()
    else:
        state_path = STATE_DIR / f"{name}.json"

    return WatchConfig(
        name=name,
        state_path=state_path,
        watches=watches,
        poll_seconds=int(data.get("poll_seconds", 180)),
        nag_after_seconds=int(nag.get("after_seconds", 1800)),
        nag_count=int(nag.get("count", 1)),
        inbound_only=bool(filters.get("inbound_only", True)),
        path=path,
    )


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
    """Per-chat memory between polls. See spec §5.1.

    ``label``/``priority`` are cached here so the re-raise pass can render a line
    for a chat that did not appear in this poll's list at all.
    """

    last: int = 0          # lastActivity ms of the newest message seen
    emitted: float = 0.0   # wall-clock secs when we last reported it
    open: bool = False     # last message was inbound and matched
    nags: int = 0          # re-raises already spent
    label: str = ""
    priority: Optional[str] = None


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
    """Write the state file atomically (temp + os.replace)."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = {
        chat_id: {
            "last": st.last,
            "emitted": st.emitted,
            "open": st.open,
            "nags": st.nags,
            "label": st.label,
            "priority": st.priority,
        }
        for chat_id, st in state.items()
    }
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


def collapse(text: Optional[str], limit: int = PREVIEW_CHARS) -> str:
    """One line, whitespace-normalised, truncated to `limit` chars."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# the state machine (spec §5.2)
# ---------------------------------------------------------------------------


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
        st = state.setdefault(chat.chat_id, ChatState())
        st.label, st.priority = spec.label, spec.priority

        ts = chat.last_activity_ms or 0
        if ts <= st.last:
            continue
        st.last = ts

        if seed:
            st.open, st.nags, st.emitted = False, 0, 0.0
            continue

        outbound = bool(chat.preview_is_sender)
        if outbound and config.inbound_only:
            st.open, st.nags = False, 0
            continue

        if spec.text_match is not None:
            if chat.preview_text is None:
                warn(
                    f"{spec.label}: text_match is set but the API gave no preview text "
                    f"for {chat.chat_id} — treating as no match."
                )
            if not spec.text_match.search(chat.preview_text or ""):
                st.open, st.nags = False, 0
                continue

        events.append(
            Event(
                kind="activity" if outbound else "reply",
                chat_id=chat.chat_id,
                label=spec.label,
                text=collapse(chat.preview_text),
                ts=ts,
                priority=spec.priority,
            )
        )
        if outbound:
            # Our own message is worth reporting when inbound_only is off, but it
            # never means someone is waiting on us.
            st.open, st.nags = False, 0
        else:
            st.open, st.nags, st.emitted = True, 0, now
    return events


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

watch_app = typer.Typer(
    invoke_without_command=True,
    help="Watch named chats and print one line per new message.",
)


def _load_or_exit(config: Optional[str], state: Optional[str]) -> WatchConfig:
    if not config:
        typer.echo("Provide --config <path|name>.", err=True)
        raise typer.Exit(code=2)
    try:
        return load_config(resolve_config_path(config), state_override=state)
    except WatchConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2)


def _resolve(config: WatchConfig, *, agent: bool, json_: Optional[bool]) -> list[dict]:
    """Match every spec against the live chat list, for `list` and `check`."""
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        chats = client.list_chats(use_cache=False)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=resolve_json_flag(agent, json_), human=f"Error: {exc}")
        raise typer.Exit(code=1)
    state = load_state(config.state_path)
    rows = []
    for spec in config.watches:
        matched = [c for c in chats if spec.matches(c)]
        rows.append(
            {
                "label": spec.label,
                "priority": spec.priority,
                "textMatch": spec.text_match.pattern if spec.text_match else None,
                "chats": [
                    {
                        "chatID": c.chat_id,
                        "title": c.title,
                        "lastActivity": c.last_activity_ms,
                        "previewIsSender": c.preview_is_sender,
                        "open": bool(state.get(c.chat_id) and state[c.chat_id].open),
                    }
                    for c in matched
                ],
            }
        )
    return rows


def _render_rows(config: WatchConfig, rows: list[dict]) -> str:
    lines = [f"{config.name} ({len(config.watches)} watches, state {config.state_path})"]
    for row in rows:
        tag = f" [{row['priority']}]" if row["priority"] else ""
        lines.append(f"  {row['label']}{tag}")
        if not row["chats"]:
            lines.append("    (no chat matched)")
        for c in row["chats"]:
            flag = " ← open" if c["open"] else ""
            lines.append(f"    {c['chatID']}  {c['title']}  last={c['lastActivity']}{flag}")
        if row["textMatch"]:
            lines.append(f"    text_match: {row['textMatch']}")
    return "\n".join(lines)


@watch_app.callback()
def watch_main(
    ctx: typer.Context,
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Watch config path, or a name in ~/.config/beeper-watches."
    ),
    once: bool = typer.Option(False, "--once", help="Single poll then exit (cron, tests)."),
    json_: bool = typer.Option(False, "--json", help="One JSON object per line instead of text."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Seed state from current reality and emit nothing."
    ),
    state: Optional[str] = typer.Option(None, "--state", help="Override the config's state path."),
) -> None:
    """Poll the configured chats and print one line per event."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = _load_or_exit(config, state)
    # Only events belong on stdout, but the proxy bootstrap in runtime.py narrates
    # itself there — so lend it stderr for the duration of the connect.
    with contextlib.redirect_stdout(sys.stderr):
        client = build_client_or_exit(agent=True, json_flag=None)
    run(
        client,
        cfg,
        max_polls=1 if (once or dry_run) else None,
        seed=dry_run,
        json_mode=json_,
    )


@watch_app.command("list")
def watch_list(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Watch config path or name."),
    state: Optional[str] = typer.Option(None, "--state", help="Override the config's state path."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Show the configured watches, the chats they resolve to, and last activity."""
    cfg = _load_or_exit(config, state)
    rows = _resolve(cfg, agent=agent, json_=json_)
    emit(
        {"name": cfg.name, "state": str(cfg.state_path), "watches": rows},
        json_flag=resolve_json_flag(agent, json_),
        human=_render_rows(cfg, rows),
    )


@watch_app.command("check")
def watch_check(
    config: str = typer.Argument(..., help="Watch config path, or a name in ~/.config/beeper-watches."),
    state: Optional[str] = typer.Option(None, "--state", help="Override the config's state path."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Resolve every watch against live chats; exit 1 if any matches nothing.

    Run this after editing a config. A typo'd title_match otherwise fails silently
    — the watch simply never fires, and silence is the one signal this tool cannot
    distinguish from "nothing happened".
    """
    cfg = _load_or_exit(config, state)
    rows = _resolve(cfg, agent=agent, json_=json_)
    unresolved = [row["label"] for row in rows if not row["chats"]]
    human = _render_rows(cfg, rows)
    if unresolved:
        human += "\n\nUnresolved: " + ", ".join(unresolved)
    emit(
        {"name": cfg.name, "watches": rows, "unresolved": unresolved},
        json_flag=resolve_json_flag(agent, json_),
        human=human,
    )
    if unresolved:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Attach the `watch` command group to the given Typer app."""
    app.add_typer(watch_app, name="watch")
