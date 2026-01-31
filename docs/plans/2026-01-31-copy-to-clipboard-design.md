# Copy Chat Context to Clipboard

## Summary

Add a "copy to clipboard" action to beeper-triage so users can grab chat context and paste it into other LLM tools (ChatGPT, etc.) instead of only replying through Beeper.

## Flow Change

Current: chat selection → fetch messages → LLM draft → edit → send

New: chat selection → fetch messages → **pick action** → (reply OR copy)

### Action Prompt

After messages are fetched, display:

```
Action: [1] Reply  [2] Copy to clipboard
>
```

- `1` (default) — existing reply flow
- `2` — format transcript with timestamps, copy to clipboard, exit
- Invalid input / Ctrl-C — exit cleanly (code 0)

## Transcript Format (Clipboard)

```
[2025-01-30 14:32] Alice: Hey, are you free tomorrow?
[2025-01-30 14:33] You: Yeah, what's up?
[2025-01-30 14:35] Alice: Want to grab coffee?
```

Timestamps: `YYYY-MM-DD HH:MM` in local time, converted from `BeeperMessage.timestamp_ms`.

## Clipboard Mechanism

Pipe text into a detected clipboard tool via `subprocess.run()`.

Detection order:
1. `clip.exe` (WSL — highest priority, works out of the box)
2. `wl-copy` (Wayland)
3. `xclip -selection clipboard` (X11)
4. `xsel --clipboard --input` (X11 fallback)

If none found, print error and exit with non-zero code.

## Code Changes

All changes in `cli.py`:

1. Add `_pick_action()` — simple input prompt returning "reply" or "copy"
2. Add `_format_transcript_with_timestamps()` — like existing `_format_transcript()` but with `[YYYY-MM-DD HH:MM]` prefix per line
3. Add `_detect_clipboard_cmd()` — returns the first available clipboard command
4. Add `_copy_to_clipboard(text)` — pipes text into detected clipboard tool
5. Insert action choice between message fetch and reply flow in `triage()`
6. Branch: if "copy", format with timestamps → copy → confirm → exit

No new files, no new dependencies.
