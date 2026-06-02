# Beeper CLI Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the command `beeper-triage` → `beeper`, remove the duplicated connection-bootstrap code, and add a JSON/TTY output helper — all with zero change to existing `triage` / `new-chat` / `export` behaviour — then register `beeper` in the `toolkit` framework.

**Architecture:** `beeper-triage` is already a `typer` app (`beeper_triage.cli:app`) with two commands (`triage`, `new-chat`) backed by `beeper_client.py`, a thin adapter over the official `beeper_desktop_api` SDK. This phase is pure refactor + rename + registration — no new SDK calls, no new verbs. It de-risks the foundation that Phases 2–4 build on.

**Tech Stack:** Python ≥3.10, `typer`, `beeper_desktop_api` SDK, `pytest`, `uv` (tool install), and the `toolkit` shell framework (separate repo).

**Branch:** Work on `beeper-cli-redesign` (already created in the `beeper-triage` repo). The toolkit task (Task 4) is in the **separate** `~/projects/personal/toolkit` repo.

**Spec:** `../specs/2026-06-02-beeper-cli-redesign-design.md`

---

### Task 1: Rename the console script to `beeper`

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]`)
- Modify: `README.md` (usage examples)

- [ ] **Step 1: Add the `beeper` console script, keep `beeper-triage` as a transitional alias**

In `pyproject.toml`, change the `[project.scripts]` block from:

```toml
[project.scripts]
beeper-triage = "beeper_triage.cli:app"
beeper-proxy = "beeper_triage.wsl_proxy:main"
```

to:

```toml
[project.scripts]
beeper = "beeper_triage.cli:app"
beeper-triage = "beeper_triage.cli:app"  # deprecated alias; remove after migration
beeper-proxy = "beeper_triage.wsl_proxy:main"
```

- [ ] **Step 2: Reinstall the tool so the new console script is created**

Run: `cd ~/projects/personal/beeper-triage && uv tool install -e . --force`
Expected: install succeeds; output lists `beeper`, `beeper-triage`, `beeper-proxy` as installed executables.

- [ ] **Step 3: Verify the new command name works and shows both subcommands**

Run: `beeper --help`
Expected: help text lists the `triage` and `new-chat` commands (exit code 0).

Run: `beeper triage --help`
Expected: the triage command help (the same as `beeper-triage triage --help` previously).

- [ ] **Step 4: Update README usage examples**

In `README.md`, replace the bare command `beeper-triage` with `beeper triage` in the Usage examples (e.g. `beeper triage --max-chats 30 --message-window 7d`). Leave the `BEEPER_BASE_URL` / setup notes unchanged. Add one line under Usage: `> The command was renamed from `beeper-triage` to `beeper`; `beeper-triage` still works as a deprecated alias.`

- [ ] **Step 5: Commit**

```bash
cd ~/projects/personal/beeper-triage
git add pyproject.toml README.md
git commit -m "rename console script beeper-triage -> beeper (keep alias)"
```

---

### Task 2: Extract the shared connection bootstrap (de-dup `triage` + `new-chat`)

The base-URL resolution + proxy fallback + `BeeperClient` construction is copy-pasted in both commands (in `triage` around `cli.py:636-660`, in `new-chat` around `cli.py:964-986`). Extract it into two module-level helpers and call them from both sites. Behaviour is preserved, with one deliberate consistency fix: the "configured proxy not reachable — auto-detecting" info line is now suppressed in agent mode for **both** commands (previously only `new-chat` suppressed it).

**Files:**
- Modify: `beeper_triage/cli.py` (add helpers near the other `_`-helpers, ~line 560; rewire both command bodies)
- Test: `tests/test_runtime.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runtime.py`:

```python
"""Tests for the shared connection-bootstrap helpers."""
from unittest.mock import MagicMock, patch

import pytest

import beeper_triage.cli as cli


def _fake_socket_ok():
    sock = MagicMock()
    sock.connect.return_value = None
    return sock


def _fake_socket_refused():
    sock = MagicMock()
    sock.connect.side_effect = ConnectionRefusedError()
    return sock


def test_resolve_base_url_uses_reachable_configured_url(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_ok())
    assert cli._resolve_base_url(agent=False) == "http://172.28.96.1:23399"


def test_resolve_base_url_falls_back_to_proxy_when_unreachable(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_refused())
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    assert cli._resolve_base_url(agent=True) == "http://127.0.0.1:23399"


