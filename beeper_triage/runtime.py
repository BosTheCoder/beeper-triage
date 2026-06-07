"""Connection bootstrap: env, WSL proxy detection/launch, SDK client construction."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

import typer

from .beeper_client import BeeperClient

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise typer.BadParameter(f"Missing env var: {name}")
    return value


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
# Unified fixed proxy port — wsl_proxy.py listens on 0.0.0.0:23399 and detects
# Beeper's actual (drifting) loopback port itself, so the CLI and the MCP both
# reach it at a single stable port regardless of Beeper version.
_PROXY_PORTS = [23399]
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


def _resolve_base_url(*, agent: bool) -> str:
    """Return a reachable Beeper API base URL.

    Uses BEEPER_BASE_URL if set and reachable; otherwise (or if the configured
    URL refuses a connection) auto-detects/starts the WSL proxy. The
    'not reachable' info line is suppressed in agent mode.
    """
    base_url = os.getenv("BEEPER_BASE_URL")
    if not base_url:
        return _ensure_proxy()
    try:
        parsed = urlparse(base_url)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect((parsed.hostname, parsed.port))
        finally:
            sock.close()
    except (ConnectionRefusedError, OSError, socket.timeout):
        if not agent:
            typer.echo(f"[!] Configured proxy at {base_url} not reachable — auto-detecting ...")
        base_url = _ensure_proxy()
    return base_url


def _build_client(access_token: str, *, agent: bool) -> "BeeperClient":
    """Resolve the base URL and construct a BeeperClient.

    Raises BeeperSDKError if the SDK client cannot be constructed; callers
    keep their own error-handling UX.
    """
    base_url = _resolve_base_url(agent=agent)
    return BeeperClient(access_token=access_token, base_url=base_url)
