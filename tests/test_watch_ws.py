"""Tests for the push transport — frame parsing, reconnect, and the shared
state machine reached via `apply_message`.

No socket is opened: `WatchSocket` takes a `connect_fn` seam, so the whole
transport is driven off scripted frames.
"""
import json

import pytest

from beeper_triage import watch as W
from beeper_triage import watch_ws as WS

CHAT_A = "!aaa:beeper.local"
CHAT_B = "!bbb:beeper.local"

# 2026-08-08T05:25:08.666Z, the timestamp in the sample frames below.
TS_MS = 1786166708666


def entry(mid="1", chat=CHAT_A, text="boiler is out", is_sender=False,
          ts="2026-08-08T05:25:08.666Z", sender="@them:beeper.com"):
    return {
        "id": mid, "chatID": chat, "accountID": "whatsapp",
        "senderID": sender, "senderName": "Them", "timestamp": ts,
        "type": "TEXT", "text": text, "isSender": is_sender, "isDeleted": False,
    }


def frame(*entries, chat=CHAT_A, kind="message.upserted"):
    return json.dumps({"type": kind, "chatID": chat, "entries": list(entries), "seq": 1})


def config(tmp_path, body=None):
    return W.parse_config(
        body or {"watch": [{"chat": CHAT_A, "label": "ELEC"}]}, state_dir=tmp_path
    )


# --------------------------------------------------------------------------
# url + parsing
# --------------------------------------------------------------------------

def test_ws_url_swaps_the_scheme():
    # /v1/info reports the socket as an http:// URL even though it is a socket.
    assert WS.ws_url("http://127.0.0.1:23373") == "ws://127.0.0.1:23373/v1/ws"
    assert WS.ws_url("https://host:443/anything") == "wss://host:443/v1/ws"
    assert WS.ws_url("http://172.21.240.1:23399") == "ws://172.21.240.1:23399/v1/ws"


def test_parse_ts_handles_iso_and_numbers():
    assert W.parse_ts("2026-08-08T05:25:08.666Z") == TS_MS
    assert W.parse_ts(TS_MS) == TS_MS
    assert W.parse_ts(None) == 0
    assert W.parse_ts("not a date") == 0


def test_message_from_ws_entry():
    m = W.WatchMessage.from_ws_entry(entry(mid="347992"))
    assert m.chat_id == CHAT_A
    assert m.message_id == "347992"
    assert m.text == "boiler is out"
    assert m.is_sender is False
    assert m.sender_id == "@them:beeper.com"
    assert m.ts == TS_MS


def test_only_message_upserted_carries_content():
    assert len(WS._messages_from_frame(json.loads(frame(entry())))) == 1
    for kind in ("chat.upserted", "chat.deleted", "message.deleted", "ready"):
        assert WS._messages_from_frame(json.loads(frame(entry(), kind=kind))) == []


def test_malformed_frames_are_skipped_not_fatal():
    assert WS._messages_from_frame({"type": "message.upserted"}) == []
    assert WS._messages_from_frame({"type": "message.upserted", "entries": "nope"}) == []
    assert WS._messages_from_frame(
        {"type": "message.upserted", "chatID": CHAT_A, "entries": ["nope", None]}
    ) == []
    # an entry with no usable timestamp is dropped rather than treated as epoch 0
    assert WS._messages_from_frame(
        {"type": "message.upserted", "chatID": CHAT_A, "entries": [{"id": "1"}]}
    ) == []


# --------------------------------------------------------------------------
# the socket loop
# --------------------------------------------------------------------------

