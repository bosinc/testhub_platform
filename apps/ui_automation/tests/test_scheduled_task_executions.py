"""定时用例和套件必须保存任务关联，筛选与删除不能破坏执行历史。"""
import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.management.commands.run_all_scheduled_tasks import Command
from apps.ui_automation import scheduled_tasks
from apps.ui_automation.models import (
    TestCase as UiTestCase, TestCaseExecution, TestCaseStep, TestExecution, TestSuite,
    TestSuiteTestCase, UiProject, UiScheduledTask,
)
from apps.ui_automation.test_executor import TestExecutor


class ScheduledTaskExecutionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='scheduled-owner')
        self.project = UiProject.objects.create(name='UI', owner=self.user)
        self.cases = [UiTestCase.objects.create(
            name=f'Case {index}', project=self.project, created_by=self.user,
        ) for index in range(2)]
        self.suite = TestSuite.objects.create(name='Suite', project=self.project)
        for index, case in enumerate(self.cases):
            # 没有步骤的用例不会被执行，夹具用例各带一个步骤。
            TestCaseStep.objects.create(test_case=case, step_number=1, action_type='wait', input_value='1')
            TestSuiteTestCase.objects.create(test_suite=self.suite, test_case=case, order=index)
        self.task = self.make_task('TEST_CASE')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        # 浏览器与通知使用替身，只验证真实的记录持久化和汇总流程。
        self.ready = patch(
            'apps.ui_automation.playwright_engine.PlaywrightTestEngine.check_execution_environment_sync',
            return_value=(True, ''),
        )
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.notify = patch('apps.ui_automation.views.UiScheduledTaskViewSet._send_task_notification')
        self.notify.start()
        self.addCleanup(self.notify.stop)

    def make_task(self, task_type, **extra):
        return UiScheduledTask.objects.create(
            name=f'{task_type} task', task_type=task_type, trigger_type='INTERVAL',
            interval_seconds=600, next_run_time=timezone.now() - timedelta(seconds=1),
            project=self.project, created_by=self.user, engine='playwright', headless=True,
            test_suite=self.suite if task_type == 'TEST_SUITE' else None,
            test_cases=[case.id for case in reversed(self.cases)] if task_type == 'TEST_CASE' else [],
            **extra,
        )

    def start(self, task=None):
        with patch.object(scheduled_tasks, '_launch_task') as launch, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f'/api/ui-automation/scheduled-tasks/{(task or self.task).id}/run_now/')
        self.assertEqual(response.status_code, 200, response.data)
        return launch.call_args.args

    def case_result(self, case, **kwargs):
        """结果由 run_single_case 的真实持久化逻辑写入，而不是测试自行保存。"""
        return {
            'test_case_id': case['id'], 'test_case_name': case['name'],
            'status': 'passed', 'steps': [{'step_number': 1, 'success': True}],
            'screenshots': [{'url': '/screenshot.png'}], 'error': '',
        }

    def test_manual_task_precreates_records_and_preserves_order(self):
        task_id, suite_id, execution_ids, config = self.start()
        self.assertIsNone(suite_id)
        records = [TestCaseExecution.objects.get(pk=pk) for pk in execution_ids]
        self.assertEqual([record.test_case_id for record in records], [case.id for case in reversed(self.cases)])
        self.assertTrue(all(record.scheduled_task_id == task_id for record in records))
        self.assertTrue(all(record.status == 'pending' for record in records))
        self.assertTrue(all(record.test_suite_id is None for record in records))
        self.assertEqual(TestSuite.objects.count(), 1)
        with patch('apps.ui_automation.test_executor._run_case_playwright',
                   side_effect=lambda executor, case: self.case_result(case)):
            scheduled_tasks._run_task(task_id, suite_id, execution_ids, config)
        records = TestCaseExecution.objects.filter(pk__in=execution_ids)
        self.assertEqual(records.filter(status='passed').count(), 2)
        self.assertTrue(all(record.finished_at and record.execution_logs and record.screenshots for record in records))
        self.task.refresh_from_db()
        self.assertEqual(self.task.successful_runs, 1)
        self.assertEqual(self.task.total_runs, 1)

    def test_automatic_scheduler_creates_persistent_records_and_does_not_reclaim(self):
        suite_task = self.make_task('TEST_SUITE')
        command = Command(stdout=io.StringIO())
        with patch.object(scheduled_tasks, '_launch_task') as launch, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(command.schedule_ui_tasks(), 2)
            self.assertEqual(command.schedule_ui_tasks(), 0)
        self.assertEqual(launch.call_count, 2)
        self.assertEqual({call.args[0] for call in launch.call_args_list}, {self.task.id, suite_task.id})
        self.assertEqual(TestExecution.objects.filter(scheduled_task=suite_task).count(), 1)
        self.assertEqual(TestCaseExecution.objects.filter(scheduled_task=self.task).count(), 2)
        self.assertEqual(TestSuite.objects.count(), 1)
        self.assertIsNone(scheduled_tasks.start_scheduled_task(self.task.id, automatic=True))

    def test_case_exception_continues_batch_and_counts_failure(self):
        args = self.start()
        with patch.object(scheduled_tasks, 'run_single_case', side_effect=[
            RuntimeError('浏览器启动失败'), RuntimeError('元素超时'),
        ]) as runner:
            scheduled_tasks._run_task(*args)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(TestCaseExecution.objects.filter(scheduled_task=self.task, status='failed').count(), 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.failed_runs, 1)
        self.assertEqual(self.task.successful_runs, 0)
        self.assertEqual(self.task.last_result['status'], 'failed')

    def test_partial_case_failure_is_persisted_and_not_counted_as_success(self):
        args = self.start()

        def run_case(executor, case):
            result = self.case_result(case)
            if case['id'] == self.cases[0].id:
                result.update(status='failed', error='断言失败')
            return result

        with patch('apps.ui_automation.test_executor._run_case_playwright', side_effect=run_case):
            scheduled_tasks._run_task(*args)
        self.task.refresh_from_db()
        self.assertEqual(self.task.failed_runs, 1)
        self.assertEqual(self.task.last_result['status'], 'partial')
        self.assertEqual(self.task.last_result['success_count'], 1)
        self.assertEqual(self.task.last_result['failed_count'], 1)

    def test_passed_case_task_marks_draft_cases_ready(self):
        args = self.start()
        with patch('apps.ui_automation.test_executor._run_case_playwright',
                   side_effect=lambda executor, case: self.case_result(case)):
            scheduled_tasks._run_task(*args)
        self.assertEqual(set(UiTestCase.objects.values_list('status', flat=True)), {'ready'})

    def test_case_without_steps_is_failed_without_running_and_batch_continues(self):
        """零步骤会被执行器判为通过：该用例不启动浏览器直接记失败，后面的用例照常执行。"""
        runnable, empty = self.cases  # 任务按逆序执行，空用例排在前面
        TestCaseStep.objects.filter(test_case=empty).delete()
        args = self.start()
        with patch('apps.ui_automation.test_executor._run_case_playwright',
                   side_effect=lambda executor, case: self.case_result(case)) as browser_run:
            scheduled_tasks._run_task(*args)
        self.assertEqual([call.args[1]['id'] for call in browser_run.call_args_list], [runnable.id])
        record = TestCaseExecution.objects.get(scheduled_task=self.task, test_case=empty)
        self.assertEqual(record.status, 'failed')
        self.assertIn('没有定义任何步骤', record.error_message)
        self.assertIsNone(record.started_at)
        self.assertIsNotNone(record.finished_at)
        self.assertEqual(
            TestCaseExecution.objects.get(scheduled_task=self.task, test_case=runnable).status, 'passed')
        self.task.refresh_from_db()
        self.assertEqual(self.task.last_result['status'], 'partial')
        self.assertEqual(self.task.last_result['success_count'], 1)
        self.assertEqual(self.task.last_result['failed_count'], 1)

    def test_passed_suite_run_keeps_case_status(self):
        """套件执行不经过单用例入口，通过后用例状态保持不变。"""
        parent = TestExecution.objects.create(
            project=self.project, test_suite=self.suite, executed_by=self.user,
            engine='playwright', status='RUNNING')
        executor = TestExecutor(
            test_suite=self.suite, engine='playwright', executed_by=self.user, execution=parent)
        executor.get_test_cases()
        with patch('apps.ui_automation.test_executor.sync_playwright', return_value=MagicMock()), \
                patch.object(executor, 'execute_test_case_playwright_no_db', side_effect=self.case_result):
            executor.run_with_playwright()
        self.assertEqual(parent.case_executions.filter(status='passed').count(), 2)
        self.assertEqual(set(UiTestCase.objects.values_list('status', flat=True)), {'draft'})

    def test_thread_start_failure_finishes_prebuilt_records(self):
        with patch.object(scheduled_tasks.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('无法启动线程')
            with self.captureOnCommitCallbacks(execute=True):
                scheduled_tasks.start_scheduled_task(self.task.id)
        self.assertEqual(TestCaseExecution.objects.filter(
            scheduled_task=self.task, status='failed', error_message='无法启动线程').count(), 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.failed_runs, 1)

    def test_suite_task_uses_actual_execution_status_and_keeps_parent(self):
        task = self.make_task('TEST_SUITE')
        args = self.start(task)
        parent = TestExecution.objects.get(pk=args[1])
        self.assertEqual(parent.scheduled_task_id, task.id)

        def fail_suite(executor):
            executor.update_execution_result('FAILED', failed=2, error_msg='断言失败')

        with patch.object(TestExecutor, 'run', fail_suite):
            scheduled_tasks._run_task(*args)
        task.refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(task.failed_runs, 1)
        self.assertEqual(task.successful_runs, 0)
        self.assertEqual(parent.status, 'FAILED')
        self.assertEqual(task.last_result['suite_execution_id'], parent.id)

    def test_suite_children_inherit_task_in_both_engines(self):
        task = self.make_task('TEST_SUITE')
        for engine in ('playwright', 'selenium'):
            with self.subTest(engine=engine):
                parent = TestExecution.objects.create(
                    project=self.project, test_suite=self.suite, scheduled_task=task,
                    executed_by=self.user, engine=engine, status='RUNNING')
                executor = TestExecutor(
                    test_suite=self.suite, engine=engine, executed_by=self.user, execution=parent)
                executor.get_test_cases()
                with patch('apps.ui_automation.test_executor.sync_playwright', return_value=MagicMock()), \
                        patch.object(executor, 'create_selenium_driver', return_value=MagicMock()), \
                        patch.object(executor, 'execute_test_case_playwright_no_db', side_effect=self.case_result), \
                        patch.object(executor, 'execute_test_case_selenium_no_db', side_effect=lambda driver, case: self.case_result(case)):
                    if engine == 'playwright':
                        executor.run_with_playwright()
                    else:
                        executor.run_with_selenium()
                self.assertEqual(parent.case_executions.count(), 2)
                self.assertEqual(parent.case_executions.filter(
                    scheduled_task=task, execution_source='scheduled', status='passed').count(), 2)

    def test_task_filters_permissions_and_deletion_preserve_history(self):
        task = self.make_task('TEST_SUITE')
        parent = TestExecution.objects.create(
            project=self.project, test_suite=self.suite, scheduled_task=task)
        child = TestCaseExecution.objects.create(
            project=self.project, test_case=self.cases[0], test_suite=self.suite,
            suite_execution=parent, scheduled_task=task, created_by=self.user)
        unrelated = TestExecution.objects.create(project=self.project, test_suite=self.suite)
        for endpoint, record in [('test-executions', parent), ('test-case-executions', child)]:
            url = f'/api/ui-automation/{endpoint}/'
            response = self.client.get(url, {'scheduled_task': task.id})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['id'] for row in response.data['results']], [record.id])
            self.assertEqual(response.data['results'][0]['scheduled_task_name'], task.name)
            self.assertEqual(self.client.get(f'{url}{record.id}/').data['scheduled_task'], task.id)
            self.client.force_authenticate(get_user_model().objects.create_user(username=f'other-{endpoint}'))
            self.assertEqual(self.client.get(url, {'scheduled_task': task.id}).data['results'], [])
            self.assertEqual(self.client.get(f'{url}{record.id}/').status_code, 404)
            self.client.force_authenticate(self.user)
        self.assertTrue(TestExecution.objects.filter(pk=unrelated.id).exists())
        task.delete()
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNone(parent.scheduled_task_id)
        self.assertIsNone(child.scheduled_task_id)
        self.assertEqual(child.suite_execution_id, parent.id)

    def test_invalid_configuration_does_not_increment_runs(self):
        self.task.test_cases = [999999]
        self.task.save()
        response = self.client.post(f'/api/ui-automation/scheduled-tasks/{self.task.id}/run_now/')
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.total_runs, 0)
        self.assertFalse(TestCaseExecution.objects.exists())
