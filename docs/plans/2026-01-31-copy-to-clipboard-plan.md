# Copy-to-Clipboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "copy to clipboard" action so users can grab chat context for use in other tools instead of replying.

**Architecture:** After messages are fetched, present a numbered action prompt. "Copy" formats the transcript with timestamps and pipes it to a detected clipboard tool. "Reply" continues the existing flow unchanged.

**Tech Stack:** Python stdlib only — `datetime`, `shutil`, `subprocess`. No new dependencies.

---

### Task 1: Add test infrastructure and `_format_transcript_with_timestamps()`

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_cli.py`
- Modify: `beeper_triage/cli.py:58` (add new function after `_format_transcript`)

**Step 1: Create test directory and write failing test**

Create `tests/__init__.py` (empty file).

Create `tests/test_cli.py`:

```python
"""Tests for cli helper functions."""

from beeper_triage.beeper_client import BeeperMessage
from beeper_triage.cli import _format_transcript_with_timestamps


def test_format_transcript_with_timestamps():
    messages = [
        BeeperMessage(
            message_id="1",
            sender_name="Alice",
            is_sender=False,
            text="Hey, are you free?",
            timestamp_ms=1706623920000,  # 2024-01-30 14:32 UTC
        ),
        BeeperMessage(
            message_id="2",
            sender_name="Me",
            is_sender=True,
            text="Yeah, what's up?",
            timestamp_ms=1706623980000,  # 2024-01-30 14:33 UTC
        ),
    ]
    result = _format_transcript_with_timestamps(messages)
    lines = result.strip().split("\n")
    assert len(lines) == 2
    # Check format: [YYYY-MM-DD HH:MM] Speaker: text
    assert "] Alice: Hey, are you free?" in lines[0]
    assert "] You: Yeah, what's up?" in lines[1]
    # Check timestamp bracket format exists
    assert lines[0].startswith("[")
    assert "] " in lines[0]


def test_format_transcript_with_timestamps_skips_empty():
    messages = [
        BeeperMessage(
            message_id="1",
            sender_name="Alice",
            is_sender=False,
            text="",
            timestamp_ms=1706623920000,
        ),
        BeeperMessage(
            message_id="2",
            sender_name="Alice",
            is_sender=False,
            text="Hello",
            timestamp_ms=1706623980000,
        ),
    ]
    result = _format_transcript_with_timestamps(messages)
    lines = result.strip().split("\n")
    assert len(lines) == 1
    assert "Hello" in lines[0]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ImportError — `_format_transcript_with_timestamps` doesn't exist yet.

**Step 3: Implement `_format_transcript_with_timestamps`**

In `beeper_triage/cli.py`, add after the existing `_format_transcript` function (after line 66) and add `datetime` to the imports at top:

```python
import datetime
```

```python
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
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/ beeper_triage/cli.py
git commit -m "feat: add _format_transcript_with_timestamps for clipboard export"
```

---

### Task 2: Add `_detect_clipboard_cmd()` and `_copy_to_clipboard()`

**Files:**
- Modify: `tests/test_cli.py` (add tests)
- Modify: `beeper_triage/cli.py` (add two functions)

**Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
from unittest.mock import patch, MagicMock

from beeper_triage.cli import _detect_clipboard_cmd, _copy_to_clipboard


def test_detect_clipboard_cmd_clip_exe():
    """clip.exe should be preferred (WSL)."""
    with patch("shutil.which", side_effect=lambda cmd: "/mnt/c/clip.exe" if cmd == "clip.exe" else None):
        assert _detect_clipboard_cmd() == ["clip.exe"]


def test_detect_clipboard_cmd_wl_copy():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/wl-copy" if cmd == "wl-copy" else None):
        assert _detect_clipboard_cmd() == ["wl-copy"]


def test_detect_clipboard_cmd_xclip():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None):
        assert _detect_clipboard_cmd() == ["xclip", "-selection", "clipboard"]


def test_detect_clipboard_cmd_xsel():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/xsel" if cmd == "xsel" else None):
        assert _detect_clipboard_cmd() == ["xsel", "--clipboard", "--input"]


def test_detect_clipboard_cmd_none():
    with patch("shutil.which", return_value=None):
        assert _detect_clipboard_cmd() is None


def test_copy_to_clipboard_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _copy_to_clipboard("hello", ["clip.exe"])
        mock_run.assert_called_once_with(["clip.exe"], input="hello", text=True, check=True)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ImportError — functions don't exist yet.

**Step 3: Implement both functions**

Add to `beeper_triage/cli.py` after `_format_transcript_with_timestamps`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_cli.py beeper_triage/cli.py
git commit -m "feat: add clipboard detection and copy helpers"
```

---

### Task 3: Add `_pick_action()` prompt

**Files:**
- Modify: `tests/test_cli.py` (add tests)
- Modify: `beeper_triage/cli.py` (add function)

**Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
from beeper_triage.cli import _pick_action


def test_pick_action_reply():
    with patch("builtins.input", return_value="1"):
        assert _pick_action() == "reply"


def test_pick_action_copy():
    with patch("builtins.input", return_value="2"):
        assert _pick_action() == "copy"


def test_pick_action_default_is_reply():
    with patch("builtins.input", return_value=""):
        assert _pick_action() == "reply"


def test_pick_action_invalid_then_valid():
    with patch("builtins.input", side_effect=["3", "2"]):
        assert _pick_action() == "copy"


def test_pick_action_ctrl_c():
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert _pick_action() is None
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ImportError — `_pick_action` doesn't exist yet.

**Step 3: Implement `_pick_action`**

Add to `beeper_triage/cli.py` after `_copy_to_clipboard`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_cli.py beeper_triage/cli.py
git commit -m "feat: add _pick_action prompt for reply vs copy"
```

---

### Task 4: Wire action choice into `triage()` flow

**Files:**
- Modify: `beeper_triage/cli.py:148-197` (restructure triage flow)

**Step 1: Modify the `triage()` function**

In `beeper_triage/cli.py`, replace the code from after the transcript building (line 154) through the end of the function (line 197) with the action branching logic.

The new flow after line 153 (`raise typer.Exit(code=0)`):

```python
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

    # action == "reply" — existing flow continues unchanged
    if not no_llm:
        if not model:
            model = default_model
        if not model:
            raise typer.BadParameter("OPENROUTER_MODEL or --model is required.")

    draft = ""
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
```

**Important detail:** The OpenRouter API key validation and model check currently happen at the top of `triage()` (lines 103-108). Since "copy" doesn't need the LLM, move that validation into the reply branch. Remove lines 103-108 from the top and place them inside the `action == "reply"` block as shown above.

**Step 2: Run all tests**

Run: `python -m pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 3: Manual smoke test**

Run: `beeper-triage triage --max-chats 5`
- Select a chat via fzf
- Verify the action prompt appears: `Action: [1] Reply  [2] Copy to clipboard`
- Choose `2` and verify text is copied to clipboard
- Paste somewhere to confirm content and timestamp format

**Step 4: Commit**

```bash
git add beeper_triage/cli.py
git commit -m "feat: wire copy-to-clipboard action into triage flow"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `CLAUDE.md` (add copy action to docs)
- Modify: `README.md` (add usage example)

**Step 1: Update CLAUDE.md**

Add the copy-to-clipboard action to the "Core Workflow" section and document the new helper functions.

**Step 2: Update README.md**

Add usage example showing the copy action.

**Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document copy-to-clipboard feature"
```
