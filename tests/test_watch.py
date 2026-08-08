"""Tests for `beeper watch` — config, state machine, output contract.

The bugs this feature exists to prevent are all state-machine bugs (see the
design spec §1), so most of this is table-driven over synthetic chat lists with
no network involved.
"""
import io
import json
import re

import pytest
from typer.testing import CliRunner

from beeper_triage.beeper_client import BeeperChat, BeeperSDKError
from beeper_triage.cli import app
from beeper_triage import watch as W

runner = CliRunner()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

CHAT_A = "!aaa:beeper.local"
CHAT_B = "!bbb:beeper.local"


def chat(
    chat_id=CHAT_A,
    title="AK Electrical",
    ts=1_000_000,
    is_sender=False,
    text="hello there",
):
    return BeeperChat(
        chat_id=chat_id,
        title=title,
        unread_count=0,
        preview_is_sender=is_sender,
        is_muted=False,
        last_activity_ms=ts,
        preview_text=text,
    )


def write_config(tmp_path, body):
    path = tmp_path / "w.toml"
    path.write_text(body, encoding="utf-8")
    return path


BASIC = """
name = "test-watch"
poll_seconds = 60
state = "{state}"

[[watch]]
chat  = "!aaa:beeper.local"
label = "ELEC AK Electrical"
"""


def basic_config(tmp_path, extra="", body=BASIC):
    state = tmp_path / "state.json"
    return W.load_config(
        write_config(tmp_path, body.format(state=state) + extra)
    )


class FakeClient:
    """Records the use_cache= it was called with; can be told to blow up."""

    def __init__(self, pages):
        self.pages = list(pages)  # each entry: list[BeeperChat] | Exception
        self.calls = []

    def list_chats(self, use_cache=True):
        self.calls.append(use_cache)
        page = self.pages.pop(0) if self.pages else []
        if isinstance(page, Exception):
            raise page
        return page


def run_events(config, client, **kw):
    out, err = io.StringIO(), io.StringIO()
    state = W.run(client, config, out=out, err=err, sleep=lambda _s: None, **kw)
    return out.getvalue().splitlines(), err.getvalue(), state


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_load_config_minimal(tmp_path):
    cfg = basic_config(tmp_path)
    assert cfg.name == "test-watch"
    assert cfg.poll_seconds == 60
    assert cfg.state_path == tmp_path / "state.json"
    assert cfg.inbound_only is True
    assert cfg.nag_after_seconds == 1800 and cfg.nag_count == 1
    assert [w.label for w in cfg.watches] == ["ELEC AK Electrical"]
    assert cfg.watches[0].chat_id == CHAT_A


def test_load_config_name_defaults_to_filename(tmp_path):
    path = tmp_path / "npm-13-edward.toml"
    path.write_text('[[watch]]\nchat = "!x"\n', encoding="utf-8")
    cfg = W.load_config(path)
    assert cfg.name == "npm-13-edward"
    # and the state path derives from the name
    assert cfg.state_path.name == "npm-13-edward.json"


def test_load_config_nag_and_filters(tmp_path):
    cfg = basic_config(
        tmp_path,
        body=BASIC.replace(
            '[[watch]]',
            "[nag]\nafter_seconds = 60\ncount = 3\n\n[filters]\ninbound_only = false\n\n[[watch]]",
        ),
    )
    assert (cfg.nag_after_seconds, cfg.nag_count) == (60, 3)
    assert cfg.inbound_only is False


def test_load_config_state_override(tmp_path):
    cfg = W.load_config(
        write_config(tmp_path, BASIC.format(state=tmp_path / "state.json")),
        state_override=tmp_path / "other.json",
    )
    assert cfg.state_path == tmp_path / "other.json"


def test_load_config_bad_regex(tmp_path):
    with pytest.raises(W.WatchConfigError, match="title_match"):
        basic_config(tmp_path, extra='\n[[watch]]\ntitle_match = "(unclosed"\n')


def test_load_config_watch_without_selector(tmp_path):
    with pytest.raises(W.WatchConfigError, match="chat.*title_match"):
        basic_config(tmp_path, extra='\n[[watch]]\nlabel = "orphan"\n')


def test_load_config_rejects_unknown_watch_key(tmp_path):
    # A typo'd key must fail loudly rather than silently disable the watch.
    with pytest.raises(W.WatchConfigError, match="title-match"):
        basic_config(tmp_path, extra='\n[[watch]]\n"title-match" = "x"\n')


def test_load_config_no_watches(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text('name = "empty"\n', encoding="utf-8")
    with pytest.raises(W.WatchConfigError, match="no \\[\\[watch\\]\\]"):
        W.load_config(path)


def test_resolve_config_path_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "CONFIG_DIR", tmp_path)
    (tmp_path / "npm.toml").write_text('[[watch]]\nchat = "!x"\n', encoding="utf-8")
    assert W.resolve_config_path("npm") == tmp_path / "npm.toml"


