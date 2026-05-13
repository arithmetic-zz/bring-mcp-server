import os
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from bring_api.bring import BringItemOperation  # noqa: E402


class DummySession:
    def __init__(self, *args, **kwargs):
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
        self.get_list = AsyncMock(return_value={"name": "Groceries", "items": []})
        self.save_item = AsyncMock()
        self.remove_item = AsyncMock()
        self.complete_item = AsyncMock()
        self.batch_update_list = AsyncMock()
        DummyBring.instances.append(self)


class BringServerTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        DummyBring.instances.clear()
        self.env_patch = patch.dict(os.environ, {"BRING_EMAIL": "markus@example.com", "BRING_PASSWORD": "secret"}, clear=False)
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

    async def test_get_bring_client_reuses_session_and_login(self):
        with patch.object(server, "Bring", DummyBring), patch.object(server.aiohttp, "ClientSession", DummySession):
            client1 = await server.get_bring_client()
            client2 = await server.get_bring_client()

        self.assertIs(client1, client2)
        self.assertEqual(len(DummyBring.instances), 1)
        self.assertEqual(DummyBring.instances[0].login.await_count, 1)
        self.assertFalse(DummyBring.instances[0].session.closed)

    async def test_remove_and_complete_use_dedicated_api_methods(self):
        fake = DummyBring(DummySession(), "markus@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": []})
        fake.get_list = AsyncMock(return_value={"name": "Groceries", "items": []})

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
            self.assertIn("completed", result[0].text.lower())
            fake.complete_item.assert_awaited_once_with("list-1", "Milk", "low-fat", "item-1")

    async def test_batch_update_uses_enum(self):
        fake = DummyBring(DummySession(), "markus@example.com", "secret")
        fake.load_lists = AsyncMock(return_value={"lists": []})
        fake.get_list = AsyncMock(return_value={"name": "Groceries", "items": []})

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
