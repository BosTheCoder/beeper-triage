# `beeper watch` — design spec

**Opened:** 2026-08-07
**Status:** Phase 1 shipped 2026-08-07 (`beeper_triage/watch.py`, `watch_cli.py`,
`tests/test_watch.py`). Phase 2 shipped 2026-08-08 (`beeper-inbox/app/watches.py`).
Phase 3 not started.
**Repo:** `~/projects/personal/beeper-triage` (engine + CLI). Phase 2 touches `~/projects/personal/beeper-inbox`.

---

## 1. Why

An agent working a live case needs to know when a specific person replies, without
re-reading every chat. There is no verb for that today, so it gets hand-rolled per task.

On 7 August 2026 the same 120-line watcher was written **three times in one session**, and
each rewrite fixed a different bug in the same small state machine:

| Version | Bug |
|---|---|
| `emit2.py` | Re-raised every chat whose last message was inbound, every 10 minutes, forever. One chat fired ~100 times over seventeen hours after being read and acted on. |
| `emit3.py` | Nag capped, but read a single page of `/v1/chats` — 25 chats — and 9 of 11 watched chats were outside that window. |
| `emit4.py` | Paged with the cursor, plus a per-chat fallback for chats too quiet to appear at all. |

The code volume is not the problem. The problem is that **the bugs live in the state
machine and they recur**, because each rewrite starts from scratch. That is the case for
extracting it once and hardening it.

## 2. Scope

**In:** a `beeper watch` verb that polls, decides what is new and worth reporting, and
prints one line per event to stdout. Config-driven so a new watch is a file, not code.

**Out (deliberately):**

- Sending, drafting or replying. `watch` observes; `send` sends.
- Any notion of "handled". The tool cannot know the user answered by phone, by email, or
  in person. See §5.3 — this is the single most important constraint in the design.
- A daemon. Phase 1 is a foreground process that a caller supervises (Monitor tool, cron,
  `systemd --user`, a shell loop). Persistence is Phase 2's job.

## 3. Verified API behaviour

Everything below was checked against the live API and the installed SDK on 2026-08-07.
**These are the facts the implementation must be built on; several are counter-intuitive.**

### 3.1 `/v1/chats` has no `limit` — but the SDK auto-pages

The raw endpoint takes `cursor` and `direction` only. **There is no `limit` parameter**, so
`beeper api GET /v1/chats -q limit=100` silently returns 25 and looks like it worked. It
returns `hasMore`, `oldestCursor` and `newestCursor` for paging.

**However** — `chats.list()` in the SDK returns `SyncCursorNoLimit[ChatListResponse]`, and
`BaseSyncPage.__iter__` (`_base_client.py:254`) walks `iter_pages()` to exhaustion. So
`beeper_client.list_chats()`, which does a plain `for chat in chats:`, **already sees every
chat, not 25.**

The 25-cap only bites callers using the raw `api` passthrough. `emit4.py`'s hand-rolled
cursor paging was working around a problem the engine does not have. **Build `watch` on
`list_chats`, not on the passthrough.**

### 3.2 `chats.search` does take a limit

`resources/chats/chats.py:496` — `search` accepts `limit`, valid range 1–200, default 50,
and returns `SyncCursorSearch[Chat]`. If a bounded query ever beats a full walk, this is
the endpoint. Not needed for Phase 1.

### 3.3 `BeeperChat` does not carry the preview text

The dataclass has `preview_is_sender` but not the preview body. **Content matching (§4.3)
requires adding a field** — `preview_text: Optional[str]` — populated in `list_chats` from
`preview.text`. This is the only change to an existing structure the spec asks for.

### 3.4 `list_chats(use_cache=True)` is the default

A watcher that forgets `use_cache=False` polls its own cache and goes permanently quiet.
**Silence is indistinguishable from "nothing happened"**, so this fails invisibly. The
`watch` verb must always pass `use_cache=False`, and there should be a test asserting it.

### 3.5 Per-chat probe

`/v1/chats/{chatID}` returns the chat but `preview` is **null** — useless for this purpose.
`/v1/chats/{chatID}/messages?limit=1` returns newest-first with `isSender` and `text`, and
is the reliable single-chat probe if one is ever needed.

### 3.6 What not to build on

