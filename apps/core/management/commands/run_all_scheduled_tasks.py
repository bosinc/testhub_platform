from django.core.management.base import BaseCommand
from django.utils import timezone
import time
import logging

# 避免长循环中的数据库连接超时
from django.db import close_old_connections

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '运行所有模块的定时任务调度器（API测试 + UI自动化 + APP自动化 + 性能测试）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=60,
            help='检查间隔（秒），默认60秒'
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='只执行一次检查，不循环'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        run_once = options['once']

        self.stdout.write(self.style.SUCCESS(f"{'='*60}"))
        self.stdout.write(self.style.SUCCESS("启动统一定时任务调度器"))
        self.stdout.write(self.style.SUCCESS(f"检查间隔: {interval}秒"))
        self.stdout.write(self.style.SUCCESS(f"调度模块: API测试 + UI自动化 + APP自动化 + 性能测试"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}"))

        while True:
            # 每次循环前关闭旧连接，防止 MySQL 超时断开
            close_old_connections()
            try:
                # 统一使用本地时区，避免UTC导致调度延后8小时问题
                now = timezone.localtime(timezone.now())
                self.stdout.write(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 开始检查任务...")

                # 调度 API 测试模块的定时任务
                api_count = self.schedule_api_tasks()

                # 调度 UI 自动化模块的定时任务
                ui_count = self.schedule_ui_tasks()

                # 调度 APP 自动化模块的定时任务
                app_count = self.schedule_app_tasks()

                # 调度性能测试模块的定时任务
                perf_count = self.schedule_perf_tasks()

                total_count = api_count + ui_count + app_count + perf_count
                if total_count > 0:
                    self.stdout.write(self.style.SUCCESS(f"✓ 本次调度执行了 {total_count} 个任务 (API: {api_count}, UI: {ui_count}, APP: {app_count}, 性能: {perf_count})"))
                else:
                    self.stdout.write("  没有需要执行的任务")

                if run_once:
                    self.stdout.write(self.style.WARNING("单次执行模式，调度器退出"))
                    break

                self.stdout.write(f"等待 {interval} 秒后进行下一次检查...")
                time.sleep(interval)

            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("\n\n调度器已停止"))
                break
            except Exception as e:
                logger.error(f"调度器运行出错: {e}", exc_info=True)
                self.stdout.write(self.style.ERROR(f"调度器运行出错: {e}"))
                if run_once:
                    break
                self.stdout.write(f"等待 {interval} 秒后重试...")
                time.sleep(interval)

    def schedule_api_tasks(self):
        """调度 API 测试模块的定时任务"""
        try:
            # 确保连接有效，简单地关闭旧连接让 Django 重连
            close_old_connections()
            from apps.api_testing.models import ScheduledTask
            from apps.api_testing.views import ScheduledTaskViewSet

            # 获取所有活跃的定时任务
            active_tasks = ScheduledTask.objects.filter(status='ACTIVE')
            executed_count = 0

            # 显示所有活跃任务的调试信息
            if active_tasks.exists():
                now = timezone.now()
                self.stdout.write(f"  [API] 活跃任务数: {active_tasks.count()}")
                for task in active_tasks:
                    if task.next_run_time:
                        time_diff = (task.next_run_time - now).total_seconds()
                        if time_diff > 0:
                            self.stdout.write(f"        - {task.name}: 距下次执行还有 {int(time_diff)} 秒")
                        else:
                            self.stdout.write(f"        - {task.name}: 应该立即执行！")
                    else:
                        self.stdout.write(f"        - {task.name}: 未设置下次执行时间")

            for task in active_tasks:
                if task.should_run_now():
                    self.stdout.write(f"  [API] 执行任务: {task.name}")
                    self.stdout.write(f"       类型: {task.get_task_type_display() if hasattr(task, 'get_task_type_display') else task.task_type}, 触发方式: {task.get_trigger_type_display() if hasattr(task, 'get_trigger_type_display') else task.trigger_type}")
                    try:
                        # 创建执行日志
                        from apps.api_testing.models import TaskExecutionLog
                        execution_log = TaskExecutionLog.objects.create(
                            task=task,
                            status='PENDING'
                        )

                        # 调用任务执行方法
                        view = ScheduledTaskViewSet()
                        view._execute_task_async(task, execution_log)

                        executed_count += 1
                        self.stdout.write(self.style.SUCCESS(f"    ✓ 任务 {task.name} 已启动"))

                    except Exception as e:
                        logger.error(f"执行API任务 {task.name} 时出错: {e}", exc_info=True)
                        self.stdout.write(self.style.ERROR(f"    ✗ 任务 {task.name} 执行失败: {e}"))

            return executed_count

        except Exception as e:
            logger.error(f"调度API任务时出错: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f"[API] 调度失败: {e}"))
            return 0

    def schedule_ui_tasks(self):
        """自动触发与页面立即运行共用入口，避免临时套件级联删除执行记录。"""
        from apps.ui_automation.models import UiScheduledTask
        from apps.ui_automation.scheduled_tasks import start_scheduled_task

        executed_count = 0
        due_tasks = UiScheduledTask.objects.filter(
            status='ACTIVE', next_run_time__lte=timezone.now())
        for task in due_tasks:
            try:
                result = start_scheduled_task(task.id, automatic=True)
                if result:
                    executed_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  [UI] 任务 {task.name} 已启动"))
            except Exception as exc:
                logger.exception('调度 UI 任务失败 task_id=%s', task.id)
                # 无效配置也推进到下个触发点，避免每轮扫描重复报错。
                task.refresh_from_db()
                task.next_run_time = task.calculate_next_run()
                task.error_message = str(exc)
                task.save(update_fields=['next_run_time', 'error_message', 'updated_at'])
                self.stdout.write(self.style.ERROR(f"  [UI] 任务 {task.name} 启动失败: {exc}"))
        return executed_count

    def schedule_app_tasks(self):
        """调度 APP 自动化模块的定时任务"""
        try:
            from apps.app_automation.models import AppScheduledTask, AppTestExecution

            active_tasks = AppScheduledTask.objects.filter(status='ACTIVE')
            executed_count = 0

            if active_tasks.exists():
                now = timezone.now()
                self.stdout.write(f"  [APP] 活跃任务数: {active_tasks.count()}")
                for task in active_tasks:
                    if task.next_run_time:
                        time_diff = (task.next_run_time - now).total_seconds()
                        if time_diff > 0:
                            self.stdout.write(f"        - {task.name}: 距下次执行还有 {int(time_diff)} 秒")
                        else:
                            self.stdout.write(f"        - {task.name}: 应该立即执行！")
                    else:
                        self.stdout.write(f"        - {task.name}: 未设置下次执行时间")

            for task in active_tasks:
                if task.should_run_now():
                    self.stdout.write(f"  [APP] 执行任务: {task.name}")
                    self.stdout.write(f"       类型: {task.get_task_type_display()}, 触发方式: {task.get_trigger_type_display()}")
                    try:
                        # 更新统计
                        task.last_run_time = timezone.now()
                        task.total_runs += 1
                        task.next_run_time = task.calculate_next_run()
                        task.save()

                        device = task.device
                        if not device:
                            self.stdout.write(self.style.ERROR(f"    ✗ 任务 {task.name} 未配置设备"))
                            continue

                        package_name = task.app_package.package_name if task.app_package else ''

                        if task.task_type == 'TEST_SUITE' and task.test_suite:
                            suite_cases = task.test_suite.suite_cases.select_related('test_case').all()
                            if not suite_cases.exists():
                                self.stdout.write(self.style.ERROR(f"    ✗ 套件 {task.test_suite.name} 无用例"))
                                continue

                            executions = []
                            for sc in suite_cases:
                                execution = AppTestExecution.objects.create(
                                    test_case=sc.test_case,
                                    test_suite=task.test_suite,
                                    device=device,
                                    user=task.created_by,
                                    status='pending'
                                )
                                executions.append(execution)

                            task.test_suite.execution_status = 'running'
                            task.test_suite.save(update_fields=['execution_status'])

                            from apps.app_automation.tasks import execute_app_suite_task
                            from apps.core.task_queue import enqueue_task
                            enqueue_task(
                                execute_app_suite_task,
                                suite_id=task.test_suite.id,
                                execution_ids=[e.id for e in executions],
                                package_name=package_name,
                                scheduled_task_id=task.id,
                            )

                        elif task.task_type == 'TEST_CASE' and task.test_case:
                            execution = AppTestExecution.objects.create(
                                test_case=task.test_case,
                                device=device,
                                user=task.created_by,
                                status='pending'
                            )
                            from apps.app_automation.tasks import execute_app_test_task
                            from apps.core.task_queue import enqueue_task
                            task_id = enqueue_task(
                                execute_app_test_task,
                                execution.id,
                                package_name=package_name,
                                scheduled_task_id=task.id,
                            )
                            execution.task_id = task_id
                            execution.save(update_fields=['task_id'])

                        else:
                            self.stdout.write(self.style.ERROR(f"    ✗ 任务 {task.name} 配置不完整"))
                            continue

                        executed_count += 1
                        self.stdout.write(self.style.SUCCESS(f"    ✓ 任务 {task.name} 已启动"))

                    except Exception as e:
                        logger.error(f"执行APP任务 {task.name} 时出错: {e}", exc_info=True)
                        self.stdout.write(self.style.ERROR(f"    ✗ 任务 {task.name} 执行失败: {e}"))

            return executed_count

        except Exception as e:
            logger.error(f"调度APP任务时出错: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f"[APP] 调度失败: {e}"))
            return 0

    def schedule_perf_tasks(self):
        """调度性能测试模块的定时压测任务。

        压测是异步长任务，调度器只负责「到点拉起子进程」：
        - 派发成功后调用 task.mark_dispatched() 累加 run_count、推进 next_run_at
          （ONCE 任务自动暂停）；
        - 真正的 success_count/fail_count 由压测子进程收尾时回写
          （见 services/executor._update_task_stats），避免两处重复计数。
        """
        try:
            from apps.perf_testing.models import PerfScheduledTask
            from apps.perf_testing.services import executor

            active_tasks = PerfScheduledTask.objects.filter(status='ACTIVE').select_related('scenario', 'created_by')
            executed_count = 0

            if active_tasks.exists():
                now = timezone.now()
                self.stdout.write(f"  [性能] 活跃任务数: {active_tasks.count()}")
                for task in active_tasks:
                    if task.next_run_at:
                        time_diff = (task.next_run_at - now).total_seconds()
                        if time_diff > 0:
                            self.stdout.write(f"        - {task.name}: 距下次执行还有 {int(time_diff)} 秒")
                        else:
                            self.stdout.write(f"        - {task.name}: 应该立即执行！")
                    else:
                        self.stdout.write(f"        - {task.name}: 未设置下次执行时间")

            for task in active_tasks:
                if not task.should_run_now():
                    continue

                self.stdout.write(f"  [性能] 执行任务: {task.name}")
                self.stdout.write(f"       触发方式: {task.get_trigger_type_display()}")
                try:
                    scenario = task.scenario
                    if scenario.has_active_execution():
                        # 场景已有正在执行的压测，跳过本轮，不推进 next_run_at，
                        # 下个调度周期再尝试，避免重复加压。
                        self.stdout.write(self.style.WARNING(
                            f"    ! 任务 {task.name} 所属场景已有执行中的压测，本轮跳过"))
                        continue

                    # trigger_type 固定 SCHEDULED，便于执行记录归属与通知判定
                    execution, check = executor.start_execution(
                        scenario, user=task.created_by,
                        trigger_type='SCHEDULED', scheduled_task=task)

                    if execution is None:
                        # preflight 未通过，属于派发阶段失败
                        errors = (check or {}).get('errors') or []
                        error_msg = '; '.join(errors) if errors else '执行前检查未通过'
                        task.mark_dispatched(error=error_msg)
                        self.stdout.write(self.style.ERROR(
                            f"    ✗ 任务 {task.name} 前置检查未通过: {error_msg}"))
                        continue

                    task.mark_dispatched()
                    executed_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"    ✓ 任务 {task.name} 已启动 (执行 {execution.execution_no})"))

                except Exception as e:
                    logger.error(f"执行性能任务 {task.name} 时出错: {e}", exc_info=True)
                    # 派发阶段异常，计入失败并推进调度，避免卡死
                    try:
                        task.mark_dispatched(error=str(e))
                    except Exception:  # noqa: BLE001
                        logger.error(f"回写性能任务 {task.name} 失败状态时出错", exc_info=True)
                    self.stdout.write(self.style.ERROR(f"    ✗ 任务 {task.name} 执行失败: {e}"))

            return executed_count

        except Exception as e:
            logger.error(f"调度性能任务时出错: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f"[性能] 调度失败: {e}"))
            return 0
