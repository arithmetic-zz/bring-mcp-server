"""Regression tests for GitHub issues 1–5, without Bring network access."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import server
from tests.test_server import DummyBring, DummySession


class IssueFeatureTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        self.bring = DummyBring(DummySession(), "test@example.com", "test")
        self.bring.load_lists.return_value = {"lists": [
            {"listUuid": "home", "name": "Home"}, {"listUuid": "work", "name": "Work"}
        ]}
        self.bring.get_list.return_value = {"items": {
            "purchase": [{"itemId": "Milk", "uuid": "milk", "specification": "low-fat"}],
            "recently": [{"itemId": "Bread", "uuid": "bread", "specification": ""}]
        }}
        self.enterContext(patch.object(server, "get_bring_client", AsyncMock(return_value=self.bring)))
        self.addAsyncCleanup(server.close_bring_client)

    async def test_json_and_filter_preserve_separate_sections(self):
        result = await server.call_tool("get_list", {"list_uuid": "home", "format": "json", "include_recent": False})
        self.assertFalse(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["name"], "Home")
        self.assertEqual(payload["listUuid"], "home")
        self.assertEqual(payload["items"]["purchase"][0]["uuid"], "milk")
        self.assertEqual(payload["items"]["recently"], [])

    async def test_text_filter_and_cold_warm_names(self):
        for _ in range(2):
            result = await server.call_tool("get_list", {"list_uuid": "home", "include_recent": False})
            self.assertIn("List: Home", result.content[0].text)
            self.assertNotIn("Recently Purchased", result.content[0].text)
            self.assertNotIn("Bread", result.content[0].text)
        self.bring.load_lists.assert_awaited_once()

    async def test_all_tools_accept_list_name(self):
        for name, extra in (
            ("get_list", {}), ("add_item", {"item_id": "Milk"}),
            ("remove_item", {"item_id": "Milk"}), ("complete_item", {"item_id": "Milk"}),
            ("batch_update", {"items": [{"itemId": "Milk"}], "operation": "ADD"}),
        ):
            with self.subTest(tool=name):
                result = await server.call_tool(name, {"list_name": "Home", **extra})
                self.assertFalse(result.isError, result.content)
        self.bring.batch_update_list.assert_awaited_once()
        self.assertEqual(self.bring.batch_update_list.await_args.args[0], "home")

    async def test_uuid_precedes_name(self):
        result = await server.call_tool("get_list", {"list_uuid": "home", "list_name": "Work"})
        self.assertIn("Home", result.content[0].text)
        self.bring.get_list.assert_awaited_once_with("home")

    async def test_unknown_and_duplicate_list_names(self):
        result = await server.call_tool("get_list", {"list_name": "Missing"})
        self.assertTrue(result.isError)
        self.assertIn("Home", result.content[0].text)
        self.assertIn("Work", result.content[0].text)
        self.bring.load_lists.return_value["lists"].append({"listUuid": "other", "name": "Home"})
        server._LIST_NAME_CACHE.clear()
        result = await server.call_tool("get_list", {"list_name": "Home"})
        self.assertTrue(result.isError)
        self.assertIn("list_uuid", result.content[0].text)

    async def test_add_updates_existing_uuid(self):
        result = await server.call_tool("add_item", {"list_uuid": "home", "item_id": "Milk", "spec": "2 l"})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["status"], "updated")
        self.assertEqual(result.structuredContent["item_uuid"], "milk")
        self.bring.save_item.assert_awaited_once_with("home", "Milk", "2 l", "milk")

    async def test_add_creates_new_uuid_and_restores_recent(self):
        for name in ("Eggs", "Bread"):
            with self.subTest(name=name):
                self.bring.save_item.reset_mock()
                result = await server.call_tool("add_item", {"list_uuid": "home", "item_id": name})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["status"], "created")
                uuid = result.structuredContent["item_uuid"]
                self.assertTrue(uuid)
                self.assertEqual(self.bring.save_item.await_args.args[3], uuid)
                if name == "Bread":
                    self.assertEqual(uuid, "bread")

    async def test_remove_and_complete_return_target_uuid(self):
        for name in ("remove_item", "complete_item"):
            with self.subTest(tool=name):
                result = await server.call_tool(name, {"list_uuid": "home", "item_id": "Milk"})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["item_uuid"], "milk")
                self.assertIn("milk", result.content[1].text)

    async def test_duplicate_items_require_uuid(self):
        self.bring.get_list.return_value["items"]["purchase"].append(
            {"itemId": "Milk", "uuid": "other", "specification": "whole"})
        for name in ("add_item", "remove_item", "complete_item"):
            with self.subTest(tool=name):
                result = await server.call_tool(name, {"list_uuid": "home", "item_id": "Milk"})
                self.assertTrue(result.isError)
                self.assertIn("item_uuid", result.content[0].text)
                result = await server.call_tool(name, {"list_uuid": "home", "item_id": "Milk", "item_uuid": "other"})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["item_uuid"], "other")

    async def test_invalid_get_list_options_rejected(self):
        for extra in ({"format": "xml"}, {"include_recent": "maybe"}):
            result = await server.call_tool("get_list", {"list_uuid": "home", **extra})
            self.assertTrue(result.isError)

    async def test_json_matches_resource_with_history_by_default(self):
        result = await server.call_tool("get_list", {"list_uuid": "home", "format": "json"})
        from pydantic import AnyUrl
        resource = await server.read_resource(AnyUrl("bring://lists/home"))
        self.assertEqual(json.loads(result.content[0].text), json.loads(resource))
        self.assertEqual(json.loads(resource)["items"]["recently"][0]["uuid"], "bread")

    async def test_explicit_and_legacy_uuid_are_returned(self):
        chosen = "4204b322-44b5-4ed1-9d0a-a647bde960c7"
        legacy = "8f3c5a22-44b5-4ed1-9d0a-a647bde960c7"
        for extra in ({"item_uuid": chosen}, {"uuid": legacy},
                      {"item_uuid": chosen, "uuid": legacy}):
            with self.subTest(extra=extra):
                result = await server.call_tool("add_item", {"list_uuid": "home", "item_id": "Eggs", **extra})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["item_uuid"], extra.get("item_uuid", legacy))
                self.assertEqual(result.structuredContent["status"], "created")

    async def test_missing_item_remove_fails_without_mutation(self):
        result = await server.call_tool("remove_item", {"list_uuid": "home", "item_id": "Absent"})
        self.assertTrue(result.isError)
        self.assertIn("not found", result.content[0].text)
        self.bring.remove_item.assert_not_awaited()

    async def test_dataclass_mutation_lookup(self):
        from bring_api.types import BringItemsResponse, BringPurchase, Items, Status
        self.bring.get_list.return_value = BringItemsResponse(
            uuid="home", status=Status.SHARED, items=Items(
                purchase=[BringPurchase(uuid="milk", itemId="Milk", specification="")], recently=[]))
        result = await server.call_tool("add_item", {"list_uuid": "home", "item_id": "Milk"})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["status"], "updated")
        self.assertEqual(result.structuredContent["item_uuid"], "milk")

    async def test_name_cache_miss_refreshes_and_warm_match_does_not(self):
        server._LIST_NAME_CACHE["old"] = "Old"
        for _ in range(2):
            result = await server.call_tool("batch_update", {"list_name": "Work", "items": [{"itemId": "Milk"}], "operation": "ADD"})
            self.assertFalse(result.isError)
        self.bring.load_lists.assert_awaited_once()
        self.assertEqual(self.bring.batch_update_list.await_args.args[0], "work")
