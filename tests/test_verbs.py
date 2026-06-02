"""Tests for verb commands and shared verb dispatch."""
import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from beeper_triage.beeper_client import BeeperSDKError
from beeper_triage.cli import app
from beeper_triage.output import resolve_json_flag

runner = CliRunner()


def test_resolve_json_flag_agent_forces_json():
    assert resolve_json_flag(True, None) is True
    assert resolve_json_flag(True, False) is True


def test_resolve_json_flag_non_agent_passes_through():
    assert resolve_json_flag(False, None) is None
    assert resolve_json_flag(False, True) is True
    assert resolve_json_flag(False, False) is False


def test_mark_read_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["mark-read", "!chat", "--json"])
    assert result.exit_code == 0
    fake.mark_read.assert_called_once_with("!chat")
    assert json.loads(result.stdout) == {"chatID": "!chat", "status": "read"}


def test_mark_unread_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["mark-unread", "!chat", "--json"])
    assert result.exit_code == 0
    fake.mark_unread.assert_called_once_with("!chat")
    assert json.loads(result.stdout) == {"chatID": "!chat", "status": "unread"}


def test_mark_read_command_error(monkeypatch):
    fake = MagicMock()
    fake.mark_read.side_effect = BeeperSDKError("nope")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["mark-read", "!chat", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)


def test_react_add_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["react", "!chat", "$msg", "👍", "--json"])
    assert result.exit_code == 0
    fake.add_reaction.assert_called_once_with("!chat", "$msg", "👍")
    assert json.loads(result.stdout)["action"] == "added"


def test_react_remove_command(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["react", "!chat", "$msg", "👍", "--remove", "--json"])
    assert result.exit_code == 0
    fake.remove_reaction.assert_called_once_with("!chat", "$msg", "👍")
    assert json.loads(result.stdout)["action"] == "removed"


def test_react_command_error(monkeypatch):
    fake = MagicMock()
    fake.add_reaction.side_effect = BeeperSDKError("nope")
    monkeypatch.setattr("beeper_triage.verbs.build_client_or_exit", lambda **k: fake)
    result = runner.invoke(app, ["react", "!chat", "$msg", "👍", "--json"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
