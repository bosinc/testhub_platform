# -*- coding: utf-8 -*-
"""场景用例 / 测试套件执行视图：确认已委托给 utils 的统一执行实现。

这两个视图原先各自维护一份执行循环，现已收敛为对
``utils.execute_scenario_case`` / ``utils.execute_test_suite`` 的调用，
本文件只验证「视图层不再自带执行逻辑，且响应契约保持兼容」。

运行: python manage.py test apps.api_testing.test_execute_views
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ApiProject, ScenarioCase, TestExecution, TestSuite, TestSuiteItem,
)

User = get_user_model()


class ExecuteViewFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='execute_view_user', email='ev@a.com')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='登录场景', created_by=self.user)
        self.suite = TestSuite.objects.create(
            project=self.project, name='冒烟套件', created_by=self.user)
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
            project=self.project, name='空套件', created_by=self.user)
        with patch('apps.api_testing.views.execute_test_suite',
                   return_value={'success': False, 'test_suite': empty_suite.name,
                                 'suite_execution_id': 99, 'results': []}):
            response = self.client.post(f'/api/api-testing/test-suites/{empty_suite.id}/execute/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['results'], [])
