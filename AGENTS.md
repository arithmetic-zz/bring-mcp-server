# Repository Guidelines

## Project Structure & Module Organization

- `server.py` contains the async stdio MCP server, Bring! client lifecycle, tool dispatch, resources, and prompts.
- `tests/test_server.py` contains unit tests and fake Bring clients/sessions.
- `tests/test_mcp_protocol.py` exercises MCP validation and responses through an in-memory client/server connection with mocked Bring access.
- `requirements.txt` lists runtime dependencies; `.env.example` documents required credentials.
- `bring-mcp-server.spec`, `requirements-build.txt` and `scripts/` build, package and smoke-test standalone downloads; `.github/workflows/release.yml` publishes them after all four targets pass.
- `.github/workflows/tests.yml` runs CI checks. `README.md` covers setup; this file is the shared contributor and agent guide.

## Build, Test, and Development Commands

Use Python 3.11+ and a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

Configure credentials before starting the server. It communicates over stdio and is normally launched by an MCP client.

- `.venv/bin/python -m py_compile server.py` — check source syntax.
- `.venv/bin/python -m PyInstaller --noconfirm --clean bring-mcp-server.spec` — build a standalone executable after installing `requirements-build.txt`.
- `.venv/bin/python -m unittest discover -s tests -v` — run the unit suite.
- `npx @modelcontextprotocol/inspector .venv/bin/python server.py` — inspect MCP tools, resources, and prompts interactively.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, `snake_case` functions/variables, `PascalCase` classes, and uppercase module constants. Use type annotations in server code and `async`/`await` for network operations. Keep tool descriptions and user-facing messages in English. No formatter or linter is configured.

Keep logging configuration inside `main()` so imports do not override the test runner's logging.

## Architecture & Compatibility

- Keep `mcp<2` until the server is migrated: MCP 2 removes the handler decorators used here.
- Reuse `get_bring_client()`: it caches the authenticated client/session under `_BRING_LOCK`. Close through `close_bring_client()` on shutdown and authentication failure so the next call logs in again.
- Convert API dataclasses through `_as_dict()` before dictionary access. `_load_lists()` refreshes `_LIST_NAME_CACHE`; `_cached_list_name()` returns a name or UUID without fetching. `_get_list_payload()` lazily warms missing names and shares the tool/resource JSON shape. Closing the client clears the cache.
- Keep `list_tools()` schemas aligned with `execute_tool()`, including argument aliases. `call_tool()` returns `CallToolResult`: friendly `TextContent` with `isError=True` on failure, `False` on success. Single-item mutations include a second JSON text block and matching `structuredContent` with status and UUID.
- Preserve `_item_name()` aliases (`item_id`, `item_name`, `item`), `add_item`'s canonical `item_uuid` and legacy `uuid`, and `list_uuid` precedence over the alternative exact `list_name`. Require at least one list selector; reject ambiguous names.
- Map batch operations (`ADD`, `COMPLETE`, `REMOVE`) to `BringItemOperation`. Single-item removal/completion must use dedicated client methods.
- Batch items may override the global operation (default `ADD`); preflight the whole request before one bulk write. `ATTRIBUTE_UPDATE` uses singular `attribute` entries with validated `{type, content}`; never silently drop nonempty attributes.
- `dry_run` on mutations shares real resolution/validation but must send no writes. History ADD returns `restored` while preserving the UUID. Explicit UUID/name mismatches must fail clearly rather than silently rename or ignore the requested name.
- `get_lists` and `search_items` return structured content alongside text/JSON. Cross-list search uses the existing normalization and reports list identity for every match; a failed list read must not look like a complete search.
- Resources expose `bring://lists` and `bring://lists/{listUuid}`. Discovery must fall back to the index without credentials/network; unknown resource reads raise `ValueError`. Tool and resource authentication failures must clear client/session/name caches before the next request.
- Keep `meal_plan` and `weekly_groceries` prompts explicit: call `get_list` before `batch_update` with `ADD`.

## Testing Guidelines

Tests use standard-library `unittest`, `IsolatedAsyncioTestCase`, and `AsyncMock`. Name test files `test_*.py` and methods `test_<behavior>`. Mock Bring/network access so tests require no live credentials. Cover changed tool schemas, response formatting, error handling, and client/session reuse as applicable. No numerical coverage threshold is configured. CI checks Python 3.11, 3.12, and 3.13.

Run one test with `.venv/bin/python -m unittest tests.test_server.BringServerTests.test_batch_update_uses_enum`. If Inspector hides error output, debug with a script calling `server.execute_tool()` directly after `load_dotenv()`.

## Commit & Pull Request Guidelines

The available local history uses imperative subjects, for example `Add MCP resources, prompts, and dataclass coercion (v1.1.0)`. Use concise, descriptive subjects. PRs should explain the behavior change, link relevant issues, and report syntax/unit-test results. Include MCP request/response examples when changing the public interface.

## Security & Configuration

Copy `.env.example` to `.env` and set `BRING_EMAIL` and `BRING_PASSWORD`. Keep `.env` ignored; never commit real credentials or include them in logs, fixtures, or PR examples.

Source runs load `.env` beside `server.py`; frozen downloads load it beside `sys.executable`. Packaging must keep an explicit public-file allowlist and include Bring locale data and third-party licenses. Never bundle `.env`, logs or local configuration. Release executables use no developer signing certificate or notarization.
