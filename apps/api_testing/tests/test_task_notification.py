"""定时任务通知文案渲染的单元测试（不依赖 DB）。

覆盖通知里的执行时间：数据库存的是 UTC，通知里要显示本地时间。
"""
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from django.utils import timezone

from ..views import ScheduledTaskViewSet


class TaskNotificationRenderTests(SimpleTestCase):

    def render(self):
        task = MagicMock()
        task.name = '回归任务'
        task.get_task_type_display.return_value = '测试套件执行'
        execution_log = SimpleNamespace(created_at=datetime(2026, 11, 23, 18, 30, 45, tzinfo=dt_timezone.utc))
        return ScheduledTaskViewSet()._render_task_notification(task, execution_log, True)

    def test_run_time_uses_local_timezone(self):
        """UTC 18:30 在东八区是次日 02:30，Webhook 正文和邮件正文都按本地时间显示"""
        with timezone.override('Asia/Shanghai'):
            notification = self.render()
        for text in (notification.body(), notification.email_body()):
            self.assertIn('2026-11-24 02:30:45', text)
            self.assertNotIn('18:30:45', text)
