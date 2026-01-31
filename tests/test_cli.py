"""Tests for cli helper functions."""

from unittest.mock import MagicMock, patch

from beeper_triage.beeper_client import BeeperMessage
from beeper_triage.cli import (
    _copy_to_clipboard,
    _detect_clipboard_cmd,
    _format_transcript_with_timestamps,
    _pick_action,
)


def test_format_transcript_with_timestamps():
    messages = [
        BeeperMessage(
            message_id="1",
            sender_name="Alice",
            is_sender=False,
            text="Hey, are you free?",
            timestamp_ms=1706623920000,  # 2024-01-30 14:32 UTC
        ),
        BeeperMessage(
            message_id="2",
            sender_name="Me",
            is_sender=True,
            text="Yeah, what's up?",
            timestamp_ms=1706623980000,  # 2024-01-30 14:33 UTC
        ),
    ]
    result = _format_transcript_with_timestamps(messages)
    lines = result.strip().split("\n")
    assert len(lines) == 2
    # Check format: [YYYY-MM-DD HH:MM] Speaker: text
    assert "] Alice: Hey, are you free?" in lines[0]
    assert "] You: Yeah, what's up?" in lines[1]
    # Check timestamp bracket format exists
    assert lines[0].startswith("[")
    assert "] " in lines[0]


def test_format_transcript_with_timestamps_skips_empty():
    messages = [
        BeeperMessage(
            message_id="1",
            sender_name="Alice",
            is_sender=False,
            text="",
            timestamp_ms=1706623920000,
        ),
        BeeperMessage(
            message_id="2",
            sender_name="Alice",
            is_sender=False,
            text="Hello",
            timestamp_ms=1706623980000,
        ),
    ]
    result = _format_transcript_with_timestamps(messages)
    lines = result.strip().split("\n")
    assert len(lines) == 1
    assert "Hello" in lines[0]


def test_detect_clipboard_cmd_clip_exe():
    """clip.exe should be preferred (WSL)."""
    with patch("shutil.which", side_effect=lambda cmd: "/mnt/c/clip.exe" if cmd == "clip.exe" else None):
        assert _detect_clipboard_cmd() == ["clip.exe"]


def test_detect_clipboard_cmd_wl_copy():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/wl-copy" if cmd == "wl-copy" else None):
        assert _detect_clipboard_cmd() == ["wl-copy"]


def test_detect_clipboard_cmd_xclip():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None):
        assert _detect_clipboard_cmd() == ["xclip", "-selection", "clipboard"]


def test_detect_clipboard_cmd_xsel():
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/xsel" if cmd == "xsel" else None):
        assert _detect_clipboard_cmd() == ["xsel", "--clipboard", "--input"]


def test_detect_clipboard_cmd_none():
    with patch("shutil.which", return_value=None):
        assert _detect_clipboard_cmd() is None


def test_copy_to_clipboard_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _copy_to_clipboard("hello", ["clip.exe"])
        mock_run.assert_called_once_with(["clip.exe"], input="hello", text=True, check=True)


def test_pick_action_reply():
    with patch("builtins.input", return_value="1"):
        assert _pick_action() == "reply"


def test_pick_action_copy():
    with patch("builtins.input", return_value="2"):
        assert _pick_action() == "copy"


def test_pick_action_default_is_reply():
    with patch("builtins.input", return_value=""):
        assert _pick_action() == "reply"


def test_pick_action_invalid_then_valid():
    with patch("builtins.input", side_effect=["3", "2"]):
        assert _pick_action() == "copy"


def test_pick_action_ctrl_c():
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert _pick_action() is None
