# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**beeper-triage** is a minimal Python CLI tool that helps users triage Beeper chat messages and draft intelligent replies using OpenRouter AI models. It integrates with Beeper Desktop's API and LLM services to automate the process of responding to messages across different chat networks.

### Core Workflow

1. Fetch unread Beeper chats that need replies
2. User selects a chat interactively via `fzf` (fuzzy finder)
3. Retrieve full message history from the selected chat
4. User chooses an action: **Reply** (continue to LLM draft) or **Copy to clipboard** (copy transcript and exit)
5. Generate a draft reply using an LLM (via OpenRouter API)
6. Open user's configured text editor to review/modify the draft
7. Send the reply back through Beeper or preview in dry-run mode

## Repository Structure

```
beeper-triage/
├── beeper_triage/              # Main Python package
│   ├── cli.py                  # CLI orchestration and entry point
│   ├── beeper_client.py        # Beeper API wrapper/adapter
│   ├── openrouter_client.py    # OpenRouter LLM API client
│   ├── editor.py               # Text editor interface helpers
│   ├── prompts.py              # LLM prompt construction
│   └── __init__.py
├── .env                        # Runtime config (secrets - not in git)
├── pyproject.toml              # Project metadata and dependencies
└── README.md                   # User-facing documentation
```

### Key Modules

- **cli.py**: Entry point (`beeper-triage triage`). Orchestrates the full workflow: chat filtering, selection, message fetching, action choice (reply or copy to clipboard), LLM generation, editing, and sending. Includes helpers for clipboard detection (`_detect_clipboard_cmd()`), transcript formatting with timestamps (`_format_transcript_with_timestamps()`), and clipboard copy (`_copy_to_clipboard()`). Custom exceptions (BeeperSDKError, OpenRouterError, EditorError) converted to user-friendly CLI errors.

- **beeper_client.py**: Wrapper around the official `beeper_desktop_api` SDK. Provides `list_chats()` and `list_messages()` methods with normalized response handling via `BeeperChat` and `BeeperMessage` dataclasses. Includes resilient attribute extraction (`_get_attr()`) to handle API schema variations.

- **openrouter_client.py**: REST client for OpenRouter API. Handles LLM calls via `create_chat_completion()` with proper header handling and error propagation.

- **editor.py**: Invokes the user's configured text editor (e.g., vim, nano) to allow message review/modification in a temporary file. Handles file creation and cleanup.

- **prompts.py**: Builds system + user prompts for the LLM. Formats chat transcript and context to guide reply generation.

## Development Commands

### Setup

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in editable mode (includes all dependencies)
pip install -e .

# Or install dependencies manually
pip install typer python-dotenv requests beeper_desktop_api
```

### Environment Configuration

Create `.env` file with required variables:

```env
BEEPER_ACCESS_TOKEN=your_beeper_token
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
EDITOR=vim
```

Optional variables:
- `BEEPER_BASE_URL`: Override default Beeper API endpoint (useful for testing)

### Running the Application

```bash
# Basic usage - triage unread chats needing replies
beeper-triage triage

# Or via module directly
python -m beeper_triage.cli triage

# Limit number of chats to consider
beeper-triage triage --max-chats 30

# Fetch more message history per chat
beeper-triage triage --max-messages 40

# Override the default LLM model
beeper-triage triage --model openai/gpt-4o-mini

# Skip LLM generation (review chats without AI draft)
beeper-triage triage --no-llm

# Dry-run mode (show draft without sending)
beeper-triage triage --dry-run

# Include muted chats in triage
beeper-triage triage --include-muted

