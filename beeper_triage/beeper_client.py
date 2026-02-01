"""Beeper Desktop API wrapper."""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional


class BeeperSDKError(RuntimeError):
    """Raised when the Beeper SDK fails or is misused."""


@dataclass
class BeeperMessage:
    """Normalized chat message."""

    message_id: str
    sender_name: str
    is_sender: bool
    text: str
    timestamp_ms: int


@dataclass
class BeeperChat:
    """Normalized chat summary."""

    chat_id: str
    title: str
    unread_count: int
    preview_is_sender: bool
    is_muted: bool


class BeeperClient:
    """Thin wrapper around the official beeper_desktop_api SDK."""

    CACHE_DIR = os.path.expanduser("~/.cache/beeper-triage")
    CACHE_FILE = os.path.join(CACHE_DIR, "chats.json")
    CACHE_TTL_MS = 6 * 60 * 60 * 1000  # 6 hours in milliseconds

    def __init__(self, access_token: str, base_url: Optional[str] = None) -> None:
        try:
            from beeper_desktop_api import BeeperDesktop as SDKClient  # type: ignore
        except Exception as exc:  # pragma: no cover - import error path
            raise BeeperSDKError(
                "Failed to import beeper_desktop_api. Install it with 'pip install beeper_desktop_api'."
            ) from exc

        try:
            kwargs: dict[str, Any] = {"access_token": access_token}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = SDKClient(**kwargs)
        except Exception as exc:
            raise BeeperSDKError("Failed to initialize Beeper SDK client.") from exc

    def _get_cache(self) -> Optional[list[BeeperChat]]:
        """Load chats from cache if valid and not expired."""
        if not os.path.exists(self.CACHE_FILE):
            return None
        try:
            with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            timestamp_ms = data.get("timestamp", 0)
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            if now_ms - timestamp_ms > self.CACHE_TTL_MS:
                return None
            chats_data = data.get("chats", [])
            return [BeeperChat(**chat) for chat in chats_data]
        except Exception:
            return None

    def _save_cache(self, chats: list[BeeperChat]) -> None:
        """Save chats to cache with current timestamp."""
        try:
            os.makedirs(self.CACHE_DIR, exist_ok=True)
            timestamp_ms = int(datetime.datetime.now().timestamp() * 1000)
            data = {
                "timestamp": timestamp_ms,
                "chats": [
                    {
                        "chat_id": chat.chat_id,
                        "title": chat.title,
                        "unread_count": chat.unread_count,
                        "preview_is_sender": chat.preview_is_sender,
                        "is_muted": chat.is_muted,
                    }
                    for chat in chats
                ],
            }
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _get_attr(self, obj: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return default

    def list_chats(self, use_cache: bool = True) -> list[BeeperChat]:
        if use_cache:
            cached = self._get_cache()
            if cached is not None:
                return cached

        try:
            chats = self._client.chats.list()
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to list chats via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

        results: list[BeeperChat] = []
        for chat in chats:
            preview = self._get_attr(chat, "preview", default=None)
            preview_is_sender = False
            if preview is not None:
                preview_is_sender = bool(
                    self._get_attr(preview, "is_sender", default=False)
                )
            results.append(
                BeeperChat(
                    chat_id=str(self._get_attr(chat, "chat_id", "id")),
                    title=str(
                        self._get_attr(chat, "title", "name", default="(no title)")
                    ),
                    unread_count=int(
                        self._get_attr(chat, "unread_count", default=0) or 0
                    ),
                    preview_is_sender=preview_is_sender,
                    is_muted=bool(
                        self._get_attr(chat, "is_muted", "muted", default=False)
                    ),
                )
            )

        self._save_cache(results)
        return results

    def get_chat(self, chat_id: str) -> Any:
        try:
            return self._client.chats.get(chat_id)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to fetch chat details via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

    def list_messages(
        self, chat_id: str, limit: Optional[int] = None, since_ms: Optional[int] = None
    ) -> list[BeeperMessage]:
        try:
            page = self._client.messages.list(chat_id=chat_id)
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to list messages via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc

        results: list[BeeperMessage] = []
        stopped_for_since = False

        for page in page.iter_pages():
            page_items = list(page.items)
            if not page_items:
                break

            normalized: list[BeeperMessage] = []
            timestamps: list[int] = []
            for msg in page_items:
                # Handle timestamp conversion - could be int (timestamp_ms) or datetime (timestamp)
                timestamp_value = (
                    self._get_attr(msg, "timestamp_ms", "timestamp", default=0) or 0
                )
                if isinstance(timestamp_value, datetime.datetime):
                    # Convert datetime to milliseconds since epoch
                    timestamp_ms = int(timestamp_value.timestamp() * 1000)
                else:
                    timestamp_ms = int(timestamp_value)

                timestamps.append(timestamp_ms)
                normalized.append(
                    BeeperMessage(
                        message_id=str(self._get_attr(msg, "message_id", "id")),
                        sender_name=str(
                            self._get_attr(
                                msg, "sender_name", "sender", "author", default="Unknown"
                            )
                        ),
                        is_sender=bool(self._get_attr(msg, "is_sender", default=False)),
                        text=str(self._get_attr(msg, "text", "body", default="")),
                        timestamp_ms=timestamp_ms,
                    )
                )

            descending = len(timestamps) < 2 or timestamps[0] >= timestamps[-1]

            for normalized_msg in normalized:
                if since_ms is not None and normalized_msg.timestamp_ms < since_ms:
                    if descending:
                        stopped_for_since = True
                        break
                    continue
                results.append(normalized_msg)
                if limit is not None and len(results) >= limit:
                    break

            if (limit is not None and len(results) >= limit) or stopped_for_since:
                break

            if (
                since_ms is not None
                and descending
                and timestamps
                and min(timestamps) < since_ms
            ):
                stopped_for_since = True
                break

        return results

    def send_message(
        self, chat_id: str, text: str, reply_to_message_id: Optional[str]
    ) -> Any:
        try:
            return self._client.messages.send(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as exc:
            raise BeeperSDKError(
                f"Failed to send message via SDK: {type(exc).__name__}: {str(exc)}"
            ) from exc
