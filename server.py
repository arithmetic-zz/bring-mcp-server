"""Bring! MCP server.

This server exposes the Bring! shopping list API to MCP clients such as
Claude Desktop or Cursor.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import ssl
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import certifi
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
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    ResourceTemplate,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

def _load_environment() -> None:
    """Load local configuration beside the script or frozen executable."""
    entrypoint = sys.executable if getattr(sys, "frozen", False) else __file__
    load_dotenv(Path(entrypoint).resolve().parent / ".env")


_load_environment()

logger = logging.getLogger(__name__)

SERVER = Server("bring-mcp-server")
_BRING_LOCK = asyncio.Lock()
_BRING_CLIENT: Bring | None = None
_BRING_SESSION: aiohttp.ClientSession | None = None
_LIST_NAME_CACHE: dict[str, str] = {}

LISTS_INDEX_URI = "bring://lists"


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
        if _BRING_CLIENT is None:
            email, password = _require_credentials()
            context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=context)
            session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30))
            client = Bring(session, email, password)

            try:
                await client.login()
            except Exception:
                await session.close()
                raise

            _BRING_SESSION = session
            _BRING_CLIENT = client

        return _BRING_CLIENT


async def close_bring_client() -> None:
    """Close the cached Bring client and its session."""
    global _BRING_CLIENT, _BRING_SESSION

    async with _BRING_LOCK:
        if _BRING_SESSION is not None and not _BRING_SESSION.closed:
            await _BRING_SESSION.close()

        _BRING_CLIENT = None
        _BRING_SESSION = None
        _LIST_NAME_CACHE.clear()


def _text(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]


def _item_name(arguments: dict[str, Any]) -> str:
    value = arguments.get("item_id") or arguments.get("item_name") or arguments.get("item")
    if not value:
        raise ValueError("item_id is required")
    return str(value)


async def _resolve_list_uuid(arguments: dict[str, Any]) -> str:
    if arguments.get("list_uuid"):
        return str(arguments["list_uuid"])
    name = arguments.get("list_name")
    if not name:
        raise ValueError("list_uuid or list_name is required")
    matches = [uuid for uuid, cached in _LIST_NAME_CACHE.items() if cached == name]
    if not matches:
        await _load_lists(await get_bring_client())
        matches = [uuid for uuid, cached in _LIST_NAME_CACHE.items() if cached == name]
    if len(matches) > 1:
        raise ValueError(f"Multiple lists named '{name}'; use list_uuid.")
    if not matches:
        available = ", ".join(sorted(set(_LIST_NAME_CACHE.values()))) or "(none)"
        raise ValueError(f"Unknown list name '{name}'. Available lists: {available}")
    return matches[0]


def _as_dict(value: Any) -> Any:
    """Coerce a bring-api dataclass response into nested dicts.

    Why: bring-api returns dataclasses (BringListResponse, BringItemsResponse,
    BringPurchase, ...), but the rest of this server and its tests work with
    plain dicts. Recursively converting at the boundary keeps both shapes
    working.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