# Combine options
beeper-triage triage --max-chats 20 --no-llm --dry-run
```

After selecting a chat, you are prompted to choose an action:
- **[1] Reply** -- proceeds with the LLM draft and reply flow
- **[2] Copy to clipboard** -- formats the transcript with timestamps and copies it to the system clipboard (supports `clip.exe` on WSL, `wl-copy`, `xclip`, `xsel`)

## Architecture and Design

### Design Patterns

1. **Client/Service Pattern**: `BeeperClient` and `OpenRouterClient` encapsulate external API interactions with consistent error handling.

2. **Adapter Pattern**: `BeeperClient` wraps the official SDK and normalizes responses to internal dataclasses (`BeeperChat`, `BeeperMessage`), handling schema variations gracefully.

3. **Dataclass-Based Models**: Immutable data containers (`BeeperMessage`, `BeeperChat`, `OpenRouterMessage`) provide type safety and clarity.

4. **Composition Over Inheritance**: `cli.py` composes multiple clients rather than inheriting from them.

5. **CLI Framework Pattern**: Uses `typer` for declarative argument/option handling with built-in validation and help generation.

### Key Data Structures

- **BeeperChat**: Normalized chat summary with fields: `id`, `name`, `unread_count`, `last_message_ms`, `preview` (message preview with `text` and `is_sender`)
- **BeeperMessage**: Message data with: `id`, `text`, `sender`, `timestamp_ms`, `user_id`
- **OpenRouterMessage**: LLM API payload with `role` and `content`

### Error Handling

Custom exceptions at API boundaries:
- `BeeperSDKError`: Beeper API/SDK failures
- `OpenRouterError`: LLM API failures
- `EditorError`: Text editor invocation failures

All caught and converted to `typer.BadParameter()` for user-friendly CLI output.

### Chat Filtering Logic ("Needs Reply")

A chat is included in triage if:
1. `unread_count > 0` (has unread messages)
2. `preview.is_sender == False` (last message is NOT from the authenticated user)
3. Chat is not muted (unless `--include-muted` flag used)

This MVP filter avoids replying to your own messages and focuses on chats awaiting response.

### Action Choice Flow

After a chat is selected and messages are fetched, the user is prompted with `[1] Reply  [2] Copy to clipboard`. Choosing "copy" formats the full transcript with human-readable timestamps and pipes it to a detected clipboard command. The tool auto-detects the appropriate clipboard utility for the platform (`clip.exe` for WSL, `wl-copy` for Wayland, `xclip`/`xsel` for X11).

## Dependencies

### Runtime

- **typer** (latest): CLI framework for command-line argument/option parsing with type hints
- **python-dotenv** (latest): Load environment variables from `.env` for secure config management
- **requests** (latest): HTTP client for OpenRouter API calls
- **beeper_desktop_api** (latest): Official Beeper Desktop SDK for chat and message access

### Build-Time

- **setuptools** (>=68): Package building
- **wheel**: Distribution format

### System

- **fzf**: Fuzzy finder for interactive chat selection (required on PATH)
- **Text editor** (vim, nano, emacs, etc.): Configured via `EDITOR` env var for message editing

## Important Implementation Details

### Message Timestamp Handling

`BeeperMessage` timestamps are flexible and accept both:
- Python `datetime` objects (converted to milliseconds)
- Integer millisecond Unix timestamps (used directly)

The `_get_attr()` resilience pattern in `BeeperClient` handles both and gracefully falls back to alternative field names.

### Chat Ordering

Messages are sorted chronologically by `timestamp_ms` before being formatted for the LLM, ensuring correct conversation context.

### Temporary File Handling

The editor flow creates temporary files that are properly cleaned up after editing. The edited content is read back and used as the final message draft.

### API Response Resilience

The Beeper SDK may return responses with slightly different field names or structures. `BeeperClient._get_attr()` provides defensive extraction:

```python
def _get_attr(obj, *names, default=None):
    """Try multiple attribute names, return first found or default."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default
```

This allows the adapter to work with API schema variations without breaking.

### LLM Prompt Construction

`prompts.py` builds a system prompt that establishes the AI's role and a user message containing:
- Full formatted conversation history
- Instruction to draft a helpful reply

The LLM generates a single reply, which is then presented to the user for editing.

## Common Development Workflows

### Modifying Chat Selection Logic

Edit the `_needs_reply()` function in `cli.py` to change the filtering criteria. Currently checks: unread count, last message sender, and mute status.

### Adding New CLI Options

Add new parameters to the `triage()` function signature in `cli.py`. Use `typer.Option()` for customization (help text, default values, etc.).

### Changing LLM Behavior

Modify `prompts.py` to adjust system prompt and user message formatting. The `build_prompt()` function takes the message transcript and returns a list of `OpenRouterMessage` objects ready for API consumption.

### Debugging API Calls

Check `.env` file values first (ensure `BEEPER_ACCESS_TOKEN` and `OPENROUTER_API_KEY` are correct). The client classes include error handling that prints meaningful messages. Add `print()` statements in client methods or use Python debugger (`pdb`) for deeper investigation.

### Testing Changes Safely

Use `--dry-run` flag to preview drafts without sending:
```bash
beeper-triage triage --max-chats 5 --dry-run
```

Use `--no-llm` to skip LLM generation and test the chat selection flow alone:
```bash
beeper-triage triage --no-llm
```

## Notes for Future Development

- **No test suite**: This MVP has no automated tests. Consider adding pytest for critical paths (chat filtering, message formatting).
- **Single command structure**: Currently one command (`triage`). Future subcommands could include: `draft` (offline drafting), `templates` (message templates), `stats` (analytics).
- **No batch processing**: Each run processes one chat. Batch/scheduled triage would require refactoring.
- **Minimal persistence**: No message queue, retry logic, or audit trail. Failed sends are not recovered.
- **MVP UI**: Uses fzf for selection. A TUI (Text User Interface) framework like `rich` or `textual` could enhance interactivity.

## Type Hints and Code Style

- **Type hints**: Comprehensive use of Python 3.10+ type hints throughout (PEP 484)
- **Style**: Follows PEP 8 (4-space indentation, descriptive names)
- **Docstrings**: Present on classes but sparse on functions (consider expanding for public APIs)
- **Error handling**: Try-catch at API boundaries, custom exception classes for semantics

## Security Considerations

- Secrets managed via `.env` file (must not be committed to git)
- API keys passed as Bearer tokens in `Authorization` header to OpenRouter
- Temporary editor files created in system temp directory and cleaned up
- No logging of sensitive data (tokens, API responses with PII)
