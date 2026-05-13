# Bring! MCP Server

A small [Model Context Protocol](https://modelcontextprotocol.io/) server for the Bring! shopping list API.

It exposes Bring! shopping lists to MCP clients such as Claude Desktop, Cursor, or any client that can launch a stdio MCP server.

## Available tools

- `get_lists` — list all Bring! shopping lists and their UUIDs
- `get_list` — fetch one shopping list and show to-buy plus recently completed items
- `add_item` — add or update an item on a list
- `remove_item` — remove an item from a list
- `complete_item` — move an item to Recently Purchased (complete it in Bring!)
- `batch_update` — apply bulk operations (`ADD`, `COMPLETE`, or `REMOVE`) to multiple items

## Requirements

- Python 3.11+
- A Bring! account
- Environment variables for your Bring! login

## Installation

```bash
git clone https://github.com/arithmetic-zz/bring-mcp-server.git
cd bring-mcp-server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configuration

Set these environment variables:

```bash
export BRING_EMAIL="your@email.com"
export BRING_PASSWORD="your-password"
```

Or create a local `.env` file:

```env
BRING_EMAIL=your@email.com
BRING_PASSWORD=your-password
```

`.env` is ignored by Git. Do not commit real Bring! credentials.

## Run

```bash
python server.py
```

The server communicates over stdio, so it is normally launched by an MCP client instead of being run interactively.

## Claude Desktop integration

```json
{
  "mcpServers": {
    "bring": {
      "command": "python",
      "args": ["/path/to/bring-mcp-server/server.py"],
      "env": {
        "BRING_EMAIL": "your@email.com",
        "BRING_PASSWORD": "your-password"
      }
    }
  }
}
```

If you use the virtual environment from the installation steps, set `command` to `/path/to/bring-mcp-server/.venv/bin/python`.

## Development

```bash
python -m py_compile server.py
python -m unittest discover -s tests -v
```

GitHub Actions runs the same compile and unit-test checks on Python 3.11 and 3.12 for pushes and pull requests.

## Notes

- The server keeps one authenticated Bring client per process and reuses the HTTP session.
- `get_list` follows the current `bring-api` response shape: active items are under `purchase`, completed/recent items under `recently`.
- `save_item` (used by `add_item`) is an upsert — adding an item that already exists updates it rather than creating a duplicate.
- `complete_item` moves items to the "Recently Purchased" list; it does not check items off in place.
- Known Bring/API/input errors are caught, logged, and translated into short user-facing messages.

## License

MIT
