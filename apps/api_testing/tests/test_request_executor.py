"""Regression tests for environment addressing, transport building and request views."""

import base64
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from ..environment_resolver import build_request_url
from ..models import ApiCollection, ApiProject, ApiRequest, Environment, RequestHistory
from ..request_dto import normalize_request
from ..request_executor import build_transport

User = get_user_model()


def entry(key, value='', enabled=True, kind='string'):
    return {'key': key, 'value': value, 'enabled': enabled, 'type': kind, 'description': ''}


def dto(**overrides):
    payload = {'url': '/users/{id}', 'path_params': [entry('id', 'a/b')]}
    payload.update(overrides)
    return normalize_request(payload)


class UrlBuilderTests(SimpleTestCase):
    def test_keeps_environment_base_path_and_encodes_path_parameter(self):
        url = build_request_url('https://test.example.com/api/v1', '/users/{id}',
                                [entry('id', 'a/b')])
        self.assertEqual(url, 'https://test.example.com/api/v1/users/a%2Fb')

    def test_websocket_maps_scheme_and_keeps_base_path(self):
        url = build_request_url('https://test.example.com/api/v1', '/socket',
                                request_type='WEBSOCKET')
        self.assertEqual(url, 'wss://test.example.com/api/v1/socket')

    def test_rejects_missing_and_unused_path_parameters(self):
        with self.assertRaises(ValidationError):
            build_request_url('https://example.test', '/users/{id}', [])
        with self.assertRaises(ValidationError):
            build_request_url('https://example.test', '/users', [entry('id', '1')])

    def test_rejects_traversal_in_base_or_path(self):
        for base, path in (('https://example.test/api/../admin', '/x'),
                           ('https://example.test/api', '/../admin'),
                           ('https://example.test/api', '/%2e%2e/admin')):
            with self.subTest(base=base, path=path), self.assertRaises(ValidationError):
                build_request_url(base, path)


class TransportTests(SimpleTestCase):
    def test_duplicate_query_and_disabled_rows_are_preserved(self):
        transport = build_transport(dto(url='/search', path_params=[], params=[
            entry('tag', 'a'), entry('tag', 'b'), entry('tag', 'c', enabled=False),
        ]), base_url='https://example.test')
        self.assertEqual(transport['params'], [('tag', 'a'), ('tag', 'b')])

    def test_form_urlencoded_is_encoded_instead_of_sent_as_json(self):
        transport = build_transport(dto(url='/login', path_params=[], body={
            'type': 'x-www-form-urlencoded',
            'data': [entry('user', 'a b'), entry('skip', '1', enabled=False)],
        }), base_url='https://example.test')
        self.assertEqual(transport['data'], 'user=a+b')
        self.assertIsNone(transport['json'])
        self.assertEqual(transport['headers']['Content-Type'], 'application/x-www-form-urlencoded')

    def test_form_data_file_is_decoded_from_base64(self):
        content = base64.b64encode(b'payload').decode()
        transport = build_transport(dto(url='/upload', path_params=[], body={
            'type': 'form-data', 'data': [{
                'key': 'file', 'enabled': True, 'type': 'file', 'description': '',
                'value': {'filename': 'a.txt', 'content_base64': content,
                          'content_type': 'text/plain'},
            }],
        }), base_url='https://example.test')
        self.assertEqual(transport['files']['file'][0], 'a.txt')
        self.assertEqual(transport['files']['file'][1], b'payload')

    def test_bearer_and_api_key_auth_are_applied(self):
        transport = build_transport(dto(url='/x', path_params=[], auth={
            'type': 'bearer', 'token': 'token-value'}), base_url='https://example.test')
        self.assertEqual(transport['headers']['Authorization'], 'Bearer token-value')
        transport = build_transport(dto(url='/x', path_params=[], auth={
            'type': 'api_key', 'key': 'api_key', 'value': 'secret', 'in': 'query',
        }), base_url='https://example.test')
        self.assertEqual(transport['params'], [('api_key', 'secret')])

    def test_settings_drive_timeout_and_verification(self):
        transport = build_transport(dto(url='/x', path_params=[], settings={
            'timeout': 12, 'connect_timeout': 3, 'verify_ssl': False,
            'follow_redirects': False}), base_url='https://example.test')
        self.assertEqual(transport['timeout'], (3, 12))
        self.assertFalse(transport['verify'])
        self.assertFalse(transport['allow_redirects'])

    def test_unresolved_variable_is_rejected_before_sending(self):
        with self.assertRaises(ValidationError):
            build_transport(dto(url='/users/{{env.missing}}', path_params=[]),
                            base_url='https://example.test', variables={})


class RequestViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='request-view-user', password='password')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.collection = ApiCollection.objects.create(name='集合', project=self.project)
        self.api_request = ApiRequest.objects.create(
            collection=self.collection, name='查询用户', method='GET', url='/users/{id}',
            path_params=[entry('id', '42')], created_by=self.user)
        self.environment = Environment.objects.create(
            name='测试环境', scope='LOCAL', project=self.project, base_url='https://test.example.com/api/v1',
            variables={'token': 'abc'}, created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_preview_url_uses_selected_environment(self):
        response = self.client.post(
            f'/api/api-testing/requests/{self.api_request.id}/preview-url/',
            {'environment_id': self.environment.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'],
                         'https://test.example.com/api/v1/users/42')

    def test_preview_url_requires_environment(self):
        response = self.client.post(
            f'/api/api-testing/requests/{self.api_request.id}/preview-url/', {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_preview_url_rejects_environment_from_another_project(self):
        other_project = ApiProject.objects.create(
            name='其他项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        foreign = Environment.objects.create(
            name='其他环境', scope='LOCAL', project=other_project,
            base_url='https://other.example.com', created_by=self.user)
        response = self.client.post(
            f'/api/api-testing/requests/{self.api_request.id}/preview-url/',
            {'environment_id': foreign.id}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_execute_without_environment_does_not_send_or_store_history(self):
        with patch('apps.api_testing.views.execute_request') as sender:
            response = self.client.post(
                f'/api/api-testing/requests/{self.api_request.id}/execute/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        sender.assert_not_called()
        self.assertFalse(RequestHistory.objects.exists())

    def test_execute_stores_history_and_returns_assertion_results(self):
        fake_response = Mock(status_code=200, headers={'content-type': 'application/json'},
                             text='{"ok": true}')
        fake_response.json.return_value = {'ok': True}
        with patch('apps.api_testing.views.execute_request', return_value={
            'response': fake_response, 'response_time': 12.5,
            'request': {'url': 'https://test.example.com/api/v1/users/42', 'method': 'GET',
                        'headers': {}, 'params': {}, 'body': None},
        }):
            response = self.client.post(
                f'/api/api-testing/requests/{self.api_request.id}/execute/',
                {'environment_id': self.environment.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status_code'], 200)
        history = RequestHistory.objects.get()
        self.assertEqual(history.environment_id, self.environment.id)
        self.assertEqual(history.request_data['url'],
                         'https://test.example.com/api/v1/users/42')

    def test_execute_merges_submitted_draft_without_persisting_it(self):
        fake_response = Mock(status_code=200, headers={}, text='ok')
        fake_response.json.side_effect = ValueError
        captured = {}

        def fake_execute(draft, *, base_url, variables=None, session=None):
            captured['draft'] = draft
            captured['base_url'] = base_url
            return {'response': fake_response, 'response_time': 1.0,
                    'request': {'url': base_url + draft['url'], 'method': draft['method'],
                                'headers': {}, 'params': {}, 'body': None}}

        with patch('apps.api_testing.views.execute_request', side_effect=fake_execute):
            response = self.client.post(
                f'/api/api-testing/requests/{self.api_request.id}/execute/',
                {'environment_id': self.environment.id, 'url': '/draft/{id}',
                 'path_params': [entry('id', '7')], 'pre_request_script': 'pre()'},
                format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['draft']['url'], '/draft/{id}')
        self.assertEqual(captured['draft']['scripts']['pre_request'], 'pre()')
        self.assertEqual(captured['base_url'], 'https://test.example.com/api/v1')
        self.api_request.refresh_from_db()
        self.assertEqual(self.api_request.url, '/users/{id}')
        self.assertEqual(self.api_request.pre_request_script, '')
