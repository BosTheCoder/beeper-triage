"""Tests for network normalization, colouring, and chat filtering."""

import pytest
import typer

from beeper_triage.beeper_client import BeeperChat
from beeper_triage.cli import (
    _deflag,
    _filter_chats,
    _format_chat_display,
    _network_color,
    _network_slug,
    _normalize_network_filter,
    _render_fzf_lines,
)


def _chat(
    chat_id="!c:beeper.local",
    title="Someone",
    network_type="WhatsApp",
    unread_count=0,
    preview_is_sender=False,
    is_muted=False,
    is_group=False,
):
    return BeeperChat(
        chat_id=chat_id,
        title=title,
        unread_count=unread_count,
        preview_is_sender=preview_is_sender,
        is_muted=is_muted,
        network_type=network_type,
        is_group=is_group,
    )


# --- network normalization -------------------------------------------------

def test_network_slug_folds_display_names():
    assert _network_slug("WhatsApp") == "whatsapp"
    assert _network_slug("Telegram") == "telegram"
    assert _network_slug("Google Messages") == "gmessages"
    assert _network_slug("X") == "x"


def test_network_slug_folds_aliases():
    assert _network_slug("wa") == "whatsapp"
    assert _network_slug("tg") == "telegram"
    assert _network_slug("ig") == "instagram"
    assert _network_slug("instagramgo") == "instagram"
    assert _network_slug("twitter") == "x"
    assert _network_slug("matrix") == "beeper"


def test_network_slug_unknown_falls_back_to_cleaned_string():
    assert _network_slug("Weirdnet") == "weirdnet"
    assert _network_slug(None) == ""


def test_normalize_network_filter_accepts_known():
    assert _normalize_network_filter("wa") == "whatsapp"
    assert _normalize_network_filter("WhatsApp") == "whatsapp"


def test_normalize_network_filter_rejects_unknown():
    with pytest.raises(typer.BadParameter):
        _normalize_network_filter("nope")


# --- colours ---------------------------------------------------------------

def test_network_color_known():
    assert _network_color("WhatsApp") == typer.colors.GREEN
    assert _network_color("Telegram") == typer.colors.CYAN
    assert _network_color("Instagram") == typer.colors.MAGENTA


def test_network_color_unknown_is_default():
    assert _network_color("Weirdnet") == typer.colors.WHITE
    assert _network_color(None) == typer.colors.WHITE


# --- display formatting ----------------------------------------------------

def test_format_chat_display_colours_tag_only():
    chat = _chat(title="Mum", network_type="WhatsApp", unread_count=2)
    result = _format_chat_display(chat)
    # Title stays plain at the start of the line.
    assert result.startswith("Mum ")
    # The network tag is wrapped in the network colour.
    styled_tag = typer.style("[WhatsApp]", fg=typer.colors.GREEN)
    assert styled_tag in result
    # Unread count still shown.
    assert "(2 new)" in result


def test_format_chat_display_no_network_has_no_tag():
    chat = _chat(title="Mystery", network_type=None)
    result = _format_chat_display(chat)
    assert result.startswith("Mystery")
    assert "[" not in result


# --- filtering -------------------------------------------------------------

def test_filter_chats_excludes_muted_by_default():
    chats = [_chat(title="A", is_muted=True), _chat(title="B", is_muted=False)]
    out = _filter_chats(chats, include_muted=False, networks=set(), unread=False, unreplied=False)
    assert [c.title for c in out] == ["B"]


def test_filter_chats_include_muted():
    chats = [_chat(title="A", is_muted=True), _chat(title="B", is_muted=False)]
    out = _filter_chats(chats, include_muted=True, networks=set(), unread=False, unreplied=False)
    assert [c.title for c in out] == ["A", "B"]


def test_filter_chats_by_network():
    chats = [
        _chat(title="A", network_type="WhatsApp"),
        _chat(title="B", network_type="Telegram"),
        _chat(title="C", network_type="WhatsApp"),
    ]
    out = _filter_chats(chats, include_muted=False, networks={"whatsapp"}, unread=False, unreplied=False)
    assert [c.title for c in out] == ["A", "C"]


