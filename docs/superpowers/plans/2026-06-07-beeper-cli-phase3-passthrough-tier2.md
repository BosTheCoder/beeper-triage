# Beeper CLI Phase 3 — Passthrough + Tier-2 Verbs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `beeper api` raw passthrough escape hatch plus the Tier-2 verbs `edit`, `delete`, and `dl` (download incoming media) to the `beeper` CLI.

**Architecture:** Extend the existing SDK-adapter (`beeper_client.py`) with four new methods that wrap real, introspected v5.0.0 SDK calls, then add four Typer verbs to `verbs.py` following the established Phase-2 verb pattern (`resolve_json_flag` → `build_client_or_exit` → try/except `BeeperSDKError` → `emit`). The passthrough reuses the SDK's own request pipeline (`client.get/post/...(path, cast_to=object, ...)`) so it inherits the exact auth + base-URL + WSL-proxy behaviour the typed verbs already use — no hand-rolled HTTP.

**Tech Stack:** Python 3.13, `typer`, `beeper_desktop_api==5.0.0`, `pytest`, `typer.testing.CliRunner`.

---

## Grounding — verified v5.0.0 facts (introspected live 2026-06-07)

All signatures below were confirmed against the installed SDK and, where safe, exercised against a real chat. **Do not re-guess these.**

- **Passthrough transport:** `client.<method>(path, *, cast_to, body=None, options=RequestOptions, ...)` exists for `get/post/put/patch/delete`. `cast_to=object` returns **parsed JSON** (dict/list). `get` has **no** `body` param; `post/put/patch/delete` do. Query params go via `options={"params": {...}}`. Auth lives in `client.access_token` and is injected by the SDK pipeline — a hand-rolled httpx call would miss it, so we MUST go through these methods.
- **Edit:** `client.messages.update(message_id, *, chat_id, text) -> MessageUpdateResponse`.
- **Delete:** `client.messages.delete(message_id, *, chat_id, for_everyone: Optional[bool]=Omit) -> None`.
- **Retrieve (for dl):** `client.messages.retrieve(message_id, *, chat_id) -> Message`. The `Message` has `.attachments` (list); each attachment has `.src_url`, `.file_name`, `.mime_type`, `.file_size`, `.type`.
- **Serve bytes (for dl):** `client.assets.serve(url=<src_url>) -> BinaryAPIResponse`, which has `.write_to_file(path)` (writes the raw bytes — verified it reproduced a 70-byte PNG exactly). (`assets.download(url=)` returns only `{src_url, error}`, NOT bytes — use `serve`.)

## File Structure

- **Modify** `beeper_triage/beeper_client.py` — add adapter methods `edit_message`, `delete_message`, `get_message`, `download_attachment`, `raw_request`. Each wraps a real SDK call and re-raises failures as `BeeperSDKError` (matching the existing pattern at `beeper_client.py:433-480`).
- **Modify** `beeper_triage/verbs.py` — add verb functions `_edit`, `_delete`, `_dl`, `_api`, and register them in `register(app)` (currently `verbs.py:146-152`).
- **Modify** `tests/test_adapter.py` — adapter unit tests (SDK mocked via `BeeperClient.__new__` + `_client = MagicMock()`).
- **Modify** `tests/test_verbs.py` — verb tests via `CliRunner`, mocking `beeper_triage.verbs.build_client_or_exit`.

## Conventions to follow (already in the repo)

- Verb pattern (see `verbs.py:26-39` `_mark_read`):
  ```python
  eff_json = resolve_json_flag(agent, json_)
  client = build_client_or_exit(agent=agent, json_flag=json_)
  try:
      ...client call...
  except BeeperSDKError as exc:
      emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
      raise typer.Exit(code=1)
  emit({...}, json_flag=eff_json, human="...")
  ```
- Adapter pattern (see `beeper_client.py:433-439` `mark_read`): wrap the SDK call in try/except, raise `BeeperSDKError(f"... via SDK: {type(exc).__name__}: {str(exc)}")`.
- Arg-validation failures use `raise typer.Exit(code=2)` (see `_send` at `verbs.py:129-132`).

---

## Task 1: Adapter — `edit_message`

