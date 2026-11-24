"""Regression tests for environment addressing, transport building and request views."""

import base64
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from ..environment_resolver import build_request_url
from ..models import (
    ApiCollection, ApiProject, ApiRequest, Environment, RequestHistory, ScenarioCase)
from ..request_dto import normalize_request
from ..request_executor import LITERAL_HINT, build_transport, resolve_request

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

    def test_variables_resolve_in_every_part_of_the_request(self):
        transport = build_transport(dto(
            method='POST', url='/{{env.tenant}}/users/{id}', path_params=[entry('id', '{{ env.uid }}')],
            headers=[entry('X-Tenant', '{{tenant}}')], params=[entry('owner', '{{vars.uid}}')],
            auth={'type': 'bearer', 'token': '{{env.token}}'},
            body={'type': 'json', 'data': {'tenant': '{{ tenant }}'}},
        ), base_url='https://example.test', variables={'tenant': 'acme', 'token': 'abc', 'uid': 'a/b'})
        self.assertEqual(transport['url'], 'https://example.test/acme/users/a%2Fb')
        self.assertEqual(transport['headers'], {'X-Tenant': 'acme', 'Authorization': 'Bearer abc'})
        self.assertEqual(transport['params'], [('owner', 'a/b')])
        self.assertEqual(transport['json'], {'tenant': 'acme'})

    def test_placeholders_follow_scenario_rules_for_nesting_types_and_assertions(self):
        variables = {'user': {'id': 7}, 'count': 5, 'code': 200}
        request = dto(
            method='POST', url='/x', path_params=[], headers=[entry('X-User', 'u-{{env.user.id}}')],
            body={'type': 'json', 'data': {'n': '{{env.count}}', 'label': 'n={{count}}'}},
            assertions=[{'name': '状态码', 'type': 'status_code', 'expected': '{{ vars.code }}'}])
        transport = build_transport(request, base_url='https://example.test', variables=variables)
        self.assertEqual(transport['headers'], {'X-User': 'u-7'})
        # A placeholder that is the whole value keeps its JSON type.
        self.assertEqual(transport['json'], {'n': 5, 'label': 'n=5'})
        self.assertEqual(resolve_request(request, variables)['assertions'][0]['expected'], 200)

    def test_scripts_are_not_templates_and_credentials_are_always_text(self):
        scripts = {'pre_request': 'const t = "{{ title }}"', 'post_request': ''}
        request = dto(url='/x', path_params=[], scripts=scripts,
                      auth={'type': 'bearer', 'token': '{{env.token}}'})
        self.assertEqual(resolve_request(request, {'token': 123})['scripts'], scripts)
        transport = build_transport(request, base_url='https://example.test', variables={'token': 123})
        self.assertEqual(transport['headers'], {'Authorization': 'Bearer 123'})

    def test_body_resolves_variables_and_keeps_escaped_braces_as_text(self):
        transport = build_transport(dto(
            method='POST', url='/x', path_params=[],
            body={'type': 'raw', 'data': r'Hello {{ user }}, template: \{{ user }}'},
        ), base_url='https://example.test', variables={'user': 'Ann'})
        self.assertEqual(transport['data'], 'Hello Ann, template: {{ user }}')
        transport = build_transport(dto(
            method='POST', url='/x', path_params=[],
            body={'type': 'json', 'data': {'tpl': r'\{{ name }}', 'who': '{{env.user}}'}},
        ), base_url='https://example.test', variables={'user': 'Ann'})
        self.assertEqual(transport['json'], {'tpl': '{{ name }}', 'who': 'Ann'})

    def test_structured_variable_needs_a_json_entry_like_in_a_scenario_step(self):
        with self.assertRaises(ValidationError):
            build_transport(
                dto(url='/x', path_params=[], params=[entry('filter', '{{vars.filter}}')]),
                base_url='https://example.test', variables={'filter': {'active': True}})
        transport = build_transport(
            dto(url='/x', path_params=[], params=[entry('filter', '{{vars.filter}}', kind='json')]),
            base_url='https://example.test', variables={'filter': {'active': True}})
        self.assertEqual(transport['params'], [('filter', '{"active": true}')])

    def test_unknown_variable_is_rejected_whatever_its_form(self):
        for value, expression in (
                ('{{env.missing}}', 'env.missing'), ('{{ vars.missing }}', 'vars.missing'),
                ('Hi {{ name }}', 'name'), ('{{loop.index}}', 'loop.index'),
                ('{{steps.s1.response.body.id}}', 'steps.s1.response.body.id')):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as caught:
                    build_transport(dto(url='/x', path_params=[], headers=[entry('X-A', value)]),
                                    base_url='https://example.test', variables={'index': 1})
                self.assertEqual(caught.exception.detail['errors']['variables'],
                                 [f'变量 {{{{ {expression} }}}} 不存在或不可用{LITERAL_HINT}'])

    def test_resolved_dto_without_variables_is_sent_as_it_is(self):
        transport = build_transport(
            dto(url='/x', path_params=[], headers=[entry('X-A', '{{vars.kept}}')]),
            base_url='https://example.test')
        self.assertEqual(transport['headers'], {'X-A': '{{vars.kept}}'})

    def test_path_variable_left_unknown_or_blank_is_rejected_before_sending(self):
        for variables, field, message in (
                ({}, 'variables', '变量 {{ env.uid }} 不存在或不可用' + LITERAL_HINT),
                ({'uid': ''}, 'path_params', '启用路径参数必须提供非空值: id')):
            with self.subTest(variables=variables):
                with self.assertRaises(ValidationError) as caught:
                    build_transport(dto(path_params=[entry('id', '{{env.uid}}')]),
                                    base_url='https://example.test', variables=variables)
                self.assertEqual(caught.exception.detail['errors'][field], [message])

    def test_blank_path_value_is_rejected_instead_of_addressing_the_parent_path(self):
        with self.assertRaises(ValidationError) as caught:
            build_transport(dto(method='DELETE', path_params=[entry('id', '')]),
                            base_url='https://example.test')
        self.assertEqual(caught.exception.detail['errors']['path_params'],
                         ['启用路径参数必须提供非空值: id'])


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

    def test_unfinished_path_value_is_readable_but_never_sent(self):
        self.api_request.path_params = [entry('id', '')]
        self.api_request.save(update_fields=['path_params'])

        response = self.client.get(f'/api/api-testing/requests/{self.api_request.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['path_params'][0]['value'], '')
        # One unfinished row must not take the whole list down with it.
        response = self.client.get('/api/api-testing/requests/', {'project': self.project.id})
        self.assertEqual(response.status_code, 200)

        for action in ('execute', 'preview-url'):
            with self.subTest(action=action), patch('apps.api_testing.views.execute_request') as sender:
                response = self.client.post(
                    f'/api/api-testing/requests/{self.api_request.id}/{action}/',
                    {'environment_id': self.environment.id}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['errors']['path_params'],
                                 ['启用路径参数必须提供非空值: id'])
                sender.assert_not_called()
        self.assertFalse(RequestHistory.objects.exists())

    def test_unfinished_path_value_can_be_saved_and_filled_in_by_the_sent_draft(self):
        url = f'/api/api-testing/requests/{self.api_request.id}/'
        response = self.client.patch(url, {'path_params': [entry('id', '')]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.api_request.refresh_from_db()
        self.assertEqual(self.api_request.path_params, [entry('id', '')])

        response = self.client.post(
            f'{url}preview-url/',
            {'environment_id': self.environment.id, 'path_params': [entry('id', '7')]},
            format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], 'https://test.example.com/api/v1/users/7')

    def test_execute_and_preview_resolve_environment_variables(self):
        # Environments saved from the page wrap every value as initial/current.
        self.environment.variables = {
            'tenant': 'acme', 'token': {'initialValue': 'old', 'currentValue': 'abc'},
            'uid': {'initialValue': '42', 'currentValue': ''}}
        self.environment.save(update_fields=['variables'])
        self.api_request.url = '/{{env.tenant}}/users/{id}'
        self.api_request.path_params = [entry('id', '{{env.uid}}')]
        self.api_request.headers = [entry('Authorization', 'Bearer {{env.token}}')]
        self.api_request.params = [entry('tenant', '{{tenant}}')]
        self.api_request.save()
        url = f'/api/api-testing/requests/{self.api_request.id}/'
        address = 'https://test.example.com/api/v1/acme/users/42'

        response = self.client.post(
            f'{url}preview-url/', {'environment_id': self.environment.id}, format='json')
        self.assertEqual(response.data['url'], address)

        answer = Mock(status_code=200, headers={}, text='ok', request=None)
        with patch('requests.Session.request', return_value=answer) as sender:
            response = self.client.post(
                f'{url}execute/', {'environment_id': self.environment.id}, format='json')
        self.assertEqual(response.status_code, 200)
        sent = sender.call_args.kwargs
        self.assertEqual(sent['url'], address)
        self.assertEqual(sent['headers'], {'Authorization': 'Bearer abc'})
        self.assertEqual(sent['params'], [('tenant', 'acme')])

    def test_unknown_variable_blocks_sending_and_names_the_variable(self):
        url = f'/api/api-testing/requests/{self.api_request.id}/execute/'
        for value, expression in (('Bearer {{env.missing}}', 'env.missing'),
                                  ('Bearer {{ missing }}', 'missing')):
            self.api_request.headers = [entry('Authorization', value)]
            self.api_request.save(update_fields=['headers'])
            with self.subTest(value=value), patch('requests.Session.request') as sender:
                response = self.client.post(
                    url, {'environment_id': self.environment.id}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['errors']['variables'],
                                 [f'变量 {{{{ {expression} }}}} 不存在或不可用{LITERAL_HINT}'])
                sender.assert_not_called()
        self.assertFalse(RequestHistory.objects.exists())

    def test_execute_resolves_variables_in_assertions(self):
        self.environment.variables = {'code': {'initialValue': 200, 'currentValue': 200}}
        self.environment.save(update_fields=['variables'])
        self.api_request.assertions = [
            {'name': '状态码', 'type': 'status_code', 'expected': '{{env.code}}'}]
        self.api_request.save(update_fields=['assertions'])
        answer = Mock(status_code=200, headers={}, text='ok', request=None)
        with patch('requests.Session.request', return_value=answer):
            response = self.client.post(
                f'/api/api-testing/requests/{self.api_request.id}/execute/',
                {'environment_id': self.environment.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['assertions_results'][0]['passed'])

    def test_debug_step_resolves_environment_and_initial_variables_in_the_address(self):
        self.environment.variables = {'tenant': 'acme'}
        self.environment.save(update_fields=['variables'])
        scenario = ScenarioCase.objects.create(
            project=self.project, name='场景', variables={'user_id': '42'}, created_by=self.user)
        answer = Mock(status_code=200, headers={}, text='ok', request=None)
        with patch('requests.Session.request', return_value=answer) as sender:
            response = self.client.post(
                f'/api/api-testing/scenario-cases/{scenario.id}/debug-step/',
                {'environment_id': self.environment.id, 'request': {
                    'url': '/{{env.tenant}}/users/{id}',
                    'path_params': [entry('id', '{{vars.user_id}}')]}},
                format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sender.call_args.kwargs['url'],
                         'https://test.example.com/api/v1/acme/users/42')

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
