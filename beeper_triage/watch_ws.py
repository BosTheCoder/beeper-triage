"""Push transport for `watch` — Beeper Desktop's experimental event socket.

Spec §10.2. Beeper advertises a WebSocket at ``GET /v1/info`` →
``endpoints.ws_events``; subscribing yields ``message.upserted`` frames that
carry the **whole** message (text, isSender, senderID, timestamp), not just an
invalidation. That is everything the state machine needs, at millisecond
latency instead of up-to-poll_seconds.

This module is only the transport: connect, subscribe, reconnect, and yield
normalised ``WatchMessage`` records. It makes no decisions — those stay in
``watch.observe`` — so the poll and the socket cannot drift apart.

**The socket does not replace the poll; it demotes it.** Three things keep the
reconcile pass necessary:

* The interface is documented experimental and can change under us. When it
  does, the poll is what keeps watches working rather than silently dead.
* A dropped socket loses events with no way to notice from inside. Frames carry
  a ``seq``, but a gap only tells you *that* you missed something, not what.
* Frames carry no chat title, and ``title_match`` needs one, so something has to
  keep a chatID → title map fresh.

Separate module from ``watch.py`` so the engine keeps its stdlib-only property:
this is the one piece that needs a third-party dependency (``websockets``), and
a caller that only polls never imports it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import urlparse, urlunparse

from .watch import WatchMessage

logger = logging.getLogger(__name__)

# Reconnect backoff: quick first retry (a desktop app restart is seconds), then
# ease off so a genuinely-down Beeper is not hammered.
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
BACKOFF_FACTOR = 2.0

# How long recv() blocks before looping to re-check the stop flag. Keeps
# shutdown responsive without polling in a tight loop.
RECV_TIMEOUT = 5.0


class WatchSocketError(RuntimeError):
    """The event socket could not be reached or negotiated."""


def ws_url(base_url: str) -> str:
    """The event-socket URL for an API base URL.

    ``/v1/info`` reports this as an ``http://`` URL even though it is a socket,
    so the scheme is swapped here rather than trusted. Works unchanged through
    the WSL proxy, which is raw TCP and passes the Upgrade straight through.
    """
    parts = urlparse(base_url)
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    return urlunparse((scheme, parts.netloc, "/v1/ws", "", "", ""))


def _messages_from_frame(frame: Mapping[str, Any]) -> list[WatchMessage]:
    """Normalise one socket frame into zero or more messages.

    Only ``message.upserted`` carries content. ``chat.upserted`` is an
    invalidation with no message in it, and deletions are not something a watch
    reports, so both are dropped here rather than half-handled downstream.
    """
    if frame.get("type") != "message.upserted":
        return []
    chat_id = str(frame.get("chatID") or "")
    entries = frame.get("entries") or []
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        message = WatchMessage.from_ws_entry(entry, chat_id)
        if message.chat_id and message.ts:
            out.append(message)
    return out


class WatchSocket:
    """Subscribing client over Beeper's event socket, with reconnect.

    ``listen`` is a blocking generator of ``WatchMessage``. It never raises for
    a connection problem: it reports via ``on_error`` and retries with backoff,
    because a monitor that dies quietly is worse than one reporting nothing.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        chat_ids: Optional[list[str]] = None,
        on_error: Callable[[str], None] = lambda _m: None,
        on_connect: Callable[[], None] = lambda: None,
        connect_fn: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = ws_url(base_url)
        self._token = token
        # "*" means every chat. Subscribing per-chat would need re-subscribing
        # whenever a config changes or a title_match starts matching something
        # new, and the filtering is a dict lookup either way.
        self._chat_ids = chat_ids or ["*"]
        self._on_error = on_error
        self._on_connect = on_connect
        self._connect_fn = connect_fn
        self._sleep = sleep
        self._stop = threading.Event()

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(
                self._url, additional_headers={"Authorization": f"Bearer {self._token}"}
            )
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise WatchSocketError(
                "The push transport needs the 'websockets' package "
                "(pip install websockets). Fall back to polling without it."
            ) from exc
        return connect(
            self._url,
            additional_headers={"Authorization": f"Bearer {self._token}"},
            open_timeout=10,
            close_timeout=2,
        )

    def stop(self) -> None:
        self._stop.set()

    def listen(self) -> Iterator[WatchMessage]:
        """Yield messages until ``stop()``. Reconnects on any transport failure."""
        backoff = BACKOFF_START
        while not self._stop.is_set():
            try:
                with self._connect() as sock:
                    sock.send(json.dumps({
                        "type": "subscriptions.set",
                        "requestID": "watch",
                        "chatIDs": self._chat_ids,
                    }))
                    backoff = BACKOFF_START  # a good connect resets the penalty
                    self._on_connect()
                    yield from self._pump(sock)
            except WatchSocketError:
                raise  # a missing dependency is not something to retry
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._on_error(f"event socket: {type(exc).__name__}: {exc}")
            if self._stop.is_set():
                return
            self._sleep(backoff)
            backoff = min(BACKOFF_MAX, backoff * BACKOFF_FACTOR)

    def _pump(self, sock) -> Iterator[WatchMessage]:
        while not self._stop.is_set():
            try:
                raw = sock.recv(timeout=RECV_TIMEOUT)
            except TimeoutError:
                continue  # idle socket — loop to re-check the stop flag
            if raw is None:
                return
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                frame = json.loads(raw)
            except ValueError:
                self._on_error(f"event socket: unparseable frame {raw[:120]!r}")
                continue
            if not isinstance(frame, Mapping):
                continue
            yield from _messages_from_frame(frame)
