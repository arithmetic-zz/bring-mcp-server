# Bring! MCP Server

A small [Model Context Protocol](https://modelcontextprotocol.io/) server for the Bring! shopping list API.

It exposes Bring! shopping lists to MCP clients such as Codex, Claude Desktop, Cursor, or any client that can launch a stdio MCP server.

## Available tools

- `get_lists` — list all Bring! shopping lists and their UUIDs
- `get_list` — fetch one shopping list and show to-buy plus recently completed items
- `add_item` — add or update an item on a list
- `remove_item` — remove an item from a list
- `complete_item` — move an item to Recently Purchased (complete it in Bring!)
- `batch_update` — apply bulk operations (`ADD`, `COMPLETE`, or `REMOVE`) to multiple items

Single-item tools accept `item_id` (preferred), `item_name`, or `item` for the item name, in that order of precedence. Tool failures return a short message with the MCP `isError` flag set to `true`.

## Available resources

- `bring://lists` — JSON index of every shopping list
- `bring://lists/{listUuid}` — JSON contents of a single list (purchase + recently sections)

Clients such as Claude Desktop expose these in their "attach resource" picker.

## Available prompts

- `meal_plan` — plan a meal and stage missing ingredients on a target list
- `weekly_groceries` — draft a balanced weekly grocery list and stage it on a target list

## Requirements

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

The server loads `.env` from the project directory even when launched by a client from another working directory. Existing environment variables take precedence over `.env` values.

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

## Notes

- The server keeps one authenticated Bring client per process and reuses the HTTP session.
- Authentication failures during tool or resource access clear the cached client and session, allowing the next request to log in again. Resource discovery falls back to the lists index when credentials or network access are unavailable.
- `get_list` follows the current `bring-api` response shape: active items are under `purchase`, completed/recent items under `recently`.
- `save_item` (used by `add_item`) is an upsert — adding an item that already exists updates it rather than creating a duplicate.
- `complete_item` moves items to the "Recently Purchased" list; it does not check items off in place.
- Known Bring/API/input errors are caught, logged, and translated into short user-facing messages.

## License

MIT
