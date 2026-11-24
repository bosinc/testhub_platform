"""通知文案模板单元测试（纯函数，不依赖 DB）。

- 模板语法规则用测试内的临时模板验证，不依赖线上文案；
- 线上模板只校验用到的变量都在调用方提供的范围内，改文案不需要改测试，
  但模板里写错变量名会在这里暴露（否则该行会被静默丢弃）。
"""
import re
import unittest
from unittest import mock

from apps.core import notification_templates
from apps.core.notification_templates import (
    NotificationTemplate, render_notification, render_text, status_text,
)

# 各场景调用方传入的全部变量，需与业务模块里 render_notification() 的调用保持一致
TEMPLATE_VARIABLES = {
    'api_testing.scheduled_task': {
        'task_name', 'status_text', 'run_time', 'task_type', 'summary', 'error_message',
    },
    'ui_automation.scheduled_task': {
        'task_name', 'status_text', 'run_time', 'task_type', 'engine', 'browser',
        'result_message', 'success_count', 'failed_count', 'error_message', 'error_summary',
    },
    'app_automation.scheduled_task': {
        'task_name', 'status_text', 'run_time', 'task_type', 'device_name', 'result_message',
    },
    'monitor.alert': {
        'check_time', 'target_name', 'target_type', 'target_url',
        'consecutive_failures', 'error_message', 'response_body',
    },
    'monitor.recovered': {'check_time', 'target_name', 'target_type', 'target_url', 'duration'},
    'monitor.channel_test': set(),
    'perf_testing.execution_result': {
        'scenario_name', 'execution_no', 'status_text', 'sla_text', 'sla_result',
        'total_requests', 'tps', 'peak_tps', 'avg_rt', 'p95_rt', 'error_rate',
        'duration', 'peak_load_gen_cpu', 'failed_items', 'error_message',
    },
    'mcp.pending_approval': {'username', 'tool_name', 'preview', 'expires_at'},
}

# 带变量的词条及其变量，其余词条不应包含变量
TEXT_VARIABLES = {
    'monitor.duration.minutes': {'minutes'},
    'monitor.duration.hours': {'hours', 'minutes'},
    'perf_testing.sla_failed_items.item': {'label', 'actual', 'comparator', 'threshold'},
}


def _used_variables(*texts):
    return set(re.findall(r'\{(\w+)', '\n'.join(texts)))


class RenderRuleTests(unittest.TestCase):
    """模板语法规则"""

    def render(self, template, **context):
        with mock.patch.dict(notification_templates.TEMPLATES, {'test.demo': template}):
            return render_notification('test.demo', **context)

    def test_variable_is_replaced(self):
        result = self.render(NotificationTemplate(body='任务: {name}'), name='回归')
        self.assertEqual(result.body(), '任务: 回归')

    def test_line_dropped_when_variable_empty(self):
        """变量为 None、空串或未提供时，整行不输出"""
        template = NotificationTemplate(body='第一行\n结果: {result}\n最后一行')
        for context in ({'result': None}, {'result': ''}, {}):
            with self.subTest(context=context):
                self.assertEqual(self.render(template, **context).lines, ('第一行', '最后一行'))

    def test_line_dropped_when_any_variable_empty(self):
        template = NotificationTemplate(body='成功: {passed} 个，失败: {failed} 个')
        self.assertEqual(self.render(template, passed=3).lines, ())

    def test_default_used_when_variable_empty(self):
        """有默认文案时不丢行，默认文案可以为空"""
        template = NotificationTemplate(body='错误: {error|无错误信息}\n时长: {duration|}')
        self.assertEqual(self.render(template).lines, ('错误: 无错误信息', '时长: '))
        self.assertEqual(
            self.render(template, error='超时', duration='3 分钟').lines,
            ('错误: 超时', '时长: 3 分钟'),
        )

    def test_zero_is_not_empty(self):
        template = NotificationTemplate(body='成功: {passed} 个，失败: {failed} 个')
        self.assertEqual(self.render(template, passed=0, failed=2).body(), '成功: 0 个，失败: 2 个')

    def test_title_and_subject_are_never_dropped(self):
        template = NotificationTemplate(title='执行{status}', email_subject='执行{status}: {name}')
        result = self.render(template, name='回归')
        self.assertEqual(result.title, '执行')
        self.assertEqual(result.email_subject, '执行: 回归')

    def test_blank_lines_and_multiline_values_are_kept(self):
        template = NotificationTemplate(body='概要:\n{summary}\n\n结束\n')
        result = self.render(template, summary='a: 1\nb: 2')
        self.assertEqual(result.body(), '概要:\na: 1\nb: 2\n\n结束')

    def test_value_with_braces_is_not_rendered_again(self):
        """变量值里的花括号原样输出，不会被当成模板变量"""
        template = NotificationTemplate(body='错误: {error}')
        result = self.render(template, error='{name} 未定义', name='x')
        self.assertEqual(result.body(), '错误: {name} 未定义')

    def test_body_separator(self):
        result = self.render(NotificationTemplate(body='a\nb', email_body='c\nd'))
        self.assertEqual(result.body('\n\n'), 'a\n\nb')
        self.assertEqual(result.email_body(), 'c\nd')

    def test_unused_sections_render_empty(self):
        result = self.render(NotificationTemplate(body='a'))
        self.assertEqual((result.title, result.email_subject, result.email_lines), ('', '', ()))


class RenderTextTests(unittest.TestCase):
    """词条渲染"""

    def test_render_text_with_variables(self):
        with mock.patch.dict(notification_templates.TEXTS, {'test.duration': '约 {hours} 小时 {minutes} 分钟'}):
            self.assertEqual(render_text('test.duration', hours=2, minutes=0), '约 2 小时 0 分钟')

    def test_status_text(self):
        self.assertEqual(status_text(True), notification_templates.TEXTS['common.status.success'])
        self.assertEqual(status_text(False), notification_templates.TEXTS['common.status.failed'])


class ProductionTemplateTests(unittest.TestCase):
    """线上模板自检：不校验具体文案，只防止写错变量名"""

    def test_every_template_declares_its_variables(self):
        self.assertEqual(set(TEMPLATE_VARIABLES), set(notification_templates.TEMPLATES))

    def test_templates_only_use_provided_variables(self):
        for key, template in notification_templates.TEMPLATES.items():
            with self.subTest(template=key):
                used = _used_variables(
                    template.title, template.body, template.email_subject, template.email_body)
                self.assertLessEqual(used, TEMPLATE_VARIABLES[key])

    def test_texts_only_use_provided_variables(self):
        for key, text in notification_templates.TEXTS.items():
            with self.subTest(text=key):
                self.assertLessEqual(_used_variables(text), TEXT_VARIABLES.get(key, set()))
