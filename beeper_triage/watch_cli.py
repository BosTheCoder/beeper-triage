"""Typer wiring for `beeper watch` — the CLI surface over ``watch.py``.

Kept apart from the engine so ``watch.py`` stays free of typer and can be
vendored into beeper-inbox's container, which has no CLI dependencies.
"""
from __future__ import annotations

import contextlib
import sys
from typing import Any, Optional

import typer

from .beeper_client import BeeperSDKError
from .output import emit, resolve_json_flag
from .verbs import build_client_or_exit
from .watch import (
    WatchConfig,
    WatchConfigError,
    load_config,
    load_state,
    resolve_config_path,
    run,
)


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
