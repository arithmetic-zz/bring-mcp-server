# Bring! MCP Server

A Model Context Protocol (MCP) server for the Bring! shopping list API.

## What it does

This server exposes Bring! shopping list operations to MCP clients such as Claude Desktop or Cursor.

## Available tools

- `get_lists` — list all shopping lists
- `get_list` — fetch a single list with its items
- `add_item` — add an item to a list
- `remove_item` — remove an item from a list
- `complete_item` — mark an item as completed
- `batch_update` — apply bulk list updates

## APIs and dependencies

### External APIs / services

- **Bring! Shopping List API** — used through the `bring-api` Python package
- **MCP (Model Context Protocol)** — served via the `mcp` Python package
- **aiohttp** — async HTTP client used by `bring-api`
- **python-dotenv** — optional `.env` loading for local development

## Installation

```bash
pip install -r requirements.txt
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

## Run

```bash
python server.py
```

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

## Development

```bash
python -m py_compile server.py
python -m unittest
```

## Notes

- The server keeps one authenticated Bring client per process and reuses the HTTP session.
- Errors are translated into short user-facing messages.

## License

MIT
