"""Bring! MCP server.

This server exposes the Bring! shopping list API to MCP clients such as
Claude Desktop or Cursor.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp
from bring_api import Bring
from bring_api.bring import BringItemOperation
from bring_api.exceptions import (
    BringAuthException,
    BringException,
    BringParseException,
    BringRequestException,
)
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SERVER = Server("bring-mcp-server")
_BRING_LOCK = asyncio.Lock()
_BRING_CLIENT: Bring | None = None
_BRING_SESSION: aiohttp.ClientSession | None = None


def _require_credentials() -> tuple[str, str]:
    email = os.environ.get("BRING_EMAIL")
    password = os.environ.get("BRING_PASSWORD")

    if not email or not password:
        raise ValueError("BRING_EMAIL and BRING_PASSWORD must be set")

    return email, password


async def get_bring_client() -> Bring:
    """Return a cached Bring client and initialize it on first use."""
    global _BRING_CLIENT, _BRING_SESSION

    async with _BRING_LOCK:
        if _BRING_CLIENT is not None:
            return _BRING_CLIENT

        email, password = _require_credentials()
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        client = Bring(session, email, password)

        try:
            await client.login()
        except Exception:
            await session.close()
            raise

        _BRING_SESSION = session
        _BRING_CLIENT = client
        return client


async def close_bring_client() -> None:
    """Close the cached Bring client and its session."""
    global _BRING_CLIENT, _BRING_SESSION

    async with _BRING_LOCK:
        if _BRING_SESSION is not None and not _BRING_SESSION.closed:
            await _BRING_SESSION.close()

        _BRING_CLIENT = None
        _BRING_SESSION = None


def _text(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]


def _item_name(arguments: dict[str, Any]) -> str:
    value = arguments.get("item_id") or arguments.get("item_name") or arguments.get("item")
    if not value:
        raise ValueError("item_id is required")
    return str(value)


def _list_uuid(arguments: dict[str, Any]) -> str:
    value = arguments.get("list_uuid")
    if not value:
        raise ValueError("list_uuid is required")
    return str(value)


@SERVER.list_tools()
async def list_tools() -> list[Tool]:
    """Return all available tools."""
    return [
        Tool(
            name="get_lists",
            description="List all Bring! shopping lists.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_list",
            description="Return a single Bring! shopping list with its items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    }
                },
                "required": ["list_uuid"],
            },
        ),
        Tool(
            name="add_item",
            description="Add an item to a shopping list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Item name, for example 'Milk'",
                    },
                    "spec": {
                        "type": "string",
                        "description": "Optional specification, for example 'low-fat'",
                    },
                    "uuid": {
                        "type": "string",
                        "description": "Optional UUID for the item",
                    },
                },
                "required": ["list_uuid", "item_id"],
            },
        ),
        Tool(
            name="remove_item",
            description="Remove an item from a shopping list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Item name or identifier",
                    },
                    "item_uuid": {
                        "type": "string",
                        "description": "Optional UUID of the item",
                    },
                },
                "required": ["list_uuid", "item_id"],
            },
        ),
        Tool(
            name="complete_item",
            description="Mark an item as completed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Item name or identifier",
                    },
                    "spec": {
                        "type": "string",
                        "description": "Optional specification",
                    },
                    "item_uuid": {
                        "type": "string",
                        "description": "Optional UUID of the item",
                    },
                },
                "required": ["list_uuid", "item_id"],
            },
        ),
        Tool(
            name="batch_update",
            description="Apply a batch operation to multiple items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    },
                    "items": {
                        "type": "array",
                        "description": "Items to update",
                        "items": {
                            "type": "object",
                            "properties": {
                                "itemId": {"type": "string"},
                                "spec": {"type": "string"},
                                "uuid": {"type": "string"},
                            },
                            "required": ["itemId"],
                        },
                    },
                    "operation": {
                        "type": "string",
                        "description": "ADD, COMPLETE, or REMOVE",
                        "enum": ["ADD", "COMPLETE", "REMOVE"],
                    },
                },
                "required": ["list_uuid", "items", "operation"],
            },
        ),
    ]


async def execute_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    bring = await get_bring_client()

    if name == "get_lists":
        response = await bring.load_lists()
        lists = response.get("lists", [])
        if not lists:
            return _text("No shopping lists found.")

        lines = ["Your shopping lists:"]
        lines.extend(f"• {lst['name']} (UUID: {lst['listUuid']})" for lst in lists)
        return _text("\n".join(lines))

    if name == "get_list":
        list_uuid = _list_uuid(arguments)
        response = await bring.get_list(list_uuid)
        items = response.get("items", [])
        list_name = response.get("name", "Untitled list")

        if not items:
            return _text(f"List '{list_name}': no items.")

        lines = [f"List: {list_name}", "-" * 40]
        for item in items:
            done = " ✓" if item.get("status") == "DONE" else ""
            spec = f" ({item.get('spec', '')})" if item.get("spec") else ""
            item_uuid = f" [UUID: {item.get('uuid', '')}]" if item.get("uuid") else ""
            lines.append(f"• {item.get('itemId', '?')}{spec}{item_uuid}{done}")

        return _text("\n".join(lines))

    if name == "add_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        spec = arguments.get("spec") or ""
        item_uuid = arguments.get("uuid")
        await bring.save_item(list_uuid, item_id, spec, item_uuid)
        return _text(f"✓ Added '{item_id}' to the list.")

    if name == "remove_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        item_uuid = arguments.get("item_uuid")
        await bring.remove_item(list_uuid, item_id, item_uuid)
        return _text(f"✓ Removed '{item_id}' from the list.")

    if name == "complete_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        spec = arguments.get("spec") or ""
        item_uuid = arguments.get("item_uuid")
        await bring.complete_item(list_uuid, item_id, spec, item_uuid)
        return _text(f"✓ Marked '{item_id}' as completed.")

    if name == "batch_update":
        list_uuid = _list_uuid(arguments)
        items = arguments.get("items", [])
        operation_name = str(arguments.get("operation", "ADD")).upper()
        try:
            operation = BringItemOperation[operation_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported batch operation: {operation_name}") from exc

        await bring.batch_update_list(list_uuid, items, operation)
        return _text(f"✓ Batch operation '{operation.name}' completed for {len(items)} item(s).")

    raise ValueError(f"Unknown tool: {name}")


@SERVER.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Execute a tool and translate known Bring errors into user-friendly text."""
    try:
        return await execute_tool(name, dict(arguments or {}))
    except BringAuthException:
        logger.exception("Bring authentication failed for tool '%s'", name)
        return _text("Authentication failed. Check BRING_EMAIL and BRING_PASSWORD.")
    except BringRequestException:
        logger.exception("Bring API request failed for tool '%s'", name)
        return _text("Bring API request failed.")
    except BringParseException:
        logger.exception("Bring API returned invalid data for tool '%s'", name)
        return _text("Bring API returned invalid data.")
    except BringException:
        logger.exception("Bring API error for tool '%s'", name)
        return _text("Bring API error.")
    except Exception:
        logger.exception("Unexpected error while handling tool '%s'", name)
        return _text("Unexpected server error.")


async def main() -> None:
    """Start the MCP server."""
    logger.info("Starting Bring! MCP server...")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await SERVER.run(
                read_stream,
                write_stream,
                SERVER.create_initialization_options(),
            )
    finally:
        await close_bring_client()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
