"""Protocol and real SDK transport regressions for issues #21–27."""

import copy
import json
import os
from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from bring_api import Bring
from bring_api.exceptions import BringAuthException
from bring_api.types import Attribute, BringPurchase, Content
from mcp.shared.memory import create_connected_server_and_client_session

import server
from tests.test_server import DummyBring, DummySession


DEADLINE = {"type": "Deadline", "content": {
    "activeFrom": "2026-11-04T00:00:00Z", "activeTo": "2026-11-10T23:59:59Z"}}


class V140ProtocolTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        self.enterContext(patch.dict(os.environ, {
            "BRING_EMAIL": "test@example.com", "BRING_PASSWORD": "test-only"}))
        self.enterContext(patch.object(server, "Bring", DummyBring))
        self.enterContext(patch.object(server.aiohttp, "ClientSession", DummySession))
        self.addAsyncCleanup(server.close_bring_client)
        self.bring = await server.get_bring_client()
        self.bring.load_lists.return_value = {"lists": [
            {"listUuid": "home", "name": "Home (family)"},
            {"listUuid": "work", "name": "Work"}]}
        self.sections = {"items": {
            "purchase": [{"itemId": "Milk", "specification": "2 l", "uuid": "milk", "attributes": []},
                         {"itemId": "Küchenrolle", "specification": "large", "uuid": "paper", "attributes": []}],
            "recently": [{"itemId": "Bread", "specification": "wholemeal", "uuid": "bread", "attributes": []}]}}
        self.bring.get_list.return_value = self.sections

    async def invoke(self, name, arguments):
        async with create_connected_server_and_client_session(
            server.SERVER, read_timeout_seconds=timedelta(seconds=5)
        ) as client:
            return await client.call_tool(name, arguments)

    def assert_success(self, result):
        self.assertFalse(result.isError, result.content)
        self.assertIsInstance(result.structuredContent, dict)

    def assert_no_writes(self):
        for method in ("save_item", "remove_item", "complete_item", "batch_update_list"):
            getattr(self.bring, method).assert_not_awaited()

    async def test_get_lists_json_has_lossless_names_uuids_and_structured_content(self):
        result = await self.invoke("get_lists", {"format": "json"})
        self.assert_success(result)
        expected = {"lists": [{"name": "Home (family)", "list_uuid": "home"},
                              {"name": "Work", "list_uuid": "work"}]}
        self.assertEqual(json.loads(result.content[0].text), expected)
        self.assertEqual(result.structuredContent, expected)

    async def test_empty_lists_json_remains_machine_readable(self):
        self.bring.load_lists.return_value = {"lists": []}
        result = await self.invoke("get_lists", {"format": "json"})
        self.assert_success(result)
        self.assertEqual(json.loads(result.content[0].text), {"lists": []})
        self.assertEqual(result.structuredContent, {"lists": []})

    async def test_get_lists_default_text_keeps_machine_readable_metadata(self):
        result = await self.invoke("get_lists", {})
        self.assert_success(result)
        self.assertIn("Home (family)", result.content[0].text)
        self.assertEqual(result.structuredContent["lists"][0]["list_uuid"], "home")

    async def test_search_all_lists_normalizes_unicode_and_namespaces_matches(self):
        work = {"items": {"purchase": [], "recently": [
            {"itemId": "Küchenrolle", "specification": "small", "uuid": "paper", "attributes": []}]}}
        self.bring.get_list.side_effect = lambda list_uuid: self.sections if list_uuid == "home" else work
        result = await self.invoke("search_items", {"filter": "  KU\u0308CHENROLLE  ", "format": "json"})
        self.assert_success(result)
        expected = {"matches": [
            {"list_uuid": "home", "list_name": "Home (family)", "item_id": "Küchenrolle",
             "item_uuid": "paper", "specification": "large", "status": "purchase"},
            {"list_uuid": "work", "list_name": "Work", "item_id": "Küchenrolle",
             "item_uuid": "paper", "specification": "small", "status": "recently"}]}
        self.assertEqual(json.loads(result.content[0].text), expected)
        self.assertEqual(result.structuredContent, expected)
        self.assert_no_writes()

    async def test_search_query_alias_and_string_false_hide_history(self):
        result = await self.invoke("search_items", {"query": "wholemeal", "include_recent": "FALSE", "format": "json"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent, {"matches": []})

    async def test_search_without_filter_rejects_before_fetch(self):
        result = await self.invoke("search_items", {})
        self.assertTrue(result.isError)
        self.bring.load_lists.assert_not_awaited()

    async def test_search_auth_failure_discards_partial_matches_and_cached_client(self):
        self.bring.get_list.side_effect = [self.sections, BringAuthException("expired")]
        server._LIST_NAME_CACHE["stale"] = "Stale"
        with self.assertLogs(server.logger, level="ERROR"):
            result = await self.invoke("search_items", {"filter": "Milk", "format": "json"})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["error"]["category"], "authentication")
        self.assertNotIn("matches", result.structuredContent)
        self.assertIsNone(server._BRING_CLIENT)
        self.assertIsNone(server._BRING_SESSION)
        self.assertEqual(server._LIST_NAME_CACHE, {})
        self.assertTrue(self.bring.session.closed)

    async def test_search_reauthenticates_after_auth_failure(self):
        self.bring.load_lists.side_effect = BringAuthException("expired")
        with self.assertLogs(server.logger, level="ERROR"):
            failed = await self.invoke("search_items", {"filter": "Milk"})
        self.assertTrue(failed.isError)
        fresh = DummyBring(DummySession(), "test@example.com", "test-only")
        fresh.load_lists.return_value = {"lists": [{"listUuid": "fresh", "name": "Fresh"}]}
        fresh.get_list.return_value = self.sections
        with patch.object(server, "Bring", return_value=fresh):
            result = await self.invoke("search_items", {"filter": "Milk", "format": "json"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["matches"][0]["list_uuid"], "fresh")
        fresh.login.assert_awaited_once()
        self.assertIs(server._BRING_CLIENT, fresh)

    async def test_uuid_name_mismatch_fails_without_silent_update(self):
        result = await self.invoke("add_item", {
            "list_uuid": "home", "item_id": "New name", "item_uuid": "milk", "spec": "1 l"})
        self.assertTrue(result.isError)
        self.assertIn("Milk", result.content[0].text)
        self.assertIn("milk", result.content[0].text)
        self.assert_no_writes()

    async def test_uuid_normalized_display_name_still_updates(self):
        result = await self.invoke("add_item", {
            "list_uuid": "home", "item_id": " MILK (2 L) ", "item_uuid": "milk"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["status"], "updated")
        self.bring.save_item.assert_awaited_once_with("home", "Milk", "2 l", "milk")

    async def test_add_history_reports_restored_with_original_uuid_and_spec(self):
        result = await self.invoke("add_item", {"list_uuid": "home", "item_id": "Bread"})
        self.assert_success(result)
        self.assertEqual(result.structuredContent["status"], "restored")
        self.assertEqual(result.structuredContent["item_uuid"], "bread")
        self.assertIn("Restored", result.content[0].text)
        self.bring.save_item.assert_awaited_once_with("home", "Bread", "wholemeal", "bread")

    async def test_single_dry_runs_reuse_resolution_and_never_write(self):
        for name, item, status, uuid in (
            ("add_item", "Milk", "updated", "milk"),
            ("add_item", "Bread", "restored", "bread"),
            ("remove_item", "Milk (2 l)", "removed", "milk"),
            ("complete_item", "Milk", "completed", "milk"),
        ):
            with self.subTest(tool=name, item=item):
                result = await self.invoke(name, {"list_uuid": "home", "item_id": item, "dry_run": "TRUE"})
                self.assert_success(result)
                self.assertEqual(result.structuredContent["item_uuid"], uuid)
                self.assertEqual(result.structuredContent["status"], status)
                self.assertIs(result.structuredContent["dry_run"], True)
                self.assertEqual(json.loads(result.content[1].text), result.structuredContent)
        self.assert_no_writes()

    async def test_dry_run_does_not_bypass_missing_or_ambiguous_targets(self):
        self.sections["items"]["purchase"].append({
            "itemId": "Milk", "uuid": "milk-other", "specification": "1 l", "attributes": []})
        for item in ("Unknown", "Milk"):
            result = await self.invoke("complete_item", {"list_uuid": "home", "item_id": item, "dry_run": True})
            self.assertTrue(result.isError)
        self.assert_no_writes()

    async def test_mixed_batch_defaults_to_add_and_has_per_item_outcomes(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": [
            {"item_id": "Bread"}, {"item_id": "Milk", "operation": "REMOVE"},
            {"item_id": "Küchenrolle", "operation": "COMPLETE"}]})
        self.assert_success(result)
        rows = result.structuredContent["results"]
        self.assertEqual([(r["item_uuid"], r["status"], r["operation"]) for r in rows], [
            ("bread", "restored", "ADD"), ("milk", "removed", "REMOVE"), ("paper", "completed", "COMPLETE")])
        self.bring.batch_update_list.assert_awaited_once()
        changes = self.bring.batch_update_list.await_args.args[1]
        self.assertEqual([str(change["operation"]) for change in changes], ["TO_PURCHASE", "REMOVE", "TO_RECENTLY"])

    async def test_mixed_batch_global_default_can_be_overridden(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "operation": "REMOVE", "items": [
            {"item_id": "Milk"}, {"item_id": "Bread", "operation": "ADD"}]})
        self.assert_success(result)
        self.assertEqual([r["status"] for r in result.structuredContent["results"]], ["removed", "restored"])

    async def test_mixed_batch_preflight_validates_all_before_any_write(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": [
            {"item_id": "Fresh item"}, {"item_id": "Unknown", "operation": "REMOVE"}]})
        self.assertTrue(result.isError)
        self.assertIn("Unknown", result.content[0].text)
        self.assert_no_writes()

    async def test_mixed_batch_dry_run_returns_same_resolved_outcomes_without_write(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "dry_run": True, "items": [
            {"item_id": "Bread"}, {"item_id": "Milk", "operation": "COMPLETE"}]})
        self.assert_success(result)
        self.assertIs(result.structuredContent["dry_run"], True)
        self.assertEqual([r["status"] for r in result.structuredContent["results"]], ["restored", "completed"])
        self.assert_no_writes()

    async def test_add_attributes_rejected_before_real_sdk_transport(self):
        real = Bring(DummySession(), "test@example.com", "test-only")
        real.get_list = AsyncMock(return_value=copy.deepcopy(self.sections))
        real._request = AsyncMock()
        with patch.object(server, "get_bring_client", AsyncMock(return_value=real)):
            result = await self.invoke("add_item", {
                "list_uuid": "home", "item_id": "Milk", "attributes": [DEADLINE]})
        self.assertTrue(result.isError)
        self.assertIn("attribute writes are not supported", result.content[0].text)
        real.get_list.assert_not_awaited()
        real._request.assert_not_awaited()

    async def test_attribute_only_history_update_is_unsupported(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": [
            {"item_id": "Bread", "operation": "ATTRIBUTE_UPDATE", "attributes": [DEADLINE]}]})
        self.assertTrue(result.isError)
        self.assertIn("attribute writes are not supported", result.content[0].text)
        self.bring.get_list.assert_not_awaited()
        self.assert_no_writes()

    async def test_attribute_roundtrip_rejected_instead_of_dropping_metadata(self):
        self.sections["items"]["purchase"][0]["attributes"] = [copy.deepcopy(DEADLINE)]
        listing = await self.invoke("get_list", {"list_uuid": "home", "format": "json"})
        items = json.loads(listing.content[0].text)["items"]["purchase"][:1]
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": items})
        self.assertTrue(result.isError)
        self.assertIn("attribute writes are not supported", result.content[0].text)
        self.assert_no_writes()

    async def test_dataclass_attributes_remain_readable_but_cannot_be_written(self):
        self.sections["items"]["purchase"] = [BringPurchase(
            itemId="Milk", uuid="milk", specification="2 l", attributes=[Attribute(
                type="Deadline", content=Content(
                    activeFrom=datetime(2026, 11, 4, tzinfo=timezone.utc),
                    activeTo=datetime(2026, 11, 10, tzinfo=timezone.utc), urgent=True))])]
        listing = await self.invoke("get_list", {"list_uuid": "home", "format": "json"})
        self.assertFalse(listing.isError, listing.content)
        items = json.loads(listing.content[0].text)["items"]["purchase"]
        self.assertEqual(items[0]["attributes"][0]["content"]["activeTo"], "2026-11-10T00:00:00+00:00")
        self.assertIsNone(items[0]["attributes"][0]["content"]["source"])
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": items})
        self.assertTrue(result.isError)
        self.assertIn("attribute writes are not supported", result.content[0].text)
        self.assert_no_writes()

    async def test_attribute_dry_run_reports_same_unsupported_error(self):
        result = await self.invoke("add_item", {"list_uuid": "home", "item_id": "Milk",
                                               "attributes": [DEADLINE], "dry_run": True})
        self.assertTrue(result.isError)
        self.assertIn("attribute writes are not supported", result.content[0].text)
        self.bring.get_list.assert_not_awaited()
        self.assert_no_writes()

    async def test_invalid_attribute_on_later_batch_item_cannot_partially_write(self):
        result = await self.invoke("batch_update", {"list_uuid": "home", "items": [
            {"item_id": "Fresh item"}, {"item_id": "Milk", "attributes": [{"name": "Deadline", "value": "tomorrow"}]}]})
        self.assertTrue(result.isError)
        self.assert_no_writes()
