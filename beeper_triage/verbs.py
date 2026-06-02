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


def register(app: typer.Typer) -> None:
    """Attach the Tier-1 verb commands to the given Typer app. Commands are added
    in later tasks."""
    pass
