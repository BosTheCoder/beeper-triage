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

```bash
# Basic triage
beeper-triage

# Limit chats and set message window upfront
beeper-triage --max-chats 30 --message-window 7d

# Override LLM model
beeper-triage --model openai/gpt-4o-mini

# Skip LLM / dry-run
beeper-triage --no-llm --dry-run

# Include muted chats
beeper-triage --include-muted

# Only chats where someone else sent the last message
beeper-triage --needs-reply-only

# Force refresh the chat cache (bypasses 6-hour TTL)
beeper-triage --refresh-chats

# Provide reply guidance upfront (skip interactive prompt)
beeper-triage --guidance close
beeper-triage --guidance "ask about the weekend"
```

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
