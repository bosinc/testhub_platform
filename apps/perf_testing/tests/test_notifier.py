"""压测结果通知的 DB-free 单测（mock 掉渠道查询与发送）。

覆盖两点：
- 发送失败（send_via_channel 返回 (False, 原因)）必须如实记为失败；
- 可选行（压力机 CPU、未达标项、错误信息）只在有数据时出现，未达标项最多列 5 项。
"""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.test_settings')
import django  # noqa: E402

django.setup()

from apps.monitor.models import NotificationChannel  # noqa: E402
from apps.perf_testing.services import notifier  # noqa: E402


def _execution(**overrides):
    data = dict(
        scenario=SimpleNamespace(name='下单压测'),
        STATUS_CHOICES=[('COMPLETED', '已完成')],
        status='COMPLETED', sla_result='PASSED', execution_no='PERF-001', duration=60,
        summary={'total_requests': 1200, 'tps': 35.5, 'peak_tps': 60, 'avg_rt': 120,
                 'p95_rt': 300, 'error_rate': 0.5},
        sla_detail=[], error_message='',
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _failed_item(label):
    return {'label': label, 'actual': 800, 'comparator': '<=', 'threshold': 500, 'passed': False}


class TestSendExecutionNotification(unittest.TestCase):

    def _send(self, **send_mock):
        channel = mock.MagicMock()
        channel.name = '企微'
        with mock.patch.object(NotificationChannel.objects, 'filter', return_value=[channel]), \
                mock.patch('apps.monitor.utils.notifiers.send_via_channel', **send_mock):
            return notifier.send_execution_notification(_execution(), [1])

    def test_success_is_recorded(self):
        self.assertEqual(self._send(return_value=(True, 'ok')), [{'channel': '企微', 'success': True}])

    def test_failed_send_is_recorded_as_failure(self):
        """渠道返回 (False, 原因) 时不能记成成功"""
        results = self._send(return_value=(False, 'HTTP 500'))
        self.assertEqual(results, [{'channel': '企微', 'success': False, 'error': 'HTTP 500'}])

    def test_exception_is_recorded_as_failure(self):
        results = self._send(side_effect=RuntimeError('boom'))
        self.assertEqual(results, [{'channel': '企微', 'success': False, 'error': 'boom'}])


class TestBuildNotification(unittest.TestCase):

    def test_optional_lines_only_appear_with_data(self):
        plain = notifier.build_notification(_execution()).body()
        full = notifier.build_notification(_execution(
            error_message='连接超时',
            sla_detail=[_failed_item('登录接口耗时')],
            summary={'data_trustworthy': False, 'peak_load_gen_cpu': 97.5},
        )).body()
        for text in ('连接超时', '登录接口耗时', '97.5'):
            self.assertIn(text, full)
            self.assertNotIn(text, plain)

    def test_failed_items_are_capped_at_five(self):
        items = [_failed_item(f'指标{i}') for i in range(7)]
        body = notifier.build_notification(_execution(sla_detail=items)).body()
        self.assertIn('指标4', body)
        self.assertNotIn('指标5', body)