def test_resolve_config_path_missing_name(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "CONFIG_DIR", tmp_path)
    with pytest.raises(W.WatchConfigError, match="No watch config"):
        W.resolve_config_path("nope")


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def test_title_match_selects_chat(tmp_path):
    cfg = basic_config(
        tmp_path, extra='\n[[watch]]\ntitle_match = "(?i)damp detectives"\nlabel = "DAMP"\n'
    )
    got = W.match_watch(chat(chat_id=CHAT_B, title="Damp Detectives Ltd"), cfg.watches)
    assert got is not None and got.label == "DAMP"


def test_unwatched_chat_matches_nothing(tmp_path):
    cfg = basic_config(tmp_path)
    assert W.match_watch(chat(chat_id="!zzz", title="Mum"), cfg.watches) is None


def test_collapse_truncates_and_flattens():
    assert W.collapse("a\n\n  b  \tc") == "a b c"
    long = "x" * 400
    out = W.collapse(long)
    assert len(out) == W.PREVIEW_CHARS and out.endswith("...")


# --------------------------------------------------------------------------
# state machine (§9 table)
# --------------------------------------------------------------------------

def test_inbound_new_chat_emits_one_reply(tmp_path):
    cfg = basic_config(tmp_path)
    client = FakeClient([[chat(text="Your welcome. Please can you take a minute")]])
    lines, _, state = run_events(cfg, client, max_polls=1)
    assert lines == [
        "REPLY: ELEC AK Electrical | Your welcome. Please can you take a minute"
    ]
    assert state[CHAT_A].open is True


def test_same_poll_repeated_is_silent(tmp_path):
    cfg = basic_config(tmp_path)
    c = chat()
    client = FakeClient([[c], [c], [c]])
    lines, _, _ = run_events(cfg, client, max_polls=3)
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]


def test_outbound_last_message_is_silent_and_clears_open(tmp_path):
    cfg = basic_config(tmp_path)
    client = FakeClient([[chat(is_sender=True)]])
    lines, _, state = run_events(cfg, client, max_polls=1)
    assert lines == []
    assert state[CHAT_A].open is False


def test_our_reply_clears_open_without_emitting(tmp_path):
    cfg = basic_config(tmp_path)
    client = FakeClient([
        [chat(ts=1000)],                        # they message
        [chat(ts=2000, is_sender=True)],        # we reply
    ])
    lines, _, state = run_events(cfg, client, max_polls=2)
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]
    assert state[CHAT_A].open is False and state[CHAT_A].last == 2000


