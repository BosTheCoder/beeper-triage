# Beeper CLI Redesign — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorm), pending implementation plan
**Repo:** `~/projects/personal/beeper-triage`
**Related:**
- `../../../../tasks/2026-06-02-beeper-mcp-vs-api-gaps/comparison.md` — the gap analysis that motivated this
- `../../../../tasks/2026-05-30-beeper-mcp-fixed-proxy-port/index.md` — WSL connection bootstrap (reused here)
- `../../../../tasks/2026-03-22-beeper-new-chat-feature/index.md` — the "start new chat" gap, now filled by `beeper start`
- `../../../../tasks/2026-06-02-shell-toolkit-framework/design.md` — the `toolkit` framework this registers into

---

## Problem

The Beeper-provided MCP server exposes only ~12 of the ~30 Beeper Desktop API operations (verified 2026-06-02 against `developers.beeper.com`). It is a *read + send-text + archive + remind + search + focus* surface. Missing, in order of everyday impact: **send attachments, react, start a new chat, mark read/unread** (Tier 1); **edit/delete messages, download incoming media, PATCH chat (draft/rename/mute/pin)** (Tier 2); and a long tail (contacts, bridges, info).

`beeper-triage` has meanwhile grown from a reply tool into an offline-export + multi-feature tool. It is becoming, in effect, a CLI over the Beeper API. **Important correction (verified by reading the code 2026-06-02):** `beeper_client.py` is **already a thin adapter over the official `beeper_desktop_api` SDK** — it imports `from beeper_desktop_api import BeeperDesktop` and calls `client.chats.list()`, `client.messages.send()`, `client.chats.create()`, etc. The SDK is already a `pyproject.toml` dependency, and the CLI is a **`typer`** app (`beeper-triage = beeper_triage.cli:app`) that **already has two commands** (`triage` default + `new-chat`) and a partial `--agent`/JSON-output convention. So the work is **not** "adopt the SDK" — it is **restructure** (rename, modularise, de-duplicate the connection bootstrap) and **extend the adapter** with the operations it doesn't yet wrap (`react`, `edit`, `delete`, `archive`, `mark_read/unread`, `start`, asset up/download, `chats.search`, `messages.search`).

This spec rationalises the three ways to reach Beeper (MCP, `beeper-triage`, raw API) into one coherent setup, optimised primarily for an **AI agent** with the user retaining the interactive triage flow.

## Goals

- Give the AI agent reliable, full-API Beeper power (especially the Tier-1 gaps).
- Preserve the user's interactive triage/export workflow unchanged.
- Lean on the official SDK the adapter already wraps; **extend** the adapter with missing operations rather than hand-rolling HTTP.
- Register the tool into the new `toolkit` framework for discovery, machine-scoped sync, and `doctor` coverage.
- Give the agent a single routing skill so it knows which path to use when.

## Non-Goals (YAGNI)

- **No second MCP server.** It would inherit the exact WSL proxy/connection battle already being fought for the one MCP, double the auth story, and add maintenance. Ruled out.
- **No real-time / WebSocket support.** The agent is request/response; not needed.
- **No bespoke verbs for the long tail** (bridges, info, single-account detail). The `beeper api` passthrough covers them.

## Architecture — three layers, CLI as backbone

```
┌─ AI agent ─────────────────────────────────────────────┐
│  routing skill decides which path per task              │
└───────┬───────────────────────────┬────────────────────┘
        │ fast-path (when up)        │ backbone (always)
        ▼                            ▼
  Beeper MCP (12 tools)        `beeper` CLI ──► official Beeper Python SDK ──► /v1 API
  reads · search · reply       all writes, attachments, reactions,
  structured, low-token        exports, triage, + reads as fallback
                               `beeper api …` passthrough = escape hatch
        ▲                            ▲
        └─ user (interactive) ──────►┘  `beeper triage`, occasional `beeper send`
```

**Key inversion:** the **CLI is the reliable backbone; the MCP is an optional low-token fast-path for reads**. Rationale: the MCP is the component with the unreliable WSL connection; the CLI already solved that connection (proxy auto-start, base-URL, token bootstrap). So the full-power, always-available path is the CLI, and the MCP is a convenience used *only when connected*, with the CLI as fallback. A down read-only convenience layer never blocks the agent.

## Component 1 — the `beeper` CLI

