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
        for tool, method, tail in (
            ("add_item", "save_item", ("", None)),
            ("remove_item", "remove_item", (None,)),
            ("complete_item", "complete_item", ("", None)),
        ):
            for key in ("item_id", "item_name", "item"):
                with self.subTest(tool=tool, key=key):
                    mock = getattr(self.bring, method)
                    mock.reset_mock()
                    result = await self.invoke(tool, {"list_uuid": "list-1", key: "Milk"})
                    self.assertFalse(result.isError)
                    mock.assert_awaited_once_with("list-1", "Milk", *tail)

    async def test_canonical_item_name_takes_precedence(self):
        for arguments, expected in (
            ({"item_id": "Milk", "item_name": "Bread", "item": "Eggs"}, "Milk"),
            ({"item_name": "Bread", "item": "Eggs"}, "Bread"),
        ):
            with self.subTest(arguments=arguments):
                self.bring.save_item.reset_mock()
                result = await self.invoke("add_item", {"list_uuid": "list-1", **arguments})
                self.assertFalse(result.isError)
                self.bring.save_item.assert_awaited_once_with("list-1", expected, "", None)

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