class FakeSocket:
    """Scripted frames; an Exception in the script is raised from recv()."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self, timeout=None):
        if not self.script:
            raise ConnectionError("closed")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def socket_over(*scripts, stop_after=None, **kw):
    """A WatchSocket that hands out `scripts` in order, one per connect."""
    made = []

    def connect_fn(url, **_kw):
        made.append(url)
        return FakeSocket(scripts[len(made) - 1] if len(made) <= len(scripts) else [])

    sock = WS.WatchSocket(
        "http://127.0.0.1:23373", "tok",
        connect_fn=connect_fn, sleep=lambda _s: None, **kw
    )
    sock._made = made
    return sock


def drain(sock, limit=10):
    out = []
    for message in sock.listen():
        out.append(message)
        if len(out) >= limit:
            sock.stop()
            break
    return out


def test_subscribes_to_everything_on_connect():
    sock = socket_over([frame(entry())])
    got = drain(sock, limit=1)
    assert len(got) == 1 and got[0].chat_id == CHAT_A


def test_subscribe_command_shape():
    sent = {}

    def connect_fn(url, **_kw):
        s = FakeSocket([frame(entry())])
        sent["sock"] = s
        return s

    sock = WS.WatchSocket("http://h:1", "tok", connect_fn=connect_fn, sleep=lambda _s: None)
    drain(sock, limit=1)
    assert sent["sock"].sent == [
        {"type": "subscriptions.set", "requestID": "watch", "chatIDs": ["*"]}
    ]


def test_reconnects_after_a_dropped_socket():
    errors = []
    sock = socket_over(
        [frame(entry(mid="1"))],                       # first connection
        [frame(entry(mid="2", ts="2026-08-08T05:26:08.666Z"))],  # after reconnect
        on_error=errors.append,
    )
    got = drain(sock, limit=2)
    assert [m.message_id for m in got] == ["1", "2"]
    assert len(sock._made) == 2
    assert errors and "event socket" in errors[0]


def test_a_bad_frame_does_not_kill_the_connection():
    errors = []
    sock = socket_over(["{not json", frame(entry())], on_error=errors.append)
    got = drain(sock, limit=1)
    assert len(got) == 1
    assert any("unparseable" in e for e in errors)


def test_stop_ends_the_generator():
    sock = socket_over([frame(entry()), frame(entry(mid="2"))])
    stream = sock.listen()
    next(stream)
    sock.stop()
    assert list(stream) == []


def test_idle_socket_keeps_waiting():
    sock = socket_over([TimeoutError(), TimeoutError(), frame(entry())])
    assert len(drain(sock, limit=1)) == 1
    assert len(sock._made) == 1  # a timeout is idleness, not a disconnect


# --------------------------------------------------------------------------
# apply_message — the shared state machine, reached from the push path
# --------------------------------------------------------------------------

def message(**kw):
    return W.WatchMessage.from_ws_entry(entry(**kw))


def test_inbound_message_emits_a_reply(tmp_path):
    cfg, state = config(tmp_path), {}
    event = W.apply_message(message(), cfg, state, now=1000)
    assert event is not None
    assert event.kind == "reply" and event.label == "ELEC"
    assert event.text == "boiler is out"
    assert state[CHAT_A].open is True


def test_unwatched_chat_is_ignored(tmp_path):
    cfg, state = config(tmp_path), {}
    assert W.apply_message(message(chat=CHAT_B), cfg, state, now=1000) is None
    assert state == {}


def test_redelivery_of_the_same_message_is_dropped(tmp_path):
    # The socket re-sends a message as its sendStatus advances; observed three
    # times for one send, all with the same id and timestamp.
    cfg, state = config(tmp_path), {}
    assert W.apply_message(message(mid="7"), cfg, state, now=1000) is not None
    assert W.apply_message(message(mid="7"), cfg, state, now=1001) is None
    assert W.apply_message(message(mid="7"), cfg, state, now=1002) is None


def test_two_messages_in_the_same_millisecond_both_report(tmp_path):
    # This is what the message id buys over the poll's `ts <= last` guard.
    cfg, state = config(tmp_path), {}
    assert W.apply_message(message(mid="1"), cfg, state, now=1000) is not None
    assert W.apply_message(message(mid="2", text="and another"),
                           cfg, state, now=1000) is not None


def test_an_older_message_is_ignored(tmp_path):
    cfg, state = config(tmp_path), {}
    W.apply_message(message(mid="2", ts="2026-08-08T05:26:00Z"), cfg, state, now=1000)
    assert W.apply_message(
        message(mid="1", ts="2026-08-08T05:20:00Z"), cfg, state, now=1001
    ) is None


def test_our_own_message_clears_open_without_emitting(tmp_path):
    cfg, state = config(tmp_path), {}
    W.apply_message(message(mid="1"), cfg, state, now=1000)
    assert state[CHAT_A].open is True
    assert W.apply_message(
        message(mid="2", is_sender=True, ts="2026-08-08T05:26:00Z"),
        cfg, state, now=1001,
    ) is None
    assert state[CHAT_A].open is False


def test_title_match_needs_the_title_map(tmp_path):
    cfg = config(tmp_path, {"watch": [{"title_match": "(?i)damp", "label": "DAMP"}]})
    state, warnings = {}, []
    # without a title, a title_match watch cannot match — and says so
    assert W.apply_message(message(chat=CHAT_B), cfg, state, now=1000,
                           warn=warnings.append) is None
    assert warnings and "no title known" in warnings[0]
    # with one, it matches
    event = W.apply_message(message(chat=CHAT_B), cfg, state, now=1000,
                            titles={CHAT_B: "Damp Detectives Ltd"})
    assert event is not None and event.label == "DAMP"


def test_chat_id_watches_need_no_title(tmp_path):
    cfg, state, warnings = config(tmp_path), {}, []
    event = W.apply_message(message(), cfg, state, now=1000, warn=warnings.append)
    assert event is not None
    assert warnings == []  # no title_match in the config, so nothing to warn about


def test_text_match_applies_on_the_push_path(tmp_path):
    cfg = config(tmp_path, {"watch": [
        {"chat": CHAT_A, "label": "GAS", "text_match": "(?i)engineer"}
    ]})
    state = {}
    assert W.apply_message(message(mid="1", text="happy new year"),
                           cfg, state, now=1000) is None
    assert W.apply_message(message(mid="2", text="the engineer is booked",
                                   ts="2026-08-08T05:26:00Z"),
                           cfg, state, now=1000) is not None


def test_inbound_only_false_reports_our_own_as_activity(tmp_path):
    cfg = W.parse_config(
        {"watch": [{"chat": CHAT_A, "label": "ELEC"}], "filters": {"inbound_only": False}},
        state_dir=tmp_path,
    )
    event = W.apply_message(message(is_sender=True, text="on my way"), cfg, {}, now=1000)
    assert event is not None and event.kind == "activity"


# --------------------------------------------------------------------------
# the two transports must agree
# --------------------------------------------------------------------------

def _chat(ts_ms, is_sender=False, text="boiler is out"):
    from beeper_triage.beeper_client import BeeperChat
    return BeeperChat(
        chat_id=CHAT_A, title="AK Electrical", unread_count=0,
        preview_is_sender=is_sender, is_muted=False,
        last_activity_ms=ts_ms, preview_text=text,
    )


def test_push_and_poll_produce_the_same_event(tmp_path):
    cfg = config(tmp_path)
    push_state, poll_state = {}, {}
    pushed = W.apply_message(message(), cfg, push_state, now=1000)
    (polled,) = W.scan([_chat(TS_MS)], cfg, poll_state, now=1000)
    assert pushed.kind == polled.kind
    assert pushed.label == polled.label
    assert pushed.text == polled.text
    assert pushed.ts == polled.ts
    assert push_state[CHAT_A].open == poll_state[CHAT_A].open


def test_a_reconcile_after_the_socket_saw_it_does_not_double_report(tmp_path):
    # Both transports write the same state, so the poll that follows a pushed
    # event must stay silent rather than announce it a second time.
    cfg, state = config(tmp_path), {}
    assert W.apply_message(message(), cfg, state, now=1000) is not None
    assert W.scan([_chat(TS_MS)], cfg, state, now=1001) == []


def test_the_reconcile_still_catches_what_the_socket_missed(tmp_path):
    cfg, state = config(tmp_path), {}
    W.apply_message(message(mid="1"), cfg, state, now=1000)
    # a later message the socket never delivered (dropped connection)
    (caught,) = W.scan([_chat(TS_MS + 100_000, text="still waiting")], cfg, state, now=1002)
    assert caught.text == "still waiting"


def test_state_round_trips_the_push_dedupe_key(tmp_path):
    cfg, state = config(tmp_path), {}
    W.apply_message(message(mid="347992"), cfg, state, now=1000)
    W.save_state(cfg.state_path, state)
    reloaded = W.load_state(cfg.state_path)
    assert reloaded[CHAT_A].last_msg_id == "347992"
    # and a restart does not re-report the message it already saw
    assert W.apply_message(message(mid="347992"), cfg, reloaded, now=1001) is None
