# -*- coding: utf-8 -*-
"""场景用例 / 测试套件执行视图：确认已委托给 utils 的统一执行实现。

这两个视图原先各自维护一份执行循环，现已收敛为对
``utils.execute_scenario_case`` / ``utils.execute_test_suite`` 的调用，
本文件只验证「视图层不再自带执行逻辑，且响应契约保持兼容」。

运行: python manage.py test apps.api_testing.test_execute_views
"""
from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ..models import (
    ApiProject, ApiCollection, ApiRequest, Environment, RequestHistory,
    ApiSuiteExecution, ScheduledTask, ScenarioCase, TestExecution, TestSuite, TestSuiteItem,
)
from ..serializers import ScheduledTaskSerializer

User = get_user_model()


class ExecuteViewFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='execute_view_user', email='ev@a.com')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='登录场景', created_by=self.user)
        self.environment = Environment.objects.create(
            name='测试环境', scope='LOCAL', project=self.project, created_by=self.user)
        self.suite = TestSuite.objects.create(
            project=self.project, name='冒烟套件', environment=self.environment, created_by=self.user)
        TestSuiteItem.objects.create(
            test_suite=self.suite, scenario_case=self.scenario)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _execution(self, status='COMPLETED'):
        return TestExecution.objects.create(
            scenario_case=self.scenario, status=status,
            start_time=timezone.now(), end_time=timezone.now(),
            total_requests=1, passed_requests=1, failed_requests=0,
            results=[{'name': '登录', 'passed': True, 'status_code': 200}],
            executed_by=self.user,
        )


