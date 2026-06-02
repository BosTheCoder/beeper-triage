# Beeper CLI Phase 2 — Tier-1 Verbs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the four Tier-1 verbs the Beeper MCP can't do — `send` (with attachments), `react`, `mark-read`/`mark-unread`, `start` (new chat) — to the `beeper` CLI, backed by the official SDK, with JSON-for-agents / human-for-TTY output.

**Architecture:** Upgrade `beeper_desktop_api` 4.1.296 → 5.0.0 (which wraps these ops; 4.1.296 does not). Extract the connection bootstrap into `runtime.py` (so command modules can reuse it without import cycles), add the new verbs in a focused `verbs.py` registered onto the existing `typer` app, extend the `beeper_client.py` adapter with one method per op, and emit results through the Phase-1 `output` helper.

**Tech Stack:** Python ≥3.10, `typer` (+ `typer.testing.CliRunner`), `beeper_desktop_api==5.0.0`, `pytest`, `uv`.

**Branch:** Create `beeper-cli-phase2` off `main` in the `beeper-triage` repo. Run tests with `python -m pytest`.

**Spec:** `../specs/2026-06-02-beeper-cli-redesign-design.md`

**Ground-truth v5.0.0 signatures (introspected 2026-06-02 — use these exactly):**
- `client.chats.mark_read(chat_id, *, message_id=Omit)` → `Chat`
- `client.chats.mark_unread(chat_id, *, message_id=Omit)` → `Chat`
- `client.chats.messages.reactions.add(message_id, *, chat_id, reaction_key, transaction_id=Omit)` → `ReactionAddResponse`
- `client.chats.messages.reactions.delete(reaction_key, *, chat_id, message_id)` → `ReactionDeleteResponse`
- `client.chats.start(*, account_id, user, allow_invite=Omit, message_text=Omit)` → `ChatStartResponse`; `user` is a dict with ONE of `id` / `email` / `phone_number` / `username` / `full_name`
- `client.assets.upload(file, *, file_name=Omit, mime_type=Omit)` → `AssetUploadResponse`
- `client.messages.send(chat_id, *, attachment=Omit, text=Omit, reply_to_message_id=Omit)` → `MessageSendResponse`; `attachment` is a dict: `upload_id` (required), `type` (one of `image|video|audio|file|gif|voice-note|sticker`), `file_name?`, `mime_type?`, `size?{height,width}`, `duration?`
- (already used by adapter and confirmed present in v5: `chats.list`, `chats.retrieve`, `chats.create`, `accounts.list`, `accounts.contacts.search`, `messages.list`, `messages.search`.)

**File structure (created/changed in this phase):**
- `pyproject.toml` — pin `beeper_desktop_api==5.0.0`
- `beeper_triage/runtime.py` — NEW; relocated connection bootstrap + `build_client_or_exit`
- `beeper_triage/output.py` — add `resolve_json_flag`
- `beeper_triage/verbs.py` — NEW; the four verb commands + `register(app)`
- `beeper_triage/beeper_client.py` — add adapter methods (`get_chat` fix, `mark_read`, `mark_unread`, `add_reaction`, `remove_reaction`, `start_chat`, `upload_asset`, `send_message` attachment support)
- `beeper_triage/cli.py` — import bootstrap from `runtime`; call `verbs.register(app)`
- `tests/test_runtime.py` — repoint patches to `runtime`
- `tests/test_verbs.py` — NEW; command tests
- `tests/test_adapter.py` — NEW; adapter method tests

---

### Task 1: Upgrade the SDK to 5.0.0, pin it, fix `get_chat`, regression-check

**Files:** Modify `pyproject.toml`, `beeper_triage/beeper_client.py`.

- [ ] **Step 1: Pin the SDK version**

In `pyproject.toml` `dependencies`, change `"beeper_desktop_api"` to `"beeper_desktop_api==5.0.0"`.

- [ ] **Step 2: Upgrade the installed tool**

Run: `cd ~/projects/personal/beeper-triage && uv tool install -e . --force`
Then verify: `python -c "import importlib.metadata as m; print(m.version('beeper_desktop_api'))"`
Expected: `5.0.0`.

- [ ] **Step 3: Fix the `get_chat` adapter method (v5 has `retrieve`, not `get`)**

