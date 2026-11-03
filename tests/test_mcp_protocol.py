"""Exercise MCP validation, dispatch and results without network access."""

import os
import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import aiohttp  # noqa: E402
from bring_api.exceptions import (  # noqa: E402
    BringAuthException,
    BringException,
    BringParseException,
    BringRequestException,
)
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402
from mcp.shared.exceptions import McpError  # noqa: E402
from tests.test_server import DummyBring, DummySession  # noqa: E402


class McpProtocolTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        self.enterContext(patch.dict(os.environ, {"BRING_EMAIL": "test@example.com", "BRING_PASSWORD": "test"}))
        self.enterContext(patch.object(server, "Bring", DummyBring))
        self.enterContext(patch.object(server.aiohttp, "ClientSession", DummySession))
        self.addAsyncCleanup(server.close_bring_client)
        self.bring = await server.get_bring_client()

    def connect(self):
        return create_connected_server_and_client_session(
            server.SERVER, read_timeout_seconds=timedelta(seconds=5)
        )

    async def invoke(self, name, arguments):
        async with self.connect() as client:
            return await client.call_tool(name, arguments)

    async def test_tool_configuration_error_sets_is_error(self):
        await server.close_bring_client()
        with patch.dict(os.environ, {"BRING_EMAIL": "", "BRING_PASSWORD": ""}):
            result = await self.invoke("get_lists", {})
        self.assertTrue(result.isError)
        self.assertEqual(result.content[0].text, "Configuration error: BRING_EMAIL and BRING_PASSWORD must be set")

    async def test_tool_success_clears_is_error(self):
        result = await self.invoke("get_lists", {})
        self.assertFalse(result.isError)
        self.assertEqual(result.content[0].text, "No shopping lists found.")

    async def test_tool_failures_set_is_error(self):
        cases = [
            (ValueError("bad argument"), "Invalid request: bad argument"),
            (BringRequestException("private detail"), "Bring API request failed."),
            (BringParseException("private detail"), "Bring API returned invalid data."),
            (BringException("private detail"), "Bring API error."),
            (RuntimeError("private detail"), "Unexpected server error."),
            (BringAuthException("private detail"), "Authentication failed. Check BRING_EMAIL and BRING_PASSWORD."),
        ]
        for exception, message in cases:
            with self.subTest(exception=type(exception).__name__):
                self.bring.load_lists.side_effect = exception
                with self.assertLogs(server.logger, level="INFO"):
                    result = await self.invoke("get_lists", {})
                self.assertTrue(result.isError)
                self.assertEqual(result.content[0].text, message)
                self.assertNotIn("private detail", result.content[0].text)

    async def test_tool_input_validation_sets_is_error(self):
        result = await self.invoke("get_list", {})
        self.assertTrue(result.isError)
        self.bring.get_list.assert_not_awaited()

    async def test_item_name_aliases_pass_mcp_validation(self):
        self.bring.get_list.return_value = {"items": {
            "purchase": [{"itemId": "Milk", "uuid": "milk"}], "recently": []}}
        for tool, method, tail in (
            ("add_item", "save_item", ("", "milk")),
            ("remove_item", "remove_item", ("milk",)),
            ("complete_item", "complete_item", ("", "milk")),
        ):
            for key in ("item_id", "item_name", "item"):
                with self.subTest(tool=tool, key=key):
                    mock = getattr(self.bring, method)
                    mock.reset_mock()
                    result = await self.invoke(tool, {"list_uuid": "list-1", key: "Milk"})
                    self.assertFalse(result.isError)
                    mock.assert_awaited_once_with("list-1", "Milk", *tail)

    async def test_canonical_item_name_takes_precedence(self):
        self.bring.get_list.return_value = {"items": {"purchase": [
            {"itemId": "Milk", "uuid": "milk"}, {"itemId": "Bread", "uuid": "bread"}], "recently": []}}
        for arguments, expected in (
            ({"item_id": "Milk", "item_name": "Bread", "item": "Eggs"}, "Milk"),
            ({"item_name": "Bread", "item": "Eggs"}, "Bread"),
        ):
            with self.subTest(arguments=arguments):
                self.bring.save_item.reset_mock()
                result = await self.invoke("add_item", {"list_uuid": "list-1", **arguments})
                self.assertFalse(result.isError)
                self.bring.save_item.assert_awaited_once_with("list-1", expected, "", expected.lower())

    async def test_missing_or_empty_item_name_is_rejected(self):
        for tool in ("add_item", "remove_item", "complete_item"):
            for names in ({}, {"item_id": ""}, {"item_name": ""}, {"item": ""}):
                with self.subTest(tool=tool, names=names):
                    result = await self.invoke(tool, {"list_uuid": "list-1", **names})
                    self.assertTrue(result.isError)
        self.bring.save_item.assert_not_awaited()
        self.bring.remove_item.assert_not_awaited()
        self.bring.complete_item.assert_not_awaited()

    async def test_invalid_item_name_type_is_rejected(self):
        for tool in ("add_item", "remove_item", "complete_item"):
            for key in ("item_id", "item_name", "item"):
                with self.subTest(tool=tool, key=key):
                    result = await self.invoke(tool, {"list_uuid": "list-1", key: 123})
                    self.assertTrue(result.isError)
        self.bring.save_item.assert_not_awaited()
        self.bring.remove_item.assert_not_awaited()
        self.bring.complete_item.assert_not_awaited()

    async def test_add_item_legacy_uuid_passes_mcp_validation(self):
        result = await self.invoke("add_item", {"list_uuid": "list-1", "item_id": "Milk", "uuid": "item-1"})
        self.assertFalse(result.isError)
        self.bring.save_item.assert_awaited_once_with("list-1", "Milk", "", "item-1")

    async def test_resource_discovery_auth_failure_resets_client(self):
        self.bring.load_lists.side_effect = BringAuthException("expired")
        server._LIST_NAME_CACHE["list-1"] = "Home"
        async with self.connect() as client:
            result = await client.list_resources()
        self.assertEqual([str(r.uri) for r in result.resources], ["bring://lists"])
        self.assertTrue(self.bring.session.closed)
        self.assertIsNone(server._BRING_CLIENT)
        self.assertIsNone(server._BRING_SESSION)
        self.assertEqual(server._LIST_NAME_CACHE, {})

    async def test_resource_read_auth_failure_resets_client(self):
        for uri in ("bring://lists", "bring://lists/list-1"):
            with self.subTest(uri=uri):
                await server.close_bring_client()
                failed = await server.get_bring_client()
                failed.load_lists.side_effect = BringAuthException("expired")
                failed.get_list.side_effect = BringAuthException("expired")
                server._LIST_NAME_CACHE["list-1"] = "Home"
                async with self.connect() as client:
                    with self.assertRaises(McpError):
                        await client.read_resource(uri)
                self.assertTrue(failed.session.closed)
                self.assertIsNone(server._BRING_CLIENT)
                self.assertIsNone(server._BRING_SESSION)
                self.assertEqual(server._LIST_NAME_CACHE, {})

    async def test_resources_reauthenticate_after_auth_failure(self):
        for read_uri in (None, "bring://lists", "bring://lists/list-1"):
            with self.subTest(uri=read_uri):
                await server.close_bring_client()
                failed = await server.get_bring_client()
                failed.load_lists.side_effect = BringAuthException("expired")
                failed.get_list.side_effect = BringAuthException("expired")
                fresh = DummyBring(DummySession(), "test@example.com", "test")
                fresh.load_lists.return_value = {"lists": [{"listUuid": "list-1", "name": "Home"}]}
                with patch.object(server, "Bring", return_value=fresh):
                    async with self.connect() as client:
                        if read_uri is None:
                            first = await client.list_resources()
                            result = await client.list_resources()
                        else:
                            with self.assertRaises(McpError):
                                await client.read_resource(read_uri)
                            result = await client.read_resource(read_uri)
                if read_uri is None:
                    self.assertEqual(len(first.resources), 1)
                    self.assertEqual(len(result.resources), 2)
                else:
                    self.assertIsInstance(json.loads(result.contents[0].text), dict)
                fresh.login.assert_awaited_once()
                self.assertIs(server._BRING_CLIENT, fresh)

    async def test_resource_discovery_network_failure_preserves_client(self):
        for error in (aiohttp.ClientError("offline"), OSError("offline")):
            with self.subTest(error=type(error).__name__):
                self.bring.load_lists.side_effect = error
                async with self.connect() as client:
                    result = await client.list_resources()
                self.assertEqual(len(result.resources), 1)
                self.assertIs(server._BRING_CLIENT, self.bring)
                self.assertFalse(self.bring.session.closed)

    async def test_resource_discovery_without_credentials_returns_index(self):
        await server.close_bring_client()
        with patch.dict(os.environ, {"BRING_EMAIL": "", "BRING_PASSWORD": ""}):
            async with self.connect() as client:
                result = await client.list_resources()
        self.assertEqual([str(r.uri) for r in result.resources], ["bring://lists"])

    async def test_unknown_resource_is_rejected(self):
        async with self.connect() as client:
            with self.assertRaises(McpError):
                await client.read_resource("bring://other/thing")
        self.bring.load_lists.assert_not_awaited()
        self.bring.get_list.assert_not_awaited()

    async def test_list_name_and_json_options_over_protocol(self):
        self.bring.load_lists.return_value = {"lists": [{"listUuid": "home", "name": "Home"}]}
        self.bring.get_list.return_value = {"items": {
            "purchase": [{"itemId": "Milk", "uuid": "milk"}],
            "recently": [{"itemId": "Bread", "uuid": "bread"}]}}
        for tool, extra in (
            ("get_list", {"format": "json", "include_recent": False}),
            ("add_item", {"item": "Milk"}), ("remove_item", {"item_name": "Milk"}),
            ("complete_item", {"item_id": "Milk"}),
            ("batch_update", {"items": [{"itemId": "Eggs"}], "operation": "ADD"}),
        ):
            with self.subTest(tool=tool):
                result = await self.invoke(tool, {"list_name": "Home", **extra})
                self.assertFalse(result.isError, result.content)
                if tool == "get_list":
                    data = json.loads(result.content[0].text)
                    self.assertEqual(data["items"]["recently"], [])
                    self.assertEqual(data["items"]["purchase"][0]["uuid"], "milk")
                elif tool != "batch_update":
                    self.assertEqual(result.structuredContent["item_uuid"], "milk")
                    self.assertEqual(result.structuredContent["list_uuid"], "home")
        self.bring.load_lists.assert_awaited_once()

    async def test_invalid_list_selectors_and_options_over_protocol(self):
        for arguments in ({}, {"list_name": ""}, {"list_uuid": ""},
                          {"list_name": 123}, {"list_uuid": "home", "include_recent": "false"},
                          {"list_uuid": "home", "format": "xml"}):
            with self.subTest(arguments=arguments):
                result = await self.invoke("get_list", arguments)
                self.assertTrue(result.isError)
        self.bring.get_list.assert_not_awaited()

    async def test_added_lookup_auth_failure_invalidates_caches(self):
        for tool, arguments, lookup in (
            ("get_list", {"list_name": "Home"}, "load_lists"),
            ("get_list", {"list_uuid": "home"}, "load_lists"),
            ("add_item", {"list_uuid": "home", "item_id": "Milk"}, "get_list"),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                await server.close_bring_client()
                self.bring = await server.get_bring_client()
                getattr(self.bring, lookup).side_effect = BringAuthException("expired")
                with self.assertLogs(server.logger, level="ERROR"):
                    result = await self.invoke(tool, arguments)
                self.assertTrue(result.isError)
                self.assertIsNone(server._BRING_CLIENT)
                self.assertEqual(server._LIST_NAME_CACHE, {})
                self.assertTrue(self.bring.session.closed)
                self.bring.save_item.assert_not_awaited()

    async def test_json_tool_and_resource_preserve_item_attribute_dates(self):
        from bring_api.types import BringItemsResponse
        self.bring.load_lists.return_value = {"lists": [{"listUuid": "home", "name": "Home"}]}
        # Parse an API-shaped response with the real library: date strings become datetime objects.
        self.bring.get_list.return_value = BringItemsResponse.from_json(json.dumps({
            "uuid": "home", "status": "SHARED", "items": {"purchase": [{
                "itemId": "Milk", "uuid": "milk", "specification": "2 l",
                "attributes": [{"type": "discount", "content": {
                    "discounted": True, "activeFrom": "2026-11-03T10:00:00+00:00",
                    "activeTo": "2026-11-04T10:00:00+00:00"}}]
            }], "recently": []}}))
        result = await self.invoke("get_list", {"list_uuid": "home", "format": "json"})
        self.assertFalse(result.isError, result.content)
        payload = json.loads(result.content[0].text)
        content = payload["items"]["purchase"][0]["attributes"][0]["content"]
        self.assertEqual(content["activeFrom"], "2026-11-03T10:00:00+00:00")
        self.assertEqual(content["activeTo"], "2026-11-04T10:00:00+00:00")
        self.assertTrue(content["discounted"])
        async with self.connect() as client:
            resource = await client.read_resource("bring://lists/home")
        self.assertEqual(json.loads(resource.contents[0].text), payload)