def test_resolve_base_url_uses_proxy_when_env_missing(monkeypatch):
    monkeypatch.delenv("BEEPER_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    assert cli._resolve_base_url(agent=False) == "http://127.0.0.1:23399"


def test_build_client_passes_resolved_url(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_base_url", lambda *, agent: "http://h:1")
    captured = {}

    def fake_client(*, access_token, base_url):
        captured["access_token"] = access_token
        captured["base_url"] = base_url
        return MagicMock()

    monkeypatch.setattr(cli, "BeeperClient", fake_client)
    cli._build_client("tok123", agent=False)
    assert captured == {"access_token": "tok123", "base_url": "http://h:1"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/personal/beeper-triage && python -m pytest tests/test_runtime.py -v`
Expected: FAIL — `AttributeError: module 'beeper_triage.cli' has no attribute '_resolve_base_url'`.

- [ ] **Step 3: Add the helpers to `cli.py`**

Insert these two functions in `cli.py` immediately after `_ensure_proxy` (around line 565, before the first `@app.command()`):

```python
def _resolve_base_url(*, agent: bool) -> str:
    """Return a reachable Beeper API base URL.

    Uses BEEPER_BASE_URL if set and reachable; otherwise (or if the configured
    URL refuses a connection) auto-detects/starts the WSL proxy. The
    'not reachable' info line is suppressed in agent mode.
    """
    base_url = os.getenv("BEEPER_BASE_URL")
    if not base_url:
        return _ensure_proxy()
    try:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((parsed.hostname, parsed.port))
        sock.close()
    except (ConnectionRefusedError, OSError, socket.timeout):
        if not agent:
            typer.echo(f"[!] Configured proxy at {base_url} not reachable — auto-detecting ...")
        base_url = _ensure_proxy()
    return base_url


def _build_client(access_token: str, *, agent: bool) -> "BeeperClient":
    """Resolve the base URL and construct a BeeperClient.

    Raises BeeperSDKError if the SDK client cannot be constructed; callers
    keep their own error-handling UX.
    """
    base_url = _resolve_base_url(agent=agent)
    return BeeperClient(access_token=access_token, base_url=base_url)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/personal/beeper-triage && python -m pytest tests/test_runtime.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Rewire the `triage` command to use `_build_client`**

In the `triage` command body (~`cli.py:636-660`), replace the duplicated block — the `base_url = os.getenv("BEEPER_BASE_URL")` … through the `client = BeeperClient(...)` `try/except` that raises `typer.BadParameter` — with:

```python
    try:
        client = _build_client(access_token, agent=agent)
    except BeeperSDKError as exc:
        logger.exception("Failed to initialize Beeper client")
        raise typer.BadParameter(str(exc)) from exc
```

- [ ] **Step 6: Rewire the `new-chat` command to use `_build_client`**

In the `new-chat` command body (~`cli.py:964-986`), replace the equivalent duplicated block — through the `client = BeeperClient(...)` `try/except` that emits JSON and raises `typer.Exit(code=1)` — with:

```python
    try:
        client = _build_client(access_token, agent=agent)
    except BeeperSDKError as exc:
        if agent:
            typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=1)
```

- [ ] **Step 7: Run the full test suite + a smoke check**

Run: `cd ~/projects/personal/beeper-triage && python -m pytest -q`
Expected: all tests pass (existing `tests/test_cli.py` + new `tests/test_runtime.py`).

Run: `beeper triage --help` and `beeper new-chat --help`
Expected: both exit 0 (no import/refactor breakage).

- [ ] **Step 8: Commit**

```bash
cd ~/projects/personal/beeper-triage
git add beeper_triage/cli.py tests/test_runtime.py
git commit -m "extract shared _resolve_base_url/_build_client bootstrap; de-dup triage + new-chat"
```

---

### Task 3: Add the JSON/TTY output helper (foundation for Phase 2)

This introduces the output contract module. It is tested in isolation here; the Phase-2 verbs are its first consumers. Existing commands are intentionally left untouched in this phase to preserve their behaviour.

**Files:**
- Create: `beeper_triage/output.py`
- Test: `tests/test_output.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_output.py`:

```python
"""Tests for the JSON/TTY output helper."""
import json

from beeper_triage.output import emit, is_json_mode


def test_is_json_mode_explicit_overrides_tty(monkeypatch):
    # Explicit flag wins regardless of TTY state.
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert is_json_mode(True) is True
    assert is_json_mode(False) is False


def test_is_json_mode_auto_uses_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert is_json_mode(None) is False  # human at a terminal
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert is_json_mode(None) is True   # piped / agent


def test_emit_json_mode(capsys):
    emit({"a": 1}, json_flag=True, human="ignored")
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1}


def test_emit_human_mode(capsys):
    emit({"a": 1}, json_flag=False, human="hello human")
    assert capsys.readouterr().out.strip() == "hello human"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/projects/personal/beeper-triage && python -m pytest tests/test_output.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'beeper_triage.output'`.

- [ ] **Step 3: Create `beeper_triage/output.py`**

```python
"""Output helpers: JSON for machines (non-TTY / --json), pretty text for humans."""
from __future__ import annotations

import json
import sys
from typing import Any, Optional


def is_json_mode(json_flag: Optional[bool]) -> bool:
    """Decide JSON vs human output.

    An explicit --json / --no-json flag (True/False) always wins. When the flag
    is None (unset), default to JSON whenever stdout is not a TTY (i.e. piped or
    invoked by an agent), and human output when attached to a terminal.
    """
    if json_flag is not None:
        return json_flag
    return not sys.stdout.isatty()


def emit(data: Any, *, json_flag: Optional[bool] = None, human: Optional[str] = None) -> None:
    """Print `data` as JSON in machine mode, or `human` text in human mode.

    Falls back to indented JSON for humans when no `human` string is supplied.
    """
    if is_json_mode(json_flag):
        print(json.dumps(data, default=str))
    else:
        print(human if human is not None else json.dumps(data, indent=2, default=str))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/projects/personal/beeper-triage && python -m pytest tests/test_output.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/projects/personal/beeper-triage
git add beeper_triage/output.py tests/test_output.py
git commit -m "add JSON/TTY output helper (output.emit/is_json_mode) for Phase 2 verbs"
```

---

### Task 4: Register `beeper` in the toolkit framework (separate repo)

This task is in `~/projects/personal/toolkit`, not `beeper-triage`. It promotes the existing `bpt` stub into a proper `beeper` tool entry with a scoped tag and a `@needs beeper` dependency check.

**Files:**
- Create: `~/projects/personal/toolkit/tools/comms/beeper.sh`
- Delete: `~/projects/personal/toolkit/tools/other/bpt.sh`

- [ ] **Step 1: Create the `comms` category tool file**

Create `~/projects/personal/toolkit/tools/comms/beeper.sh`:

```sh
# @tool  beeper
# @cat   Comms
# @desc  Beeper CLI — chats, messages, send/react/triage
# @flags <verb> [args]   (triage|new-chat|send|react|read|export|api …)
# @needs beeper
# @tags  beeper
beeper() { command beeper "$@"; }

# @tool  bpt
# @cat   Comms
# @desc  Beeper triage (shortcut for `beeper triage`)
# @needs beeper
# @tags  beeper
alias bpt='beeper triage'
```

- [ ] **Step 2: Remove the old stub**

Run: `rm ~/projects/personal/toolkit/tools/other/bpt.sh`

- [ ] **Step 3: Rebuild the toolkit and verify `beeper` is registered**

Run: `toolkit build`
Expected: build succeeds with no parse errors.

Run: `toolkit list | grep -i beeper`
Expected: shows both `beeper` and `bpt` under the `Comms` category with their `@desc` text.

- [ ] **Step 4: Verify the dependency check**

Run: `toolkit doctor`
Expected: no `missing-dep` for `beeper` (the console script from Task 1 is on PATH). If `beeper` is reported missing, the Task-1 `uv tool install` did not expose it — fix that before continuing.

- [ ] **Step 5: Note machine scoping (manual, not committed)**

The `beeper` tag must be present in `~/.config/toolkit/profile` on machines where Beeper Desktop runs for these tools to load. Add `beeper` to the profile's tag list on this machine if not already present. (This file is machine-local and not part of the repo.)

- [ ] **Step 6: Commit (in the toolkit repo)**

```bash
cd ~/projects/personal/toolkit
git add tools/comms/beeper.sh
git rm tools/other/bpt.sh
git commit -m "register beeper CLI as a Comms tool; scope to 'beeper' tag; drop bpt stub"
```

---

## Self-Review

**Spec coverage (Phase 1 items):**
- "Rename command → `beeper`" → Task 1. ✅
- "Extract the shared connection bootstrap out of triage/new-chat" → Task 2. ✅
- "Add the JSON/TTY output helper" → Task 3. ✅
- "Preserve triage/export/reads behaviour exactly" → Task 2 Step 7 (full suite green) + no edits to export/read paths. ✅ (One named, deliberate consistency change: agent-mode suppression of the "not reachable" info line in `triage`.)
- "Promote `bpt.sh` → `tools/comms/beeper.sh` (scoped tag, `@needs beeper`)" → Task 4. ✅
- "Tests green" → Steps run `pytest` after each code task. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows the command and expected result. The two helpers (`_resolve_base_url`, `_build_client`) and the two `output` functions are fully defined before they are referenced.

**Type/name consistency:** `_resolve_base_url(*, agent)`, `_build_client(access_token, *, agent)`, `is_json_mode(json_flag)`, `emit(data, *, json_flag, human)` — names match between their definitions, their tests, and their call sites. `BeeperClient`/`BeeperSDKError` are the existing names imported at `cli.py:22`.

**Scope:** Foundation only — no new verbs, no new SDK calls. Phases 2–4 (Tier-1 verbs, passthrough + Tier-2, routing skill) are separate plans.

## Out of scope for Phase 1 (deferred)
- Splitting `cli.py` (1163 lines) into per-verb modules — defer to Phase 2 when the verb count justifies it.
- Wiring `output.emit` into existing commands — deferred to preserve current behaviour; Phase 2 verbs are its first consumers.
- Removing the `beeper-triage` deprecated alias — a later cleanup once nothing references it.
