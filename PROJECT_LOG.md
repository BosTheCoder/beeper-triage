## 2026-03-06: Update Beeper WSL proxy port to 23374

### Issue
- `beeper-triage` stopped working after `beeper-wsl-proxy` moved from port `23373` to `23374`.
- Runtime config and setup docs still pointed at `http://172.28.96.1:23373`, causing Beeper API connection failures.

### Solution
- Updated `BEEPER_BASE_URL` in `.env` to `http://172.28.96.1:23374`.
- Updated setup/config documentation to match the new port so future environment setup is consistent.
- Kept code unchanged because `beeper_triage/cli.py` already correctly reads `BEEPER_BASE_URL` from environment and passes it to `BeeperClient`.

### Files Changed
- `.env`
- `README.md`
- `CLAUDE.md`
- `PROJECT_LOG.md`

### Key Technical Notes
- `beeper-triage` base URL is environment-driven via `BEEPER_BASE_URL` in `beeper_triage/cli.py`.
- This fix is an environment/config alignment change, not an application logic change.

### Testing
- Ran `pytest -q tests/test_cli.py` in `.venv`.
- Result: `13 passed, 1 failed`.
- Failure observed in `test_pick_action_invalid_then_valid` expects option `3` to be invalid, but current CLI behavior maps `3` to `export`; this appears unrelated to the port update.

---
