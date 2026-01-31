"""CLI entrypoint for beeper-triage."""

from __future__ import annotations

import datetime
import logging
import os
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


def _needs_reply(unread_count: int, preview_is_sender: bool) -> bool:
    return unread_count > 0 and not preview_is_sender


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


def _pick_action() -> Optional[str]:
    """Prompt user to pick an action. Returns 'reply', 'copy', or None (cancelled)."""
    try:
        while True:
            choice = input("\nAction: [1] Reply  [2] Copy to clipboard\n> ").strip()
            if choice == "" or choice == "1":
                return "reply"
            if choice == "2":
                return "copy"
            typer.echo("Invalid choice. Enter 1 or 2.")
    except (KeyboardInterrupt, EOFError):
        return None


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
    max_messages: int = typer.Option(20, "--max-messages", min=1),
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
        if _needs_reply(chat.unread_count, chat.preview_is_sender):
            filtered.append(chat)

    filtered = filtered[:max_chats]

    if not filtered:
        typer.echo("No chats need reply.")
        raise typer.Exit(code=0)

    selection = _pick_chat_fzf([(c.chat_id, c.title) for c in filtered])
    if not selection:
        typer.echo("No chat selected.")
        raise typer.Exit(code=0)

    try:
        messages = client.list_messages(selection, limit=max_messages)
    except BeeperSDKError as exc:
        logger.exception("Failed to list messages")
        raise typer.BadParameter(str(exc)) from exc

    messages_sorted = sorted(messages, key=lambda m: m.timestamp_ms)
    transcript = _format_transcript(messages_sorted)
    if not transcript:
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
