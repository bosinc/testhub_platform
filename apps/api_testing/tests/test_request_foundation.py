"""Database-free foundation tests; run after django.setup(), without migrate."""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import migrations
from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.api_testing.environment_resolver import (
    EnvironmentResolver, build_request_url, validate_base_url,
)
from apps.api_testing.models import ApiProject, ApiRequest, Environment
from apps.api_testing.request_dto import (
    REQUEST_FIELDS, normalize_request, request_from_model,
)
from apps.api_testing.serializers import ApiRequestSerializer, EnvironmentSerializer


migration = import_module('apps.api_testing.migrations.0003_request_foundation')


def entry(key, value='', **metadata):
    return {'key': key, 'value': value, **metadata}


class RequestDTOTests(SimpleTestCase):
    def assert_invalid(self, data):
        with self.assertRaises(ValidationError) as caught:
            normalize_request(data)
        self.assertIn('errors', caught.exception.detail)

    def test_fields_defaults_and_detached_normalization(self):
        self.assertEqual(REQUEST_FIELDS, (
            'method', 'url', 'request_type', 'headers', 'params', 'path_params',
            'body', 'auth', 'scripts', 'assertions', 'extractors', 'settings',
        ))
        result = normalize_request({})
        self.assertEqual(set(result), set(REQUEST_FIELDS))
        self.assertEqual(result['settings'], {
            'timeout': 30, 'connect_timeout': 5, 'verify_ssl': True, 'follow_redirects': True,
        })
        result['headers'].append(entry('modified'))
        result['settings']['timeout'] = 1
        self.assertEqual(normalize_request({})['headers'], [])
        self.assertEqual(normalize_request({})['settings']['timeout'], 30)

    def test_order_metadata_disabled_and_duplicate_query_preserved(self):
        source = {'params': [
            entry('q', 'one', enabled=False, type='text', description='first'),
            entry('q', '', enabled=True, type='string', description='second'),
        ], 'headers': [entry('X-Empty', '')],
            'path_params': [entry('id', 0, type='integer')]}
        before = deepcopy(source)
        result = normalize_request(source)
        self.assertEqual(result['params'], source['params'])
        self.assertEqual(source, before)
        result['params'][0]['description'] = 'changed'
        self.assertEqual(source, before)
        self.assertEqual(result['path_params'][0]['value'], 0)

    def test_no_dict_or_non_object_entry_compatibility(self):
        for field in ('headers', 'params', 'path_params'):
            for invalid in ({}, {'key': 'value'}, None, 'x', [1], [[]]):
                with self.subTest(field=field, invalid=invalid):
                    self.assert_invalid({field: invalid})

    def test_entry_schema_is_strict(self):
        for invalid in (
            entry('k', enabled=1), entry('k', description={}), entry('k', type='unknown'),
            entry('k', extra=True), entry(1, 'v'), entry(''),
            entry('k', True, type='number'), entry('k', False, type='integer'),
            entry('k', {'nested': 1}), entry('k', float('nan')),
        ):
            with self.subTest(invalid=invalid):
                self.assert_invalid({'params': [invalid]})
        self.assertEqual(normalize_request({'params': [entry('', enabled=False)]})['params'][0]['key'], '')

    def test_path_params_independent_unique_and_require_enabled_values(self):
        for rows in (
            [entry('id', 1), entry('id', 2)],
            [entry('id', 1), entry('id', '', enabled=False)],
            [{'key': 'id'}], [entry('id', '')], [entry('id', None)],
            [entry('id', {'x': 1}, type='json')],
        ):
            with self.subTest(rows=rows):
                self.assert_invalid({'path_params': rows})
        dto = normalize_request({'url': '/users/{id}', 'params': [entry('id', 'query')],
                                 'path_params': [entry('id', '', enabled=False)]})
        self.assertEqual(dto['params'][0]['value'], 'query')
        self.assertFalse(dto['path_params'][0]['enabled'])

    def test_relative_draft_paths_and_namespaced_variables(self):
        for path in ('', '/', '/users/{id}', 'users/{id}', '/users/{{vars.id}}',
                     '/{{env.tenant}}/{{steps.abc-123.response.body.id}}/{{loop.index}}'):
            with self.subTest(path=path):
                self.assertEqual(normalize_request({'url': path})['url'], path)
        for path in ('https://example.com/a', 'ws://example.com/a', '//example.com/a',
                     '///example.com/a', '/a?q=1', '/a?', '/a#', '/a#fragment',
                     '/a\\b', '/a\nb', '/a/../b', '/%2e%2e/b', '/%252e%252e/b',
                     '%2f%2fexample.com/a', '/{{legacy}}', '/{{vars.id}', '/{broken', 10):
            with self.subTest(path=path):
                self.assert_invalid({'url': path})

    def test_json_body_preserves_all_json_types(self):
        for value in ({'items': [1]}, [1, None], 'text', 2, 1.25, True, False, None):
            with self.subTest(value=value):
                source = {'body': {'type': 'json', 'data': value}}
                self.assertEqual(normalize_request(source)['body'], source['body'])
        self.assert_invalid({'body': {'type': 'json', 'data': float('inf')}})

    def test_body_schema_and_form_order(self):
        self.assertEqual(normalize_request({'body': {}})['body'], {'type': 'none'})
        self.assertEqual(normalize_request({'body': {'type': 'raw', 'data': ''}})['body']['data'], '')
        rows = [entry('key', ''), entry('key', 'second', enabled=False, description='keep')]
        for kind in ('form-data', 'x-www-form-urlencoded'):
            dto = normalize_request({'body': {'type': kind, 'data': rows}})
            self.assertEqual([row['value'] for row in dto['body']['data']], ['', 'second'])
            for invalid in ({'key': 'value'}, None):
                self.assert_invalid({'body': {'type': kind, 'data': invalid}})
        for body in (None, [], {'type': 'unknown'}, {'type': 'none', 'data': ''},
                     {'type': 'raw', 'data': 1}, {'type': 'json', 'extra': 1}):
            self.assert_invalid({'body': body})

    def test_controlled_multipart_and_binary_files(self):
        file_data = {'filename': 'sample.txt', 'content_base64': 'aGVsbG8=', 'content_type': 'text/plain'}
        dto = normalize_request({'body': {'type': 'form-data', 'data': [entry('upload', file_data, type='file')]}})
        self.assertEqual(dto['body']['data'][0]['value'], file_data)
        dto['body']['data'][0]['value']['filename'] = 'changed.txt'
        self.assertEqual(file_data['filename'], 'sample.txt')
        self.assertEqual(normalize_request({'body': {'type': 'binary', 'data': file_data}})['body']['data'], file_data)
        for invalid in (
            '/tmp/private.txt', {'path': '/tmp/private.txt'},
            {**file_data, 'filename': '/tmp/file.txt'}, {**file_data, 'filename': '../file.txt'},
            {**file_data, 'filename': 'C:\\file.txt'}, {**file_data, 'filename': '..'},
            {**file_data, 'content_base64': 'not base64'},
            {**file_data, 'content_base64': '你好'}, {**file_data, 'content_type': 'text/plain\r\nInjected: true'},
            {**file_data, 'path': '/tmp/file.txt'},
        ):
            with self.subTest(invalid=invalid):
                self.assert_invalid({'body': {'type': 'binary', 'data': invalid}})
        self.assert_invalid({'body': {'type': 'x-www-form-urlencoded', 'data': [entry('f', file_data, type='file')]}})

    def test_auth_fixed_schema(self):
        for auth in ({}, {'type': 'none'}, {'type': 'basic', 'username': '{{env.user}}', 'password': ''},
                     {'type': 'bearer', 'token': '{{vars.token}}'},
                     {'type': 'api_key', 'key': 'X-API-Key', 'value': '', 'in': 'header'},
                     {'type': 'api_key', 'key': 'key', 'value': 'v', 'in': 'query'}):
            with self.subTest(auth=auth):
                self.assertEqual(normalize_request({'auth': auth})['auth']['type'], auth.get('type', 'none'))
        for invalid in ([], {'type': 'oauth2'}, {'type': 'none', 'token': 'x'},
                        {'type': 'basic', 'username': 1}, {'type': 'bearer', 'password': 'x'},
                        {'type': 'api_key', 'in': 'cookie'}, {'type': 'api_key', 'location': 'header'}):
            self.assert_invalid({'auth': invalid})

    def test_settings_bounds_strict_types_and_fixed_fields(self):
        dto = normalize_request({'settings': {'timeout': 300, 'connect_timeout': 60,
                                             'verify_ssl': False, 'follow_redirects': False}})
        self.assertEqual(dto['settings']['timeout'], 300)
        self.assertEqual(normalize_request({'settings': {'timeout': 0.5}})['settings']['timeout'], 0.5)
        for key, limit in (('timeout', 300), ('connect_timeout', 60)):
            for value in (True, False, 0, -1, limit + 1, '30', None, float('inf'), float('nan'), 10 ** 1000):
                with self.subTest(key=key, value=value):
                    self.assert_invalid({'settings': {key: value}})
        for settings in ([], {'retry': 1}, {'verify_ssl': 1}, {'follow_redirects': 'false'}):
            self.assert_invalid({'settings': settings})

    def test_scripts_and_rule_lists_are_detached_and_strict(self):
        source = {'scripts': {'pre_request': 'pre()', 'post_request': 'post()'},
                  'assertions': [{'type': 'status', 'enabled': False, 'expected': 200}],
                  'extractors': [{'name': 'token', 'path': '$.token', 'enabled': True}]}
        dto = normalize_request(source)
        self.assertEqual(dto['scripts'], source['scripts'])
        dto['assertions'][0]['expected'] = 201
        self.assertEqual(source['assertions'][0]['expected'], 200)
        for data in ({'scripts': []}, {'scripts': {'pre_request': 1}}, {'scripts': {'other': ''}},
                     {'assertions': {}}, {'extractors': [None]}, {'assertions': [{'enabled': 1}]},
                     {'unknown': 1}, {'method': 'BOGUS'}, {'request_type': 'FTP'}):
            self.assert_invalid(data)

    def test_model_projection_and_overrides(self):
        obj = ApiRequest(name='original', url='/users/{id}', path_params=[entry('id', 'a')],
                         pre_request_script='pre()', post_request_script='post()')
        dto = request_from_model(obj, {'method': 'POST', 'params': [entry('q', '1')]})
        self.assertEqual(set(dto), set(REQUEST_FIELDS))
        self.assertEqual(dto['scripts'], {'pre_request': 'pre()', 'post_request': 'post()'})
        self.assertEqual(dto['method'], 'POST')
        dto['path_params'][0]['value'] = 'changed'
        self.assertEqual(obj.path_params[0]['value'], 'a')
        self.assertEqual(obj.method, 'GET')
        with self.assertRaises(ValidationError):
            request_from_model(obj, {'name': 'not a DTO field'})
        obj.headers = {'legacy': 'value'}
        with self.assertRaises(ValidationError):
            request_from_model(obj)


