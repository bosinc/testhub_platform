"""定时任务通知文案渲染测试。

覆盖：通过率与失败用例列表、运行报告链接（套件任务指向本次报告，用例任务指向执行记录列表）、
任务本身出错时的错误信息（Webhook 摘要截断，邮件保留全文）。
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.ui_automation.models import (
    TestCase as UiTestCase, TestCaseExecution, TestExecution, TestSuite, UiProject, UiScheduledTask,
)
from apps.ui_automation.views import UiScheduledTaskViewSet

BASE_URL = 'https://testhub.example.com'


@override_settings(TESTHUB_BASE_URL=BASE_URL)
class TaskNotificationRenderTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='notify-owner')
        self.project = UiProject.objects.create(name='UI', owner=self.user)
        self.suite = TestSuite.objects.create(name='Suite', project=self.project)

    def make_task(self, task_type, **extra):
        return UiScheduledTask.objects.create(
            name='Daily UI automation', task_type=task_type, trigger_type='INTERVAL',
            interval_seconds=600, project=self.project, created_by=self.user,
            test_suite=self.suite if task_type == 'TEST_SUITE' else None, **extra,
        )

    def make_case_executions(self, task, statuses, suite_execution=None, name='Case {index}'):
        executions = []
        for index, status in enumerate(statuses):
            case = UiTestCase.objects.create(
                name=name.format(index=index), project=self.project, created_by=self.user)
            executions.append(TestCaseExecution.objects.create(
                test_case=case, project=self.project, scheduled_task=task, status=status,
                suite_execution=suite_execution, created_by=self.user,
            ))
        return executions

    def render_case_task(self, statuses, **kwargs):
        """用例任务跑完后的通知，last_result 与 scheduled_tasks._run_task 写入的结构一致"""
        task = self.make_task('TEST_CASE')
        executions = self.make_case_executions(task, statuses, **kwargs)
        passed = statuses.count('passed')
        failed = len(statuses) - passed
        task.last_result = {
            'status': 'success' if not failed else 'partial', 'suite_execution_id': None,
            'case_execution_ids': [execution.id for execution in executions],
            'success_count': passed, 'failed_count': failed, 'test_case_count': len(statuses),
        }
        task.error_message = f'{failed}个测试用例执行失败' if failed else ''
        return task, UiScheduledTaskViewSet()._render_task_notification(task, not failed)

    def render_suite_task(self, statuses, error=''):
        task = self.make_task('TEST_SUITE')
        passed = statuses.count('passed')
        failed = len(statuses) - passed
        execution = TestExecution.objects.create(
            project=self.project, test_suite=self.suite, scheduled_task=task,
            status='FAILED' if failed else 'SUCCESS', error_message=error,
            total_cases=len(statuses), passed_cases=passed, failed_cases=failed,
        )
        self.make_case_executions(task, statuses, suite_execution=execution)
        task.last_result = {
            'status': 'failed' if failed else 'success', 'suite_execution_id': execution.id,
            'case_execution_ids': [], 'success_count': passed, 'failed_count': failed,
            'test_case_count': len(statuses),
        }
        task.error_message = error or ('套件执行失败' if failed else '')
        return task, execution, UiScheduledTaskViewSet()._render_task_notification(task, not failed)

    def render_broken_task(self, error):
        """任务本身出错：没有任何用例结果"""
        task = self.make_task('TEST_CASE')
        task.last_result = {'status': 'failed', 'error': error}
        task.error_message = error
        return UiScheduledTaskViewSet()._render_task_notification(task, False)

    def test_title_and_subject(self):
        _, notification = self.render_case_task(['passed'])
        self.assertEqual(notification.title, '**【Testhub-UI Automation 每日报告】**')
        self.assertEqual(notification.plain_title, '【Testhub-UI Automation 每日报告】')
        self.assertEqual(notification.bold_title('*'), '*【Testhub-UI Automation 每日报告】*')
        self.assertEqual(notification.email_subject, '【Testhub-UI Automation 每日报告】Daily UI automation')

    def test_status_is_bold(self):
        _, notification = self.render_case_task(['passed'])
        self.assertIn('执行状态: **已完成**', notification.lines)
        self.assertIn('执行状态: **已完成**', notification.email_lines)

    def test_email_html_converts_bold_markers(self):
        _, notification = self.render_case_task(['passed'])
        html = UiScheduledTaskViewSet._email_html_body(notification.email_body())
        self.assertIn('<strong>已完成</strong>', html)
        self.assertNotIn('**', html)

    def test_case_task_lists_failed_cases_and_links_to_execution_list(self):
        task, notification = self.render_case_task(['passed', 'failed', 'error'])
        self.assertEqual(notification.lines, (
            '任务名称: Daily UI automation',
            '执行状态: **已完成**',
            '通过率: 33.3%（1/3）· 失败 2 条',
            '失败用例:\n• Case 1\n• Case 2',
            f'运行报告: {BASE_URL}/ui-automation/executions'
            f'?project={self.project.id}&scheduled_task={task.id}&task_name=Daily+UI+automation',
        ))
        # 邮件正文与 Webhook 正文一致
        self.assertEqual(notification.email_lines, notification.lines)

    def test_suite_task_links_to_this_report(self):
        task, execution, notification = self.render_suite_task(['passed', 'failed'])
        self.assertEqual(notification.lines, (
            '任务名称: Daily UI automation',
            '执行状态: **已完成**',
            '通过率: 50.0%（1/2）· 失败 1 条',
            '失败用例:\n• Case 1',
            f'运行报告: {BASE_URL}/ui-automation/reports/{execution.id}'
            f'?project={self.project.id}&scheduled_task={task.id}&task_name=Daily+UI+automation',
        ))

    def test_only_failed_cases_of_this_run_are_listed(self):
        """同一任务其他运行的失败用例不能混进本次通知"""
        task, _ = self.render_case_task(['passed'])
        self.make_case_executions(task, ['failed'], name='Other run case {index}')
        notification = UiScheduledTaskViewSet()._render_task_notification(task, True)
        self.assertNotIn('失败用例', notification.body())

    def test_all_passed_has_no_failed_cases_or_error_line(self):
        _, notification = self.render_case_task(['passed', 'passed'])
        self.assertIn('通过率: 100.0%（2/2）· 失败 0 条', notification.lines)
        self.assertNotIn('失败用例', notification.body())
        self.assertNotIn('错误信息', notification.body())

    def test_failed_cases_are_capped_and_long_names_truncated(self):
        _, notification = self.render_case_task(['failed'] * 12, name='N{index}-' + 'x' * 100)
        failed_cases = notification.lines[3].split('\n')
        self.assertEqual(len(failed_cases), 12)  # 标题行 + 10 条 + 说明行
        self.assertEqual(failed_cases[1], '• ' + ('N0-' + 'x' * 100)[:80] + '...')
        self.assertEqual(failed_cases[-1], '… 仅列出前 10 条，共 12 条')

    def test_suite_error_is_shown_with_results(self):
        """套件自身的错误（如浏览器环境异常）要展示，用例失败的汇总文案不展示"""
        _, _, with_error = self.render_suite_task(['failed', 'failed'], error='浏览器启动失败')
        self.assertIn('错误信息: 浏览器启动失败', with_error.lines)
        self.assertIn('错误信息: 浏览器启动失败', with_error.email_lines)
        _, _, without_error = self.render_suite_task(['passed', 'failed'])
        self.assertNotIn('错误信息', without_error.body())

    def test_broken_task_shows_error_instead_of_pass_rate(self):
        notification = self.render_broken_task('登录态不存在')
        self.assertEqual(notification.lines[:3], (
            '任务名称: Daily UI automation', '执行状态: **执行异常**', '错误信息: 登录态不存在'))
        self.assertNotIn('通过率', notification.body())

    def test_long_error_is_truncated_in_webhook_only(self):
        error = '浏览器启动失败: ' + 'x' * 500
        notification = self.render_broken_task(error)
        self.assertIn(error[:200] + '...', notification.body())
        self.assertNotIn(error, notification.body())
        self.assertIn(error, notification.email_body())

    def test_suite_task_without_execution_links_to_report_list(self):
        """线程没启动起来时结果里没有执行记录 ID，退回到该任务的报告列表"""
        task = self.make_task('TEST_SUITE')
        task.last_result = {'status': 'failed', 'error': '线程启动失败'}
        task.error_message = '线程启动失败'
        notification = UiScheduledTaskViewSet()._render_task_notification(task, False)
        self.assertEqual(
            notification.lines[-1],
            f'运行报告: {BASE_URL}/ui-automation/reports'
            f'?project={self.project.id}&scheduled_task={task.id}&task_name=Daily+UI+automation')

    @override_settings(TESTHUB_BASE_URL='')
    def test_no_detail_line_without_base_url(self):
        _, notification = self.render_case_task(['passed'])
        self.assertNotIn('运行报告', notification.body())
        self.assertNotIn('运行报告', notification.email_body())
