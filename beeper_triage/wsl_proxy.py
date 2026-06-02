#!/usr/bin/env python3
"""
Beeper WSL Proxy — runs on Windows.

Bridges WSL (NAT mode) to Beeper Desktop's loopback-only API:
- Listens on a FIXED port 0.0.0.0:23399 so the WSL-side URL never changes,
  even when Beeper's own port flips across updates.
- Forwards each connection to whichever loopback Beeper is actually on,
  detected PER CONNECTION across {127.0.0.1, [::1]} x {23374, 23373}. Doing
  the detection lazily (not once at startup) means the proxy can be launched
  at logon before Beeper is up, survives Beeper restarts, and copes with the
  IPv4/IPv6 + port drift seen across machines and Beeper versions.

Consumers (both reach it at http://<wsl-gateway>:23399):
- the Beeper MCP server registered in ~/.claude.json
- the beeper-triage CLI (_PROXY_PORTS = [23399] in cli.py)

Usage (on Windows):  python wsl_proxy.py [listen_port]
"""
import socket
import sys
import threading

# Fixed WSL-facing listen port (override with argv[1] if ever needed).
DEFAULT_LISTEN_PORT = 23399

# Candidate Beeper backends. Beeper binds to a loopback only; the family (IPv4
# vs IPv6) and port both vary across versions, so we scan the cross product.
# IPv4 first — it's the common case and, crucially, a CLOSED loopback port on
# Windows *times out* (it doesn't refuse fast), so probing dead candidates is
# expensive. We keep the per-candidate timeout short and cache the last-good
# target so steady-state connections skip the scan entirely.
BEEPER_PORTS = [23374, 23373]
BEEPER_HOSTS = [
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET6, "::1"),
]
CONNECT_TIMEOUT = 0.5  # seconds per candidate during a scan

_cache_lock = threading.Lock()
_cached_target = None  # (family, host, port) of the last backend that worked


def _try_connect(family, host, port):
    """Attempt one backend; return a connected socket or None."""
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT)
        s.connect((host, port))
        s.settimeout(None)
        return s
    except OSError:
        try:
            s.close()
        except (OSError, UnboundLocalError):
            pass
        return None


def connect_backend():
    """Return (socket, host, port) connected to Beeper, or (None, None, None).

    Tries the cached last-good target first (fast path), then scans the full
    {host} x {port} cross product, caching whatever works.
    """
    global _cached_target
    with _cache_lock:
        cached = _cached_target
    if cached:
        s = _try_connect(*cached)
        if s is not None:
            return s, cached[1], cached[2]

    for port in BEEPER_PORTS:
        for family, host in BEEPER_HOSTS:
            cand = (family, host, port)
            if cand == cached:
                continue  # already tried above
            s = _try_connect(*cand)
            if s is not None:
                with _cache_lock:
                    _cached_target = cand
                return s, host, port
    return None, None, None


def forward(src, dst):
    """Pump bytes one direction until either side closes."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


def handle_client(client, addr):
    remote, host, port = connect_backend()
    if remote is None:
        print(f"[!] {addr}: Beeper not reachable on any of "
              f"{[(h, p) for p in BEEPER_PORTS for _, h in BEEPER_HOSTS]}",
              file=sys.stderr)
        try:
            client.close()
        except OSError:
            pass
        return

    print(f"[+] {addr} <-> [{host}]:{port}")
    threading.Thread(target=forward, args=(client, remote), daemon=True).start()
    threading.Thread(target=forward, args=(remote, client), daemon=True).start()


def main():
    listen_port = DEFAULT_LISTEN_PORT
    if len(sys.argv) > 1:
        try:
            listen_port = int(sys.argv[1])
        except ValueError:
            print(f"[!] invalid listen port: {sys.argv[1]!r}", file=sys.stderr)
            sys.exit(2)

    print("=" * 60)
    print("Beeper WSL Proxy")
    print(f"Listening on:  0.0.0.0:{listen_port} (reachable from WSL)")
    print(f"Forwarding to: Beeper loopback, detected per connection "
          f"({BEEPER_HOSTS} x {BEEPER_PORTS})")
    print("=" * 60)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Make the first binder authoritative. On Windows, SO_REUSEADDR would let a
    # SECOND process also bind 23399 (a phantom duplicate listener) — so if the
    # logon-started proxy is up and the CLI's auto-start fires, they'd race. With
    # SO_EXCLUSIVEADDRUSE the later bind fails and that instance exits below,
    # leaving the logon proxy the single owner. Windows-only option; elsewhere
    # this is a no-op (the proxy only ever runs on Windows).
    _exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if _exclusive is not None:
        srv.setsockopt(socket.SOL_SOCKET, _exclusive, 1)
    try:
        srv.bind(("0.0.0.0", listen_port))
        srv.listen(8)
    except OSError as e:
        print(f"[!] Failed to bind 0.0.0.0:{listen_port}: {e}", file=sys.stderr)
        print(f"[!] Is another proxy instance already running?", file=sys.stderr)
        sys.exit(1)
    print("[+] Proxy started\n")

    try:
        while True:
            client, addr = srv.accept()
            threading.Thread(
                target=handle_client, args=(client, addr), daemon=True
            ).start()
    except KeyboardInterrupt:
        print("\n[+] Shutting down")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
