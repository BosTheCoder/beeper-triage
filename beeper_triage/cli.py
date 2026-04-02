"""CLI entrypoint for beeper-triage."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import asdict
from typing import Iterable, Optional

import typer
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from .beeper_client import BeeperChat, BeeperClient, BeeperMessage, BeeperSDKError
from .editor import EditorError, edit_text
from .openrouter_client import OpenRouterClient, OpenRouterError
from .prompts import build_analyse_prompt, build_prompt, build_todo_prompt

app = typer.Typer(add_completion=False)


_SMS_MAX_CHARS = 160

# UK number patterns that cannot receive MMS (landlines, business lines).
# Messages over 160 chars get converted to MMS which these numbers silently
# drop.  Mobile numbers (07xx) handle long SMS fine via concatenation.
_LANDLINE_RE = re.compile(
    r"^\+44(?:2|3|8)\d+"  # 02x, 03x, 08x — landlines / non-geographic
)


def _needs_sms_split(phone: str) -> bool:
    """Return True if *phone* (E.164) is a UK landline / non-geographic number."""
    return bool(_LANDLINE_RE.match(phone))


def _split_sms(text: str, limit: int = _SMS_MAX_CHARS) -> list[str]:
    """Split *text* into chunks of at most *limit* characters.

    Tries to break at sentence boundaries (`. `, `! `, `? `) first, then at
    spaces, and only hard-splits as a last resort.  This avoids MMS conversion
    on landline / 020 / 08xx numbers that cannot receive MMS.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try sentence boundary
        best = -1
        for sep in (". ", "! ", "? "):
            idx = text.rfind(sep, 0, limit)
            if idx > best:
                best = idx + len(sep)  # include the separator
        if best > 0:
            chunks.append(text[:best].rstrip())
            text = text[best:].lstrip()
            continue
        # Try space
        idx = text.rfind(" ", 0, limit)
        if idx > 0:
            chunks.append(text[:idx])
            text = text[idx + 1:]
            continue
        # Hard split
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise typer.BadParameter(f"Missing env var: {name}")
    return value


def _ensure_fzf() -> None:
    if shutil.which("fzf") is None:
        raise typer.BadParameter("Missing dependency: fzf is not on PATH.")


def _format_chat_display(
    chat: "BeeperChat", show_account_label: bool = False
) -> str:
    """Format a chat for display in FZF with account and network info."""
    parts = [chat.title]
    if chat.network_type:
        if show_account_label and chat.account_label:
            # Show account label when there are multiple accounts for the same network
            parts.append(f"[{chat.network_type} • {chat.account_label}]")
        else:
            parts.append(f"[{chat.network_type}]")
    if chat.unread_count > 0:
        parts.append(f"({chat.unread_count} new)")

    # Add last activity timestamp for context
    if chat.last_activity_ms:
        dt = datetime.datetime.fromtimestamp(chat.last_activity_ms / 1000)
        now = datetime.datetime.now()

        # Show relative time for recent activity, absolute for older
        delta = now - dt
        if delta.days == 0:
            time_str = dt.strftime("%H:%M")
        elif delta.days == 1:
            time_str = "yesterday"
        elif delta.days < 7:
            time_str = f"{delta.days}d ago"
        else:
            time_str = dt.strftime("%b %d")

        parts.append(f"• {time_str}")

    return " ".join(parts)


def _pick_chat_fzf(chats: list["BeeperChat"]) -> Optional[str]:
    if not chats:
        return None

    # Check if there are multiple accounts with the same network
    network_counts: dict[str, int] = {}
    for chat in chats:
        if chat.network_type:
            network_counts[chat.network_type] = network_counts.get(chat.network_type, 0) + 1
    show_account_labels = any(count > 1 for count in network_counts.values())

    input_text = "\n".join(
        [
            f"{chat.chat_id}\t{_format_chat_display(chat, show_account_labels)}"
            for chat in chats
        ]
    )
    result = subprocess.run(
        ["fzf", "--ansi", "--with-nth", "2..", "--prompt", "Chat> ", "--tiebreak=index"],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if not line:
        return None
    return line.split("\t", 1)[0]


def _needs_reply(preview_is_sender: bool) -> bool:
    return not preview_is_sender


def _format_transcript(messages: Iterable[BeeperMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        speaker = "You" if msg.is_sender else msg.sender_name
        text = msg.text.strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines).strip()


def _format_transcript_with_timestamps(messages: Iterable[BeeperMessage]) -> str:
    """Format messages as a timestamped transcript for clipboard export."""
    lines: list[str] = []
    for msg in messages:
        speaker = "You" if msg.is_sender else msg.sender_name
        text = msg.text.strip()
        if not text:
            continue
        dt = datetime.datetime.fromtimestamp(msg.timestamp_ms / 1000)
        ts = dt.strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] {speaker}: {text}")
    return "\n".join(lines).strip()


def _detect_clipboard_cmd() -> Optional[list[str]]:
    """Return the command list for the first available clipboard tool, or None."""
    candidates = [
        (["clip.exe"], "clip.exe"),
        (["wl-copy"], "wl-copy"),
        (["xclip", "-selection", "clipboard"], "xclip"),
        (["xsel", "--clipboard", "--input"], "xsel"),
    ]
    for cmd, binary in candidates:
        if shutil.which(binary):
            return cmd
    return None


def _copy_to_clipboard(text: str, cmd: list[str]) -> None:
    """Pipe text into the given clipboard command."""
    subprocess.run(cmd, input=text, text=True, check=True)


def _sanitize_export_suffix(title: str) -> str:
    cleaned = title.strip().lower()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9\-]+", "", cleaned)
    cleaned = cleaned.strip("-")
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip("-")
    return cleaned


