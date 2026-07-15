"""Tests for the surface-agnostic triage engine (beeper_triage.inbox)."""

from __future__ import annotations

import pytest

from beeper_triage.beeper_client import BeeperChat, BeeperMessage
from beeper_triage import inbox
from beeper_triage.openrouter_client import OpenRouterMessage
from beeper_triage.prompts import build_options_prompt


def test_openrouter_message_cache_payload():
    plain = OpenRouterMessage(role="user", content="hi").to_payload()
    assert plain == {"role": "user", "content": "hi"}
    cached = OpenRouterMessage(role="system", content="rules", cache=True).to_payload()
    block = cached["content"][0]
    assert block["text"] == "rules"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_transcript_marks_big_time_gaps():
    day = 24 * 60 * 60 * 1000
    base = 1_700_000_000_000  # a real-ish epoch (0 reads as "no timestamp")
    msgs = [
        _msg(False, "you free sat?", mid="1", ts=base),
        _msg(False, "yo you about?", mid="2", ts=base + 14 * day),  # 2 weeks later
    ]
    view = inbox.chat_view(FakeClient(messages={"c": msgs}), "c")
    t = view.transcript()
    assert "[2 weeks later]" in t
    assert t.index("you free sat") < t.index("[2 weeks later]") < t.index("yo you about")


def test_no_marker_for_small_gaps():
    base = 1_700_000_000_000
    msgs = [_msg(False, "a", mid="1", ts=base), _msg(False, "b", mid="2", ts=base + 60_000)]
    t = inbox.chat_view(FakeClient(messages={"c": msgs}), "c").transcript()
    assert "later]" not in t


def test_tentative_reply_type_available():
    from beeper_triage.prompts import REPLY_TYPES, build_options_prompt
    assert "tentative" in REPLY_TYPES
    sys_text = build_options_prompt("Me: hi", style="")[0].content
    assert "tentative" in sys_text  # offered to the model


def test_options_prompt_marks_system_cacheable():
    msgs = build_options_prompt("Me: hi", count=5, style="talks casual")
    assert msgs[0].role == "system" and msgs[0].cache is True
    assert msgs[1].role == "user" and msgs[1].cache is False  # transcript not cached


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


def _msg(is_sender, text="hi", *, mid="1", msg_type="TEXT", attachment=None,
         reactions=None, ts=0, is_deleted=False):
    return BeeperMessage(
        message_id=mid, sender_name="Them", is_sender=is_sender,
        text=text, timestamp_ms=ts, msg_type=msg_type,
        attachment=attachment, reactions=reactions or [], is_deleted=is_deleted,
    )


class FakeClient:
    def __init__(self, chats=None, messages=None):
        self._chats = chats or []
        self._messages = messages or {}
        self.sent = []
        self.archived = []
        self.reacted = []
        self.edited = []
        self.deleted = []

    def list_chats(self, use_cache=False):
        return list(self._chats)

    def list_messages(self, chat_id, limit=None, since_ms=None):
        return list(self._messages.get(chat_id, []))

    def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))

    def archive(self, chat_id, archived=True):
        self.archived.append((chat_id, archived))
        # reflect state so archive_reliable's verification can see it
        for c in self._chats:
            if c.chat_id == chat_id:
                c.is_archived = archived

    def add_reaction(self, chat_id, message_id, reaction_key):
        self.reacted.append((chat_id, message_id, reaction_key))

    def edit_message(self, chat_id, message_id, text):
        self.edited.append((chat_id, message_id, text))

    def delete_message(self, chat_id, message_id, for_everyone=False):
        self.deleted.append((chat_id, message_id, for_everyone))


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
    q = inbox.build_queue(FakeClient(chats), verify=False)
    assert [c.chat_id for c in q] == ["owe"]


def test_queue_carries_pinned_flag():
    # A pinned chat can't be archived in Beeper; the UI needs the flag to say so.
    chats = [_chat("pinned", is_group=True, is_pinned=True), _chat("plain")]
    q = inbox.build_queue(
        FakeClient(chats), inbox.QueueFilters(groups=True), verify=False
    )
    by_id = {c.chat_id: c for c in q}
    assert by_id["pinned"].is_pinned is True
    assert by_id["plain"].is_pinned is False
    assert by_id["pinned"].to_dict()["is_pinned"] is True  # reaches the browser


def test_queue_groups_and_muted_toggles():
    chats = [_chat("g", is_group=True), _chat("m", is_muted=True), _chat("plain")]
    q = inbox.build_queue(
        FakeClient(chats), inbox.QueueFilters(groups=True, include_muted=True), verify=False
    )
    assert {c.chat_id for c in q} == {"g", "m", "plain"}


