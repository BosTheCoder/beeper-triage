# Beeper CLI Phase 4 — Routing Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stale `beeper-triage` skill with a single `beeper` routing skill that tells the AI agent which path to use (Beeper MCP vs `beeper` CLI vs `beeper api` passthrough) for any Beeper task, documents the full verb surface, and bakes in the connection/ID/semantic gotchas learned in Phases 2–3.

**Architecture:** This is a documentation/skill change, not code. The existing skill at `ai-toolkit/plugins/ai-toolkit/skills/beeper-triage/SKILL.md` is outdated — it references the pre-Phase-1 command name `beeper-triage` and only knows `triage`/`new-chat`. We rename the skill directory to `beeper`, rewrite `SKILL.md` as the routing skill, and verify every documented command against the real `beeper --help`.

**Tech Stack:** Markdown skill file (`SKILL.md` with YAML frontmatter), the `beeper` CLI (for verification), the Beeper MCP server.

---

## Grounding — facts the skill must encode (verified Phases 2–3)

- **Command is `beeper`** (renamed from `beeper-triage` in Phase 1). `bpt` is the alias for `beeper triage`.
- **Full verb surface** (from `beeper_triage/verbs.py` + Phase 3): `triage`, `new-chat`, `send`, `react`, `mark-read`, `mark-unread`, `start`, `edit`, `delete`, `dl`, `api`.
- **Output contract:** `--agent` forces JSON; `--json/--no-json` overrides TTY auto-detection. `triage` is interactive and exempt.
- **ID formats (live-verified):** chat IDs are matrix-form `!xxxx:beeper.local`; message IDs are numeric (e.g. `293417`). The Beeper **MCP's short numeric chat IDs (e.g. `185255`) are NOT interchangeable** with the SDK/CLI — always pass the CLI the matrix-form chat ID.
- **`start --text` semantic (live-verified):** delivers the first message **only when a new chat is actually created**. On an existing chat it resolves to it and **silently drops `--text`**. Rule: for guaranteed delivery use `send`, not `start --text`.
- **`send` returns `pendingMessageID`**, not the final message ID. To act on a just-sent message (edit/delete/react), re-list the chat to get its real numeric ID.
- **MCP-vs-CLI:** the MCP is the flaky-over-WSL path; the CLI solved the connection (proxy auto-start). The CLI is the always-available backbone; the MCP is a low-token read fast-path used only when connected.

## File Structure

- **Rename** dir `ai-toolkit/plugins/ai-toolkit/skills/beeper-triage/` → `ai-toolkit/plugins/ai-toolkit/skills/beeper/` (preserve git history with `git mv`).
- **Rewrite** `ai-toolkit/plugins/ai-toolkit/skills/beeper/SKILL.md` — new frontmatter (`name: beeper`) + routing-skill body.

Note: all work for this phase happens in the **ai-toolkit repo** (`/home/bosire/projects/personal/ai-toolkit`), not the beeper-triage repo. Commit there.

---

## Task 1: Rename the skill directory

**Files:**
- Rename: `ai-toolkit/plugins/ai-toolkit/skills/beeper-triage/` → `.../skills/beeper/`

- [ ] **Step 1: Rename with git to preserve history**

```bash
cd /home/bosire/projects/personal/ai-toolkit
git mv plugins/ai-toolkit/skills/beeper-triage plugins/ai-toolkit/skills/beeper
```

- [ ] **Step 2: Verify the move**

