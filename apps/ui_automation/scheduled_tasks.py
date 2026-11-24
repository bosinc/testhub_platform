"""UI 定时任务共用入口：先保存执行记录，再交给后台线程运行。"""
import logging
import threading

from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    StorageState, TestCase, TestCaseExecution, TestCaseStep, TestExecution,
    TestSuite, UiScheduledTask,
)
from .test_executor import TestExecutor, run_single_case

logger = logging.getLogger(__name__)


def start_scheduled_task(task_id, *, automatic=False):
    """领取一次运行；锁定到期时间，避免多个调度器重复领取同一个触发点。"""
    with transaction.atomic():
        task = UiScheduledTask.objects.select_for_update().get(pk=task_id)
        if automatic and not task.should_run_now():
            return None

        if task.task_type == 'TEST_SUITE':
            if not task.test_suite_id or task.test_suite.project_id != task.project_id:
                raise ValueError('该任务未配置有效的同项目测试套件')
            cases = list(task.test_suite.test_cases.all())
        elif task.task_type == 'TEST_CASE':
            # 按配置顺序运行，禁止把其他项目或已删除的用例混入执行记录。
            case_ids = list(dict.fromkeys(task.test_cases or []))
            case_map = {case.id: case for case in TestCase.objects.filter(
                id__in=case_ids, project_id=task.project_id)}
            if len(case_map) != len(case_ids):
                raise ValueError('配置的测试用例不存在或不属于任务关联项目')
            cases = [case_map[case_id] for case_id in case_ids]
        else:
            raise ValueError('不支持的任务类型')
        if not cases:
            raise ValueError('该任务未包含任何测试用例，无法执行')
        if task.engine not in ('playwright', 'selenium'):
            raise ValueError('不支持的执行引擎')
        if task.storage_state_id and not StorageState.objects.filter(
                pk=task.storage_state_id, project_id=task.project_id).exists():
            raise ValueError('登录态不存在或不属于任务关联项目')
        if task.engine == 'selenium' and (task.storage_state_id or TestCaseStep.objects.filter(
                test_case__in=cases, action_type='storageState').exists()):
            raise ValueError('包含登录态配置的任务必须使用 Playwright 执行')
        if not automatic and task.engine == 'playwright':
            from .playwright_engine import PlaywrightTestEngine
            ready, error = PlaywrightTestEngine.check_execution_environment_sync(task.browser)
            if not ready:
                raise ValueError(error)

        config = {
            'engine': task.engine, 'browser': task.browser, 'headless': task.headless,
            'storage_state_id': task.storage_state_id,
        }
        suite_execution_id = None
        case_execution_ids = []
        if task.task_type == 'TEST_SUITE':
            execution = TestExecution.objects.create(
                project_id=task.project_id, test_suite_id=task.test_suite_id,
                scheduled_task=task, executed_by=task.created_by, status='PENDING',
                engine=task.engine, browser=task.browser, headless=task.headless,
                run_config={'storage_state_id': task.storage_state_id},
            )
            suite_execution_id = execution.id
            TestSuite.objects.filter(pk=task.test_suite_id).update(execution_status='running')
        else:
            # 预建本批全部记录；启动线程失败时也能留下明确的失败结果。
            for case in cases:
                execution = TestCaseExecution.objects.create(
                    test_case=case, project_id=task.project_id, scheduled_task=task,
                    execution_source='scheduled', status='pending',
                    engine=task.engine, browser=task.browser, headless=task.headless,
                    created_by=task.created_by,
                )
                case_execution_ids.append(execution.id)

        task.last_run_time = timezone.now()
        task.total_runs = F('total_runs') + 1
        # 领取时推进下次时间，长任务不会被每一轮扫描重复触发。
        task.next_run_time = task.calculate_next_run()
        task.save(update_fields=['last_run_time', 'total_runs', 'next_run_time', 'updated_at'])
        transaction.on_commit(lambda: _launch_task(
            task.id, suite_execution_id, case_execution_ids, config))

        return {
            'message': '测试任务开始执行', 'task_id': task.id, 'task_name': task.name,
            'test_case_count': len(cases), 'engine': task.engine,
            'browser': task.browser, 'headless': task.headless,
            'suite_execution_id': suite_execution_id, 'case_execution_ids': case_execution_ids,
        }