**Files:**
- Modify: `beeper_triage/beeper_client.py` (add after `remove_reaction`, ~line 467)
- Test: `tests/test_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_edit_message_calls_sdk():
    c = _adapter()
    c.edit_message("!chat", "$msg", "new text")
    c._client.messages.update.assert_called_once_with(
        "$msg", chat_id="!chat", text="new text"
    )


def test_edit_message_wraps_errors():
    c = _adapter()
    c._client.messages.update.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.edit_message("!chat", "$msg", "x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k edit_message -v`
Expected: FAIL — `AttributeError: 'BeeperClient' object has no attribute 'edit_message'`

- [ ] **Step 3: Implement**

```python
    def edit_message(self, chat_id: str, message_id: str, text: str) -> Any:
        try:
            return self._client.messages.update(
                message_id, chat_id=chat_id, text=text
            )
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to edit message via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k edit_message -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/beeper_client.py tests/test_adapter.py
git commit -m "feat(adapter): add edit_message wrapping messages.update"
```

---

## Task 2: Adapter — `delete_message`

**Files:**
- Modify: `beeper_triage/beeper_client.py` (after `edit_message`)
- Test: `tests/test_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_message_calls_sdk_default():
    c = _adapter()
    c.delete_message("!chat", "$msg")
    c._client.messages.delete.assert_called_once_with(
        "$msg", chat_id="!chat", for_everyone=False
    )


def test_delete_message_for_everyone():
    c = _adapter()
    c.delete_message("!chat", "$msg", for_everyone=True)
    c._client.messages.delete.assert_called_once_with(
        "$msg", chat_id="!chat", for_everyone=True
    )


def test_delete_message_wraps_errors():
    c = _adapter()
    c._client.messages.delete.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.delete_message("!chat", "$msg")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k delete_message -v`
Expected: FAIL — no attribute `delete_message`

- [ ] **Step 3: Implement**

```python
    def delete_message(
        self, chat_id: str, message_id: str, for_everyone: bool = False
    ) -> Any:
        try:
            return self._client.messages.delete(
                message_id, chat_id=chat_id, for_everyone=for_everyone
            )
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to delete message via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k delete_message -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/beeper_client.py tests/test_adapter.py
git commit -m "feat(adapter): add delete_message wrapping messages.delete"
```

---

## Task 3: Adapter — `get_message` + `download_attachment`

