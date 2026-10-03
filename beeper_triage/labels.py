"""`beeper labels sync` engine — mirror Google Contacts labels into Beeper labels.

Pure-ish like ``watch.py``: no typer, stdlib-only I/O, so the CLI in
``labels_cli.py`` stays thin and the matching logic is testable offline.

How a Beeper label works (labels v2, July 2026): each label is a Matrix SPACE
flagged ``com.beeper.label``; the chats filed under it are its
``m.space.child`` state events. ``GET /v1/labels`` lists them. The Desktop API
proxies ``createRoom`` and ``/leave`` but 404s every room-state PUT, so a chat
cannot be added to or removed from an existing label. Any change is therefore
applied by REBUILDING the label: create a new space holding the final set of
chats (name, colour and icon carried over) and leave the old one. The room id
changes each time — consumers must key labels by name.

Ownership: the sync records which chats it put in each label (the ledger).
When a contact leaves a Google label, only chats the ledger says the sync
added are removed. Chats filed by hand in Beeper are never removed.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

DEFAULT_GROUPS = ["Best Friends", "Close Friends", "Contractors", "Family", "Friends"]
DEFAULT_EMAIL = "nyakundihotmail@gmail.com"
GOOGLE_CREDS = Path(os.path.expanduser("~/.google_workspace_mcp/credentials/%s.json"))
PEOPLE = "https://people.googleapis.com/v1"
LEDGER = Path(os.path.expanduser("~/.config/beeper-labels/ledger.json"))

# (method, path, query, body) -> parsed JSON. BeeperClient.raw_request fits.
Api = Callable[..., Any]


class LabelSyncError(RuntimeError):
    pass


# --- Google ------------------------------------------------------------------

def google_token(user: str = DEFAULT_EMAIL) -> str:
    """Short-lived token from the google_workspace MCP's refresh token. Only
    ever reads that file (same contract as gdraft)."""
    path = Path(str(GOOGLE_CREDS) % user)
    if not path.exists():
        raise LabelSyncError(f"no Google credentials at {path} (is the workspace MCP authorised?)")
    c = json.loads(path.read_text())
    body = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(c["token_uri"], data=body), timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        raise LabelSyncError(f"Google token refresh failed ({e.code}): {e.read().decode()[:300]}")


# People API's per-minute read quota is shared with everything else on the GCP
# project, and it was already spent on about half the 08:00/20:00 runs in late
# Sep 2026 (429 on the very first call). The quota refills each minute, so
# waiting it out is the fix; the delays add up to past a full window.
RETRY_DELAYS = (20, 45, 90)


def _gget(token: str, path: str, params: dict, *, sleep=time.sleep) -> dict:
    url = f"{PEOPLE}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    for delay in (*RETRY_DELAYS, None):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and delay is not None:
                sleep(delay)
                continue
            raise LabelSyncError(f"Google GET {path} -> {e.code}: {e.read().decode()[:300]}")


def google_groups(token: str, wanted: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """({label name: [People API person]}, [wanted names Google doesn't have]).

    Matched case-insensitively on the name Contacts shows, so the system
    ``family`` group answers to "Family". User groups win a clash: Contacts
    also has an empty built-in "friends" next to a real "Friends"."""
    groups = _gget(token, "/contactGroups", {"pageSize": 1000}).get("contactGroups", [])
    groups.sort(key=lambda g: g.get("groupType") == "USER_CONTACT_GROUP")
    by_name = {g.get("formattedName", g.get("name", "")).lower(): g for g in groups}
    out: dict[str, list[dict]] = {}
    missing: list[str] = []
    for name in wanted:
        g = by_name.get(name.lower())
        if not g:
            missing.append(name)
            continue
        members = _gget(token, f"/{g['resourceName']}", {"maxMembers": 5000}).get(
            "memberResourceNames", [])
        people: list[dict] = []
        for i in range(0, len(members), 200):
            res = _gget(token, "/people:batchGet", {
                "resourceNames": members[i:i + 200],
                "personFields": "names,phoneNumbers,emailAddresses",
            })
            people += [r["person"] for r in res.get("responses", []) if "person" in r]
        out[g.get("formattedName", name)] = people
    return out, missing


def norm_phone(p: Optional[str]) -> str:
    """E.164-ish key. National numbers written 07… are taken as UK (+44) —
    the account's home country (com.beeper.account_settings says GB)."""
    p = (p or "").strip()
    digits = re.sub(r"\D", "", p)
    if not digits:
        return ""
    if p.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0"):
        return "+44" + digits[1:]
    return "+" + digits


def person_keys(person: dict) -> set[str]:
    keys = {norm_phone(ph.get("canonicalForm") or ph.get("value"))
            for ph in person.get("phoneNumbers", [])}
    keys |= {(em.get("value") or "").strip().lower() for em in person.get("emailAddresses", [])}
    keys.discard("")
    return keys


def person_name(person: dict) -> str:
    names = person.get("names") or [{}]
    return names[0].get("displayName") or person.get("resourceName", "?")


# --- Beeper ------------------------------------------------------------------

def all_chats(api: Api) -> list[dict]:
    """Every chat, archived included, as raw API dicts (participants carry the
    phone numbers the matching needs; BeeperChat drops them)."""
    chats: list[dict] = []
    cursor = None
    while True:
        q: dict[str, Any] = {"limit": 200}
        if cursor:
            q.update(cursor=cursor, direction="before")
        page = api("get", "/v1/chats", query=q)
        items = page.get("items", [])
        chats += items
        if not page.get("hasMore") or not items:
            return chats
        cursor = page["oldestCursor"]


def beeper_labels(api: Api) -> dict[str, str]:
    """{lowercased label name: space id}."""
    out: dict[str, str] = {}
    for lb in api("get", "/v1/labels") or []:
        out.setdefault(str(lb.get("name", "")).lower(), lb["id"])
    return out


def _room(rid: str) -> str:
    return f"/_matrix/client/v3/rooms/{quote(rid, safe='')}"


def label_state(api: Api, rid: str) -> tuple[set[str], dict]:
    """(chat ids filed under the label, its colour/icon events to carry over).
    Read from the space's own state — the authoritative membership, including
    anything filed by hand that /v1/chats may not list."""
    events = api("get", f"{_room(rid)}/state")
    events = events if isinstance(events, list) else []
    children = {e["state_key"] for e in events
                if e.get("type") == "m.space.child" and e.get("state_key") and e.get("content")}
    look = {e["type"]: e["content"] for e in events
            if e.get("type") in ("com.beeper.label.color", "com.beeper.label.icon")}
    return children, look


def build_label(api: Api, name: str, children: set[str], look: dict) -> str:
    """Create a label (the shape the Beeper app writes) with its chats filed."""
    initial = [
        {"type": "com.beeper.label.color", "state_key": "",
         "content": look.get("com.beeper.label.color", {"color_index": None})},
        {"type": "com.beeper.label.icon", "state_key": "",
         "content": look.get("com.beeper.label.icon", {"icon": None})},
    ] + [{"type": "m.space.child", "state_key": cid, "content": {"via": ["beeper.com"]}}
         for cid in sorted(children)]
    return api("post", "/_matrix/client/v3/createRoom", body={
        "name": name, "preset": "private_chat", "room_version": "11",
        "creation_content": {"com.beeper.label": True, "type": "m.space"},
        "initial_state": initial,
    })["room_id"]


def leave(api: Api, rid: str) -> None:
    api("post", f"{_room(rid)}/leave", body={})


# --- matching ------------------------------------------------------------------

def chat_keys(chat: dict) -> set[str]:
    """Phone/email keys of the other person in a 1:1. Groups never match."""
    if chat.get("type") != "single":
        return set()
    keys: set[str] = set()
    for p in (chat.get("participants") or {}).get("items", []):
        if p.get("isSelf"):
            continue
        keys.add(norm_phone(p.get("phoneNumber")))
        keys.add((p.get("email") or "").strip().lower())
    keys.discard("")
    return keys


def merge_groups(chats: list[dict]) -> dict[str, set[str]]:
    """{chat id: every chat id merged with it (itself included)}.

    Beeper's Merge Chats joins one person's chats across networks. The API
    marks it both ways: the merged chat carries ``merge.chatIDs`` and each
    member carries ``mergedIntoChatID``. So an Instagram chat with no phone
    number is labelled once it is merged with that person's WhatsApp chat."""
    roots: dict[str, set[str]] = {}
    for c in chats:
        root = c.get("mergedIntoChatID") or (c["id"] if c.get("merge") else None)
        if not root:
            continue
        members = roots.setdefault(root, {root})
        members.add(c["id"])
        members |= set((c.get("merge") or {}).get("chatIDs") or [])
    out: dict[str, set[str]] = {}
    for members in roots.values():
        for cid in members:
            out[cid] = members
    return out


@dataclass
class LabelPlan:
    name: str
    label_id: Optional[str]
    contacts: int
    wanted: dict[str, str]          # chat id -> "Person (Network)"
    current: set[str] = field(default_factory=set)
    add: set[str] = field(default_factory=set)
    remove: set[str] = field(default_factory=set)
    unmatched: list[str] = field(default_factory=list)

    @property
    def final(self) -> set[str]:
        return (self.current | self.add) - self.remove

    @property
    def changed(self) -> bool:
        return bool(self.add or self.remove) or self.label_id is None

    def to_dict(self) -> dict:
        return {"label": self.name, "labelID": self.label_id, "contacts": self.contacts,
                "chats": len(self.wanted), "current": len(self.current),
                "add": sorted(self.wanted.get(c, c) for c in self.add),
                "remove": sorted(self.remove), "unmatched": sorted(self.unmatched)}


def match(groups: dict[str, list[dict]], chats: list[dict]) -> dict[str, tuple[dict[str, str], list[str]]]:
    """{label: ({chat id: description}, [contacts with no chat])}."""
    index: dict[str, list[dict]] = {}
    for c in chats:
        for k in chat_keys(c):
            index.setdefault(k, []).append(c)
    by_id = {c["id"]: c for c in chats}
    merged = merge_groups(chats)
    out = {}
    for label, people in groups.items():
        wanted: dict[str, str] = {}
        unmatched: list[str] = []
        for person in people:
            hits = {c["id"] for k in person_keys(person) for c in index.get(k, [])}
            for cid in list(hits):
                hits |= merged.get(cid, set())
            if not hits:
                unmatched.append(person_name(person))
            for cid in hits:
                net = (by_id.get(cid) or {}).get("network") or "merged"
                wanted[cid] = f"{person_name(person)} ({net})"
        out[label] = (wanted, unmatched)
    return out


def plan_label(name: str, label_id: Optional[str], contacts: int, wanted: dict[str, str],
               unmatched: list[str], current: set[str], owned: Optional[set[str]]) -> LabelPlan:
    """Decide adds/removes for one label.

    ``owned`` is what the ledger says the sync added; None means the sync has
    never recorded this label, in which case it adopts whatever already
    matches Google (so a first run removes nothing)."""
    if owned is None:
        owned = current & set(wanted)
    p = LabelPlan(name, label_id, contacts, wanted, current, unmatched=unmatched)
    p.add = set(wanted) - current
    p.remove = (owned & current) - set(wanted)
    return p


def next_owned(p: LabelPlan, owned: Optional[set[str]]) -> set[str]:
    """Ledger entry after applying ``p``. Anything no longer in the label
    (removed by hand in Beeper) drops out of ownership too."""
    base = owned if owned is not None else (p.current & set(p.wanted))
    return ((base | p.add) - p.remove) & p.final


def load_ledger(path: Path = LEDGER) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_ledger(ledger: dict[str, list[str]], path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    os.replace(tmp, path)


def sync(api: Api, groups: dict[str, list[dict]], *, apply: bool,
         ledger_path: Path = LEDGER, log: Callable[[str], None] = lambda s: None) -> list[LabelPlan]:
    """Plan every label and, with ``apply``, rebuild the ones that changed."""
    chats = all_chats(api)
    labels = beeper_labels(api)
    ledger = load_ledger(ledger_path)
    plans = []
    for name, (wanted, unmatched) in match(groups, chats).items():
        rid = labels.get(name.lower())
        current, look = label_state(api, rid) if rid else (set(), {})
        owned = set(ledger[name]) if name in ledger else None
        p = plan_label(name, rid, len(groups[name]), wanted, unmatched, current, owned)
        plans.append(p)
        if not apply:
            continue
        if p.changed:
            new = build_label(api, name, p.final, look)
            log(f"{name}: {'rebuilt' if rid else 'created'} {new} "
                f"(+{len(p.add)} -{len(p.remove)}, {len(p.final)} chats)")
            if rid:
                try:
                    leave(api, rid)
                except Exception as exc:  # the new label exists; say so loudly
                    log(f"! {name}: old label {rid} not left, duplicate in Beeper: {exc}")
            p.label_id = new
        ledger[name] = sorted(next_owned(p, owned))
        save_ledger(ledger, ledger_path)
    return plans
