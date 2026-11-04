"""Regressions for API diagnostics (#31), through Bring and MCP boundaries."""
import json
import os
from contextlib import asynccontextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiohttp
from bring_api import Bring
from bring_api.exceptions import BringRequestException
from mcp.shared.memory import create_connected_server_and_client_session

import server
from tests.test_server import DummyBring, DummySession

REAL_LOGIN = Bring.login


class ErrorSession(DummySession):
    """Only the external HTTP exchange is simulated; Bring dispatch stays real."""
    status = 400
    body = b'{"message":"Invalid item payload","access_token":"must-not-leak"}'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.check_status = kwargs.get('raise_for_status')
        self.calls = 0

    @asynccontextmanager
    async def post(self, *args, **kwargs):
        yield await self.request(*args, **kwargs)

    async def request(self, *args, **kwargs):
        self.calls += 1
        error = aiohttp.ClientResponseError(
            SimpleNamespace(real_url='https://example.invalid/private'), (),
            status=self.status, message='Bad response')
        response = SimpleNamespace(status=self.status, reason='Bad response',
            headers={'Retry-After': '120'}, release=lambda: None,
            content=SimpleNamespace(read=AsyncMock(side_effect=[self.body, b''])),
            raise_for_status=lambda: (_ for _ in ()).throw(error) if self.status >= 400 else None)
        if self.check_status:
            await self.check_status(response)
        response.text = AsyncMock(return_value=self.body.decode())
        return response


class ApiErrorTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await server.close_bring_client()
        self.addAsyncCleanup(server.close_bring_client)
        self.enterContext(patch.dict(os.environ, {
            'BRING_EMAIL': 'test@example.com', 'BRING_PASSWORD': 'test-secret-password'}))
        self.enterContext(patch.object(server.aiohttp, 'ClientSession', ErrorSession))
        self.enterContext(patch.object(Bring, 'login', AsyncMock()))

    async def invoke(self, name='get_lists', arguments=None):
        async with create_connected_server_and_client_session(
            server.SERVER, read_timeout_seconds=timedelta(seconds=5)
        ) as client:
            return await client.call_tool(name, arguments or {})

    async def test_http_errors_reach_mcp_with_status_category_and_detail(self):
        for status, category, retryable in ((400, 'invalid_request', False),
                (422, 'invalid_request', False), (401, 'authentication', False),
                (403, 'authentication', False), (429, 'rate_limit', True), (503, 'temporary', True)):
            with self.subTest(status=status):
                await server.close_bring_client()
                with patch.object(ErrorSession, 'status', status), self.assertLogs(server.logger):
                    result = await self.invoke()
                self.assertTrue(result.isError)
                self.assertIn(f'HTTP {status}', result.content[0].text)
                error = result.structuredContent['error']
                self.assertEqual(error['http_status'], status)
                self.assertEqual(error['category'], category)
                self.assertIs(error['retryable'], retryable)
                self.assertEqual(error['detail'], 'Invalid item payload')
                if status == 429:
                    self.assertEqual(error['retry_after'], '120')
                self.assertNotIn('must-not-leak', result.model_dump_json())
                self.assertNotIn('example.invalid', result.model_dump_json())

    async def test_http_auth_errors_clear_all_caches_for_tools_and_resources(self):
        for status in (401, 403):
            for action in ('tool', 'discovery', 'resource'):
                with self.subTest(status=status, action=action):
                    await server.close_bring_client()
                    failed = await server.get_bring_client()
                    server._LIST_NAME_CACHE['home'] = 'Home'
                    logs = self.assertLogs(server.logger, level='INFO') if action != 'resource' else nullcontext()
                    with patch.object(ErrorSession, 'status', status), logs:
                        if action == 'tool':
                            await self.invoke()
                        elif action == 'discovery':
                            resources = await server.list_resources()
                            self.assertEqual(len(resources), 1)
                        else:
                            with self.assertRaises(Exception):
                                await server.read_resource('bring://lists')
                    self.assertTrue(failed._session.closed)
                    self.assertIsNone(server._BRING_CLIENT)
                    self.assertIsNone(server._BRING_SESSION)
                    self.assertEqual(server._LIST_NAME_CACHE, {})
                    self.assertIsNot(await server.get_bring_client(), failed)

    async def test_details_are_bounded_and_credentials_are_redacted(self):
        body = json.dumps({'message': 'test-secret-password test@example.com ' + 'x' * 2000,
                           'refresh_token': 'must-not-leak'}).encode()
        with patch.object(ErrorSession, 'body', body), self.assertLogs(server.logger):
            result = await self.invoke()
        error = result.structuredContent['error']
        self.assertLessEqual(len(error['detail']), 512)
        self.assertNotIn('test-secret-password', result.model_dump_json())
        self.assertNotIn('test@example.com', result.model_dump_json())
        self.assertNotIn('must-not-leak', result.model_dump_json())

    async def test_network_failures_are_distinct_from_rejected_payloads(self):
        for cause in (TimeoutError(), aiohttp.ClientConnectionError('private host')):
            with self.subTest(cause=type(cause).__name__):
                wrapped = BringRequestException('private request detail')
                wrapped.__cause__ = cause
                bring = DummyBring(DummySession(), 'test@example.com', 'test')
                bring.load_lists.side_effect = wrapped
                with patch.object(server, 'get_bring_client', AsyncMock(return_value=bring)), self.assertLogs(server.logger):
                    result = await self.invoke()
                error = result.structuredContent['error']
                self.assertEqual(error['category'], 'temporary')
                self.assertIsNone(error['http_status'])
                self.assertTrue(error['retryable'])
                self.assertNotIn('private', result.model_dump_json())

    async def test_nested_http_cause_is_reported_without_parsing_exception_strings(self):
        cause = aiohttp.ClientResponseError(SimpleNamespace(real_url='https://private.invalid'), (),
            status=400, message='Bad Request')
        error = BringRequestException('private wrapper')
        error.__cause__ = cause
        bring = DummyBring(DummySession(), 'test@example.com', 'test')
        bring.load_lists.side_effect = error
        with patch.object(server, 'get_bring_client', AsyncMock(return_value=bring)), self.assertLogs(server.logger):
            result = await self.invoke()
        self.assertEqual(result.structuredContent['error']['http_status'], 400)
        self.assertIn('HTTP 400', result.content[0].text)
        self.assertNotIn('private', result.model_dump_json())

    async def test_quoted_credentials_inside_error_messages_are_redacted(self):
        for message in (
            'Invalid payload: {"access_token":"opaque-secret"}',
            'Invalid payload: {"refresh_token": "opaque secret with spaces"}',
            "Invalid password='opaque secret with spaces'",
        ):
            with self.subTest(message=message):
                body = json.dumps({'message': message}).encode()
                with patch.object(ErrorSession, 'body', body), self.assertLogs(server.logger):
                    result = await self.invoke()
                self.assertNotIn('opaque', result.model_dump_json())
                self.assertNotIn('with spaces', result.model_dump_json())

    async def test_successful_http_response_is_left_readable_by_bring(self):
        with patch.object(ErrorSession, 'status', 200), patch.object(ErrorSession, 'body', b'{"lists":[]}'):
            result = await self.invoke()
        self.assertFalse(result.isError, result.content)
        self.assertEqual(result.structuredContent, {'lists': []})

    async def test_unreadable_error_body_keeps_http_status(self):
        failed = await server.get_bring_client()
        original = failed._session.check_status

        async def truncated(response):
            response.content.read.side_effect = aiohttp.ClientPayloadError('private read detail')
            await original(response)

        failed._session.check_status = truncated
        with self.assertLogs(server.logger):
            result = await self.invoke()
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['error']['http_status'], 400)
        self.assertNotIn('private read detail', result.model_dump_json())

    async def test_plain_empty_and_html_error_bodies(self):
        for body, expected in ((b'Invalid item field', 'Invalid item field'),
                (b'', None), (b'<html>private proxy error</html>', None),
                (b'{"message":"truncated', None),
                (b'{"detail":"Invalid field"}', 'Invalid field')):
            with self.subTest(body=body):
                with patch.object(ErrorSession, 'body', body), self.assertLogs(server.logger):
                    result = await self.invoke()
                error = result.structuredContent['error']
                self.assertEqual(error.get('detail'), expected)
                self.assertEqual(error['http_status'], 400)

    async def test_real_login_http_failure_keeps_status_and_closes_session(self):
        for status in (400, 401, 403, 503):
            with self.subTest(status=status):
                await server.close_bring_client()
                sessions = []

                def create_session(**kwargs):
                    session = ErrorSession(**kwargs)
                    sessions.append(session)
                    return session

                with patch.object(Bring, 'login', REAL_LOGIN), patch.object(ErrorSession, 'status', status), \
                        patch.object(server.aiohttp, 'ClientSession', create_session), self.assertLogs(server.logger):
                    result = await self.invoke()
                self.assertTrue(result.isError)
                self.assertEqual(result.structuredContent['error']['http_status'], status)
                self.assertTrue(sessions[0].closed)
                self.assertEqual(sessions[0].calls, 1)
                self.assertIsNone(server._BRING_CLIENT)
                self.assertIsNone(server._BRING_SESSION)

    async def test_refresh_auth_failure_keeps_status_and_clears_session(self):
        failed = await server.get_bring_client()
        failed._Bring__refresh_token = 'test-refresh-token'
        server._LIST_NAME_CACHE['home'] = 'Home'
        with patch.object(ErrorSession, 'status', 401), self.assertLogs(server.logger):
            result = await self.invoke()
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['error']['http_status'], 401)
        self.assertEqual(failed._session.calls, 1)
        self.assertTrue(failed._session.closed)
        self.assertEqual(server._LIST_NAME_CACHE, {})

    async def test_http_write_failure_does_not_claim_success_or_replay_write(self):
        failed = await server.get_bring_client()
        failed.get_list = AsyncMock(return_value={'items': {
            'purchase': [{'itemId': 'Milk', 'uuid': 'milk', 'specification': ''}], 'recently': []}})
        with patch.object(ErrorSession, 'status', 503), self.assertLogs(server.logger):
            result = await self.invoke('add_item', {'list_uuid': 'home', 'item_id': 'Milk'})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['error']['http_status'], 503)
        self.assertNotIn('status', result.structuredContent)
        self.assertEqual(failed._session.calls, 1)
