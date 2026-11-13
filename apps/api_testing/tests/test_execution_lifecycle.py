"""Execution lifecycle tests: step records, polling, cancellation and async runs."""

import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ..models import ApiProject, Environment, ScenarioCase, StepExecution, TestExecution
from ..scenario_executor import execute_scenario_tree
from ..step_registry import STEP_REGISTRY
from ..tasks import run_scenario_execution, sweep_stale_executions

User = get_user_model()


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.headers = {'content-type': 'application/json'}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def transport(response=None, *, fail=False):
    def _execute(dto, *, base_url, variables=None, session=None):
        _execute.calls.append(dto)
        return {
            'response': response or FakeResponse({}),
            'response_time': 5.0,
            'request': {'url': f'{base_url}{dto.get("url", "")}', 'method': dto.get('method'),
                        'headers': {}, 'params': {}, 'body': None},
        }

    _execute.calls = []
    return _execute


def http_node(*, url='/users', parent=None, position=0, name='请求'):
    config = STEP_REGISTRY['http'].validate_config({'request': {'method': 'GET', 'url': url}})
    return {
        'id': str(uuid4()), 'parent': parent, 'position': position, 'type': 'http',
        'name': name, 'enabled': True, 'config': config,
        'source_type': None, 'source_id': None,
    }


class ExecutionRecordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lifecycle', password='password')
        self.project = ApiProject.objects.create(
            name='项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.environment = Environment.objects.create(
            name='环境', scope='LOCAL', project=self.project,
            base_url='https://api.example.com', variables={}, created_by=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='场景', created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _save(self, nodes):
        from ..scenario_service import save_definition

        return save_definition(self.scenario, nodes, user=self.user)

    def test_each_step_becomes_a_step_execution_row(self):
        first = http_node()
        group = {'id': str(uuid4()), 'parent': None, 'position': 1, 'type': 'group',
                 'name': '分组', 'enabled': True, 'config': {},
                 'source_type': None, 'source_id': None}
        child = http_node(parent=group['id'])
        self._save([first, group, child])
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)

        rows = list(StepExecution.objects.filter(execution_id=result['execution_id']))
        self.assertEqual([row.sequence for row in rows], [1, 2, 3])
        self.assertEqual({row.status for row in rows}, {'PASSED'})
        # Nested steps record their parent invocation so the tree can be rebuilt.
        nested = StepExecution.objects.get(step_id_snapshot=child['id'])
        self.assertEqual(nested.parent_invocation_id, group['id'])
        self.assertEqual(nested.invocation_id, f'{group["id"]}/{child["id"]}')
        self.assertEqual(StepExecution.objects.get(step_id_snapshot=first['id']).iteration_path, '')

    def test_loop_iterations_are_distinguishable(self):
        loop = {'id': str(uuid4()), 'parent': None, 'position': 0, 'type': 'for',
                'name': '循环', 'enabled': True,
                'config': STEP_REGISTRY['for'].validate_config({'count': 2}),
                'source_type': None, 'source_id': None}
        body = http_node(parent=loop['id'])
        self._save([loop, body])
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)
        rows = StepExecution.objects.filter(
            execution_id=result['execution_id'], step_id_snapshot=body['id']).order_by('sequence')
        self.assertEqual([row.iteration_path for row in rows], ['0', '1'])

    def test_execution_snapshot_records_environment_base_url(self):
        self._save([http_node()])
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)
        execution = TestExecution.objects.get(pk=result['execution_id'])
        self.assertEqual(execution.environment_snapshot['base_url'], 'https://api.example.com')
        self.assertEqual(execution.conclusion, 'PASSED')

    def test_steps_endpoint_supports_incremental_polling(self):
        self._save([http_node(), http_node(position=1)])
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)
        url = f'/api/api-testing/executions/{result["execution_id"]}/steps/'
        full = self.client.get(url)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(len(full.data['steps']), 2)
        self.assertEqual(full.data['conclusion'], 'PASSED')

        incremental = self.client.get(url, {'after_sequence': 1})
        self.assertEqual(len(incremental.data['steps']), 1)
        self.assertEqual(incremental.data['steps'][0]['sequence'], 2)

        invalid = self.client.get(url, {'after_sequence': 'x'})
        self.assertEqual(invalid.status_code, 400)

    def test_cancel_endpoint_marks_a_queued_run(self):
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='PENDING', executed_by=self.user)
        response = self.client.post(f'/api/api-testing/executions/{execution.id}/cancel/')
        self.assertEqual(response.status_code, 200)
        execution.refresh_from_db()
        self.assertTrue(execution.cancel_requested)
        self.assertEqual(execution.status, 'CANCELLED')
        self.assertEqual(execution.conclusion, 'CANCELLED')

    def test_cancel_endpoint_rejects_a_finished_run(self):
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='COMPLETED', conclusion='PASSED',
            executed_by=self.user)
        response = self.client.post(f'/api/api-testing/executions/{execution.id}/cancel/')
        self.assertEqual(response.status_code, 400)

    def test_cancelled_run_is_not_reported_as_passed(self):
        self._save([http_node()])
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='PENDING', executed_by=self.user,
            cancel_requested=True)
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            result = execute_scenario_tree(
                self.scenario, self.environment, self.user, execution=execution)
        self.assertFalse(result['success'])
        self.assertEqual(result['conclusion'], 'CANCELLED')
        self.assertEqual(TestExecution.objects.get(pk=execution.pk).status, 'CANCELLED')

    def test_async_submit_queues_a_pending_record(self):
        self._save([http_node()])
        url = f'/api/api-testing/scenario-cases/{self.scenario.id}/execute/'
        with patch('apps.api_testing.views.enqueue_task') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    url, {'environment_id': self.environment.pk, 'async': True}, format='json')
        self.assertEqual(response.status_code, 202)
        execution = TestExecution.objects.get(pk=response.data['execution_id'])
        self.assertEqual(execution.status, 'PENDING')
        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(enqueue.call_args[0][1], execution.id)

    def test_worker_claims_and_runs_a_queued_execution(self):
        self._save([http_node()])
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='PENDING', executed_by=self.user)
        with patch('apps.api_testing.scenario_executor.execute_request', transport()):
            outcome = run_scenario_execution(execution.pk, self.environment.pk)
        self.assertTrue(outcome['success'])
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertEqual(execution.conclusion, 'PASSED')
        self.assertEqual(execution.passed_requests, 1)

    def test_worker_does_not_run_the_same_record_twice(self):
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='COMPLETED', executed_by=self.user)
        outcome = run_scenario_execution(execution.pk, self.environment.pk)
        self.assertTrue(outcome['skipped'])

    def test_stale_running_execution_is_converged(self):
        execution = TestExecution.objects.create(
            scenario_case=self.scenario, status='RUNNING', executed_by=self.user,
            start_time=timezone.now() - timedelta(hours=1),
            heartbeat_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(sweep_stale_executions(), 1)
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'FAILED')
        self.assertEqual(execution.conclusion, 'FAILED')
        self.assertIn('失联', execution.results[-1]['error'])

    def test_fresh_heartbeat_is_not_swept(self):
        TestExecution.objects.create(
            scenario_case=self.scenario, status='RUNNING', executed_by=self.user,
            start_time=timezone.now(), heartbeat_at=timezone.now())
        self.assertEqual(sweep_stale_executions(), 0)
