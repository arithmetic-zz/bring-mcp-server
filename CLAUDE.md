# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (the `mcp` package needs Python 3.11+; the system Python 3.9 on macOS is too old)
- Run server (stdio MCP): `.venv/bin/python server.py` — requires `BRING_EMAIL` and `BRING_PASSWORD` (read from env or `.env`)
- Syntax check: `.venv/bin/python -m py_compile server.py`
- Tests: `.venv/bin/python -m unittest discover -s tests`
- Single test: `.venv/bin/python -m unittest tests.test_server.BringServerTests.test_batch_update_uses_enum`
- Interactive smoke test: `npx @modelcontextprotocol/inspector .venv/bin/python server.py` (credentials read from `.env`)

## Architecture

Single-file MCP server (`server.py`) over stdio that wraps the `bring-api` Python client. Exposes **tools**, **resources**, and **prompts**.

- **Singleton Bring client**: `get_bring_client()` lazily logs in once and caches both the `Bring` client and its `aiohttp.ClientSession` in module globals (`_BRING_CLIENT`, `_BRING_SESSION`), guarded by `_BRING_LOCK`. The same session is reused across tool/resource calls. `close_bring_client()` runs in `main()`'s `finally` to close the session, and is also called by `call_tool` on `BringAuthException` so an expired token triggers a fresh login on the next call. Always route through `get_bring_client()` rather than constructing new clients.
- **Dataclass coercion at the boundary**: `bring-api` 1.x returns dataclasses (`BringListResponse`, `BringItemsResponse`, `BringPurchase`, ...). `_as_dict()` recursively converts them via `dataclasses.asdict` so the rest of the code (and the dict-based test mocks) can stay dict-shaped. Any new `bring-api` response must pass through `_as_dict` before `.get(...)` is called on it.
- **List-name cache**: `_LIST_NAME_CACHE` is (re)populated whenever `_load_lists()` runs. `_cached_list_name()` is pure-sync: it returns the cached name or falls back to the UUID. It never auto-fetches, so a cold `get_list` (no prior `get_lists`) renders the UUID instead of doubling the HTTP traffic. `close_bring_client()` clears the cache.
- **Tool dispatch**: `list_tools()` declares JSON schemas; `execute_tool()` is a flat `if name == ...` dispatcher; `call_tool()` wraps it and translates `BringAuthException` / `BringRequestException` / `BringParseException` / `BringException` (and any unexpected exception) into short user-facing `TextContent` messages — it never re-raises to the MCP client.
- **Resources**: `list_resources()` returns the `bring://lists` index plus one `bring://lists/{listUuid}` resource per existing list, so clients show them by name in their resource picker. It must keep working without credentials or network — it catches `ValueError`, `BringException`, `aiohttp.ClientError`, and `OSError` and falls back to just the index. `list_resource_templates()` advertises the `bring://lists/{listUuid}` pattern. `read_resource()` returns JSON for either URI and raises `ValueError` for unknown URIs (surfaced to the MCP client).
- **Prompts**: `meal_plan` and `weekly_groceries` return ready-made user-role prompts that explicitly tell the model to call `get_list` first, then `batch_update` (ADD). Keep prompts opinionated about *which* tools to call so the LLM has a clear playbook.
- **Argument normalization**: `_item_name()` accepts `item_id`, `item_name`, or `item`; `add_item` accepts both `item_uuid` (canonical) and `uuid` (legacy) for backward compatibility. `_list_uuid()` requires `list_uuid`. Preserve this leniency when editing handlers.
- **Batch operations**: `batch_update` maps the string `operation` ("ADD" / "COMPLETE" / "REMOVE") to `BringItemOperation` via enum lookup. Single-item `remove_item` / `complete_item` use the dedicated `bring.remove_item` / `bring.complete_item` methods (not `batch_update_list`) — there is a test enforcing this.

## Conventions

- Tool names, descriptions, and user-facing strings are English only — `test_list_tools_are_english` fails on German markers like "gibt", "fügt", "liste", "artikel".
- All Bring errors must be caught and converted to friendly text; do not let exceptions propagate out of `call_tool`.
- `logging.basicConfig` is called inside `main()`, not at import time, so importing `server` in tests doesn't override the test runner's logging config.
- The MCP Inspector swallows the server's stderr — to debug a tool failure, run `server.execute_tool()` directly from a script with `load_dotenv()`, not through the inspector.