def _item_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _normalize_items(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return Bring items as (to-buy, recently-purchased) sections."""
    if isinstance(items, dict):
        purchase = items.get("purchase") or []
        recently = items.get("recently") or []
        return list(purchase), list(recently)
    return [], []


def _format_items(title: str, items: Iterable[dict[str, Any]]) -> list[str]:
    lines = [title]
    for item in items:
        name = _item_value(item, "itemId", "name") or "?"
        spec = _item_value(item, "spec", "specification")
        item_uuid = _item_value(item, "uuid")
        spec_text = f" ({spec})" if spec else ""
        uuid_text = f" [UUID: {item_uuid}]" if item_uuid else ""
        lines.append(f"• {name}{spec_text}{uuid_text}")
    return lines


async def _load_lists(bring: Bring) -> list[dict[str, Any]]:
    """Fetch all lists and refresh the name cache."""
    response = _as_dict(await bring.load_lists())
    lists = list(response.get("lists", []))
    _LIST_NAME_CACHE.clear()
    for shopping_list in lists:
        uuid_value = shopping_list.get("listUuid")
        name = shopping_list.get("name")
        if uuid_value and name:
            _LIST_NAME_CACHE[uuid_value] = name
    return lists


def _cached_list_name(list_uuid: str) -> str:
    """Return a name or UUID without fetching; callers can warm the cache."""
    return _LIST_NAME_CACHE.get(list_uuid, list_uuid)


async def _get_list_payload(bring: Bring, list_uuid: str) -> dict[str, Any]:
    response = _as_dict(await bring.get_list(list_uuid))
    if not response.get("name") and list_uuid not in _LIST_NAME_CACHE:
        await _load_lists(bring)
    purchase, recently = _normalize_items(response.get("items"))
    return {
        "listUuid": list_uuid,
        "name": response.get("name") or _cached_list_name(list_uuid),
        "items": {"purchase": purchase, "recently": recently},
    }


async def _mutation_target(
    bring: Bring, list_uuid: str, item_id: str, item_uuid: str | None, operation: str
) -> tuple[str, bool]:
    """Resolve an unambiguous UUID and whether it is currently on the buy list."""
    response = _as_dict(await bring.get_list(list_uuid))
    purchase, recently = _normalize_items(response.get("items"))
    all_items = purchase + recently
    if item_uuid:
        return item_uuid, any(_item_value(item, "uuid") == item_uuid for item in purchase)
    matches = [item for item in all_items if _item_value(item, "itemId", "name") == item_id]
    # A UUID may occur in both sections during transitions; it is still one item.
    ids = {_item_value(item, "uuid") for item in matches}
    if len(ids) > 1 or (len(matches) > 1 and "" in ids):
        raise ValueError(f"Multiple items named '{item_id}'; use item_uuid.")
    if matches:
        resolved = _item_value(matches[0], "uuid")
        if not resolved:
            raise ValueError(f"Item '{item_id}' has no UUID; refresh the list before retrying.")
        return resolved, any(_item_value(item, "uuid") == resolved for item in purchase)
    if operation == "remove_item":
        raise ValueError(f"Item '{item_id}' not found; supply item_uuid if known.")
    return str(uuid4()), False


def _mutation_result(message: str, list_uuid: str, item_id: str, item_uuid: str, status: str) -> list[TextContent]:
    payload = {"list_uuid": list_uuid, "item_id": item_id, "item_uuid": item_uuid, "status": status}
    return _text(message) + _text(json.dumps(payload, ensure_ascii=False))


@SERVER.list_tools()
async def list_tools() -> list[Tool]:
    """Return all available tools."""
    tools = [
        Tool(
            name="get_lists",
            description="List all Bring! shopping lists.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_list",
            description="Return a single Bring! shopping list. Defaults to text with Recently Purchased; use format=json for structured sections or include_recent=false to hide history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_uuid": {
                        "type": "string",
                        "description": "UUID of the shopping list",
                    },
                    "format": {"type": "string", "enum": ["text", "json"], "default": "text"},
                    "include_recent": {"type": "boolean", "default": True},
                },
                "required": ["list_uuid"],
            },
        ),
        Tool(
            name="add_item",
            description="Add an item, or update it if it already exists. Returns created/updated status and item_uuid. Duplicate names require item_uuid.",
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
                    "item_name": {"type": "string", "description": "Alias for item_id"},
                    "item": {"type": "string", "description": "Alias for item_id"},
                    "item_uuid": {
                        "type": "string",
                        "description": "Optional UUID for the item",
                    },
                },
                "required": ["list_uuid"],
                "anyOf": [
                    {"required": ["item_id"]},
                    {"required": ["item_name"]},
                    {"required": ["item"]},
                ],
            },
        ),
        Tool(
            name="remove_item",
            description="Delete an item completely from the list, rather than checking it off. Returns item_uuid; duplicate names require item_uuid.",
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
                    "item_name": {"type": "string", "description": "Alias for item_id"},
                    "item": {"type": "string", "description": "Alias for item_id"},
                    "item_uuid": {
                        "type": "string",
                        "description": "Optional UUID of the item",
                    },
                },
                "required": ["list_uuid"],
                "anyOf": [
                    {"required": ["item_id"]},
                    {"required": ["item_name"]},
                    {"required": ["item"]},
                ],
            },
        ),
        Tool(
            name="complete_item",
            description="Move an item to Recently Purchased; it is not checked off in place. Returns item_uuid; duplicate names require item_uuid.",
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
                    "item_name": {"type": "string", "description": "Alias for item_id"},
                    "item": {"type": "string", "description": "Alias for item_id"},
                    "item_uuid": {
                        "type": "string",
                        "description": "Optional UUID of the item",
                    },
                },
                "required": ["list_uuid"],
                "anyOf": [
                    {"required": ["item_id"]},
                    {"required": ["item_name"]},
                    {"required": ["item"]},
                ],
            },
        ),
        Tool(
            name="batch_update",
            description="Apply ADD (add or update), COMPLETE (move to Recently Purchased, not check off in place), or REMOVE (delete completely) to multiple items.",
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
                        "description": "ADD (add to list), COMPLETE (move to Recently Purchased), or REMOVE (delete). Note: COMPLETE moves items to recently purchased rather than checking them off.",
                        "enum": ["ADD", "COMPLETE", "REMOVE"],
                    },
                },
                "required": ["list_uuid", "items", "operation"],
            },
        ),
    ]

    for tool in tools:
        if tool.name == "get_lists":
            continue
        schema = tool.inputSchema
        schema["properties"]["list_name"] = {
            "type": "string", "minLength": 1,
            "description": "Exact list name, as an alternative to list_uuid; UUID takes precedence.",
        }
        schema["properties"]["list_uuid"]["minLength"] = 1
        schema["required"] = [key for key in schema.get("required", []) if key != "list_uuid"]
        schema["allOf"] = [{"anyOf": [{"required": ["list_uuid"]}, {"required": ["list_name"]}]}]
        tool.description += " Select the list by list_uuid or list_name (UUID takes precedence)."
    return tools


async def execute_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_lists":
        bring = await get_bring_client()
        lists = await _load_lists(bring)
        if not lists:
            return _text("No shopping lists found.")

        lines = ["Your shopping lists:"]
        lines.extend(f"• {lst['name']} (UUID: {lst['listUuid']})" for lst in lists)
        return _text("\n".join(lines))

    if name == "get_list":
        list_uuid = await _resolve_list_uuid(arguments)
        bring = await get_bring_client()
        output_format = arguments.get("format", "text")
        include_recent = arguments.get("include_recent", True)
        if output_format not in ("text", "json"):
            raise ValueError("format must be text or json")
        if not isinstance(include_recent, bool):
            raise ValueError("include_recent must be a boolean")
        payload = await _get_list_payload(bring, list_uuid)
        if not include_recent:
            payload["items"]["recently"] = []
        if output_format == "json":
            return _text(json.dumps(payload, ensure_ascii=False, indent=2))
        purchase_items = payload["items"]["purchase"]
        recently_items = payload["items"]["recently"]
        list_name = payload["name"]

        if not purchase_items and not recently_items:
            return _text(f"List '{list_name}': no items.")

        lines = [f"List: {list_name}", "-" * 40]
        if purchase_items:
            lines.extend(_format_items("To buy:", purchase_items))
        if recently_items:
            if purchase_items:
                lines.append("")
            lines.extend(_format_items("Recently Purchased:", recently_items))

        return _text("\n".join(lines))

    if name in ("add_item", "remove_item", "complete_item"):
        list_uuid = await _resolve_list_uuid(arguments)
        item_id = _item_name(arguments)
        spec = arguments.get("spec") or ""
        supplied_uuid = arguments.get("item_uuid")
        if name == "add_item":
            supplied_uuid = supplied_uuid or arguments.get("uuid")
        bring = await get_bring_client()
        item_uuid, existed = await _mutation_target(bring, list_uuid, item_id, supplied_uuid, name)
        if name == "add_item":
            await bring.save_item(list_uuid, item_id, spec, item_uuid)
            status = "updated" if existed else "created"
            message = f"✓ {'Updated' if existed else 'Added'} '{item_id}' in the list."
        elif name == "remove_item":
            await bring.remove_item(list_uuid, item_id, item_uuid)
            status = "removed"
            message = f"✓ Removed '{item_id}' from the list."
        else:
            await bring.complete_item(list_uuid, item_id, spec, item_uuid)
            status = "completed"
            message = f"✓ Moved '{item_id}' to Recently Purchased."
        return _mutation_result(message, list_uuid, item_id, item_uuid, status)

    if name == "batch_update":
        list_uuid = await _resolve_list_uuid(arguments)
        items = arguments.get("items", [])
        operation_name = str(arguments.get("operation", "ADD")).upper()
        try:
            operation = BringItemOperation[operation_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported batch operation: {operation_name}") from exc

        bring = await get_bring_client()
        await bring.batch_update_list(list_uuid, items, operation)
        return _text(f"✓ Batch operation '{operation.name}' completed for {len(items)} item(s).")

    raise ValueError(f"Unknown tool: {name}")


@SERVER.call_tool()
async def call_tool(name: str, arguments: Any) -> CallToolResult:
    """Execute a tool and translate known Bring errors into user-friendly text."""
    try:
        content = await execute_tool(name, dict(arguments or {}))
        metadata = json.loads(content[1].text) if name in ("add_item", "remove_item", "complete_item") else None
        return CallToolResult(content=content, structuredContent=metadata, isError=False)
    except ValueError as exc:
        logger.info("Invalid request for tool '%s': %s", name, exc)
        if str(exc) == "BRING_EMAIL and BRING_PASSWORD must be set":
            return CallToolResult(content=_text(f"Configuration error: {exc}"), isError=True)
        return CallToolResult(content=_text(f"Invalid request: {exc}"), isError=True)
    except BringAuthException:
        logger.exception("Bring authentication failed for tool '%s'", name)
        # Token may have expired; drop the cached client so the next call re-logs in.
        await close_bring_client()
        return CallToolResult(content=_text("Authentication failed. Check BRING_EMAIL and BRING_PASSWORD."), isError=True)
    except BringRequestException:
        logger.exception("Bring API request failed for tool '%s'", name)
        return CallToolResult(content=_text("Bring API request failed."), isError=True)
    except BringParseException:
        logger.exception("Bring API returned invalid data for tool '%s'", name)
        return CallToolResult(content=_text("Bring API returned invalid data."), isError=True)
    except BringException:
        logger.exception("Bring API error for tool '%s'", name)
        return CallToolResult(content=_text("Bring API error."), isError=True)
    except Exception:
        logger.exception("Unexpected error while handling tool '%s'", name)
        return CallToolResult(content=_text("Unexpected server error."), isError=True)


# --- Resources ---------------------------------------------------------------


@SERVER.list_resources()
async def list_resources() -> list[Resource]:
    """Expose the lists index plus one resource per shopping list.

    Falls back to the index alone when credentials are missing or the Bring
    API is unreachable, so MCP clients can still enumerate resources without
    a working login.
    """
    resources: list[Resource] = [
        Resource(
            uri=AnyUrl(LISTS_INDEX_URI),
            name="Bring! shopping lists",
            description="JSON index of every shopping list this account can access.",
            mimeType="application/json",
        )
    ]

    try:
        bring = await get_bring_client()
        lists = await _load_lists(bring)
    except BringAuthException:
        await close_bring_client()
        logger.info("list_resources cleared invalid authentication; falling back to index-only", exc_info=True)
        return resources
    except (ValueError, BringException, aiohttp.ClientError, OSError):
        logger.info("list_resources falling back to index-only", exc_info=True)
        return resources

    for shopping_list in lists:
        uuid_value = shopping_list.get("listUuid")
        if not uuid_value:
            continue
        name = shopping_list.get("name") or uuid_value
        resources.append(
            Resource(
                uri=AnyUrl(f"{LISTS_INDEX_URI}/{uuid_value}"),
                name=f"Bring! list: {name}",
                description=f"Items on the '{name}' Bring! shopping list.",
                mimeType="application/json",
            )
        )

    return resources


@SERVER.list_resource_templates()
async def list_resource_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            uriTemplate="bring://lists/{listUuid}",
            name="Bring! shopping list",
            description="Items on a single Bring! shopping list, addressed by UUID.",
            mimeType="application/json",
        )
    ]


@SERVER.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """Return JSON content for ``bring://lists`` or ``bring://lists/{uuid}``."""
    try:
        uri_str = str(uri).rstrip("/")
        if uri_str == LISTS_INDEX_URI:
            bring = await get_bring_client()
            lists = await _load_lists(bring)
            return json.dumps({"lists": lists}, ensure_ascii=False, indent=2)

        prefix = f"{LISTS_INDEX_URI}/"
        if uri_str.startswith(prefix):
            list_uuid = uri_str[len(prefix):]
            if not list_uuid:
                raise ValueError("Missing list UUID in resource URI")
            bring = await get_bring_client()
            payload = await _get_list_payload(bring, list_uuid)
            return json.dumps(payload, ensure_ascii=False, indent=2)

        raise ValueError(f"Unknown resource URI: {uri_str}")
    except BringAuthException:
        await close_bring_client()
        raise


# --- Prompts -----------------------------------------------------------------


@SERVER.list_prompts()
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="meal_plan",
            description=(
                "Plan a meal and stage the missing ingredients on the user's Bring! list. "
                "The assistant uses get_lists to find the list, get_list to see what is "
                "already there, and batch_update to add what is missing."
            ),
            arguments=[
                PromptArgument(name="meal", description="Meal to plan (e.g. 'Chili sin Carne for Friday').", required=True),
                PromptArgument(name="servings", description="Number of servings.", required=False),
            ],
        ),
        Prompt(
            name="weekly_groceries",
            description=(
                "Draft a weekly grocery list based on the household's typical needs "
                "and stage the items on the user's Bring! list."
            ),
            arguments=[
                PromptArgument(
                    name="household",
                    description="Short description of the household, e.g. 'two adults, one toddler, vegetarian'.",
                    required=False,
                ),
            ],
        ),
    ]


