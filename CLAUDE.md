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
- **watch.py**: The `beeper watch` engine — config validation (`parse_config` for a decoded mapping, `load_config` for a TOML file), the poll state machine (`scan` / `nag_pass`), atomic state file, and the `run` loop. Pure: chats in, events out, no typer and no requests, so the state machine is testable without a network **and vendorable into beeper-inbox's container**. Spec: `docs/superpowers/specs/2026-08-07-beeper-watch-design.md`.
- **watch_cli.py**: Typer wiring over `watch.py` (`watch`, `watch list`, `watch check`). Kept separate so the engine stays dependency-free — don't import typer into `watch.py`.
- **watch_ws.py**: Push transport for `watch` — Beeper's experimental event socket (`/v1/ws`, advertised by `GET /v1/info`). Transport only: connect, subscribe, reconnect, yield normalised `WatchMessage`. It makes no decisions; those stay in `watch.observe`. Needs `websockets`, imported lazily, so poll-only callers never load it.
- **wsl_proxy.py**: TCP proxy (runs on Windows) bridging WSL IPv4 → Beeper's IPv6 loopback. Entry point: `beeper-proxy`.

### Key Design Decisions

- **`_get_attr()` resilience pattern**: The Beeper SDK returns objects with varying field names across versions. `BeeperClient._get_attr(obj, *names, default=None)` tries multiple attribute names, enabling the adapter to survive schema changes.
- **Proxy auto-detection**: `cli.py` probes candidate ports with an HTTP health check (not just TCP connect) to detect stale proxy processes. Falls back to launching via PowerShell.
- **SMS splitting**: Messages to UK landlines (02x/03x/08x) are split at 160 chars to avoid silent MMS drops. Mobile numbers (07x) are sent as-is.
- **Chat cache**: `list_chats()` caches results with a 6-hour TTL. Use `--refresh-chats` to bypass.
- **Reply guidance**: Seven preset guidance modes affect LLM prompt construction. "analyse" and "todo" use entirely different system prompts.
- **`watch` has two transports and one state machine**: `watch.observe()` is the single decision point — the poll reads `(ts, is_sender, text)` off a chat's preview, the socket reads them off the message. Don't reimplement the rules in either path. The socket carries live traffic; the poll is the reconcile pass that covers a dropped socket, an experimental interface changing shape, and the chatID→title map `title_match` needs. Only the push path dedupes on `last_msg_id` (the socket re-delivers a message as its send status advances); the poll keeps its `ts <= last` rule.
- **`watch` polls uncached, and its re-raise is capped**: `list_chats` defaults to the 6-hour cache, so a watcher that forgets `use_cache=False` goes silently dead — silence is indistinguishable from "nothing happened", so this fails invisibly. And a chat whose last message is inbound has *not* necessarily gone unanswered (phone, email, in person), so the re-raise is capped at `nag.count`. Both are asserted in `tests/test_watch.py`; read spec §3.4 and §5.3 before loosening either.
- **`text_match`/`sender_match` filter, `chat`/`title_match` select**: `WatchSpec.matches()` deliberately ignores the content filters. Folding them in would make a group appear and disappear from `watch check` depending on who happened to speak last, and would make `[[watch]]` entries with only a filter look valid. `sender_match` tries the regex against the sender's *name and* their network id — a WhatsApp group frequently reports the raw LID as the name.
- **`[notify]` is described by the engine and performed by the host**: the engine validates `sink`/`target`/`kinds`/`priorities` into a `NotifySpec` and carries it, but never delivers — that would mean a network client in a module that is vendored into a container and is being mined for reusable parts. `beeper-inbox/app/notify.py` owns the sinks and looks one up by `sink` name, so adding a route is a change there plus one word in a config. `tests/test_watch.py` asserts the engine still imports no HTTP client.

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

**Runtime**: `typer`, `python-dotenv`, `requests`, `beeper_desktop_api`, `websockets` (push transport only, imported lazily)
**System**: `fzf` (interactive mode only), a text editor (`$EDITOR`)
**Test**: `pytest`
