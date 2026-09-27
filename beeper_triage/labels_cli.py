"""Typer wiring for `beeper labels` — the CLI surface over ``labels.py``."""
from __future__ import annotations

from typing import Optional

import typer

from .beeper_client import BeeperSDKError
from .labels import DEFAULT_EMAIL, DEFAULT_GROUPS, LabelSyncError, google_groups, google_token, sync
from .output import emit, resolve_json_flag
from .verbs import build_client_or_exit

labels_app = typer.Typer(help="Beeper labels, mirrored from Google Contacts labels.")


@labels_app.command("sync")
def _sync(
    groups: Optional[list[str]] = typer.Argument(
        None, help=f"Google labels to mirror (default: {', '.join(DEFAULT_GROUPS)})."),
    apply: bool = typer.Option(False, "--apply", help="Write to Beeper (default: dry run)."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="List every add, remove and miss."),
    user: str = typer.Option(DEFAULT_EMAIL, "--user", help="Google account."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Mirror Google Contacts labels into Beeper labels.

    Matches the other person's phone/email in 1:1 chats on any network, and
    carries a match across Beeper merged chats. Adds what Google has; removes
    only chats this sync added earlier, never ones you filed by hand. A change
    rebuilds the label (the Desktop API can't edit one), so its id changes.
    """
    eff_json = resolve_json_flag(agent, json_)
    lines: list[str] = []
    try:
        wanted, missing = google_groups(google_token(user), list(groups or DEFAULT_GROUPS))
        client = build_client_or_exit(agent=agent, json_flag=json_)
        plans = sync(client.raw_request, wanted, apply=apply, log=lines.append)
    except (LabelSyncError, BeeperSDKError) as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)

    human = []
    for p in plans:
        human.append(f"{p.name}: {p.contacts} contacts -> {len(p.wanted)} chats, "
                     f"+{len(p.add)} -{len(p.remove)}"
                     f"{'' if p.label_id or apply else ' (label will be created)'}, "
                     f"{len(p.unmatched)} contacts with no Beeper chat")
        if verbose:
            human += [f"   + {p.wanted.get(c, c)}" for c in sorted(p.add)]
            human += [f"   - {c}" for c in sorted(p.remove)]
            human += [f"   ? {n}" for n in sorted(p.unmatched)]
    human += lines + [f"! no Google label called {m!r}" for m in missing]
    if not apply:
        human.append("dry run — pass --apply to write")
    emit({"applied": apply, "labels": [p.to_dict() for p in plans], "log": lines,
          "missingGoogleLabels": missing}, json_flag=eff_json, human="\n".join(human))
    if missing or any("!" in line for line in lines):
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    app.add_typer(labels_app, name="labels")
