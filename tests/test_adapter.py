"""Tests for BeeperClient adapter methods (SDK mocked)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from beeper_triage.beeper_client import (
    _LABELS_CACHE,
    BeeperClient,
    BeeperSDKError,
    _attachment_type_for_mime,
)


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


# --- attachment type hints -------------------------------------------------

def test_attachment_type_gif_is_its_own_type():
    # A GIF sent as a plain "image" gets retracted by the WhatsApp bridge; the
    # SDK has a distinct "gif" type for it.
    assert _attachment_type_for_mime("image/gif") == "gif"
    assert _attachment_type_for_mime("image/GIF; charset=binary") == "gif"


def test_attachment_type_prefix_map_unchanged():
    assert _attachment_type_for_mime("image/png") == "image"
    assert _attachment_type_for_mime("video/mp4") == "video"
    assert _attachment_type_for_mime("audio/ogg") == "audio"
    assert _attachment_type_for_mime("application/pdf") == "file"
    assert _attachment_type_for_mime(None) == "file"


def test_send_message_tags_a_gif_as_gif(tmp_path):
    c = _adapter()
    gif = tmp_path / "goat.gif"
    gif.write_bytes(b"GIF89a")
    c._client.assets.upload.return_value = MagicMock(upload_id="up1")
    c.send_message("!chat", attachment_path=gif)
    kwargs = c._client.messages.send.call_args.kwargs
    assert kwargs["attachment"]["type"] == "gif"
    assert kwargs["attachment"]["mime_type"] == "image/gif"


# --- labels (Matrix account data) ------------------------------------------

class _User:
    def __init__(self, uid):
        self.id = uid


class _Bridge:
    def __init__(self, btype):
        self.type = btype


class _Account:
    def __init__(self, account_id, uid, btype):
        self.account_id = account_id
        self.user = _User(uid)
        self.bridge = _Bridge(btype)


_LABELS_PAYLOAD = {
    "20b66533-304c-4091-b2a7-ff8212db016d": {
        "createdAt": 1773142218010,
        "title": "High Priority",
        "rooms": ["!a:beeper.local", "!b:beeper.local"],
        "isShownInInbox": True,
    }
}


def _labelled(payload, accounts=None):
    _LABELS_CACHE.clear()  # the 60s cache is module-level; don't leak between tests
    c = _adapter()
    c._matrix_uid = None
    c._client.accounts.list.return_value = accounts or [
        _Account("whatsapp", "447730784352", "whatsapp"),
        _Account("matrix", "@me:beeper.com", "matrix"),
    ]
    c.raw_request = MagicMock(return_value=payload)
    return c


def test_matrix_user_id_from_the_matrix_account():
    c = _labelled(_LABELS_PAYLOAD)
    assert c.matrix_user_id() == "@me:beeper.com"
    # memoised — the accounts list is not re-read
    assert c.matrix_user_id() == "@me:beeper.com"
    assert c._client.accounts.list.call_count == 1


def test_list_labels_reads_matrix_account_data():
    c = _labelled(_LABELS_PAYLOAD)
    labels = c.list_labels(use_cache=False)
    assert [(l.label_id, l.title) for l in labels] == [
        ("20b66533-304c-4091-b2a7-ff8212db016d", "High Priority")
    ]
    assert labels[0].chat_ids == {"!a:beeper.local", "!b:beeper.local"}
    path = c.raw_request.call_args.args[1]
    assert path == (
        "/_matrix/client/v3/user/@me:beeper.com/account_data/com.beeper.labels"
    )


def test_list_labels_missing_event_is_no_labels_not_an_error():
    # Beeper answers HTTP 500 when the user has never made a label.
    c = _labelled(_LABELS_PAYLOAD)
    c.raw_request = MagicMock(side_effect=BeeperSDKError(
        'HTTP 500 GET /_matrix/... failed: InternalServerError: '
        '{"errcode": "M_UNKNOWN", "error": "getAccountData failed: '
        'No account data event found with type \"com.beeper.labels\""}'
    ))
    assert c.list_labels(use_cache=False) == []


def test_list_labels_other_errors_still_raise():
    c = _labelled(_LABELS_PAYLOAD)
    c.raw_request = MagicMock(side_effect=BeeperSDKError("HTTP 403 GET ... failed"))
    with pytest.raises(BeeperSDKError):
        c.list_labels(use_cache=False)


def test_list_labels_tolerates_malformed_payload():
    c = _labelled({
        "ok": {"title": "Keep", "rooms": ["!a:beeper.local", None]},
        "no-rooms": {"title": "Empty"},
        "bad-rooms": {"title": "Odd", "rooms": "not-a-list"},
        "untitled": {"rooms": []},
        "not-a-dict": "nope",
    })
    by_id = {l.label_id: l for l in c.list_labels(use_cache=False)}
    assert "not-a-dict" not in by_id  # skipped, not fatal
    assert by_id["ok"].chat_ids == {"!a:beeper.local"}
    assert by_id["no-rooms"].chat_ids == set()
    assert by_id["bad-rooms"].chat_ids == set()
    assert by_id["untitled"].title == "untitled"  # id stands in for a missing title


def test_list_labels_returns_empty_without_a_matrix_account():
    c = _labelled(_LABELS_PAYLOAD, accounts=[_Account("whatsapp", "4477", "whatsapp")])
    assert c.list_labels(use_cache=False) == []
    c.raw_request.assert_not_called()


def test_list_labels_caches_in_process():
    c = _labelled(_LABELS_PAYLOAD)
    c.list_labels()
    c.list_labels()
    assert c.raw_request.call_count == 1


# --------------------------------------------------------------------------
# chat cache round-trip
# --------------------------------------------------------------------------

def _cached_client(tmp_path, monkeypatch):
    c = BeeperClient.__new__(BeeperClient)
    monkeypatch.setattr(BeeperClient, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(BeeperClient, "CACHE_FILE", str(tmp_path / "chats.json"))
    return c


def test_cache_round_trip_preserves_every_field(tmp_path, monkeypatch):
    # The save row used to be a hand-written dict, so every field added after it
    # was silently dropped and came back as its default on a cache hit.
    from dataclasses import fields

    from beeper_triage.beeper_client import BeeperChat

    c = _cached_client(tmp_path, monkeypatch)
    original = BeeperChat(
        chat_id="!x:beeper.local",
        title="Pinned thing",
        unread_count=3,
        preview_is_sender=True,
        is_muted=True,
        last_activity_ms=1_700_000_000_000,
        account_id="acct",
        network_type="whatsapp",
        account_label="Me",
        is_group=True,
        network="whatsapp",
        is_archived=True,
        is_pinned=True,
        preview_text="see you then",
    )
    c._save_cache([original])
    (restored,) = c._get_cache()
    for f in fields(BeeperChat):
        assert getattr(restored, f.name) == getattr(original, f.name), f.name


def test_cache_round_trip_keeps_is_pinned(tmp_path, monkeypatch):
    from beeper_triage.beeper_client import BeeperChat

    c = _cached_client(tmp_path, monkeypatch)
    c._save_cache([BeeperChat(
        chat_id="!p", title="Pinned", unread_count=0, preview_is_sender=False,
        is_muted=False, is_pinned=True,
    )])
    assert c._get_cache()[0].is_pinned is True


def test_expired_cache_is_ignored(tmp_path, monkeypatch):
    from beeper_triage.beeper_client import BeeperChat

    c = _cached_client(tmp_path, monkeypatch)
    monkeypatch.setattr(BeeperClient, "CACHE_TTL_MS", -1)
    c._save_cache([BeeperChat(
        chat_id="!p", title="T", unread_count=0, preview_is_sender=False, is_muted=False,
    )])
    assert c._get_cache() is None
