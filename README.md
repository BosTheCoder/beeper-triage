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
python -m beeper_triage.cli triage --message-window 7d
python -m beeper_triage.cli triage --model openai/gpt-4o-mini
python -m beeper_triage.cli triage --no-llm --dry-run

After selecting a chat, the CLI prompts for a message window (today, 2d, 7d, 14d, 30d, 60d, 365d, all).
Use `--message-window` to skip the prompt. `--max-messages` is optional; omit it to fetch the full window.
```

## Actions

After selecting a chat, you choose an action:

- **Reply** -- generate an LLM draft and send (or preview with `--dry-run`)
- **Copy to clipboard** -- copy the full conversation transcript to the system clipboard
- **Export to folder** -- write a timestamped transcript to `exports/`

Clipboard support: `clip.exe` (WSL), `wl-copy` (Wayland), `xclip`, `xsel`.

## Notes

- Requires `fzf` on PATH for chat selection.
- Uses MVP "needs reply" filter: preview.is_sender == False.
