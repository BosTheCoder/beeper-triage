"""Tests for verb commands and shared verb dispatch."""
from unittest.mock import MagicMock

from beeper_triage.output import resolve_json_flag


def test_resolve_json_flag_agent_forces_json():
    assert resolve_json_flag(True, None) is True
    assert resolve_json_flag(True, False) is True


def test_resolve_json_flag_non_agent_passes_through():
    assert resolve_json_flag(False, None) is None
    assert resolve_json_flag(False, True) is True
    assert resolve_json_flag(False, False) is False
