"""Scenario step tree persistence, source resolution and definition API tests."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from ..models import ApiCollection, ApiProject, ApiRequest, ScenarioCase, ScenarioStep
from ..request_dto import normalize_request
from ..scenario_service import (
    import_api_request,
    import_scenario_steps,
    load_definition,
    save_definition,
    source_changed,
    sync_from_source,
)

User = get_user_model()


def http_node(name='请求', url='/users', parent=None, position=0, source=None):
    node = {
        'id': str(uuid4()), 'parent': parent, 'position': position, 'type': 'http',
        'name': name, 'enabled': True, 'config': {'request': normalize_request({'url': url})},
        'source_type': None, 'source_id': None,
    }
    if source:
        node['source_type'] = 'api_request'
        node['source_id'] = str(source.pk)
        node['config']['source_meta'] = {'content_hash': 'stale'}
    return node


def wait_node(parent=None, position=0):
    return {
        'id': str(uuid4()), 'parent': parent, 'position': position, 'type': 'wait',
        'name': '等待', 'enabled': True, 'config': {'duration_ms': 100},
        'source_type': None, 'source_id': None,
    }


def group_node(parent=None, position=0):
    return {
        'id': str(uuid4()), 'parent': parent, 'position': position, 'type': 'group',
        'name': '分组', 'enabled': True, 'config': {},
        'source_type': None, 'source_id': None,
    }


class ScenarioServiceFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='scenario-service', password='password')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.collection = ApiCollection.objects.create(name='集合', project=self.project)
        self.api_request = ApiRequest.objects.create(
            collection=self.collection, name='查询用户', method='GET', url='/users',
            created_by=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='场景', created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class SaveDefinitionTests(ScenarioServiceFixture):
    def test_saves_tree_and_normalizes_sibling_positions(self):
        first = http_node('第一个', position=5)
        second = http_node('第二个', position=9)
        container = group_node(position=7)
        nested = wait_node(parent=container['id'], position=7)
        save_definition(self.scenario, [first, second, container, nested], user=self.user)

        steps = list(self.scenario.steps.order_by('position'))
        self.assertEqual(len(steps), 4)
        self.assertEqual([step.position for step in steps if step.parent_id is None], [0, 1, 2])
        child = self.scenario.steps.get(parent_id=container['id'])
        self.assertEqual(child.position, 0)
        self.assertEqual(load_definition(self.scenario)[0]['id'], first['id'])

    def test_removing_nodes_deletes_their_subtree(self):
        parent = group_node()
        child = wait_node(parent=parent['id'])
        save_definition(self.scenario, [parent, child], user=self.user)
        self.assertEqual(self.scenario.steps.count(), 2)
        save_definition(self.scenario, [], user=self.user)
        self.assertEqual(self.scenario.steps.count(), 0)

    def test_rejects_leaf_parent_and_duplicate_ids(self):
        leaf = wait_node()
        child = http_node(parent=leaf['id'])
        with self.assertRaises(ValidationError) as leaf_error:
            save_definition(self.scenario, [leaf, child], user=self.user)
        self.assertIn('steps', leaf_error.exception.detail['errors'])
        duplicated = http_node()
        with self.assertRaises(ValidationError):
            save_definition(self.scenario, [duplicated, {**duplicated, 'position': 1}], user=self.user)
        self.assertEqual(self.scenario.steps.count(), 0)

    def test_rejects_non_http_name_rewrite_on_existing_step(self):
        wait = wait_node()
        save_definition(self.scenario, [wait], user=self.user)
        wait['name'] = '我改的名字'
        with self.assertRaises(ValidationError) as rewrite_error:
            save_definition(self.scenario, [wait], user=self.user)
        self.assertIn('name', str(rewrite_error.exception.detail['errors']['steps']))

    def test_stores_variables_and_execution_config(self):
        save_definition(self.scenario, [http_node()], user=self.user,
                        variables={'tenant': 'acme'}, execution_config={'on_error': 'continue'})
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.variables, {'tenant': 'acme'})
        self.assertEqual(self.scenario.execution_config['on_error'], 'continue')
        self.assertEqual(self.scenario.execution_config['total_timeout_ms'], 600000)
        with self.assertRaises(ValidationError):
            save_definition(self.scenario, [], user=self.user,
                            execution_config={'on_error': 'ignore'})


class SourceResolutionTests(ScenarioServiceFixture):
    def test_http_source_must_belong_to_the_same_project(self):
        other_project = ApiProject.objects.create(
            name='其他项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        other_collection = ApiCollection.objects.create(name='其他集合', project=other_project)
        foreign = ApiRequest.objects.create(
            collection=other_collection, name='外部请求', method='GET', url='/x', created_by=self.user)
        with self.assertRaises(ValidationError):
            save_definition(self.scenario, [http_node(source=foreign)], user=self.user)

    def test_source_without_project_permission_is_rejected(self):
        stranger = User.objects.create_user(username='stranger', password='password')
        with self.assertRaises(ValidationError):
            save_definition(self.scenario, [http_node(source=self.api_request)], user=stranger)

    def test_scenario_reference_requires_target_and_blocks_cycles(self):
        target = ScenarioCase.objects.create(
            project=self.project, name='目标场景', created_by=self.user)
        ref = {
            'id': str(uuid4()), 'parent': None, 'position': 0, 'type': 'scenario_ref',
            'name': '引用场景：目标场景', 'enabled': True, 'config': {},
            'source_type': 'scenario_case', 'source_id': str(target.pk),
        }
        save_definition(self.scenario, [ref], user=self.user)
        self.assertEqual(self.scenario.steps.count(), 1)

        # Target now references back to the first scenario, so saving it must fail.
        back = {**ref, 'id': str(uuid4()), 'source_id': str(self.scenario.pk)}
        with self.assertRaises(ValidationError):
            save_definition(target, [back], user=self.user)

    def test_self_reference_is_rejected(self):
        ref = {
            'id': str(uuid4()), 'parent': None, 'position': 0, 'type': 'scenario_ref',
            'name': '引用场景：自身', 'enabled': True, 'config': {},
            'source_type': 'scenario_case', 'source_id': str(self.scenario.pk),
        }
        with self.assertRaises(ValidationError):
            save_definition(self.scenario, [ref], user=self.user)


class ImportTests(ScenarioServiceFixture):
    def test_import_api_request_creates_independent_http_step(self):
        import_api_request(self.scenario, self.api_request.pk, user=self.user)
        import_api_request(self.scenario, self.api_request.pk, user=self.user)
        steps = list(self.scenario.steps.order_by('position'))
        self.assertEqual(len(steps), 2)
        self.assertEqual({step.source_type for step in steps}, {'api_request'})
        self.assertEqual({step.source_id for step in steps}, {str(self.api_request.pk)})
        self.assertEqual(steps[0].config['request']['url'], '/users')
        self.assertTrue(steps[0].config['source_meta']['content_hash'])

    def test_import_scenario_deep_copies_with_new_ids(self):
        source = ScenarioCase.objects.create(
            project=self.project, name='来源场景', created_by=self.user)
        parent = group_node()
        child = wait_node(parent=parent['id'])
        save_definition(source, [parent, child], user=self.user)

        import_scenario_steps(self.scenario, source.pk, user=self.user)
        copied = list(self.scenario.steps.order_by('position'))
        self.assertEqual(len(copied), 2)
        self.assertNotIn(str(copied[0].id), {parent['id'], child['id']})
        root = self.scenario.steps.get(parent__isnull=True)
        self.assertEqual(self.scenario.steps.get(parent=root).type, 'wait')

    def test_cannot_import_itself(self):
        with self.assertRaises(ValidationError):
            import_scenario_steps(self.scenario, self.scenario.pk, user=self.user)

    def test_import_request_without_permission_raises(self):
        stranger = User.objects.create_user(username='import-stranger', password='password')
        with self.assertRaises(PermissionDenied):
            import_api_request(self.scenario, self.api_request.pk, user=stranger)


class SourceSyncTests(ScenarioServiceFixture):
    def test_detects_and_applies_source_changes(self):
        import_api_request(self.scenario, self.api_request.pk, user=self.user)
        step = self.scenario.steps.get()
        self.assertFalse(source_changed(step, user=self.user))

        self.api_request.url = '/users/v2'
        self.api_request.save(update_fields=['url'])
        self.assertTrue(source_changed(step, user=self.user))
        sync_from_source(step, user=self.user)
        step.refresh_from_db()
        self.assertEqual(step.config['request']['url'], '/users/v2')
        self.assertFalse(source_changed(step, user=self.user))

    def test_sync_fails_when_source_is_deleted(self):
        import_api_request(self.scenario, self.api_request.pk, user=self.user)
        step = self.scenario.steps.get()
        self.api_request.delete()
        from rest_framework.exceptions import NotFound
        with self.assertRaises(NotFound):
            sync_from_source(step, user=self.user)


class DefinitionApiTests(ScenarioServiceFixture):
    def test_get_and_put_definition(self):
        url = f'/api/api-testing/scenario-cases/{self.scenario.id}/definition/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['steps'], [])

        node = http_node('保存请求')
        response = self.client.put(
            url, {'steps': [node], 'variables': {'tenant': 'acme'}}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['steps'][0]['name'], '保存请求')
        self.assertEqual(response.data['variables'], {'tenant': 'acme'})
        self.assertEqual(self.scenario.steps.count(), 1)

    def test_put_definition_reports_invalid_tree(self):
        url = f'/api/api-testing/scenario-cases/{self.scenario.id}/definition/'
        node = http_node()
        node['type'] = 'unknown'
        response = self.client.put(url, {'steps': [node]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('errors', response.data)
        self.assertEqual(self.scenario.steps.count(), 0)

    def test_validate_endpoint_does_not_persist(self):
        url = f'/api/api-testing/scenario-cases/{self.scenario.id}/validate/'
        response = self.client.post(url, {'steps': [http_node()]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['valid'])
        self.assertEqual(self.scenario.steps.count(), 0)

    def test_import_request_endpoint_returns_steps(self):
        url = f'/api/api-testing/scenario-cases/{self.scenario.id}/import-request/'
        response = self.client.post(url, {'request_id': self.api_request.pk}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['steps'][0]['source_type'], 'api_request')
        self.assertEqual(self.scenario.steps.count(), 1)

    def test_step_capabilities_endpoint(self):
        response = self.client.get('/api/api-testing/scenario-cases/step-capabilities/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['types']['http']['name_editable'])
        self.assertFalse(response.data['types']['group']['name_editable'])
        self.assertFalse(response.data['types']['else']['independently_creatable'])

    def test_delete_blocked_while_referenced(self):
        target = ScenarioCase.objects.create(
            project=self.project, name='被引用场景', created_by=self.user)
        ref = {
            'id': str(uuid4()), 'parent': None, 'position': 0, 'type': 'scenario_ref',
            'name': '引用场景：被引用场景', 'enabled': True, 'config': {},
            'source_type': 'scenario_case', 'source_id': str(target.pk),
        }
        save_definition(self.scenario, [ref], user=self.user)
        response = self.client.delete(f'/api/api-testing/scenario-cases/{target.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(ScenarioCase.objects.filter(pk=target.pk).exists())
