"""CLI entrypoint for beeper-triage."""

from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
import subprocess
from typing import Iterable, Optional

import typer
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from .beeper_client import BeeperClient, BeeperMessage, BeeperSDKError
from .editor import EditorError, edit_text
from .openrouter_client import OpenRouterClient, OpenRouterError
from .prompts import build_prompt

app = typer.Typer(add_completion=False)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise typer.BadParameter(f"Missing env var: {name}")
    return value


def _ensure_fzf() -> None:
    if shutil.which("fzf") is None:
        raise typer.BadParameter("Missing dependency: fzf is not on PATH.")


def _pick_chat_fzf(chats: list[tuple[str, str]]) -> Optional[str]:
    if not chats:
        return None
    input_text = "\n".join([f"{chat_id}\t{title}" for chat_id, title in chats])
    result = subprocess.run(
        ["fzf", "--ansi", "--with-nth", "2..", "--prompt", "Chat> "],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if not line:
        return None
    return line.split("\t", 1)[0]


def _needs_reply(preview_is_sender: bool) -> bool:
    return not preview_is_sender


def _format_transcript(messages: Iterable[BeeperMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        speaker = "You" if msg.is_sender else msg.sender_name
        text = msg.text.strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines).strip()


def _format_transcript_with_timestamps(messages: Iterable[BeeperMessage]) -> str:
    """Format messages as a timestamped transcript for clipboard export."""
    lines: list[str] = []
    for msg in messages:
        speaker = "You" if msg.is_sender else msg.sender_name
        text = msg.text.strip()
        if not text:
            continue
        dt = datetime.datetime.fromtimestamp(msg.timestamp_ms / 1000)
        ts = dt.strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] {speaker}: {text}")
    return "\n".join(lines).strip()


def _detect_clipboard_cmd() -> Optional[list[str]]:
    """Return the command list for the first available clipboard tool, or None."""
    candidates = [
        (["clip.exe"], "clip.exe"),
        (["wl-copy"], "wl-copy"),
        (["xclip", "-selection", "clipboard"], "xclip"),
        (["xsel", "--clipboard", "--input"], "xsel"),
    ]
    for cmd, binary in candidates:
        if shutil.which(binary):
            return cmd
    return None


def _copy_to_clipboard(text: str, cmd: list[str]) -> None:
    """Pipe text into the given clipboard command."""
    subprocess.run(cmd, input=text, text=True, check=True)


def _sanitize_export_suffix(title: str) -> str:
    cleaned = title.strip().lower()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9\-]+", "", cleaned)
    cleaned = cleaned.strip("-")
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip("-")
    return cleaned


