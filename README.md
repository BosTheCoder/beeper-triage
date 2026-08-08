# beeper-triage

Minimal CLI to triage Beeper chats and draft replies with OpenRouter.

## Setup

1) Install via [uv](https://docs.astral.sh/uv/):

```bash
# Install globally (available from anywhere)
uv tool install -e .

# Or just run from the project directory (no install needed)
uv run beeper-triage
```

2) Create a `.env` file:

```env
BEEPER_ACCESS_TOKEN=your_beeper_token
BEEPER_BASE_URL=http://172.28.96.1:23374
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
EDITOR=vim
```

**Note**: `BEEPER_BASE_URL` points to the local Beeper Desktop API instance. If omitted, the CLI will auto-detect and start the WSL proxy.

3) Run:

```bash
beeper-triage
```

## Usage

> The command was renamed from `beeper-triage` to `beeper`; `beeper-triage` still works as a deprecated alias.

```bash
# Basic triage
beeper triage

# Limit chats and set message window upfront
beeper triage --max-chats 30 --message-window 7d

# Override LLM model
beeper triage --model openai/gpt-4o-mini

# Skip LLM / dry-run
beeper triage --no-llm --dry-run

# Include muted chats
beeper triage --include-muted

# Filter by network (repeatable; aliases like wa/tg/ig accepted)
beeper triage --network whatsapp
beeper triage --network whatsapp --network telegram

# Only unread chats / only chats you owe a reply to (filters combine with AND)
beeper triage --unread
beeper triage --unreplied
beeper triage --network whatsapp --unread

# Only 1:1 chats (hide group chats)
beeper triage --no-groups

# Force refresh the chat cache (bypasses 6-hour TTL)
beeper triage --refresh-chats

# Provide reply guidance upfront (skip interactive prompt)
beeper triage --guidance close
beeper triage --guidance "ask about the weekend"
```

### Interactive picker

In the fzf chat picker, each `[network]` tag is colour-coded (WhatsApp green,
Telegram cyan, Instagram magenta, LinkedIn blue, X white, Google Messages
bright-blue, Beeper yellow). Live filter shortcuts:

- **alt-u** — unread chats only
- **alt-r** — unreplied chats only (you owe a reply)
- **alt-g** — 1:1 chats only (hide groups)
- **alt-a** — reset to all
- **type a network name** (e.g. `whatsapp`) to filter by network — the tag is part of each row

Launch flags (`--network`, `--unread`, `--unreplied`, `--no-groups`) are
preserved as the base view, so the live toggles filter within them.

Regional-indicator flag emojis in titles (e.g. 🇱🇨) are shown as their letter
code (`[LC]`) because terminals compute their display width inconsistently and
garble the row; all other emojis are left as-is.

After selecting a chat, the CLI prompts for a message window (today, 2d, 7d, 14d, 30d, 60d, 365d, all).
Use `--message-window` to skip the prompt. `--max-messages` is an optional safety cap.

## Actions

After selecting a chat, you choose an action:

- **Reply** -- pick reply guidance, generate an LLM draft, review in editor, and send (or preview with `--dry-run`)
- **Copy to clipboard** -- copy the full timestamped transcript to the system clipboard
- **Export to folder** -- write a timestamped transcript to `exports/`

Clipboard support: `clip.exe` (WSL), `wl-copy` (Wayland), `xclip`, `xsel`.

## Reply Guidance

When replying, you can choose a guidance preset or type custom guidance:

| Preset | Description |
|---------|-------------|
| `close` | Wrap things up naturally |
| `going` | Keep it going (same energy) |
| `rekindle` | Re-engage the conversation |
| `decline` | Soft decline (not obvious) |
| `schedule` | Arrange or schedule something |
| `todo` | Acknowledge + generate a todo item |
| `analyse` | Analyse best next steps (no reply sent) |

## Watching chats

