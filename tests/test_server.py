import json
import os
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import AnyUrl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from bring_api.bring import BringItemOperation  # noqa: E402
from bring_api.exceptions import BringAuthException  # noqa: E402
from bring_api.types import (  # noqa: E402
    BringItemsResponse,
    BringList,
    BringListResponse,
    BringPurchase,
    Items,
    Status,
)


class DummySession:
    def __init__(self, *_args, **_kwargs):
        self.closed = False

    async def close(self):
        self.closed = True


class DummyBring:
    instances = []

    def __init__(self, session, email, password):
        self.session = session
        self.email = email
        self.password = password
        self.login = AsyncMock()
        self.load_lists = AsyncMock(return_value={"lists": []})
        self.get_list = AsyncMock(
            return_value={"uuid": "list-1", "items": {"purchase": [], "recently": []}}
        )
        self.save_item = AsyncMock()
        self.remove_item = AsyncMock()
        self.complete_item = AsyncMock()
        self.batch_update_list = AsyncMock()
        DummyBring.instances.append(self)


class BringServerTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        DummyBring.instances.clear()
        self.env_patch = patch.dict(
            os.environ,
            {"BRING_EMAIL": "ada.lovelace@example.com", "BRING_PASSWORD": "secret"},
            clear=False,
        )
        self.env_patch.start()

    async def asyncTearDown(self):
        self.env_patch.stop()
        await server.close_bring_client()

    async def test_list_tools_are_english(self):
        tools = await server.list_tools()
        names = [tool.name for tool in tools]
        self.assertEqual(
            names,
            ["get_lists", "get_list", "add_item", "remove_item", "complete_item", "batch_update"],
        )
        german_markers = ("gibt", "fügt", "entfernt", "markiert", "führt", "liste", "artikel")
        self.assertTrue(
            all(not any(marker in tool.description.lower() for marker in german_markers) for tool in tools)
        )

    async def test_add_item_schema_uses_item_uuid(self):
        tools = {tool.name: tool for tool in await server.list_tools()}
        add_props = tools["add_item"].inputSchema["properties"]
        self.assertIn("item_uuid", add_props)
        self.assertNotIn("uuid", add_props)

    async def test_get_bring_client_reuses_session_and_login(self):
        with patch.object(server, "Bring", DummyBring), patch.object(server.aiohttp, "ClientSession", DummySession):
            client1 = await server.get_bring_client()
            client2 = await server.get_bring_client()

        self.assertIs(client1, client2)
        self.assertEqual(len(DummyBring.instances), 1)
        self.assertEqual(DummyBring.instances[0].login.await_count, 1)
        self.assertFalse(DummyBring.instances[0].session.closed)

    async def test_get_list_formats_purchase_and_recently_sections(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": [{"name": "Zuhause", "listUuid": "list-1"}]})
        fake.get_list = AsyncMock(
            return_value={
                "uuid": "list-1",
                "items": {
                    "purchase": [
                        {"itemId": "Milk", "specification": "low-fat", "uuid": "item-1"},
                    ],
                    "recently": [
                        {"itemId": "Bread", "specification": "whole grain", "uuid": "item-2"},
                    ],
                },
            }
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            # Warm the list-name cache so get_list can render the human name.
            await server.call_tool("get_lists", {})
            result = await server.call_tool("get_list", {"list_uuid": "list-1"})

        self.assertIn("List: Zuhause", result[0].text)
        self.assertIn("To buy:", result[0].text)
        self.assertIn("• Milk (low-fat) [UUID: item-1]", result[0].text)
        self.assertIn("Recently Purchased:", result[0].text)
        self.assertIn("• Bread (whole grain) [UUID: item-2]", result[0].text)

    async def test_get_list_cold_start_uses_uuid_when_cache_empty(self):
        """Without a prior get_lists, get_list must not refetch — render the UUID."""
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": [{"name": "Zuhause", "listUuid": "list-1"}]})
        fake.get_list = AsyncMock(
            return_value={
                "uuid": "list-1",
                "items": {
                    "purchase": [{"itemId": "Milk", "specification": "", "uuid": "item-1"}],
                    "recently": [],
                },
            }
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            result = await server.call_tool("get_list", {"list_uuid": "list-1"})

        fake.load_lists.assert_not_awaited()
        self.assertIn("List: list-1", result[0].text)

    async def test_get_list_accepts_bring_api_dataclasses(self):
        """bring-api 1.x returns dataclasses, not dicts — server must coerce them."""
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(
            return_value=BringListResponse(
                lists=[BringList(listUuid="list-1", name="Zuhause", theme="t")]
            )
        )
        fake.get_list = AsyncMock(
            return_value=BringItemsResponse(
                uuid="list-1",
                status=Status.SHARED,
                items=Items(
                    purchase=[BringPurchase(uuid="item-1", itemId="Milk", specification="low-fat")],
                    recently=[BringPurchase(uuid="item-2", itemId="Bread", specification="")],
                ),
            )
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            lists_result = await server.call_tool("get_lists", {})
            list_result = await server.call_tool("get_list", {"list_uuid": "list-1"})

        self.assertIn("• Zuhause (UUID: list-1)", lists_result[0].text)
        self.assertIn("List: Zuhause", list_result[0].text)
        self.assertIn("• Milk (low-fat) [UUID: item-1]", list_result[0].text)
        self.assertIn("• Bread [UUID: item-2]", list_result[0].text)

    async def test_get_list_uses_cached_list_name_after_get_lists(self):
        """After get_lists populates the cache, get_list must not call load_lists again."""
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": [{"name": "Zuhause", "listUuid": "list-1"}]})
        fake.get_list = AsyncMock(
            return_value={"uuid": "list-1", "items": {"purchase": [], "recently": []}}
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            await server.call_tool("get_lists", {})
            self.assertEqual(fake.load_lists.await_count, 1)
            result = await server.call_tool("get_list", {"list_uuid": "list-1"})

        self.assertEqual(fake.load_lists.await_count, 1)  # not re-fetched
        self.assertIn("Zuhause", result[0].text)

    async def test_remove_and_complete_use_dedicated_api_methods(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            result = await server.call_tool(
                "remove_item",
                {"list_uuid": "list-1", "item_id": "Milk", "item_uuid": "item-1"},
            )
            self.assertIn("Removed 'Milk'", result[0].text)
            fake.remove_item.assert_awaited_once_with("list-1", "Milk", "item-1")
            fake.batch_update_list.assert_not_awaited()

            result = await server.call_tool(
                "complete_item",
                {"list_uuid": "list-1", "item_id": "Milk", "item_uuid": "item-1", "spec": "low-fat"},
            )
            self.assertIn("Recently Purchased", result[0].text)
            fake.complete_item.assert_awaited_once_with("list-1", "Milk", "low-fat", "item-1")

    async def test_add_item_accepts_item_uuid_and_legacy_uuid(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            await server.call_tool(
                "add_item",
                {"list_uuid": "list-1", "item_id": "Milk", "item_uuid": "item-1"},
            )
            await server.call_tool(
                "add_item",
                {"list_uuid": "list-1", "item_id": "Bread", "uuid": "item-2"},
            )

        self.assertEqual(
            [call.args for call in fake.save_item.await_args_list],
            [
                ("list-1", "Milk", "", "item-1"),
                ("list-1", "Bread", "", "item-2"),
            ],
        )

    async def test_batch_update_uses_enum(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            await server.call_tool(
                "batch_update",
                {
                    "list_uuid": "list-1",
                    "items": [{"itemId": "Milk"}],
                    "operation": "ADD",
                },
            )

        fake.batch_update_list.assert_awaited_once()
        args = fake.batch_update_list.await_args.args
        self.assertEqual(args[0], "list-1")
        self.assertEqual(args[1], [{"itemId": "Milk"}])
        self.assertEqual(args[2], BringItemOperation.ADD)

    async def test_validation_errors_are_user_facing(self):
        result = await server.call_tool("add_item", {"list_uuid": "list-1"})

        self.assertEqual(result[0].text, "Invalid request: item_id is required")

    async def test_missing_credentials_are_reported_as_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await server.call_tool("get_lists", {})

        self.assertEqual(
            result[0].text,
            "Configuration error: BRING_EMAIL and BRING_PASSWORD must be set",
        )

    async def test_auth_failure_drops_cached_client(self):
        """An expired token should invalidate the cached client so the next call re-logs in."""
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(side_effect=BringAuthException("expired"))

        # Seed the cache directly so we can observe it being cleared.
        server._BRING_CLIENT = fake  # noqa: SLF001
        server._BRING_SESSION = fake.session  # noqa: SLF001

        result = await server.call_tool("get_lists", {})

        self.assertIn("Authentication failed", result[0].text)
        self.assertIsNone(server._BRING_CLIENT)  # noqa: SLF001
        self.assertIsNone(server._BRING_SESSION)  # noqa: SLF001

    # --- Resources ----------------------------------------------------------

    async def test_list_resources_falls_back_to_index_without_credentials(self):
        """Without credentials, list_resources must still return at least the index."""
        with patch.dict(os.environ, {}, clear=True):
            resources = await server.list_resources()

        uris = [str(r.uri).rstrip("/") for r in resources]
        self.assertEqual(uris, ["bring://lists"])

    async def test_list_resources_enumerates_one_resource_per_list(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(
            return_value={
                "lists": [
                    {"name": "Zuhause", "listUuid": "list-1"},
                    {"name": "Work", "listUuid": "list-2"},
                ]
            }
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            resources = await server.list_resources()

        uris = [str(r.uri).rstrip("/") for r in resources]
        self.assertEqual(
            uris,
            ["bring://lists", "bring://lists/list-1", "bring://lists/list-2"],
        )
        names = [r.name for r in resources]
        self.assertIn("Bring! list: Zuhause", names)
        self.assertIn("Bring! list: Work", names)

    async def test_list_resource_templates_advertises_list_uri(self):
        templates = await server.list_resource_templates()
        self.assertEqual([t.uriTemplate for t in templates], ["bring://lists/{listUuid}"])

    async def test_read_resource_returns_list_json(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": [{"name": "Zuhause", "listUuid": "list-1"}]})
        fake.get_list = AsyncMock(
            return_value={
                "uuid": "list-1",
                "items": {
                    "purchase": [{"itemId": "Milk", "specification": "", "uuid": "item-1"}],
                    "recently": [],
                },
            }
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            # Warm the name cache by reading the index resource first.
            await server.read_resource(AnyUrl("bring://lists"))
            payload = await server.read_resource(AnyUrl("bring://lists/list-1"))

        data = json.loads(payload)
        self.assertEqual(data["listUuid"], "list-1")
        self.assertEqual(data["name"], "Zuhause")
        self.assertEqual(data["items"]["purchase"][0]["itemId"], "Milk")

    async def test_read_resource_returns_index_json(self):
        fake = DummyBring(DummySession(), "ada.lovelace@example.com", "secret")
        fake.load_lists = AsyncMock(
            return_value={"lists": [{"name": "Zuhause", "listUuid": "list-1"}]}
        )

        with patch.object(server, "get_bring_client", AsyncMock(return_value=fake)):
            payload = await server.read_resource(AnyUrl("bring://lists"))

        data = json.loads(payload)
        self.assertEqual(data["lists"][0]["name"], "Zuhause")

    # --- Prompts ------------------------------------------------------------

    async def test_list_prompts_exposes_meal_plan_and_weekly_groceries(self):
        prompts = await server.list_prompts()
        names = [p.name for p in prompts]
        self.assertIn("meal_plan", names)
        self.assertIn("weekly_groceries", names)

    async def test_get_prompt_meal_plan_includes_arguments(self):
        result = await server.get_prompt(
            "meal_plan",
            {"meal": "Chili sin Carne", "servings": "4"},
        )
        text = result.messages[0].content.text
        self.assertIn("Chili sin Carne", text)
        self.assertIn("4 servings", text)
        self.assertIn("get_lists", text)

    async def test_get_prompt_rejects_unknown_name(self):
        with self.assertRaises(ValueError):
            await server.get_prompt("does_not_exist", {})

    async def test_get_prompt_weekly_groceries_includes_household(self):
        result = await server.get_prompt(
            "weekly_groceries",
            {"household": "two adults, one toddler, vegetarian"},
        )
        text = result.messages[0].content.text
        self.assertIn("two adults, one toddler, vegetarian", text)
        self.assertIn("get_lists", text)
        self.assertIn("batch_update", text)

    async def test_read_resource_rejects_malformed_uri(self):
        with self.assertRaises(ValueError):
            await server.read_resource(AnyUrl("bring://other/thing"))

    async def test_close_bring_client_clears_list_name_cache(self):
        server._LIST_NAME_CACHE["list-1"] = "Zuhause"  # noqa: SLF001
        await server.close_bring_client()
        self.assertEqual(server._LIST_NAME_CACHE, {})  # noqa: SLF001
