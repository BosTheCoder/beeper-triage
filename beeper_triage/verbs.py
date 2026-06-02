"""Tier-1 Beeper verbs (send/react/mark-read/start), registered onto the CLI app."""
from __future__ import annotations

from typing import Optional

import typer
from dotenv import load_dotenv

from .beeper_client import BeeperClient, BeeperSDKError
from .output import emit, resolve_json_flag
from .runtime import _build_client, _require_env


def build_client_or_exit(*, agent: bool, json_flag: Optional[bool]) -> BeeperClient:
    """Load env, build the SDK client, or emit an error and exit(1)."""
    load_dotenv()
    token = _require_env("BEEPER_ACCESS_TOKEN")
    try:
        return _build_client(token, agent=agent)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=resolve_json_flag(agent, json_flag), human=f"Error: {exc}")
        raise typer.Exit(code=1)


def _mark_read(
    chat_id: str = typer.Argument(..., help="Chat ID to mark read."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Mark a chat as read."""
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.mark_read(chat_id)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=resolve_json_flag(agent, json_), human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({"chatID": chat_id, "status": "read"},
         json_flag=resolve_json_flag(agent, json_), human=f"Marked {chat_id} as read.")


def _mark_unread(
    chat_id: str = typer.Argument(..., help="Chat ID to mark unread."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Mark a chat as unread."""
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.mark_unread(chat_id)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=resolve_json_flag(agent, json_), human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({"chatID": chat_id, "status": "unread"},
         json_flag=resolve_json_flag(agent, json_), human=f"Marked {chat_id} as unread.")


def register(app: typer.Typer) -> None:
    """Attach the Tier-1 verb commands to the given Typer app."""
    app.command("mark-read")(_mark_read)
    app.command("mark-unread")(_mark_unread)
