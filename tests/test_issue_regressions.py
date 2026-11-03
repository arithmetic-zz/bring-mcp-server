"""Consumer-level regressions for shopping operations, using only fake Bring I/O."""

import copy
import json
import os
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID

import server
from mcp.shared.memory import create_connected_server_and_client_session
from tests.test_server import DummyBring, DummySession


class ShoppingIssueProtocolTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        self.enterContext(patch.dict(os.environ, {
            "BRING_EMAIL": "test@example.com", "BRING_PASSWORD": "test-only"}))
        self.enterContext(patch.object(server, "Bring", DummyBring))
        self.enterContext(patch.object(server.aiohttp, "ClientSession", DummySession))
        self.addAsyncCleanup(server.close_bring_client)
        self.bring = await server.get_bring_client()
        self.bring.load_lists.return_value = {"lists": [{"listUuid": "home", "name": "Home"}]}
        self.bring.get_list.return_value = {
            "name": "Home", "items": {
                "purchase": [{"itemId": "Rotwein", "specification": "SMS",
                              "uuid": "wine", "attributes": []}],
                "recently": [{"itemId": "Bread", "specification": "wholemeal",
                              "uuid": "bread", "attributes": []}]}}

    async def invoke(self, name, arguments):
        async with create_connected_server_and_client_session(
            server.SERVER, read_timeout_seconds=timedelta(seconds=5)
        ) as client:
            return await client.call_tool(name, {"list_uuid": "home", **arguments})

    def assert_success(self, result):
        self.assertFalse(result.isError, result.content)

    async def test_display_name_add_updates_canonical_item_without_erasing_spec(self):
        result = await self.invoke("add_item", {"item_id": "Rotwein (SMS)"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["status"], "updated")
        self.assertEqual(result.structuredContent["item_uuid"], "wine")
        self.bring.save_item.assert_awaited_once_with("home", "Rotwein", "SMS", "wine")

    async def test_case_and_whitespace_fallback_uses_existing_identity(self):
        result = await self.invoke("add_item", {"item_id": "  ROTWEIN  "})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["status"], "updated")
        self.bring.save_item.assert_awaited_once_with("home", "Rotwein", "SMS", "wine")

    async def test_explicit_specification_alias_replaces_existing_spec(self):
        result = await self.invoke("add_item", {"item_id": "Rotwein", "specification": "dry"})
        self.assert_success(result)
        self.bring.save_item.assert_awaited_once_with("home", "Rotwein", "dry", "wine")

    async def test_explicit_empty_spec_clears_existing_spec(self):
        result = await self.invoke("add_item", {"item_id": "Rotwein", "spec": ""})
        self.assert_success(result)
        self.bring.save_item.assert_awaited_once_with("home", "Rotwein", "", "wine")

    async def test_skip_preserves_existing_item_without_writing(self):
        result = await self.invoke("add_item", {
            "item_id": "Rotwein (SMS)", "spec": "replacement", "on_exists": "skip"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["status"], "skipped")
        self.assertEqual(result.structuredContent["item_uuid"], "wine")
        self.bring.save_item.assert_not_awaited()

    async def test_unknown_or_recent_only_completion_cannot_create_history(self):
        for name in ("Never existed", "Bread"):
            with self.subTest(name=name):
                result = await self.invoke("complete_item", {"item_name": name})
                self.assertTrue(result.isError)
                self.assertIn(name, result.content[0].text)
        self.bring.complete_item.assert_not_awaited()

    async def test_ambiguous_name_error_includes_all_candidate_uuids(self):
        self.bring.get_list.return_value["items"]["purchase"].append({
            "itemId": "Rotwein", "specification": "dry", "uuid": "wine-dry", "attributes": []})
        result = await self.invoke("remove_item", {"item_name": "Rotwein"})
        self.assertTrue(result.isError)
        self.assertIn("wine", result.content[0].text)
        self.assertIn("wine-dry", result.content[0].text)
        self.bring.remove_item.assert_not_awaited()

    async def test_invalid_new_uuid_is_rejected_but_valid_duplicate_is_allowed(self):
        invalid = await self.invoke("add_item", {"item_id": "Rotwein", "item_uuid": "REPLACE"})
        self.assertTrue(invalid.isError)
        self.bring.save_item.assert_not_awaited()
        duplicate_uuid = "4204b322-44b5-4ed1-9d0a-a647bde960c7"
        valid = await self.invoke("add_item", {"item_id": "Rotwein", "item_uuid": duplicate_uuid})
        self.assert_success(valid)
        self.assertEqual(valid.structuredContent["item_uuid"], duplicate_uuid)
        self.assertEqual(valid.structuredContent["status"], "created")

    async def test_boolean_strings_are_coerced_over_mcp(self):
        for value, recent_count in ((False, 0), ("false", 0), ("FALSE", 0),
                                    (True, 1), ("true", 1), ("TrUe", 1)):
            with self.subTest(value=value):
                result = await self.invoke("get_list", {"format": "json", "include_recent": value})
                self.assert_success(result)
                payload = json.loads(result.content[0].text)
                self.assertEqual(len(payload["items"]["recently"]), recent_count)

    async def test_invalid_boolean_error_identifies_tool_and_parameter(self):
        result = await self.invoke("get_list", {"include_recent": "maybe"})
        self.assertTrue(result.isError)
        self.assertIn("get_list", result.content[0].text)
        self.assertIn("include_recent", result.content[0].text)
        self.bring.get_list.assert_not_awaited()

    async def test_unknown_top_level_parameter_cannot_mutate_list(self):
        result = await self.invoke("add_item", {"item_id": "Milk", "specifikation": "2 l"})
        self.assertTrue(result.isError)
        self.assertIn("add_item", result.content[0].text)
        self.assertIn("specifikation", result.content[0].text)
        self.bring.save_item.assert_not_awaited()

    async def test_unknown_nested_batch_parameter_is_reported_before_write(self):
        result = await self.invoke("batch_update", {"operation": "ADD", "items": [
            {"itemId": "Milk", "specifikation": "2 l"}]})
        self.assertTrue(result.isError)
        self.assertIn("batch_update", result.content[0].text)
        self.assertIn("specifikation", result.content[0].text)
        self.bring.batch_update_list.assert_not_awaited()

    async def test_nested_operation_cannot_override_batch_operation(self):
        result = await self.invoke("batch_update", {"operation": "ADD", "items": [
            {"itemId": "Rotwein", "operation": "REMOVE"}]})
        self.assertTrue(result.isError)
        self.assertIn("batch_update", result.content[0].text)
        self.assertIn("operation", result.content[0].text)
        self.bring.batch_update_list.assert_not_awaited()

    async def test_filtered_json_lookup_includes_uuid_and_section_status(self):
        result = await self.invoke("get_list", {"format": "json", "filter": " sms "})
        self.assert_success(result)
        payload = json.loads(result.content[0].text)
        self.assertEqual([item["uuid"] for item in payload["items"]["purchase"]], ["wine"])
        self.assertEqual(payload["items"]["recently"], [])
        self.assertEqual(payload["matches"], [{"item_id": "Rotwein", "item_uuid": "wine",
                                               "specification": "SMS", "status": "purchase"}])

    async def test_query_alias_filters_text_and_keeps_uuid_and_status_heading(self):
        result = await self.invoke("get_list", {"query": "wholemeal"})
        self.assert_success(result)
        text = result.content[0].text
        self.assertIn("Bread", text)
        self.assertIn("bread", text)
        self.assertIn("Recently Purchased", text)
        self.assertNotIn("Rotwein", text)

    async def test_get_list_json_can_roundtrip_to_batch_without_losing_specification(self):
        listing = await self.invoke("get_list", {"format": "json"})
        self.assert_success(listing)
        items = json.loads(listing.content[0].text)["items"]["purchase"]
        result = await self.invoke("batch_update", {"operation": "ADD", "items": items})
        self.assert_success(result)
        self.assertIsInstance(result.structuredContent, dict)
        self.assertEqual(result.structuredContent["results"], [
            {"item_id": "Rotwein", "item_uuid": "wine", "status": "updated"}])
        args = self.bring.batch_update_list.await_args.args
        self.assertEqual(args[1][0]["itemId"], "Rotwein")
        self.assertEqual(args[1][0]["spec"], "SMS")
        self.assertEqual(args[1][0]["uuid"], "wine")

    async def test_batch_snake_case_aliases_and_created_updated_results(self):
        result = await self.invoke("batch_update", {"operation": "ADD", "items": [
            {"item_id": "Rotwein", "item_uuid": "wine", "specification": "dry"},
            {"item_id": "Milk", "spec": "2 l"}]})
        self.assert_success(result)
        payload = result.structuredContent
        self.assertEqual(payload["operation"], "ADD")
        self.assertEqual([row["status"] for row in payload["results"]], ["updated", "created"])
        self.assertEqual(payload["results"][0]["item_uuid"], "wine")
        UUID(payload["results"][1]["item_uuid"])
        self.assertEqual(json.loads(result.content[1].text), payload)

    async def test_batch_missing_target_preflight_prevents_partial_mutation(self):
        original = copy.deepcopy(self.bring.get_list.return_value)
        for operation in ("REMOVE", "COMPLETE"):
            with self.subTest(operation=operation):
                result = await self.invoke("batch_update", {"operation": operation, "items": [
                    {"itemId": "Rotwein"}, {"itemId": "Never existed"}]})
                self.assertTrue(result.isError)
                self.assertIn("Never existed", result.content[0].text)
        self.bring.batch_update_list.assert_not_awaited()
        self.assertEqual(self.bring.get_list.return_value, original)

    async def test_batch_remove_and_complete_return_resolved_uuid_and_status(self):
        for operation, status in (("REMOVE", "removed"), ("COMPLETE", "completed")):
            with self.subTest(operation=operation):
                result = await self.invoke("batch_update", {
                    "operation": operation, "items": [{"item_id": "Rotwein"}]})
                self.assert_success(result)
                self.assertEqual(result.structuredContent["results"], [
                    {"item_id": "Rotwein", "item_uuid": "wine", "status": status}])
                self.assertIn("1 item(s)", result.content[0].text)
