# Bring! MCP Server

A small [Model Context Protocol](https://modelcontextprotocol.io/) server for the Bring! shopping list API.

It exposes Bring! shopping lists to MCP clients such as Codex, Claude Desktop, Cursor, or any client that can launch a stdio MCP server.

## Available tools

- `get_lists` — list all Bring! shopping lists and their UUIDs, as text or JSON
- `get_list` — fetch one shopping list as text or JSON, optionally hiding Recently Purchased
- `search_items` — find items across all shopping lists with list and item identities
- `add_item` — add, update or restore an item; return its status and UUID
- `remove_item` — delete an item completely from a list; return its UUID
- `complete_item` — move an item to Recently Purchased, rather than checking it off in place; return its UUID
- `batch_update` — apply mixed ADD, COMPLETE and REMOVE operations

Single-item tools accept `item_id` (preferred), `item_name`, or `item` for the item name, in that order of precedence. Tool failures return a short message with the MCP `isError` flag set to `true`. Unknown parameters are rejected, including unknown fields inside batch items; validation errors identify the tool and parameter. `specification` is an alias for `spec`.

### Select a list and read its contents

All list-specific tools accept either `list_uuid` or the exact, case-sensitive `list_name`. At least one is required; `list_uuid` takes precedence when both are supplied. Names are cached after discovery. A name miss refreshes the cache once; unknown names report the available names, and duplicate names require a UUID. If a list was renamed, call `get_lists` to refresh cached names.

`get_lists` keeps its readable default text and also exposes the index as MCP `structuredContent`. Use `{"format":"json"}` to receive JSON text:

```json
{"lists":[{"name":"Home","list_uuid":"list-1"},{"name":"Hardware","list_uuid":"list-2"}]}
```

`get_list` defaults to the existing text output with both **To buy** and **Recently Purchased** sections. Set `include_recent` to `false` to omit history, or `format` to `"json"` for the same JSON shape as `bring://lists/{listUuid}`. Both options can be combined:

```json
{"list_name": "Home", "format": "json", "include_recent": false}
```

Example tool JSON response:

```json
{
  "listUuid": "list-1",
  "name": "Home",
  "items": {
    "purchase": [{"itemId": "Milk", "specification": "2 l", "uuid": "item-1"}],
    "recently": []
  }
}
```

`purchase` contains open items; `recently` is purchase history. Filtering history leaves `recently` as an empty array in JSON. Dates in item attributes (such as discount validity) are returned as ISO 8601 strings. A cold read lazily fetches list names once; a cached name avoids that extra request.

Use `filter` (or its alias `query`) for a substring lookup across item names, display names, specifications and UUIDs:

```json
{"list_name": "Home", "filter": "milk", "format": "json", "include_recent": false}
```

The text and JSON sections contain only matching items. Filtered JSON also includes `matches`, with `item_id`, `item_uuid`, `specification` and `status` (`purchase` or `recently`) for each hit. No hits produce empty sections and matches. Matching normalizes case, whitespace and German umlauts. `include_recent` accepts booleans and case-insensitive strings `"true"`/`"false"`; other values produce a contextual validation error.

To find an item without knowing its list, call `search_items`:

```json
{"filter":"milk","include_recent":false,"format":"json"}
```

It searches every accessible list with the same normalization and returns `{"matches":[...]}`. Each match includes `list_uuid`, `list_name`, `item_id`, `item_uuid`, `specification` and `status` (`purchase` or `recently`). The default is readable text; JSON is available through `format` and MCP `structuredContent` is always present. An empty search result is `{"matches":[]}`. If any list cannot be read, the call returns an error rather than presenting an incomplete search as complete.

### Add, remove and complete items

`add_item` is an upsert: a matching existing item is updated; a new item is added. `complete_item` moves the item to **Recently Purchased**; it does not check it off in place. `remove_item` deletes it completely. Batch `ADD`, `COMPLETE` and `REMOVE` have the same respective semantics.

Single-item changes retain a readable first text block and add a JSON text block, also available as MCP `structuredContent`:

```json
{"list_uuid": "list-1", "item_id": "Milk", "item_uuid": "item-1", "status": "updated"}
```

For `add_item`, `status` is `updated` when the target UUID was already in `purchase`, `restored` when it was in `recently`, and `created` for a new item. Restoring moves the existing UUID back to the open list and preserves the specification when omitted. Removal returns `removed`; completion returns `completed`. These statuses describe the state read immediately before the write; simultaneous edits by other Bring clients are not atomic with this check.

The server reads the items before a single-item change, reuses an existing UUID, or assigns a new UUID when adding a new item. Specify `item_uuid` to select among duplicate names or to create a separate same-name item with a new, valid UUID. New client-supplied UUIDs are validated before writing; existing legacy identifiers remain addressable. `add_item` also accepts legacy `uuid`; `item_uuid` takes precedence. An item-name argument remains required even when a UUID is supplied.

An explicit UUID selects an existing item; it does not rename it. If the supplied name differs from that item's canonical, display or normalized name, the request fails with an explicit unsupported-rename error before writing. This prevents a successful response from silently discarding the requested name change.

`item_id` is the base item name, while `spec` is its specification: `{"item_id":"Rotwein","spec":"SMS"}` is displayed as `Rotwein (SMS)`. That complete display name also resolves the existing item, without renaming it. Exact names are matched first, with a fallback that normalizes case, whitespace, German umlauts (including `ue`/`ü`/`u`) and `ß`/`ss`. Ambiguous matches are rejected with candidate UUIDs so the client can retry using `item_uuid`. This is normalization, not translation between languages.

Upsert matches by name or UUID, not by specification. An explicitly supplied `spec` replaces the existing specification; `""` clears it. Omitting it preserves the matched specification. To keep an already-open item completely unchanged, set `on_exists` to `"skip"`; the result has status `skipped`. `"error"` rejects an existing open item. The default `"update"` retains upsert behavior. A matching history item can be re-added. Name-only completion requires an open item, so typos cannot create history entries. Removing an unknown name also reports an error.

### Batch changes

Batch items accept `item_id` / legacy `itemId`, `item_uuid` / legacy `uuid`, and `spec` / `specification`. The first spelling in each pair takes precedence. Items with an empty `attributes` array can be passed back from `get_list`. Nonempty attributes are read-only and must be removed before submitting a mutation. For example:

```json
{"list_name":"Home","operation":"ADD","items":[{"item_id":"Milk","specification":"2 l"},{"itemId":"Bread","spec":"wholemeal"}]}
```

All items are resolved and validated before the batch write. Unknown or ambiguous REMOVE/COMPLETE targets reject the whole request before any mutation; COMPLETE requires open items and cannot create history entries for unknown names. A successful batch retains its readable text summary and adds a JSON text block plus matching `structuredContent`:

```json
{"list_uuid":"list-1","operation":"ADD","results":[{"item_id":"Milk","item_uuid":"item-1","operation":"ADD","status":"updated"},{"item_id":"Bread","item_uuid":"item-2","operation":"ADD","status":"created"}]}
```

The global `operation` defaults to `ADD`. Each item can override it, so additions, completions and removals can share one batch request:

```json
{"list_name":"Home","items":[{"item_id":"Milk"},{"item_id":"Bread","operation":"REMOVE"}]}
```

Each result reports its UUID, effective operation and actual `created`, `updated`, `restored`, `removed`, or `completed` status. The top-level result `operation` is `MIXED` when items use different operations, otherwise their effective operation. Each target may occur only once in a batch; duplicate targets are rejected before writing. All changes are sent through one bulk API request after successful preflight. Preflight and writes are separate API calls, so concurrent changes from other Bring clients can still race with this snapshot; batch validation does not provide a cross-client transaction.

### Preview changes

`add_item`, `remove_item`, `complete_item` and `batch_update` accept `dry_run: true`. The server reads and resolves the same targets and applies the same validation as a real call, but sends no writes. Preview results have the same JSON structure with `dry_run: true` added; the text identifies the result as a preview. For example:

```json
{"list_name":"Home","dry_run":true,"items":[{"item_id":"Milk"},{"item_id":"Bread","operation":"REMOVE"}]}
```

Like `include_recent`, `dry_run` accepts booleans and case-insensitive `"true"`/`"false"` strings. This is a preview, not a reservation: the list may change before execution. A preview of a new item may allocate a UUID that differs from a later call unless supplied explicitly.

### Item attributes are read-only

Bring returns attributes in list JSON, including ISO 8601 dates. Attribute **writes are disabled**: the v1.4.0 `ATTRIBUTE_UPDATE` payload is rejected by the live Bring API with HTTP 400. The previous transport-only tests did not establish that Bring accepted these writes.

Use the Bring app to edit attributes. `add_item` and batch items accept `attributes: []` for compatibility, but reject nonempty arrays before any network request. `ATTRIBUTE_UPDATE` is no longer advertised as an operation; old clients receive an explicit unsupported-feature error. A batch containing an unsupported attribute edit is rejected in full. `dry_run` applies exactly the same rejection and never sends a write. Omitting attributes does not request any attribute change.

### API error diagnostics

Failed API calls set MCP `isError: true`. HTTP failures include the status in the readable text and an `error` object in `structuredContent`, for example:

```json
{"error":{"http_status":400,"category":"invalid_request","retryable":false,"detail":"Invalid item payload","message":"Bring API request failed (HTTP 400). The request payload or parameters were rejected; correct the request before retrying. Invalid item payload"}}
```

- **400 / 422:** `invalid_request`; fix the payload or parameters before retrying.
- **401 / 403:** `authentication`; client, session and list-name caches are cleared so the next call logs in again. Check credentials if authentication keeps failing.
- **429:** `rate_limit`; `retryable: true`, with `retry_after` when Bring supplies a `Retry-After` header (seconds or an HTTP date).
- **5xx / connection errors / timeouts:** `temporary`; `retryable: true`. Network failures have `http_status: null`.
- Other HTTP errors and unclassified request failures use `request`, with `retryable: false`; the status is `null` when unavailable.

API response details are limited to 512 characters; JSON responses expose only a string `message`, `detail` or `error` field. Known credentials and common token formats are redacted. Raw response documents, headers other than `Retry-After`, and request URLs are not returned. HTML error pages and incomplete JSON bodies are omitted. Resource reads return the same readable diagnostics; resource discovery still falls back to the lists index when Bring is unavailable.

HTTP failures are surfaced before the Bring library's internal error parsing and retries, including during login. The server does not automatically replay a failed HTTP request. `retryable` describes the error category, not a guarantee that a write had no effect: read the list before repeating a write after a timeout or server error.

## Available resources

- `bring://lists` — JSON index of every shopping list
- `bring://lists/{listUuid}` — JSON contents of a single list (purchase + recently sections)

Clients such as Claude Desktop expose these in their "attach resource" picker.

## Available prompts

- `meal_plan` — plan a meal and stage missing ingredients on a target list
- `weekly_groceries` — draft a balanced weekly grocery list and stage it on a target list

## Download a standalone executable

Download the archive for your system from [GitHub Releases](https://github.com/arithmetic-zz/bring-mcp-server/releases/latest). These bundles include Python and the dependencies; no Python installation or `pip` setup is required.

| Download suffix | Target |
| --- | --- |
| `macos-arm64.tar.gz` | macOS 15+ on Apple Silicon |
| `macos-x86_64.tar.gz` | macOS 15+ on Intel |
| `linux-x86_64.tar.gz` | Ubuntu 22.04+ or compatible glibc-based Linux |
| `windows-x86_64.zip` | Windows 10/11, 64-bit Intel/AMD |

Extract the archive into a permanent folder, copy `.env.example` to `.env` beside `bring-mcp-server` (or `bring-mcp-server.exe` on Windows), and enter your Bring! credentials. Existing environment variables take precedence. `SHA256SUMS` provides checksums for the archives.

The downloads have no Developer ID/Authenticode signing or macOS notarization. macOS or Windows may ask you to explicitly approve the executable. The automatic tests cover MCP startup and protocol behavior on the build runners; verify live Bring access on your own machine.

For Codex, register the executable directly:

```bash
codex mcp add bring -- "/absolute/path/to/bring-mcp-server"
```

On Windows, use the full path to `bring-mcp-server.exe`. For Claude Desktop, set `command` to the executable's absolute path and `args` to `[]`. Keep `.env` beside the executable, not in the client's working directory.

## Requirements for running from source

- Python 3.11+
- A Bring! account
- Environment variables for your Bring! login

Dependencies are declared in [requirements.txt](requirements.txt). The server uses MCP SDK 1.x (`mcp>=1.30.0,<2`); MCP 2 removes the handler decorators used here and requires a code migration.

## Installation

```bash
git clone https://github.com/arithmetic-zz/bring-mcp-server.git
cd bring-mcp-server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Configuration

Set these environment variables:

```bash
export BRING_EMAIL="your@email.com"
export BRING_PASSWORD="your-password"
```

Or copy the example to a local `.env` file and replace the placeholder values:

```bash
cp .env.example .env
```

```env
BRING_EMAIL=your@email.com
BRING_PASSWORD=your-password
```

`.env` is ignored by Git. Do not commit real Bring! credentials.

When running from source, the server loads `.env` beside `server.py`, even when launched from another working directory. Standalone downloads load it beside the executable. Existing environment variables take precedence over `.env` values.

## Run

```bash
.venv/bin/python server.py
```

The server communicates over stdio, so it is normally launched by an MCP client instead of being run interactively.

## Claude Desktop integration

```json
{
  "mcpServers": {
    "bring": {
      "command": "/path/to/bring-mcp-server/.venv/bin/python",
      "args": ["/path/to/bring-mcp-server/server.py"],
      "env": {
        "BRING_EMAIL": "your@email.com",
        "BRING_PASSWORD": "your-password"
      }
    }
  }
}
```

Replace `/path/to/bring-mcp-server` in both paths with your checkout's absolute path. If credentials are already configured in the project's `.env`, omit the `env` object.

## Codex integration

After installing the dependencies, configure `BRING_EMAIL` and `BRING_PASSWORD` in the checkout's `.env` file. Register the server with the Codex CLI:

```bash
codex mcp add bring -- \
  "/path/to/bring-mcp-server/.venv/bin/python" \
  "/path/to/bring-mcp-server/server.py"
```

Replace `/path/to/bring-mcp-server` with your checkout's absolute path. The server loads the project's `.env`; no credentials need to be included in the command.

Alternatively, add this entry to `~/.codex/config.toml`:

```toml
[mcp_servers.bring]
command = "/path/to/bring-mcp-server/.venv/bin/python"
args = ["/path/to/bring-mcp-server/server.py"]
cwd = "/path/to/bring-mcp-server"
```

Use either method, then start a new Codex session or restart the IDE extension. Run `codex mcp list` to check registration and `/mcp` inside the Codex CLI to inspect active servers. Ask Codex to list your Bring! shopping lists to verify the connection.

The CLI and IDE extension share this configuration. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp) for more options.

## Development

```bash
.venv/bin/python -m py_compile server.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

GitHub Actions runs dependency, compile, and test checks on Python 3.11, 3.12, and 3.13 for pushes and pull requests targeting `main`.

Tests mock Bring API access and require no credentials. `tests/test_server.py` covers server behavior; `tests/test_mcp_protocol.py` connects an MCP client and server over in-memory streams to test schema validation, error flags, and authentication recovery. These tests do not verify live login or changes to actual shopping lists.

For interactive MCP testing, use the Inspector (requires Node.js/npm):

```bash
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

With valid credentials, verify `get_lists` and `get_list` first. On a chosen list, add uniquely named test items using `add_item` or `batch_update` (`ADD`), read them back, then delete them with `remove_item` and confirm they are absent. These calls modify real Bring data; clean up only the items created for the test.

Contributor conventions and architecture details are in [AGENTS.md](AGENTS.md).

## Building release downloads

Build on the target OS and architecture:

```bash
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm --clean bring-mcp-server.spec
.venv/bin/python scripts/package_release.py --binary dist/bring-mcp-server --version v1.4.0 --system macos --arch arm64
.venv/bin/python scripts/smoke_test.py --archive dist/release/bring-mcp-server-v1.4.0-macos-arm64.tar.gz
```

Adjust the version, system (`macos`, `linux`, `windows`), architecture and executable suffix for your target. The package contains the executable, this README, `.env.example`, the project license and third-party license texts. The real `.env` is never included.

The `Release binaries` workflow builds all four targets when a `v*` tag is pushed. It can also be run manually for an existing version tag. Every target runs the source tests and an MCP smoke test against its extracted archive. The release is published with downloads and checksums only after all targets succeed. Publishing expects that the version has no existing GitHub Release; it does not overwrite releases or move tags.

## Notes

- The server keeps one authenticated Bring client per process and reuses the HTTP session.
- Authentication failures during tool or resource access clear the cached client and session, allowing the next request to log in again. Resource discovery falls back to the lists index when credentials or network access are unavailable.
- `get_list` follows the current `bring-api` response shape: active items are under `purchase`, completed/recent items under `recently`.
- `save_item` (used by `add_item`) is an upsert — adding an item that already exists updates it rather than creating a duplicate.
- `complete_item` moves items to the "Recently Purchased" list; it does not check items off in place.
- Known Bring/API/input errors are caught, logged, and translated into short user-facing messages.

## License

MIT