class URLFoundationTests(SimpleTestCase):
    def test_base_url_validation_and_blank_draft(self):
        self.assertEqual(validate_base_url('', require_base_url=False), '')
        for url in ('http://localhost:8000/api', 'https://example.com/api/v1/',
                    'http://127.0.0.1:8080', 'https://[::1]:8443/api', 'https://example.com/a%20b'):
            self.assertEqual(validate_base_url(url), url)
        with self.assertRaises(ValidationError):
            validate_base_url('')

    def test_invalid_base_urls(self):
        for url in ('https://user:pass@example.com/api', 'https://user@example.com',
                    'https://example.com/?', 'https://example.com/#', 'https://example.com?q=1',
                    'https://example.com#frag', 'https://{{env.host}}',
                    'https://example.com/{{vars.path}}', 'https://example.com/%257B%257Benv.path%257D%257D',
                    'https://example.com/../a', 'https://example.com/%2E%2E/a',
                    'https://example.com/a%2f..%2fb', 'https://example.com/%252e%252e/a',
                    'https://example.com:abc', 'https://example.com:65536',
                    'https://example.com:0', 'https://example.com:', 'ftp://example.com',
                    '//example.com', 'https://', 'https://example.com\\a',
                    'https://example.com/%5ca', 'https://example.com/\n',
                    'https://example.com/%00', ' https://example.com', None):
            with self.subTest(url=url), self.assertRaises(ValidationError) as caught:
                validate_base_url(url, require_base_url=False)
            self.assertIn('errors', caught.exception.detail)

    def test_join_preserves_base_path_and_maps_websocket_scheme(self):
        for base in ('https://example.com/api/v1', 'https://example.com/api/v1/'):
            for path in ('users', '/users'):
                self.assertEqual(build_request_url(base, path), 'https://example.com/api/v1/users')
        self.assertEqual(build_request_url('https://example.com/api', ''), 'https://example.com/api/')
        self.assertEqual(build_request_url('http://example.com/api', '/ws', request_type='WEBSOCKET'), 'ws://example.com/api/ws')
        self.assertEqual(build_request_url('https://example.com/api', '/ws', request_type='WEBSOCKET'), 'wss://example.com/api/ws')

    def test_path_parameter_encoding_repetition_and_disabled_unused_rows(self):
        value = 'a/b ?#&=中文'
        rows = [entry('id', value), entry('unused', '', enabled=False)]
        before = deepcopy(rows)
        url = build_request_url('https://example.com/api', '/users/{id}/{id}', rows)
        encoded = 'a%2Fb%20%3F%23%26%3D%E4%B8%AD%E6%96%87'
        self.assertEqual(url, f'https://example.com/api/users/{encoded}/{encoded}')
        self.assertEqual(rows, before)
        self.assertEqual(build_request_url('https://example.com', '/{id}', [entry('id', 0)]), 'https://example.com/0')

    def test_missing_duplicate_and_unused_enabled_path_parameters_rejected(self):
        for path, rows in (
            ('/{id}', None), ('/{id}', []), ('/{id}', [entry('id', '', enabled=False)]),
            ('/{id}', [entry('id', '')]), ('/{id}', [{'key': 'id'}]),
            ('/{id}', [entry('id', 1), entry('id', 2)]),
            ('/users', [entry('unused', 'v')]), ('/{id}', {'id': 1}),
        ):
            with self.subTest(path=path, rows=rows), self.assertRaises(ValidationError):
                build_request_url('https://example.com/api', path, rows)

    def test_absolute_traversal_inline_query_and_unresolved_execution_rejected(self):
        for path in ('http://example.org/a', '//example.org/a', '/users?q=1', '/users#frag',
                     '/a\\b', '/a\tb', '/a/../b', '/a/%2e%2e/b', '/a/%252e%252e/b',
                     '/a/%2e%2e%2fb', '/{{vars.id}}', '/{{env.path}}', '/{broken',
                     '/%257B%257Benv.path%257D%257D', '%2f%2fexample.com'):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                build_request_url('https://example.com/api', path)
        for value in ('..', '%2e%2e', '{{vars.id}}', '%257B%257Bvars.id%257D%257D', '\\', '\n', 'a/../b'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                build_request_url('https://example.com/api', '/{id}', [entry('id', value)])
        with self.assertRaises(ValidationError):
            build_request_url('https://example.com', '/a', request_type='FTP')


class EnvironmentResolverTests(SimpleTestCase):
    def setUp(self):
        self.user = get_user_model()(pk=1, username='tester')
        self.environment = Environment(pk=7, scope='LOCAL', project_id=10,
                                       base_url='https://example.com/api', variables={'nested': {'values': [1]}})
        members = Mock()
        members.filter.return_value.exists.return_value = False
        self.project = SimpleNamespace(pk=10, owner_id=1, members=members,
                                       is_deleted=False, workspace_project_id=None)
        self.env_lookup = self.enterContext(patch.object(Environment.objects, 'get', return_value=self.environment))
        self.projects = self.enterContext(patch.object(ApiProject.all_objects, 'select_related'))
        self.projects.return_value.get.return_value = self.project

    def test_resolve_returns_orm_instance_and_accepts_explicit_string_ids(self):
        result = EnvironmentResolver.resolve('7', user=self.user, project_id='10')
        self.assertIs(result, self.environment)
        self.env_lookup.assert_called_once_with(pk=7)
        self.projects.return_value.get.assert_called_once_with(pk=10)

    def test_no_implicit_environment_or_invalid_id_lookup(self):
        for identifier in (None, '', True, False, 'abc', 1.5, {}, [], 0, -1, 2 ** 64):
            with self.subTest(identifier=identifier), self.assertRaises(ValidationError):
                EnvironmentResolver.resolve(identifier, user=self.user, project_id=10)
        self.env_lookup.assert_not_called()

    def test_nonexistent_environment_is_404_with_unified_errors(self):
        self.env_lookup.side_effect = Environment.DoesNotExist
        with self.assertRaises(NotFound) as caught:
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn('environment_id', caught.exception.detail['errors'])

    def test_local_environment_requires_current_project_and_membership(self):
        with self.assertRaises(ValidationError):
            EnvironmentResolver.resolve(7, user=self.user)
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=self.user, project_id=11)
        self.project.owner_id = 2
        with self.assertRaises(PermissionDenied) as caught:
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn('errors', caught.exception.detail)
        self.project.members.filter.return_value.exists.return_value = True
        self.assertIs(EnvironmentResolver.resolve(7, user=self.user, project_id=10), self.environment)
        self.project.members.filter.assert_called_with(pk=1)

    def test_global_environment_open_to_authenticated_users_only(self):
        self.environment.scope = 'GLOBAL'
        self.environment.project_id = None
        self.assertIs(EnvironmentResolver.resolve(7, user=self.user), self.environment)
        self.projects.assert_not_called()
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=AnonymousUser())
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.snapshot(self.environment, user=None)

    def test_deleted_api_or_workspace_project_blocks_even_global_execution(self):
        self.project.is_deleted = True
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.project.is_deleted = False
        self.project.workspace_project_id = 100
        self.project.workspace_project = SimpleNamespace(is_deleted=True)
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.environment.scope = 'GLOBAL'
        self.environment.project_id = None
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)

    def test_missing_or_invalid_project_and_snapshot_project_mismatch(self):
        for identifier in (True, 'invalid', 0):
            with self.subTest(identifier=identifier), self.assertRaises(ValidationError):
                EnvironmentResolver.resolve(7, user=self.user, project_id=identifier)
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.snapshot(self.environment, user=self.user, project_id=11)
        self.projects.return_value.get.side_effect = ApiProject.DoesNotExist
        with self.assertRaises(NotFound) as caught:
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.assertIn('project_id', caught.exception.detail['errors'])

    def test_global_environment_attached_to_deleted_project_is_blocked(self):
        self.environment.scope = 'GLOBAL'
        self.project.is_deleted = True
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.resolve(7, user=self.user)
        self.project.is_deleted = False
        self.assertIs(EnvironmentResolver.resolve(7, user=self.user), self.environment)

    def test_blank_draft_allowed_only_when_explicitly_requested(self):
        self.environment.base_url = ''
        self.assertIs(EnvironmentResolver.resolve(7, user=self.user, project_id=10, require_base_url=False), self.environment)
        with self.assertRaises(ValidationError):
            EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        with self.assertRaises(ValidationError):
            EnvironmentResolver.snapshot(self.environment, user=self.user, project_id=10)
        self.environment.base_url = 'https://example.com/?query'
        with self.assertRaises(ValidationError):
            EnvironmentResolver.resolve(7, user=self.user, project_id=10, require_base_url=False)

    def test_snapshot_deep_copy_and_revalidates_permissions_and_url(self):
        resolved = EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        snapshot = EnvironmentResolver.snapshot(resolved, user=self.user, project_id=10)
        self.assertEqual(set(snapshot), {'id', 'base_url', 'variables'})
        snapshot['variables']['nested']['values'].append(2)
        self.assertEqual(self.environment.variables['nested']['values'], [1])
        self.assertEqual(self.projects.return_value.get.call_count, 2)
        self.project.owner_id = 2
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.snapshot(resolved, user=self.user, project_id=10)
        self.project.owner_id = 1
        resolved.base_url = 'https://example.com/#fragment'
        with self.assertRaises(ValidationError):
            EnvironmentResolver.snapshot(resolved, user=self.user, project_id=10)

    def test_snapshot_rechecks_project_deletion_and_rejects_unsaved_or_wrong_inputs(self):
        resolved = EnvironmentResolver.resolve(7, user=self.user, project_id=10)
        self.project.is_deleted = True
        with self.assertRaises(PermissionDenied):
            EnvironmentResolver.snapshot(resolved, user=self.user, project_id=10)
        for invalid in (None, {}, Environment(scope='GLOBAL', base_url='https://example.com')):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                EnvironmentResolver.snapshot(invalid, user=self.user)


