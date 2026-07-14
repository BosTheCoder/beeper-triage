"""Tests for the hidden `picker` command and triage filter flags."""

import json

from typer.testing import CliRunner

from beeper_triage.beeper_client import BeeperChat
from beeper_triage.cli import app

runner = CliRunner()


def _fake_client(monkeypatch):
    class FakeClient:
        def list_accounts(self, use_cache=True):
            return {
                "acc_wa": ("WhatsApp", "me"),
                "acc_tg": ("Telegram", "me"),
            }

        def list_chats(self, use_cache=True):
            return [
                BeeperChat(
                    chat_id="!wa1:beeper.local", title="Mum",
                    unread_count=2, preview_is_sender=False, is_muted=False,
                    account_id="acc_wa",
                ),
                BeeperChat(
                    chat_id="!wa2:beeper.local", title="Dad",
                    unread_count=0, preview_is_sender=True, is_muted=False,
                    account_id="acc_wa",
                ),
                BeeperChat(
                    chat_id="!tg1:beeper.local", title="Recruiter",
                    unread_count=1, preview_is_sender=False, is_muted=False,
                    account_id="acc_tg",
                ),
            ]

    fake = FakeClient()
    monkeypatch.setenv("BEEPER_ACCESS_TOKEN", "tok")
    monkeypatch.setattr("beeper_triage.cli._build_client", lambda *a, **k: fake)
    return fake


def _titles_from_picker(output):
    titles = []
    for line in output.strip().splitlines():
        if "\t" not in line:
            continue
        _, display = line.split("\t", 1)
        titles.append(display.split(" ", 1)[0])
    return titles


def test_picker_network_filter(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["picker", "--network", "whatsapp"])
    assert result.exit_code == 0
    assert _titles_from_picker(result.stdout) == ["Mum", "Dad"]


def test_picker_unread_filter(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["picker", "--unread"])
    assert result.exit_code == 0
    assert set(_titles_from_picker(result.stdout)) == {"Mum", "Recruiter"}


def test_picker_unreplied_filter(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["picker", "--unreplied"])
    assert result.exit_code == 0
    assert set(_titles_from_picker(result.stdout)) == {"Mum", "Recruiter"}


def test_picker_network_and_unread_combined(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["picker", "--network", "whatsapp", "--unread"])
    assert result.exit_code == 0
    assert _titles_from_picker(result.stdout) == ["Mum"]


def test_picker_rejects_unknown_network(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["picker", "--network", "nope"])
    assert result.exit_code != 0


def test_triage_agent_applies_network_filter(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["triage", "--agent", "--network", "telegram"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    titles = [c["title"] for c in payload["chats"]]
    assert titles == ["Recruiter"]


def test_triage_agent_unread_filter(monkeypatch):
    _fake_client(monkeypatch)
    result = runner.invoke(app, ["triage", "--agent", "--unread"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    titles = sorted(c["title"] for c in payload["chats"])
    assert titles == ["Mum", "Recruiter"]


def _fake_client_with_group(monkeypatch):
    class FakeClient:
        def list_accounts(self, use_cache=True):
            return {"acc_wa": ("WhatsApp", "me")}

        def list_chats(self, use_cache=True):
            return [
                BeeperChat(
                    chat_id="!p1:beeper.local", title="Mum",
                    unread_count=1, preview_is_sender=False, is_muted=False,
                    account_id="acc_wa", is_group=False,
                ),
                BeeperChat(
                    chat_id="!g1:beeper.local", title="Family Group",
                    unread_count=1, preview_is_sender=False, is_muted=False,
                    account_id="acc_wa", is_group=True,
                ),
            ]

    monkeypatch.setenv("BEEPER_ACCESS_TOKEN", "tok")
    monkeypatch.setattr("beeper_triage.cli._build_client", lambda *a, **k: FakeClient())


def test_picker_no_groups_excludes_group_chats(monkeypatch):
    _fake_client_with_group(monkeypatch)
    result = runner.invoke(app, ["picker", "--no-groups"])
    assert result.exit_code == 0
    assert _titles_from_picker(result.stdout) == ["Mum"]


def test_triage_agent_no_groups_and_is_group_field(monkeypatch):
    _fake_client_with_group(monkeypatch)
    # Default includes groups, and exposes is_group in the JSON.
    result = runner.invoke(app, ["triage", "--agent"])
    payload = json.loads(result.stdout)
    by_title = {c["title"]: c for c in payload["chats"]}
    assert by_title["Family Group"]["is_group"] is True
    assert by_title["Mum"]["is_group"] is False
    # --no-groups drops the group.
    result2 = runner.invoke(app, ["triage", "--agent", "--no-groups"])
    payload2 = json.loads(result2.stdout)
    assert [c["title"] for c in payload2["chats"]] == ["Mum"]