### Rename & restructure (no SDK swap — the SDK is already adopted)
- Rename the **command** `beeper-triage` → `beeper` (console-script in `pyproject.toml`). The repo may keep its name; `triage` becomes one verb among many.
- **Extract the duplicated connection bootstrap** (proxy auto-start + `BEEPER_BASE_URL` reachability check + `BeeperClient` construction with `Authorization: Bearer $BEEPER_ACCESS_TOKEN`) — currently copy-pasted in both `triage` and `new-chat` — into one shared helper so every verb gets it for free.
- **SDK version reality (verified 2026-06-02 by introspecting installed + isolated v5):** the *installed* SDK was `beeper_desktop_api` **v4.1.296**, which does **not** wrap reactions, mark-read/unread, start, message edit/delete, chat update, or asset upload. **v5.0.0** (on PyPI) wraps all of them. **Decision: upgrade to `beeper_desktop_api==5.0.0`** as the first task of Phase 2, with a regression pass on existing `triage`/`new-chat`/`export` (note: the adapter's `get_chat` calls `chats.get`, which neither version has — it must become `chats.retrieve`).
- **Extend the existing `beeper_client.py` adapter** with the missing SDK calls (exact v5.0.0 signatures, introspected):
  - `client.chats.messages.reactions.add(message_id, chat_id=…, …)` / `.delete(reaction_key, chat_id=…, message_id=…)`
  - `client.messages.update(message_id, chat_id=…, …)` (edit) · `client.messages.delete(message_id, chat_id=…, …)` · `client.messages.retrieve(message_id, chat_id=…)`
  - `client.chats.archive(chat_id, …)` · `client.chats.mark_read(chat_id, …)` · `client.chats.mark_unread(chat_id, …)` · `client.chats.start(…)` · `client.chats.update(chat_id, …)`
  - `client.assets.upload(file=Path(…))` · `client.assets.serve(…)` / `client.assets.download(…)`
  - `client.chats.search(…)` · `client.messages.search(…)`
  - Note: a few methods take `**params`; the exact keyword names (e.g. the reaction-emoji key, archive's boolean, delete's for-everyone flag) are verified by introspection as the first task of the verb phase, not guessed.

### Verb surface

| Verb | Fills gap | Notes |
|---|---|---|
| `beeper triage` | — | interactive triage — current UX preserved |
| `beeper export [chat…]` | — | offline-analysis dumps (current feature) |
| `beeper chats` / `beeper search <q>` | — | list / search chats |
| `beeper read <chat>` | — | list messages in a chat |
| `beeper send <chat> [--reply-to ID] [--attach FILE…]` | 🔴 attachments | text **and/or** files/images |
| `beeper react <chat> <msg> <emoji>` | 🔴 reactions | add; `--remove` to delete |
| `beeper mark-read <chat>` / `beeper mark-unread <chat>` | 🔴 read state | |
| `beeper start <account> <user> [--text T]` | 🔴 new chat | DM someone new / create group |
| `beeper edit <chat> <msg> <text>` | 🟠 edit | |
| `beeper delete <chat> <msg> [--for-everyone]` | 🟠 unsend | |
| `beeper dl <chat> <msg> [--out PATH]` | 🟠 media | download incoming attachment |
| `beeper api <METHOD> <path> [--query k=v…] [--body JSON]` | long tail | raw passthrough to any `/v1` endpoint |

### Output contract (serves both consumers)
- **Default JSON when stdout is not a TTY** (agent invocation) — clean machine parsing.
- **Pretty/human output when stdout is a TTY** (interactive use).
- A `--json` / `--no-json` flag overrides the auto-detection either way.
- `triage` is inherently interactive and exempt from JSON mode.

### Error contract
- Non-zero exit on failure; error detail emitted as JSON `{ "error": … }` in JSON mode, human message otherwise.
- Connection/bootstrap failures (proxy down, token missing) produce a distinct exit code and a one-line remediation hint, so the agent can distinguish "Beeper unreachable" from "bad arguments".

## Component 2 — toolkit registration

`beeper` stays in its own repo and is installed on `PATH` via `uv tool install -e .`. The `toolkit` framework only *registers the command* for discovery/sync/doctor — it is **not** in the agent's execution path, and there is **no** entry-wrapper logic (the connection bootstrap lives inside the app; `beeper` is already on PATH).

- **Evolve the existing stub** `tools/other/bpt.sh` into `tools/comms/beeper.sh` (new `Comms` category):
  ```sh
  # @tool  beeper
  # @cat   Comms
  # @desc  Beeper CLI — chats, messages, send/react/triage
  # @flags <verb> [args]   (triage|send|react|read|export|api …)
  # @needs beeper
  # @tags  beeper
  beeper() { command beeper "$@"; }   # registration + discovery only

  # @tool  bpt
  # @cat   Comms
  # @desc  Beeper triage (shortcut)
  # @needs beeper
  # @tags  beeper
  alias bpt='beeper triage'
  ```
- **Machine scoping:** tag `beeper` (not `all`); include the `beeper` tag in `~/.config/toolkit/profile` only on machines where Beeper Desktop runs. `toolkit doctor`'s `@needs beeper` check (via `shutil.which`) then flags any in-scope machine missing the binary.
- `bpt` is retained as a human shortcut for `beeper triage`.

## Component 3 — the routing skill (`beeper`)

A single skill = the agent's decision table for all things Beeper. Core is a routing rule, not prose:

| Task | Use | Why |
|---|---|---|
| Search/find chat or messages, read a thread, **simple text reply** | **MCP tool** *if connected*, else `beeper` CLI | low-token, structured; CLI fallback so a down MCP never blocks |
| Send **with attachment**, react, mark read/unread, start a new chat, edit/delete | **`beeper` CLI** | not in MCP at all |
| Bulk **export / offline analysis**, interactive triage | **`beeper` CLI** | its home turf |
| No verb yet (bridges, info, contacts, niche) | **`beeper api <METHOD> <path>`** | passthrough escape hatch |

Plus:
- A quick "is the MCP up?" check the agent does before relying on the fast-path.
- Standing rule: **the CLI can do everything the MCP can — when in doubt, use the CLI.**
- The auth/connection note (token + proxy) so the agent doesn't re-derive the WSL saga each session.

## Testing

- **Phase 1 (SDK swap):** existing `tests/test_cli.py` triage/export behaviour stays green; add a thin contract test that the SDK shim configures base-URL + bearer from env. This is the de-risking phase — no behaviour change.
- **Per verb:** unit test argument parsing → SDK call mapping (mock the SDK client); assert JSON-mode output shape and exit codes. One happy-path + one error-path per verb.
- **Output contract:** test TTY vs non-TTY switches JSON on/off; `--json/--no-json` overrides.
- **Passthrough:** test method/path/query/body are forwarded verbatim and the raw response is returned.
- **Toolkit registration:** `toolkit build` parses `tools/comms/beeper.sh` without error and `toolkit doctor` reports `@needs beeper` correctly (covered by toolkit's own registry/doctor tests; add a fixture there).

## Phasing

1. **Rename + restructure + toolkit registration.** Rename command → `beeper`; extract the shared connection bootstrap out of `triage`/`new-chat`; add the JSON/TTY output helper; preserve `triage`/`export`/reads behaviour exactly; promote `bpt.sh` → `tools/comms/beeper.sh` (scoped tag, `@needs beeper`). Tests green. *(Foundation; no new user-facing features. No SDK swap — the SDK is already in use.)*
2. **Tier-1 verbs.** `send --attach`, `react`, `mark-read`/`mark-unread`, `start` + the JSON/TTY output contract.
3. **Passthrough + Tier-2.** `beeper api`, then `edit`/`delete`/`dl`.
4. **Routing skill.** Author the `beeper` skill with the decision table + MCP-up check + connection note.

Phases 1–3 have no external dependency. Phase 4 documents the result.

## Open questions / deferred

- ~~Exact official SDK package name~~ — **resolved:** Python package is `beeper_desktop_api` (`from beeper_desktop_api import BeeperDesktop`). **Pin `==5.0.0`** (was unpinned, resolved to 4.1.296 which lacks the Tier-1 methods). v5 verb signatures introspected: `chats.messages.reactions.add(message_id, *, chat_id, reaction_key)` / `.delete(reaction_key, *, chat_id, message_id)`; `chats.mark_read/.mark_unread(chat_id, *, message_id?)`; `chats.start(*, account_id, user, allow_invite?, message_text?)` where `user` is `{id|email|phone_number|username|full_name}`; `assets.upload(file=, file_name?, mime_type?)`; `messages.send(chat_id, *, attachment?, text?, reply_to_message_id?)` where `attachment` is `{upload_id (required), type (image|video|audio|file|gif|voice-note|sticker), file_name?, mime_type?, size?, duration?}`.
- Exact `**params` keyword names for `reactions.add` (emoji key), `chats.archive` (bool), `messages.delete` (for-everyone), and `messages.send` (attachment param) — resolved by SDK introspection as the first task of the verb phase.
- Whether `export`'s current on-disk format needs any change (default: keep identical — no behaviour change in Phase 1).