class ScenarioCaseExecuteViewTest(ExecuteViewFixture):
    def test_delegates_to_shared_executor(self):
        execution = self._execution()
        with patch('apps.api_testing.views.execute_scenario_case',
                   return_value={'success': True, 'execution_id': execution.id,
                                 'passed_count': 1, 'failed_count': 0,
                                 'total_count': 1, 'results': []}) as runner:
            response = self.client.post(
                f'/api/api-testing/scenario-cases/{self.scenario.id}/execute/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], execution.id)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], self.scenario)
        self.assertIsNone(runner.call_args.args[1])  # 场景未绑定环境

    def test_returns_400_when_executor_cannot_create_record(self):
        with patch('apps.api_testing.views.execute_scenario_case',
                   return_value={'success': False, 'error': '数据库不可用'}):
            response = self.client.post(
                f'/api/api-testing/scenario-cases/{self.scenario.id}/execute/')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error'], '数据库不可用')


class TestSuiteExecuteViewTest(ExecuteViewFixture):
    def test_delegates_to_shared_suite_executor(self):
        payload = {
            'success': True,
            'test_suite': self.suite.name,
            'suite_execution_id': 88,
            'results': [{'scenario_case_id': self.scenario.id,
                         'scenario_case_name': self.scenario.name,
                         'status': 'COMPLETED'}],
        }
        with patch('apps.api_testing.views.execute_test_suite',
                   return_value=payload) as runner:
            response = self.client.post(f'/api/api-testing/test-suites/{self.suite.id}/execute/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['suite_execution_id'], 88)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['results'], payload['results'])
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], self.suite)

    def test_empty_suite_is_reported_as_failure(self):
        empty_suite = TestSuite.objects.create(
            project=self.project, name='空套件', environment=self.environment, created_by=self.user)
        with patch('apps.api_testing.views.execute_test_suite',
                   return_value={'success': False, 'test_suite': empty_suite.name,
                                 'suite_execution_id': 99, 'results': []}):
            response = self.client.post(f'/api/api-testing/test-suites/{empty_suite.id}/execute/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['results'], [])

    def test_scheduled_scenario_task_uses_scenario_handler(self):
        """场景用例定时任务必须进入场景执行分支并返回其结果。"""
        from ..views import ScheduledTaskViewSet

        view = ScheduledTaskViewSet()
        task = SimpleNamespace(task_type='SCENARIO_CASE')
        expected = {'success': True, 'execution_ids': [1]}
        with patch.object(view, '_execute_scenario_case', return_value=expected) as handler:
            result = view._execute_task(task)

        self.assertEqual(result, expected)
        handler.assert_called_once_with(task)


class ScheduledExecutionAssociationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='scheduled-user')
        self.project = ApiProject.objects.create(
            name='项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.collection = ApiCollection.objects.create(name='集合', project=self.project)
        self.request = ApiRequest.objects.create(
            collection=self.collection, name='请求', method='GET', url='/health',
            request_type='HTTP',
            headers=[], params=[{'key': 'check', 'value': 'true', 'enabled': True}],
            created_by=self.user)
        self.environment = Environment.objects.create(
            name='环境', scope='LOCAL', project=self.project,
            base_url='https://example.com', created_by=self.user)

    def test_scheduled_task_target_cleanup_uses_null_for_foreign_keys(self):
        validated_data = {
            'task_type': 'API_REQUEST',
            'test_suite': object(),
            'api_requests': [self.request.id],
            'scenario_cases': [1],
        }

        ScheduledTaskSerializer._clear_unused_targets(validated_data)

        self.assertIsNone(validated_data['test_suite'])
        self.assertEqual(validated_data['api_requests'], [self.request.id])
        self.assertEqual(validated_data['scenario_cases'], [])

    @patch('requests.Session.request')
    def test_scheduled_request_supports_key_value_rows_and_saves_history(self, send):
        from ..utils import execute_api_request

        response = SimpleNamespace(
            status_code=200, headers={'content-type': 'application/json'},
            text='{}', json=lambda: {}, request=None)
        send.return_value = response
        task = ScheduledTask.objects.create(
            name='请求任务', task_type='API_REQUEST', trigger_type='CRON',
            cron_expression='0 * * * *', api_requests=[self.request.id],
            environment=self.environment, created_by=self.user)

        result = execute_api_request(self.request, self.environment, self.user, scheduled_task=task)

        self.assertTrue(result['success'])
        self.assertEqual(RequestHistory.objects.get(pk=result['history_id']).scheduled_task_id, task.id)
        self.assertEqual(send.call_args.kwargs['url'], 'https://example.com/health')
        self.assertEqual(send.call_args.kwargs['params'], [('check', 'true')])

    @patch('requests.Session.request')
    def test_scheduled_request_is_sent_the_way_the_interface_page_sends_it(self, send):
        """定时执行与单接口页「发送」共用一套规则：环境变量和认证配置都要生效。"""
        from ..utils import execute_api_request

        send.return_value = SimpleNamespace(status_code=200, headers={}, text='ok', request=None)
        self.environment.variables = {
            'token': {'initialValue': 'abc', 'currentValue': 'abc'}, 'uid': '42'}
        self.environment.save(update_fields=['variables'])
        self.request.url = '/users/{id}'
        self.request.path_params = [{'key': 'id', 'value': '{{env.uid}}'}]
        self.request.auth = {'type': 'bearer', 'token': '{{env.token}}'}
        self.request.save()

        result = execute_api_request(self.request, self.environment, self.user)

        self.assertTrue(result['success'])
        self.assertEqual(send.call_args.kwargs['url'], 'https://example.com/users/42')
        self.assertEqual(send.call_args.kwargs['headers'], {'Authorization': 'Bearer abc'})

    @patch('requests.Session.request')
    def test_scheduled_request_reports_a_readable_reason_without_sending(self, send):
        from ..utils import execute_api_request

        self.request.headers = [{'key': 'Authorization', 'value': 'Bearer {{env.token}}'}]
        self.request.save(update_fields=['headers'])

        result = execute_api_request(self.request, self.environment, self.user)

        self.assertEqual(result, {'success': False, 'error': (
            '变量 {{ env.token }} 不存在或不可用；如需按原文发送，请在花括号前加反斜杠，例如 \\{{ name }}')})
        send.assert_not_called()
        self.assertFalse(RequestHistory.objects.exists())

    @patch('requests.Session.request')
    def test_scheduled_request_task_fails_when_an_assertion_does_not_pass(self, send):
        """请求发出去不等于通过：任一断言未通过，该请求和整个任务都算失败。"""
        from ..views import ScheduledTaskViewSet

        send.return_value = SimpleNamespace(status_code=200, headers={}, text='ok', request=None)
        self.request.assertions = [{'name': '状态码', 'type': 'status_code', 'expected': 200}]
        self.request.save(update_fields=['assertions'])
        failing = ApiRequest.objects.create(
            collection=self.collection, name='创建', method='GET', url='/create',
            assertions=[{'name': '返回创建成功', 'type': 'status_code', 'expected': 201}],
            created_by=self.user)
        task = ScheduledTask.objects.create(
            name='请求任务', task_type='API_REQUEST', trigger_type='CRON',
            cron_expression='0 * * * *', api_requests=[self.request.id, failing.id],
            environment=self.environment, created_by=self.user)

        result = ScheduledTaskViewSet()._execute_api_request(task)

        self.assertFalse(result['success'])
        self.assertEqual((result['passed_count'], result['failed_count']), (1, 1))
        self.assertTrue(result['results'][0]['success'])
        self.assertEqual(result['results'][1]['error'], '断言未通过：返回创建成功')
        # 两条请求都已发出，响应和历史照常保留。
        self.assertEqual(result['results'][1]['status_code'], 200)
        self.assertEqual(len(result['history_ids']), 2)

    @patch('apps.api_testing.utils.execute_scenario_case')
    def test_scheduled_suite_associates_suite_and_scenario_records(self, execute):
        from ..utils import execute_test_suite

        scenario = ScenarioCase.objects.create(
            project=self.project, name='场景', created_by=self.user)
        suite = TestSuite.objects.create(
            project=self.project, name='套件', environment=self.environment,
            created_by=self.user)
        TestSuiteItem.objects.create(test_suite=suite, scenario_case=scenario)
        task = ScheduledTask.objects.create(
            name='套件任务', task_type='TEST_SUITE', trigger_type='CRON',
            cron_expression='0 * * * *', test_suite=suite,
            environment=self.environment, created_by=self.user)
        execute.return_value = {
            'success': True, 'execution_id': 1, 'passed_count': 1,
            'failed_count': 0, 'total_count': 1,
        }

        result = execute_test_suite(suite, self.environment, self.user, scheduled_task=task)

        self.assertTrue(result['success'])
        self.assertEqual(result['suite_execution_id'],
                         ApiSuiteExecution.objects.get(scheduled_task=task).id)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs['scheduled_task'], task)
