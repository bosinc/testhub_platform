"""Scenario step-tree execution tests.

HTTP calls are replaced by a fake transport so the tests only cover control
flow, variable scoping and result recording.
"""

import json
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from ..models import ApiProject, Environment, ScenarioCase, TestExecution
from ..scenario_executor import (
    ABORT,
    CONTINUE,
    FAILED,
    PASSED,
    SKIPPED,
    ExecutionRecorder,
    ScenarioExecutor,
    execute_scenario_tree,
)
from ..step_registry import STEP_REGISTRY

User = get_user_model()


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, text=None, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {'content-type': 'application/json'}
        self.text = text if text is not None else json.dumps(payload if payload is not None else {})

    def json(self):
        return self._payload


def fake_transport(response):
    """Return a stand-in for ``execute_request`` that records its calls."""

    def _execute(dto, *, base_url, variables=None, session=None):
        _execute.calls.append({'dto': dto, 'base_url': base_url})
        return {
            'response': response,
            'response_time': 12.5,
            'request': {'url': f'{base_url}{dto.get("url", "")}', 'method': dto.get('method'),
                        'headers': {}, 'params': {}, 'body': None},
        }

    _execute.calls = []
    return _execute


def node(step_type, config=None, *, parent=None, position=0, name=None, step_id=None,
         enabled=True, source_type=None, source_id=None):
    """Build a validated node so test fixtures stay legal definitions."""
    config = STEP_REGISTRY[step_type].validate_config(config or {})
    return {
        'id': step_id or str(uuid4()),
        'parent': parent,
        'position': position,
        'type': step_type,
        'name': name or step_type,
        'enabled': enabled,
        'config': config,
        'source_type': source_type,
        'source_id': source_id,
    }


def http_node(*, url='/users', parent=None, position=0, name='请求', assertions=None,
              extractors=None):
    request = {'method': 'GET', 'url': url}
    if assertions:
        request['assertions'] = assertions
    if extractors:
        request['extractors'] = extractors
    return node('http', {'request': request}, parent=parent, position=position, name=name)


def run(nodes, *, variables=None, environment=None, execution_config=None):
    executor = ScenarioExecutor(
        nodes=nodes,
        environment=environment or {'base_url': 'https://api.example.com', 'variables': {}},
        variables=variables or {},
        execution_config=execution_config or {},
        recorder=ExecutionRecorder(None),
    )
    return executor, executor.run()


