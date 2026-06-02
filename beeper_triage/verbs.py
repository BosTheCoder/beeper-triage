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
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.mark_read(chat_id)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({"chatID": chat_id, "status": "read"}, json_flag=eff_json, human=f"Marked {chat_id} as read.")


def _mark_unread(
    chat_id: str = typer.Argument(..., help="Chat ID to mark unread."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Mark a chat as unread."""
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.mark_unread(chat_id)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({"chatID": chat_id, "status": "unread"}, json_flag=eff_json, human=f"Marked {chat_id} as unread.")


def _react(
    chat_id: str = typer.Argument(..., help="Chat ID."),
    message_id: str = typer.Argument(..., help="Message ID to react to."),
    emoji: str = typer.Argument(..., help="Reaction emoji / key."),
    remove: bool = typer.Option(False, "--remove", help="Remove the reaction instead of adding."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Add (or --remove) an emoji reaction on a message."""
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        if remove:
            client.remove_reaction(chat_id, message_id, emoji)
            action = "removed"
        else:
            client.add_reaction(chat_id, message_id, emoji)
            action = "added"
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit(
        {"chatID": chat_id, "messageID": message_id, "reaction": emoji, "action": action},
        json_flag=eff_json,
        human=f"{action.capitalize()} {emoji} on {message_id}.",
    )


def register(app: typer.Typer) -> None:
    """Attach the Tier-1 verb commands to the given Typer app."""
    app.command("mark-read")(_mark_read)
    app.command("mark-unread")(_mark_unread)
    app.command("react")(_react)
