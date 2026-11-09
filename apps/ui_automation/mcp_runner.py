"""UI 自动化的 MCP 异步执行器（单用例 / 测试套件）。

apps.mcp.actions 依赖本模块的 ``start_case_execution`` 与
``start_suite_execution``。与页面的同步执行不同，MCP 契约要求
「立即返回执行记录 ID → 后台执行 → Agent 轮询结果」。

执行体本身不再由本模块实现，统一复用 ``test_executor``：

- 单用例：``test_executor.run_single_case``，与页面「运行用例」同一套步骤循环
  （含 storageState、navigate、urlContains 等待）
- 套件：``test_executor.TestExecutor``，与页面「执行套件」同一实现

本模块只负责「预建执行记录 + 起后台线程 + 立即返回 ID」。
"""
import logging
import threading

from django.db import connection
from django.utils import timezone

from .models import TestCaseExecution

logger = logging.getLogger(__name__)


def _worker_case(execution, case, engine, browser, headless):
    """后台执行单个用例；结果由 run_single_case 写入预建记录。"""
    from .test_executor import run_single_case

    try:
        run_single_case(
            case, engine=engine, browser=browser, headless=headless,
            executed_by=execution.created_by, execution=execution,
        )
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底，避免记录卡在 running
        logger.exception('[McpRunner] 用例执行异常 case_id=%s', case.id)
        try:
            execution.status = 'failed'
            execution.error_message = f'执行异常: {exc}'
            execution.finished_at = timezone.now()
            execution.save(update_fields=['status', 'error_message', 'finished_at'])
        except Exception:  # noqa: BLE001
            logger.exception('[McpRunner] 执行记录落库失败 execution_id=%s', execution.id)
    finally:
        connection.close()


def start_case_execution(case, user, engine='playwright', browser='chrome',
                         headless=True):
    """异步启动单个 UI 用例执行，立即返回 TestCaseExecution。

    Args:
        case: TestCase 实例（调用方已完成权限校验）
        user: 触发执行的用户
        engine: 'playwright' | 'selenium'
        browser: 浏览器类型（chrome/firefox/edge/safari）
        headless: 是否无头模式（MCP 服务端场景默认无头）

    Returns:
        TestCaseExecution（status='running'）

    Raises:
        ValueError: 执行环境检查失败（浏览器/驱动缺失等）或用例没有步骤
    """
    from .test_executor import check_engine_environment

    is_ready, error_msg = check_engine_environment(engine, browser)
    if not is_ready:
        raise ValueError(f'{engine} 执行环境检查失败: {error_msg}')

    if not case.steps.exists():
        raise ValueError(f"用例 '{case.name}' 没有定义任何步骤")

    execution = TestCaseExecution.objects.create(
        test_case=case,
        project=case.project,
        execution_source='manual',
        status='running',
        engine=engine,
        browser=browser,
        headless=headless,
        created_by=user,
        started_at=timezone.now(),
    )

    thread = threading.Thread(
        target=_worker_case,
        args=(execution, case, engine, browser, headless),
        name=f'mcp-ui-case-{execution.id}',
        daemon=False,
    )
    thread.start()
    logger.info('[McpRunner] 用例 %s (id=%s) 已异步启动, engine=%s, execution_id=%s',
                case.name, case.id, engine, execution.id)
    return execution


def _worker_suite(execution, suite, engine, browser, headless, storage_state_id, user):
    """后台执行 UI 套件（复用预建的 TestExecution 记录）。"""
    from .test_executor import TestExecutor

    try:
        executor = TestExecutor(
            test_suite=suite,
            engine=engine,
            browser=browser,
            headless=headless,
            executed_by=user,
            storage_state_id=storage_state_id,
            execution=execution,
        )
        executor.run()
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底，避免记录卡在 RUNNING
        logger.exception('[McpRunner] 套件执行异常 suite_id=%s', suite.id)
        try:
            execution.status = 'FAILED'
            execution.error_message = f'套件执行异常: {exc}'
            execution.finished_at = timezone.now()
            execution.save(update_fields=['status', 'error_message', 'finished_at'])
        except Exception:  # noqa: BLE001
            logger.exception('[McpRunner] 套件执行记录落库失败 execution_id=%s', execution.id)
    finally:
        connection.close()


def start_suite_execution(suite, user, engine='playwright', browser='chrome',
                          headless=True, storage_state_id=None):
    """异步启动 UI 测试套件执行，立即返回 TestExecution（status='RUNNING'）。

    与页面 ``TestSuiteViewSet.run_suite`` 一致：套件必须包含测试用例，
    执行前校验浏览器环境；结果落在 TestExecution.result_data，
    每个用例另有一条 TestCaseExecution 记录。

    Raises:
        ValueError: 套件为空或执行环境检查失败
    """
    from .models import StorageState, TestExecution
    from .test_executor import check_engine_environment

    if not suite.suite_test_cases.exists():
        raise ValueError(f'测试套件「{suite.name}」未包含任何测试用例，无法执行')

    if engine not in ('playwright', 'selenium'):
        raise ValueError(f'不支持的引擎: {engine}')

    if storage_state_id:
        if not StorageState.objects.filter(pk=storage_state_id, project=suite.project).exists():
            raise ValueError('登录态不存在或不属于当前套件项目')
    is_ready, error_msg = check_engine_environment(engine, browser)
    if engine == 'selenium':
        storage_state_id = None  # Selenium 引擎不支持登录态注入
    if not is_ready:
        raise ValueError(f'{engine} 执行环境检查失败: {error_msg}')

    execution = TestExecution.objects.create(
        project=suite.project,
        test_suite=suite,
        status='RUNNING',
        engine=engine,
        browser=browser,
        headless=headless,
        executed_by=user,
        started_at=timezone.now(),
        run_config={'storage_state_id': storage_state_id, 'trigger': 'mcp'},
    )
    suite.execution_status = 'running'
    suite.save(update_fields=['execution_status'])

    thread = threading.Thread(
        target=_worker_suite,
        args=(execution, suite, engine, browser, headless, storage_state_id, user),
        name=f'mcp-ui-suite-{execution.id}',
        daemon=False,
    )
    thread.start()
    logger.info('[McpRunner] 套件 %s (id=%s) 已异步启动, engine=%s, execution_id=%s',
                suite.name, suite.id, engine, execution.id)
    return execution