- **`--unread`** hides anything already opened on the phone. Most watched messages get
  glanced at on mobile within seconds, so an unread-based watch reports almost nothing.
- **`beeper triage`'s `last_activity_ms`** goes stale. Observed 5 August: at 05:43 it still
  called a 20:35 message the newest, four hours after a 04:29 reply.

## 4. Config

One TOML file per watch. Path passed with `--config`, or resolved by name from
`~/.config/beeper-watches/<name>.toml`.

```toml
name          = "npm-13-edward"
poll_seconds  = 180
state         = "~/.local/state/beeper-watch/npm-13-edward.json"

[nag]
# The emit2 bug, encoded as policy instead of rediscovered. 0 disables re-raises.
after_seconds = 1800
count         = 1

[filters]
inbound_only = true    # skip when preview.isSender. Default true.

# Watches are matched by explicit chat ID, or by a regex on the chat title.
# Title matching is what stops a config being a wall of matrix IDs.
[[watch]]
chat  = "!2VeiAV7APqb0sTtSY226:beeper.local"
label = "ELEC AK Electrical (NAPIT)"

[[watch]]
title_match = "(?i)damp detectives"
label       = "DAMP Damp Detectives"

[[watch]]
chat     = "!oyUw9ncrHGVxMisuSc4X:beeper.local"
label    = "*** TENANT 13 Edward group"
priority = "high"        # advisory; surfaces in the output line

# Optional per-watch content filter. Omit to report every inbound message.
[[watch]]
title_match = "(?i)cadent|national grid"
label       = "GAS network"
text_match  = "(?i)\\b(appointment|engineer|book(ed|ing)?)\\b"
```

**Design note.** `text_match` is deliberately optional and per-watch, never global. A
keyword sweep across every chat in the account is the feature most likely to generate
false positives, and noise is what killed the first two versions of this watcher. The chat
allowlist does the real work; content matching narrows an already-selected chat.

## 5. The state machine

This is the part worth getting right once. Everything else is plumbing.

### 5.1 State file

```json
{
  "!chatid:beeper.local": {
    "last":    1786132403437,   // lastActivity ms of the newest message seen
    "emitted": 1786132410.5,    // wall-clock secs when we last reported it
    "open":    true,            // last message was inbound and matched
    "nags":    0                // re-raises already spent
  }
}
```

Written atomically (temp file + `os.replace`). A corrupt or missing file must degrade to
"start fresh", never crash the loop.

### 5.2 Per-poll algorithm

```
for chat in list_chats(use_cache=False):
    if chat not in configured watches:      continue
    ts = chat.last_activity_ms
    if ts <= state.last:                    continue        # nothing new

    state.last = ts

    if chat.preview_is_sender:                              # we spoke last
        state.open = False; state.nags = 0; continue
    if not text_match(chat.preview_text):                   # inbound but uninteresting
        state.open = False; state.nags = 0; continue

    emit  "REPLY: <label> | <preview text, collapsed, 160 chars>"
    state.open = True; state.nags = 0; state.emitted = now

# re-raise pass
for cid, st in state:
    if st.open and st.nags < nag.count and (now - st.emitted) > nag.after_seconds:
        st.nags += 1
        emit "STILL UNANSWERED (<N>m, final reminder): <label>"
```

### 5.3 Why the nag is capped — read this before changing it

`open == true` means *"the last message in this chat came from them"*. It is tempting to
read that as *"you still owe them a reply"*. **They are not the same thing**, and conflating
them is exactly the emit2 bug.

A message can be fully dealt with — read, filed, acted on, answered by phone, answered by
email — and the chat still ends on their message forever. The tool has no way to observe
any of that. So an uncapped re-raise on `open` is not a reminder; it is a permanent alarm
on a resolved item, and it trains the reader to ignore the channel.

The cap exists because the re-raise has exactly one legitimate job: **covering a lost first
notification.** One repeat does that. A second never adds information.

Marking something handled without a reply is a genuine gap. `beeper mark-read` is the
existing verb closest to that intent, and §8 lists it as an open question. It is not in
Phase 1 — the cap makes it unnecessary rather than merely tolerable.

## 6. Output contract

One event per line on stdout, flushed per line. Nothing else on stdout — diagnostics and
poll errors go to stderr.

```
REPLY: ELEC AK Electrical (NAPIT) | Your welcome. Please if you don't mind can you take a minute...
STILL UNANSWERED (34m, final reminder): GAS Kamdem HomeTech
```