In `beeper_triage/beeper_client.py`, the `get_chat` method calls `self._client.chats.get(chat_id)`. Neither v4.1.296 nor v5.0.0 has `chats.get` — the method is `chats.retrieve`. Change that single call:

```python
            return self._client.chats.retrieve(chat_id)
```
(Leave the surrounding `try/except BeeperSDKError` wrapper unchanged.)

- [ ] **Step 4: Audit the other adapter SDK calls against v5 (read-only check)**

Run this introspection to confirm every method the adapter calls exists in v5:
```bash
python - <<'PY'
from beeper_desktop_api import BeeperDesktop
c = BeeperDesktop(access_token="dummy")
for path in ["chats.list","chats.retrieve","chats.create","accounts.list",
             "accounts.contacts.search","messages.list","messages.send","messages.search"]:
    obj=c
    ok=True
    for part in path.split("."):
        obj=getattr(obj,part,None)
        if obj is None: ok=False; break
    print(("OK  " if ok else "MISSING ")+path)
PY
```
Expected: all `OK`. If any is `MISSING`, STOP and report — the adapter needs that call fixed before proceeding.

- [ ] **Step 5: Run the full suite + smoke check**

Run: `python -m pytest -q`
Expected: all tests pass (26 from Phase 1; the previously-pre-existing failure is already fixed on main).

Run: `beeper triage --help` and `beeper new-chat --help`
Expected: both exit 0.

