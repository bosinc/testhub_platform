"""定时任务通知文案渲染的单元测试（不依赖 DB）。

覆盖失败原因的展示：Webhook 正文带错误信息摘要（过长截断），邮件正文保留全文；
没有错误信息时不出现这一行。
"""
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from apps.ui_automation.views import UiScheduledTaskViewSet


class TaskNotificationRenderTests(SimpleTestCase):

    def render(self, success, error_message, last_result=None):
        task = MagicMock()
        task.name = 'UI夜间回归'
        task.task_type = 'TEST_CASE'
        task.engine = 'playwright'
        task.browser = 'chrome'
        task.last_run_time = None
        task.last_result = last_result or {}
        task.error_message = error_message
        return UiScheduledTaskViewSet()._render_task_notification(task, success)

    def test_webhook_and_email_show_failure_reason(self):
        notification = self.render(False, '2个测试用例执行失败', {'success_count': 1, 'failed_count': 2})
        self.assertIn('2个测试用例执行失败', notification.body())
        self.assertIn('2个测试用例执行失败', notification.email_body())

    def test_long_failure_reason_is_truncated_in_webhook_only(self):
        error = '浏览器启动失败: ' + 'x' * 500
        notification = self.render(False, error)
        self.assertIn(error[:200] + '...', notification.body())
        self.assertNotIn(error, notification.body())
        self.assertIn(error, notification.email_body())

    def test_no_error_line_without_error(self):
        with_error = self.render(False, '套件执行失败')
        without_error = self.render(True, '')
        self.assertEqual(len(with_error.lines) - len(without_error.lines), 1)