That contract is what makes this composable: it is exactly what the Claude Code Monitor
tool consumes (one stdout line becomes one notification), and it pipes to `grep`, `tee`,
`notify-send` or a webhook with no adapter.

`--json` emits one JSON object per line instead, for programmatic consumers:

```json
{"event":"reply","chat":"!…","label":"…","text":"…","ts":1786132403437,"priority":"high"}
```

**A failed poll must never kill the loop.** Log to stderr, keep the previous state, try
again next tick. A monitor that dies silently is worse than one that reports nothing,
because both look the same from outside.

## 7. CLI surface

```
beeper watch --config <path|name>     # foreground, one line per event
        [--once]                      # single poll then exit — for cron and for tests
        [--json]
        [--dry-run]                   # seed state from current reality, emit nothing
        [--state <path>]              # override the config's state path

beeper watch list                     # configured watches, resolved chat IDs, last activity
beeper watch check <name>             # resolve title_match patterns to real chats and print
                                      # them, so a typo'd regex fails loudly at setup
```

`--dry-run` matters more than it looks. Arming a watch against a busy account without
seeding fires an event for every chat whose last message happens to be inbound. Seed
first, then run.

## 8. Phases

**Phase 1 — the verb.** Config loader, `preview_text` on `BeeperChat` (§3.3), state
machine, `watch` / `--once` / `--dry-run` / `--json`, `watch check`. Ships as a
`beeper-triage` change; `beeper-inbox` picks it up through `sync-engine.sh`.

**Phase 2 — watches that outlive a session.** This is the larger prize. `beeper-inbox`
already runs continuously in a container on the tailnet and already has an append-only
`events.jsonl` (`app/events.py`). A watch registered there keeps running when the
conversation that created it ends, which is the real limitation of the current approach —
a session-scoped monitor dies with the session, and on 7 August one was also lost when its
scratchpad was cleared mid-task. Endpoints: `POST /api/watches`, `GET /api/watches`,
`DELETE /api/watches/{name}`, events appended to the existing log and surfaced in the UI.

**Phase 3 — push.** Route Phase 2 events somewhere that reaches a phone. Out of scope
until Phase 2 exists.

## 9. Testing

The bugs this spec exists to prevent are all state-machine bugs, so the tests are mostly
table-driven over synthetic chat lists — no network needed:

| Case | Expected |
|---|---|
| Inbound message, chat newly seen | one `REPLY` |
| Same poll repeated, nothing changed | silence |
| Outbound last message | silence, `open` cleared |
| Inbound, then we reply | silence on the reply, `open` cleared |
| Inbound, `nag.after` elapses | exactly one `STILL UNANSWERED`, then silence forever |
| `nag.count = 0` | never re-raises |
| Inbound not matching `text_match` | silence, `open` cleared |
| Corrupt state file | starts fresh, no crash |
| API raises mid-poll | logged to stderr, loop survives, state intact |
| `use_cache` | asserted `False` on every call (§3.4) |

One live smoke test against the WhatsApp self-chat (`!stApPk0AHQFs5wAY91pU:beeper.local`),
following the repo's existing send-safety convention.

## 10. Open questions

1. **~~Cost of `list_chats(use_cache=False)` per poll.~~ Answered 2026-08-07 — it is
   cheap.** Measured on the live account: **2,000 chats over 81 pages in 1.7–2.3s**, and
   the final page reports `hasMore: false`, so the walk is genuinely exhaustive (§3.1
   confirmed). A 180s poll costs ~1% of a core; even 30s would be comfortable. The
   `chats.search` fallback (§3.2) is not needed.
2. **Is there a push/subscribe transport?** The SDK ships `_streaming.py`, but no
   subscribe surface was found on the chats resource. If Beeper Desktop exposes a socket,
   the whole poll loop collapses into a listener and §5.2 gets much simpler. Still open —
   given Q1's answer, polling is cheap enough that this is now an elegance question, not a
   cost one.
3. **Marking handled without replying** (§5.3). Does `mark-read` clear `open`, or does
   `watch` need its own ack? Deferrable — the nag cap removes the urgency.
