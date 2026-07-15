# beeper-inbox — fast web triage for Beeper

**Opened:** 2026-07-14
**Status:** Building (overnight autonomous)
**Goal:** Wake up, open one website (phone or laptop), and blast through every
unreplied Beeper chat — see context + ready-made reply options, pick/edit/send,
auto-archive, auto-advance — until the inbox is empty. 10 messages in the time
it used to take for 1.

---

## The experience

A conveyor belt. One chat on screen at a time:

- header: who + network + `3 / 24` progress
- the last few messages (them vs you)
- **5 draft replies**, each tagged with a reply *type* the AI judged fits
  (`going` / `schedule` / `close` / `rekindle` / `decline` / `todo`)
- actions: tap a draft → edit inline → **Send** (= send + archive + advance);
  plus **Skip**, **Archive (no reply)**, **Regenerate**, **→ Todo**
- desktop keyboard: `1-5` pick · `e` edit · `s` skip · `a` archive · `r` regen ·
  `Enter` send · `→`/`←` nav
- loops until empty → "Inbox clear".

Drafts for the **next** card are prefetched while you read the current one, so
picking feels instant.

## Architecture — three thin layers

```
Web UI (mobile-first Tailwind + tiny vanilla JS)   card stack + keyboard
        │  fetch()
FastAPI (local, uvicorn, Tailscale-served)         thin HTTP ⇄ engine
        │  import
Engine  (beeper_triage.inbox)                       surface-agnostic, tested
        │
BeeperClient + prompts + OpenRouterClient (reused)  + new archive() + 2 fields
        │
Beeper Desktop  ←  WSL proxy (already running)
```

The **engine lives in the existing `beeper-triage` repo** so it reuses the
Beeper SDK wrapper, OpenRouter client, and prompt plumbing — no duplication.
The **web app is a separate project scaffolded with `demo-tools`** (the canonical
generator) that imports `beeper_triage` as a local dependency.

### Engine contract (`beeper_triage/inbox.py`)

- `build_queue(client, filters) -> list[QueuedChat]` — chats that are
  *unreplied* (`preview_is_sender == False`) **and** `not is_archived`, filtered
  by `groups` / `include_muted` / `networks`, ordered by recency. Default:
  1:1 only, muted hidden, all networks.
- `chat_view(client, chat_id, ...) -> ChatView` — recent messages for display.
- `draft_options(orc, model, transcript, count=5, hint=None) -> list[Draft]` —
  **one** OpenRouter call returns up to N `{type, text}` drafts; the model both
  picks the fitting types and writes each draft. Structured JSON out.
- `resolve(client, chat_id, action, text=None, dry_run=False) -> ActionResult` —
  the single mutation point. `send` = send_message → archive; `archive` =
  archive only; `skip` = no-op. Every UI action routes through here.

### API (FastAPI)

`GET /` (page) · `GET /api/queue` · `GET /api/chat/{id}` (context + drafts) ·
`POST /api/chat/{id}/send {text}` · `POST /api/chat/{id}/archive` ·
`POST /api/chat/{id}/regenerate {hint?}` · `POST /api/todo {chat_id,text}`.
Skip is client-only (just advance). One `BeeperClient` built at startup.

## Key decisions & trade-offs

1. **Web UI, not TUI.** The reusable work is the *engine*, not the surface; a
   TUI would throw away exactly the interface layer and can't do phone. Engine
   is separated so a TUI (or CLI) could ride the same API later for free.
2. **`fastapi` demo-tools stack, single service, run via uvicorn — NOT Fly.**
   The app must reach Beeper Desktop on the local Windows box through the WSL
   proxy; a cloud machine can't. demo-tools assumes Fly+Docker deploy, so we use
   only its *scaffold* and run bare locally. See demo-tools-gaps log.
3. **Engine in beeper-triage, app as separate demo-tools project** — reuse over
   rewrite; keeps demo-tools as the canonical UI-project generator.
4. **5 AI-picked drafts in one structured call** (not N calls, not fixed types).
   Cheaper, faster, consistent; model `claude-haiku-4.5` (already configured).
5. **send = send + archive atomically** via `resolve`, so "done" always means
   "replied and out of the inbox".
6. **Prefetch next card's drafts** so the wait disappears (the thing that makes
   it feel fast).
7. **Fresh chat reads (`use_cache=False`) for the queue** so archive state is
   current; advance locally within a session so archived chats don't reappear.
8. **Testing:** mocked + dry-run everywhere; at most ONE real send+archive to a
   self-chat (Note-to-Self / Saved Messages) if one exists, else dry-run only.
   Never a message to a real contact.

## Staging (all done in this build)

- Engine + `archive()` + tests (beeper-triage).
- FastAPI app + card UI + all actions + prefetch (beeper-inbox).
- Run locally, prep Tailscale serve command (user runs the one admin step).
- Test (dry-run + optional self-send), UX review, notes.

## Out of scope (v1)

Multi-user, auth (tailnet-only), attachments in replies, reactions, threading,
search. Todo capture writes to a local markdown file (TickTick wiring later).
