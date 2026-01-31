# beeper-triage

Minimal CLI to triage Beeper chats and draft replies with OpenRouter.

## Setup

1) Create a virtualenv and install deps:

```bash
python -m venv .venv
source .venv/bin/activate
pip install typer python-dotenv requests beeper_desktop_api
```

2) Create a `.env` file:

```env
BEEPER_ACCESS_TOKEN=your_beeper_token
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
EDITOR=vim
```

3) Run:

```bash
python -m beeper_triage.cli triage
```

## Examples

```bash
python -m beeper_triage.cli triage --max-chats 30 --max-messages 40
python -m beeper_triage.cli triage --model openai/gpt-4o-mini
python -m beeper_triage.cli triage --no-llm --dry-run
```

## Actions

After selecting a chat, you choose an action:

- **Reply** -- generate an LLM draft and send (or preview with `--dry-run`)
- **Copy to clipboard** -- copy the full conversation transcript to the system clipboard

Clipboard support: `clip.exe` (WSL), `wl-copy` (Wayland), `xclip`, `xsel`.

## Notes

- Requires `fzf` on PATH for chat selection.
- Uses MVP "needs reply" filter: unread_count > 0 and preview.is_sender == False.
