# Bring! MCP Server

Model Context Protocol (MCP) Server für die Bring! Einkaufslisten-API.

## Überblick

Dieser MCP-Server ermöglicht es MCP-Clients (Claude Desktop, Cursor, etc.) mit der Bring! Einkaufslisten-API zu interagieren.

## Features

- **get_lists** – Alle Einkaufslisten abrufen
- **get_list** – Eine spezifische Liste mit Items abrufen
- **add_item** – Item zur Liste hinzufügen
- **remove_item** – Item aus Liste entfernen
- **complete_item** – Item als erledigt markieren
- **batch_update** – Batch-Operationen (mehrere Items gleichzeitig)

## Installation

```bash
pip install -r requirements.txt
```

## Konfiguration

Umgebungsvariablen setzen:

```bash
export BRING_EMAIL="deine@email.de"
export BRING_PASSWORD="deinpasswort"
```

Oder in einer `.env`-Datei:

```
BRING_EMAIL=deine@email.de
BRING_PASSWORD=deinpasswort
```

## Starten

```bash
python server.py
```

## Claude Desktop Integration

Füge in `claude_desktop_config.json` hinzu:

```json
{
  "mcpServers": {
    "bring": {
      "command": "python",
      "args": ["/path/to/bring-mcp-server/server.py"],
      "env": {
        "BRING_EMAIL": "deine@email.de",
        "BRING_PASSWORD": "deinpasswort"
      }
    }
  }
}
```

## API-Endpunkte (als Referenz)

| Tool | Beschreibung |
|------|-------------|
| `get_lists` | Alle Listen |
| `get_list` | Liste mit UUID |
| `add_item` | Item hinzufügen |
| `remove_item` | Item entfernen |
| `complete_item` | Item erledigen |
| `batch_update` | Batch-Operation |

## Development

```bash
pip install -r requirements.txt
python server.py
```

## Lizenz

MIT