def test_filter_chats_multiple_networks():
    chats = [
        _chat(title="A", network_type="WhatsApp"),
        _chat(title="B", network_type="Telegram"),
        _chat(title="C", network_type="Instagram"),
    ]
    out = _filter_chats(
        chats, include_muted=False, networks={"whatsapp", "telegram"}, unread=False, unreplied=False
    )
    assert [c.title for c in out] == ["A", "B"]


def test_filter_chats_unread_only():
    chats = [_chat(title="A", unread_count=0), _chat(title="B", unread_count=3)]
    out = _filter_chats(chats, include_muted=False, networks=set(), unread=True, unreplied=False)
    assert [c.title for c in out] == ["B"]


def test_filter_chats_unreplied_only():
    # preview_is_sender=True means you sent last -> replied. False -> you owe a reply.
    chats = [
        _chat(title="A", preview_is_sender=True),
        _chat(title="B", preview_is_sender=False),
    ]
    out = _filter_chats(chats, include_muted=False, networks=set(), unread=False, unreplied=True)
    assert [c.title for c in out] == ["B"]


def test_filter_chats_combines_network_and_unread():
    chats = [
        _chat(title="A", network_type="WhatsApp", unread_count=0),
        _chat(title="B", network_type="WhatsApp", unread_count=5),
        _chat(title="C", network_type="Telegram", unread_count=5),
    ]
    out = _filter_chats(
        chats, include_muted=False, networks={"whatsapp"}, unread=True, unreplied=False
    )
    assert [c.title for c in out] == ["B"]


# --- flag-emoji handling ---------------------------------------------------

def test_deflag_converts_regional_indicator_to_bracketed_letters():
    assert _deflag("Sent Lisi \U0001F1F1\U0001F1E8 General") == "Sent Lisi [LC] General"


def test_deflag_leaves_plain_text_and_normal_emoji_untouched():
    assert _deflag("Team \U0001F600 chat") == "Team \U0001F600 chat"
    assert _deflag("no flags here") == "no flags here"


def test_deflag_handles_adjacent_flags_as_pairs():
    # 🇬🇧🇺🇸 -> [GB][US]
    assert _deflag("\U0001F1EC\U0001F1E7\U0001F1FA\U0001F1F8") == "[GB][US]"


def test_format_chat_display_deflags_title():
    chat = _chat(title="Sent Lisi \U0001F1F1\U0001F1E8 General", network_type="WhatsApp")
    result = _format_chat_display(chat)
    assert result.startswith("Sent Lisi [LC] General ")
    assert "\U0001F1F1" not in result


# --- group filtering -------------------------------------------------------

def test_filter_chats_no_groups_excludes_groups():
    chats = [
        _chat(title="Mum", is_group=False),
        _chat(title="Family", is_group=True),
        _chat(title="Dad", is_group=False),
    ]
    out = _filter_chats(
        chats, include_muted=False, networks=set(), unread=False, unreplied=False, no_groups=True
    )
    assert [c.title for c in out] == ["Mum", "Dad"]


def test_filter_chats_groups_included_by_default():
    chats = [_chat(title="Mum", is_group=False), _chat(title="Family", is_group=True)]
    out = _filter_chats(
        chats, include_muted=False, networks=set(), unread=False, unreplied=False, no_groups=False
    )
    assert [c.title for c in out] == ["Mum", "Family"]


# --- fzf line rendering ----------------------------------------------------

def test_render_fzf_lines_has_chat_id_first_column():
    chats = [_chat(chat_id="!x:beeper.local", title="Mum", network_type="WhatsApp")]
    lines = _render_fzf_lines(chats)
    line = lines.splitlines()[0]
    chat_id, display = line.split("\t", 1)
    assert chat_id == "!x:beeper.local"
    assert display.startswith("Mum ")
