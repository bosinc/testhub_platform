# -*- coding: utf-8 -*-
"""单用例执行收敛后的行为测试。

页面「运行用例」与 MCP 异步执行现在都走 ``test_executor.run_single_case``，
本文件覆盖三件事：
1. 共享入口确实复用 TestExecutor 的步骤执行（含 storageState，不再假通过）
2. 执行结果正确落到预建的 TestCaseExecution
3. 视图与 MCP worker 只是委托，不再各自维护步骤循环

运行: python manage.py test apps.ui_automation.test_single_case_runner
"""
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ..models import (
    TestCase, TestCaseExecution, TestCaseStep, UiProject,
)
from ..test_executor import TestExecutor, run_single_case
from ..variable_context import UiVariableContext

User = get_user_model()


class SingleCaseFixture(DjangoTestCase):
    def setUp(self):
        self.user = User.objects.create(username='ui_single_user', email='ui@a.com')
        self.project = UiProject.objects.create(
            name='UI 项目', base_url='https://example.com', owner=self.user)
        self.case = TestCase.objects.create(
            name='登录用例', project=self.project, created_by=self.user)

    def _execution(self):
        return TestCaseExecution.objects.create(
            test_case=self.case, project=self.project, execution_source='manual',
            status='running', engine='playwright', browser='chrome',
            headless=True, created_by=self.user, started_at=timezone.now())

    def _step_data(self, **overrides):
        data = {
            'id': 1,
            'step_number': 1,
            'action_type': 'storageState',
            'description': '切换登录态',
            'input_value': '',
            'wait_time': 1000,
            'assert_type': '',
            'assert_value': '',
            'action_params': {'operation': 'switch', 'storage_state_id': 3},
            'element': None,
        }
        data.update(overrides)
        return data


class CaseDataTest(SingleCaseFixture):
    def test_build_case_data_keeps_action_params_and_element(self):
        TestCaseStep.objects.create(
            test_case=self.case, step_number=1, action_type='storageState',
            action_params={'operation': 'save', 'name': '登录态'})

        executor = TestExecutor(project=self.project)
        case_data = executor._build_case_data(self.case)

        self.assertEqual(case_data['project_id'], self.project.id)
        self.assertEqual(case_data['steps'][0]['action_params']['operation'], 'save')


class StorageStateStepTest(SingleCaseFixture):
    def test_shared_step_executor_handles_storage_state(self):
        """storageState 必须由 TestExecutor 处理，不能落到引擎的「未知动作」。"""
        TestCaseStep.objects.create(
            test_case=self.case, step_number=1, action_type='storageState',
            action_params={'operation': 'switch', 'storage_state_id': 3})
        executor = TestExecutor(project=self.project)
        executor.variables = UiVariableContext(self.project.variables, run={})
        executor._execute_storage_state_action = MagicMock()

        result = executor.execute_step_playwright(self._step_data())

        self.assertTrue(result['success'])
        executor._execute_storage_state_action.assert_called_once_with(
            {'operation': 'switch', 'storage_state_id': 3})


class RunSingleCaseTest(SingleCaseFixture):
    def test_persists_result_into_prebuilt_execution(self):
        execution = self._execution()
        case_result = {
            'test_case_id': self.case.id, 'test_case_name': self.case.name,
            'status': 'failed', 'steps': [{'step_number': 1, 'success': False,
                                          'error': '元素超时'}],
            'error': '元素超时', 'screenshots': [{'url': 'data:image/png;base64,x'}],
        }
        with patch('apps.ui_automation.test_executor._run_case_playwright',
                   return_value=case_result):
            result = run_single_case(
                self.case, engine='playwright', browser='chrome',
                headless=True, executed_by=self.user, execution=execution)

        execution.refresh_from_db()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(execution.status, 'failed')
        self.assertEqual(execution.error_message, '元素超时')
        self.assertEqual(json.loads(execution.execution_logs)[0]['error'], '元素超时')
        self.assertEqual(execution.screenshots[0]['url'], 'data:image/png;base64,x')
        self.assertIsNotNone(execution.finished_at)
        self.assertIsNotNone(execution.execution_time)

    def test_execution_exception_is_persisted_as_failed(self):
        execution = self._execution()
        with patch('apps.ui_automation.test_executor._run_case_playwright',
                   side_effect=RuntimeError('浏览器崩溃')):
            result = run_single_case(
                self.case, engine='playwright', executed_by=self.user,
                execution=execution)

        execution.refresh_from_db()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('浏览器崩溃', result['error'])
        self.assertEqual(execution.status, 'failed')
        self.assertIn('浏览器崩溃', execution.error_message)


class McpWorkerDelegatesTest(SingleCaseFixture):
    def test_worker_calls_shared_runner_with_prebuilt_execution(self):
        from apps.ui_automation import mcp_runner

        execution = self._execution()
        with patch('apps.ui_automation.test_executor.run_single_case') as runner, \
                patch.object(mcp_runner, 'connection'):
            mcp_runner._worker_case(execution, self.case, 'playwright', 'chrome', True)

        runner.assert_called_once()
        self.assertIs(runner.call_args.kwargs['execution'], execution)

    def test_worker_marks_failed_when_runner_raises(self):
        from apps.ui_automation import mcp_runner

        execution = self._execution()
        with patch('apps.ui_automation.test_executor.run_single_case',
                   side_effect=RuntimeError('启动失败')), \
                patch.object(mcp_runner, 'connection'):
            mcp_runner._worker_case(execution, self.case, 'playwright', 'chrome', True)

        execution.refresh_from_db()
        self.assertEqual(execution.status, 'failed')
        self.assertIn('启动失败', execution.error_message)


class RunViewDelegatesTest(SingleCaseFixture):
    def setUp(self):
        super().setUp()
        TestCaseStep.objects.create(
            test_case=self.case, step_number=1, action_type='click',
            description='点击按钮')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_view_returns_structured_errors_from_shared_runner(self):
        case_result = {
            'test_case_id': self.case.id, 'test_case_name': self.case.name,
            'status': 'failed',
            'steps': [{'step_number': 1, 'action_type': 'click',
                       'description': '点击按钮', 'success': False,
                       'error': '元素超时'}],
            'error': '元素超时', 'screenshots': [],
        }

        def fake_run(case, *, engine, browser, headless, executed_by, execution):
            execution.status = 'failed'
            execution.error_message = '元素超时'
            execution.execution_logs = json.dumps(case_result['steps'], ensure_ascii=False)
            execution.execution_time = 1.5
            execution.save()
            return case_result

        with patch('apps.ui_automation.test_executor.check_engine_environment',
                   return_value=(True, '')), \
                patch('apps.ui_automation.test_executor.run_single_case',
                      side_effect=fake_run) as runner:
            response = self.client.post(
                f'/api/ui-automation/test-cases/{self.case.id}/run/',
                {'engine': 'playwright', 'browser': 'chrome'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['execution_time'], 1.5)
        self.assertEqual(response.data['errors'][0]['action_type'], '点击')
        self.assertEqual(response.data['errors'][0]['details'], '元素超时')
        self.assertEqual(runner.call_args.kwargs['execution'].id,
                         TestCaseExecution.objects.latest('id').id)