def test_queue_network_filter_and_recency_order():
    chats = [
        _chat("old", last_activity_ms=1, network="whatsapp"),
        _chat("new", last_activity_ms=9, network="whatsapp"),
        _chat("tg", network="telegram"),
    ]
    q = inbox.build_queue(
        FakeClient(chats), inbox.QueueFilters(networks=["whatsapp"]), verify=False
    )
    assert [c.chat_id for c in q] == ["new", "old"]


def test_queue_verify_ignores_trailing_reaction():
    # #3: my message stands, they only reacted after -> I do NOT owe a reply.
    chats = [_chat("reacted-only"), _chat("they-spoke")]
    messages = {
        "reacted-only": [
            _msg(True, "my last message", ts=1),
            _msg(False, "None", msg_type="REACTION", ts=2),
        ],
        "they-spoke": [
            _msg(True, "hey", ts=1),
            _msg(False, "you there?", ts=2),
        ],
    }
    q = inbox.build_queue(FakeClient(chats, messages), verify=True)
    assert [c.chat_id for c in q] == ["they-spoke"]


def test_owes_reply_last_real_message():
    c = FakeClient(messages={
        "a": [_msg(False, "hi", ts=1), _msg(True, "reply", ts=2), _msg(False, "None", msg_type="REACTION", ts=3)],
    })
    assert inbox._owes_reply(c, "a") is False  # my reply stands under the reaction
    c2 = FakeClient(messages={"b": [_msg(True, "hi", ts=1), _msg(False, "yo", ts=2)]})
    assert inbox._owes_reply(c2, "b") is True


# ------------------------------ chat_view --------------------------------

def test_clean_text_strips_html_and_none():
    assert inbox.clean_text("<p>hi<br><br>there</p>") == "hi\n\nthere"
    assert inbox.clean_text("None") == ""
    assert inbox.clean_text(None) == ""
    assert inbox.clean_text("a &amp; b") == "a & b"


def test_clean_text_handles_bullet_lists_and_invisibles():
    # #7: <ul><li> bullets must not mash together; word-joiners stripped.
    raw = "Brands:<br><ul><li>Uniqlo - tees</li><li>⁠Zara - hit and miss</li></ul>"
    out = inbox.clean_text(raw)
    assert "• Uniqlo - tees" in out
    assert "• Zara - hit and miss" in out
    assert "⁠" not in out
    # the two bullets are on their own lines
    assert out.count("•") == 2


def test_chat_view_renders_voice_note_with_transcript():
    # #5b: voice notes are visible and (when a transcriber is given) transcribed.
    vn = _msg(True, "None", mid="v1", msg_type="VOICE",
              attachment={"is_voice_note": True, "duration": 34, "src_url": "u"})
    c = FakeClient(messages={"c": [vn]})
    # no transcriber -> placeholder only
    view = inbox.chat_view(c, "c")
    assert view.messages[0].kind == "voice"
    assert view.messages[0].text == "🎤 Voice note (0:34)"
    # with transcriber -> content included
    view2 = inbox.chat_view(c, "c", transcribe_fn=lambda url, mid: "yeah sounds good")
    assert 'yeah sounds good' in view2.messages[0].text


def test_chat_view_renders_image_with_caption():
    # Images are shown (media_src carried) and described (caption feeds display + prompt).
    img = _msg(False, "None", mid="i1", msg_type="IMAGE",
               attachment={"kind": "image", "mime": "image/jpeg", "src_url": "file://x.jpg"})
    c = FakeClient(messages={"c": [img]})
    # no caption fn -> src carried for display; no clutter text
    view = inbox.chat_view(c, "c")
    m = view.messages[0]
    assert m.kind == "image"
    assert m.media_src == "file://x.jpg"
    assert m.text == ""  # no sender caption -> nothing shown but the image
    assert m.caption == ""
    # with a caption fn -> description lives in .caption (hidden in UI), NOT .text,
    # but DOES reach the transcript/prompt so the model can read the image.
    view2 = inbox.chat_view(c, "c", caption_fn=lambda url, mid: "Party flyer: Sat 3pm, Flat 26")
    m2 = view2.messages[0]
    assert m2.caption == "Party flyer: Sat 3pm, Flat 26"
    assert m2.text == ""  # description not dumped into the display text
    assert "Flat 26" in view2.transcript()
    assert "[image:" in view2.transcript()


def test_chat_view_renders_deleted_message_as_context():
    # A deleted/unsent message is kept as context (with original text if the
    # network preserved it) instead of being dropped.
    msgs = [
        _msg(False, "wait ignore that", mid="d1", is_deleted=True),
        _msg(False, "so anyway you free sat?", mid="m2"),
    ]
    view = inbox.chat_view(FakeClient(messages={"c": msgs}), "c")
    assert view.messages[0].kind == "deleted"
    assert "Deleted" in view.messages[0].text and "wait ignore that" in view.messages[0].text
    assert "wait ignore that" in view.transcript()  # reaches the AI
    # a tombstone with no preserved text still registers
    v2 = inbox.chat_view(FakeClient(messages={"c": [_msg(False, "", mid="d2", is_deleted=True)]}), "c")
    assert v2.messages[0].kind == "deleted" and "deleted" in v2.messages[0].text.lower()


