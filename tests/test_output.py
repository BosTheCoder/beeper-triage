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