class FoundationSerializerTests(SimpleTestCase):
    def test_serializer_normalizes_all_request_fields(self):
        serializer = ApiRequestSerializer(data={
            'name': 'test', 'url': '/users/{id}', 'path_params': [entry('id', 1)],
            'params': [entry('q', 'one'), entry('q', 'two', enabled=False)],
            'scripts': {'pre_request': 'pre()', 'post_request': 'post()'},
            'settings': {'timeout': 10},
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['headers'], [])
        self.assertEqual(len(serializer.validated_data['params']), 2)
        self.assertEqual(serializer.validated_data['settings']['connect_timeout'], 5)
        self.assertEqual(serializer.validated_data['pre_request_script'], 'pre()')
        self.assertEqual(serializer.validated_data['post_request_script'], 'post()')
        self.assertNotIn('scripts', serializer.validated_data)

    def test_serializer_rejects_legacy_lists_body_auth_settings_and_absolute_url(self):
        for change in ({'headers': {}}, {'params': {}}, {'path_params': {}},
                       {'body': {'type': 'form-data', 'data': {'key': 'value'}}},
                       {'auth': {'type': 'none', 'password': 'unused'}},
                       {'settings': {'timeout': True}}, {'url': 'https://example.com/a'}, {'url': 12}):
            with self.subTest(change=change):
                serializer = ApiRequestSerializer(data={'name': 'test', 'url': '/', **change})
                self.assertFalse(serializer.is_valid())
                self.assertIn('errors', serializer.errors)

    def test_serializer_partial_update_preserves_other_fields_and_projects_scripts(self):
        obj = ApiRequest(name='test', url='/users', pre_request_script='old_pre()', post_request_script='old_post()')
        serializer = ApiRequestSerializer(obj, data={'scripts': {'pre_request': 'new_pre()'},
                                                    'settings': {'timeout': 10}}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['pre_request_script'], 'new_pre()')
        self.assertEqual(serializer.validated_data['post_request_script'], 'old_post()')
        self.assertNotIn('headers', serializer.validated_data)
        self.assertEqual(serializer.validated_data['settings']['timeout'], 10)
        conflict = ApiRequestSerializer(obj, data={'scripts': {'pre_request': 'one'},
                                                 'pre_request_script': 'two'}, partial=True)
        self.assertFalse(conflict.is_valid())
        self.assertIn('errors', conflict.errors)

    def test_serializer_output_includes_dto_defaults_and_script_projection(self):
        user = get_user_model()(pk=1, username='tester')
        obj = ApiRequest(name='test', url='/users', created_by=user, pre_request_script='pre()')
        data = ApiRequestSerializer(obj).data
        self.assertEqual(data['scripts'], {'pre_request': 'pre()', 'post_request': ''})
        self.assertEqual(data['settings']['timeout'], 30)
        self.assertIn('path_params', data)

    def test_environment_serializer_blank_draft_and_nonempty_validation(self):
        serializer = EnvironmentSerializer(data={'name': 'draft', 'scope': 'GLOBAL', 'base_url': ''})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data['project'])
        for url in ('https://example.com?q=1', 'https://user@example.com',
                    'https://example.com/{{vars.path}}', 'https://example.com/\n'):
            serializer = EnvironmentSerializer(data={'name': 'test', 'scope': 'GLOBAL', 'base_url': url})
            self.assertFalse(serializer.is_valid())
            self.assertIn('errors', serializer.errors)

    def test_environment_partial_update_preserves_local_project_requirement(self):
        project = ApiProject(pk=10, name='project')
        env = Environment(pk=7, scope='LOCAL', project=project)
        serializer = EnvironmentSerializer(env, data={'base_url': 'https://example.com/api'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer = EnvironmentSerializer(env, data={'project': None}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn('errors', serializer.errors)


class FoundationMigrationTests(SimpleTestCase):
    def test_schema_fields_defaults_and_stage_one_does_not_delete_scenario_tables(self):
        self.assertIs(ApiRequest._meta.get_field('headers').default, list)
        self.assertIs(ApiRequest._meta.get_field('params').default, list)
        self.assertIs(ApiRequest._meta.get_field('path_params').default, list)
        self.assertIs(ApiRequest._meta.get_field('settings').default, dict)
        self.assertEqual(Environment._meta.get_field('base_url').default, '')
        self.assertTrue(Environment._meta.get_field('base_url').blank)
        self.assertEqual(migration.Migration.dependencies, [('api_testing', '0002_initial')])
        self.assertFalse(any(isinstance(op, (migrations.DeleteModel, migrations.RemoveField))
                             for op in migration.Migration.operations))

    def test_convert_dict_entries_and_path_params_without_guessing_urls(self):
        row = SimpleNamespace(
            url='https://example.com/api/users/{id}?q={not_path}', headers={'X-Empty': ''},
            params={'id': 0, 'q': '', 'not_path': 'keep-query', 'variable': '{{vars.id}}'},
            path_params=[], body={'type': 'form-data', 'data': {'first': '', 'second': False}},
        )
        before = deepcopy(row.__dict__)
        converted = migration.convert_request(row)
        self.assertEqual([item['key'] for item in converted['params']], ['q', 'not_path', 'variable'])
        self.assertEqual(converted['path_params'][0]['value'], 0)
        self.assertEqual(converted['headers'][0]['value'], '')
        self.assertEqual([item['value'] for item in converted['body']['data']], ['', False])
        self.assertEqual(row.__dict__, before)
        self.assertEqual(row.url, before['url'])

    def test_existing_lists_metadata_disabled_duplicates_and_empty_values_preserved(self):
        params = [entry('id', '', enabled=False, type='integer', description='empty path'),
                  entry('q', 'first', enabled=True, type='text', description='one'),
                  entry('q', '', enabled=False, type='string', description='two')]
        headers = [entry('X-Test', '', enabled=False, type='text', description='keep')]
        forms = [entry('field', '', enabled=False, type='text', description='blank')]
        row = SimpleNamespace(url='/users/{id}/{{vars.query}}', params=params, headers=headers,
                              path_params=[entry('existing', 'value')], body={'type': 'x-www-form-urlencoded', 'data': forms})
        converted = migration.convert_request(row)
        self.assertEqual(converted['headers'], headers)
        self.assertEqual(converted['params'], params[1:])
        self.assertEqual(converted['path_params'], row.path_params + params[:1])
        self.assertEqual(converted['body']['data'], forms)
        converted['path_params'][-1]['description'] = 'changed'
        self.assertEqual(params[0]['description'], 'empty path')

    def test_legacy_metadata_dict_and_form_alias_are_converted_only_in_migration(self):
        metadata = {'value': '', 'enabled': False, 'type': 'text', 'description': 'keep'}
        row = SimpleNamespace(url='/users/{id}', headers={'X': metadata}, params={'id': metadata},
                              path_params=[], body={'type': 'formdata', 'data': {'field': metadata}})
        converted = migration.convert_request(row)
        self.assertEqual(converted['headers'], [{'key': 'X', **metadata}])
        self.assertEqual(converted['path_params'], [{'key': 'id', **metadata}])
        self.assertEqual(converted['body'], {'type': 'form-data', 'data': [{'key': 'field', **metadata}]})

    def test_historical_model_and_database_alias_are_used_without_actual_database_io(self):
        row = SimpleNamespace(pk=12, url='/users/{id}', headers={'X': ''}, params={'id': 'a'},
                              path_params=[], body={}, save=Mock())
        historical = Mock()
        historical.objects.using.return_value.all.return_value.iterator.return_value = [row]
        registry = Mock()
        registry.get_model.return_value = historical
        schema_editor = SimpleNamespace(connection=SimpleNamespace(alias='archive'))
        migration.migrate_request_entries(registry, schema_editor)
        registry.get_model.assert_called_once_with('api_testing', 'ApiRequest')
        historical.objects.using.assert_called_once_with('archive')
        row.save.assert_called_once_with(using='archive', update_fields=['headers', 'params', 'path_params', 'body'])
        self.assertEqual(row.path_params[0]['value'], 'a')
        self.assertEqual(row.params, [])

    def test_unrecognized_data_fails_with_row_identity_instead_of_discarding(self):
        row = SimpleNamespace(pk=99, url='/', headers=None, params={}, path_params=[], body={}, save=Mock())
        historical = Mock()
        historical.objects.using.return_value.all.return_value.iterator.return_value = [row]
        registry = Mock()
        registry.get_model.return_value = historical
        with self.assertRaisesMessage(ValueError, 'ApiRequest 99'):
            migration.migrate_request_entries(registry, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        row.save.assert_not_called()
