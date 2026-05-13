"""
Bring! MCP Server

Model Context Protocol Server für die Bring! Einkaufslisten-API.
Ermöglicht MCP-Clients (Claude Desktop, Cursor, etc.) mit Bring! zu interagieren.
"""

import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from bring_api import Bring
from bring_api.bring import BringItemOperation
import aiohttp

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Server-Instanz
SERVER = Server("bring-mcp-server")


def get_bring_client() -> Bring:
    """Erstellt einen Bring-Client mit Umgebungsvariablen."""
    email = os.environ.get("BRING_EMAIL")
    password = os.environ.get("BRING_PASSWORD")
    
    if not email or not password:
        raise ValueError("BRING_EMAIL und BRING_PASSWORD müssen gesetzt sein")
    
    session = aiohttp.ClientSession()
    return Bring(session, email, password)


async def bring_login() -> Bring:
    """Meldet sich bei der Bring-API an."""
    bring = get_bring_client()
    await bring.login()
    return bring


# ============ TOOL DEFINITIONS ============

@SERVER.list_tools()
async def list_tools() -> list[Tool]:
    """Gibt alle verfügbaren Tools zurück."""
    return [
        Tool(
            name="get_lists",
            description="Gibt alle verfügbaren Einkaufslisten zurück.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_list",
            description="Gibt eine spezifische Einkaufsliste mit allen Items zurück.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID der Einkaufsliste"
                    }
                },
                "required": ["list_uuid"]
            }
        ),
        Tool(
            name="add_item",
            description="Fügt ein Item zu einer Einkaufsliste hinzu.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID der Einkaufsliste"
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Name des Items (z.B. 'Milch')"
                    },
                    "spec": {
                        "type": "string",
                        "description": "Spezifikation (z.B. 'fettarm')",
                        "default": None
                    },
                    "uuid": {
                        "type": "string",
                        "description": "Eindeutige UUID für das Item",
                        "default": None
                    }
                },
                "required": ["list_uuid", "item_id"]
            }
        ),
        Tool(
            name="remove_item",
            description="Entfernt ein Item aus einer Einkaufsliste.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID der Einkaufsliste"
                    },
                    "item_uuid": {
                        "type": "string",
                        "description": "UUID des Items"
                    },
                    "item_id": {
                        "type": "string",
                        "description": "ID/Name des Items"
                    }
                },
                "required": ["list_uuid", "item_uuid", "item_id"]
            }
        ),
        Tool(
            name="complete_item",
            description="Markiert ein Item als erledigt.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID der Einkaufsliste"
                    },
                    "item_uuid": {
                        "type": "string",
                        "description": "UUID des Items"
                    },
                    "item_id": {
                        "type": "string",
                        "description": "ID/Name des Items"
                    }
                },
                "required": ["list_uuid", "item_uuid", "item_id"]
            }
        ),
        Tool(
            name="batch_update",
            description="Führt eine Batch-Operation auf einer Liste aus (mehrere Items gleichzeitig).",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID der Einkaufsliste"
                    },
                    "items": {
                        "type": "array",
                        "description": "Liste von Items",
                        "items": {
                            "type": "object",
                            "properties": {
                                "itemId": {"type": "string"},
                                "spec": {"type": "string"},
                                "uuid": {"type": "string"}
                            },
                            "required": ["itemId"]
                        }
                    },
                    "operation": {
                        "type": "string",
                        "description": "ADD, COMPLETE, oder REMOVE",
                        "enum": ["ADD", "COMPLETE", "REMOVE"]
                    }
                },
                "required": ["list_uuid", "items", "operation"]
            }
        )
    ]


@SERVER.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Führt ein Tool aus."""
    try:
        bring = await bring_login()
        
        if name == "get_lists":
            response = await bring.load_lists()
            lists = response.get("lists", [])
            if not lists:
                return [TextContent(type="text", text="Keine Einkaufslisten gefunden.")]
            
            text = "Deine Einkaufslisten:\n"
            text += "\n".join([f"• {lst['name']} (UUID: {lst['listUuid']})" for lst in lists])
            return [TextContent(type="text", text=text)]
        
        elif name == "get_list":
            list_uuid = arguments.get("list_uuid")
            response = await bring.get_list(list_uuid)
            items = response.get("items", [])
            
            list_name = response.get("name", "Unbekannte Liste")
            if not items:
                return [TextContent(type="text", text=f"Liste '{list_name}': keine Items")]
            
            text = f"Liste: {list_name}\n"
            text += "-" * 40 + "\n"
            for item in items:
                done = " ✓" if item.get("status") == "DONE" else ""
                spec = f" ({item.get('spec', '')})" if item.get('spec') else ""
                uuid = f" [UUID: {item.get('uuid', '')}]" if item.get('uuid') else ""
                text += f"• {item.get('itemId', '?')}{spec}{uuid}{done}\n"
            
            return [TextContent(type="text", text=text)]
        
        elif name == "add_item":
            result = await bring.save_item(
                arguments.get("list_uuid"),
                arguments.get("item_id"),
                arguments.get("spec"),
                arguments.get("uuid")
            )
            return [TextContent(
                type="text",
                text=f"✓ Item '{arguments.get('item_id')}' zur Liste hinzugefügt"
            )]
        
        elif name == "remove_item":
            item = {
                "itemId": arguments.get("item_id"),
                "uuid": arguments.get("item_uuid")
            }
            await bring.batch_update_list(
                arguments.get("list_uuid"),
                item,
                BringItemOperation.REMOVE
            )
            return [TextContent(
                type="text",
                text=f"✓ Item '{arguments.get('item_id')}' aus der Liste entfernt"
            )]
        
        elif name == "complete_item":
            item = {
                "itemId": arguments.get("item_id"),
                "uuid": arguments.get("item_uuid")
            }
            await bring.batch_update_list(
                arguments.get("list_uuid"),
                item,
                BringItemOperation.COMPLETE
            )
            return [TextContent(
                type="text",
                text=f"✓ Item '{arguments.get('item_id')}' als erledigt markiert"
            )]
        
        elif name == "batch_update":
            op = BringItemOperation[arguments.get("operation", "ADD").upper()]
            await bring.batch_update_list(
                arguments.get("list_uuid"),
                arguments.get("items", []),
                op
            )
            return [TextContent(
                type="text",
                text=f"✓ Batch-Operation '{op.name}' für {len(arguments.get('items', []))} Item(s) ausgeführt"
            )]
        
        else:
            return [TextContent(type="text", text=f"Unbekanntes Tool: {name}")]
    
    except Exception as e:
        logger.error(f"Fehler bei Tool '{name}': {e}")
        return [TextContent(type="text", text=f"Fehler: {str(e)}")]


async def main():
    """Startet den MCP-Server."""
    logger.info("Starte Bring! MCP Server...")
    async with stdio_server() as (read_stream, write_stream):
        await SERVER.run(
            read_stream,
            write_stream,
            SERVER.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())