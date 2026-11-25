"""
通知文案模板

所有通知（Webhook 机器人、邮件、监控渠道）的文案都集中在本文件，
业务模块只负责准备数据并调用 render_notification()，不再各自拼接文字。
修改文案只需要改这里的 TEMPLATES / TEXTS。

模板语法（正文按行处理）：
- {变量}           变量值为空（None 或空字符串）时，该行整行不输出，用于可选行
- {变量|默认文案}   变量值为空时显示默认文案；默认文案可以留空，如 {变量|}
标题和词条只有一行，变量为空时按空字符串处理，不会被丢弃。
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationTemplate:
    """一个通知场景的文案模板，用不到的字段留空即可"""
    title: str = ''          # 标题：Webhook 卡片标题 / 正文加粗首行
    body: str = ''           # 正文
    email_subject: str = ''  # 邮件标题
    email_body: str = ''     # 邮件正文（与 body 排版不同时才需要）


TEMPLATES = {
    # 接口测试 - 定时任务执行结果
    # 可用变量：task_name 任务名称、status_text 成功/失败、run_time 执行时间、task_type 任务类型、
    #          summary 执行概要、error_message 错误信息
    'api_testing.scheduled_task': NotificationTemplate(
        title='定时任务执行{status_text}',
        body='''\
任务名称: {task_name}
执行状态: {status_text}
执行时间: {run_time}
任务类型: {task_type}
''',
        email_subject='定时任务执行{status_text}: {task_name}',
        email_body='''\
任务名称: {task_name}
执行状态: {status_text}
执行时间: {run_time}
任务类型: {task_type}

执行概要:
{summary|无详细信息}

错误信息:
{error_message|无错误信息}
''',
    ),

    # UI 自动化 - 定时任务执行结果
    # 可用变量：task_name、status_text 已完成/执行异常、pass_rate 通过率（如 89.7%）、
    #          success_count / total_count / failed_count 通过、总、失败用例数、
    #          failed_cases 失败用例（标题行加逐条用例名）、detail_url 运行报告链接、
    #          error_message 错误信息全文、error_summary 错误信息摘要（过长时截断，用于 Webhook）
    # 标题和执行状态用 ** 标记加粗，各发送渠道负责转成对应平台的加粗语法
    'ui_automation.scheduled_task': NotificationTemplate(
        title='**【Testhub-UI Automation 每日报告】**',
        body='''\
任务名称: {task_name}
执行状态: **{status_text}**
通过率: {pass_rate}（{success_count}/{total_count}）· 失败 {failed_count} 条
{failed_cases}
错误信息: {error_summary}
运行报告: {detail_url}
''',
        email_subject='【Testhub-UI Automation 每日报告】{task_name}',
        email_body='''\
任务名称: {task_name}
执行状态: **{status_text}**
通过率: {pass_rate}（{success_count}/{total_count}）· 失败 {failed_count} 条
{failed_cases}
错误信息: {error_message}
运行报告: {detail_url}
''',
    ),

    # APP 自动化 - 定时任务执行结果（邮件正文与 Webhook 正文相同）
    # 可用变量：task_name、status_text、run_time、task_type、device_name 执行设备、result_message 执行结果
    'app_automation.scheduled_task': NotificationTemplate(
        title='APP自动化定时任务执行{status_text}',
        body='''\
任务名称: {task_name}
执行状态: {status_text}
执行时间: {run_time|未知}
任务类型: {task_type}
执行设备: {device_name|未指定}
执行结果: {result_message}
''',
        email_subject='APP自动化定时任务执行{status_text}: {task_name}',
    ),

    # 监控中心 - 告警
    # 可用变量：check_time 监控时间、target_name 接口名称、target_type 监测类型、target_url 请求地址、
    #          consecutive_failures 连续失败次数、error_message 错误信息、response_body 响应内容
    'monitor.alert': NotificationTemplate(
        body='''\
🔴 告警：
-----> 接口监控异常 <-----

【监控时间】：{check_time}
【接口名称】：{target_name}
【监测类型】：{target_type}
【请求地址】：{target_url|-}
【失败次数】：第 {consecutive_failures} 次连续失败
【错误信息】：{error_message|-}
【响应内容】：{response_body|-}
''',
        email_subject='【监控中心】告警：{target_name}',
    ),

    # 监控中心 - 恢复
    # 可用变量：check_time、target_name、target_type、target_url、duration 异常时长
    'monitor.recovered': NotificationTemplate(
        body='''\
🟢 恢复：
-----> 接口已恢复 <-----

【监控时间】：{check_time}
【接口名称】：{target_name}
【监测类型】：{target_type}
【请求地址】：{target_url|-}
【异常时长】：{duration|}
''',
        email_subject='【监控中心】已恢复：{target_name}',
    ),

    # 监控中心 - 通知渠道测试消息
    'monitor.channel_test': NotificationTemplate(
        body='【监控中心】这是一条测试消息',
        email_subject='监控中心测试',
    ),

    # 性能测试 - 定时压测结果
    # 可用变量：scenario_name 场景名称、execution_no 执行编号、status_text 执行状态、sla_text SLA 判定、
    #          sla_result SLA 结果原值、total_requests、tps、peak_tps、avg_rt、p95_rt、error_rate、
    #          duration 执行时长、peak_load_gen_cpu 压力机 CPU 峰值、failed_items 未达标项、error_message
    'perf_testing.execution_result': NotificationTemplate(
        body='''\
【性能测试】{scenario_name}
执行编号：{execution_no}
执行状态：{status_text}
SLA 判定：{sla_text}
总请求数：{total_requests}
TPS：{tps}（峰值 {peak_tps}）
平均响应：{avg_rt} ms  P95：{p95_rt} ms
错误率：{error_rate}%
执行时长：{duration} s
⚠️ 压力机 CPU 峰值 {peak_load_gen_cpu}%，数据可信度存疑
{failed_items}
错误信息：{error_message}
''',
        email_subject='性能测试结果 - {scenario_name} - {sla_result}',
    ),

    # MCP - 危险操作待人工审批
    # 可用变量：username 发起人、tool_name 操作、preview 影响、expires_at 过期时间
    'mcp.pending_approval': NotificationTemplate(
        body='''\
【TestHub MCP 危险操作待审批】
发起人：{username}
操作：{tool_name}
影响：{preview|}
过期时间：{expires_at}
请到「MCP 控制台 → 待确认」批准或拒绝。
''',
        email_subject='MCP 危险操作待审批',
    ),
}


# 词条：填进上面模板变量里的短文案
TEXTS = {
    # 执行状态，对应 {status_text}
    'common.status.success': '成功',
    'common.status.failed': '失败',

    # UI 自动化执行状态，对应 {status_text}：有用例结果为已完成，任务本身出错没有结果为执行异常
    'ui_automation.run_status.completed': '已完成',
    'ui_automation.run_status.error': '执行异常',

    # UI 自动化失败用例：标题行加逐条用例名，超出条数上限时加一行说明，整体对应 {failed_cases}
    'ui_automation.failed_cases.header': '失败用例:',
    'ui_automation.failed_cases.item': '• {name}',
    'ui_automation.failed_cases.more': '… 仅列出前 {shown} 条，共 {total} 条',

    # 监控恢复通知的异常时长，对应 {duration}
    'monitor.duration.under_minute': '不足 1 分钟',
    'monitor.duration.minutes': '约 {minutes} 分钟',
    'monitor.duration.hours': '约 {hours} 小时 {minutes} 分钟',

    # 性能测试 SLA 判定，对应 {sla_text}
    'perf_testing.sla.passed': '✅ 通过',
    'perf_testing.sla.failed': '❌ 未通过',
    'perf_testing.sla.not_evaluated': '➖ 未评估',

    # 性能测试未达标项：标题行加逐项明细，整体对应 {failed_items}
    'perf_testing.sla_failed_items.header': '未达标项：',
    'perf_testing.sla_failed_items.item': '  · {label}：实测 {actual} {comparator} 阈值 {threshold}',
}


@dataclass(frozen=True)
class RenderedNotification:
    """渲染结果。正文按行保存，行分隔符由调用方按平台决定（如 markdown 需要空行才换行）"""
    title: str
    email_subject: str
    lines: tuple
    email_lines: tuple

    def body(self, sep='\n'):
        return sep.join(self.lines)

    def email_body(self, sep='\n'):
        return sep.join(self.email_lines)

    @property
    def plain_title(self):
        """不带加粗标记的标题，用于邮件主题、卡片标题等纯文本位置"""
        return self.title[2:-2] if self.title.startswith('**') and self.title.endswith('**') else self.title

    def bold_title(self, marker='**'):
        """返回加粗标题；模板已写 ** 时先去掉，避免重复包裹。Slack 用单星号"""
        return f'{marker}{self.plain_title}{marker}'


_PLACEHOLDER = re.compile(r'\{(\w+)(?:\|([^{}]*))?\}')


def _render_line(line, context, optional):
    """渲染一行。optional=True 且有变量为空又没给默认文案时返回 None，表示该行不输出"""
    dropped = False

    def replace(match):
        nonlocal dropped
        name, default = match.group(1), match.group(2)
        value = context.get(name)
        if value is not None and value != '':
            return str(value)
        if default is None:
            dropped = True
            return ''
        return default

    rendered = _PLACEHOLDER.sub(replace, line)
    return None if optional and dropped else rendered


def _render_lines(text, context):
    if not text:
        return ()
    lines = (_render_line(line, context, optional=True) for line in text.strip('\n').split('\n'))
    return tuple(line for line in lines if line is not None)


def render_notification(key, /, **context):
    """按场景渲染通知文案，context 为模板变量"""
    template = TEMPLATES[key]
    return RenderedNotification(
        title=_render_line(template.title, context, optional=False),
        email_subject=_render_line(template.email_subject, context, optional=False),
        lines=_render_lines(template.body, context),
        email_lines=_render_lines(template.email_body, context),
    )


def render_text(key, /, **context):
    """渲染 TEXTS 里的词条"""
    return _render_line(TEXTS[key], context, optional=False)


def status_text(success):
    """执行状态词条：成功 / 失败"""
    return render_text('common.status.success' if success else 'common.status.failed')