def _export_transcript(
    transcript: str, chat_title: str, export_root: str = "exports"
) -> str:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    suffix = _sanitize_export_suffix(chat_title)
    base_name = f"{timestamp}-{suffix}" if suffix else timestamp
    export_base = os.path.join(export_root, base_name)
    export_path = export_base
    counter = 2
    while os.path.exists(export_path):
        export_path = f"{export_base}-{counter}"
        counter += 1
    os.makedirs(export_path, exist_ok=False)
    transcript_path = os.path.join(export_path, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as handle:
        handle.write(transcript)
        handle.write("\n")
    return export_path


def _pick_action() -> Optional[str]:
    """Prompt user to pick an action. Returns 'reply', 'copy', or None (cancelled)."""
    try:
        while True:
            choice = input(
                "\nAction: [1] Reply  [2] Copy to clipboard  [3] Export to folder\n> "
            ).strip()
            if choice == "" or choice == "1":
                return "reply"
            if choice == "2":
                return "copy"
            if choice == "3":
                return "export"
            typer.echo("Invalid choice. Enter 1, 2, or 3.")
    except (KeyboardInterrupt, EOFError):
        return None


_MESSAGE_WINDOW_CHOICES: list[tuple[str, str]] = [
    ("today", "Today (since 00:00)"),
    ("2d", "Last 2 days"),
    ("7d", "Last 7 days"),
    ("14d", "Last 14 days"),
    ("30d", "Last 30 days"),
    ("60d", "Last 60 days"),
    ("365d", "Last 365 days"),
    ("all", "All messages (no time filter)"),
]

_MESSAGE_WINDOW_ALIASES = {
    "today": "today",
    "2d": "2d",
    "2days": "2d",
    "couple days": "2d",
    "couple day": "2d",
    "7d": "7d",
    "7days": "7d",
    "week": "7d",
    "1w": "7d",
    "14d": "14d",
    "14days": "14d",
    "two weeks": "14d",
    "2w": "14d",
    "30d": "30d",
    "30days": "30d",
    "month": "30d",
    "1m": "30d",
    "60d": "60d",
    "60days": "60d",
    "2m": "60d",
    "two months": "60d",
    "couple months": "60d",
    "365d": "365d",
    "365days": "365d",
    "year": "365d",
    "1y": "365d",
    "all": "all",
    "none": "all",
    "no limit": "all",
}


def _normalize_message_window(value: str) -> str:
    cleaned = value.strip().lower().replace("-", " ").replace("_", " ")
    cleaned = " ".join(cleaned.split())
    if cleaned in _MESSAGE_WINDOW_ALIASES:
        return _MESSAGE_WINDOW_ALIASES[cleaned]
    valid = ", ".join(key for key, _ in _MESSAGE_WINDOW_CHOICES)
    raise typer.BadParameter(
        f"Invalid message window: {value!r}. Use one of: {valid}."
    )


def _pick_message_window(default_key: str = "7d") -> Optional[str]:
    try:
        while True:
            typer.echo("\nMessage window:")
            for idx, (key, label) in enumerate(_MESSAGE_WINDOW_CHOICES, start=1):
                default_marker = " (default)" if key == default_key else ""
                typer.echo(f"  [{idx}] {label}{default_marker}")
            choice = input("> ").strip()
            if not choice:
                return default_key
            if choice.isdigit():
                index = int(choice)
                if 1 <= index <= len(_MESSAGE_WINDOW_CHOICES):
                    return _MESSAGE_WINDOW_CHOICES[index - 1][0]
            try:
                return _normalize_message_window(choice)
            except typer.BadParameter:
                typer.echo("Invalid choice. Try again.")
    except (KeyboardInterrupt, EOFError):
        return None


def _message_window_since_ms(window_key: str) -> Optional[int]:
    if window_key == "all":
        return None
    now = datetime.datetime.now()
    if window_key == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window_key == "2d":
        since = now - datetime.timedelta(days=2)
    elif window_key == "7d":
        since = now - datetime.timedelta(days=7)
    elif window_key == "14d":
        since = now - datetime.timedelta(days=14)
    elif window_key == "30d":
        since = now - datetime.timedelta(days=30)
    elif window_key == "60d":
        since = now - datetime.timedelta(days=60)
    elif window_key == "365d":
        since = now - datetime.timedelta(days=365)
    else:
        raise typer.BadParameter(f"Invalid message window key: {window_key!r}")
    return int(since.timestamp() * 1000)


def _last_message_from_others(messages: Iterable[BeeperMessage]) -> Optional[str]:
    last_id: Optional[str] = None
    for msg in messages:
        if not msg.is_sender:
            last_id = msg.message_id
    return last_id


_REPLY_GUIDANCE_OPTIONS: list[tuple[str, str]] = [
    ("close", "Close the loop (no back-and-forth)"),
    ("going", "Keep it going (same energy)"),
    ("rekindle", "Rekindle the conversation"),
    ("decline", "Soft decline (not obvious)"),
    ("schedule", "Schedule something"),
    ("todo", "Acknowledge + add to todo"),
    ("analyse", "Analyse best next steps (no reply)"),
]


def _print_styled_section(title: str, content: str, color: str) -> None:
    """Print a visually distinct output section with a colored border."""
    bar = typer.style("━" * 50, fg=color)
    typer.echo(bar)
    typer.echo(typer.style(f"  {title}", fg=color, bold=True))
    typer.echo(bar)
    typer.echo(content)
    typer.echo(bar)


def _get_reply_guidance(messages: list[BeeperMessage], preview_count: int = 10) -> tuple[str, str]:
    """Show recent messages and prompt for reply guidance.

    Returns (guidance_key, custom_text). guidance_key is one of the predefined
    keys, 'custom' for free-text input, or '' if skipped.
    """
    typer.echo("\n--- Recent messages ---")

    recent = messages[-preview_count:] if len(messages) > preview_count else messages
    for msg in recent:
        speaker = "You" if msg.is_sender else msg.sender_name
        text = msg.text.strip()
        if not text:
            continue
        dt = datetime.datetime.fromtimestamp(msg.timestamp_ms / 1000)
        ts = dt.strftime("%H:%M")
        typer.echo(f"[{ts}] {speaker}: {text}")

    typer.echo("\n--- Reply guidance ---")
    for idx, (_, label) in enumerate(_REPLY_GUIDANCE_OPTIONS, start=1):
        typer.echo(f"  [{idx}] {label}")
    typer.echo("  Or type custom guidance, or press Enter to skip.")

    try:
        choice = input("> ").strip()
    except (KeyboardInterrupt, EOFError):
        return ("", "")

    if not choice:
        return ("", "")
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(_REPLY_GUIDANCE_OPTIONS):
            return (_REPLY_GUIDANCE_OPTIONS[index - 1][0], "")
    return ("custom", choice)


# --- Proxy auto-start ---

# Windows host IP from WSL (detected dynamically) and candidate ports
def _detect_wsl_host_ip() -> str:
    """Detect the Windows host IP from WSL. Falls back to default gateway."""
    try:
        result = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True
        ).strip()
        # e.g. "default via 172.20.144.1 dev eth0"
        parts = result.split()
        if "via" in parts:
            ip = parts[parts.index("via") + 1]
            logger.debug("Detected WSL host IP: %s", ip)
            return ip
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError, IndexError):
        logger.debug("Could not detect WSL host IP from default route")
    logger.debug("Using fallback WSL host IP: 172.28.96.1")
    return "172.28.96.1"  # last-resort fallback


