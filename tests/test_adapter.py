"""Tests for BeeperClient adapter methods (SDK mocked)."""
from pathlib import Path
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


def test_mark_unread_wraps_errors():
    c = _adapter()
    c._client.chats.mark_unread.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.mark_unread("!chat")


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


def test_add_reaction_wraps_errors():
    c = _adapter()
    c._client.chats.messages.reactions.add.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.add_reaction("!chat", "$msg", "👍")


def test_remove_reaction_wraps_errors():
    c = _adapter()
    c._client.chats.messages.reactions.delete.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.remove_reaction("!chat", "$msg", "👍")


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


def test_start_chat_wraps_errors():
    c = _adapter()
    c._client.chats.start.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.start_chat("acct1", user={"username": "alice"})


def test_upload_asset_calls_sdk(tmp_path):
    c = _adapter()
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n")
    c.upload_asset(f, mime_type="image/png")
    _, kwargs = c._client.assets.upload.call_args
    assert kwargs["mime_type"] == "image/png"
    assert kwargs["file_name"] == "pic.png"
    assert kwargs["file"] == f


def test_send_message_text_only_unchanged(tmp_path):
    c = _adapter()
    c.send_message("!chat", text="hello", reply_to_message_id="$r")
    c._client.messages.send.assert_called_once_with(
        chat_id="!chat", text="hello", reply_to_message_id="$r"
    )


def test_send_message_with_attachment_builds_attachment():
    c = _adapter()
    c._client.assets.upload.return_value = MagicMock(upload_id="up123")
    c.send_message("!chat", text="caption", attachment_path=Path("/tmp/pic.png"),
                   attachment_mime="image/png")
    _, kwargs = c._client.messages.send.call_args
    assert kwargs["chat_id"] == "!chat"
    assert kwargs["text"] == "caption"
    assert kwargs["attachment"]["upload_id"] == "up123"
    assert kwargs["attachment"]["type"] == "image"
    assert kwargs["attachment"]["mime_type"] == "image/png"
    assert kwargs["attachment"]["file_name"] == "pic.png"