def test_chat_view_surfaces_reactions_and_editable():
    msgs = [
        _msg(False, "hey", mid="m1", reactions=["👍"]),
        _msg(True, "my reply", mid="m2"),
        _msg(True, "None", mid="r1", msg_type="REACTION"),  # skipped as a bubble
    ]
    view = inbox.chat_view(FakeClient(messages={"c": msgs}), "c")
    assert [m.message_id for m in view.messages] == ["m1", "m2"]
    assert view.messages[0].reactions == ["👍"]
    assert view.messages[1].editable is True  # my own text message
    assert view.messages[0].editable is False


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


def test_parse_drafts_tolerates_raw_newlines_in_text():
    # The model often emits multi-line reply text with a RAW newline inside the
    # JSON string, which strict json.loads rejects -> used to dump raw JSON as a
    # single draft. Must now parse into real drafts.
    reply = '```json\n[{"type":"going","text":"say less coming now\n\nalso saw the invite, im down"},{"type":"close","text":"bet, catch you later"}]\n```'
    drafts = inbox._parse_drafts(reply, count=5)
    assert len(drafts) == 2
    assert drafts[0].type == "going" and "\n\n" in drafts[0].text
    assert "```" not in drafts[0].text  # never leaks the fence


def test_parse_drafts_never_surfaces_raw_json():
    # If parsing truly fails, we must not show the user raw JSON as a "draft".
    broken = '```json [{"type":"going" "text": BROKEN'
    drafts = inbox._parse_drafts(broken, count=5)
    assert all("```json" not in d.text and not d.text.startswith("[") for d in drafts)


def test_draft_options_empty_transcript_no_call():
    orc = FakeORC("[]")
    assert inbox.draft_options(orc, "m", "   ") == []
    assert orc.calls == []


# ------------------------------- extract_event ---------------------------

def test_extract_event_parses_object():
    reply = ('{"found": true, "title": "Rob\'s Bday", "date": "2026-08-01", '
             '"start_time": "14:00", "end_time": "", "all_day": false, '
             '"location": "Regent\'s Park", "details": "RSVP by 22 July"}')
    ev = inbox.extract_event(FakeORC(reply), "m", "Leah: come to the party")
    assert ev.found and ev.title == "Rob's Bday"
    assert ev.date == "2026-08-01" and ev.start_time == "14:00"
    assert ev.location == "Regent's Park"


def test_extract_event_not_found():
    ev = inbox.extract_event(FakeORC('{"found": false}'), "m", "Leah: hey")
    assert ev.found is False


def test_extract_event_empty_transcript_no_call():
    orc = FakeORC('{"found": true}')
    assert inbox.extract_event(orc, "m", "  ").found is False
    assert orc.calls == []


# ------------------------------- resolve ---------------------------------

def test_send_sends_only_no_archive():
    # #9: send must NOT archive immediately (the send un-archives the chat).
    c = FakeClient()
    r = inbox.send(c, "chat1", "hello")
    assert c.sent == [("chat1", "hello")]
    assert c.archived == []  # archive happens separately, later
    assert r.sent_text == "hello" and not r.archived


def test_send_dry_run_touches_nothing():
    c = FakeClient()
    r = inbox.send(c, "chat1", "hello", dry_run=True)
    assert c.sent == [] and c.archived == []
    assert r.dry_run


def test_archive_reliable_retries_until_it_sticks():
    chat = _chat("c1", is_archived=False)
    c = FakeClient([chat])
    # Simulate the bridge bouncing the first archive: swallow the first call.
    calls = {"n": 0}
    real_archive = c.archive
    def flaky(cid, archived=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return  # first attempt doesn't stick
        real_archive(cid, archived)
    c.archive = flaky
    r = inbox.archive_reliable(c, "c1", attempts=4, delay=0, sleep=lambda _: None)
    assert r.archived and calls["n"] == 2


def test_archive_reliable_gives_up_and_reports():
    chat = _chat("c1", is_archived=False)
    c = FakeClient([chat])
    c.archive = lambda cid, archived=True: None  # never sticks
    r = inbox.archive_reliable(c, "c1", attempts=3, delay=0, sleep=lambda _: None)
    assert not r.archived


def test_react_edit_unsend():
    c = FakeClient()
    inbox.react(c, "c1", "m1", "👍")
    inbox.edit(c, "c1", "m1", "new text")
    inbox.unsend(c, "c1", "m1")
    assert c.reacted == [("c1", "m1", "👍")]
    assert c.edited == [("c1", "m1", "new text")]
    assert c.deleted == [("c1", "m1", True)]


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