4. **Group chats.** Should a `[[watch]]` be able to filter on *sender* within a group, not
   just the chat? Real case: the 13 Edward group contains the tenant and the contractor,
   and only one of them is load-bearing at a time. **Now cheap to build:** `preview` is a
   full `Message`, so it already carries `senderID` and `senderName` — a `sender_match`
   key would mirror `text_match` exactly.

## 11. Phase 1 as built

Deviations from the spec above, all additive:

- **`inbound_only = false` has a defined meaning.** §4 declared the knob but §5.2 only
  ever described the inbound path. As built, `false` also reports outbound activity, as a
  distinct `ACTIVITY:` line (`"event":"activity"` in JSON) so the two stay greppable
  apart. It never sets `open` — your own message cannot mean someone is waiting on you.
- **Labels live in the state file.** `ChatState` carries `label` and `priority` alongside
  §5.1's four fields, so the re-raise pass can render a line for a chat that did not
  appear in that poll at all, and so the state file reads as something other than opaque
  IDs.
- **`nag.after_seconds = 0` also disables re-raises**, matching `count = 0`. Firing
  instantly was the only other reading and it is obviously wrong.
- **Unknown keys in `[[watch]]` are a hard error.** A typo'd `title-match` would otherwise
  silently disable a watch — the exact failure `watch check` exists to catch, so it fails
  at load instead of at 3am.
- **`preview_text` is `None` when the API omits it.** When a watch sets `text_match` and
  the preview has no text, that is reported on stderr rather than silently treated as a
  non-match, because a permanently quiet watch is the failure this design fears most.
- **`--dry-run` implies a single poll** and exits, rather than seeding and continuing.
- **CLI shape:** `--config` is an option on `watch` and on `watch list`; `watch check`
  takes the config as a positional argument (§7 wrote it that way) and exits 1 when any
  watch resolves to no chat.

Verified live on 2026-08-07 against the WhatsApp self-chat: `check` → `--dry-run` seed →
send → `--once` emitted one line → `--once` again silent; `text_match` matched and then
correctly did not.

## 12. Phase 2 as built

Lands in `beeper-inbox` as `app/watches.py` + three endpoints. §8's sketch held; the
notable calls:

- **The engine was split first.** `watch.py` is now the pure state machine (stdlib plus
  `beeper_client`) and `watch_cli.py` holds the Typer wiring, because the container has no
  typer in its requirements. `sync-engine.sh` vendors `watch.py`, so the CLI and the
  container run the *same* state machine rather than two copies of it.
- **`parse_config(mapping)` was split out of `load_config(path)`**, so a watch POSTed as
  JSON is validated by identical rules with identical messages. Only the decoding differs.
- **One chat fetch per tick serves every watch.** Given §10.1 (the whole account in ~2s,
  returning everything), N watches cost one API call. The consequence: `poll_seconds`
  becomes a floor across the set, not a per-watch schedule — a watch is evaluated at least
  as often as it asked, sometimes sooner. Never later, so nothing is lost.
- **Seed before arm, enforced by the store.** A registered watch is stored `seeded: false`;
  the poller's first tick runs `scan(seed=True)` and emits nothing. This is §7's `--dry-run`
  lesson made non-optional, because over an API there is no human to remember it. Replacing
  a watch re-seeds it.
- **Registration needs no live Beeper.** Seeding happens on the tick, not in the request,
  so a `POST` does not fail because the proxy is momentarily down.
- **Everything degrades to a logged line** in `events.jsonl` — dead Beeper, hand-broken
  `watches.json`, one watch raising mid-tick — and retries next tick.
- **Two payload fields are renamed on the way into the log.** `events.log(event, **fields)`
  builds `{"ts": now, "event": event, **fields}`, so the engine's own `event` and `ts`
  overwrote both, and watch records lost the wall-clock time they were logged at. They land
  as `kind` and `activity_ms`. Found by the live smoke test, not the unit tests.

Endpoints: `POST /api/watches` (`{name, config}`), `GET /api/watches` (`?resolve=1` adds
what each currently matches, `?events=N` the recent fired events), `DELETE
/api/watches/{name}`. **No UI** — the consumers are the CLI and agents, so the read surface
is JSON only.

Verified live on 2026-08-08 against the WhatsApp self-chat: register → poller seeded and
armed with nothing emitted → send → the event appeared on `GET /api/watches` within one
tick → delete removed the watch and its state file.