def _fail_unfinished(suite_execution_id, case_execution_ids, error):
    """执行体异常或线程启动失败时，仅收尾本次运行尚未完成的记录。"""
    now = timezone.now()
    cases = TestCaseExecution.objects.filter(status__in=['pending', 'running'])
    if suite_execution_id:
        cases = cases.filter(suite_execution_id=suite_execution_id)
        parent = TestExecution.objects.filter(pk=suite_execution_id)
        parent.filter(status__in=['PENDING', 'RUNNING']).update(
            status='FAILED', error_message=error, finished_at=now)
        TestSuite.objects.filter(executions__id=suite_execution_id).update(execution_status='failed')
    else:
        cases = cases.filter(pk__in=case_execution_ids)
    cases.update(status='failed', error_message=error, finished_at=now)


def _complete_task(task_id, result):
    """按真实测试结果更新次数；通知失败不能把成功运行再次计为失败。"""
    success = result['status'] == 'success'
    counter = 'successful_runs' if success else 'failed_runs'
    UiScheduledTask.objects.filter(pk=task_id).update(
        **{counter: F(counter) + 1}, last_result=result,
        error_message=result.get('error', ''), updated_at=timezone.now())
    task = UiScheduledTask.objects.filter(pk=task_id).first()
    if task:
        try:
            from .views import UiScheduledTaskViewSet
            UiScheduledTaskViewSet()._send_task_notification(task, success=success)
        except Exception:
            logger.exception('UI 定时任务通知失败 task_id=%s', task_id)


def _launch_task(task_id, suite_execution_id, case_execution_ids, config):
    """显式传入本次记录 ID 和配置，避免调度循环变量串到其他任务。"""
    try:
        threading.Thread(
            target=_run_task, args=(task_id, suite_execution_id, case_execution_ids, config),
            daemon=True,
        ).start()
    except Exception as exc:
        logger.exception('UI 定时任务线程启动失败 task_id=%s', task_id)
        _fail_unfinished(suite_execution_id, case_execution_ids, str(exc))
        _complete_task(task_id, {'status': 'failed', 'error': str(exc)})


def _run_task(task_id, suite_execution_id, case_execution_ids, config):
    """自动触发与立即运行共用执行体，记录不依赖任务之后的配置修改。"""
    close_old_connections()
    result = {
        'status': 'failed', 'suite_execution_id': suite_execution_id,
        'case_execution_ids': case_execution_ids,
    }
    try:
        if suite_execution_id:
            execution = TestExecution.objects.select_related('test_suite', 'executed_by').get(
                pk=suite_execution_id)
            execution.status = 'RUNNING'
            execution.started_at = timezone.now()
            execution.save(update_fields=['status', 'started_at'])
            executor = TestExecutor(
                test_suite=execution.test_suite, executed_by=execution.executed_by,
                execution=execution, **config)
            executor.run()
            execution.refresh_from_db()
            # TestExecutor 内部捕获异常，因此必须读取落库状态判定成功。
            success = execution.status == 'SUCCESS'
            if not success:
                _fail_unfinished(suite_execution_id, [], execution.error_message or '套件执行失败')
            result.update(
                status='success' if success else 'failed',
                success_count=execution.passed_cases, failed_count=execution.failed_cases,
                test_case_count=execution.total_cases,
                error=execution.error_message or ('' if success else '套件执行失败'),
            )
        else:
            passed = 0
            for execution_id in case_execution_ids:
                execution = TestCaseExecution.objects.select_related('test_case__project', 'created_by').get(
                    pk=execution_id)
                if not execution.test_case.steps.exists():
                    # 零步骤会被执行器判为通过：不启动浏览器，直接记为失败，其余用例照常执行。
                    _fail_unfinished(None, [execution_id], '用例没有定义任何步骤，未执行')
                    continue
                execution.status = 'running'
                execution.started_at = timezone.now()
                execution.save(update_fields=['status', 'started_at'])
                try:
                    run_single_case(
                        execution.test_case, executed_by=execution.created_by,
                        execution=execution, **config)
                    execution.refresh_from_db()
                    # 保存失败不能只凭返回值计成功，防止记录一直停留在 running。
                    if execution.status in ('pending', 'running'):
                        raise RuntimeError('用例执行结果未保存')
                    passed += execution.status == 'passed'
                except Exception as exc:
                    logger.exception('UI 定时用例执行异常 execution_id=%s', execution_id)
                    _fail_unfinished(None, [execution_id], str(exc))
            failed = len(case_execution_ids) - passed
            result.update(
                status='success' if not failed else ('partial' if passed else 'failed'),
                success_count=passed, failed_count=failed, test_case_count=len(case_execution_ids),
                error=f'{failed}个测试用例执行失败' if failed else '',
            )
    except Exception as exc:
        logger.exception('UI 定时任务执行异常 task_id=%s', task_id)
        result.update(status='failed', error=str(exc))
        _fail_unfinished(suite_execution_id, case_execution_ids, str(exc))
    finally:
        try:
            _complete_task(task_id, result)
        finally:
            close_old_connections()