(If you have a live Beeper connection, also run `beeper new-chat --help`-level smoke against the real instance is optional; the suite + import-time checks are the gate here.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml beeper_triage/beeper_client.py
git commit -m "upgrade beeper_desktop_api 4.1.296 -> 5.0.0 (pinned); fix get_chat (chats.retrieve)"
```

---

### Task 2: Extract `runtime.py` + add output/verb-dispatch helpers + `verbs.py` scaffold

This unblocks the verb modules: they need `_build_client` / `_require_env` without importing `cli.py` (which will import `verbs`, so a `cli ← verbs ← cli` cycle must be avoided). Move the bootstrap to `runtime.py`; both `cli.py` and `verbs.py` import from it.

**Files:** Create `beeper_triage/runtime.py`, `beeper_triage/verbs.py`; modify `beeper_triage/cli.py`, `beeper_triage/output.py`; modify `tests/test_runtime.py`.

- [ ] **Step 1: Create `runtime.py` by relocating the bootstrap functions verbatim**

Move these functions **unchanged** from `cli.py` into a new `beeper_triage/runtime.py`: `_require_env`, `_detect_wsl_host_ip`, `_probe_proxy_port`, `_start_proxy_via_powershell`, `_ensure_proxy`, `_resolve_base_url`, `_build_client`. Delete them from `cli.py`.

At the top of `runtime.py` add the imports those functions use:
```python
"""Connection bootstrap: env, WSL proxy detection/launch, SDK client construction."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

import typer

from .beeper_client import BeeperClient, BeeperSDKError

logger = logging.getLogger(__name__)
```
(If any moved function references a module-level constant defined in `cli.py`, move that constant too. Verify by reading each moved function's body. If a moved function needs something that must stay in `cli.py`, STOP and report rather than guessing.)

- [ ] **Step 2: Update `cli.py` to import the relocated helpers**

At the top of `cli.py`, add:
```python
from .runtime import _build_client, _ensure_proxy, _require_env, _resolve_base_url
```
Remove the now-deleted function definitions. Confirm `cli.py`'s `triage` and `new-chat` bodies still resolve (`_build_client`, `_require_env` are the names they call).

- [ ] **Step 3: Repoint the runtime tests**

In `tests/test_runtime.py`, change `import beeper_triage.cli as cli` to `import beeper_triage.runtime as rt`, and replace every `cli.` reference with `rt.` (e.g. `rt._resolve_base_url`, `rt._ensure_proxy`, `rt.socket`, `rt.BeeperClient`, `rt.typer`).

- [ ] **Step 4: Run the runtime + full suite to verify the move is behaviour-preserving**

Run: `python -m pytest tests/test_runtime.py -v` → Expected: 6 passed.
Run: `python -m pytest -q` → Expected: all pass; `beeper triage --help` still exits 0.

- [ ] **Step 5: Add `resolve_json_flag` to `output.py`**

Append to `beeper_triage/output.py`:
```python
def resolve_json_flag(agent: bool, json_flag: Optional[bool]) -> Optional[bool]:
    """Agent mode always forces JSON; otherwise honour the explicit --json/--no-json
    flag (None = auto-detect from TTY in is_json_mode)."""
    return True if agent else json_flag
```

- [ ] **Step 6: Write the failing test for `resolve_json_flag` and `build_client_or_exit`**

Create `tests/test_verbs.py`:
```python
"""Tests for verb commands and shared verb dispatch."""
from unittest.mock import MagicMock

from beeper_triage.output import resolve_json_flag


def test_resolve_json_flag_agent_forces_json():
    assert resolve_json_flag(True, None) is True
    assert resolve_json_flag(True, False) is True


def test_resolve_json_flag_non_agent_passes_through():
    assert resolve_json_flag(False, None) is None
    assert resolve_json_flag(False, True) is True
    assert resolve_json_flag(False, False) is False
```

Run: `python -m pytest tests/test_verbs.py -q` → Expected: FAIL (ImportError until Step 5 saved) then PASS once `resolve_json_flag` exists. (If Step 5 is already saved, these pass immediately — that's fine; they lock the behaviour.)

- [ ] **Step 7: Create `verbs.py` with the shared dispatch helper + empty registration**

Create `beeper_triage/verbs.py`:
```python
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
    return None
```

- [ ] **Step 8: Wire `verbs.register(app)` into `cli.py`**

In `cli.py`, after `app = typer.Typer(add_completion=False)` and after the command definitions (end of module is fine), add:
```python
from . import verbs

verbs.register(app)
```
(Import at the bottom or top; if at top, it's safe because `verbs` imports only from `runtime`/`output`/`beeper_client`, not from `cli`.)

- [ ] **Step 9: Verify no import cycle + suite green**

Run: `python -c "import beeper_triage.cli"` → Expected: no ImportError.
Run: `python -m pytest -q` → Expected: all pass. `beeper --help` exits 0.

- [ ] **Step 10: Commit**

```bash
git add beeper_triage/runtime.py beeper_triage/verbs.py beeper_triage/cli.py beeper_triage/output.py tests/test_runtime.py tests/test_verbs.py
git commit -m "extract runtime.py; add verbs.py scaffold + resolve_json_flag/build_client_or_exit"
```

---

### Task 3: `mark-read` / `mark-unread`

**Files:** Modify `beeper_triage/beeper_client.py`, `beeper_triage/verbs.py`; create `tests/test_adapter.py`; extend `tests/test_verbs.py`.

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_adapter.py`:
```python
"""Tests for BeeperClient adapter methods (SDK mocked)."""
from unittest.mock import MagicMock

import pytest

from beeper_triage.beeper_client import BeeperClient, BeeperSDKError


def _adapter():
    c = BeeperClient.__new__(BeeperClient)  # bypass __init__ (no real SDK/connection)
    c._client = MagicMock()
    return c


def test_mark_read_calls_sdk():
    c = _adapter()
    c.mark_read("!chat")
    c._client.chats.mark_read.assert_called_once_with("!chat")


def test_mark_unread_calls_sdk():
    c = _adapter()
    c.mark_unread("!chat")
    c._client.chats.mark_unread.assert_called_once_with("!chat")


def test_mark_read_wraps_errors():
    c = _adapter()
    c._client.chats.mark_read.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.mark_read("!chat")
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: FAIL (`mark_read` not defined).

- [ ] **Step 2: Add the adapter methods**

Add to `BeeperClient` in `beeper_client.py` (after `send_message`):
```python
    def mark_read(self, chat_id: str) -> Any:
        try:
            return self._client.chats.mark_read(chat_id)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to mark chat read via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

    def mark_unread(self, chat_id: str) -> Any:
        try:
            return self._client.chats.mark_unread(chat_id)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to mark chat unread via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: 3 passed.

- [ ] **Step 3: Write failing command tests**

Append to `tests/test_verbs.py`:
```python
import json

from typer.testing import CliRunner

from beeper_triage.cli import app

runner = CliRunner()


def test_mark_read_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["mark-read", "!chat", "--json"])
    assert result.exit_code == 0
    fake.mark_read.assert_called_once_with("!chat")
    assert json.loads(result.stdout) == {"chatID": "!chat", "status": "read"}


def test_mark_unread_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["mark-unread", "!chat", "--json"])
    assert result.exit_code == 0
    fake.mark_unread.assert_called_once_with("!chat")
    assert json.loads(result.stdout) == {"chatID": "!chat", "status": "unread"}
```

Run: `python -m pytest tests/test_verbs.py -q` → Expected: FAIL (no `mark-read` command).

- [ ] **Step 4: Implement the commands in `verbs.py`**

Add to `verbs.py`, and register them:
```python
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
```
Update `register`:
```python
def register(app: typer.Typer) -> None:
    app.command("mark-read")(_mark_read)
    app.command("mark-unread")(_mark_unread)
```

Run: `python -m pytest tests/test_verbs.py -q` → Expected: all pass.

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q` → Expected: all pass. `beeper mark-read --help` exits 0.
```bash
git add beeper_triage/beeper_client.py beeper_triage/verbs.py tests/test_adapter.py tests/test_verbs.py
git commit -m "add mark-read/mark-unread verbs + adapter methods"
```

---

### Task 4: `react` (add / remove)

**Files:** Modify `beeper_triage/beeper_client.py`, `beeper_triage/verbs.py`; extend `tests/test_adapter.py`, `tests/test_verbs.py`.

- [ ] **Step 1: Write failing adapter tests**

Append to `tests/test_adapter.py`:
```python
def test_add_reaction_calls_sdk():
    c = _adapter()
    c.add_reaction("!chat", "$msg", "👍")
    c._client.chats.messages.reactions.add.assert_called_once_with(
        "$msg", chat_id="!chat", reaction_key="👍"
    )


def test_remove_reaction_calls_sdk():
    c = _adapter()
    c.remove_reaction("!chat", "$msg", "👍")
    c._client.chats.messages.reactions.delete.assert_called_once_with(
        "👍", chat_id="!chat", message_id="$msg"
    )
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: FAIL.

- [ ] **Step 2: Add adapter methods**

Add to `BeeperClient`:
```python
    def add_reaction(self, chat_id: str, message_id: str, reaction_key: str) -> Any:
        try:
            return self._client.chats.messages.reactions.add(
                message_id, chat_id=chat_id, reaction_key=reaction_key
            )
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to add reaction via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

    def remove_reaction(self, chat_id: str, message_id: str, reaction_key: str) -> Any:
        try:
            return self._client.chats.messages.reactions.delete(
                reaction_key, chat_id=chat_id, message_id=message_id
            )
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to remove reaction via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: pass.

- [ ] **Step 3: Write failing command tests**

Append to `tests/test_verbs.py`:
```python
def test_react_add_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["react", "!chat", "$msg", "👍", "--json"])
    assert result.exit_code == 0
    fake.add_reaction.assert_called_once_with("!chat", "$msg", "👍")
    assert json.loads(result.stdout)["action"] == "added"


def test_react_remove_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["react", "!chat", "$msg", "👍", "--remove", "--json"])
    assert result.exit_code == 0
    fake.remove_reaction.assert_called_once_with("!chat", "$msg", "👍")
    assert json.loads(result.stdout)["action"] == "removed"
```

Run: `python -m pytest tests/test_verbs.py -q` → Expected: FAIL.

- [ ] **Step 4: Implement the command**

Add to `verbs.py`:
```python
def _react(
    chat_id: str = typer.Argument(..., help="Chat ID."),
    message_id: str = typer.Argument(..., help="Message ID to react to."),
    emoji: str = typer.Argument(..., help="Reaction emoji / key."),
    remove: bool = typer.Option(False, "--remove", help="Remove the reaction instead of adding."),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Add (or --remove) an emoji reaction on a message."""
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        if remove:
            client.remove_reaction(chat_id, message_id, emoji)
            action = "removed"
        else:
            client.add_reaction(chat_id, message_id, emoji)
            action = "added"
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=resolve_json_flag(agent, json_), human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit(
        {"chatID": chat_id, "messageID": message_id, "reaction": emoji, "action": action},
        json_flag=resolve_json_flag(agent, json_),
        human=f"{action.capitalize()} {emoji} on {message_id}.",
    )
```
Add to `register`: `app.command("react")(_react)`.

Run: `python -m pytest tests/test_verbs.py -q` → Expected: pass.

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q` → Expected: all pass.
```bash
git add beeper_triage/beeper_client.py beeper_triage/verbs.py tests/test_adapter.py tests/test_verbs.py
git commit -m "add react verb (add/--remove) + adapter methods"
```

---

### Task 5: `start` (new DM / chat)

`chats.start` takes `account_id`, a `user` dict (one of id/email/phone_number/username), optional `message_text` and `allow_invite`.

**Files:** Modify `beeper_triage/beeper_client.py`, `beeper_triage/verbs.py`; extend tests.

- [ ] **Step 1: Write failing adapter tests**

Append to `tests/test_adapter.py`:
```python
def test_start_chat_phone():
    c = _adapter()
    c._client.chats.start.return_value = MagicMock()
    c.start_chat("acct1", user={"phone_number": "+15551234567"}, message_text="hi")
    c._client.chats.start.assert_called_once_with(
        account_id="acct1", user={"phone_number": "+15551234567"}, message_text="hi"
    )


def test_start_chat_omits_message_when_none():
    c = _adapter()
    c.start_chat("acct1", user={"username": "alice"})
    c._client.chats.start.assert_called_once_with(
        account_id="acct1", user={"username": "alice"}
    )
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: FAIL.

- [ ] **Step 2: Add the adapter method**

Add to `BeeperClient`:
```python
    def start_chat(
        self, account_id: str, *, user: dict[str, str], message_text: Optional[str] = None
    ) -> Any:
        try:
            kwargs: dict[str, Any] = {"account_id": account_id, "user": user}
            if message_text:
                kwargs["message_text"] = message_text
            return self._client.chats.start(**kwargs)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to start chat via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

Run: `python -m pytest tests/test_adapter.py -q` → Expected: pass.

- [ ] **Step 3: Write failing command test**

The command takes `account_id` and exactly one identifier option (`--phone` / `--username` / `--email` / `--user-id`), optional `--text`. Append to `tests/test_verbs.py`:
```python
def test_start_command_phone(monkeypatch):
    fake = MagicMock()
    fake.start_chat.return_value = MagicMock(chat_id="!new")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(
        app, ["start", "acct1", "--phone", "+15551234567", "--text", "hi", "--json"]
    )
    assert result.exit_code == 0
    fake.start_chat.assert_called_once_with(
        "acct1", user={"phone_number": "+15551234567"}, message_text="hi"
    )


def test_start_command_requires_one_identifier(monkeypatch):
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: MagicMock())
    result = runner.invoke(app, ["start", "acct1", "--json"])
    assert result.exit_code != 0
    assert "identifier" in result.stdout.lower() or "identifier" in str(result.exception).lower()
```

Run: `python -m pytest tests/test_verbs.py -q` → Expected: FAIL.

- [ ] **Step 4: Implement the command**

Add to `verbs.py`:
```python
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
    identifiers = {"phone_number": phone, "username": username, "email": email, "id": user_id}
    provided = {k: v for k, v in identifiers.items() if v}
    eff_json = resolve_json_flag(agent, json_)
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
```
Add to `register`: `app.command("start")(_start)`.

Run: `python -m pytest tests/test_verbs.py -q` → Expected: pass.

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q` → Expected: all pass.
```bash
git add beeper_triage/beeper_client.py beeper_triage/verbs.py tests/test_adapter.py tests/test_verbs.py
git commit -m "add start verb (new DM) + adapter method"
```

---

### Task 6: `send` with `--attach` (and `--reply-to`)

The MCP and current adapter only send text. v5 supports attachments: `assets.upload(file=...)` → response carrying an upload id → `messages.send(chat_id, attachment={upload_id, type, file_name, mime_type}, text=...)`.

**Files:** Modify `beeper_triage/beeper_client.py`, `beeper_triage/verbs.py`; extend tests.

- [ ] **Step 1: Confirm the `AssetUploadResponse` id field (introspection)**

The `attachment.upload_id` must come from the upload response. Confirm the field name:
```bash
python - <<'PY'
from beeper_desktop_api.types import AssetUploadResponse
print(list(AssetUploadResponse.model_fields.keys()))
PY
```
Record the id field name (likely `upload_id`). Use it in Step 3's `_extract_upload_id`. If the field name differs from `upload_id`, adjust `_extract_upload_id` accordingly and note it in the commit message.

- [ ] **Step 2: Write failing adapter tests**

Append to `tests/test_adapter.py`:
```python
from pathlib import Path


def test_upload_asset_calls_sdk(tmp_path):
    c = _adapter()
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n")
    c.upload_asset(f, mime_type="image/png")
    args, kwargs = c._client.assets.upload.call_args
    assert kwargs["mime_type"] == "image/png"
    assert kwargs["file_name"] == "pic.png"
    assert kwargs["file"] == f


def test_send_message_with_attachment_builds_attachment(monkeypatch):
    c = _adapter()
    # upload returns an object exposing the id field as `upload_id`
    c._client.assets.upload.return_value = MagicMock(upload_id="up123")
    f = Path("/tmp/pic.png")
    c.send_message("!chat", text="caption", attachment_path=f, attachment_mime="image/png")
    # send called with attachment dict referencing the upload id + derived type
    _, kwargs = c._client.messages.send.call_args
    assert kwargs["chat_id"] == "!chat"
    assert kwargs["text"] == "caption"
    assert kwargs["attachment"]["upload_id"] == "up123"
    assert kwargs["attachment"]["type"] == "image"
    assert kwargs["attachment"]["mime_type"] == "image/png"
```
(Adjust `upload_id=` in the mock to the field name confirmed in Step 1.)

Run: `python -m pytest tests/test_adapter.py -q` → Expected: FAIL.

- [ ] **Step 3: Extend the adapter**

Add to `beeper_client.py` (module-level helper + methods). Keep the existing text-only `send_message` signature working by making `attachment_path` optional:

```python
import mimetypes  # add to the imports at the top of beeper_client.py


_ATTACHMENT_TYPE_BY_PREFIX = {"image": "image", "video": "video", "audio": "audio"}


def _attachment_type_for_mime(mime_type: Optional[str]) -> str:
    """Map a MIME type to the SDK attachment `type` enum; default to 'file'."""
    if mime_type:
        prefix = mime_type.split("/", 1)[0]
        return _ATTACHMENT_TYPE_BY_PREFIX.get(prefix, "file")
    return "file"
```

Then add methods to `BeeperClient`:
```python
    def upload_asset(self, path: "Path", mime_type: Optional[str] = None) -> Any:
        import mimetypes
        from pathlib import Path as _P

        p = _P(path)
        mime = mime_type or mimetypes.guess_type(p.name)[0]
        try:
            kwargs: dict[str, Any] = {"file": p, "file_name": p.name}
            if mime:
                kwargs["mime_type"] = mime
            return self._client.assets.upload(**kwargs)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to upload asset via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

And REPLACE the existing `send_message` with an attachment-aware version (preserving the text/reply behaviour):
```python
    def send_message(
        self,
        chat_id: str,
        text: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        attachment_path: "Optional[Path]" = None,
        attachment_mime: Optional[str] = None,
    ) -> Any:
        import mimetypes
        from pathlib import Path as _P

        try:
            kwargs: dict[str, Any] = {"chat_id": chat_id}
            if text is not None:
                kwargs["text"] = text
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            if attachment_path is not None:
                p = _P(attachment_path)
                mime = attachment_mime or mimetypes.guess_type(p.name)[0]
                up = self.upload_asset(p, mime_type=mime)
                upload_id = self._get_attr(up, "upload_id", "uploadID", "id")
                kwargs["attachment"] = {
                    "upload_id": upload_id,
                    "type": _attachment_type_for_mime(mime),
                    "file_name": p.name,
                }
                if mime:
                    kwargs["attachment"]["mime_type"] = mime
            return self._client.messages.send(**kwargs)
        except BeeperSDKError:
            raise
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to send message via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```
NOTE: this changes `send_message`'s signature (adds optional params; `text` now optional). The existing callers in `cli.py` (`triage` and `new-chat`) pass `text=`/`reply_to_message_id=` positionally or by keyword — verify they still work: `triage` calls `client.send_message(selection, edited, reply_to_message_id=reply_to_id)` (text positional) and `new-chat` calls `client.send_message(chat_id=chat_id, text=chunk, reply_to_message_id=None)`. Both remain valid. Run the full suite in Step 5 to confirm.

Run: `python -m pytest tests/test_adapter.py -q` → Expected: pass (adjust the `upload_id` field name if Step 1 found a different one).

- [ ] **Step 4: Command tests + implementation**

Append to `tests/test_verbs.py`:
```python
def test_send_text_command(monkeypatch):
    fake = MagicMock()
    fake.send_message.return_value = MagicMock(message_id="$m1")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["send", "!chat", "--text", "hello", "--json"])
    assert result.exit_code == 0
    _, kwargs = fake.send_message.call_args
    assert kwargs["text"] == "hello" and kwargs["attachment_path"] is None


def test_send_attach_command(monkeypatch, tmp_path):
    f = tmp_path / "pic.png"
    f.write_bytes(b"x")
    fake = MagicMock()
    fake.send_message.return_value = MagicMock(message_id="$m2")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["send", "!chat", "--attach", str(f), "--json"])
    assert result.exit_code == 0
    _, kwargs = fake.send_message.call_args
    assert str(kwargs["attachment_path"]) == str(f)


def test_send_requires_text_or_attach(monkeypatch):
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: MagicMock())
    result = runner.invoke(app, ["send", "!chat", "--json"])
    assert result.exit_code != 0
```

Add to `verbs.py`:
```python
from pathlib import Path


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
    message_id = getattr(result, "message_id", None) or getattr(result, "messageID", None)
    emit({"chatID": chat_id, "messageID": message_id, "status": "sent"},
         json_flag=eff_json, human=f"Sent to {chat_id} (message {message_id}).")
```
Add to `register`: `app.command("send")(_send)`.

Run: `python -m pytest tests/test_verbs.py -q` → Expected: pass.

- [ ] **Step 5: Full suite + smoke + commit**

Run: `python -m pytest -q` → Expected: ALL pass (existing triage/new-chat use of `send_message` still works).
Run: `beeper send --help` → Expected: exit 0, shows `--text/--attach/--reply-to`.
```bash
git add beeper_triage/beeper_client.py beeper_triage/verbs.py tests/test_adapter.py tests/test_verbs.py
git commit -m "add send verb with --attach (asset upload) + --reply-to; extend adapter"
```

---

## Self-Review

**Spec coverage (Phase 2 items):**
- "Upgrade SDK→5.0.0 + regression" → Task 1. ✅
- "`send --attach`" → Task 6. ✅ · "`react`" → Task 4. ✅ · "`mark-read`/`mark-unread`" → Task 3. ✅ · "`start`" → Task 5. ✅
- "wire `output.emit` / JSON-TTY contract" → `resolve_json_flag` (Task 2) used by every verb; `--json/--no-json` + `--agent` on each. ✅
- "extend the adapter (not hand-roll HTTP)" → every verb calls a `beeper_client` method wrapping the SDK. ✅

**Placeholder scan:** Every code step contains complete code. The only runtime-discovery step (Task 6 Step 1, the `AssetUploadResponse` id field) is a defined-type lookup with an explicit fallback (`_get_attr(up, "upload_id", "uploadID", "id")` already tolerates the common variants), not a blank. Task 2 Step 1 relocates *named, existing* functions verbatim (not new code), with a STOP-and-report guard if a moved function has an unexpected dependency.

**Type/name consistency:** adapter methods (`mark_read`, `mark_unread`, `add_reaction`, `remove_reaction`, `start_chat`, `upload_asset`, `send_message`) match between their definitions, the `test_adapter.py` calls, and the `verbs.py` call sites. `build_client_or_exit(*, agent, json_flag)`, `resolve_json_flag(agent, json_flag)`, `emit(data, *, json_flag, human)` are used consistently. SDK call shapes match the introspected v5 signatures exactly (e.g. `reactions.add(message_id, chat_id=, reaction_key=)`).

**Scope:** Tier-1 verbs only. Deliberately deferred: `edit`/`delete`/`dl`/`beeper api` (Phase 3), the routing skill (Phase 4), and splitting `cli.py` further (the new verbs live in `verbs.py`, so `cli.py` does not grow with verb logic).

## Out of scope / deferred
- `beeper api` passthrough and `edit`/`delete`/`dl` → Phase 3.
- Routing skill → Phase 4.
- A live end-to-end send/react against a real chat is a manual post-merge smoke test (needs the WSL proxy + a real chat) — the automated tests mock the SDK boundary.