Run: `cd /home/bosire/projects/personal/ai-toolkit && ls plugins/ai-toolkit/skills/beeper/`
Expected: `SKILL.md` present under the new path; old `beeper-triage/` gone.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor(skill): rename beeper-triage skill dir to beeper"
```

---

## Task 2: Rewrite SKILL.md as the routing skill

**Files:**
- Overwrite: `ai-toolkit/plugins/ai-toolkit/skills/beeper/SKILL.md`

- [ ] **Step 1: Write the new SKILL.md**

Write this exact content:

````markdown
---
name: beeper
description: Read, search, send, and manage Beeper (WhatsApp/iMessage/Telegram/etc) chats and messages. Use for ANY Beeper task — checking messages, replying, sending attachments, reactions, starting chats, editing/deleting, downloading media, or hitting the raw API.
---

# Beeper Skill

One tool family, three access paths. This skill is the decision table for which to use.

**Requires:** Beeper Desktop running locally. The `beeper` CLI auto-starts a WSL→Windows proxy; the Beeper MCP needs the same desktop app up.

## The three paths

1. **Beeper MCP** (`mcp__beeper__*`) — low-token, structured **reads** (search chats/messages, list messages, get chat) and simple text send. Convenient but rides the flaky WSL connection. Use **only when it responds**.
2. **`beeper` CLI** — the reliable backbone. Every write, attachments, reactions, read-state, start, edit, delete, media download, bulk export, interactive triage — plus reads as a fallback when the MCP is down. Always pass `--agent` for JSON output.
3. **`beeper api <METHOD> <path>`** — raw passthrough to any `/v1` endpoint for the long tail with no dedicated verb.

**Standing rule: the CLI can do everything the MCP can. When in doubt, use the CLI.** A down MCP must never block you — fall back to the CLI.

## Routing decision table

| Task | Use | Why |
|---|---|---|
| Search/find a chat or messages, read a thread, **simple text reply** | **MCP** if it responds, else `beeper` CLI | low-token, structured; CLI fallback so a down MCP never blocks |
| Send **with attachment**, react, mark read/unread, start a new chat, edit, delete, download media | **`beeper` CLI** | not in the MCP at all |
| Bulk **export / offline analysis**, interactive triage | **`beeper` CLI** | its home turf |
| Anything with no verb yet (bridges, info, contacts, niche endpoints) | **`beeper api <METHOD> <path>`** | passthrough escape hatch |

### Is the MCP up? (do this before relying on the fast-path)
Make one cheap MCP call (e.g. `mcp__beeper__get_accounts`). If it errors or times out, treat the MCP as down and use the CLI for everything this session.

## CLI verb reference

All verbs accept `--agent` (force JSON, always use it programmatically) and `--json/--no-json`.

| Verb | Syntax | Notes |
|---|---|---|
| triage | `beeper triage --agent --no-llm --message-window 30d` | list/read/reply flow; interactive without `--agent` |
| new-chat | `beeper new-chat --agent --phone +44… --network whatsapp [-m "hi"]` | resolves a phone→contact, creates a 1:1 |
| send | `beeper send '<chat>' --text "…" [--attach FILE] [--reply-to <msgID>] --agent` | text and/or attachment; returns `pendingMessageID` |
| react | `beeper react '<chat>' <msgID> 👍 [--remove] --agent` | add or remove an emoji reaction |
| mark-read / mark-unread | `beeper mark-read '<chat>' --agent` | toggle chat read state |
| start | `beeper start <accountID> --phone +44… [--username/--email/--user-id] [--text "…"] --agent` | start a **new** conversation |
| edit | `beeper edit '<chat>' <msgID> "new text" --agent` | edit a message you sent |
| delete | `beeper delete '<chat>' <msgID> [--for-everyone] --agent` | unsend |
| dl | `beeper dl '<chat>' <msgID> [--out PATH] [--index N] --agent` | download an incoming attachment to disk |
| api | `beeper api GET /v1/accounts --agent` · `beeper api POST /v1/… --query k=v --body '{"…":…}' --agent` | raw passthrough; returns parsed JSON |

### Finding a chat / message ID for the CLI
```bash
# list + filter by name, grab the matrix-form chat_id
beeper triage --agent --no-llm --include-muted --message-window 30d \
  | jq '.chats[] | select(.title | test("dad";"i")) | {chat_id, title}'
