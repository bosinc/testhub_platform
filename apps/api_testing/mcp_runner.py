"""接口测试的 MCP 异步执行器（场景用例 / 测试套件）。

与 ``apps/ui_automation/mcp_runner.py`` 对称：两个模块都只负责
「预建执行记录 + 起后台线程 + 立即返回 ID」，执行体分别复用各自的 utils。

apps.mcp.actions 依赖本模块的 ``start_scenario_execution`` 与
``start_suite_execution``。与页面的同步执行不同，MCP 契约要求
「立即返回执行记录 ID → 后台执行 → Agent 轮询结果」：

- 场景用例执行复用 ``utils.execute_scenario_case``，执行体与页面一致
- 套件执行复用 ``utils.execute_test_suite``（传入预建记录），循环 / 汇总 /
  成功判定只有一份实现，本模块只负责建记录与起线程
"""
import logging
import threading

from django.db import connection
from django.utils import timezone

from .models import ApiSuiteExecution, TestExecution
from .utils import execute_scenario_case, execute_test_suite

logger = logging.getLogger(__name__)


def _resolve_environment(scenario_case, environment):
    """环境优先级：调用方指定 > 场景用例自身环境。"""
    return environment or scenario_case.environment


def _run_scenario_worker(execution, scenario_case, environment, user):
    """后台执行单个场景用例并落库（复用预建的 TestExecution）。"""
    try:
        result = execute_scenario_case(
            scenario_case, _resolve_environment(scenario_case, environment),
            user, execution=execution)
        if not result.get('success'):
            logger.warning('[ApiSuiteRunner] 场景用例执行失败 id=%s: %s',
                           scenario_case.id, result.get('error'))
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底，避免记录卡在 RUNNING
        logger.exception('[ApiSuiteRunner] 场景用例执行异常 id=%s', scenario_case.id)
        try:
            execution.status = 'FAILED'
            execution.end_time = timezone.now()
            execution.results = [{'passed': False, 'error': str(exc)}]
            execution.save(update_fields=['status', 'end_time', 'results'])
        except Exception:  # noqa: BLE001
            logger.exception('[ApiSuiteRunner] 执行记录落库失败 id=%s', execution.id)
    finally:
        connection.close()


def start_scenario_execution(scenario_case, user, environment=None):
    """异步启动单个场景用例执行，立即返回 TestExecution（status=RUNNING）。

    Args:
        scenario_case: ScenarioCase 实例（调用方已完成权限校验）
        user: 触发执行的用户
        environment: 可选执行环境，缺省使用场景用例自身环境

    Returns:
        TestExecution
    """
    enabled_count = scenario_case.scenariocaserequest_set.filter(enabled=True).count()
    execution = TestExecution.objects.create(
        scenario_case=scenario_case,
        status='RUNNING',
        start_time=timezone.now(),
        total_requests=enabled_count,
        executed_by=user,
    )
    thread = threading.Thread(
        target=_run_scenario_worker,
        args=(execution, scenario_case, environment, user),
        name=f'mcp-api-scenario-{execution.id}',
        daemon=False,
    )
    thread.start()
    logger.info('[ApiSuiteRunner] 场景用例 %s (id=%s) 已异步启动, execution_id=%s',
                scenario_case.name, scenario_case.id, execution.id)
    return execution


def _run_suite_worker(suite_execution, test_suite, environment, user):
    """后台执行套件：循环、进度汇总与成功判定统一收敛在 execute_test_suite。"""
    try:
        execute_test_suite(
            test_suite, environment, user, suite_execution=suite_execution)
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底，避免记录卡在 RUNNING
        logger.exception('[ApiSuiteRunner] 套件执行异常 suite_id=%s', test_suite.id)
        suite_execution.status = 'FAILED'
        suite_execution.error_message = str(exc)
        suite_execution.end_time = timezone.now()
        try:
            suite_execution.save()
        except Exception:  # noqa: BLE001
            logger.exception('[ApiSuiteRunner] 套件执行记录落库失败 id=%s', suite_execution.id)
    finally:
        connection.close()


def start_suite_execution(test_suite, user, environment=None):
    """异步启动测试套件执行，立即返回 ApiSuiteExecution（status=RUNNING）。

    Raises:
        ValueError: 套件内没有启用的场景用例
    """
    from .models import TestSuiteItem

    total_scenarios = TestSuiteItem.objects.filter(
        test_suite=test_suite, enabled=True).count()
    if not total_scenarios:
        raise ValueError(f'测试套件「{test_suite.name}」未包含任何启用的场景用例，无法执行')

    suite_execution = ApiSuiteExecution.objects.create(
        test_suite=test_suite,
        status='RUNNING',
        start_time=timezone.now(),
        total_scenarios=total_scenarios,
        executed_by=user,
    )
    thread = threading.Thread(
        target=_run_suite_worker,
        args=(suite_execution, test_suite, environment, user),
        name=f'mcp-api-suite-{suite_execution.id}',
        daemon=False,
    )
    thread.start()
    logger.info('[ApiSuiteRunner] 套件 %s (id=%s) 已异步启动, execution_id=%s',
                test_suite.name, test_suite.id, suite_execution.id)
    return suite_execution
