import json
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ..models import ApiCollection, ApiProject, ApiRequest
from ..importers import parse_collection
from ..views import ApiRequestViewSet
from ..views import ApiCollectionViewSet


class CollectionImportParserTests(SimpleTestCase):
    def test_preview_route_requires_post_and_parses_multipart_upload(self):
        document = {'openapi': '3.0.0', 'paths': {'/health': {'get': {'summary': 'Health'}}}}
        view = ApiRequestViewSet.as_view({'post': 'import_preview'})
        user = SimpleNamespace(is_authenticated=True, is_anonymous=False)
        factory = APIRequestFactory()
        request = factory.post(
            '/api/api-testing/requests/import-preview/',
            {'file': SimpleUploadedFile('openapi.json', json.dumps(document).encode())},
            format='multipart',
        )
        force_authenticate(request, user=user)

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)
        imported_item = response.data['groups'][0]['items'][0]
        self.assertEqual(imported_item['method'], 'GET')
        self.assertEqual(imported_item['name'], 'Health')
        self.assertEqual(imported_item['url'], '{{baseUrl}}/health')
        self.assertEqual(imported_item['path'], '/health')

        request = factory.get('/api/api-testing/requests/import-preview/')
        force_authenticate(request, user=user)
        response = view(request)
        self.assertEqual(response.status_code, 405)

    def test_openapi_v3_resolves_server_parameters_and_json_body(self):
        document = {
            'openapi': '3.0.3',
            'servers': [{'url': 'https://api.example.test/v1'}],
            'paths': {
                '/users/{userId}': {
                    'parameters': [{'name': 'userId', 'in': 'path'}],
                    'post': {
                        'summary': 'Update user', 'tags': ['Users'],
                        'parameters': [{'name': 'expand', 'in': 'query', 'example': 'profile'}],
                        'requestBody': {'content': {'application/json': {
                            'schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}},
                        }}},
                    },
                },
            },
        }

        [item] = parse_collection(json.dumps(document).encode(), 'openapi.json')

        self.assertEqual(item['url'], '{{baseUrl}}/users/{userId}')
        self.assertEqual(item['params'], {'userId': '', 'expand': 'profile'})
        self.assertEqual(item['body'], {'type': 'json', 'data': {'name': ''}})
        self.assertEqual(item['group'], 'Users')

    def test_swagger_v2_body_parameter_is_imported(self):
        document = {
            'swagger': '2.0', 'host': 'api.example.test', 'schemes': ['https'],
            'basePath': '/v1', 'consumes': ['application/json'],
            'paths': {'/users': {'post': {
                'parameters': [{'name': 'user', 'in': 'body', 'schema': {
                    'type': 'object', 'properties': {'name': {'type': 'string'}},
                }}],
            }}},
        }

        [item] = parse_collection(json.dumps(document).encode(), 'swagger.json')

        self.assertEqual(item['url'], '{{baseUrl}}/users')
        self.assertEqual(item['headers']['Content-Type'], 'application/json')
        self.assertEqual(item['body']['data'], {'name': ''})

    def test_path_template_is_imported_even_without_parameter_declarations(self):
        document = {
            'openapi': '3.0.0',
            'servers': [{'url': 'https://release.example.test/api'}],
            'paths': {'/promotions/{merchantId}/{promotionId}/detail': {'get': {}}},
        }

        [item] = parse_collection(json.dumps(document).encode(), 'openapi.json')

        self.assertEqual(item['url'], '{{baseUrl}}/promotions/{merchantId}/{promotionId}/detail')
        self.assertEqual(item['params'], {'merchantId': '', 'promotionId': ''})

    def test_postman_collection_preserves_nested_folder_and_request_values(self):
        document = {
            'info': {'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'},
            'item': [{'name': 'Users', 'item': [{'name': 'Create user', 'request': {
                'method': 'POST', 'url': {'raw': '{{baseUrl}}/users', 'query': [
                    {'key': 'notify', 'value': 'true'},
                ]}, 'header': [{'key': 'Content-Type', 'value': 'application/json'}],
                'body': {'mode': 'raw', 'raw': '{"name":"Ada"}'},
            }}]}],
        }

        [item] = parse_collection(json.dumps(document).encode(), 'collection.json')

        self.assertEqual(item['group'], 'Users')
        self.assertEqual(item['method'], 'POST')
        self.assertEqual(item['url'], '{{baseUrl}}/users')
        self.assertEqual(item['params'], {'notify': 'true'})
        self.assertEqual(item['body'], {'type': 'json', 'data': {'name': 'Ada'}})

    def test_postman_builds_url_when_raw_value_is_missing(self):
        document = {
            'info': {'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'},
            'item': [{'name': 'List users', 'request': {
                'method': 'GET', 'url': {'host': ['{{baseUrl}}'], 'path': ['v1', 'users']},
            }}],
        }

        [item] = parse_collection(json.dumps(document).encode(), 'collection.json')

        self.assertEqual(item['url'], '{{baseUrl}}/v1/users')
        self.assertEqual(item['path'], '/v1/users')

    def test_postman_prefers_collection_name_from_json(self):
        document = {
            'info': {
                'name': 'Orders API',
                'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
            },
            'item': [
                {'name': 'List orders', 'request': {'method': 'GET', 'url': '{{baseUrl}}/orders'}},
                {'name': 'Admin', 'item': [{
                    'name': 'Delete order',
                    'request': {'method': 'DELETE', 'url': '{{baseUrl}}/orders/{id}'},
                }]},
            ],
        }

        items = parse_collection(json.dumps(document).encode(), 'collection.json')

        self.assertEqual(items[0]['group'], 'Orders API')
        self.assertEqual(items[1]['group'], 'Orders API / Admin')
        self.assertEqual(items[0]['group_path'], ['Orders API'])
        self.assertEqual(items[1]['group_path'], ['Orders API', 'Admin'])

    def test_postman_import_keeps_path_variables_separate_from_query_values(self):
        document = {
            'info': {'name': 'Forms'},
            'item': [{
                'name': 'List forms',
                'request': {
                    'method': 'GET',
                    'url': {
                        'raw': '{{baseUrl}}/forms/{userId}/list?page=1',
                        'host': ['{{baseUrl}}'],
                        'path': ['forms', '{userId}', 'list'],
                        'query': [{'key': 'page', 'value': '1'}],
                        'variable': [{'key': 'userId', 'value': ''}],
                    },
                },
            }],
        }

        [item] = parse_collection(json.dumps(document).encode(), 'forms.json')

        self.assertEqual(item['params'], {'page': '1', 'userId': ''})

    def test_rejects_unknown_document(self):
        with self.assertRaisesMessage(ValueError, '不支持的接口定义格式'):
            parse_collection(b'{"unexpected": true}', 'unknown.json')


class CollectionImportExportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='api-import-export', password='password')
        self.project = ApiProject.objects.create(
            name='API project', project_type='HTTP', status='NOT_STARTED', owner=self.user,
        )
        self.root = ApiCollection.objects.create(name='Root', project=self.project)

    def test_export_includes_root_requests_and_nested_collection_requests(self):
        child = ApiCollection.objects.create(name='Child', project=self.project, parent=self.root)
        ApiRequest.objects.create(
            collection=self.root, name='Root request', method='GET', url='{{baseUrl}}/root',
            params={'page': '1'}, headers={'X-Test': 'yes'}, created_by=self.user,
        )
        ApiRequest.objects.create(
            collection=self.root, name='Request with path variable', method='GET',
            url='{{baseUrl}}/forms/{userId}/list',
            params={'userId': '', 'page': '1'}, created_by=self.user,
        )
        ApiRequest.objects.create(
            collection=child, name='Child request', method='POST', url='{{baseUrl}}/child',
            body={'type': 'json', 'data': {'ok': True}}, created_by=self.user,
        )

        request = APIRequestFactory().get(f'/api/api-testing/collections/{self.root.id}/export/')
        force_authenticate(request, user=self.user)
        response = ApiCollectionViewSet.as_view({'get': 'export_collection'})(request, pk=self.root.id)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Root.postman_collection.json', response.headers['Content-Disposition'])
        payload = json.loads(response.content)
        self.assertEqual(payload['info']['name'], 'Root')
        self.assertEqual(payload['item'][0]['name'], 'Root request')
        exported_url = payload['item'][0]['request']['url']
        self.assertEqual(exported_url['raw'], '{{baseUrl}}/root')
        self.assertEqual(exported_url['host'], ['{{baseUrl}}'])
        self.assertEqual(exported_url['path'], ['root'])
        self.assertEqual(exported_url['query'], [{'key': 'page', 'value': '1', 'description': '', 'disabled': False}])
        path_variable_url = payload['item'][1]['request']['url']
        self.assertEqual(path_variable_url['raw'], '{{baseUrl}}/forms/{userId}/list')
        self.assertEqual(path_variable_url['path'], ['forms', '{userId}', 'list'])
        self.assertEqual(path_variable_url['query'], [{'key': 'page', 'value': '1', 'description': '', 'disabled': False}])
        self.assertEqual(path_variable_url['variable'], [
            {'key': 'userId', 'value': '', 'description': '', 'disabled': False},
        ])
        self.assertEqual(payload['item'][2]['name'], 'Child')
        self.assertEqual(payload['item'][2]['item'][0]['name'], 'Child request')

    def test_import_batch_can_attach_source_groups_to_target_collection(self):
        document = {
            'info': {'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'},
            'item': [{'name': 'Folder', 'item': [{
                'name': 'Imported request', 'request': {'method': 'GET', 'url': '{{baseUrl}}/imported'},
            }]}],
        }
        request = APIRequestFactory().post(
            '/api/api-testing/requests/import-batch/',
            {
                'file': SimpleUploadedFile('collection.json', json.dumps(document).encode()),
                'project': str(self.project.id),
                'collection': str(self.root.id),
                'selected_ids': '0',
            },
            format='multipart',
        )
        force_authenticate(request, user=self.user)
        response = ApiRequestViewSet.as_view({'post': 'import_batch'})(request)

        self.assertEqual(response.status_code, 201)
        imported = ApiRequest.objects.get(name='Imported request')
        self.assertEqual(imported.collection.parent_id, self.root.id)
        self.assertEqual(imported.collection.name, 'Folder')

    def test_import_keeps_json_root_collection_separate_from_child_collection(self):
        document = {
            'info': {'name': 'Orders API'},
            'item': [
                {'name': 'List orders', 'request': {'method': 'GET', 'url': '{{baseUrl}}/orders'}},
                {'name': 'Admin', 'item': [{
                    'name': 'Delete order',
                    'request': {'method': 'DELETE', 'url': '{{baseUrl}}/orders/{id}'},
                }]},
            ],
        }
        request = APIRequestFactory().post(
            '/api/api-testing/requests/import-batch/',
            {
                'file': SimpleUploadedFile('orders.json', json.dumps(document).encode()),
                'project': str(self.project.id),
                'selected_ids': ['0', '1'],
            },
            format='multipart',
        )
        force_authenticate(request, user=self.user)
        response = ApiRequestViewSet.as_view({'post': 'import_batch'})(request)

        self.assertEqual(response.status_code, 201)
        root = ApiCollection.objects.get(project=self.project, name='Orders API', parent=None)
        child = ApiCollection.objects.get(project=self.project, name='Admin', parent=root)
        self.assertEqual(root.requests.get(name='List orders').collection_id, root.id)
        self.assertEqual(child.requests.get(name='Delete order').collection_id, child.id)
