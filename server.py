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
from collections.abc import Iterable
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
from mcp.types import (
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

load_dotenv()

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
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
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


def _list_uuid(arguments: dict[str, Any]) -> str:
    value = arguments.get("list_uuid")
    if not value:
        raise ValueError("list_uuid is required")
    return str(value)


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
    """Return the cached list name, or the UUID if it has not been seen yet.

    Why: ``BringItemsResponse`` has no ``name`` field, so we used to refresh
    list names from the API on every ``get_list`` call. That doubled the HTTP
    traffic on a cold start. Instead we only render the name when the cache
    has been warmed by a previous ``get_lists`` / ``_load_lists`` call.
    """
    return _LIST_NAME_CACHE.get(list_uuid, list_uuid)


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
                    "item_uuid": {
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
                        "description": "ADD (add to list), COMPLETE (move to Recently Purchased), or REMOVE (delete). Note: COMPLETE moves items to recently purchased rather than checking them off.",
                        "enum": ["ADD", "COMPLETE", "REMOVE"],
                    },
                },
                "required": ["list_uuid", "items", "operation"],
            },
        ),
    ]


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
        list_uuid = _list_uuid(arguments)
        bring = await get_bring_client()
        response = _as_dict(await bring.get_list(list_uuid))
        purchase_items, recently_items = _normalize_items(response.get("items"))
        list_name = response.get("name") or _cached_list_name(list_uuid)

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

    if name == "add_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        spec = arguments.get("spec") or ""
        item_uuid = arguments.get("item_uuid") or arguments.get("uuid")
        bring = await get_bring_client()
        await bring.save_item(list_uuid, item_id, spec, item_uuid)
        return _text(f"✓ Added or updated '{item_id}' in the list.")

    if name == "remove_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        item_uuid = arguments.get("item_uuid")
        bring = await get_bring_client()
        await bring.remove_item(list_uuid, item_id, item_uuid)
        return _text(f"✓ Removed '{item_id}' from the list.")

    if name == "complete_item":
        list_uuid = _list_uuid(arguments)
        item_id = _item_name(arguments)
        spec = arguments.get("spec") or ""
        item_uuid = arguments.get("item_uuid")
        bring = await get_bring_client()
        await bring.complete_item(list_uuid, item_id, spec, item_uuid)
        return _text(f"✓ Moved '{item_id}' to Recently Purchased.")

    if name == "batch_update":
        list_uuid = _list_uuid(arguments)
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
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Execute a tool and translate known Bring errors into user-friendly text."""
    try:
        return await execute_tool(name, dict(arguments or {}))
    except ValueError as exc:
        logger.info("Invalid request for tool '%s': %s", name, exc)
        if str(exc) == "BRING_EMAIL and BRING_PASSWORD must be set":
            return _text(f"Configuration error: {exc}")
        return _text(f"Invalid request: {exc}")
    except BringAuthException:
        logger.exception("Bring authentication failed for tool '%s'", name)
        # Token may have expired; drop the cached client so the next call re-logs in.
        await close_bring_client()
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
        response = _as_dict(await bring.get_list(list_uuid))
        list_name = response.get("name") or _cached_list_name(list_uuid)
        purchase_items, recently_items = _normalize_items(response.get("items"))
        payload = {
            "listUuid": list_uuid,
            "name": list_name,
            "items": {
                "purchase": purchase_items,
                "recently": recently_items,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    raise ValueError(f"Unknown resource URI: {uri_str}")


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