`download_attachment` orchestrates: retrieve the message, pick attachment `index`, resolve the output path (default = attachment's own `file_name` in cwd), stream bytes via `assets.serve(...).write_to_file(out)`, return metadata.

**Files:**
- Modify: `beeper_triage/beeper_client.py` (after `delete_message`)
- Test: `tests/test_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_get_message_calls_sdk():
    c = _adapter()
    c.get_message("!chat", "$msg")
    c._client.messages.retrieve.assert_called_once_with("$msg", chat_id="!chat")


def test_download_attachment_default_path(tmp_path, monkeypatch):
    c = _adapter()
    att = MagicMock(src_url="mxc://x", file_name="pic.png",
                    mime_type="image/png", file_size=70)
    c._client.messages.retrieve.return_value = MagicMock(attachments=[att])
    monkeypatch.chdir(tmp_path)  # default out = file_name in cwd
    result = c.download_attachment("!chat", "$msg")
    c._client.assets.serve.assert_called_once_with(url="mxc://x")
    c._client.assets.serve.return_value.write_to_file.assert_called_once()
    assert result["file_name"] == "pic.png"
    assert result["mime_type"] == "image/png"
    assert result["path"].endswith("pic.png")


def test_download_attachment_explicit_out(tmp_path):
    c = _adapter()
    att = MagicMock(src_url="mxc://x", file_name="pic.png",
                    mime_type="image/png", file_size=70)
    c._client.messages.retrieve.return_value = MagicMock(attachments=[att])
    out = tmp_path / "saved.png"
    result = c.download_attachment("!chat", "$msg", out_path=str(out))
    c._client.assets.serve.return_value.write_to_file.assert_called_once_with(str(out))
    assert result["path"] == str(out)


def test_download_attachment_no_attachments():
    c = _adapter()
    c._client.messages.retrieve.return_value = MagicMock(attachments=[])
    with pytest.raises(BeeperSDKError):
        c.download_attachment("!chat", "$msg")


def test_download_attachment_bad_index():
    c = _adapter()
    att = MagicMock(src_url="mxc://x", file_name="pic.png")
    c._client.messages.retrieve.return_value = MagicMock(attachments=[att])
    with pytest.raises(BeeperSDKError):
        c.download_attachment("!chat", "$msg", index=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k "get_message or download_attachment" -v`
Expected: FAIL — no such attributes

- [ ] **Step 3: Implement**

```python
    def get_message(self, chat_id: str, message_id: str) -> Any:
        try:
            return self._client.messages.retrieve(message_id, chat_id=chat_id)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to retrieve message via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

    def download_attachment(
        self,
        chat_id: str,
        message_id: str,
        *,
        index: int = 0,
        out_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Download one attachment from a message to disk.

        Returns {path, file_name, mime_type, file_size}. Raises BeeperSDKError
        if the message has no attachment at *index* or the serve/write fails.
        """
        message = self.get_message(chat_id, message_id)
        attachments = self._get_attr(message, "attachments", default=None) or []
        if not attachments:
            raise BeeperSDKError("Message has no attachments to download.")
        if index < 0 or index >= len(attachments):
            raise BeeperSDKError(
                f"Attachment index {index} out of range (message has {len(attachments)})."
            )
        att = attachments[index]
        src_url = self._get_attr(att, "src_url", "srcURL", default=None)
        if not src_url:
            raise BeeperSDKError("Attachment has no source URL.")
        file_name = self._get_attr(att, "file_name", "fileName", default=None) or "attachment"
        target = out_path or os.path.join(os.getcwd(), file_name)
        try:
            response = self._client.assets.serve(url=src_url)
            response.write_to_file(target)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to download attachment via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
        return {
            "path": target,
            "file_name": file_name,
            "mime_type": self._get_attr(att, "mime_type", "mimeType", default=None),
            "file_size": self._get_attr(att, "file_size", "fileSize", default=None),
        }
```

Note: `os` is already imported at `beeper_client.py:8`; `Optional` and `Any` are already imported at line 11. No new imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k "get_message or download_attachment" -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/beeper_client.py tests/test_adapter.py
git commit -m "feat(adapter): add get_message + download_attachment (retrieve + assets.serve)"
```

---

## Task 4: Adapter — `raw_request` (passthrough)

**Files:**
- Modify: `beeper_triage/beeper_client.py` (after `download_attachment`)
- Test: `tests/test_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_raw_request_get_no_body():
    c = _adapter()
    c._client.get.return_value = {"ok": True}
    out = c.raw_request("GET", "/v1/accounts")
    c._client.get.assert_called_once_with("/v1/accounts", cast_to=object)
    assert out == {"ok": True}


def test_raw_request_get_with_query():
    c = _adapter()
    c.raw_request("get", "/v1/x", query={"limit": "5"})
    c._client.get.assert_called_once_with(
        "/v1/x", cast_to=object, options={"params": {"limit": "5"}}
    )


def test_raw_request_post_with_body():
    c = _adapter()
    c.raw_request("POST", "/v1/x", body={"a": 1})
    c._client.post.assert_called_once_with("/v1/x", cast_to=object, body={"a": 1})


def test_raw_request_rejects_unknown_method():
    c = _adapter()
    with pytest.raises(BeeperSDKError):
        c.raw_request("TRACE", "/v1/x")


def test_raw_request_wraps_errors():
    c = _adapter()
    c._client.get.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.raw_request("GET", "/v1/x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k raw_request -v`
Expected: FAIL — no attribute `raw_request`

- [ ] **Step 3: Implement**

```python
    _RAW_METHODS = {"get", "post", "put", "patch", "delete"}

    def raw_request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]] = None,
        body: Optional[Any] = None,
    ) -> Any:
        """Raw passthrough to any /v1 endpoint via the SDK's request pipeline.

        Returns parsed JSON (dict/list) on success. Reuses the SDK's configured
        auth + base URL so the WSL-proxy bootstrap applies unchanged.
        """
        verb = method.lower()
        if verb not in self._RAW_METHODS:
            raise BeeperSDKError(
                f"Unsupported HTTP method: {method!r} (use GET/POST/PUT/PATCH/DELETE)."
            )
        fn = getattr(self._client, verb)
        kwargs: dict[str, Any] = {"cast_to": object}
        if query:
            kwargs["options"] = {"params": query}
        if body is not None and verb != "get":
            kwargs["body"] = body
        try:
            return fn(path, **kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            prefix = f"HTTP {status} " if status else ""
            raise BeeperSDKError(
                f"{prefix}{method.upper()} {path} failed: {type(exc).__name__}: {str(exc)}"
            ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_adapter.py -k raw_request -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/beeper_client.py tests/test_adapter.py
git commit -m "feat(adapter): add raw_request passthrough (cast_to=object via SDK pipeline)"
```

---

## Task 5: Verb — `edit`

**Files:**
- Modify: `beeper_triage/verbs.py` (add `_edit`; register in `register()`)
- Test: `tests/test_verbs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_edit_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["edit", "!chat", "$msg", "fixed text", "--json"])
    assert result.exit_code == 0
    fake.edit_message.assert_called_once_with("!chat", "$msg", "fixed text")
    out = json.loads(result.stdout)
    assert out == {"chatID": "!chat", "messageID": "$msg", "status": "edited"}


def test_edit_command_error(monkeypatch):
    fake = MagicMock()
    fake.edit_message.side_effect = BeeperSDKError("nope")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["edit", "!chat", "$msg", "x", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k edit -v`
Expected: FAIL — `No such command 'edit'` (exit code 2)

- [ ] **Step 3: Implement**

Add the verb function (after `_send`, before `register`):

```python
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
```

Add to `register()`:

```python
    app.command("edit")(_edit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k edit -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/verbs.py tests/test_verbs.py
git commit -m "feat(verb): add 'beeper edit' (messages.update)"
```

---

## Task 6: Verb — `delete`

**Files:**
- Modify: `beeper_triage/verbs.py` (add `_delete`; register)
- Test: `tests/test_verbs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["delete", "!chat", "$msg", "--json"])
    assert result.exit_code == 0
    fake.delete_message.assert_called_once_with("!chat", "$msg", for_everyone=False)
    out = json.loads(result.stdout)
    assert out == {"chatID": "!chat", "messageID": "$msg",
                   "forEveryone": False, "status": "deleted"}


def test_delete_command_for_everyone(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(
        app, ["delete", "!chat", "$msg", "--for-everyone", "--json"]
    )
    assert result.exit_code == 0
    fake.delete_message.assert_called_once_with("!chat", "$msg", for_everyone=True)
    assert json.loads(result.stdout)["forEveryone"] is True


def test_delete_command_error(monkeypatch):
    fake = MagicMock()
    fake.delete_message.side_effect = BeeperSDKError("nope")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["delete", "!chat", "$msg", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k "delete_command" -v`
Expected: FAIL — `No such command 'delete'`

- [ ] **Step 3: Implement**

```python
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
```

Add to `register()`:

```python
    app.command("delete")(_delete)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k "delete_command" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/verbs.py tests/test_verbs.py
git commit -m "feat(verb): add 'beeper delete' (messages.delete, --for-everyone)"
```

---

## Task 7: Verb — `dl` (download attachment)

**Files:**
- Modify: `beeper_triage/verbs.py` (add `_dl`; register; add `from pathlib import Path` is already imported at `verbs.py:4`)
- Test: `tests/test_verbs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_dl_command(monkeypatch):
    fake = MagicMock()
    fake.download_attachment.return_value = {
        "path": "/tmp/pic.png", "file_name": "pic.png",
        "mime_type": "image/png", "file_size": 70,
    }
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["dl", "!chat", "$msg", "--json"])
    assert result.exit_code == 0
    fake.download_attachment.assert_called_once_with("!chat", "$msg", index=0, out_path=None)
    out = json.loads(result.stdout)
    assert out["path"] == "/tmp/pic.png"
    assert out["status"] == "downloaded"


def test_dl_command_with_out_and_index(monkeypatch):
    fake = MagicMock()
    fake.download_attachment.return_value = {"path": "/tmp/x", "file_name": "x"}
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(
        app, ["dl", "!chat", "$msg", "--out", "/tmp/x", "--index", "2", "--json"]
    )
    assert result.exit_code == 0
    fake.download_attachment.assert_called_once_with("!chat", "$msg", index=2, out_path="/tmp/x")


def test_dl_command_error(monkeypatch):
    fake = MagicMock()
    fake.download_attachment.side_effect = BeeperSDKError("no attachments")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["dl", "!chat", "$msg", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k dl_command -v`
Expected: FAIL — `No such command 'dl'`

- [ ] **Step 3: Implement**

```python
def _dl(
    chat_id: str = typer.Argument(..., help="Chat ID."),
    message_id: str = typer.Argument(..., help="Message ID with the attachment."),
    out: Optional[str] = typer.Option(
        None, "--out", help="Output path (default: the attachment's own filename in cwd)."
    ),
    index: int = typer.Option(
        0, "--index", help="Which attachment to download if the message has several."
    ),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Download an incoming attachment (image/file/etc.) to disk."""
    eff_json = resolve_json_flag(agent, json_)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        result = client.download_attachment(chat_id, message_id, index=index, out_path=out)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit({**result, "status": "downloaded"},
         json_flag=eff_json, human=f"Downloaded to {result['path']}.")
```

Add to `register()`:

```python
    app.command("dl")(_dl)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k dl_command -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/verbs.py tests/test_verbs.py
git commit -m "feat(verb): add 'beeper dl' (download attachment via retrieve + assets.serve)"
```

---

## Task 8: Verb — `api` (raw passthrough)

`--query` is repeatable `KEY=VALUE`; `--body` is a JSON string. Both parsed in the verb. A malformed `--query` item or `--body` is an arg error → exit 2.

**Files:**
- Modify: `beeper_triage/verbs.py` (add `_parse_query_pairs` helper + `_api`; register; add `import json` at top of `verbs.py`)
- Test: `tests/test_verbs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_api_get(monkeypatch):
    fake = MagicMock()
    fake.raw_request.return_value = [{"accountID": "whatsapp"}]
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["api", "GET", "/v1/accounts", "--json"])
    assert result.exit_code == 0
    fake.raw_request.assert_called_once_with("GET", "/v1/accounts", query={}, body=None)
    assert json.loads(result.stdout) == [{"accountID": "whatsapp"}]


def test_api_get_with_query(monkeypatch):
    fake = MagicMock()
    fake.raw_request.return_value = {"items": []}
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(
        app, ["api", "GET", "/v1/x", "--query", "limit=5", "--query", "q=hi", "--json"]
    )
    assert result.exit_code == 0
    fake.raw_request.assert_called_once_with(
        "GET", "/v1/x", query={"limit": "5", "q": "hi"}, body=None
    )


def test_api_post_with_body(monkeypatch):
    fake = MagicMock()
    fake.raw_request.return_value = {"ok": True}
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(
        app, ["api", "POST", "/v1/x", "--body", '{"a": 1}', "--json"]
    )
    assert result.exit_code == 0
    fake.raw_request.assert_called_once_with("POST", "/v1/x", query={}, body={"a": 1})


def test_api_bad_query_item(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["api", "GET", "/v1/x", "--query", "noequals", "--json"])
    assert result.exit_code == 2
    fake.raw_request.assert_not_called()


def test_api_bad_body_json(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["api", "POST", "/v1/x", "--body", "{not json", "--json"])
    assert result.exit_code == 2
    fake.raw_request.assert_not_called()


def test_api_error(monkeypatch):
    fake = MagicMock()
    fake.raw_request.side_effect = BeeperSDKError("HTTP 404 ...")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["api", "GET", "/v1/nope", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k "api_" -v`
Expected: FAIL — `No such command 'api'`

- [ ] **Step 3: Implement**

Add `import json` at the top of `verbs.py` (with the other stdlib imports), the helper, and the verb:

```python
def _parse_query_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse repeated KEY=VALUE strings into a dict. Raises ValueError on a bad item."""
    out: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"Bad --query item {item!r}; expected KEY=VALUE.")
        key, value = item.split("=", 1)
        out[key] = value
    return out


def _api(
    method: str = typer.Argument(..., help="HTTP method: GET/POST/PUT/PATCH/DELETE."),
    path: str = typer.Argument(..., help="API path, e.g. /v1/accounts."),
    query: list[str] = typer.Option(
        [], "--query", "-q", help="Repeatable KEY=VALUE query parameter."
    ),
    body: Optional[str] = typer.Option(
        None, "--body", help="JSON request body (for POST/PUT/PATCH/DELETE)."
    ),
    agent: bool = typer.Option(False, "--agent", help="Agent mode: force JSON output."),
    json_: Optional[bool] = typer.Option(None, "--json/--no-json", help="Force/disable JSON output."),
) -> None:
    """Raw passthrough to any Beeper /v1 endpoint (escape hatch for ops with no verb)."""
    eff_json = resolve_json_flag(agent, json_)
    try:
        query_dict = _parse_query_pairs(query)
    except ValueError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=str(exc))
        raise typer.Exit(code=2)
    body_obj = None
    if body is not None:
        try:
            body_obj = json.loads(body)
        except json.JSONDecodeError as exc:
            msg = f"Invalid --body JSON: {exc}"
            emit({"error": msg}, json_flag=eff_json, human=msg)
            raise typer.Exit(code=2)
    client = build_client_or_exit(agent=agent, json_flag=json_)
    try:
        result = client.raw_request(method, path, query=query_dict, body=body_obj)
    except BeeperSDKError as exc:
        emit({"error": str(exc)}, json_flag=eff_json, human=f"Error: {exc}")
        raise typer.Exit(code=1)
    emit(result, json_flag=eff_json, human=json.dumps(result, indent=2, default=str))
```

Add to `register()`:

```python
    app.command("api")(_api)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py -k "api_" -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add beeper_triage/verbs.py tests/test_verbs.py
git commit -m "feat(verb): add 'beeper api' raw passthrough (--query/--body)"
```

---

## Task 9: Wiring smoke test + full suite

**Files:**
- Test: `tests/test_verbs.py` (one help-registration assertion)

- [ ] **Step 1: Write the registration test**

```python
def test_phase3_verbs_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for verb in ("edit", "delete", "dl", "api"):
        assert verb in result.stdout
```

- [ ] **Step 2: Run it**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest tests/test_verbs.py::test_phase3_verbs_registered -v`
Expected: PASS

- [ ] **Step 3: Run the full suite**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m pytest -q`
Expected: PASS — all prior tests + the new Phase-3 tests green.

- [ ] **Step 4: Manual CLI help check**

Run: `cd /home/bosire/projects/personal/beeper-triage && python -m beeper_triage.cli --help` (or `command beeper --help`)
Expected: `edit`, `delete`, `dl`, `api` listed alongside the Phase-2 verbs.

- [ ] **Step 5: Commit**

```bash
git add tests/test_verbs.py
git commit -m "test: assert Phase 3 verbs are registered on the app"
```

---

## Live smoke test (after merge — manual, like Phase 2)

Against the WhatsApp self-chat `!stApPk0AHQFs5wAY91pU:beeper.local` (account `whatsapp`):
1. `beeper send '<chat>' --text 'edit me' --agent` → note you must re-list to get the real messageID (send returns only pendingMessageID).
2. `beeper api GET /v1/chats/'<chat>'/messages --agent` (or use the MCP) to grab the new message's numeric ID.
3. `beeper edit '<chat>' <msgID> 'edited text' --agent` → verify via read-back.
4. `beeper delete '<chat>' <msgID> --agent` → verify it's gone / tombstoned.
5. `beeper dl '<chat>' <imgMsgID> --out /tmp/dl.png --agent` → verify the file lands.
6. `beeper api GET /v1/accounts --agent` → verify raw JSON list returned.

Note: like Phase 2, automated tests mock the SDK boundary; this confirms the live wiring.

## Self-Review (completed by plan author)

- **Spec coverage:** `beeper api` ✅ (Task 4 + 8), `edit` ✅ (1+5), `delete` ✅ (2+6), `dl` ✅ (3+7). All Phase-3 spec rows covered.
- **Placeholders:** none — every step has concrete code/commands.
- **Type consistency:** adapter method names (`edit_message`, `delete_message`, `get_message`, `download_attachment`, `raw_request`) are used identically in verb tasks; verb registration strings (`edit`/`delete`/`dl`/`api`) match the CliRunner invocations and the Task 9 assertion. `download_attachment` returns the dict shape asserted in both adapter and verb tests.
