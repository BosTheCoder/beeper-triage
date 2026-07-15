"""Tests for the surface-agnostic triage engine (beeper_triage.inbox)."""

from __future__ import annotations

import pytest

from beeper_triage.beeper_client import BeeperChat, BeeperMessage
from beeper_triage import inbox


def _chat(cid, **kw):
    defaults = dict(
        chat_id=cid,
        title=cid,
        unread_count=1,
        preview_is_sender=False,  # they spoke last -> we owe a reply
        is_muted=False,
        last_activity_ms=0,
        is_group=False,
        network="whatsapp",
        is_archived=False,
    )
    defaults.update(kw)
    return BeeperChat(**defaults)


class FakeClient:
    def __init__(self, chats=None, messages=None):
        self._chats = chats or []
        self._messages = messages or {}
        self.sent = []
        self.archived = []

    def list_chats(self, use_cache=False):
        return list(self._chats)

    def list_messages(self, chat_id, limit=None, since_ms=None):
        return list(self._messages.get(chat_id, []))

    def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))

    def archive(self, chat_id, archived=True):
        self.archived.append((chat_id, archived))


class FakeORC:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create_chat_completion(self, model, messages):
        self.calls.append((model, messages))
        return self.reply


# ------------------------------ build_queue ------------------------------

def test_queue_keeps_only_unreplied_unarchived_1to1():
    chats = [
        _chat("owe"),  # kept
        _chat("i-replied", preview_is_sender=True),  # dropped: we spoke last
        _chat("archived", is_archived=True),  # dropped
        _chat("group", is_group=True),  # dropped by default
        _chat("muted", is_muted=True),  # dropped by default
    ]
    q = inbox.build_queue(FakeClient(chats))
    assert [c.chat_id for c in q] == ["owe"]


def test_queue_groups_and_muted_toggles():
    chats = [_chat("g", is_group=True), _chat("m", is_muted=True), _chat("plain")]
    q = inbox.build_queue(
        FakeClient(chats), inbox.QueueFilters(groups=True, include_muted=True)
    )
    assert {c.chat_id for c in q} == {"g", "m", "plain"}


def test_queue_network_filter_and_recency_order():
    chats = [
        _chat("old", last_activity_ms=1, network="whatsapp"),
        _chat("new", last_activity_ms=9, network="whatsapp"),
        _chat("tg", network="telegram"),
    ]
    q = inbox.build_queue(FakeClient(chats), inbox.QueueFilters(networks=["whatsapp"]))
    assert [c.chat_id for c in q] == ["new", "old"]


# ------------------------------ chat_view --------------------------------

def test_clean_text_strips_html_and_none():
    assert inbox.clean_text("<p>hi<br><br>there</p>") == "hi\n\nthere"
    assert inbox.clean_text("None") == ""
    assert inbox.clean_text(None) == ""
    assert inbox.clean_text("a &amp; b") == "a & b"


def test_chat_view_drops_none_and_cleans_html():
    msgs = [
        BeeperMessage("1", "Ann", False, "<p>hey<br>you</p>", 100),
        BeeperMessage("2", "Ann", False, "None", 150),  # null body -> dropped
        BeeperMessage("3", "Me", True, "sup", 200),
    ]
    view = inbox.chat_view(FakeClient(messages={"c": msgs}), "c")
    assert [m.text for m in view.messages] == ["hey\nyou", "sup"]


def test_chat_view_oldest_first_and_transcript():
    msgs = [
        BeeperMessage("2", "Me", True, "later", 200),
        BeeperMessage("1", "Ann", False, "hi", 100),
    ]
    view = inbox.chat_view(FakeClient(messages={"c": msgs}), "c")
    assert [m.text for m in view.messages] == ["hi", "later"]
    assert view.transcript() == "Ann: hi\nMe: later"


# ----------------------------- draft_options -----------------------------

def test_draft_options_parses_json_array():
    reply = '[{"type":"schedule","text":"Sat 2pm?"},{"type":"close","text":"Sorted!"}]'
    drafts = inbox.draft_options(FakeORC(reply), "m", "Ann: hi")
    assert [(d.type, d.text) for d in drafts] == [
        ("schedule", "Sat 2pm?"),
        ("close", "Sorted!"),
    ]


def test_draft_options_strips_fence_and_bad_types():
    reply = '```json\n[{"type":"weird","text":"hey"}]\n```'
    drafts = inbox.draft_options(FakeORC(reply), "m", "Ann: hi")
    assert drafts[0].type == "going"  # unknown type coerced
    assert drafts[0].text == "hey"


def test_draft_options_dedupes_and_caps():
    reply = '[{"type":"going","text":"hey"},{"type":"close","text":"hey"},{"type":"going","text":"yo"}]'
    drafts = inbox.draft_options(FakeORC(reply), "m", "Ann: hi", count=5)
    assert [d.text for d in drafts] == ["hey", "yo"]


def test_draft_options_falls_back_to_single_draft():
    drafts = inbox.draft_options(FakeORC("just some text"), "m", "Ann: hi")
    assert drafts == [inbox.Draft(type="going", text="just some text")]


def test_draft_options_empty_transcript_no_call():
    orc = FakeORC("[]")
    assert inbox.draft_options(orc, "m", "   ") == []
    assert orc.calls == []


# ------------------------------- resolve ---------------------------------

def test_resolve_send_sends_then_archives():
    c = FakeClient()
    r = inbox.resolve(c, "chat1", "send", text="hello")
    assert c.sent == [("chat1", "hello")]
    assert c.archived == [("chat1", True)]
    assert r.archived and r.sent_text == "hello"


def test_resolve_send_dry_run_touches_nothing():
    c = FakeClient()
    r = inbox.resolve(c, "chat1", "send", text="hello", dry_run=True)
    assert c.sent == [] and c.archived == []
    assert r.dry_run and r.archived


def test_resolve_archive_only():
    c = FakeClient()
    inbox.resolve(c, "chat1", "archive")
    assert c.sent == [] and c.archived == [("chat1", True)]


def test_resolve_skip_is_noop():
    c = FakeClient()
    inbox.resolve(c, "chat1", "skip")
    assert c.sent == [] and c.archived == []


def test_resolve_send_requires_text():
    with pytest.raises(ValueError):
        inbox.resolve(FakeClient(), "c", "send", text="  ")


def test_resolve_rejects_unknown_action():
    with pytest.raises(ValueError):
        inbox.resolve(FakeClient(), "c", "nope")