def _export_transcript(
    transcript: str, chat_title: str, export_root: str = "exports"
) -> str:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    suffix = _sanitize_export_suffix(chat_title)
    base_name = f"{timestamp}-{suffix}" if suffix else timestamp
    export_base = os.path.join(export_root, base_name)
    export_path = export_base
    counter = 2
    while os.path.exists(export_path):
        export_path = f"{export_base}-{counter}"
        counter += 1
    os.makedirs(export_path, exist_ok=False)
    transcript_path = os.path.join(export_path, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as handle:
        handle.write(transcript)
        handle.write("\n")
    return export_path


def _pick_action() -> Optional[str]:
    """Prompt user to pick an action. Returns 'reply', 'copy', or None (cancelled)."""
    try:
        while True:
            choice = input(
                "\nAction: [1] Reply  [2] Copy to clipboard  [3] Export to folder\n> "
            ).strip()
            if choice == "" or choice == "1":
                return "reply"
            if choice == "2":
                return "copy"
            if choice == "3":
                return "export"
            typer.echo("Invalid choice. Enter 1, 2, or 3.")
    except (KeyboardInterrupt, EOFError):
        return None


_MESSAGE_WINDOW_CHOICES: list[tuple[str, str]] = [
    ("today", "Today (since 00:00)"),
    ("2d", "Last 2 days"),
    ("7d", "Last 7 days"),
    ("14d", "Last 14 days"),
    ("30d", "Last 30 days"),
    ("60d", "Last 60 days"),
    ("365d", "Last 365 days"),
    ("all", "All messages (no time filter)"),
]

_MESSAGE_WINDOW_ALIASES = {
    "today": "today",
    "2d": "2d",
    "2days": "2d",
    "couple days": "2d",
    "couple day": "2d",
    "7d": "7d",
    "7days": "7d",
    "week": "7d",
    "1w": "7d",
    "14d": "14d",
    "14days": "14d",
    "two weeks": "14d",
    "2w": "14d",
    "30d": "30d",
    "30days": "30d",
    "month": "30d",
    "1m": "30d",
    "60d": "60d",
    "60days": "60d",
    "2m": "60d",
    "two months": "60d",
    "couple months": "60d",
    "365d": "365d",
    "365days": "365d",
    "year": "365d",
    "1y": "365d",
    "all": "all",
    "none": "all",
    "no limit": "all",
}


def _normalize_message_window(value: str) -> str:
    cleaned = value.strip().lower().replace("-", " ").replace("_", " ")
    cleaned = " ".join(cleaned.split())
    if cleaned in _MESSAGE_WINDOW_ALIASES:
        return _MESSAGE_WINDOW_ALIASES[cleaned]
    valid = ", ".join(key for key, _ in _MESSAGE_WINDOW_CHOICES)
    raise typer.BadParameter(
        f"Invalid message window: {value!r}. Use one of: {valid}."
    )


def _pick_message_window(default_key: str = "7d") -> Optional[str]:
    try:
        while True:
            typer.echo("\nMessage window:")
            for idx, (key, label) in enumerate(_MESSAGE_WINDOW_CHOICES, start=1):
                default_marker = " (default)" if key == default_key else ""
                typer.echo(f"  [{idx}] {label}{default_marker}")
            choice = input("> ").strip()
            if not choice:
                return default_key
            if choice.isdigit():
                index = int(choice)
                if 1 <= index <= len(_MESSAGE_WINDOW_CHOICES):
                    return _MESSAGE_WINDOW_CHOICES[index - 1][0]
            try:
                return _normalize_message_window(choice)
            except typer.BadParameter:
                typer.echo("Invalid choice. Try again.")
    except (KeyboardInterrupt, EOFError):
        return None


def _message_window_since_ms(window_key: str) -> Optional[int]:
    if window_key == "all":
        return None
    now = datetime.datetime.now()
    if window_key == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window_key == "2d":
        since = now - datetime.timedelta(days=2)
    elif window_key == "7d":
        since = now - datetime.timedelta(days=7)
    elif window_key == "14d":
        since = now - datetime.timedelta(days=14)
    elif window_key == "30d":
        since = now - datetime.timedelta(days=30)
    elif window_key == "60d":
        since = now - datetime.timedelta(days=60)
    elif window_key == "365d":
        since = now - datetime.timedelta(days=365)
    else:
        raise typer.BadParameter(f"Invalid message window key: {window_key!r}")
    return int(since.timestamp() * 1000)


def _last_message_from_others(messages: Iterable[BeeperMessage]) -> Optional[str]:
    last_id: Optional[str] = None
    for msg in messages:
        if not msg.is_sender:
            last_id = msg.message_id
    return last_id


@app.command()
def triage(
    model: Optional[str] = typer.Option(
        None, "--model", help="OpenRouter model override"
    ),
    max_chats: int = typer.Option(50, "--max-chats", min=1),
    max_messages: Optional[int] = typer.Option(
        None,
        "--max-messages",
        min=1,
        help="Optional safety cap for fetched messages",
    ),
    message_window: Optional[str] = typer.Option(
        None,
        "--message-window",
        help="Time window for messages (today, 2d, 7d, 14d, 30d, 60d, 365d, all)",
    ),
    include_muted: bool = typer.Option(False, "--include-muted"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_llm: bool = typer.Option(False, "--no-llm"),
) -> None:
    """Triage Beeper chats and draft a reply."""

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    load_dotenv()

    _ensure_fzf()

    access_token = _require_env("BEEPER_ACCESS_TOKEN")
    default_model = os.getenv("OPENROUTER_MODEL", "")
    editor = os.getenv("EDITOR", "")

    base_url = os.getenv("BEEPER_BASE_URL")

    try:
        client = BeeperClient(access_token=access_token, base_url=base_url)
    except BeeperSDKError as exc:
        logger.exception("Failed to initialize Beeper client")
        raise typer.BadParameter(str(exc)) from exc

    try:
        chats = client.list_chats()
    except BeeperSDKError as exc:
        logger.exception("Failed to list chats")
        raise typer.BadParameter(str(exc)) from exc

    filtered = []
    for chat in chats:
        if not include_muted and chat.is_muted:
            continue
        if _needs_reply(chat.preview_is_sender):
            filtered.append(chat)

    filtered = filtered[:max_chats]

    if not filtered:
        typer.echo("No chats need reply.")
        raise typer.Exit(code=0)

    chat_choices = [(c.chat_id, c.title) for c in filtered]
    selection = _pick_chat_fzf(chat_choices)
    if not selection:
        typer.echo("No chat selected.")
        raise typer.Exit(code=0)
    chat_title = next((title for chat_id, title in chat_choices if chat_id == selection), "")

    if message_window is None:
        window_key = _pick_message_window()
        if window_key is None:
            typer.echo("Cancelled.")
            raise typer.Exit(code=0)
    else:
        window_key = _normalize_message_window(message_window)
    since_ms = _message_window_since_ms(window_key)

    try:
        messages = client.list_messages(
            selection, limit=max_messages, since_ms=since_ms
        )
    except BeeperSDKError as exc:
        logger.exception("Failed to list messages")
        raise typer.BadParameter(str(exc)) from exc

    messages_sorted = sorted(messages, key=lambda m: m.timestamp_ms)
    transcript = _format_transcript(messages_sorted)
    if not transcript:
        if since_ms is not None:
            typer.echo("No messages found in the selected time window.")
        else:
            typer.echo("No message content available.")
        raise typer.Exit(code=0)

    reply_to_id = _last_message_from_others(messages_sorted)

    action = _pick_action()
    if action is None:
        typer.echo("Cancelled.")
        raise typer.Exit(code=0)

    if action == "copy":
        clipboard_cmd = _detect_clipboard_cmd()
        if clipboard_cmd is None:
            typer.echo(
                "No clipboard tool found. Install one of: clip.exe (WSL), wl-copy, xclip, xsel"
            )
            raise typer.Exit(code=1)
        timestamped = _format_transcript_with_timestamps(messages_sorted)
        try:
            _copy_to_clipboard(timestamped, clipboard_cmd)
        except subprocess.CalledProcessError as exc:
            raise typer.BadParameter(f"Clipboard copy failed: {exc}") from exc
        typer.echo("Transcript copied to clipboard.")
        raise typer.Exit(code=0)

    if action == "export":
        timestamped = _format_transcript_with_timestamps(messages_sorted)
        export_path = _export_transcript(timestamped, chat_title)
        typer.echo(f"Exported transcript to: {export_path}")
        raise typer.Exit(code=0)

    # action == "reply" — existing flow continues
    if not no_llm:
        if not model:
            model = default_model
        if not model:
            raise typer.BadParameter("OPENROUTER_MODEL or --model is required.")
        _require_env("OPENROUTER_API_KEY")

    if no_llm:
        draft = ""
    else:
        openrouter = OpenRouterClient(api_key=_require_env("OPENROUTER_API_KEY"))
        try:
            draft = openrouter.create_chat_completion(
                model=model, messages=build_prompt(transcript)
            )
        except OpenRouterError as exc:
            logger.exception("Failed to create chat completion via OpenRouter")
            raise typer.BadParameter(str(exc)) from exc

    try:
        edited = edit_text(draft, editor=editor)
    except EditorError as exc:
        logger.exception("Editor error")
        raise typer.BadParameter(str(exc)) from exc

    if not edited:
        typer.echo("Empty message, aborting.")
        raise typer.Exit(code=0)

    typer.echo("\nDraft reply:\n")
    typer.echo(edited)

    confirm = typer.confirm("\nSend this message?", default=False)
    if not confirm:
        typer.echo("Cancelled.")
        raise typer.Exit(code=0)

    if dry_run:
        typer.echo("Dry run enabled. Not sending.")
        raise typer.Exit(code=0)

    try:
        client.send_message(selection, edited, reply_to_message_id=reply_to_id)
    except BeeperSDKError as exc:
        logger.exception("Failed to send message")
        raise typer.BadParameter(str(exc)) from exc

    typer.echo("Message sent.")


if __name__ == "__main__":
    app()