def test_nag_fires_exactly_once_then_never_again(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.nag_after_seconds = 100
    c = chat()
    client = FakeClient([[c]] * 5)
    clock = iter([0, 50, 200, 400, 9999])
    lines, _, state = run_events(cfg, client, max_polls=5, now_fn=lambda: next(clock))
    assert lines == [
        "REPLY: ELEC AK Electrical | hello there",
        "STILL UNANSWERED (3m, final reminder): ELEC AK Electrical",
    ]
    assert state[CHAT_A].nags == 1


def test_nag_count_zero_never_reraises(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.nag_after_seconds, cfg.nag_count = 1, 0
    c = chat()
    client = FakeClient([[c]] * 3)
    clock = iter([0, 500, 1000])
    lines, _, _ = run_events(cfg, client, max_polls=3, now_fn=lambda: next(clock))
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]


def test_nag_after_zero_disables_reraise(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.nag_after_seconds = 0
    client = FakeClient([[chat()]] * 2)
    clock = iter([0, 5000])
    lines, _, _ = run_events(cfg, client, max_polls=2, now_fn=lambda: next(clock))
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]


def test_nag_reports_multiple_reminders_when_configured(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.nag_after_seconds, cfg.nag_count = 100, 2
    client = FakeClient([[chat()]] * 3)
    clock = iter([0, 200, 400])
    lines, _, _ = run_events(cfg, client, max_polls=3, now_fn=lambda: next(clock))
    assert lines[1] == "STILL UNANSWERED (3m): ELEC AK Electrical"
    assert lines[2] == "STILL UNANSWERED (6m, final reminder): ELEC AK Electrical"


def test_text_match_filters_out_uninteresting_inbound(tmp_path):
    cfg = basic_config(
        tmp_path,
        extra=(
            '\n[[watch]]\ntitle_match = "(?i)cadent"\nlabel = "GAS network"\n'
            'text_match = "(?i)\\\\b(appointment|engineer)\\\\b"\n'
        ),
    )
    hit = chat(chat_id=CHAT_B, title="Cadent Gas", text="Your engineer is booked")
    miss = chat(chat_id=CHAT_B, title="Cadent Gas", ts=2000, text="Happy new year")
    lines, _, state = run_events(cfg, FakeClient([[miss]]), max_polls=1)
    assert lines == []
    assert state[CHAT_B].open is False
    lines, _, state = run_events(cfg, FakeClient([[hit]]), max_polls=1)
    assert lines == ["REPLY: GAS network | Your engineer is booked"]


def test_priority_surfaces_in_the_line(tmp_path):
    cfg = basic_config(
        tmp_path,
        extra='\n[[watch]]\nchat = "!bbb:beeper.local"\nlabel = "TENANT"\npriority = "high"\n',
    )
    lines, _, _ = run_events(
        cfg, FakeClient([[chat(chat_id=CHAT_B, text="boiler is out")]]), max_polls=1
    )
    assert lines == ["REPLY: [high] TENANT | boiler is out"]


def test_inbound_only_false_reports_outbound_as_activity(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.inbound_only = False
    lines, _, state = run_events(
        cfg, FakeClient([[chat(is_sender=True, text="on my way")]]), max_polls=1
    )
    assert lines == ["ACTIVITY: ELEC AK Electrical | on my way"]
    # our own message never puts the chat in the "they are waiting" state
    assert state[CHAT_A].open is False


# --------------------------------------------------------------------------
# resilience
# --------------------------------------------------------------------------

def test_corrupt_state_file_starts_fresh(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.state_path.write_text("{not json at all", encoding="utf-8")
    lines, _, _ = run_events(cfg, FakeClient([[chat()]]), max_polls=1)
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]


def test_missing_state_file_starts_fresh(tmp_path):
    cfg = basic_config(tmp_path)
    assert W.load_state(cfg.state_path) == {}


def test_state_round_trips(tmp_path):
    cfg = basic_config(tmp_path)
    run_events(cfg, FakeClient([[chat()]]), max_polls=1)
    reloaded = W.load_state(cfg.state_path)
    assert reloaded[CHAT_A].last == 1_000_000
    assert reloaded[CHAT_A].open is True
    assert reloaded[CHAT_A].label == "ELEC AK Electrical"


def test_api_error_does_not_kill_the_loop(tmp_path):
    cfg = basic_config(tmp_path)
    client = FakeClient([BeeperSDKError("boom"), [chat()]])
    lines, err, state = run_events(cfg, client, max_polls=2)
    assert lines == ["REPLY: ELEC AK Electrical | hello there"]
    assert "boom" in err
    assert state[CHAT_A].last == 1_000_000


def test_failed_poll_leaves_state_untouched(tmp_path):
    cfg = basic_config(tmp_path)
    run_events(cfg, FakeClient([[chat()]]), max_polls=1)
    before = cfg.state_path.read_text(encoding="utf-8")
    run_events(cfg, FakeClient([BeeperSDKError("boom")]), max_polls=1)
    assert cfg.state_path.read_text(encoding="utf-8") == before


def test_poll_always_bypasses_the_chat_cache(tmp_path):
    # §3.4: a watcher that polls its own 6-hour cache goes permanently, and
    # invisibly, quiet.
    cfg = basic_config(tmp_path)
    client = FakeClient([[chat()], [chat()], [chat()]])
    run_events(cfg, client, max_polls=3)
    assert client.calls == [False, False, False]


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------

def test_dry_run_seeds_state_and_emits_nothing(tmp_path):
    cfg = basic_config(tmp_path)
    lines, _, state = run_events(cfg, FakeClient([[chat()]]), max_polls=1, seed=True)
    assert lines == []
    assert state[CHAT_A].last == 1_000_000
    assert state[CHAT_A].open is False


def test_seeded_state_suppresses_the_first_real_poll(tmp_path):
    cfg = basic_config(tmp_path)
    run_events(cfg, FakeClient([[chat()]]), max_polls=1, seed=True)
    lines, _, _ = run_events(cfg, FakeClient([[chat()]]), max_polls=1)
    assert lines == []


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------

def test_json_mode_emits_one_object_per_line(tmp_path):
    cfg = basic_config(tmp_path)
    cfg.nag_after_seconds = 100
    client = FakeClient([[chat()], [chat()]])
    clock = iter([0, 500])
    lines, _, _ = run_events(
        cfg, client, max_polls=2, json_mode=True, now_fn=lambda: next(clock)
    )
    first = json.loads(lines[0])
    assert first == {
        "event": "reply",
        "chat": CHAT_A,
        "label": "ELEC AK Electrical",
        "text": "hello there",
        "ts": 1_000_000,
        "priority": None,
    }
    second = json.loads(lines[1])
    assert second["event"] == "unanswered" and second["final"] is True


def test_diagnostics_never_reach_stdout(tmp_path):
    cfg = basic_config(tmp_path)
    lines, err, _ = run_events(
        cfg, FakeClient([BeeperSDKError("boom")]), max_polls=1
    )
    assert lines == []
    assert "boom" in err


def test_text_match_with_no_preview_text_warns(tmp_path):
    cfg = basic_config(
        tmp_path,
        extra=(
            '\n[[watch]]\nchat = "!bbb:beeper.local"\nlabel = "GAS"\n'
            'text_match = "engineer"\n'
        ),
    )
    lines, err, _ = run_events(
        cfg, FakeClient([[chat(chat_id=CHAT_B, text=None)]]), max_polls=1
    )
    assert lines == []
    assert "preview text" in err


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_watch_check_reports_resolved_chats(tmp_path, monkeypatch):
    cfg_path = write_config(
        tmp_path,
        BASIC.format(state=tmp_path / "s.json")
        + '\n[[watch]]\ntitle_match = "(?i)damp"\nlabel = "DAMP"\n',
    )
    fake = FakeClient([[chat(), chat(chat_id=CHAT_B, title="Damp Detectives")]])
    monkeypatch.setattr("beeper_triage.watch.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["watch", "check", str(cfg_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["unresolved"] == []
    labels = {w["label"]: w for w in payload["watches"]}
    assert labels["DAMP"]["chats"][0]["chatID"] == CHAT_B


def test_watch_check_fails_on_a_pattern_that_matches_nothing(tmp_path, monkeypatch):
    cfg_path = write_config(
        tmp_path,
        BASIC.format(state=tmp_path / "s.json")
        + '\n[[watch]]\ntitle_match = "(?i)typo"\nlabel = "NOPE"\n',
    )
    monkeypatch.setattr(
        "beeper_triage.watch.build_client_or_exit", lambda **k: FakeClient([[chat()]])
    )
    result = runner.invoke(app, ["watch", "check", str(cfg_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["unresolved"] == ["NOPE"]


def test_watch_list_shows_state(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path, BASIC.format(state=tmp_path / "s.json"))
    monkeypatch.setattr(
        "beeper_triage.watch.build_client_or_exit", lambda **k: FakeClient([[chat()]])
    )
    result = runner.invoke(app, ["watch", "list", "--config", str(cfg_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "test-watch"
    assert payload["watches"][0]["chats"][0]["lastActivity"] == 1_000_000


def test_watch_once_runs_a_single_poll(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path, BASIC.format(state=tmp_path / "s.json"))
    fake = FakeClient([[chat()]])
    monkeypatch.setattr("beeper_triage.watch.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["watch", "--config", str(cfg_path), "--once"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "REPLY: ELEC AK Electrical | hello there"
    assert fake.calls == [False]


def test_watch_dry_run_emits_nothing(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path, BASIC.format(state=tmp_path / "s.json"))
    monkeypatch.setattr(
        "beeper_triage.watch.build_client_or_exit", lambda **k: FakeClient([[chat()]])
    )
    result = runner.invoke(app, ["watch", "--config", str(cfg_path), "--dry-run"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    assert W.load_state(tmp_path / "s.json")[CHAT_A].last == 1_000_000


def test_watch_bad_config_exits_two(tmp_path):
    cfg_path = write_config(tmp_path, 'name = "x"\n[[watch]]\ntitle_match = "(oops"\n')
    result = runner.invoke(app, ["watch", "--config", str(cfg_path), "--once"])
    assert result.exit_code == 2


def test_watch_requires_a_config():
    result = runner.invoke(app, ["watch", "--once"])
    assert result.exit_code == 2


# --------------------------------------------------------------------------
# adapter change (§3.3)
# --------------------------------------------------------------------------

def test_preview_text_defaults_to_none():
    assert BeeperChat(
        chat_id="!x", title="t", unread_count=0, preview_is_sender=False, is_muted=False
    ).preview_text is None


def test_list_chats_populates_preview_text(monkeypatch):
    from beeper_triage.beeper_client import BeeperClient

    class Preview:
        is_sender = False
        text = "the engineer is booked"

    class Chat:
        chat_id = "!x"
        title = "Cadent"
        preview = Preview()
        unread_count = 0

    client = BeeperClient.__new__(BeeperClient)
    client._client = type("C", (), {"chats": type("X", (), {"list": staticmethod(lambda: [Chat()])})()})()
    monkeypatch.setattr(BeeperClient, "_save_cache", lambda self, chats: None)
    (out,) = client.list_chats(use_cache=False)
    assert out.preview_text == "the engineer is booked"
