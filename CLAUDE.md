# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**beeper-triage** is a Python CLI tool for triaging Beeper chat messages and drafting replies using OpenRouter LLMs. It wraps the Beeper Desktop API and supports interactive (fzf) and non-interactive (agent) modes.

### Core Workflow

1. Fetch and filter Beeper chats (muted, needs-reply, etc.)
2. Select a chat via fzf (interactive) or `--chat-id` (agent mode)
3. Choose a time window for message history
4. Pick an action: **Reply** (LLM draft → editor → send), **Copy to clipboard**, or **Export to folder**
5. For replies: optionally select reply guidance (close, rekindle, decline, schedule, todo, analyse, or custom)
6. Review/edit draft in `$EDITOR`, then confirm and send

## Development Commands

```bash
# Setup (install globally, editable)
uv tool install -e .

# Run from project directory (no install needed)
uv run beeper-triage

# Run
beeper-triage                          # interactive triage
beeper-triage --dry-run                # preview without sending
beeper-triage --no-llm                 # skip LLM, test chat selection only
beeper-triage new-chat --phone +44... --network whatsapp -m "Hello"

# Agent mode (non-interactive, JSON output)
beeper-triage --agent                  # list chats as JSON
beeper-triage --agent --chat-id X --action reply --guidance close --no-edit

# Tests
uv run pytest tests/                   # run all tests
uv run pytest tests/test_cli.py        # run specific test file
uv run pytest tests/test_cli.py::test_name  # run single test
```

## Environment Configuration

Required in `.env`:
```
BEEPER_ACCESS_TOKEN=your_beeper_token
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
EDITOR=vim
```

**IMPORTANT**: `BEEPER_BASE_URL=http://172.28.96.1:23374` is REQUIRED for this WSL development environment. If `BEEPER_BASE_URL` is unset or unreachable, the CLI auto-detects the proxy port or starts it via PowerShell.

## Architecture

### Two CLI Commands

- **`triage`** (default): Full triage workflow — list chats, select, fetch messages, choose action, generate reply, send
- **`new-chat`**: Start a new chat with a phone number on a specific network (WhatsApp, Signal, etc.)

Both commands support `--agent` mode for non-interactive JSON I/O.

### Module Responsibilities

- **cli.py**: Orchestration, CLI options via typer, proxy auto-start, SMS splitting for UK landlines, transcript export. Contains both `triage()` and `new_chat()` commands.
- **beeper_client.py**: Adapter over `beeper_desktop_api` SDK. Normalizes responses to `BeeperChat`/`BeeperMessage` dataclasses. Caches chat list to `~/.cache/beeper-triage/chats.json` (6-hour TTL). Provides `list_chats()`, `list_messages()`, `list_accounts()`, `search_contacts()`, `create_chat()`, `send_message()`.
- **openrouter_client.py**: REST client for OpenRouter API via `requests`.
- **prompts.py**: Builds LLM prompts. Three prompt builders: `build_prompt()` (reply), `build_todo_prompt()` (acknowledge + todo), `build_analyse_prompt()` (next steps analysis).
- **editor.py**: Opens `$EDITOR` with a temp file for draft review.
- **wsl_proxy.py**: TCP proxy (runs on Windows) bridging WSL IPv4 → Beeper's IPv6 loopback. Entry point: `beeper-proxy`.

### Key Design Decisions

- **`_get_attr()` resilience pattern**: The Beeper SDK returns objects with varying field names across versions. `BeeperClient._get_attr(obj, *names, default=None)` tries multiple attribute names, enabling the adapter to survive schema changes.
- **Proxy auto-detection**: `cli.py` probes candidate ports with an HTTP health check (not just TCP connect) to detect stale proxy processes. Falls back to launching via PowerShell.
- **SMS splitting**: Messages to UK landlines (02x/03x/08x) are split at 160 chars to avoid silent MMS drops. Mobile numbers (07x) are sent as-is.
- **Chat cache**: `list_chats()` caches results with a 6-hour TTL. Use `--refresh-chats` to bypass.
- **Reply guidance**: Seven preset guidance modes affect LLM prompt construction. "analyse" and "todo" use entirely different system prompts.

### Reply Guidance Modes

| Key | Effect |
|-----|--------|
| `close` | Wrap up, no back-and-forth |
| `going` | Match energy, keep flowing |
| `rekindle` | Re-engage the conversation |
| `decline` | Soft decline |
| `schedule` | Focus on scheduling |
| `todo` | Acknowledge + generate a todo item (split by `---`) |
| `analyse` | LLM analysis of next steps, no reply sent |

### Agent Mode

When `--agent` is passed, the CLI:
- Outputs JSON instead of human text
- Skips fzf and interactive prompts
- Requires `--chat-id` to proceed past chat listing
- Requires `--action` (reply/copy/export)
- Skips editor (`--no-edit` is implicit)

## Dependencies

**Runtime**: `typer`, `python-dotenv`, `requests`, `beeper_desktop_api`
**System**: `fzf` (interactive mode only), a text editor (`$EDITOR`)
**Test**: `pytest`