```

## Gotchas (learned the hard way — Phases 2–3)

- **ID formats differ between MCP and CLI.** CLI chat IDs are matrix-form `!xxxx:beeper.local`; message IDs are numeric (`293417`). The MCP's short numeric chat IDs (e.g. `185255`) are **NOT** valid for the CLI — pass the CLI the matrix-form `chat_id` (the `chat_id` field the CLI/`triage` returns is already correct).
- **`start --text` only delivers on NEW chats.** If the chat already exists, `start` resolves to it and silently drops `--text` (you still get `status: started`, exit 0, but no message is sent). **For guaranteed delivery use `send`, not `start --text`.**
- **`send` returns `pendingMessageID`, not the final message ID.** To edit/delete/react on a message you just sent, re-list the chat (MCP `list_messages` or `beeper triage … --action export`) to get its real numeric ID first.
- **Connection failures are distinct from bad args.** A connection/bootstrap failure (proxy down, token missing) exits non-zero with an error hint — if the CLI can't reach Beeper, note Beeper is unavailable and fall back to email/other sources rather than retrying blindly.
- **Message send to 08xx / landline / non-smartphone numbers:** messages over ~160 chars trigger MMS, which those numbers often can't receive. Split into <160-char parts at sentence boundaries. (Carrier limitation, handled automatically by `new-chat`.)

## Project-specific chats
Read the project's CLAUDE.md for a **Research Context** section — it may list chat names/IDs relevant to the project. Always use `--include-muted` when searching for project-specific chats (they're often muted).

## Troubleshooting
- **"No such command"**: check the verb name against the table above; the top-level command is `beeper` (not `beeper-triage`).
- **"Agent mode requires --action"** (triage): add `--action export` (or `reply`/`copy`) with `--agent --chat-id`.
- **Connection error**: Beeper Desktop must be running locally. If it persists, treat Beeper as unavailable.
- **Large JSON output**: use `--max-chats` or pipe through `jq` to filter.
````

- [ ] **Step 2: Verify frontmatter and key content**

Run:
```bash
cd /home/bosire/projects/personal/ai-toolkit
head -4 plugins/ai-toolkit/skills/beeper/SKILL.md
grep -c "beeper-triage triage" plugins/ai-toolkit/skills/beeper/SKILL.md
```
Expected: frontmatter shows `name: beeper`; the stale `beeper-triage triage` string count is `0`.

- [ ] **Step 3: Commit**

```bash
git add plugins/ai-toolkit/skills/beeper/SKILL.md
git commit -m "feat(skill): rewrite beeper skill as MCP-vs-CLI-vs-api routing table"
```

---

## Task 3: Verify documented verbs against the real CLI

Every verb named in the table must exist in `beeper --help`. This catches drift between the skill and the shipped CLI.

**Files:** none (verification only)

- [ ] **Step 1: Cross-check verbs**

Run:
```bash
cd /home/bosire/projects/personal/beeper-triage
command beeper --help 2>/dev/null || python -m beeper_triage.cli --help
```
Expected: output lists `triage`, `new-chat`, `send`, `react`, `mark-read`, `mark-unread`, `start`, `edit`, `delete`, `dl`, `api` — i.e. every verb in the skill's table. If any verb is missing, Phase 3 isn't merged yet — stop and resolve before finishing.

- [ ] **Step 2: Spot-check one read path end-to-end (optional, needs Beeper up)**

Run: `cd /home/bosire/projects/personal/beeper-triage && command beeper api GET /v1/accounts --agent | jq 'length'`
Expected: a number ≥ 1 (accounts returned as raw JSON), proving the passthrough documented in the skill works.

- [ ] **Step 3: No commit** (verification only). If Step 1 revealed missing verbs, fix the skill table to match reality and commit that fix.

---

## Update the cross-phase tracker (after merge)

In `tasks/2026-06-02-beeper-cli-redesign/index.md`, mark Phases 3 & 4 done, and update the memory note that the ai-toolkit `beeper-triage` skill is now `beeper`.

## Self-Review (completed by plan author)

- **Spec coverage:** routing decision table ✅ (Task 2), MCP-up check ✅ (Task 2), connection/auth note ✅ (Task 2 gotchas), "CLI can do everything" standing rule ✅. All Component-3 spec items covered.
- **Placeholders:** none — the full SKILL.md content is inline.
- **Consistency:** the verb table in the skill matches the verbs registered across Phases 1–3 (`verbs.py` + cli.py); Task 3 enforces this against `beeper --help`. Skill `name: beeper` matches the renamed directory from Task 1.
