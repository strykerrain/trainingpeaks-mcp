# AGENTS.md

Orientation for AI agents working in this repository.

## What this is

`strykerrain/trainingpeaks-mcp` is a fork of
[JamsusMaximus/trainingpeaks-mcp](https://github.com/JamsusMaximus/trainingpeaks-mcp) — a Python
MCP (Model Context Protocol) server that exposes the TrainingPeaks web API as tools an AI
assistant can call. Package name is `tp-mcp` (see `pyproject.toml`), source lives under
`src/tp_mcp/`, and the server registers **67 tools** (verified at runtime via `tools/list`) in `src/tp_mcp/server.py`.

This fork is maintained to support a working endurance-coaching practice. The fork exists to
serve a real coaching workflow, so changes here are judged by whether they make athlete
planning and analysis work reliably — not by parity with upstream.

Remotes in a normal clone:

- `origin` / `fork` → `strykerrain/trainingpeaks-mcp`
- `upstream` → `JamsusMaximus/trainingpeaks-mcp`

## How it is consumed

This is **not an end-user app**. It is a dependency of Erik's coaching workflow, registered
user-level in `~/.copilot/mcp-config.json`
as the `trainingpeaks` server:

```json
"trainingpeaks": {
  "tools": ["*"],
  "type": "local",
  "command": "C:\\Users\\<you>\\trainingpeaks-mcp\\.venv\\Scripts\\tp-mcp.exe",
  "args": ["serve"]
}
```

Because registration is user-level, the server is available in **every** Copilot session
regardless of the repo that session is in. The binary points at the **main checkout**
(`C:\Users\<you>\trainingpeaks-mcp`), not at a worktree — editing code in a worktree does not
change the running server until the change lands on the checked-out branch of the main clone
and the MCP server restarts.

## Privacy note

**This repository is public.** Account names, athlete IDs, absolute local paths, and
anything else identifying belong in the maintainer's private notes, not here.
Placeholders below are deliberate — do not fill them in.

## Auth model

Authentication is a **browser session cookie**, `Production_tpAuth`, captured from a logged-in
TrainingPeaks browser session and stored in the OS keyring (Windows Credential Manager). If no
keyring is available, `src/tp_mcp/auth/` falls back to an encrypted file. The cookie is never
returned to the model; the TrainingPeaks domain is hardcoded in `src/tp_mcp/auth/browser.py`.

- Account: `<your TrainingPeaks coach account>`
- Coach athlete ID: `<your coach athlete ID>`
- Re-auth: `tp-mcp.exe auth --from-browser auto`
- Local check: `tp-mcp.exe auth-status`
- Clear stored cookie: `tp-mcp.exe auth-clear`

### Gotcha: `tp_auth_status` is not a connectivity test

`tp_auth_status` (`src/tp_mcp/tools/auth_status.py`) checks the **locally stored credential**
only. It can report a healthy-looking result while live API calls fail. Use **`tp_list_athletes`**
as the real end-to-end connectivity test — it exercises a genuine authenticated request.

Cookies expire on TrainingPeaks' schedule, not ours. **If tools start failing, re-authenticate
first** (`tp-mcp.exe auth --from-browser auto`) before debugging anything else. Most "the server
is broken" reports are an expired cookie.

## Coach athlete targeting

This is a coach account, so most tools accept an optional `athlete` argument (name or ID). In
`call_tool` (`src/tp_mcp/server.py`) that argument is popped from the tool arguments and set on
the `athlete_override` contextvar defined in `src/tp_mcp/client/context.py`, then reset in a
`finally` block. `TPClient.ensure_athlete_id` (`src/tp_mcp/client/http.py`) reads that contextvar
to resolve the target athlete, and **skips its class-level cache whenever an override is active**.

Consequences for anyone editing this code:

- Omitting `athlete` targets the coach's own calendar (athlete ID <your coach athlete ID>), not an athlete's.
- Never cache athlete-scoped results at class level without checking `athlete_override.get()`.
- The contextvar is per-tool-call. Don't leak it across awaits outside the handler.

## Tool inventory

Grouped by whether the tool mutates TrainingPeaks data. Derived from `src/tp_mcp/tools/`.

### Read (31)

| Area | Tools |
| --- | --- |
| Profile & auth | `tp_auth_status`, `tp_get_profile`, `tp_list_athletes` |
| Settings | `tp_get_athlete_settings`, `tp_get_pool_length_settings` |
| Workouts | `tp_get_workouts`, `tp_get_workout`, `tp_get_workout_comments`, `tp_get_workout_note`, `tp_get_workout_types`, `tp_download_workout_file` |
| Analysis | `tp_analyze_workout`, `tp_get_weekly_summary`, `tp_get_fitness`, `tp_get_atp`, `tp_get_peaks`, `tp_get_workout_prs`, `tp_validate_structure` |
| Health | `tp_get_metrics`, `tp_get_nutrition` |
| Equipment | `tp_get_equipment` |
| Events & calendar | `tp_get_events`, `tp_get_focus_event`, `tp_get_next_event`, `tp_get_note`, `tp_list_notes`, `tp_get_note_comments`, `tp_get_availability` |
| Library | `tp_get_libraries`, `tp_get_library_items`, `tp_get_library_item` |

`tp_validate_structure` is pure local validation of a workout structure payload — no API call.

### Write (34)

| Area | Tools |
| --- | --- |
| Auth state | `tp_refresh_auth` (rewrites the stored cookie) |
| Workouts | `tp_create_workout`, `tp_update_workout`, `tp_delete_workout`, `tp_copy_workout`, `tp_reorder_workouts`, `tp_add_workout_comment`, `tp_set_workout_note`, `tp_pair_workout`, `tp_unpair_workout`, `tp_upload_workout_file`, `tp_delete_workout_file` |
| Settings | `tp_update_ftp`, `tp_update_hr_zones`, `tp_update_speed_zones`, `tp_update_nutrition` |
| Health | `tp_log_metrics` |
| Equipment | `tp_create_equipment`, `tp_update_equipment`, `tp_delete_equipment` |
| Events & calendar | `tp_create_event`, `tp_update_event`, `tp_delete_event`, `tp_create_note`, `tp_update_note`, `tp_delete_note`, `tp_add_note_comment`, `tp_create_availability`, `tp_delete_availability` |
| Library | `tp_create_library`, `tp_delete_library`, `tp_create_library_item`, `tp_update_library_item`, `tp_schedule_library_workout` |

Write tools hit a **live production coaching account**. Do not call them for exploration or to
"test that it works." Use the test suite with mocked HTTP instead.

## Dev workflow

`uv.lock` is checked in, so `uv` is the intended dependency manager:

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

`uv` is not installed on every machine Erik uses. The equivalent, and what CI actually runs
(`.github/workflows/ci.yml`, Python 3.10–3.14), is a plain venv:

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
mypy src/
```

All three commands are verified working on this branch: **382 tests pass**, ruff clean, mypy
clean across 33 source files. Config lives in `pyproject.toml` (`asyncio_mode = "auto"`,
ruff line length 120, target py310).

Run the server locally:

```bash
tp-mcp serve          # or: python -m tp_mcp serve
tp-mcp help           # full command list
```

On Windows, from the main checkout: `.venv\Scripts\tp-mcp.exe serve`.

Note for worktree sessions: the venv is installed editable against the **main checkout's**
`src/`. To run this branch's tests against this branch's code, set `PYTHONPATH` to the
worktree's `src/` before invoking pytest, or the suite will silently test the other branch.

Tests live in `tests/` mirroring the package layout (`test_auth/`, `test_client/`, `test_tools/`)
and mock `httpx` — nothing in the suite touches the TrainingPeaks API.

## Known open item

Upstream **PR #115** by `evilbruce666` (Alexey Kalinin), *"feat(strength): structured
strength/gym workouts via Peaksware API"*, is **merged upstream but not merged into this fork**.

**This is not a straightforward sync.** This fork already implements the same feature
independently:

| Tool | Defined in | Added |
| --- | --- | --- |
| `tp_search_exercises` | `src/tp_mcp/tools/library.py:695` | `85f94a9` (2026-06-10) |
| `tp_create_strength_workout` | `src/tp_mcp/tools/library.py:1075` | `85f94a9` (2026-06-10) |

Both are registered in `server.py` and exported from `tools/__init__.py`, and both appear in
the live `tools/list` handshake. `7280262` (2026-06-10) recorded a render crash in the
exercise object, and `dbf1e01` (2026-06-30) added the missing `RX_API_BASE` constant; no
WIP or TODO markers remain in `library.py`.

So merging `upstream/main` means **reconciling two competing implementations of the same
feature**, not resolving a tool-registry conflict. Before doing it, decide which
implementation wins. If upstream's is better, this fork's version and its tests come out;
if this fork's is better, PR #115 needs to be excluded from the merge rather than
mechanically resolved.

Upstream may also carry a strength-workout **delete** path that this fork lacks — that has
not been verified against the PR diff.
