"""Tests for BeeperClient adapter methods (SDK mocked)."""
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
