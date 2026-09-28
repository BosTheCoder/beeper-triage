#!/usr/bin/env python3
"""Can a Beeper label be edited in place with Beeper Desktop's own Matrix login?

Run it yourself (Claude is not allowed to read this credential):

    python3 ~/projects/personal/beeper-triage/scripts/label_edit_probe.py

It reads the access token Beeper Desktop stores in account.db, keeps it in
memory only, and never prints it. Then, straight against Beeper's server:

  1. whoami                        — does the token work outside the app?
  2. create a throwaway label       "zz label probe"
  3. file "Note to self" under it  (PUT m.space.child — the call the Desktop API 404s)
  4. read it back
  5. unfile it (empty content — how Matrix removes a space child)
  6. read it back
  7. leave the throwaway label     — it disappears from Beeper

Output is step names, HTTP codes and PASS/FAIL, nothing else. Stdlib only.
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

WIN_USER = os.environ.get("WIN_USER", "Bosire")
DB = f"/mnt/c/Users/{WIN_USER}/AppData/Roaming/BeeperTexts/account.db"
NOTE_TO_SELF = "!mOoricgCgyDbrptpXZ:beeper.com"


def load_login():
    con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
    try:
        user, token, hs = con.execute(
            "select user_id, access_token, homeserver from account").fetchone()
    finally:
        con.close()
    return user, token, hs.rstrip("/")


def main():
    user, token, hs = load_login()
    print(f"account: {user} on {hs}")

    def mx(method, path, body=None):
        req = urllib.request.Request(
            hs + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            # Matrix error bodies are {"errcode","error"} — never the token.
            try:
                err = json.loads(e.read()).get("errcode", "")
            except Exception:
                err = ""
            return e.code, {"errcode": err}

    results = []

    def step(name, ok, code, extra=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  (HTTP {code}){'  ' + extra if extra else ''}")
        return ok

    code, body = mx("GET", "/_matrix/client/v3/account/whoami")
    if not step("whoami", code == 200 and body.get("user_id") == user, code, body.get("errcode", "")):
        sys.exit(1)

    code, body = mx("POST", "/_matrix/client/v3/createRoom", {
        "name": "zz label probe", "preset": "private_chat", "room_version": "11",
        "creation_content": {"com.beeper.label": True, "type": "m.space"}})
    rid = body.get("room_id")
    if not step("create throwaway label", code == 200 and rid, code, body.get("errcode", "")):
        sys.exit(1)
    room = f"/_matrix/client/v3/rooms/{quote(rid, safe='')}"
    child = f"{room}/state/m.space.child/{quote(NOTE_TO_SELF, safe='')}"

    def children():
        c, ev = mx("GET", f"{room}/state")
        ev = ev if isinstance(ev, list) else []
        return c, {e["state_key"] for e in ev
                   if e.get("type") == "m.space.child" and e.get("content")}

    try:
        code, body = mx("PUT", child, {"via": ["beeper.com"]})
        step("add chat to label in place", code == 200, code, body.get("errcode", ""))
        code, kids = children()
        step("chat is in the label", NOTE_TO_SELF in kids, code)
        code, body = mx("PUT", child, {})
        step("remove chat from label in place", code == 200, code, body.get("errcode", ""))
        code, kids = children()
        step("chat is gone from the label", NOTE_TO_SELF not in kids, code)
    finally:
        code, body = mx("POST", f"{room}/leave", {})
        step("leave throwaway label", code == 200, code, body.get("errcode", ""))

    print("\nRESULT:", "in-place editing WORKS" if all(results) else "in-place editing does NOT work")


if __name__ == "__main__":
    main()
