"""Tier-1 Beeper verbs (send/react/mark-read/start), registered onto the CLI app."""
from __future__ import annotations

from pathlib import Path
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


def _start(
    account_id: str = typer.Argument(..., help="Account ID to start the chat on."),
    phone: Optional[str] = typer.Option(None, "--phone", help="Recipient phone number."),
    username: Optional[str] = typer.Option(None, "--username", help="Recipient username."),
    email: Optional[str] = typer.Option(None, "--email", help="Recipient email."),
    user_id: Optional[str] = typer.Option(None, "--user-id", help="Recipient user ID."),
    text: Optional[str] = typer.Option(None, "--text", help="Optional first message."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Start a new direct chat with someone you haven't messaged before."""
    eff_json = resolve_json_flag(agent, json_)
    identifiers = {"phone_number": phone, "username": username, "email": email, "id": user_id}
    provided = {k: v for k, v in identifiers.items() if v}
    if len(provided) != 1:
        emit(
            {"error": "Provide exactly one recipient identifier (--phone/--username/--email/--user-id)."},
            json_flag=eff_json,
            human="Provide exactly one recipient identifier (--phone/--username/--email/--user-id).",
        )
        raise typer.Exit(code=2)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        result = client.start_chat(account_id, user=provided, message_text=text)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    chat_id = getattr(result, "chat_id", None) or getattr(result, "chatID", None)
    emit({"chatID": chat_id, "accountID": account_id, "status": "started"},
         json_flag=eff_json, human=f"Started chat {chat_id}.")


def _send(
    chat_id: str = typer.Argument(..., help="Chat ID to send to."),
    text: Optional[str] = typer.Option(None, "--text", help="Message text."),
    attach: Optional[Path] = typer.Option(None, "--attach", exists=True, dir_okay=False,
                                          help="Path to a file/image to attach."),
    reply_to: Optional[str] = typer.Option(None, "--reply-to", help="Message ID to reply to."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Send a message, optionally with an attachment and/or as a reply."""
    eff_json = resolve_json_flag(agent, json_)
    if text is None and attach is None:
        emit({"error": "Provide --text and/or --attach."}, json_flag=eff_json,
             human="Provide --text and/or --attach.")
        raise typer.Exit(code=2)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        result = client.send_message(
            chat_id, text=text, reply_to_message_id=reply_to, attachment_path=attach
        )
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    pending_id = getattr(result, "pending_message_id", None) or getattr(result, "pendingMessageID", None)
    emit({"chatID": chat_id, "pendingMessageID": pending_id, "status": "sent"},
         json_flag=eff_json, human=f"Sent to {chat_id} (pending {pending_id}).")


def _delete(
    chat_id: str = typer.Argument(..., help="Chat ID."),
    message_id: str = typer.Argument(..., help="Message ID to delete."),
    for_everyone: bool = typer.Option(
        False, "--for-everyone", help="Unsend for everyone (not just yourself)."
    ),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Delete (unsend) a message."""
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.delete_message(chat_id, message_id, for_everyone=for_everyone)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit(
        {"chatID": chat_id, "messageID": message_id,
         "forEveryone": for_everyone, "status": "deleted"},
        json_flag=eff_json,
        human=f"Deleted {message_id}" + (" for everyone." if for_everyone else "."),
    )


def _edit(
    chat_id: str = typer.Argument(..., help="Chat ID."),
    message_id: str = typer.Argument(..., help="Message ID to edit."),
    text: str = typer.Argument(..., help="New message text."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Edit the text of a message you sent."""
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        client.edit_message(chat_id, message_id, text)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({"chatID": chat_id, "messageID": message_id, "status": "edited"},
         json_flag=eff_json, human=f"Edited {message_id}.")


def register(app: typer.Typer) -> None:
    """Attach the Tier-1 verb commands to the given Typer app."""
    app.command("mark-read")(_mark_read)
    app.command("mark-unread")(_mark_unread)
    app.command("react")(_react)
    app.command("start")(_start)
    app.command("send")(_send)
    app.command("delete")(_delete)
    app.command("edit")(_edit)
