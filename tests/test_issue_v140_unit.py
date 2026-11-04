"""Mutation planning and real Bring transport regressions for issues 21–27."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from bring_api import Bring

import server
from tests.test_server import DummyBring, DummySession


class IssueV140UnitTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bring = DummyBring(DummySession(), 'test@example.com', 'test')
        self.bring.load_lists.return_value = {'lists': [{'name': 'Home', 'listUuid': 'home'}, {'name': 'Work', 'listUuid': 'work'}]}
        self.bring.get_list.return_value = {'items': {'purchase': [{'itemId': 'Milk', 'uuid': 'milk', 'specification': '2 l'}],
            'recently': [{'itemId': 'Bread', 'uuid': 'bread', 'specification': 'wholemeal'}]}}
        self.enterContext(patch.object(server, 'get_bring_client', AsyncMock(return_value=self.bring)))

    async def test_explicit_uuid_mismatched_name_rejected_before_writes(self):
        result = await server.call_tool('add_item', {'list_uuid': 'home', 'item_id': 'Oat milk', 'item_uuid': 'milk'})
        self.assertTrue(result.isError)
        self.assertIn('Milk', result.content[0].text)
        self.assertIn('Oat milk', result.content[0].text)
        self.bring.save_item.assert_not_awaited()

    async def test_list_index_json_and_metadata(self):
        result = await server.call_tool('get_lists', {'format': 'json'})
        self.assertFalse(result.isError)
        self.assertEqual(json.loads(result.content[0].text), result.structuredContent)
        self.assertEqual(result.structuredContent['lists'][0], {'name': 'Home', 'list_uuid': 'home'})

    async def test_cross_list_search_uses_normalization_and_list_identity(self):
        result = await server.call_tool('search_items', {'filter': ' MILK ', 'format': 'json'})
        self.assertFalse(result.isError)
        self.assertEqual({m['list_uuid'] for m in result.structuredContent['matches']}, {'home', 'work'})
        self.assertEqual({m['list_name'] for m in result.structuredContent['matches']}, {'Home', 'Work'})

    async def test_history_add_is_restored_with_stored_uuid(self):
        result = await server.call_tool('add_item', {'list_uuid': 'home', 'item_id': 'Bread'})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent['status'], 'restored')
        self.bring.save_item.assert_awaited_once_with('home', 'Bread', 'wholemeal', 'bread')

    async def test_all_mutation_previews_skip_every_write(self):
        cases = [('add_item', {'item_id': 'Milk'}), ('complete_item', {'item_id': 'Milk'}),
            ('remove_item', {'item_id': 'Milk'}), ('batch_update', {'items': [{'item_id': 'Milk', 'operation': 'REMOVE'}]})]
        for name, args in cases:
            with self.subTest(name=name):
                result = await server.call_tool(name, {'list_uuid': 'home', 'dry_run': True, **args})
                self.assertFalse(result.isError)
                self.assertTrue(result.structuredContent['dry_run'])
                self.assertIn('Preview', result.content[0].text)
        for method in ('save_item', 'remove_item', 'complete_item', 'batch_update_list'):
            getattr(self.bring, method).assert_not_awaited()

    async def test_mixed_batch_preflight_and_statuses(self):
        result = await server.call_tool('batch_update', {'list_uuid': 'home', 'items': [
            {'item_id': 'Bread'}, {'item_id': 'Milk', 'operation': 'REMOVE'}]})
        self.assertFalse(result.isError)
        self.assertEqual([x['status'] for x in result.structuredContent['results']], ['restored', 'removed'])
        self.bring.batch_update_list.assert_awaited_once()
        changes = self.bring.batch_update_list.await_args.args[1]
        self.assertEqual([str(x['operation']) for x in changes], ['TO_PURCHASE', 'REMOVE'])
        self.bring.batch_update_list.reset_mock()
        result = await server.call_tool('batch_update', {'list_uuid': 'home', 'items': [
            {'item_id': 'Bread'}, {'item_id': 'Ghost', 'operation': 'REMOVE'}]})
        self.assertTrue(result.isError)
        self.bring.batch_update_list.assert_not_awaited()

    async def test_invalid_attribute_shape_rejected_before_network(self):
        result = await server.call_tool('add_item', {'list_uuid': 'home', 'item_id': 'Milk',
            'attributes': [{'name': 'Deadline', 'value': '2026-11-10'}]})
        self.assertTrue(result.isError)
        self.assertIn('attributes', result.content[0].text)
        self.bring.get_list.assert_not_awaited()

    async def test_mixed_batch_rejects_attributes_before_transport(self):
        bring = Bring(DummySession(), 'test@example.com', 'test')
        bring.get_list = self.bring.get_list
        bring._request = AsyncMock(return_value={})
        with patch.object(server, 'get_bring_client', AsyncMock(return_value=bring)):
            result = await server.call_tool('batch_update', {'list_uuid': 'home', 'items': [
                {'item_id': 'Bread'}, {'item_id': 'Milk', 'operation': 'ATTRIBUTE_UPDATE',
                 'attributes': [{'type': 'urgent', 'content': {'urgent': True}}]}]})
        self.assertTrue(result.isError)
        self.assertIn('attribute writes are not supported', result.content[0].text)
        bring.get_list.assert_not_awaited()
        bring._request.assert_not_awaited()

    async def test_search_normalizes_decomposed_unicode(self):
        self.bring.get_list.return_value['items']['purchase'] = [{'itemId': 'Küchenrolle', 'uuid': 'paper'}]
        result = await server.call_tool('search_items', {'filter': 'KU\u0308CHENROLLE', 'format': 'json'})
        self.assertFalse(result.isError)
        self.assertEqual(len(result.structuredContent['matches']), 2)

    async def test_attribute_dates_reject_invalid_values_before_network(self):
        for value in ('', 'not-a-date', '2026-02-30'):
            with self.subTest(value=value):
                result = await server.call_tool('add_item', {'list_uuid': 'home', 'item_id': 'Milk',
                    'attributes': [{'type': 'Deadline', 'content': {'activeTo': value}}]})
                self.assertTrue(result.isError)
                self.assertIn('attribute writes are not supported', result.content[0].text)
        self.bring.get_list.assert_not_awaited()

    async def test_filter_does_not_reinclude_nonmatching_duplicate_uuid(self):
        self.bring.get_list.return_value['items']['purchase'][0]['uuid'] = 'same-id'
        self.bring.get_list.return_value['items']['purchase'].append({'itemId': 'Soap', 'uuid': 'same-id'})
        result = await server.call_tool('get_list', {'list_uuid': 'home', 'filter': 'Milk', 'format': 'json'})
        self.assertFalse(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertEqual([x['itemId'] for x in payload['items']['purchase']], ['Milk'])