_WSL_HOST_IP = _detect_wsl_host_ip()
_PROXY_PORTS = [23374, 23373]
_PROXY_MODULE_PATH = os.path.join(os.path.dirname(__file__), "wsl_proxy.py")


def _probe_proxy_port() -> Optional[int]:
    """Try each candidate port on the Windows host. Return the first that responds with a valid HTTP response."""
    logger.debug("Probing proxy ports %s on host %s", _PROXY_PORTS, _WSL_HOST_IP)
    for port in _PROXY_PORTS:
        try:
            logger.debug("Trying port %d ...", port)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((_WSL_HOST_IP, port))
            # Send a lightweight HTTP request to verify the proxy's backend is alive
            sock.sendall(b"GET /v1/accounts HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = sock.recv(128)
            sock.close()
            if response and b"HTTP/" in response:
                logger.debug("Port %d responded with valid HTTP", port)
                return port
            logger.debug("Port %d connected but gave invalid response: %r", port, response[:64] if response else b"")
        except (ConnectionRefusedError, OSError, socket.timeout) as exc:
            logger.debug("Port %d: %s", port, exc)
            continue
    logger.debug("No proxy port responded")
    return None


def _start_proxy_via_powershell() -> bool:
    """Launch the WSL proxy on Windows via PowerShell and wait for it to come up."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        logger.debug("powershell.exe not found on PATH")
        return False

    # Convert WSL path to Windows path
    try:
        win_path = subprocess.check_output(
            ["wslpath", "-w", _PROXY_MODULE_PATH], text=True
        ).strip()
        logger.debug("Windows proxy path: %s", win_path)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.debug("wslpath conversion failed: %s", exc)
        return False

    # Launch proxy in a hidden PowerShell window so it persists after we exit.
    # Use a log file so we can surface errors when verbose mode is on.
    _proxy_log = os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "beeper-proxy-launch.log"
    )
    cmd = (
        f'Start-Process python'
        f' -ArgumentList \'"{win_path}"\''
        f' -WindowStyle Hidden'
        f' -RedirectStandardOutput \'"{_proxy_log}.out"\''
        f' -RedirectStandardError \'"{_proxy_log}.err"\''
    )
    logger.debug("Running: powershell -Command %s", cmd)
    try:
        proc = subprocess.Popen(
            [powershell, "-Command", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ps_out, ps_err = proc.communicate(timeout=10)
        if ps_out.strip():
            logger.debug("PowerShell stdout: %s", ps_out.decode(errors="replace").strip())
        if ps_err.strip():
            logger.debug("PowerShell stderr: %s", ps_err.decode(errors="replace").strip())
        if proc.returncode and proc.returncode != 0:
            logger.debug("PowerShell exited with code %d", proc.returncode)
    except subprocess.TimeoutExpired:
        logger.debug("PowerShell command timed out after 10s")
        proc.kill()  # proc is always bound here — TimeoutExpired can only follow Popen success
    except OSError as exc:
        logger.debug("Failed to launch PowerShell: %s", exc)
        return False

    # Wait for the proxy to come up (up to 8 seconds)
    logger.debug("Waiting up to 8s for proxy to start ...")
    for attempt in range(16):
        time.sleep(0.5)
        if _probe_proxy_port() is not None:
            logger.debug("Proxy came up after %.1fs", (attempt + 1) * 0.5)
            return True
    logger.debug("Proxy did not come up within 8s")
    # Try to read the proxy's stderr log for clues
    for suffix in (".err", ".out"):
        log_path = _proxy_log + suffix
        try:
            with open(log_path) as f:
                content = f.read().strip()
            if content:
                logger.debug("Proxy log (%s): %s", suffix, content)
        except OSError:
            pass
    return False


def _ensure_proxy() -> str:
    """Return BEEPER_BASE_URL with an active proxy port, starting the proxy if needed."""
    logger.debug("Ensuring proxy is available (host=%s, ports=%s)", _WSL_HOST_IP, _PROXY_PORTS)
    port = _probe_proxy_port()
    if port:
        return f"http://{_WSL_HOST_IP}:{port}"

    typer.echo("[*] Proxy not running — starting via PowerShell ...")
    if _start_proxy_via_powershell():
        port = _probe_proxy_port()
        if port:
            typer.echo(f"[+] Proxy started on port {port}")
            return f"http://{_WSL_HOST_IP}:{port}"

    typer.echo("[!] Could not start proxy. Start it manually in PowerShell:")
    typer.echo(f"    python {_PROXY_MODULE_PATH}")
    raise typer.Exit(code=1)


@app.command()
def triage(
    model: Optional[str] = typer.Option(
        None, "--model", help="OpenRouter model override"
    ),
    max_chats: int = typer.Option(2000, "--max-chats", min=1),
    max_messages: Optional[int] = typer.Option(
        None,
        "--max-messages",
        min=1,
        help="Optional safety cap for fetched messages",
    ),
    message_window: Optional[str] = typer.Option(
        None,
        "--message-window",
        help="Time window for messages (today, 2d, 7d, 14d, 30d, 60d, 365d, all)",
    ),
    include_muted: bool = typer.Option(False, "--include-muted"),
    needs_reply_only: bool = typer.Option(
        False, "--needs-reply-only", help="Only show chats where you're not the last sender"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_llm: bool = typer.Option(False, "--no-llm"),
    refresh_chats: bool = typer.Option(
        False, "--refresh-chats", help="Force refresh chat cache (bypasses 6-hour TTL)"
    ),
    agent: bool = typer.Option(
        False, "--agent", help="Non-interactive agent mode: JSON output, no prompts"
    ),
    chat_id: Optional[str] = typer.Option(
        None, "--chat-id", help="Select chat by ID (agent mode: required to proceed past chat list)"
    ),
    action: Optional[str] = typer.Option(
        None, "--action", help="Action to take: reply, copy, export (skips interactive prompt)"
    ),
    guidance: Optional[str] = typer.Option(
        None, "--guidance", help="Reply guidance: preset key (close/going/rekindle/decline/schedule/todo/analyse) or free text"
    ),
    no_edit: bool = typer.Option(
        False, "--no-edit", help="Skip editor step and use draft as-is"
    ),
    draft_override: Optional[str] = typer.Option(
        None, "--draft", help="Override LLM output with this text (implies --no-edit)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show debug output for proxy detection, API calls, etc."
    ),
) -> None:
    """Triage Beeper chats and draft a reply."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    load_dotenv()

    if not agent:
        _ensure_fzf()

    access_token = _require_env("BEEPER_ACCESS_TOKEN")
    default_model = os.getenv("OPENROUTER_MODEL", "")
    editor = os.getenv("EDITOR", "")

    base_url = os.getenv("BEEPER_BASE_URL")
    if not base_url:
        base_url = _ensure_proxy()
    else:
        # Even with a configured URL, verify the proxy is reachable; if not, auto-detect port
        try:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((parsed.hostname, parsed.port))
            sock.close()
        except (ConnectionRefusedError, OSError, socket.timeout):
            typer.echo(f"[!] Configured proxy at {base_url} not reachable — auto-detecting ...")
            base_url = _ensure_proxy()

    try:
        client = BeeperClient(access_token=access_token, base_url=base_url)
    except BeeperSDKError as exc:
        logger.exception("Failed to initialize Beeper client")
        raise typer.BadParameter(str(exc)) from exc

    try:
        account_map = client.list_accounts()
    except BeeperSDKError as exc:
        logger.exception("Failed to list accounts")
        raise typer.BadParameter(str(exc)) from exc

    try:
        chats = client.list_chats(use_cache=not refresh_chats)
    except BeeperSDKError as exc:
        logger.exception("Failed to list chats")
        raise typer.BadParameter(str(exc)) from exc

    # Populate network names and account labels from account mapping (works for both cached and fresh data)
    for chat in chats:
        if chat.account_id and chat.account_id in account_map:
            network_type, account_label = account_map[chat.account_id]
            chat.network_type = network_type
            chat.account_label = account_label

    filtered = []
    for chat in chats:
        if not include_muted and chat.is_muted:
            continue
        if needs_reply_only and not _needs_reply(chat.preview_is_sender):
            continue
        filtered.append(chat)

    # Truncate to max_chats (Beeper already returns chats sorted by last activity)
    filtered = filtered[:max_chats]

    if not filtered:
        if agent:
            typer.echo(json.dumps({"chats": []}))
        else:
            typer.echo("No chats need reply.")
        raise typer.Exit(code=0)

    # In agent mode without --chat-id: dump chat list as JSON and exit
    if agent and not chat_id:
        chat_list = [
            {
                "chat_id": c.chat_id,
                "title": c.title,
                "unread_count": c.unread_count,
                "last_activity_ms": c.last_activity_ms,
                "preview_is_sender": c.preview_is_sender,
                "is_muted": c.is_muted,
                "network_type": c.network_type,
                "account_label": c.account_label,
            }
            for c in filtered
        ]
        typer.echo(json.dumps({"chats": chat_list}))
        raise typer.Exit(code=0)

    if chat_id:
        selection = chat_id
        if not any(c.chat_id == chat_id for c in filtered):
            if agent:
                typer.echo(json.dumps({"error": f"chat_id not found: {chat_id}"}))
            else:
                typer.echo(f"Chat ID not found: {chat_id}")
            raise typer.Exit(code=1)
    else:
        selection = _pick_chat_fzf(filtered)
        if not selection:
            typer.echo("No chat selected.")
            raise typer.Exit(code=0)
    chat_title = next((chat.title for chat in filtered if chat.chat_id == selection), "")

    if message_window is None:
        if agent:
            window_key = "7d"
        else:
            window_key = _pick_message_window()
            if window_key is None:
                typer.echo("Cancelled.")
                raise typer.Exit(code=0)
    else:
        window_key = _normalize_message_window(message_window)
    since_ms = _message_window_since_ms(window_key)

    try:
        messages = client.list_messages(
            selection, limit=max_messages, since_ms=since_ms
        )
    except BeeperSDKError as exc:
        logger.exception("Failed to list messages")
        raise typer.BadParameter(str(exc)) from exc

    messages_sorted = sorted(messages, key=lambda m: m.timestamp_ms)
    transcript = _format_transcript(messages_sorted)
    if not transcript:
        if since_ms is not None:
            typer.echo("No messages found in the selected time window.")
        else:
            typer.echo("No message content available.")
        raise typer.Exit(code=0)

    reply_to_id = _last_message_from_others(messages_sorted)

    if action is not None:
        resolved_action = action.lower()
        if resolved_action not in ("reply", "copy", "export"):
            if agent:
                typer.echo(json.dumps({"error": f"Invalid action: {action}. Use reply, copy, or export."}))
            else:
                typer.echo(f"Invalid action: {action}. Use reply, copy, or export.")
            raise typer.Exit(code=1)
    elif agent:
        typer.echo(json.dumps({"error": "Agent mode requires --action (reply, copy, or export)."}))
        raise typer.Exit(code=1)
    else:
        resolved_action = _pick_action()
        if resolved_action is None:
            typer.echo("Cancelled.")
            raise typer.Exit(code=0)

    if resolved_action == "copy":
        clipboard_cmd = _detect_clipboard_cmd()
        if clipboard_cmd is None:
            msg = "No clipboard tool found. Install one of: clip.exe (WSL), wl-copy, xclip, xsel"
            if agent:
                typer.echo(json.dumps({"error": msg}))
            else:
                typer.echo(msg)
            raise typer.Exit(code=1)
        timestamped = _format_transcript_with_timestamps(messages_sorted)
        try:
            _copy_to_clipboard(timestamped, clipboard_cmd)
        except subprocess.CalledProcessError as exc:
            raise typer.BadParameter(f"Clipboard copy failed: {exc}") from exc
        if agent:
            typer.echo(json.dumps({"status": "copied", "chat_id": selection}))
        else:
            typer.echo("Transcript copied to clipboard.")
        raise typer.Exit(code=0)

    if resolved_action == "export":
        timestamped = _format_transcript_with_timestamps(messages_sorted)
        export_path = _export_transcript(timestamped, chat_title)
        if agent:
            typer.echo(json.dumps({"status": "exported", "chat_id": selection, "path": export_path}))
        else:
            typer.echo(f"Exported transcript to: {export_path}")
        raise typer.Exit(code=0)

    # resolved_action == "reply" — existing flow continues
    if guidance is not None:
        preset_keys = [key for key, _ in _REPLY_GUIDANCE_OPTIONS]
        if guidance in preset_keys:
            guidance_key, custom_guidance = guidance, ""
        else:
            guidance_key, custom_guidance = "custom", guidance
    elif agent:
        guidance_key, custom_guidance = "", ""
    else:
        guidance_key, custom_guidance = _get_reply_guidance(messages_sorted)

    if not no_llm:
        if not model:
            model = default_model
        if not model:
            raise typer.BadParameter("OPENROUTER_MODEL or --model is required.")
        _require_env("OPENROUTER_API_KEY")

    # --- Analyse: LLM-only, no reply ---
    if guidance_key == "analyse":
        if no_llm:
            if agent:
                typer.echo(json.dumps({"error": "LLM is disabled (--no-llm). Cannot analyse."}))
            else:
                typer.echo("LLM is disabled (--no-llm). Cannot analyse.")
            raise typer.Exit(code=0)
        openrouter = OpenRouterClient(api_key=_require_env("OPENROUTER_API_KEY"))
        try:
            analysis = openrouter.create_chat_completion(
                model=model, messages=build_analyse_prompt(transcript)
            )
        except OpenRouterError as exc:
            logger.exception("Failed to create analysis via OpenRouter")
            raise typer.BadParameter(str(exc)) from exc
        if agent:
            typer.echo(json.dumps({"status": "analysis", "chat_id": selection, "analysis": analysis}))
        else:
            _print_styled_section("NEXT STEPS ANALYSIS", analysis, typer.colors.CYAN)
        raise typer.Exit(code=0)

    # --- Todo: LLM generates reply + todo item ---
    todo_text: str = ""
    if draft_override is not None:
        draft = draft_override
    elif guidance_key == "todo":
        if not no_llm:
            openrouter = OpenRouterClient(api_key=_require_env("OPENROUTER_API_KEY"))
            try:
                todo_output = openrouter.create_chat_completion(
                    model=model, messages=build_todo_prompt(transcript)
                )
            except OpenRouterError as exc:
                logger.exception("Failed to create todo via OpenRouter")
                raise typer.BadParameter(str(exc)) from exc
            parts = todo_output.split("---", 1)
            draft = parts[0].strip()
            todo_text = parts[1].strip() if len(parts) > 1 else ""
        else:
            draft = ""
    elif no_llm:
        draft = ""
    else:
        openrouter = OpenRouterClient(api_key=_require_env("OPENROUTER_API_KEY"))
        try:
            draft = openrouter.create_chat_completion(
                model=model,
                messages=build_prompt(transcript, guidance_key=guidance_key, user_guidance=custom_guidance),
            )
        except OpenRouterError as exc:
            logger.exception("Failed to create chat completion via OpenRouter")
            raise typer.BadParameter(str(exc)) from exc

    if no_edit or agent or draft_override is not None:
        edited = draft
    else:
        try:
            edited = edit_text(draft, editor=editor)
        except EditorError as exc:
            logger.exception("Editor error")
            raise typer.BadParameter(str(exc)) from exc

    if not edited:
        if agent:
            typer.echo(json.dumps({"error": "Empty message, aborting."}))
        else:
            typer.echo("Empty message, aborting.")
        raise typer.Exit(code=0)

    if agent:
        # In agent mode, skip confirmation unless dry_run
        if dry_run:
            result: dict = {"status": "dry_run", "chat_id": selection, "draft": edited}
            if todo_text:
                result["todo"] = todo_text
            typer.echo(json.dumps(result))
            raise typer.Exit(code=0)
        # Fall through to send
    else:
        if todo_text:
            _print_styled_section("TODO ITEM", todo_text, typer.colors.YELLOW)

        typer.echo("\nDraft reply:\n")
        typer.echo(edited)

        confirm = typer.confirm("\nSend this message?", default=False)
        if not confirm:
            typer.echo("Cancelled.")
            raise typer.Exit(code=0)

    if dry_run:
        if agent:
            result = {"status": "dry_run", "chat_id": selection, "draft": edited}
            if todo_text:
                result["todo"] = todo_text
            typer.echo(json.dumps(result))
        else:
            typer.echo("Dry run enabled. Not sending.")
        raise typer.Exit(code=0)

    try:
        client.send_message(selection, edited, reply_to_message_id=reply_to_id)
    except BeeperSDKError as exc:
        logger.exception("Failed to send message")
        raise typer.BadParameter(str(exc)) from exc

    if agent:
        result = {"status": "sent", "chat_id": selection, "message": edited}
        if todo_text:
            result["todo"] = todo_text
        typer.echo(json.dumps(result))
    else:
        typer.echo("Message sent.")


@app.command("new-chat")
def new_chat(
    phone: str = typer.Option(
        ..., "--phone", help="Phone number in E.164 format (e.g. +441234567890)"
    ),
    network: Optional[str] = typer.Option(
        None, "--network", help="Network to use (e.g. whatsapp, signal, googlechat). If omitted, auto-selects or prompts."
    ),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Message to send (if omitted, just creates the chat)"
    ),
    agent: bool = typer.Option(
        False, "--agent", help="Non-interactive agent mode: JSON output, no prompts"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show debug output for proxy detection, API calls, etc."
    ),
) -> None:
    """Start a new chat with a phone number on a specific network."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    load_dotenv()

    access_token = _require_env("BEEPER_ACCESS_TOKEN")

    base_url = os.getenv("BEEPER_BASE_URL")
    if not base_url:
        base_url = _ensure_proxy()
    else:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((parsed.hostname, parsed.port))
            sock.close()
        except (ConnectionRefusedError, OSError, socket.timeout):
            if not agent:
                typer.echo(f"[!] Configured proxy at {base_url} not reachable — auto-detecting ...")
            base_url = _ensure_proxy()

    try:
        client = BeeperClient(access_token=access_token, base_url=base_url)
    except BeeperSDKError as exc:
        if agent:
            typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=1)

    # List accounts and find the right one for the requested network
    try:
        account_map = client.list_accounts()
    except BeeperSDKError as exc:
        if agent:
            typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=1)

    # Build list of accounts with their network types
    accounts_by_network: dict[str, list[tuple[str, str]]] = {}
    for acct_id, (net_type, label) in account_map.items():
        accounts_by_network.setdefault(net_type, []).append((acct_id, label))

    if network:
        # Case-insensitive network matching
        network_lower = network.lower()
        matched = None
        for net_key, accts in accounts_by_network.items():
            if net_key.lower() == network_lower:
                matched = accts
                break
        if not matched:
            available = sorted(accounts_by_network.keys())
            err = f"No account found for network '{network}'. Available: {', '.join(available)}"
            if agent:
                typer.echo(json.dumps({"error": err, "available_networks": available}))
            else:
                typer.echo(err)
            raise typer.Exit(code=1)
        account_id, account_label = matched[0]
    elif agent:
        # Agent mode requires explicit network
        available = sorted(accounts_by_network.keys())
        typer.echo(json.dumps({"error": "Agent mode requires --network", "available_networks": available}))
        raise typer.Exit(code=1)
    else:
        # Interactive: let user pick via fzf
        _ensure_fzf()
        fzf_lines = []
        for net_type, accts in sorted(accounts_by_network.items()):
            for acct_id, label in accts:
                fzf_lines.append(f"{acct_id}\t{net_type} • {label}")
        result = subprocess.run(
            ["fzf", "--ansi", "--with-nth", "2..", "--prompt", "Network> ", "--tiebreak=index"],
            input="\n".join(fzf_lines),
            text=True,
            stdout=subprocess.PIPE,
        )
        if result.returncode != 0:
            typer.echo("Cancelled.")
            raise typer.Exit(code=0)
        line = result.stdout.strip()
        if not line:
            typer.echo("Cancelled.")
            raise typer.Exit(code=0)
        account_id = line.split("\t", 1)[0]
        account_label = account_map[account_id][1]

    # Search for the contact on that account
    if not agent:
        typer.echo(f"Searching for {phone} on {account_map[account_id][0]}...")

    try:
        contacts = client.search_contacts(account_id, query=phone)
    except BeeperSDKError as exc:
        if agent:
            typer.echo(json.dumps({"error": str(exc)}))
        else:
            typer.echo(f"Contact search failed: {exc}")
        raise typer.Exit(code=1)

    if not contacts:
        err = f"No contact found for '{phone}' on {account_map[account_id][0]}"
        if agent:
            typer.echo(json.dumps({"error": err}))
        else:
            typer.echo(err)
        raise typer.Exit(code=1)

    # Pick the contact — prefer one that can be messaged
    contact = None
    for c in contacts:
        if not c.get("cannot_message"):
            contact = c
            break
    if contact is None:
        contact = contacts[0]

    if contact.get("cannot_message"):
        err = f"Contact found but cannot be messaged: {contact.get('full_name') or contact['id']}"
        if agent:
            typer.echo(json.dumps({"error": err, "contact": contact}))
        else:
            typer.echo(err)
        raise typer.Exit(code=1)

    contact_name = contact.get("full_name") or contact.get("username") or contact["id"]

    if not agent:
        typer.echo(f"Found: {contact_name} ({contact.get('phone_number', 'no phone')})")

    if dry_run:
        result_data = {
            "status": "dry_run",
            "contact": contact,
            "account_id": account_id,
            "network": account_map[account_id][0],
        }
        if message:
            result_data["message"] = message
        if agent:
            typer.echo(json.dumps(result_data))
        else:
            typer.echo(f"Dry run — would create chat with {contact_name} and send: {message or '(no message)'}")
        raise typer.Exit(code=0)

    # Create the chat (message_text only used by some platforms to initialise)
    try:
        chat_id = client.create_chat(
            account_id=account_id,
            participant_ids=[contact["id"]],
            chat_type="single",
        )
    except BeeperSDKError as exc:
        if agent:
            typer.echo(json.dumps({"error": str(exc)}))
        else:
            typer.echo(f"Failed to create chat: {exc}")
        raise typer.Exit(code=1)

    # Actually send the message via the messages API
    messages_sent: list[str] = []
    if message:
        # Only split for UK landline/non-geographic numbers (02x, 03x, 08x)
        # which silently drop MMS.  Mobile numbers (07x) handle long SMS fine.
        if _needs_sms_split(phone):
            chunks = _split_sms(message)
        else:
            chunks = [message]
        for chunk in chunks:
            try:
                client.send_message(chat_id=chat_id, text=chunk, reply_to_message_id=None)
                messages_sent.append(chunk)
            except BeeperSDKError as exc:
                if agent:
                    typer.echo(json.dumps({
                        "error": f"Chat created ({chat_id}) but message send failed: {exc}",
                        "chat_id": chat_id,
                        "messages_sent": messages_sent,
                    }))
                else:
                    typer.echo(f"Chat created but message failed: {exc}")
                raise typer.Exit(code=1)

    result_data = {
        "status": "created",
        "chat_id": chat_id,
        "contact": contact,
        "network": account_map[account_id][0],
    }
    if messages_sent:
        result_data["message_sent"] = message
        result_data["chunks"] = len(messages_sent)

    if agent:
        typer.echo(json.dumps(result_data))
    else:
        typer.echo(f"Chat created: {chat_id}")
        if messages_sent:
            typer.echo(f"Message sent to {contact_name} ({len(messages_sent)} part(s)): {message}")
        else:
            typer.echo(f"Chat with {contact_name} is ready. Use --chat-id {chat_id} to send messages.")


if __name__ == "__main__":
    app()
