"""Tests for the shared connection-bootstrap helpers."""
from unittest.mock import MagicMock

import beeper_triage.cli as cli


def _fake_socket_ok():
    sock = MagicMock()
    sock.connect.return_value = None
    return sock


def _fake_socket_refused():
    sock = MagicMock()
    sock.connect.side_effect = ConnectionRefusedError()
    return sock


def test_resolve_base_url_uses_reachable_configured_url(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_ok())
    assert cli._resolve_base_url(agent=False) == "http://172.28.96.1:23399"


def test_resolve_base_url_falls_back_to_proxy_when_unreachable(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_refused())
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    assert cli._resolve_base_url(agent=True) == "http://127.0.0.1:23399"


def test_resolve_base_url_uses_proxy_when_env_missing(monkeypatch):
    monkeypatch.delenv("BEEPER_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    assert cli._resolve_base_url(agent=False) == "http://127.0.0.1:23399"


def test_build_client_passes_resolved_url(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_base_url", lambda *, agent: "http://h:1")
    captured = {}

    def fake_client(*, access_token, base_url):
        captured["access_token"] = access_token
        captured["base_url"] = base_url
        return MagicMock()

    monkeypatch.setattr(cli, "BeeperClient", fake_client)
    cli._build_client("tok123", agent=False)
    assert captured == {"access_token": "tok123", "base_url": "http://h:1"}


def test_resolve_base_url_warns_when_not_agent(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_refused())
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    calls = []
    monkeypatch.setattr(cli.typer, "echo", lambda msg: calls.append(msg))
    cli._resolve_base_url(agent=False)
    assert any("not reachable" in c for c in calls)


def test_resolve_base_url_silent_when_agent(monkeypatch):
    monkeypatch.setenv("BEEPER_BASE_URL", "http://172.28.96.1:23399")
    monkeypatch.setattr(cli.socket, "socket", lambda *a, **k: _fake_socket_refused())
    monkeypatch.setattr(cli, "_ensure_proxy", lambda: "http://127.0.0.1:23399")
    calls = []
    monkeypatch.setattr(cli.typer, "echo", lambda msg: calls.append(msg))
    cli._resolve_base_url(agent=True)
    assert calls == []
