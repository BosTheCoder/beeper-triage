"""Tests for cli helper functions."""

from beeper_triage.beeper_client import BeeperMessage
from beeper_triage.cli import _format_transcript_with_timestamps


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
