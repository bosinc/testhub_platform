"""Django-Q2 tasks for API testing executions.

The queue worker claims a ``PENDING`` record atomically before running, so a
duplicated delivery cannot execute the same scenario twice. A record that is
still ``RUNNING`` without a fresh heartbeat is converged to ``FAILED`` instead of
staying in progress forever.
"""

import logging
from datetime import timedelta

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from .models import TestExecution
from .scenario_executor import execute_scenario_tree

logger = logging.getLogger(__name__)

STALE_HEARTBEAT = timedelta(minutes=10)


def run_scenario_execution(execution_id, environment_id=None):
    """Claim and run one queued scenario execution."""
    claimed = TestExecution.objects.filter(
        pk=execution_id, status='PENDING',
    ).update(status='RUNNING', start_time=timezone.now(), heartbeat_at=timezone.now())
    if not claimed:
        # Another worker already claimed it, or it was cancelled while queued.
        logger.warning('[ApiExecutor] 执行记录未被领取 execution_id=%s', execution_id)
        return {'execution_id': execution_id, 'skipped': True}

    execution = TestExecution.objects.select_related('scenario_case').get(pk=execution_id)
    scenario = execution.scenario_case
    try:
        environment = None
        if environment_id:
            from .environment_resolver import EnvironmentResolver

            environment = EnvironmentResolver.resolve(
                environment_id, user=execution.executed_by,
                project_id=scenario.project_id)
        else:
            environment = scenario.environment
        result = execute_scenario_tree(
            scenario, environment, execution.executed_by, execution=execution)
        if not result.get('success'):
            logger.warning('[ApiExecutor] 场景执行未通过 execution_id=%s: %s',
                           execution_id, result.get('error'))
        return {'execution_id': execution_id, 'success': result.get('success')}
    except Exception as exc:  # noqa: BLE001 - the record must leave RUNNING
        logger.exception('[ApiExecutor] 异步执行异常 execution_id=%s', execution_id)
        TestExecution.objects.filter(pk=execution_id).update(
            status='FAILED', conclusion='FAILED', end_time=timezone.now(),
            results=[{'status': 'ERROR', 'error': str(exc)}],
        )
        return {'execution_id': execution_id, 'success': False, 'error': str(exc)}
    finally:
        close_old_connections()


def sweep_stale_executions(older_than=STALE_HEARTBEAT):
    """Converge runs whose worker died; never re-run them automatically."""
    threshold = timezone.now() - older_than
    stale = TestExecution.objects.filter(status='RUNNING').filter(
        Q(heartbeat_at__lt=threshold)
        | Q(heartbeat_at__isnull=True, start_time__lt=threshold)
    )
    count = 0
    for execution in stale:
        execution.status = 'FAILED'
        execution.conclusion = 'FAILED'
        execution.end_time = timezone.now()
        execution.results = list(execution.results or []) + [{
            'status': 'ERROR', 'error': '执行进程失联，已标记为中断',
        }]
        execution.save(update_fields=['status', 'conclusion', 'end_time', 'results'])
        count += 1
    return count
