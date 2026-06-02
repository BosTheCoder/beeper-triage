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

`beeper-triage` has meanwhile grown from a reply tool into an offline-export + multi-feature tool with a hand-rolled partial API client (`beeper_client.py`, ~7 ops including `create_chat`, which the MCP lacks). It is becoming, in effect, a CLI over the Beeper API.

This spec rationalises the three ways to reach Beeper (MCP, `beeper-triage`, raw API) into one coherent setup, optimised primarily for an **AI agent** with the user retaining the interactive triage flow.

## Goals

- Give the AI agent reliable, full-API Beeper power (especially the Tier-1 gaps).
- Preserve the user's interactive triage/export workflow unchanged.
- Stop maintaining a hand-rolled API client.
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

### Rename & client swap
- Rename the **command** `beeper-triage` → `beeper`. The repo may keep its name; the entry point and console-script become `beeper`. `triage` becomes one verb among many.
- **Replace `beeper_client.py` with the official Beeper Desktop Python SDK** (the SDK that backs `@beeper/desktop-api`, covers 100% of `/v1`, tracks new endpoints). Keep the proven WSL connection bootstrap (proxy auto-start + `BEEPER_BASE_URL` + `Authorization: Bearer $BEEPER_ACCESS_TOKEN`) as a thin shim that constructs/configures the SDK client. The hand-rolled client is deleted once parity is proven.

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

1. **SDK swap + rename + toolkit registration.** Adopt the official SDK behind the connection shim; rename command to `beeper`; preserve `triage`/`export`/reads exactly; promote `bpt.sh` → `tools/comms/beeper.sh` (scoped tag, `@needs beeper`). Tests green. *(Foundation; no new user-facing features.)*
2. **Tier-1 verbs.** `send --attach`, `react`, `mark-read`/`mark-unread`, `start` + the JSON/TTY output contract.
3. **Passthrough + Tier-2.** `beeper api`, then `edit`/`delete`/`dl`.
4. **Routing skill.** Author the `beeper` skill with the decision table + MCP-up check + connection note.

Phases 1–3 have no external dependency. Phase 4 documents the result.

## Open questions / deferred

- Exact official SDK package name/version to pin — confirm at implementation time (TS pkg is `@beeper/desktop-api` v5.x; verify the Python distribution name).
- Whether `export`'s current on-disk format needs any change when reads route through the SDK (default: keep format identical).
