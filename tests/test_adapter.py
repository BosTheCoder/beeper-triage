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


def test_raw_request_get_with_body_rejected():
    c = _adapter()
    with pytest.raises(BeeperSDKError):
        c.raw_request("GET", "/v1/x", body={"a": 1})
    c._client.get.assert_not_called()


def test_get_message_wraps_errors():
    c = _adapter()
    c._client.messages.retrieve.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.get_message("!chat", "$msg")


def test_download_attachment_no_src_url():
    c = _adapter()
    att = MagicMock(src_url=None, file_name="pic.png")
    c._client.messages.retrieve.return_value = MagicMock(attachments=[att])
    with pytest.raises(BeeperSDKError):
        c.download_attachment("!chat", "$msg")


def test_download_attachment_serve_fails():
    c = _adapter()
    att = MagicMock(src_url="mxc://x", file_name="pic.png")
    c._client.messages.retrieve.return_value = MagicMock(attachments=[att])
    c._client.assets.serve.side_effect = RuntimeError("boom")
    with pytest.raises(BeeperSDKError):
        c.download_attachment("!chat", "$msg")


def test_raw_request_error_includes_status():
    c = _adapter()
    err = RuntimeError("forbidden")
    err.status_code = 403
    c._client.get.side_effect = err
    with pytest.raises(BeeperSDKError) as ei:
        c.raw_request("GET", "/v1/x")
    assert "403" in str(ei.value)


def _account_stub(account_id, network):
    a = MagicMock()
    a.account_id = account_id
    a.network = network
    a.user = None
    return a


def test_list_accounts_caches_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        BeeperClient, "ACCOUNTS_CACHE_FILE", str(tmp_path / "accounts.json")
    )
    c = _adapter()
    c._client.accounts.list.return_value = [_account_stub("acc1", "WhatsApp")]

    first = c.list_accounts()
    assert first == {"acc1": ("WhatsApp", "acc1")}

    # Second call: even if the SDK would now return nothing, the cache serves it.
    c._client.accounts.list.return_value = []
    second = c.list_accounts()
    assert second == {"acc1": ("WhatsApp", "acc1")}
    assert c._client.accounts.list.call_count == 1


def test_list_accounts_use_cache_false_bypasses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        BeeperClient, "ACCOUNTS_CACHE_FILE", str(tmp_path / "accounts.json")
    )
    c = _adapter()
    c._client.accounts.list.return_value = [_account_stub("acc1", "WhatsApp")]
    c.list_accounts()
    c.list_accounts(use_cache=False)
    assert c._client.accounts.list.call_count == 2