`beeper watch` polls a named set of chats and prints **one line per event** on
stdout — the shape the Claude Code Monitor tool, `grep`, `tee`, `notify-send` or
a webhook all consume with no adapter. It observes only; it never sends.

```bash
beeper watch --config npm-13-edward        # foreground, one line per event
beeper watch --config ./my-watch.toml --once   # single poll (cron, tests)
beeper watch --config npm-13-edward --dry-run  # seed state, emit nothing
beeper watch --config npm-13-edward --json     # one JSON object per line

beeper watch list  --config npm-13-edward  # watches → resolved chats, last activity
beeper watch check npm-13-edward           # same, but exits 1 if a pattern matches nothing
```

`--config` takes a path, or a bare name resolved from `~/.config/beeper-watches/<name>.toml`.

Output:

```
REPLY: ELEC AK Electrical (NAPIT) | Your welcome. Please if you don't mind can you take a minute...
STILL UNANSWERED (34m, final reminder): GAS Kamdem HomeTech
```

Diagnostics and failed polls go to **stderr**, never stdout. A poll that raises
is logged and skipped — the loop and the state file both survive it.

### Config

One TOML file per watch:

```toml
name          = "npm-13-edward"
poll_seconds  = 180
state         = "~/.local/state/beeper-watch/npm-13-edward.json"

[nag]
after_seconds = 1800   # re-raise a still-open chat after this long
count         = 1      # 0 disables re-raises entirely

[filters]
inbound_only  = true   # false also reports your own messages, as ACTIVITY

[[watch]]
chat  = "!2VeiAV7APqb0sTtSY226:beeper.local"
label = "ELEC AK Electrical (NAPIT)"

[[watch]]
title_match = "(?i)damp detectives"     # regex on the chat title
label       = "DAMP Damp Detectives"
priority    = "high"                    # advisory; shows as [high] in the line

[[watch]]
title_match = "(?i)cadent|national grid"
label       = "GAS network"
text_match  = "(?i)\\b(appointment|engineer|book(ed|ing)?)\\b"

[[watch]]
title_match  = "(?i)13 edward"          # a group…
sender_match = "(?i)richard|landlord"   # …but only when these two speak
label        = "13 Edward — landlord"
```

`text_match` and `sender_match` are optional and **per-watch, never global** —
the chat allowlist does the real work, and a keyword sweep over a whole account
is the part most likely to generate false positives. Neither one *selects* a
chat, only filters messages inside one that `chat`/`title_match` already claimed;
otherwise a group would appear and disappear from `watch check` depending on who
spoke last. An unknown key in a `[[watch]]` block is a hard error, because a
typo'd key would otherwise silently disable the watch.

`sender_match` is tried against both the sender's display name and their network
id, because a WhatsApp group often reports the raw handle
(`@whatsapp_lid-8122…`) as the name — matching either means one regex works
wherever the chat lives.

### Notifying a phone

A `[notify]` block says where fired events should be pushed:

```toml
[notify]
sink       = "telegram"
kinds      = ["reply", "unanswered"]   # optional; default all three
priorities = ["high"]                  # optional; default all
```

The **CLI does not deliver this** — `beeper watch` prints to stdout, and the
engine holds no network client on purpose. Delivery belongs to a long-lived
host: register the same config with [beeper-inbox](../beeper-inbox) and its
container sends it. The engine validates the block either way, so a typo fails
at `watch check` rather than at 2am.

### Two things worth knowing before changing it

**Seed before arming.** Without `--dry-run` first, the initial poll fires an
event for every watched chat whose last message merely happens to be inbound.

**The re-raise is capped, and the cap is the point.** A chat stays "open" while
their message was the last one, which is *not* the same as "you owe a reply" —
it may have been answered by phone, by email, or in person, and this tool cannot
see any of that. The re-raise has exactly one job: covering a lost first
notification. An uncapped one is a permanent alarm on a resolved item, and it
trains you to ignore the channel. See §5.3 of
`docs/superpowers/specs/2026-08-07-beeper-watch-design.md`.

