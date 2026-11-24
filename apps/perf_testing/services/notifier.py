"""压测结果通知：复用 monitor 模块已有的钉钉/企微/邮件渠道，不重复造轮子。"""
import logging

from apps.core.notification_templates import render_notification, render_text

logger = logging.getLogger(__name__)

_SLA_TEXT_KEYS = {'PASSED': 'perf_testing.sla.passed', 'FAILED': 'perf_testing.sla.failed'}


def build_notification(execution):
    """渲染压测结果通知，文案模板见 apps/core/notification_templates.py"""
    summary = execution.summary or {}
    sla_text = render_text(_SLA_TEXT_KEYS.get(execution.sla_result, 'perf_testing.sla.not_evaluated'))
    status_text = dict(execution.STATUS_CHOICES).get(execution.status, execution.status)

    failed_items = [d for d in (execution.sla_detail or []) if not d.get('passed')]
    failed_lines = []
    if failed_items:
        failed_lines.append(render_text('perf_testing.sla_failed_items.header'))
        for item in failed_items[:5]:
            failed_lines.append(render_text(
                'perf_testing.sla_failed_items.item',
                label=item.get('label'), actual=item.get('actual'),
                comparator=item.get('comparator'), threshold=item.get('threshold'),
            ))

    return render_notification(
        'perf_testing.execution_result',
        scenario_name=execution.scenario.name,
        execution_no=execution.execution_no,
        status_text=status_text,
        sla_text=sla_text,
        sla_result=execution.sla_result,
        total_requests=summary.get('total_requests', 0),
        tps=summary.get('tps', 0),
        peak_tps=summary.get('peak_tps', 0),
        avg_rt=summary.get('avg_rt', 0),
        p95_rt=summary.get('p95_rt', 0),
        error_rate=summary.get('error_rate', 0),
        duration=execution.duration or 0,
        # 压力机 CPU 峰值这一行只在数据可信度存疑时展示
        peak_load_gen_cpu=None if summary.get('data_trustworthy', True)
        else str(summary.get('peak_load_gen_cpu')),
        failed_items='\n'.join(failed_lines),
        error_message=(execution.error_message or '')[:200],
    )


def send_execution_notification(execution, channel_ids):
    """按渠道 ID 列表发送压测结果。返回发送结果明细。"""
    if not channel_ids:
        return []

    try:
        from apps.monitor.models import NotificationChannel
        from apps.monitor.utils.notifiers import send_via_channel
    except ImportError as exc:
        logger.warning('通知模块不可用: %s', exc)
        return []

    notification = build_notification(execution)
    message = notification.body()
    subject = notification.email_subject
    results = []
    for channel in NotificationChannel.objects.filter(id__in=channel_ids, enabled=True):
        try:
            # send_via_channel 发送失败不抛异常，而是返回 (False, 原因)
            ok, detail = send_via_channel(channel, message, subject=subject)
            if ok:
                results.append({'channel': channel.name, 'success': True})
            else:
                logger.warning('渠道 %s 发送失败: %s', channel.name, detail)
                results.append({'channel': channel.name, 'success': False, 'error': detail})
        except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响其他渠道
            logger.warning('渠道 %s 发送失败: %s', channel.name, exc)
            results.append({'channel': channel.name, 'success': False, 'error': str(exc)})
    return results