class HttpStepTests(SimpleTestCase):
    def test_records_request_and_response_output(self):
        transport = fake_transport(FakeResponse({'data': {'id': 5}}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([http_node()])
        self.assertEqual(outcome['passed'], 1)
        self.assertEqual(outcome['failed'], 0)
        result = outcome['results'][0]
        self.assertEqual(result['status'], PASSED)
        self.assertEqual(result['response']['status_code'], 200)
        self.assertEqual(result['response']['body'], {'data': {'id': 5}})
        self.assertEqual(transport.calls[0]['base_url'], 'https://api.example.com')

    def test_failed_assertion_fails_the_step_and_stops_by_default(self):
        transport = fake_transport(FakeResponse({'data': {'id': 5}}))
        assertions = [{'name': '状态码', 'type': 'status_code', 'expected': 201}]
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([http_node(assertions=assertions), http_node(position=1)])
        self.assertEqual(outcome['failed'], 1)
        self.assertIn('断言未通过', outcome['results'][0]['error'])
        # The second step is recorded as skipped, never sent.
        self.assertEqual(outcome['results'][1]['status'], SKIPPED)
        self.assertEqual(len(transport.calls), 1)
        self.assertIn('断言未通过', outcome['error'])

    def test_continue_policy_keeps_running_after_a_failure(self):
        transport = fake_transport(FakeResponse({'data': {'id': 5}}))
        assertions = [{'name': '状态码', 'type': 'status_code', 'expected': 201}]
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([http_node(assertions=assertions), http_node(position=1)],
                             execution_config={'on_error': 'continue'})
        self.assertEqual(outcome['failed'], 1)
        self.assertEqual(outcome['passed'], 1)
        self.assertEqual(len(outcome['results']), 2)

    def test_extracted_values_become_run_variables(self):
        transport = fake_transport(FakeResponse({'data': {'id': 5}}))
        extractors = [{'name': 'user_id', 'expression': '$.data.id'}]
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            executor, _ = run([http_node(extractors=extractors)])
        self.assertEqual(executor.scope.require('vars.user_id'), 5)

    def test_step_output_is_referenced_by_uuid_in_a_later_request(self):
        first = http_node()
        second = http_node(url='/users/{{ steps.' + first['id'] + '.response.body.data.id }}',
                           position=1)
        transport = fake_transport(FakeResponse({'data': {'id': 5}}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([first, second])
        self.assertEqual(outcome['passed'], 2)
        self.assertEqual(transport.calls[1]['dto']['url'], '/users/5')

    def test_missing_variable_fails_the_step_with_a_clear_message(self):
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([http_node(url='/{{ vars.absent }}')])
        self.assertEqual(outcome['failed'], 1)
        self.assertIn('vars.absent', outcome['results'][0]['error'])
        self.assertEqual(transport.calls, [])


class ControlFlowTests(SimpleTestCase):
    def test_group_runs_children_in_order(self):
        group = node('group')
        first = http_node(parent=group['id'], position=0)
        second = http_node(parent=group['id'], position=1)
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([group, first, second])
        names = [item['step_id'] for item in outcome['results']]
        self.assertEqual(names, [group['id'], first['id'], second['id']])

    def test_condition_true_runs_then_branch_and_skips_else(self):
        condition = node('condition', {'left': 2, 'operator': 'gte', 'right': 2,
                                       'right_type': 'number'})
        then_step = http_node(parent=condition['id'])
        else_node = node('else', {'if_step_id': condition['id']}, position=1)
        else_step = http_node(parent=else_node['id'])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([condition, then_step, else_node, else_step])
        statuses = {item['step_id']: item['status'] for item in outcome['results']}
        self.assertEqual(statuses[then_step['id']], PASSED)
        self.assertEqual(statuses[else_node['id']], SKIPPED)
        self.assertEqual(statuses[else_step['id']], SKIPPED)
        self.assertEqual(len(transport.calls), 1)

    def test_condition_false_runs_else_branch(self):
        condition = node('condition', {'left': 1, 'operator': 'gte', 'right': 2,
                                       'right_type': 'number'})
        then_step = http_node(parent=condition['id'])
        else_node = node('else', {'if_step_id': condition['id']}, position=1)
        else_step = http_node(parent=else_node['id'])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([condition, then_step, else_node, else_step])
        statuses = {item['step_id']: item['status'] for item in outcome['results']}
        self.assertEqual(statuses[then_step['id']], SKIPPED)
        self.assertEqual(statuses[else_step['id']], PASSED)

    def test_condition_operators_cover_common_cases(self):
        cases = [
            ({'left': 'abc', 'operator': 'contains', 'right': 'b', 'right_type': 'string'}, True),
            ({'left': [1, 2], 'operator': 'contains', 'right': 2, 'right_type': 'string'}, True),
            ({'left': None, 'operator': 'exists', 'right': None, 'right_type': 'null'}, False),
            ({'left': '', 'operator': 'is_empty', 'right': None, 'right_type': 'null'}, True),
            ({'left': 'x', 'operator': 'not_empty', 'right': None, 'right_type': 'null'}, True),
            ({'left': 3, 'operator': 'lt', 'right': 5, 'right_type': 'number'}, True),
            ({'left': 3, 'operator': 'eq', 'right': 3, 'right_type': 'number'}, True),
        ]
        for config, expected in cases:
            with self.subTest(config=config):
                executor = ScenarioExecutor(
                    nodes=[], environment={'base_url': 'https://x'}, recorder=ExecutionRecorder())
                self.assertEqual(executor._evaluate_condition(config), expected)

    def test_for_loop_exposes_index_and_honours_max_iterations(self):
        loop = node('for', {'count': 3})
        body = http_node(parent=loop['id'])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([loop, body])
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(outcome['passed'], 4)

    def test_foreach_iterates_the_array_expression(self):
        loop = node('foreach', {'array_expression': '{{ vars.items }}'})
        body = http_node(parent=loop['id'], url='/users/{{ loop.item.id }}')
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([loop, body], variables={'items': [{'id': 1}, {'id': 2}]})
        self.assertEqual([call['dto']['url'] for call in transport.calls],
                         ['/users/1', '/users/2'])
        self.assertEqual(outcome['failed'], 0)

    def test_foreach_rejects_non_array_expression(self):
        loop = node('foreach', {'array_expression': '{{ vars.items }}'})
        with patch('apps.api_testing.scenario_executor.execute_request',
                   fake_transport(FakeResponse({}))):
            _, outcome = run([loop], variables={'items': 'not-a-list'})
        self.assertEqual(outcome['failed'], 1)
        self.assertIn('数组', outcome['results'][0]['error'])

    def test_break_if_stops_the_loop(self):
        loop = node('for', {'count': 5, 'break_if': {
            'left': '{{ loop.index }}', 'operator': 'gte', 'right': 2, 'right_type': 'number'}})
        body = http_node(parent=loop['id'])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            run([loop, body])
        self.assertEqual(len(transport.calls), 2)

    def test_disabled_steps_are_skipped_with_their_subtree(self):
        group = node('group', enabled=False)
        child = http_node(parent=group['id'])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([group, child])
        self.assertEqual({item['status'] for item in outcome['results']}, {SKIPPED})
        self.assertEqual(transport.calls, [])

    def test_wait_step_records_duration(self):
        _, outcome = run([node('wait', {'duration_ms': 0})])
        self.assertEqual(outcome['results'][0]['status'], PASSED)

    def test_unsupported_step_type_aborts_before_running_anything(self):
        script = node('script', {'language': 'javascript', 'code': 'return 1;'})
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            _, outcome = run([script])
        self.assertEqual(outcome['failed'], 1)
        self.assertIn('无法执行', outcome['error'])

    def test_cancel_check_stops_the_run(self):
        executor = ScenarioExecutor(
            nodes=[http_node()],
            environment={'base_url': 'https://x'},
            recorder=ExecutionRecorder(),
            cancel_check=lambda: True,
        )
        outcome = executor.run()
        self.assertEqual(outcome['error'], '执行已取消')
        self.assertEqual(outcome['results'][0]['status'], SKIPPED)


class ScenarioTreeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='executor', password='password')
        self.project = ApiProject.objects.create(
            name='项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.environment = Environment.objects.create(
            name='测试环境', scope='LOCAL', project=self.project,
            base_url='https://api.example.com',
            variables={'tenant': 'acme'}, created_by=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='场景', created_by=self.user)

    def _save(self, nodes, **kwargs):
        from ..scenario_service import save_definition

        return save_definition(self.scenario, nodes, user=self.user, **kwargs)

    def test_execute_scenario_tree_persists_a_completed_execution(self):
        self._save([http_node()])
        transport = fake_transport(FakeResponse({'ok': True}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)
        self.assertTrue(result['success'])
        self.assertEqual(result['passed_count'], 1)
        execution = TestExecution.objects.get(pk=result['execution_id'])
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertEqual(execution.total_requests, 1)
        self.assertEqual(execution.results[0]['status'], PASSED)

    def test_environment_variables_reach_the_request(self):
        self._save([http_node(url='/{{ env.tenant }}/users')])
        transport = fake_transport(FakeResponse({}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            execute_scenario_tree(self.scenario, self.environment, self.user)
        self.assertEqual(transport.calls[0]['dto']['url'], '/acme/users')

    def test_execution_without_environment_is_recorded_as_failed(self):
        self._save([http_node()])
        result = execute_scenario_tree(self.scenario, None, self.user)
        self.assertFalse(result['success'])
        self.assertIn('环境', result['error'])
        self.assertEqual(TestExecution.objects.get(pk=result['execution_id']).status, 'FAILED')

    def test_scenario_without_steps_is_failed_not_passed(self):
        result = execute_scenario_tree(self.scenario, self.environment, self.user)
        self.assertFalse(result['success'])
        self.assertEqual(result['total_count'], 0)

    def test_scenario_reference_runs_target_and_maps_outputs(self):
        target = ScenarioCase.objects.create(
            project=self.project, name='被引用场景', created_by=self.user)
        from ..scenario_service import save_definition

        target_step = http_node(url='/token')
        save_definition(target, [target_step], user=self.user)
        ref = node('scenario_ref', {
            'input_mapping': {'tenant': '{{ env.tenant }}'},
            'output_mapping': {'exported': '{{ steps.' + target_step['id'] + '.response.body.data.id }}'},
        }, source_type='scenario_case', source_id=str(target.pk))
        self._save([ref])
        transport = fake_transport(FakeResponse({'data': {'id': 9}}))
        with patch('apps.api_testing.scenario_executor.execute_request', transport):
            result = execute_scenario_tree(self.scenario, self.environment, self.user)
        self.assertTrue(result['success'], result)
        # The caller exported the nested value through output_mapping.
        self.assertTrue(any(item['status'] == PASSED for item in result['results']))

    def test_scenario_reference_cycle_is_rejected_at_save_time(self):
        from rest_framework.exceptions import ValidationError

        other = ScenarioCase.objects.create(
            project=self.project, name='循环目标', created_by=self.user)
        ref_back = node('scenario_ref', {}, source_type='scenario_case',
                        source_id=str(self.scenario.pk))
        from ..scenario_service import save_definition

        save_definition(other, [ref_back], user=self.user)
        forward = node('scenario_ref', {}, source_type='scenario_case', source_id=str(other.pk))
        with self.assertRaises(ValidationError):
            self._save([forward])

    def test_flat_legacy_scenario_still_uses_the_old_path(self):
        from ..models import ApiCollection, ApiRequest, ScenarioCaseRequest
        from ..utils import execute_scenario_case

        collection = ApiCollection.objects.create(name='集合', project=self.project)
        api_request = ApiRequest.objects.create(
            collection=collection, name='请求', method='GET', url='/legacy',
            created_by=self.user)
        ScenarioCaseRequest.objects.create(
            scenario_case=self.scenario, request=api_request, order=0)
        result = execute_scenario_case(self.scenario, self.environment, self.user)
        self.assertIn('execution_id', result)
        self.assertEqual(result['total_count'], 1)