_DEFAULT_LIST_HINT = (
    "Call `get_lists` first to find the user's shopping list. If only one list "
    "exists, use it; otherwise pick the list whose name best matches 'Home', or "
    "ask the user which list to use."
)


@SERVER.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
    args = arguments or {}
    if name == "meal_plan":
        meal = args.get("meal") or "the meal"
        servings = args.get("servings")
        servings_clause = f" for {servings} servings" if servings else ""
        text = (
            f"Plan {meal}{servings_clause}.\n\n"
            f"1. {_DEFAULT_LIST_HINT}\n"
            "2. Call `get_list` on that list to see what is already on it.\n"
            "3. Decide which ingredients are still missing.\n"
            "4. Use `batch_update` (operation=ADD) on that list to add the missing items in one call.\n"
            "5. Report back which items you added and which were already present."
        )
        return GetPromptResult(
            description=f"Plan {meal} and stage missing ingredients.",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    if name == "weekly_groceries":
        household = args.get("household") or "the household"
        text = (
            f"Draft a weekly grocery list for {household} and stage it on Bring!.\n\n"
            f"1. {_DEFAULT_LIST_HINT}\n"
            "2. Call `get_list` on that list to avoid duplicating what is already there.\n"
            "3. Propose a balanced, realistic week of groceries (staples, fresh produce, proteins, snacks).\n"
            "4. Use `batch_update` (operation=ADD) on that list to add everything missing in one call.\n"
            "5. Summarise the additions grouped by category (produce, dairy, ...)."
        )
        return GetPromptResult(
            description="Draft and stage a weekly grocery list.",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    raise ValueError(f"Unknown prompt: {name}")


async def main() -> None:
    """Start the MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
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
    asyncio.run(main())