## Agent Mode

For non-interactive / programmatic use:

```bash
# List chats as JSON
beeper-triage --agent

# Act on a specific chat
beeper-triage --agent --chat-id "!abc:beeper.local" --action reply --guidance close --no-edit

# Provide a draft directly (skips LLM and editor)
beeper-triage --agent --chat-id "!abc:beeper.local" --action reply --draft "Thanks, will do!"
```

Agent mode outputs JSON and requires no interactive prompts.

## WSL Proxy

On WSL, Beeper Desktop listens on IPv6 loopback which isn't directly reachable. The included proxy bridges the gap:

```bash
# Auto-started by beeper-triage when BEEPER_BASE_URL is not set.
# To run manually on Windows:
beeper-proxy
# Or: python beeper_triage/wsl_proxy.py
```

The proxy auto-detects the Beeper Desktop port (23374 or 23373) and forwards traffic from `0.0.0.0` to `[::1]`.

### Troubleshooting: WSL can't reach Windows host

If `beeper-triage` hangs at "Proxy not running — starting via PowerShell ..." (use `-v` for verbose output), the most likely cause is **Windows Firewall blocking inbound connections from WSL2**.

WSL2 uses a virtual network adapter. When your Windows network profile is set to **Public**, the firewall blocks all inbound from the WSL subnet — including to Beeper's ports. Switching to **Private** fixes it:

```powershell
# Run in an elevated PowerShell (Run as Administrator):
Set-NetConnectionProfile -InterfaceAlias "WiFi 2" -NetworkCategory Private
```

Replace `"WiFi 2"` with your actual adapter name (check with `Get-NetConnectionProfile`).

If you can't change the network profile, add a targeted firewall rule instead:

```powershell
# Run in an elevated PowerShell:
New-NetFirewallRule -DisplayName "WSL Inbound" -Direction Inbound -Action Allow -Protocol TCP -RemoteAddress 172.16.0.0/12 -Profile Any
```

### Troubleshooting: VPN software blocking WSL-to-Windows traffic

VPN clients (especially **Surfshark**, but also NordVPN, ExpressVPN, etc.) can silently break WSL2's ability to reach the Windows host — even though internet access from WSL still works fine.

**Symptoms:** All proxy port probes time out, the PowerShell auto-start times out, but `powershell.exe` commands work (those use WSL interop, not TCP/IP).

**Why this happens:** WSL2 runs in a Hyper-V VM with a virtual network switch. Traffic from WSL to the internet goes through NAT at the hypervisor level and never needs to reach a port on the Windows host. But traffic from WSL *to* the Windows host (like the Beeper proxy on `172.x.x.x:23374`) must be accepted by the host's firewall. VPN clients like Surfshark install **Hyper-V firewall rules** (e.g. "Block All IPv6" with address range `::-ffff:ffff:...`) and WireGuard tunnel adapters that interfere with this path. The Hyper-V firewall sits between WSL and Windows — below the regular Windows Firewall — so standard firewall allow-rules don't help.

**Fix:** Disconnect or disable the VPN, then retry. If you need the VPN active, check if your VPN client has a split-tunnelling option to exclude local/LAN traffic, or remove the offending Hyper-V firewall rules:

```powershell
# Check for VPN-added Hyper-V firewall rules:
Get-NetFirewallHyperVRule | Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Block' } | Select-Object DisplayName, Direction, RemoteAddresses

# Remove a specific rule (e.g. Surfshark):
Get-NetFirewallHyperVRule | Where-Object { $_.DisplayName -like '*Surfshark*' } | Remove-NetFirewallHyperVRule
```

## Notes

- Requires `fzf` on PATH for interactive chat selection.
- Long SMS to UK landlines are auto-split into 160-char chunks to avoid MMS conversion.
- Chat list is cached with a 6-hour TTL; use `--refresh-chats` to bypass.
