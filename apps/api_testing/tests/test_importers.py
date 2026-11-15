import json
import os
import unittest
from unittest.mock import patch

from ..importers import parse_collection


def entry(key, value='', enabled=True, kind='string', description=''):
    return {'key': key, 'value': value, 'enabled': enabled, 'type': kind, 'description': description}


class CollectionImportParserTests(unittest.TestCase):
    def parse(self, document):
        return parse_collection(json.dumps(document).encode(), 'collection.json')

    def postman(self, request, **collection):
        return self.parse({'info': {'name': 'Collection'}, 'item': [
            {'name': 'Request', 'request': request}], **collection})[0]

    def test_openapi_v3_resolves_server_parameters_and_json_body(self):
        document = {
            'openapi': '3.0.3',
            'servers': [{'url': 'https://api.example.test/v1'}],
            'paths': {'/users/{userId}': {
                'parameters': [{'name': 'userId', 'in': 'path'}],
                'post': {
                    'summary': 'Update user', 'tags': ['Users'],
                    'parameters': [{'name': 'expand', 'in': 'query', 'example': 'profile'}],
                    'requestBody': {'content': {'application/json': {
                        'schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}},
                    }}},
                },
            }},
        }
        [item] = self.parse(document)
        self.assertEqual(item['url'], '/users/{userId}')
        self.assertEqual(item['params'], [entry('expand', 'profile')])
        self.assertEqual(item['path_params'], [entry('userId', enabled=False)])
        self.assertEqual(item['body'], {'type': 'json', 'data': {'name': ''}})
        self.assertEqual(item['headers'], [entry('Content-Type', 'application/json')])
        self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test/v1'}])
        self.assertIn('paths 本身', ' '.join(item['warnings']))
        self.assertEqual(item['group'], 'Users')

    def test_swagger_v2_body_parameter_is_imported(self):
        document = {
            'swagger': '2.0', 'host': 'api.example.test', 'schemes': ['http', 'https'],
            'basePath': '/v1', 'consumes': ['application/json'],
            'paths': {'/users': {'post': {
                'parameters': [{'name': 'user', 'in': 'body', 'schema': {
                    'type': 'object', 'properties': {'name': {'type': 'string'}},
                }}],
            }}},
        }
        [item] = self.parse(document)
        self.assertEqual(item['url'], '/users')
        self.assertEqual(item['headers'], [entry('Content-Type', 'application/json')])
        self.assertEqual(item['body']['data'], {'name': ''})
        self.assertEqual(item['environment_suggestions'], [
            {'base_url': 'http://api.example.test/v1'}, {'base_url': 'https://api.example.test/v1'}])
        self.assertIn('basePath 属于环境', ' '.join(item['warnings']))

    def test_path_template_without_parameter_declarations(self):
        [item] = self.parse({'openapi': '3.0.0', 'servers': [{'url': 'https://release.example.test/api'}],
                            'paths': {'/promotions/{merchantId}/{promotionId}/detail': {'get': {}}}})
        self.assertEqual(item['url'], '/promotions/{merchantId}/{promotionId}/detail')
        self.assertEqual(item['params'], [])
        self.assertEqual(item['path_params'], [entry('merchantId', enabled=False), entry('promotionId', enabled=False)])

    def test_openapi_path_and_operation_servers_override_document_servers(self):
        items = self.parse({
            'openapi': '3.0.0', 'servers': [{'url': 'https://root.example.test/root'}],
            'paths': {'/health': {
                'servers': [{'url': 'https://path.example.test/v1'}],
                'get': {}, 'post': {'servers': [{'url': 'https://operation.example.test/v2'}]},
                'put': {'servers': []},
            }},
        })
        self.assertIsInstance(items, list)
        self.assertEqual([item['url'] for item in items], ['/health'] * 3)
        self.assertEqual(items[0]['environment_suggestions'], [{'base_url': 'https://path.example.test/v1'}])
        self.assertEqual(items[1]['environment_suggestions'], [{'base_url': 'https://operation.example.test/v2'}])
        self.assertEqual(items[2]['environment_suggestions'], [])
        self.assertIn('手工设置环境', ' '.join(items[2]['warnings']))

    def test_openapi_server_defaults_are_explicit_not_guessed(self):
        [item] = self.parse({'openapi': '3.0.0', 'servers': [
            {'url': 'https://{host}/v1', 'variables': {'host': {'default': 'api.example.test'}}},
            {'url': 'https://{unknown}/v1'}, {'url': '/v1'},
        ], 'paths': {'/health': {'get': {}}}})
        self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test/v1'}])
        self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_invalid_environment_urls_are_not_suggested(self):
        for url in ('https://user:secret@api.example.test/v1', 'https://api.example.test/v1?token=x',
                    'https://api.example.test/v1#fragment', 'ftp://api.example.test/v1',
                    'https://{{host}}/v1', 'https://api.example.test:invalid/v1'):
            with self.subTest(url=url):
                [item] = self.parse({'openapi': '3.0.0', 'servers': [{'url': url}],
                                    'paths': {'/health': {'get': {}}}})
                self.assertEqual(item['environment_suggestions'], [])
                self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_swagger_without_scheme_does_not_guess_https(self):
        [item] = self.parse({'swagger': '2.0', 'host': 'api.example.test', 'basePath': '/v1',
                            'paths': {'/users': {'get': {}}}})
        self.assertEqual(item['environment_suggestions'], [])
        self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_openapi_parameter_override_keeps_order_type_and_description(self):
        [item] = self.parse({'openapi': '3.0.0', 'paths': {'/users/{id}': {
            'parameters': [{'name': 'id', 'in': 'path', 'example': 'old'},
                           {'name': 'tag', 'in': 'query', 'example': 'first'}],
            'get': {'parameters': [
                {'name': 'id', 'in': 'path', 'schema': {'type': 'integer', 'example': 7}, 'description': 'ID'},
                {'name': 'X-Test', 'in': 'header', 'example': 'yes', 'description': 'Header'},
            ]},
        }}})
        self.assertEqual(item['path_params'], [entry('id', 7, kind='integer', description='ID')])
        self.assertEqual(item['params'], [entry('tag', 'first')])
        self.assertEqual(item['headers'], [entry('X-Test', 'yes', description='Header')])

    def test_openapi_local_refs_and_media_example(self):
        [item] = self.parse({'openapi': '3.0.0', 'components': {
            'parameters': {'ID': {'name': 'id', 'in': 'path', 'schema': {'type': 'string', 'example': 'abc'}}},
            'schemas': {'Payload': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}},
        }, 'paths': {'/users/{id}': {'post': {
            'parameters': [{'$ref': '#/components/parameters/ID'}],
            'requestBody': {'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/Payload'}, 'examples': {'demo': {'value': {'ok': True}}},
            }}},
        }}}})
        self.assertEqual(item['path_params'], [entry('id', 'abc')])
        self.assertEqual(item['body'], {'type': 'json', 'data': {'ok': True}})

    def test_openapi_form_is_ordered_and_file_requires_upload(self):
        [item] = self.parse({'openapi': '3.0.0', 'paths': {'/upload': {'post': {
            'requestBody': {'content': {'multipart/form-data': {'schema': {'type': 'object', 'properties': {
                'note': {'type': 'string', 'example': 'hello', 'description': 'Note'},
                'file': {'type': 'string', 'format': 'binary'},
            }}}}},
        }}}})
        self.assertEqual(item['body'], {'type': 'form-data', 'data': [
            entry('note', 'hello', description='Note'), entry('file', kind='file')]})
        self.assertIn('受控文件引用', ' '.join(item['warnings']))

    def test_swagger_urlencoded_entries_preserve_boolean_and_integer(self):
        [item] = self.parse({'swagger': '2.0', 'consumes': ['application/x-www-form-urlencoded'],
                            'paths': {'/users': {'post': {'parameters': [
                                {'in': 'formData', 'name': 'active', 'type': 'boolean', 'example': False},
                                {'in': 'formData', 'name': 'count', 'type': 'integer', 'example': 0},
                            ]}}}})
        self.assertEqual(item['body'], {'type': 'x-www-form-urlencoded', 'data': [
            entry('active', False, kind='boolean'), entry('count', 0, kind='integer')]})

    def test_openapi_auth_mapping_and_operation_noauth_override(self):
        for scheme, expected in (
            ({'type': 'http', 'scheme': 'basic'}, {'type': 'basic', 'username': '', 'password': ''}),
            ({'type': 'http', 'scheme': 'bearer'}, {'type': 'bearer', 'token': ''}),
            ({'type': 'apiKey', 'name': 'X-Key', 'in': 'header'},
             {'type': 'api_key', 'key': 'X-Key', 'value': '', 'in': 'header'}),
        ):
            with self.subTest(scheme=scheme):
                items = self.parse({'openapi': '3.0.0', 'components': {'securitySchemes': {'Auth': scheme}},
                                   'security': [{'Auth': []}], 'paths': {'/users': {
                                       'get': {}, 'post': {'security': []},
                                   }}})
                self.assertEqual(items[0]['auth'], expected)
                self.assertEqual(items[1]['auth'], {'type': 'none'})
                self.assertIn('未提供凭证', ' '.join(items[0]['warnings']))

    def test_openapi_unsupported_auth_and_cookie_warn(self):
        [item] = self.parse({'openapi': '3.0.0', 'components': {'securitySchemes': {
            'OAuth': {'type': 'oauth2', 'flows': {'clientCredentials': {'scopes': {}}}},
        }}, 'security': [{'OAuth': []}], 'paths': {'/users': {'get': {'parameters': [
            {'in': 'cookie', 'name': 'session', 'example': 'abc'},
        ]}}}})
        self.assertIn('不支持 OpenAPI 认证', ' '.join(item['warnings']))
        self.assertIn('不支持参数位置 cookie', ' '.join(item['warnings']))
        self.assertEqual(item['source_meta']['security_schemes']['OAuth']['type'], 'oauth2')

    def test_postman_collection_preserves_nested_folder_and_request_values(self):
        [item] = self.parse({'info': {'name': 'Collection'}, 'item': [{'name': 'Users', 'item': [
            {'name': 'Create user', 'request': {
                'method': 'POST', 'url': {'raw': '{{baseUrl}}/users', 'query': [{'key': 'notify', 'value': 'true'}]},
                'header': [{'key': 'Content-Type', 'value': 'application/json'}],
                'body': {'mode': 'raw', 'raw': '{"name":"Ada"}'},
            }},
        ]}]})
        self.assertEqual(item['group'], 'Collection / Users')
        self.assertEqual(item['method'], 'POST')
        self.assertEqual(item['url'], '/users')
        self.assertEqual(item['params'], [entry('notify', 'true')])
        self.assertEqual(item['body'], {'type': 'json', 'data': {'name': 'Ada'}})

    def test_postman_builds_url_when_raw_value_is_missing(self):
        item = self.postman({'method': 'GET', 'url': {'host': ['{{baseUrl}}'], 'path': ['v1', 'users']}})
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['path'], '/v1/users')
        self.assertEqual(item['environment_suggestions'], [])
        self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_postman_structured_absolute_url_joins_hostname_with_dots(self):
        item = self.postman({'url': {'protocol': 'https', 'host': ['api', 'example', 'test'],
                                    'path': ['v1', 'users'], 'query': [{'key': 'a', 'value': '1'}]}})
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['params'], [entry('a', '1')])
        self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test'}])

    def test_postman_structured_host_without_scheme_requires_manual_environment(self):
        item = self.postman({'url': {'host': ['api', 'example', 'test'], 'path': ['v1', 'users']}})
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['environment_suggestions'], [])
        self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_postman_prefers_collection_name_from_json(self):
        items = self.parse({'info': {'name': 'Orders API'}, 'item': [
            {'name': 'List orders', 'request': {'method': 'GET', 'url': '{{baseUrl}}/orders'}},
            {'name': 'Admin', 'item': [{'name': 'Delete order', 'request': {
                'method': 'DELETE', 'url': '{{baseUrl}}/orders/{id}'}}]},
        ]})
        self.assertEqual(items[0]['group'], 'Orders API')
        self.assertEqual(items[1]['group'], 'Orders API / Admin')
        self.assertEqual(items[0]['group_path'], ['Orders API'])
        self.assertEqual(items[1]['group_path'], ['Orders API', 'Admin'])

    def test_postman_path_params_are_separate_from_query_and_colon_is_normalized(self):
        item = self.postman({'url': {'raw': '{{baseUrl}}/forms/:userId/list?page=1',
                                    'path': ['forms', ':userId', 'list'],
                                    'query': [{'key': 'page', 'value': '1'}],
                                    'variable': [{'key': 'userId', 'value': '42', 'description': 'User'}]}})
        self.assertEqual(item['url'], '/forms/{userId}/list')
        self.assertEqual(item['params'], [entry('page', '1')])
        self.assertEqual(item['path_params'], [entry('userId', '42', description='User')])

    def test_postman_raw_query_keeps_duplicates_empty_values_and_order(self):
        item = self.postman({'url': 'https://api.example.test/v1/users?tag=a&tag=b&empty=&q=a%2Bb#fragment'})
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['params'], [entry('tag', 'a'), entry('tag', 'b'), entry('empty'), entry('q', 'a+b')])
        self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test'}])
        self.assertIn('全部 path（包括 /v1）保留', ' '.join(item['warnings']))

    def test_postman_structured_query_and_headers_keep_disabled_duplicates(self):
        item = self.postman({'url': {'raw': 'https://api.example.test/users?tag=raw', 'query': [
            {'key': 'tag', 'value': 'a', 'description': {'content': 'Tag'}},
            {'key': 'tag', 'value': 'b', 'disabled': True}, {'key': '', 'value': 'empty-key'},
        ]}, 'header': [{'key': 'X-Test', 'value': 'a'}, {'key': 'X-Test', 'value': 'b', 'disabled': True}]})
        self.assertEqual(item['params'], [entry('tag', 'a', description='Tag'), entry('tag', 'b', False),
                                          entry('', 'empty-key')])
        self.assertTrue(item['blocking_errors'])
        self.assertEqual(item['headers'], [entry('X-Test', 'a'), entry('X-Test', 'b', False)])
        self.assertEqual(set(item['params'][0]), {'key', 'value', 'enabled', 'type', 'description'})

    def test_empty_structured_query_does_not_revive_raw_disabled_query(self):
        item = self.postman({'url': {'raw': 'https://api.example.test/users?tag=raw', 'query': []}})
        self.assertEqual(item['params'], [])

    def test_unknown_host_is_not_guessed_or_used_as_path(self):
        for url in ('{{host}}/v1/users', 'https://{{host}}/v1/users'):
            with self.subTest(url=url):
                item = self.postman({'url': url})
                self.assertEqual(item['url'], '/v1/users')
                self.assertEqual(item['environment_suggestions'], [])
                self.assertIn('未知主机变量 host', ' '.join(item['warnings']))
                self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_postman_host_value_without_scheme_is_not_inserted_into_request_path(self):
        item = self.postman({'url': '{{host}}/v1/users'}, variable=[{'key': 'host', 'value': 'api.example.test'}])
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['environment_suggestions'], [])
        self.assertIn('手工设置环境', ' '.join(item['warnings']))

    def test_explicit_postman_base_variable_keeps_its_known_base_path(self):
        item = self.postman({'url': '{{baseUrl}}/users?active=true'}, variable=[
            {'key': 'baseUrl', 'value': 'https://api.example.test/v1'}])
        self.assertEqual(item['url'], '/v1/users')
        self.assertEqual(item['params'], [entry('active', 'true')])
        self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test'}])

    def test_relative_postman_path_and_runtime_variables_are_preserved(self):
        item = self.postman({'url': '/users/{{id}}?q={{query}}'}, variable=[{'key': 'id', 'value': '42'}])
        self.assertEqual(item['url'], '/users/{{id}}')
        self.assertEqual(item['params'], [entry('q', '{{query}}')])
        self.assertEqual(item['path_params'], [])
        self.assertEqual(item['environment_suggestions'], [])

    def test_postman_form_entries_preserve_duplicates_and_disabled(self):
        for mode, body_type in (('urlencoded', 'x-www-form-urlencoded'), ('formdata', 'form-data')):
            with self.subTest(mode=mode):
                item = self.postman({'url': '/users', 'body': {'mode': mode, mode: [
                    {'key': 'tag', 'value': 'a', 'type': 'text', 'description': 'Tag'},
                    {'key': 'tag', 'value': 'b', 'disabled': True},
                ]}})
                self.assertEqual(item['body'], {'type': body_type, 'data': [
                    entry('tag', 'a', kind='text', description='Tag'), entry('tag', 'b', False)]})

    def test_postman_file_paths_are_metadata_and_block_import_without_reading(self):
        source_path = ['/private/upload.csv', '/private/second.csv']
        with patch('builtins.open', side_effect=AssertionError('Must not read imported source paths')):
            item = self.postman({'url': '/upload', 'body': {'mode': 'formdata', 'formdata': [
                {'key': 'file', 'type': 'file', 'src': source_path, 'value': '/private/upload.csv'},
            ]}})
        self.assertEqual(item['body'], {'type': 'form-data', 'data': [entry('file', kind='file')]})
        self.assertEqual(item['source_meta']['files'], [{'key': 'file', 'index': 0, 'source_path': source_path}])
        self.assertTrue(item['blocking_errors'])
        self.assertIn('不能导入执行', ' '.join(item['warnings']))
        self.assertNotIn('source_path', json.dumps(item['body']))
        self.assertNotIn('/private/', json.dumps(item['body']))

    def test_postman_disabled_file_source_also_blocks_import(self):
        item = self.postman({'url': '/upload', 'body': {'mode': 'formdata', 'formdata': [
            {'key': 'file', 'type': 'file', 'src': '/private/file', 'disabled': True},
        ]}})
        self.assertEqual(item['body']['data'], [entry('file', enabled=False, kind='file')])
        self.assertTrue(item['blocking_errors'])

    def test_binary_body_source_blocks_import(self):
        item = self.postman({'url': '/upload', 'body': {'mode': 'file', 'file': {'src': '/private/file'}}})
        self.assertTrue(item['blocking_errors'])
        self.assertEqual(item['body'], {'type': 'none'})

    def test_postman_basic_bearer_api_key_auth_mapping(self):
        for auth, expected in (
            ({'type': 'basic', 'basic': [{'key': 'username', 'value': '{{user}}'}, {'key': 'password', 'value': 'secret'}]},
             {'type': 'basic', 'username': '{{user}}', 'password': 'secret'}),
            ({'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{token}}'}]},
             {'type': 'bearer', 'token': '{{token}}'}),
            ({'type': 'apikey', 'apikey': [{'key': 'key', 'value': 'token'}, {'key': 'value', 'value': '{{token}}'},
                                          {'key': 'in', 'value': 'query'}]},
             {'type': 'api_key', 'key': 'token', 'value': '{{token}}', 'in': 'query'}),
        ):
            with self.subTest(auth=auth):
                item = self.postman({'url': '/users', 'auth': auth})
                self.assertEqual(item['auth'], expected)

    def test_postman_auth_scripts_settings_inherit_and_override(self):
        items = self.parse({'info': {'name': 'Collection'},
                           'auth': {'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{token}}'}]},
                           'event': [{'listen': 'prerequest', 'script': {'exec': ['collection();']}}],
                           'protocolProfileBehavior': {'strictSSL': False, 'requestTimeout': 1500},
                           'item': [{'name': 'Folder',
                                     'event': [{'listen': 'prerequest', 'script': {'exec': ['folder();']}}],
                                     'item': [{'name': 'Request',
                                               'event': [{'listen': 'prerequest', 'script': {'exec': ['request();']}},
                                                         {'listen': 'test', 'script': {'exec': ['pm.test("ok", () => {});']}}],
                                               'request': {'url': '/users', 'protocolProfileBehavior': {'strictSSL': True}}},
                                              {'name': 'No auth', 'request': {'url': '/public', 'auth': {'type': 'noauth'}}}]}]})
        self.assertEqual(items[0]['auth'], {'type': 'bearer', 'token': '{{token}}'})
        self.assertEqual(items[0]['scripts']['pre_request'], 'collection();\nfolder();\nrequest();')
        self.assertEqual(items[0]['scripts']['post_request'], 'pm.test("ok", () => {});')
        self.assertEqual(items[0]['settings'], {'verify_ssl': True, 'timeout': 1.5,
                                              'connect_timeout': 5, 'follow_redirects': True})
        self.assertEqual(items[1]['auth'], {'type': 'none'})
        self.assertIn('兼容性', ' '.join(items[0]['warnings']))

    def test_unsupported_postman_auth_body_event_settings_warn_and_keep_source(self):
        item = self.postman({'url': '/users', 'auth': {'type': 'oauth2', 'oauth2': [{'key': 'scope', 'value': 'read'}]},
                             'body': {'mode': 'graphql', 'graphql': {'query': '{ users { id } }'}},
                             'event': [{'listen': 'custom', 'script': {'exec': ['custom();']}}],
                             'protocolProfileBehavior': {'disableCookies': True}})
        warnings = ' '.join(item['warnings'])
        self.assertIn('不支持认证类型 oauth2', warnings)
        self.assertIn('不支持请求体模式 graphql', warnings)
        self.assertIn('不支持 Postman 事件 custom', warnings)
        self.assertIn('不支持 Postman 设置 disableCookies', warnings)
        self.assertEqual(item['source_meta']['auth']['type'], 'oauth2')
        self.assertEqual(item['source_meta']['body']['mode'], 'graphql')
        self.assertEqual(item['source_meta']['protocol_profile_behavior']['disableCookies'], True)

    def test_postman_raw_language_and_invalid_json_are_preserved(self):
        item = self.postman({'url': '/users', 'body': {'mode': 'raw', 'raw': '{"name": {{name}}}',
                                                      'options': {'raw': {'language': 'json'}}}})
        self.assertEqual(item['body'], {'type': 'raw', 'data': '{"name": {{name}}}'})
        self.assertIn('保留为 raw', ' '.join(item['warnings']))
        item = self.postman({'url': '/users', 'body': {'mode': 'raw', 'raw': '123',
                                                      'options': {'raw': {'language': 'text'}}}})
        self.assertEqual(item['body'], {'type': 'raw', 'data': '123'})

    def test_empty_body_is_a_complete_dto(self):
        item = self.postman({'url': '/health'})
        self.assertEqual(item['body'], {'type': 'none'})
        for key, expected in (('auth', {'type': 'none'}),
                              ('scripts', {'pre_request': '', 'post_request': ''}),
                              ('assertions', []), ('extractors', []),
                              ('settings', {'timeout': 30, 'connect_timeout': 5,
                                            'verify_ssl': True, 'follow_redirects': True}),
                              ('blocking_errors', [])):
            self.assertEqual(item[key], expected)

    def test_structured_query_values_use_strict_json_entry_type(self):
        [item] = self.parse({'openapi': '3.0.0', 'paths': {'/users': {'get': {'parameters': [
            {'in': 'query', 'name': 'tags', 'schema': {'type': 'array', 'example': ['a', 'b']}},
            {'in': 'query', 'name': 'filter', 'schema': {'type': 'object', 'example': {'active': True}}},
        ]}}}})
        self.assertEqual(item['params'], [entry('tags', ['a', 'b'], kind='json'),
                                          entry('filter', {'active': True}, kind='json')])

    def test_duplicate_path_parameters_block_without_losing_rows(self):
        item = self.postman({'url': {'raw': '/users/{id}', 'variable': [
            {'key': 'id', 'value': 'a'}, {'key': 'id', 'value': 'b', 'disabled': True},
        ]}})
        self.assertEqual(item['path_params'], [entry('id', 'a'), entry('id', 'b', False)])
        self.assertTrue(item['blocking_errors'])

    def test_raw_colon_path_parameter_is_imported_disabled_when_missing_value(self):
        item = self.postman({'url': 'https://api.example.test/users/:id'})
        self.assertEqual(item['url'], '/users/{id}')
        self.assertEqual(item['path_params'], [entry('id', enabled=False)])
        self.assertIn('填写并启用', ' '.join(item['warnings']))

    def test_unsafe_paths_and_unscoped_variables_are_preview_blockers(self):
        for url in ('/v1/../users', '/v1/%252e%252e/users', '/users/{{unknown}}'):
            with self.subTest(url=url):
                item = self.postman({'url': url})
                self.assertTrue(item['blocking_errors'])
        item = self.postman({'url': '/users/{{vars.id}}'})
        self.assertEqual(item['url'], '/users/{{vars.id}}')
        self.assertEqual(item['blocking_errors'], [])

    def test_file_source_overrides_text_type_without_leaking_value(self):
        item = self.postman({'url': '/upload', 'body': {'mode': 'formdata', 'formdata': [
            {'key': 'file', 'type': 'text', 'src': '/private/file', 'value': '/private/file'},
        ]}})
        self.assertEqual(item['body']['data'], [entry('file', kind='file')])
        self.assertTrue(item['blocking_errors'])

    def test_request_timeout_outside_dto_bounds_warns_and_uses_default(self):
        item = self.postman({'url': '/users', 'protocolProfileBehavior': {'requestTimeout': 301000}})
        self.assertEqual(item['settings']['timeout'], 30)
        self.assertIn('不支持 Postman 设置 requestTimeout', ' '.join(item['warnings']))

    def test_rejects_unknown_document(self):
        with self.assertRaisesRegex(ValueError, '不支持的接口定义格式'):
            parse_collection(b'{"unexpected": true}', 'unknown.json')

    def test_utf8_bom_and_invalid_encoding(self):
        items = parse_collection(b'\xef\xbb\xbf' + json.dumps({'openapi': '3.0.0', 'paths': {'/health': {'get': {}}}}).encode(),
                                 'openapi.json')
        self.assertEqual(items[0]['url'], '/health')
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            parse_collection(b'\xff', 'openapi.json')


# Parser tests can run without Django; integration tests use the configured Django runner.
if os.environ.get('DJANGO_SETTINGS_MODULE'):
    from types import SimpleNamespace

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.contrib.auth import get_user_model
    from django.test import SimpleTestCase, TestCase
    from rest_framework.test import APIRequestFactory, force_authenticate

    from ..models import ApiCollection, ApiProject, ApiRequest
    from ..views import ApiRequestViewSet, ApiCollectionViewSet
    from ..request_dto import REQUEST_FIELDS, normalize_request

    class CollectionImportPreviewTests(SimpleTestCase):
        def test_supported_import_items_pass_the_actual_strict_dto(self):
            documents = [
                {'openapi': '3.0.0', 'paths': {'/users/{id}': {'get': {'parameters': [
                    {'in': 'path', 'name': 'id', 'schema': {'type': 'integer', 'example': 7}},
                    {'in': 'query', 'name': 'tags', 'schema': {'type': 'array', 'example': ['a', 'b']}},
                ]}}}},
                {'info': {'name': 'Collection'}, 'item': [{'name': 'Request', 'request': {
                    'url': 'https://api.example.test/v1/users',
                    'auth': {'type': 'basic', 'basic': [{'key': 'username', 'value': 'user'},
                                                       {'key': 'password', 'value': '{{vars.password}}'}]},
                    'body': {'mode': 'urlencoded', 'urlencoded': [
                        {'key': 'tag', 'value': 'a'}, {'key': 'tag', 'value': 'b', 'disabled': True},
                    ]},
                }}]},
                {'openapi': '3.0.0', 'paths': {'/users/{id}': {'get': {}}}},
            ]
            for document in documents:
                with self.subTest(document=document):
                    [item] = parse_collection(json.dumps(document).encode(), 'collection.json')
                    self.assertEqual(item['blocking_errors'], [])
                    dto = {field: item[field] for field in REQUEST_FIELDS}
                    self.assertEqual(normalize_request(dto), dto)


        def test_preview_route_requires_post_and_parses_multipart_upload(self):
            document = {'openapi': '3.0.0', 'paths': {'/health': {'get': {'summary': 'Health'}}}}
            view = ApiRequestViewSet.as_view({'post': 'import_preview'})
            user = SimpleNamespace(is_authenticated=True, is_anonymous=False)
            factory = APIRequestFactory()
            request = factory.post('/api/api-testing/requests/import-preview/', {
                'file': SimpleUploadedFile('openapi.json', json.dumps(document).encode())}, format='multipart')
            force_authenticate(request, user=user)
            response = view(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['total'], 1)
            imported_item = response.data['groups'][0]['items'][0]
            self.assertEqual(imported_item['method'], 'GET')
            self.assertEqual(imported_item['name'], 'Health')
            self.assertEqual(imported_item['url'], '/health')
            self.assertEqual(imported_item['path'], '/health')
            request = factory.get('/api/api-testing/requests/import-preview/')
            force_authenticate(request, user=user)
            self.assertEqual(view(request).status_code, 405)

        def test_preview_exposes_file_blocking_errors(self):
            document = {'info': {'name': 'Upload'}, 'item': [{'name': 'Upload', 'request': {
                'url': 'https://api.example.test/v1/upload', 'body': {'mode': 'formdata', 'formdata': [
                    {'key': 'file', 'type': 'file', 'src': '/private/file'},
                ]},
            }}]}
            request = APIRequestFactory().post('/api/api-testing/requests/import-preview/', {
                'file': SimpleUploadedFile('upload.json', json.dumps(document).encode())}, format='multipart')
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_anonymous=False))
            response = ApiRequestViewSet.as_view({'post': 'import_preview'})(request)
            self.assertEqual(response.status_code, 200)
            item = response.data['groups'][0]['items'][0]
            self.assertTrue(item['blocking_errors'])
            self.assertEqual(item['environment_suggestions'], [{'base_url': 'https://api.example.test'}])

    class CollectionImportExportTests(TestCase):
        def setUp(self):
            self.user = get_user_model().objects.create_user(username='api-import-export', password='password')
            self.project = ApiProject.objects.create(
                name='API project', project_type='HTTP', status='NOT_STARTED', owner=self.user)
            self.root = ApiCollection.objects.create(name='Root', project=self.project)

        def test_export_includes_root_requests_and_nested_collection_requests(self):
            child = ApiCollection.objects.create(name='Child', project=self.project, parent=self.root)
            ApiRequest.objects.create(collection=self.root, name='Root request', method='GET', url='/root',
                                      params=[entry('page', '1')], headers=[entry('X-Test', 'yes')], created_by=self.user)
            ApiRequest.objects.create(collection=self.root, name='Request with path variable', method='GET',
                                      url='/forms/{userId}/list', path_params=[entry('userId', enabled=False)],
                                      params=[entry('page', '1')], created_by=self.user)
            ApiRequest.objects.create(collection=child, name='Child request', method='POST', url='/child',
                                      body={'type': 'json', 'data': {'ok': True}}, created_by=self.user)
            request = APIRequestFactory().get(f'/api/api-testing/collections/{self.root.id}/export/')
            force_authenticate(request, user=self.user)
            response = ApiCollectionViewSet.as_view({'get': 'export_collection'})(request, pk=self.root.id)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Root.postman_collection.json', response.headers['Content-Disposition'])
            payload = json.loads(response.content)
            self.assertEqual(payload['info']['name'], 'Root')
            self.assertEqual(payload['item'][0]['name'], 'Root request')
            exported_url = payload['item'][0]['request']['url']
            self.assertEqual(exported_url['raw'], '/root')
            self.assertEqual(exported_url['path'], ['root'])
            self.assertEqual(exported_url['query'], [{'key': 'page', 'value': '1', 'description': '', 'disabled': False}])
            path_variable_url = payload['item'][1]['request']['url']
            self.assertEqual(path_variable_url['raw'], '/forms/{userId}/list')
            self.assertEqual(path_variable_url['path'], ['forms', '{userId}', 'list'])
            self.assertEqual(path_variable_url['query'], [{'key': 'page', 'value': '1', 'description': '', 'disabled': False}])
            self.assertEqual(path_variable_url['variable'], [{'key': 'userId', 'value': '', 'description': '', 'disabled': True}])
            self.assertEqual(payload['item'][2]['name'], 'Child')
            self.assertEqual(payload['item'][2]['item'][0]['name'], 'Child request')

        def import_batch(self, document, **options):
            request = APIRequestFactory().post('/api/api-testing/requests/import-batch/', {
                'file': SimpleUploadedFile('collection.json', json.dumps(document).encode()),
                'project': str(self.project.id), 'selected_ids': ['0'], **options}, format='multipart')
            force_authenticate(request, user=self.user)
            return ApiRequestViewSet.as_view({'post': 'import_batch'})(request)

        def test_import_batch_can_attach_source_groups_to_target_collection(self):
            response = self.import_batch({'info': {'name': 'Collection'}, 'item': [{'name': 'Folder', 'item': [{
                'name': 'Imported request', 'request': {'method': 'GET', 'url': '{{baseUrl}}/imported'},
            }]}]}, collection=str(self.root.id))
            self.assertEqual(response.status_code, 201)
            imported = ApiRequest.objects.get(name='Imported request')
            self.assertEqual(imported.collection.parent.name, 'Collection')
            self.assertEqual(imported.collection.parent.parent_id, self.root.id)
            self.assertEqual(imported.collection.name, 'Folder')
            self.assertEqual(imported.url, '/imported')

        def test_import_keeps_json_root_collection_separate_from_child_collection(self):
            response = self.import_batch({'info': {'name': 'Orders API'}, 'item': [
                {'name': 'List orders', 'request': {'method': 'GET', 'url': '{{baseUrl}}/orders'}},
                {'name': 'Admin', 'item': [{'name': 'Delete order', 'request': {
                    'method': 'DELETE', 'url': '{{baseUrl}}/orders/{id}'}}]},
            ]}, selected_ids=['0', '1'])
            self.assertEqual(response.status_code, 201)
            root = ApiCollection.objects.get(project=self.project, name='Orders API', parent=None)
            child = ApiCollection.objects.get(project=self.project, name='Admin', parent=root)
            self.assertEqual(root.requests.get(name='List orders').collection_id, root.id)
            self.assertEqual(child.requests.get(name='Delete order').collection_id, child.id)

        def test_import_batch_rejects_file_source_before_creating_collections(self):
            response = self.import_batch({'info': {'name': 'Upload'}, 'item': [{'name': 'Unsafe upload', 'request': {
                'url': '/upload', 'body': {'mode': 'formdata', 'formdata': [
                    {'key': 'file', 'type': 'file', 'src': '/private/file'},
                ]},
            }}]})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(ApiRequest.objects.filter(name='Unsafe upload').exists())
            self.assertFalse(ApiCollection.objects.filter(name='Upload').exists())

        def test_import_batch_persists_complete_request_dto(self):
            response = self.import_batch({'info': {'name': 'Collection'}, 'item': [{
                'name': 'Complete request', 'event': [{'listen': 'prerequest', 'script': {'exec': ['prepare();']}},
                                                     {'listen': 'test', 'script': {'exec': ['check();']}}],
                'protocolProfileBehavior': {'strictSSL': False}, 'request': {
                    'url': {'raw': 'https://api.example.test/v1/users/{id}', 'variable': [{'key': 'id', 'value': '42'}],
                            'query': [{'key': 'tag', 'value': 'a'}, {'key': 'tag', 'value': 'b', 'disabled': True}]},
                    'auth': {'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{token}}'}]},
                },
            }]})
            self.assertEqual(response.status_code, 201)
            imported = ApiRequest.objects.get(name='Complete request')
            self.assertEqual(imported.url, '/v1/users/{id}')
            self.assertEqual(imported.path_params, [entry('id', '42')])
            self.assertEqual(imported.params, [entry('tag', 'a'), entry('tag', 'b', False)])
            self.assertEqual(imported.auth, {'type': 'bearer', 'token': '{{token}}'})
            self.assertEqual(imported.pre_request_script, 'prepare();')
            self.assertEqual(imported.post_request_script, 'check();')
            self.assertEqual(imported.settings['verify_ssl'], False)
