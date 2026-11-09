"""
UI自动化测试执行服务
支持 Playwright 和 Selenium 测试引擎
"""
import time
import json
import logging
from datetime import datetime
from django.utils import timezone
from django.db import connection
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.webdriver.edge.options import Options as EdgeOptions

from .models import (
    TestSuite, TestExecution, TestCase, TestCaseStep,
    TestCaseExecution, Element
)
from .variable_context import UiVariableContext, as_text
from .playwright_engine import build_playwright_locator


class TestExecutor:
    """测试执行器基类"""

    def __init__(self, test_suite=None, engine='playwright', browser='chrome', headless=False, executed_by=None,
                 storage_state_id=None, execution=None, project=None):
        self.test_suite = test_suite
        # 单用例执行没有套件，直接由调用方传入项目；套件执行时从套件取。
        self.project = project or (test_suite.project if test_suite else None)
        self.engine = engine
        self.browser = browser
        self.headless = headless
        self.executed_by = executed_by
        self.storage_state_id = storage_state_id
        # 传入 execution 表示调用方已预建执行记录（MCP 需立即返回执行 ID），
        # 此时不再重复创建，仅复用同一条记录落结果。
        self.execution = execution
        self.test_cases = []
        self.results = []
        # 共享字典仅存活于这次套件运行；单用例和其他执行器各自创建上下文。
        self.run_variables = {}
        self.variables = UiVariableContext()

    @staticmethod
    def _goto_with_load_fallback(page, url, timeout=30000):
        """等待 DOM 就绪，并对持续产生请求的页面有界地等待 networkidle。"""
        page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        try:
            page.wait_for_load_state('networkidle', timeout=min(timeout, 5000))
        except PlaywrightTimeoutError:
            return False
        return True

    @property
    def suite_execution_ref(self):
        """本次套件执行记录；单用例执行时 execution 是 TestCaseExecution，返回 None。

        逐用例记录必须挂到父执行记录上，否则并发执行同一套件时只能按时间窗
        关联，两批记录会互相串批。
        """
        return self.execution if isinstance(self.execution, TestExecution) else None

    def create_execution_record(self):
        """创建测试执行记录（已预建则直接复用）"""
        if self.execution is not None:
            return self.execution
        self.execution = TestExecution.objects.create(
            project=self.test_suite.project,
            test_suite=self.test_suite,
            status='RUNNING',
            engine=self.engine,
            browser=self.browser,
            headless=self.headless,
            executed_by=self.executed_by,
            started_at=timezone.now()
        )
        self.execution.run_config = {
            'storage_state_id': self.storage_state_id,
        }
        self.execution.save(update_fields=['run_config'])
        return self.execution

    def update_execution_result(self, status, passed=0, failed=0, skipped=0, duration=0, error_msg=''):
        """更新执行结果"""
        self.execution.status = status
        self.execution.passed_cases = passed
        self.execution.failed_cases = failed
        self.execution.skipped_cases = skipped
        self.execution.total_cases = passed + failed + skipped
        self.execution.duration = duration
        self.execution.finished_at = timezone.now()
        self.execution.error_message = error_msg
        self.execution.result_data = {
            'test_cases': self.results,
            'summary': {
                'total': self.execution.total_cases,
                'passed': passed,
                'failed': failed,
                'skipped': skipped,
                'pass_rate': round((passed / self.execution.total_cases * 100) if self.execution.total_cases > 0 else 0,
                                   2)
            }
        }
        self.execution.save()

        # 更新套件统计
        self.test_suite.passed_count = passed
        self.test_suite.failed_count = failed
        self.test_suite.execution_status = 'passed' if failed == 0 and passed > 0 else 'failed'
        self.test_suite.save()

    def get_test_cases(self):
        """获取测试套件中的所有测试用例"""
        suite_test_cases = self.test_suite.suite_test_cases.select_related('test_case').order_by('order')
        self.test_cases = [stc.test_case for stc in suite_test_cases]
        print(f"从套件 '{self.test_suite.name}' 获取到 {len(self.test_cases)} 个测试用例")
        for i, tc in enumerate(self.test_cases, 1):
            print(f"  {i}. {tc.name} (ID: {tc.id})")
        return self.test_cases

    def _build_case_data(self, test_case):
        """预加载用例步骤与元素快照，避免浏览器执行阶段再访问数据库。"""
        case_data = {
            'id': test_case.id,
            'name': test_case.name,
            'project_id': self.project.id,
            'steps': [],
        }
        steps = test_case.steps.select_related(
            'element', 'element__locator_strategy').order_by('step_number')
        for step in steps:
            step_data = {
                'id': step.id,
                'step_number': step.step_number,
                'action_type': step.action_type,
                'description': step.description,
                'input_value': step.input_value,
                'wait_time': step.wait_time,
                'assert_type': step.assert_type,
                'assert_value': step.assert_value,
                'action_params': step.action_params or {},
                'element': None,
            }
            if step.element:
                step_data['element'] = {
                    'id': step.element.id,
                    'name': step.element.name,
                    'locator_value': step.element.locator_value,
                    'locator_strategy': (step.element.locator_strategy.name
                                         if step.element.locator_strategy else 'css'),
                    'locator_options': step.element.locator_options or {},
                }
            case_data['steps'].append(step_data)
        return case_data

    def run(self):
        """执行测试套件"""
        print(f"[TestExecutor] 初始化执行器...")
        try:
            # 每次套件执行从项目定义创建全新的运行副本，步骤覆盖仅存活于本次执行。
            self.run_variables = {}
            self.variables = UiVariableContext(self.project.variables, run=self.run_variables)
            # 设置环境变量，允许在后台线程中使用同步 ORM
            # 这对于 Playwright 执行是必需的
            import os
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'

            # 关闭当前线程的数据库连接，避免线程间共享
            connection.close()
            print(f"[TestExecutor] 数据库连接已重置")

            # 创建执行记录
            print(f"[TestExecutor] 创建执行记录...")
            self.create_execution_record()
            print(f"[TestExecutor] 执行记录已创建: ID={self.execution.id}")

            # 获取测试用例
            print(f"[TestExecutor] 获取测试用例...")
            self.get_test_cases()
            print(f"[TestExecutor] 获取到 {len(self.test_cases)} 个测试用例")

            # 根据引擎选择执行方式
            print(f"[TestExecutor] 使用引擎: {self.engine}")
            if self.engine == 'playwright':
                print(f"[TestExecutor] 启动 Playwright 执行...")
                self.run_with_playwright()
            else:
                print(f"[TestExecutor] 启动 Selenium 执行...")
                self.run_with_selenium()

            print(f"[TestExecutor] 执行完成")

        except Exception as e:
            print(f"[TestExecutor] 测试执行失败: {str(e)}")
            import traceback
            traceback.print_exc()
            if self.execution:
                print(f"[TestExecutor] 更新执行结果为失败...")
                self.update_execution_result(
                    status='FAILED',
                    error_msg=f"执行失败: {str(e)}\n\n{traceback.format_exc()}"
                )
        finally:
            # 套件完成后清空运行副本；套件执行期间仍可跨用例共享变量。
            self.variables.clear()
            self.run_variables.clear()
            # 确保关闭数据库连接
            print(f"[TestExecutor] 关闭数据库连接...")
            connection.close()
            print(f"[TestExecutor] 执行器已退出")

    def run_with_playwright(self):
        """使用 Playwright 执行测试（同步版本）"""
        start_time = time.time()
        passed = 0
        failed = 0
        skipped = 0

        # 检查 Playwright 是否可用
        try:
            from playwright.sync_api import sync_playwright as test_import
        except ImportError as e:
            error_msg = (
                "Playwright 模块未正确安装或 Django 服务器未在虚拟环境中运行。\n\n"
                "请确保：\n"
                "1. 已在虚拟环境中安装: pip install playwright\n"
                "2. 已安装浏览器: playwright install\n"
                "3. Django 服务器在虚拟环境中运行\n\n"
                f"详细错误: {str(e)}"
            )
            print(f"❌ {error_msg}")

            # 更新套件执行状态
            if self.execution:
                self.update_execution_result(
                    status='FAILED',
                    failed=len(self.test_cases),
                    error_msg=error_msg
                )

            # 更新所有用例状态为失败
            for test_case in self.test_cases:
                TestCaseExecution.objects.filter(
                    test_case=test_case,
                    test_suite=self.test_suite,
                    status='pending'
                ).update(
                    status='failed',
                    error_message=error_msg,
                    finished_at=timezone.now()
                )

            return

        from apps.ui_automation.playwright_engine import PlaywrightTestEngine

        is_ready, error_msg = PlaywrightTestEngine.check_execution_environment_sync(self.browser)
        if not is_ready:
            print(f"❌ {error_msg}")

            if self.execution:
                self.update_execution_result(
                    status='FAILED',
                    failed=len(self.test_cases),
                    error_msg=error_msg
                )

            for test_case in self.test_cases:
                TestCaseExecution.objects.filter(
                    test_case=test_case,
                    test_suite=self.test_suite,
                    status='pending'
                ).update(
                    status='failed',
                    error_message=error_msg,
                    finished_at=timezone.now()
                )

            return

        # 预先获取所有测试用例的步骤数据，避免在Playwright上下文中访问ORM
        test_cases_data = [self._build_case_data(tc) for tc in self.test_cases]

        # 预先创建所有测试用例执行记录（不设置 started_at，等实际执行时再设置）
        case_executions = {}
        for case_data in test_cases_data:
            case_execution = TestCaseExecution.objects.create(
                test_case_id=case_data['id'],
                project_id=case_data['project_id'],
                test_suite=self.test_suite,
                suite_execution=self.suite_execution_ref,
                execution_source='suite',
                status='pending',  # 初始状态为 pending
                engine=self.engine,
                browser=self.browser,
                headless=self.headless,
                created_by=self.executed_by
                # 注意：不设置 started_at，等用例实际开始执行时再设置
            )
            case_executions[case_data['id']] = case_execution

        # 执行每个测试用例，为每个用例单独启动和关闭浏览器
        print(f"准备执行 {len(test_cases_data)} 个测试用例")

        with sync_playwright() as p:
            for i, case_data in enumerate(test_cases_data, 1):
                print(f"\n{'=' * 60}")
                print(f"正在执行第 {i}/{len(test_cases_data)} 个用例: {case_data['name']}")
                print(f"{'=' * 60}")

                # 记录用例实际开始执行时间
                case_execution = case_executions[case_data['id']]
                case_execution.started_at = timezone.now()
                case_execution.status = 'running'
                case_execution.save()

                # 为每个测试用例启动新的浏览器实例
                try:
                    browser = _launch_playwright_browser(
                        p, self.browser, self.headless)

                    print(f"✓ 浏览器已启动")

                    # 配置上下文，并在套件执行开始时加载用户选择的初始登录态。
                    self.browser_instance = browser
                    self._active_project_id = case_data['project_id']
                    initial_state = None
                    if self.storage_state_id:
                        from .models import StorageState
                        initial_state = StorageState.objects.filter(
                            pk=self.storage_state_id, project_id=case_data['project_id']
                        ).first()
                        if not initial_state:
                            raise ValueError('选择的登录态不存在或不属于当前项目')
                    self.context = browser.new_context(
                        viewport={'width': 1920, 'height': 1080},
                        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                        **({'storage_state': initial_state.file.path} if initial_state else {})
                    )
                    self.current_page = self.context.new_page()

                    # 导航到项目基础URL
                    if self.project.base_url:
                        try:
                            print(f"正在导航到: {self.project.base_url}")

                            # 检测是否在Linux服务器环境
                            import platform
                            is_linux = platform.system() == 'Linux'

                            self._goto_with_load_fallback(
                                self.current_page, self.project.base_url, timeout=30000
                            )

                            # 额外等待，确保动态内容加载（Vue/React等SPA应用）
                            # 服务器无头模式需要更长的等待时间
                            extra_wait = 3 if is_linux else 2
                            time.sleep(extra_wait)

                            print(
                                f"✓ 成功导航到: {self.project.base_url} (已等待页面加载完成，额外{extra_wait}秒)")
                        except Exception as e:
                            print(f"✗ 导航失败: {str(e)}")
                            # 导航失败，记录错误并继续下一个用例
                            self.results.append({
                                'test_case_id': case_data['id'],
                                'test_case_name': case_data['name'],
                                'status': 'failed',
                                'steps': [],
                                'error': f"导航到基础URL失败: {str(e)}",
                                'start_time': datetime.now().isoformat(),
                                'end_time': datetime.now().isoformat(),
                                'screenshots': []
                            })
                            failed += 1
                            browser.close()
                            print(f"✓ 浏览器已关闭")
                            continue

                    # 执行测试用例（不再传递page参数，使用self.current_page）
                    case_result = self.execute_test_case_playwright_no_db(case_data)
                    self.results.append(case_result)
                    print(f"✓ 用例执行完成，状态: {case_result['status']}")

                    # 立即更新该用例的执行记录（包含准确的执行时间）
                    case_execution = case_executions[case_data['id']]
                    case_execution.status = case_result['status']
                    case_execution.finished_at = timezone.now()
                    case_execution.execution_time = (
                                case_execution.finished_at - case_execution.started_at).total_seconds()
                    case_execution.execution_logs = json.dumps(case_result['steps'], ensure_ascii=False)
                    if case_result['error']:
                        case_execution.error_message = case_result['error']
                    if case_result.get('screenshots'):
                        case_execution.screenshots = case_result['screenshots']
                    case_execution.save()

                    print(f"⏱️  执行时长: {case_execution.execution_time:.2f}秒")

                    if case_result['status'] == 'passed':
                        passed += 1
                    elif case_result['status'] == 'failed':
                        failed += 1
                    else:
                        skipped += 1

                except Exception as e:
                    print(f"✗ 用例执行出现异常: {str(e)}")
                    # 记录异常
                    self.results.append({
                        'test_case_id': case_data['id'],
                        'test_case_name': case_data['name'],
                        'status': 'failed',
                        'steps': [],
                        'error': f"用例执行异常: {str(e)}",
                        'start_time': datetime.now().isoformat(),
                        'end_time': datetime.now().isoformat(),
                        'screenshots': []
                    })
                    failed += 1

                    # 更新执行记录
                    case_execution = case_executions[case_data['id']]
                    case_execution.status = 'failed'
                    case_execution.finished_at = timezone.now()
                    case_execution.execution_time = (
                                case_execution.finished_at - case_execution.started_at).total_seconds()
                    case_execution.error_message = f"用例执行异常: {str(e)}"
                    case_execution.save()

                finally:
                    # 确保每个用例执行后都关闭浏览器
                    try:
                        browser.close()
                        print(f"✓ 浏览器已关闭\n")
                    except:
                        pass

        # 注意：每个用例的执行记录已在执行过程中实时更新，不需要在这里统一更新

        duration = time.time() - start_time
        status = 'SUCCESS' if failed == 0 else 'FAILED'
        self.update_execution_result(status, passed, failed, skipped, duration)

    def execute_test_case_playwright_no_db(self, case_data):
        """使用 Playwright 执行单个测试用例（不访问数据库）

        Args:
            case_data: 预先准备的用例数据字典，包含id, name, project_id, steps等
            
        Note:
            使用 self.current_page 作为当前活动页面，switchTab会更新这个实例变量
        """
        result = {
            'test_case_id': case_data['id'],
            'test_case_name': case_data['name'],
            'status': 'passed',
            'steps': [],
            'error': None,
            'start_time': datetime.now().isoformat(),
            'screenshots': []
        }

        try:
            # 遍历预先准备好的步骤数据
            just_switched_tab = False  # 跟踪是否刚切换了标签页
            for step_data in case_data['steps']:
                # 如果刚切换了标签页，传递这个信息
                step_data['_just_switched_tab'] = just_switched_tab
                just_switched_tab = False  # 重置标志

                step_result = self.execute_step_playwright(step_data)

                # Debug: Log which page we're using
                print(f"📄 步骤 {step_data['step_number']} 执行完成")
                print(f"   使用的page URL: {self.current_page.url}")
                print(f"   使用的page 标题: {self.current_page.title()}")

                result['steps'].append(step_result)

                # 显式更新self.current_page，确保引用正确
                if step_result.get('switched_page'):
                    self.current_page = step_result['switched_page']
                    print(f"🔄 页面切换确认: {self.current_page.title()}")
                    print(f"   当前页面URL: {self.current_page.url}")
                    print(f"   Page ID: {id(self.current_page)}")
                    del step_result['switched_page']
                    just_switched_tab = True

                # 步骤执行完后添加短暂延迟，确保页面状态稳定
                # 特别是点击操作后，可能触发动画、下拉框展开等
                if step_result['success'] and step_data['action_type'] in ['click', 'fill', 'hover']:
                    import asyncio
                    import time as sync_time
                    # 点击操作后等待更长时间（下拉框展开动画）
                    if step_data['action_type'] == 'click':
                        self.current_page.wait_for_timeout(800)  # 等待800ms，确保下拉框完全展开
                    else:
                        self.current_page.wait_for_timeout(300)  # 其他操作等待300ms

                # 如果步骤失败，捕获失败截图
                if not step_result['success']:
                    result['status'] = 'failed'
                    # 使用step的error信息作为case的error
                    result['error'] = step_result.get('error', f"步骤 {step_data['step_number']} 执行失败")

                    # 捕获失败截图（改进版）
                    try:
                        import base64
                        # 增加超时设置，避免截图等待时间过长
                        print(f"🔍 开始捕获失败截图 (步骤 {step_data['step_number']})...")
                        print(f"   当前page对象URL: {self.current_page.url}")
                        print(f"   当前page对象标题: {self.current_page.title()}")
                        screenshot_bytes = self.current_page.screenshot(timeout=5000)  # 5秒超时
                        print(f"   截图字节大小: {len(screenshot_bytes)} bytes")

                        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                        print(f"   Base64 编码大小: {len(screenshot_base64)} characters")

                        # 验证 base64 编码是否有效
                        if len(screenshot_base64) < 100:
                            raise Exception(f"Base64 编码异常短 ({len(screenshot_base64)} chars)，可能截图失败")

                        screenshot_url = f'data:image/png;base64,{screenshot_base64}'
                        result['screenshots'].append({
                            'url': screenshot_url,
                            'description': f'步骤 {step_data["step_number"]} 失败截图: {step_data.get("description", "")}',
                            'step_number': step_data['step_number'],
                            'timestamp': datetime.now().isoformat()
                        })
                        print(f"✓ 失败截图已捕获 (步骤 {step_data['step_number']})")
                        print(f"   截图 URL 长度: {len(screenshot_url)} characters")
                    except Exception as screenshot_error:
                        error_msg = f"捕获失败截图失败: {str(screenshot_error)}"
                        print(f"⚠️  {error_msg}")
                        import traceback
                        print(f"   详细错误:\n{traceback.format_exc()}")
                        # 记录截图失败的详细信息到结果中
                        result['screenshots'].append({
                            'url': None,
                            'description': f'步骤 {step_data["step_number"]} 截图失败: {str(screenshot_error)}',
                            'step_number': step_data['step_number'],
                            'timestamp': datetime.now().isoformat(),
                            'error': str(screenshot_error)
                        })

                    break

        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)

            # 捕获异常截图（改进版）
            try:
                import base64
                # 增加超时设置，避免截图等待时间过长
                print(f"🔍 开始捕获异常截图...")
                screenshot_bytes = self.current_page.screenshot(timeout=5000)  # 5秒超时
                print(f"   截图字节大小: {len(screenshot_bytes)} bytes")

                screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                print(f"   Base64 编码大小: {len(screenshot_base64)} characters")

                # 验证 base64 编码是否有效
                if len(screenshot_base64) < 100:
                    raise Exception(f"Base64 编码异常短 ({len(screenshot_base64)} chars)，可能截图失败")

                screenshot_url = f'data:image/png;base64,{screenshot_base64}'
                result['screenshots'].append({
                    'url': screenshot_url,
                    'description': f'异常截图: {str(e)}',
                    'step_number': None,
                    'timestamp': datetime.now().isoformat()
                })
                print(f"✓ 异常截图已捕获")
                print(f"   截图 URL 长度: {len(screenshot_url)} characters")
            except Exception as screenshot_error:
                error_msg = f"捕获异常截图失败: {str(screenshot_error)}"
                print(f"⚠️  {error_msg}")
                import traceback
                print(f"   详细错误:\n{traceback.format_exc()}")
                # 记录截图失败的详细信息到结果中
                result['screenshots'].append({
                    'url': None,
                    'description': f'异常截图失败: {str(screenshot_error)}',
                    'step_number': None,
                    'timestamp': datetime.now().isoformat(),
                    'error': str(screenshot_error)
                })

        result['end_time'] = datetime.now().isoformat()
        self.variables.clear_temporary()
        return result

    def execute_test_case_playwright(self, page, case_data):
        self.current_page = page
        """使用 Playwright 执行单个测试用例（同步版本） - 已弃用，保留用于向后兼容

        Args:
            page: Playwright page对象
            case_data: 预先准备的用例数据字典，包含id, name, project_id, steps等
        """
        result = {
            'test_case_id': case_data['id'],
            'test_case_name': case_data['name'],
            'status': 'passed',
            'steps': [],
            'error': None,
            'start_time': datetime.now().isoformat(),
            'screenshots': []
        }

        # 创建用例执行记录
        case_execution = TestCaseExecution.objects.create(
            test_case_id=case_data['id'],
            project_id=case_data['project_id'],
            status='running',
            browser=self.browser,
            created_by=self.executed_by,
            started_at=timezone.now()
        )

        try:
            # 遍历预先准备好的步骤数据
            for step_data in case_data['steps']:
                step_result = self.execute_step_playwright(step_data)
                result['steps'].append(step_result)

                if not step_result['success']:
                    result['status'] = 'failed'
                    # 使用step的error信息作为case的error
                    result['error'] = step_result.get('error', f"步骤 {step_data['step_number']} 执行失败")
                    break

            # 更新用例执行记录
            case_execution.status = result['status']
            case_execution.finished_at = timezone.now()
            case_execution.execution_time = (case_execution.finished_at - case_execution.started_at).total_seconds()
            case_execution.execution_logs = json.dumps(result['steps'], ensure_ascii=False)
            if result['error']:
                case_execution.error_message = result['error']
            case_execution.save()

        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)

            case_execution.status = 'error'
            case_execution.error_message = str(e)
            case_execution.finished_at = timezone.now()
            case_execution.save()

        result['end_time'] = datetime.now().isoformat()
        self.variables.clear_temporary()
        return result

    def execute_step_playwright(self, step_data):
        """使用 Playwright 执行单个步骤（同步版本）"""
        result = self._execute_step_playwright(step_data)
        if self.variables.strict:
            # 将变量状态附在步骤结果上，供执行记录查看。
            result['variables'] = self.variables.snapshot()
        if result.get('error'):
            result['error'] = self.variables.redact(result['error'])
        if isinstance(result.get('result'), str):
            result['result'] = self.variables.redact(result['result'])
        return result

    def _execute_step_playwright(self, step_data):
        """使用 Playwright 执行单个步骤（同步版本）

        Args:
            step_data: 预先准备的步骤数据字典
            
        Note:
            使用 self.current_page 作为当前活动页面
        """
        import time
        start_time = time.time()

        step_result = {
            'step_number': step_data['step_number'],
            'action_type': step_data['action_type'],
            'description': step_data['description'],
            'success': False,
            'error': None
        }
        element_name = step_data.get('element', {}).get('name', '页面') if step_data.get('element') else '页面'
        locator_strategy = step_data.get('element', {}).get('locator_strategy', 'css') if step_data.get('element') else 'css'
        locator_value = step_data.get('element', {}).get('locator_value', '') if step_data.get('element') else ''

        try:
            # 两个引擎都只在当前步骤开始时渲染，避免未执行步骤提前生成随机值。
            step_data = self.variables.render_step(step_data)
            variable_result = self.variables.apply_step(step_data)
            if variable_result is not None:
                step_result['success'], step_result['result'] = variable_result[0], variable_result[1]
                return step_result
            # DOM 输入和文本断言使用字符串；上下文中的变量仍保留原始类型。
            step_data['input_value'] = as_text(step_data.get('input_value', ''))
            step_data['assert_value'] = as_text(step_data.get('assert_value', ''))
            # 登录态动作会改变执行上下文，必须优先于普通页面动作处理。
            if step_data['action_type'] == 'storageState':
                self._execute_storage_state_action(step_data.get('action_params') or {})
                step_result['success'] = True
            elif step_data['action_type'] == 'navigate':
                url = step_data.get('input_value', '')
                if not url:
                    step_result['error'] = '跳转URL失败: URL不能为空'
                else:
                    self._goto_with_load_fallback(
                        self.current_page, url, timeout=max(step_data.get('wait_time', 30000), 30000)
                    )
                    step_result['success'] = True
            elif step_data['action_type'] == 'assert' and step_data.get('assert_type') == 'urlContains':
                expected_url = step_data.get('assert_value', '')
                # 登录点击后的重定向是异步的，套件执行器也需要按步骤等待 URL 变化。
                timeout_seconds = max(step_data.get('wait_time', 5000), 0) / 1000
                deadline = time.monotonic() + timeout_seconds
                actual_url = self.current_page.url
                while expected_url and expected_url not in actual_url and time.monotonic() < deadline:
                    self.current_page.wait_for_timeout(100)
                    actual_url = self.current_page.url
                step_result['success'] = bool(expected_url and expected_url in actual_url)
                if not step_result['success']:
                    step_result['error'] = f"✗ 断言失败: URL不包含 '{expected_url}'\n  - 实际URL: '{actual_url}'"
            # 获取元素定位器
            elif step_data['element']:
                element = step_data['element']
                locator_value = element['locator_value']
                locator_strategy = element['locator_strategy'].lower()
                element_name = element.get('name', '未知元素')

                # 同步套件和异步单用例使用相同的定位选项及 first/nth 语义。
                target_locator = build_playwright_locator(
                    self.current_page, locator_strategy, locator_value,
                    element.get('locator_options'),
                )

                # 根据操作类型执行动作
                if step_data['action_type'] == 'click':
                    click_params = step_data.get('action_params') or {}
                    if click_params.get('optional'):
                        match_count = target_locator.count()
                        if not match_count or (match_count == 1 and not target_locator.is_visible()):
                            step_result['success'] = True
                            step_result['skipped'] = True
                            step_result['result'] = '可选点击目标不存在或不可见，已跳过'
                            return step_result

                    if click_params.get('skip_if_disabled'):
                        if target_locator.count() == 1 and not target_locator.is_enabled():
                            step_result['success'] = True
                            step_result['skipped'] = True
                            step_result['result'] = '目标按钮已禁用，无待保存变更，跳过点击'
                            return step_result

                    force_click = bool(click_params.get('force'))

                    # 检测是否是原生HTML select的option元素（优先检测，因为option元素特殊）
                    is_native_select_option = (
                            (
                                        'option[' in locator_value or ' > option' in locator_value or '//option' in locator_value) or
                            ('select' in locator_value.lower() and 'option' in locator_value.lower())
                    )

                    # 对于原生HTML select的option，使用select_option方法
                    if is_native_select_option and not element.get('locator_options'):
                        print(f"[Playwright-调试] 检测到原生HTML select元素，使用select_option方法...")

                        # 提取option的value值
                        import re
                        option_value_match = re.search(r'option\[value=["\']([^"\']+)["\']\]', locator_value)
                        option_value_xpath_match = re.search(r'option\[@value=["\']([^"\']+)["\']\]', locator_value)

                        option_value = None
                        if option_value_match:
                            option_value = option_value_match.group(1)
                        elif option_value_xpath_match:
                            option_value = option_value_xpath_match.group(1)
                        else:
                            option_value = '1'  # 默认值

                        # 构造select元素的定位器（去掉option部分）
                        select_locator_value = re.sub(r'\s*>\s*option\[.*?\]', '', locator_value)
                        select_locator_value = re.sub(r'\s+option\[.*?\]', '', select_locator_value)
                        select_locator_value = re.sub(r'//option\[.*?\]', '', select_locator_value)

                        print(f"[Playwright-调试] Select定位器: {select_locator_value}, Option值: {option_value}")

                        try:
                            # 构造select元素的locator
                            if locator_strategy.lower() == 'xpath':
                                select_match = re.match(r'^(//.*?select)(?:/option)?', locator_value)
                                if select_match:
                                    select_locator_value = select_match.group(1)
                                else:
                                    select_locator_value = locator_value.split('/')[0]
                                select_locator = self.current_page.locator(f"xpath={select_locator_value}")
                            else:
                                select_locator = self.current_page.locator(select_locator_value)

                            # 使用select_option方法
                            select_locator.select_option(value=option_value, timeout=step_data['wait_time'])

                            execution_time = round(time.time() - start_time, 2)
                            step_result['success'] = True
                            print(f"✓ 选择下拉框选项成功 (select_option方法)")
                            # 成功处理select，跳过后续逻辑
                            native_select_handled = True
                        except Exception as e:
                            print(f"✗ select_option失败: {e}")
                            # 如果失败，继续尝试普通点击
                            native_select_handled = False
                    else:
                        native_select_handled = False

                    # 只有当原生select处理失败或不是原生select时，才继续后续逻辑
                    if not native_select_handled:
                        # 检测是否是下拉框选项（需要特殊处理）
                        # 简化逻辑：只要是 XPath 的 //li 元素，或包含特定关键词，就认为是下拉框选项
                        is_dropdown_option = (
                            # 条件1: XPath 定位的 li 元素（最常见的下拉框选项）
                                (locator_strategy.lower() == 'xpath' and '//li' in locator_value) or
                                # 条件2: CSS 或 XPath 包含 el-select-dropdown
                                'el-select-dropdown' in locator_value.lower() or
                                # 条件3: 包含 role="option"
                                'role="option"' in locator_value.lower() or
                                # 条件4: 包含 li 标签且看起来像列表项
                                ('li' in locator_value.lower() and (
                                            'ul' in locator_value.lower() or 'ol' in locator_value.lower()))
                        )

                        # 检测是否是 el-select 容器（下拉框触发器）
                        is_select_trigger = (
                                'el-select' in locator_value.lower() and
                                'ancestor::' in locator_value and
                                '//li' not in locator_value
                        )

                        if is_select_trigger and not element.get('locator_options'):
                            # el-select 容器：需要点击内部的真正触发器
                            import time as sync_time

                            # 使用 JavaScript 查找并点击内部的可点击元素
                            if locator_strategy.lower() == 'xpath':
                                js_code = f"""
                                    (() => {{
                                        const xpath = {repr(locator_value)};
                                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                        const selectEl = result.singleNodeValue;

                                        if (!selectEl) return {{ success: false, error: '未找到 el-select 容器' }};

                                        // 查找内部的触发器（按优先级）
                                        let trigger = selectEl.querySelector('.el-select__wrapper') ||
                                                     selectEl.querySelector('input') ||
                                                     selectEl.querySelector('.el-input__inner');

                                        if (trigger) {{
                                            trigger.click();
                                            return {{ success: true, method: 'inner-trigger', element: trigger.className }};
                                        }} else {{
                                            // 如果找不到内部触发器，直接点击容器
                                            selectEl.click();
                                            return {{ success: true, method: 'container', element: selectEl.className }};
                                        }}
                                    }})()
                                """
                            else:
                                js_code = f"""
                                    (() => {{
                                        const selectEl = document.querySelector({repr(locator_value)});

                                        if (!selectEl) return {{ success: false, error: '未找到 el-select 容器' }};

                                        let trigger = selectEl.querySelector('.el-select__wrapper') ||
                                                     selectEl.querySelector('input') ||
                                                     selectEl.querySelector('.el-input__inner');

                                        if (trigger) {{
                                            trigger.click();
                                            return {{ success: true, method: 'inner-trigger', element: trigger.className }};
                                        }} else {{
                                            selectEl.click();
                                            return {{ success: true, method: 'container', element: selectEl.className }};
                                        }}
                                    }})()
                                """

                            js_result = self.current_page.evaluate(js_code)

                            if js_result.get('success'):
                                self.current_page.wait_for_timeout(800)  # 等待下拉框展开
                                step_result['success'] = True
                            else:
                                step_result['error'] = f"✗ 下拉框触发器点击失败: {js_result.get('error')}"

                        elif is_dropdown_option and not element.get('locator_options'):
                            # 下拉框选项：使用 Playwright 原生方法（更可靠）
                            # 之前使用 JS click() 可能无法触发 Element Plus 的事件监听
                            self.current_page.wait_for_timeout(800)  # 等待下拉框展开

                            print(f"[Playwright-调试] 下拉框选项处理: {locator_strategy}={locator_value}")

                            # 构造基础定位器（移除 Playwright 特有的伪类，因为我们要手动遍历）
                            base_locator_value = locator_value.replace(' >> visible=true', '')

                            try:
                                if locator_strategy.lower() == 'xpath':
                                    if not base_locator_value.startswith('xpath='):
                                        candidates = self.current_page.locator(f"xpath={base_locator_value}")
                                    else:
                                        candidates = self.current_page.locator(base_locator_value)
                                elif locator_strategy.lower() in ['css', 'css selector']:
                                    candidates = self.current_page.locator(base_locator_value)
                                else:
                                    # 其他策略暂按 CSS 处理
                                    candidates = self.current_page.locator(base_locator_value)

                                # 获取匹配元素数量
                                count = candidates.count()
                                print(f"[Playwright-调试] 找到 {count} 个匹配元素")

                                found_visible = False
                                last_error = None

                                for i in range(count):
                                    try:
                                        candidate = candidates.nth(i)
                                        if candidate.is_visible():
                                            print(f"[Playwright-调试] 第 {i} 个元素可见，尝试点击...")
                                            # 使用 Playwright 的 click，它会触发完整的鼠标事件链
                                            candidate.click(timeout=2000)
                                            found_visible = True
                                            step_result['success'] = True
                                            print(f"[Playwright-调试] 点击成功")
                                            break
                                    except Exception as e:
                                        print(f"[Playwright-调试] 点击第 {i} 个元素失败: {e}")
                                        last_error = e

                                if not found_visible:
                                    error_msg = f"未找到可见的下拉框选项 (匹配到 {count} 个元素)"
                                    if last_error:
                                        error_msg += f", 最后一次错误: {str(last_error)}"
                                    step_result['error'] = error_msg
                                    step_result['success'] = False

                            except Exception as e:
                                step_result['error'] = f"下拉框选项处理异常: {str(e)}"
                                step_result['success'] = False

                            # 检查并关闭多选下拉框（如果还在显示）
                            if step_result['success']:
                                try:
                                    if self.current_page.locator('.el-select-dropdown').first.is_visible():
                                        # 点击空白处关闭
                                        self.current_page.click('body', position={'x': 10, 'y': 10}, timeout=3000)
                                        self.current_page.wait_for_timeout(500)
                                except:
                                    pass

                            # 已移除调试面板代码
                        else:
                            # 普通元素：正常点击
                            # 如果刚切换了标签页，增加超时时间并滚动到元素
                            if step_data.get('_just_switched_tab'):
                                print(f"  ⚠️  刚切换标签页，增加元素等待时间和滚动")

                                # 关键修复：确保页面保持在前台！
                                self.current_page.bring_to_front()
                                print(f"  ✓ 页面已置于前台")

                                # 先尝试滚动到元素（确保元素在视口内）
                                try:
                                    target_locator.scroll_into_view_if_needed(timeout=5000)
                                    print(f"  ✓ 元素已滚动到视口")
                                except Exception as e:
                                    print(f"  ⚠️  滚动失败: {str(e)[:50]}")

                                # 使用更长的超时时间（至少10秒）
                                extended_timeout = max(step_data['wait_time'], 10000)
                                target_locator.click(timeout=extended_timeout, force=force_click)
                                print(f"  ✓ 点击成功（超时: {extended_timeout}ms）")
                            else:
                                target_locator.click(timeout=step_data['wait_time'], force=force_click)
                            step_result['success'] = True

                elif step_data['action_type'] == 'fill':
                    # 解析输入值中的变量表达式
                    resolved_value = step_data['input_value']

                    # 如果刚切换了标签页，增加超时时间
                    if step_data.get('_just_switched_tab'):
                        # 确保页面保持在前台
                        self.current_page.bring_to_front()
                        extended_timeout = max(step_data['wait_time'], 10000)
                        target_locator.fill(resolved_value, timeout=extended_timeout)
                    else:
                        target_locator.fill(resolved_value, timeout=step_data['wait_time'])

                    step_result['success'] = True
                    # 记录解析后的值（用于调试）
                    if resolved_value != step_data['input_value']:
                        step_result['resolved_value'] = resolved_value
                        print(f"  ✓ 变量解析: {step_data['input_value']} -> {resolved_value}")

                elif step_data['action_type'] == 'press':
                    target_locator.press(step_data['input_value'], timeout=step_data['wait_time'])
                    step_result['success'] = True

                elif step_data['action_type'] == 'select':
                    target_locator.select_option(value=step_data['input_value'], timeout=step_data['wait_time'])
                    step_result['success'] = True

                elif step_data['action_type'] in {'check', 'uncheck'}:
                    getattr(target_locator, step_data['action_type'])(timeout=step_data['wait_time'])
                    step_result['success'] = True


                elif step_data['action_type'] == 'getText':
                    text = target_locator.text_content(timeout=step_data['wait_time'])
                    step_result['result'] = text
                    save_name = str(step_data.get('assert_value') or '').strip()
                    if save_name:
                        self.variables.set_temporary(save_name, text)
                        step_result['saved_variable'] = save_name
                    step_result['success'] = True

                elif step_data['action_type'] == 'waitFor':
                    # 选项可先附着到 DOM 再显示；其他元素必须实际可见。
                    normalized_locator = locator_value.lower().replace(' ', '')
                    is_dropdown_option_wait = (
                        'el-select-dropdown' in normalized_locator or
                        (locator_strategy == 'role' and normalized_locator == 'option') or
                        any(role_selector in normalized_locator for role_selector in (
                            '[role="option"]', "[role='option']", '[role=option]',
                            '[@role="option"]', "[@role='option']",
                        ))
                    )

                    try:
                        if is_dropdown_option_wait:
                            target_locator.wait_for(state='attached', timeout=step_data['wait_time'])
                        else:
                            target_locator.wait_for(state='visible', timeout=step_data['wait_time'])
                        step_result['success'] = True
                    except PlaywrightTimeoutError:
                        if not (step_data.get('action_params') or {}).get('optional'):
                            raise
                        step_result['success'] = True
                        step_result['skipped'] = True
                        step_result['result'] = '可选等待目标未出现，已跳过'

                elif step_data['action_type'] == 'hover':
                    target_locator.hover(timeout=step_data['wait_time'])
                    step_result['success'] = True

                elif step_data['action_type'] == 'scroll':
                    target_locator.scroll_into_view_if_needed()
                    step_result['success'] = True

                elif step_data['action_type'] == 'screenshot':
                    screenshot_path = f'screenshots/step_{step_data["step_number"]}.png'
                    self.current_page.screenshot(path=screenshot_path)
                    step_result['screenshot'] = screenshot_path
                    step_result['success'] = True

                elif step_data['action_type'] == 'assert':
                    # 解析断言值中的变量
                    resolved_assert_value = step_data['assert_value']
                    if resolved_assert_value != step_data['assert_value']:
                        print(f"  ✓ 断言变量解析: {step_data['assert_value']} -> {resolved_assert_value}")

                    # 执行断言
                    if step_data['assert_type'] == 'textContains':
                        text = target_locator.text_content(timeout=step_data['wait_time'])
                        if resolved_assert_value in text:
                            step_result['success'] = True
                        else:
                            # 格式化为详细的错误信息，与playwright_engine.py保持一致
                            log = f"✗ 断言失败: 文本不包含 '{resolved_assert_value}'\n"
                            log += f"  - 实际文本: '{text}'"
                            step_result['error'] = log
                    elif step_data['assert_type'] == 'textEquals':
                        text = target_locator.text_content(timeout=step_data['wait_time'])
                        if text == resolved_assert_value:
                            step_result['success'] = True
                        else:
                            # 格式化为详细的错误信息
                            log = f"✗ 断言失败: 文本不等于 '{resolved_assert_value}'\n"
                            log += f"  - 期望: '{resolved_assert_value}'\n"
                            log += f"  - 实际: '{text}'"
                            step_result['error'] = log
                    elif step_data['assert_type'] == 'isVisible':
                        is_visible = target_locator.is_visible()
                        if (step_data.get('action_params') or {}).get('negate'):
                            step_result['success'] = not is_visible
                            if is_visible:
                                step_result['error'] = f"✗ 断言失败: 元素 '{element_name}' 仍然可见"
                        else:
                            step_result['success'] = is_visible
                        if not step_result['success'] and not step_result.get('error'):
                            step_result['error'] = f"✗ 断言失败: 元素 '{element_name}' 不可见"
                    elif step_data['assert_type'] == 'exists':
                        count = target_locator.count()
                        step_result['success'] = count > 0
                        if count == 0:
                            step_result['error'] = f"✗ 断言失败: 元素 '{element_name}' 不存在"
                    elif step_data['assert_type'] == 'hasAttribute':
                        attr_spec = step_data['assert_value'].strip()
                        if not attr_spec:
                            step_result['error'] = '✗ hasAttribute 断言需要指定属性名'
                        else:
                            attr_name, separator, expected_value = attr_spec.partition('=')
                            attr_name = attr_name.strip()
                            if not attr_name:
                                step_result['error'] = '✗ hasAttribute 断言需要指定属性名'
                            else:
                                actual_value = target_locator.get_attribute(
                                    attr_name, timeout=step_data['wait_time']
                                )
                                step_result['success'] = actual_value is not None and (
                                    not separator or actual_value == expected_value
                                )
                                if not step_result['success']:
                                    if separator:
                                        step_result['error'] = (
                                            f"✗ 断言失败: 属性 '{attr_name}' 值不匹配\n"
                                            f"  - 期望: '{expected_value}'\n  - 实际: '{actual_value}'"
                                        )
                                    else:
                                        step_result['error'] = (
                                            f"✗ 断言失败: 元素 '{element_name}' "
                                            f"不包含属性 '{attr_name}'"
                                        )

                elif step_data['action_type'] == 'wait':
                    self.current_page.wait_for_timeout(step_data['wait_time'])
                    step_result['success'] = True

                elif step_data['action_type'] == 'switchTab':
                    # 切换标签页 - 同步版本
                    import time as sync_time

                    # 获取超时时间
                    # 强制使用至少5秒的超时时间，确保有足够时间等待新标签页打开
                    user_wait = step_data.get('wait_time', 0) or 0
                    if user_wait > 0:
                        timeout = max(user_wait / 1000, 5.0)
                    else:
                        timeout = 5.0

                    print(f"🔄 开始执行切换标签页 (超时: {timeout}s)...")
                    start_wait = sync_time.time()
                    current_page = self.current_page
                    target_index = -1

                    # 轮询等待新标签页
                    # 轮询等待新标签页
                    while True:
                        pages = self.current_page.context.pages
                        target_index = -1  # 默认切换到最新标签页
                        should_switch = False

                        # 调试日志：打印当前页面状态
                        print(f"  [Debug] 当前页面列表 (数量: {len(pages)}):")
                        for idx, p in enumerate(pages):
                            is_current = " (Current)" if p == current_page else ""
                            try:
                                print(f"    {idx}: {p.url} - {p.title()}{is_current}")
                            except Exception as e:
                                print(f"    {idx}: [Error getting info] {str(e)}")

                        if step_data['input_value'] and str(step_data['input_value']).isdigit():
                            # 指定索引的情况
                            idx = int(step_data['input_value'])
                            if 0 <= idx < len(pages):
                                target_index = idx
                                should_switch = True
                        else:
                            # 自动模式：寻找一个不是当前页面的新页面
                            # 优先找列表末尾的（通常是新的）
                            candidates = [p for p in pages if p != current_page]
                            if candidates:
                                should_switch = True
                            elif len(pages) > 1:
                                # 如果有多个页面但都是 current_page (理论上不可能)，或者 current_page 不在 pages 里
                                # 只要页面数量增加，就应该切换
                                should_switch = True

                        if should_switch:
                            break

                        if sync_time.time() - start_wait > timeout:
                            # 超时了
                            break

                        # 关键修改：使用 wait_for_timeout 代替 time.sleep
                        # time.sleep 会阻塞线程，导致 Playwright 无法接收新页面事件
                        self.current_page.wait_for_timeout(500)

                    # 获取目标页面
                    pages = self.current_page.context.pages
                    if target_index == -1:
                        # 自动模式
                        candidates = [p for p in pages if p != current_page]
                        if candidates:
                            # 切换到最新的一个非当前页面
                            target_page = candidates[-1]
                            final_target_index = pages.index(target_page)
                        else:
                            # 如果没有找到新页面
                            if len(pages) > 1:
                                # 备选：如果有多个页面，切换到最后一个
                                target_page = pages[-1]
                                final_target_index = len(pages) - 1
                            else:
                                raise Exception(
                                    f"切换标签页失败: 在 {timeout} 秒内未检测到新标签页打开 (当前页面数: {len(pages)})")
                    else:
                        target_page = pages[target_index]
                        final_target_index = target_index

                    # 将目标页面设为当前活动页面
                    target_page.bring_to_front()

                    # 等待页面稳定
                    # 新标签页可能需要时间加载和渲染
                    try:
                        # 等待网络空闲状态（页面加载完成）
                        target_page.wait_for_load_state('networkidle', timeout=10000)  # 增加到10秒
                        print(f"  - 页面加载状态: networkidle")
                    except Exception as e:
                        # 如果networkidle超时，至少等待domcontentloaded
                        try:
                            target_page.wait_for_load_state('domcontentloaded', timeout=5000)  # 增加到5秒
                            print(f"  - 页面加载状态: domcontentloaded")
                        except Exception as e2:
                            print(f"  - 页面加载状态: 超时，继续执行 ({str(e2)[:50]})")

                    # 额外等待一小段时间，确保页面完全稳定
                    target_page.wait_for_timeout(1500)  # 使用 wait_for_timeout 代替 sleep

                    # 验证页面确实已切换
                    print(f"  - 当前活动页面URL: {target_page.url}")
                    print(f"  - 页面是否可见: {target_page.is_visible('body') if target_page else 'Unknown'}")

                    # 关键修复：直接更新实例变量！
                    self.current_page = target_page
                    step_result['switched_page'] = target_page
                    step_result['success'] = True

                    print(f"✓ 切换标签页成功")
                    print(f"  - 目标索引: {final_target_index}")
                    print(f"  - 页面标题: {self.current_page.title()}")
                    print(f"  - self.current_page已更新为新页面")

                else:
                    step_result['error'] = f'⚠ 未知的操作类型: {step_data["action_type"]}'

            else:
                # 没有元素的步骤（如等待、切换标签页）
                if step_data['action_type'] == 'wait':
                    self.current_page.wait_for_timeout(step_data['wait_time'])
                    step_result['success'] = True

                elif step_data['action_type'] == 'switchTab':
                    # 切换标签页 - 同步版本（无需元素）
                    import time as sync_time

                    # 获取超时时间
                    # 强制使用至少5秒的超时时间，确保有足够时间等待新标签页打开
                    user_wait = step_data.get('wait_time', 0) or 0
                    if user_wait > 0:
                        timeout = max(user_wait / 1000, 5.0)
                    else:
                        timeout = 5.0

                    print(f"🔄 开始执行切换标签页 (超时: {timeout}s)...")
                    start_wait = sync_time.time()
                    current_page = self.current_page
                    target_index = -1

                    # 轮询等待新标签页
                    # 轮询等待新标签页
                    while True:
                        pages = self.current_page.context.pages
                        target_index = -1  # 默认切换到最新标签页
                        should_switch = False

                        # 调试日志：打印当前页面状态
                        print(f"  [Debug] 当前页面列表 (数量: {len(pages)}):")
                        for idx, p in enumerate(pages):
                            is_current = " (Current)" if p == current_page else ""
                            try:
                                print(f"    {idx}: {p.url} - {p.title()}{is_current}")
                            except Exception as e:
                                print(f"    {idx}: [Error getting info] {str(e)}")

                        if step_data['input_value'] and str(step_data['input_value']).isdigit():
                            # 指定索引的情况
                            idx = int(step_data['input_value'])
                            if 0 <= idx < len(pages):
                                target_index = idx
                                should_switch = True
                        else:
                            # 自动模式：寻找一个不是当前页面的新页面
                            # 优先找列表末尾的（通常是新的）
                            candidates = [p for p in pages if p != current_page]
                            if candidates:
                                should_switch = True
                            elif len(pages) > 1:
                                # 如果有多个页面但都是 current_page (理论上不可能)，或者 current_page 不在 pages 里
                                # 只要页面数量增加，就应该切换
                                should_switch = True

                        if should_switch:
                            break

                        if sync_time.time() - start_wait > timeout:
                            # 超时了
                            break

                        # 关键修改：使用 wait_for_timeout 代替 time.sleep
                        self.current_page.wait_for_timeout(500)

                    # 获取目标页面
                    pages = self.current_page.context.pages
                    if target_index == -1:
                        # 自动模式
                        candidates = [p for p in pages if p != current_page]
                        if candidates:
                            # 切换到最新的一个非当前页面
                            target_page = candidates[-1]
                            final_target_index = pages.index(target_page)
                        else:
                            # 如果没有找到新页面
                            if len(pages) > 1:
                                # 备选：如果有多个页面，切换到最后一个
                                target_page = pages[-1]
                                final_target_index = len(pages) - 1
                            else:
                                raise Exception(
                                    f"切换标签页失败: 在 {timeout} 秒内未检测到新标签页打开 (当前页面数: {len(pages)})")
                    else:
                        target_page = pages[target_index]
                        final_target_index = target_index

                    # 将目标页面设为当前活动页面
                    target_page.bring_to_front()

                    # 等待页面稳定
                    try:
                        # 等待网络空闲状态（页面加载完成）
                        target_page.wait_for_load_state('networkidle', timeout=10000)  # 增加到10秒
                        print(f"  - 页面加载状态: networkidle")
                    except Exception as e:
                        # 如果networkidle超时，至少等待domcontentloaded
                        try:
                            target_page.wait_for_load_state('domcontentloaded', timeout=5000)  # 增加到5秒
                            print(f"  - 页面加载状态: domcontentloaded")
                        except Exception as e2:
                            print(f"  - 页面加载状态: 超时，继续执行 ({str(e2)[:50]})")

                    # 额外等待一小段时间，确保页面完全稳定
                    target_page.wait_for_timeout(1500)  # 使用 wait_for_timeout 代替 sleep

                    # 验证页面确实已切换
                    print(f"  - 当前活动页面URL: {target_page.url}")
                    print(f"  - 页面是否可见: {target_page.is_visible('body') if target_page else 'Unknown'}")

                    # 关键修复：直接更新实例变量！
                    self.current_page = target_page
                    step_result['switched_page'] = target_page
                    step_result['success'] = True

                    print(f"✓ 切换标签页成功")
                    print(f"  - 目标索引: {final_target_index}")
                    print(f"  - 页面标题: {self.current_page.title()}")
                    print(f"  - self.current_page已更新为新页面")

        except Exception as e:
            # 格式化为详细的错误信息，与playwright_engine.py保持一致
            execution_time = round(time.time() - start_time, 2)

            # 提取详细的错误信息（改进版）
            error_str = str(e)
            error_type = type(e).__name__

            # 尝试提取更详细的 Playwright 异常信息
            try:
                # Playwright 异常可能包含更详细的信息
                if hasattr(e, 'message') and e.message:
                    error_str = e.message
                # TimeoutError 通常有更详细的描述
                elif hasattr(e, 'args') and e.args:
                    error_str = str(e.args[0]) if len(e.args) > 0 else error_str
            except:
                pass  # 如果提取失败，使用原始 error_str

            # 添加异常类型信息（如果还没有）
            if error_type not in error_str and error_type != 'Exception':
                error_str = f"{error_type}: {error_str}"

            # 判断是否是超时错误
            if 'Timeout' in error_str or 'timeout' in error_str:
                element_name = step_data['element'].get('name', '未知元素') if step_data.get('element') else '页面'
                locator_info = f"{step_data['element']['locator_strategy']}={step_data['element']['locator_value']}" if step_data.get(
                    'element') else '无'

                log = f"✗ 操作超时\n"
                log += f"  - 元素: '{element_name}'\n"
                log += f"  - 定位器: {locator_info}\n"
                log += f"  - 超时时间: {execution_time}秒\n"
                log += f"  - 错误: {error_str}"
                step_result['error'] = log
            else:
                element_name = step_data['element'].get('name', '未知元素') if step_data.get('element') else '页面'
                locator_info = f"{step_data['element']['locator_strategy']}={step_data['element']['locator_value']}" if step_data.get(
                    'element') else '无'

                log = f"✗ 执行失败\n"
                log += f"  - 元素: '{element_name}'\n"
                log += f"  - 定位器: {locator_info}\n"
                log += f"  - 执行时间: {execution_time}秒\n"
                log += f"  - 错误: {error_str}"
                step_result['error'] = log

            # 打印详细日志便于调试
            print(f"❌ Playwright 步骤执行失败:")
            print(f"   异常类型: {error_type}")
            print(f"   错误信息: {error_str[:500]}")  # 限制长度避免刷屏

        return step_result

    def _execute_storage_state_action(self, params):
        """在同步 Playwright 上下文中执行保存、切换或清除登录态。"""
        import json
        import tempfile
        from pathlib import Path
        from .models import StorageState

        operation = params.get('operation')
        project_id = getattr(self, '_active_project_id', None)
        # 保存当前上下文的 Cookie 和 localStorage，并写回项目登录态资源。
        if operation == 'save':
            name = str(params.get('name') or '').strip()
            mode = params.get('mode', 'save_as')
            with tempfile.NamedTemporaryFile(suffix='.json') as state_file:
                self.context.storage_state(path=state_file.name)
                raw = Path(state_file.name).read_bytes()
            if mode == 'overwrite':
                target = StorageState.objects.filter(pk=params.get('storage_state_id'), project_id=project_id).first()
                if not target:
                    raise ValueError('要覆盖的登录态不存在或不属于当前项目')
            else:
                target = StorageState(project_id=project_id, name=name, source_url=self.current_page.url, browser='chromium', created_by=self.executed_by)
            if not name and mode != 'overwrite':
                raise ValueError('保存登录态需要填写名称')
            if name:
                target.name = name
            target.source_url = self.current_page.url
            target.write_content(raw)
            return target
        # 切换和清除都重建上下文，保证认证数据按步骤隔离。
        if operation in {'switch', 'clear'}:
            url = self.current_page.url
            old_context = self.context
            browser = self.browser_instance
            state_path = None
            if operation == 'switch':
                target_id = params.get('storage_state_id')
                target = StorageState.objects.filter(pk=target_id, project_id=project_id).first()
                if not target:
                    raise ValueError('目标登录态不存在或不属于当前项目')
                state_path = target.file.path
            old_context.close()
            self.context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                storage_state=state_path,
            ) if state_path else browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            )
            self.current_page = self.context.new_page()
            # clear 后由后续 navigate 步骤进入公开页面，避免回访受保护 URL 触发重定向循环。
            if url and url != 'about:blank' and params.get('navigate_current', True):
                self.current_page.goto(url, wait_until='domcontentloaded', timeout=30000)
            return
        raise ValueError('未知的登录态操作')

    def run_with_selenium(self):
        """使用 Selenium 执行测试"""
        start_time = time.time()
        passed = 0
        failed = 0
        skipped = 0

        # 预先获取所有测试用例的步骤数据，避免在Selenium上下文中访问ORM
        test_cases_data = [self._build_case_data(tc) for tc in self.test_cases]

        # 预先创建所有测试用例执行记录（不设置 started_at，等实际执行时再设置）
        case_executions = {}
        for case_data in test_cases_data:
            case_execution = TestCaseExecution.objects.create(
                test_case_id=case_data['id'],
                project_id=case_data['project_id'],
                test_suite=self.test_suite,
                suite_execution=self.suite_execution_ref,
                execution_source='suite',
                status='pending',  # 初始状态为 pending
                engine=self.engine,
                browser=self.browser,
                headless=self.headless,
                created_by=self.executed_by
                # 注意：不设置 started_at，等用例实际开始执行时再设置
            )
            case_executions[case_data['id']] = case_execution

        # 优化：整个测试套件共用一个浏览器实例，避免频繁启动/关闭
        # 注意：Safari 不支持浏览器复用（会话管理问题），需要每个用例独立启动
        print(f"准备执行 {len(test_cases_data)} 个测试用例")

        # Safari 需要独立浏览器实例，其他浏览器可以复用
        use_browser_reuse = self.browser != 'safari'

        if use_browser_reuse:
            # 在套件开始时启动一次浏览器（Chrome/Firefox/Edge）
            driver = None
            try:
                driver = self.create_selenium_driver()
                print(f"✓ 浏览器已启动（将复用于所有用例）\n")
            except Exception as e:
                print(f"✗ 浏览器启动失败: {str(e)}")
                # 标记所有用例为失败
                for case_data in test_cases_data:
                    self.results.append({
                        'test_case_id': case_data['id'],
                        'test_case_name': case_data['name'],
                        'status': 'failed',
                        'steps': [],
                        'error': f"浏览器启动失败: {str(e)}",
                        'start_time': datetime.now().isoformat(),
                        'end_time': datetime.now().isoformat(),
                        'screenshots': []
                    })
                    failed += 1
                # 更新执行记录并返回
                for case_result in self.results:
                    case_execution = case_executions[case_result['test_case_id']]
                    case_execution.status = 'failed'
                    case_execution.finished_at = timezone.now()
                    case_execution.execution_time = 0
                    case_execution.error_message = case_result['error']
                    case_execution.save()
                duration = time.time() - start_time
                self.update_execution_result('FAILED', 0, len(test_cases_data), 0, duration)
                return
        else:
            # Safari：不预先启动浏览器，每个用例独立启动
            driver = None
            print(f"ℹ️  Safari 浏览器将为每个用例独立启动（Safari 不支持浏览器复用）\n")

        # 执行所有测试用例
        for i, case_data in enumerate(test_cases_data, 1):
            print(f"\n{'=' * 60}")
            print(f"正在执行第 {i}/{len(test_cases_data)} 个用例: {case_data['name']}")
            print(f"{'=' * 60}")

            # 记录用例实际开始执行时间
            case_execution = case_executions[case_data['id']]
            case_execution.started_at = timezone.now()
            case_execution.status = 'running'
            case_execution.save()

            # Safari：为每个用例启动新的浏览器
            if not use_browser_reuse:
                try:
                    driver = self.create_selenium_driver()
                    print(f"✓ Safari 浏览器已启动")
                except Exception as e:
                    print(f"✗ Safari 浏览器启动失败: {str(e)}")
                    self.results.append({
                        'test_case_id': case_data['id'],
                        'test_case_name': case_data['name'],
                        'status': 'failed',
                        'steps': [],
                        'error': f"浏览器启动失败: {str(e)}",
                        'start_time': datetime.now().isoformat(),
                        'end_time': datetime.now().isoformat(),
                        'screenshots': []
                    })
                    failed += 1
                    # 更新执行记录
                    case_execution.status = 'failed'
                    case_execution.finished_at = timezone.now()
                    case_execution.execution_time = (
                                case_execution.finished_at - case_execution.started_at).total_seconds()
                    case_execution.error_message = f"浏览器启动失败: {str(e)}"
                    case_execution.save()
                    continue

            try:
                # 在每个用例开始前清理浏览器状态（仅对复用浏览器的情况，且跳过第1个用例）
                # 第1个用例浏览器刚启动，无需清理；从第2个用例开始才需要清理
                if use_browser_reuse and i > 1:
                    try:
                        print(f"🧹 清理浏览器状态...")
                        # 清除所有 Cookie
                        driver.delete_all_cookies()
                        # 清除 localStorage 和 sessionStorage
                        driver.execute_script("window.localStorage.clear();")
                        driver.execute_script("window.sessionStorage.clear();")
                        print(f"✓ 浏览器状态已清理")
                    except Exception as clean_error:
                        print(f"⚠️  清理浏览器状态失败: {str(clean_error)}，继续执行...")
                        pass  # 如果清理失败，继续执行

                # 导航到项目基础URL
                if self.project.base_url:
                    try:
                        print(f"正在导航到: {self.project.base_url}")

                        # 检测是否在Linux服务器环境
                        import platform
                        is_linux = platform.system() == 'Linux'

                        # 导航到URL
                        driver.get(self.project.base_url)

                        # 等待页面基本加载完成
                        # 在服务器环境（特别是无头模式）需要更长的等待时间
                        try:
                            WebDriverWait(driver, 15 if is_linux else 10).until(
                                lambda d: d.execute_script("return document.readyState") == "complete"
                            )
                        except:
                            pass  # 即使超时也继续执行

                        # 额外等待，确保动态内容加载（Vue/React等SPA应用）
                        extra_wait = 3 if is_linux else 2
                        time.sleep(extra_wait)

                        print(
                            f"✓ 成功导航到: {self.project.base_url} (已等待页面加载完成，额外{extra_wait}秒)")
                    except Exception as e:
                        print(f"✗ 导航失败: {str(e)}")
                        # 导航失败，记录错误并继续下一个用例
                        self.results.append({
                            'test_case_id': case_data['id'],
                            'test_case_name': case_data['name'],
                            'status': 'failed',
                            'steps': [],
                            'error': f"导航到基础URL失败: {str(e)}",
                            'start_time': datetime.now().isoformat(),
                            'end_time': datetime.now().isoformat(),
                            'screenshots': []
                        })
                        failed += 1
                        continue

                # 执行测试用例
                case_result = self.execute_test_case_selenium_no_db(driver, case_data)
                self.results.append(case_result)
                print(f"✓ 用例执行完成，状态: {case_result['status']}")

                # 立即更新该用例的执行记录（包含准确的执行时间）
                case_execution = case_executions[case_data['id']]
                case_execution.status = case_result['status']
                case_execution.finished_at = timezone.now()
                case_execution.execution_time = (case_execution.finished_at - case_execution.started_at).total_seconds()
                case_execution.execution_logs = json.dumps(case_result['steps'], ensure_ascii=False)
                if case_result['error']:
                    case_execution.error_message = case_result['error']
                if case_result.get('screenshots'):
                    case_execution.screenshots = case_result['screenshots']
                case_execution.save()

                print(f"⏱️  执行时长: {case_execution.execution_time:.2f}秒")

                if case_result['status'] == 'passed':
                    passed += 1
                elif case_result['status'] == 'failed':
                    failed += 1
                else:
                    skipped += 1

            except Exception as e:
                print(f"✗ 用例执行出现异常: {str(e)}")
                # 记录异常
                self.results.append({
                    'test_case_id': case_data['id'],
                    'test_case_name': case_data['name'],
                    'status': 'failed',
                    'steps': [],
                    'error': f"用例执行异常: {str(e)}",
                    'start_time': datetime.now().isoformat(),
                    'end_time': datetime.now().isoformat(),
                    'screenshots': []
                })
                failed += 1

                # 更新执行记录
                case_execution = case_executions[case_data['id']]
                case_execution.status = 'failed'
                case_execution.finished_at = timezone.now()
                case_execution.execution_time = (case_execution.finished_at - case_execution.started_at).total_seconds()
                case_execution.error_message = f"用例执行异常: {str(e)}"
                case_execution.save()

            finally:
                # Safari：每个用例执行完都关闭浏览器
                if not use_browser_reuse and driver:
                    try:
                        driver.quit()
                        print(f"✓ Safari 浏览器已关闭\n")
                    except Exception as e:
                        print(f"✗ 关闭 Safari 浏览器时出错: {str(e)}\n")
                    driver = None

        # 所有用例执行完毕后，关闭浏览器（仅对复用浏览器的情况）
        if use_browser_reuse and driver:
            try:
                print(f"\n{'=' * 60}")
                print(f"正在关闭浏览器...")
                driver.quit()
                print(f"✓ 浏览器已关闭")
                print(f"{'=' * 60}\n")
            except Exception as e:
                print(f"✗ 关闭浏览器时出错: {str(e)}")

        # 注意：每个用例的执行记录已在执行过程中实时更新，不需要在这里统一更新

        duration = time.time() - start_time
        status = 'SUCCESS' if failed == 0 else 'FAILED'
        self.update_execution_result(status, passed, failed, skipped, duration)

    def create_selenium_driver(self):
        """创建 Selenium WebDriver"""
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.webdriver.firefox.service import Service as FirefoxService
        from selenium.webdriver.edge.service import Service as EdgeService
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.firefox import GeckoDriverManager
        from webdriver_manager.microsoft import EdgeChromiumDriverManager
        from apps.ui_automation.selenium_engine import SeleniumTestEngine
        import os

        # 配置webdriver_manager使用本地缓存，避免每次下载
        # 缓存目录：~/.wdm
        os.environ['WDM_LOG_LEVEL'] = '0'  # 减少日志输出
        os.environ['WDM_PRINT_FIRST_LINE'] = 'False'  # 不打印首行信息

        # 检查浏览器和驱动是否可用
        is_ready, error_msg = SeleniumTestEngine.check_execution_environment(self.browser)
        if not is_ready:
            # 提供安装建议
            install_tips = {
                'chrome': 'brew install --cask google-chrome',
                'firefox': 'brew install --cask firefox',
                'edge': 'brew install --cask microsoft-edge',
            }
            tip = install_tips.get(self.browser, '')
            full_error = f"{error_msg}\n\n💡 安装命令（macOS）：{tip}" if tip else error_msg
            raise Exception(full_error)

        if self.browser == 'chrome':
            options = ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')

            # 禁用自动化特征检测
            options.add_experimental_option('excludeSwitches', ['enable-automation', 'enable-logging'])
            options.add_experimental_option('useAutomationExtension', False)

            # 禁用密码保存和泄露提醒（解决弹框遮挡元素的问题）
            prefs = {
                'credentials_enable_service': False,  # 禁用密码保存服务
                'profile.password_manager_enabled': False,  # 禁用密码管理器
                'profile.default_content_setting_values.notifications': 2,  # 禁用通知
                'autofill.profile_enabled': False,  # 禁用自动填充
                'profile.default_content_setting_values.automatic_downloads': 1,  # 允许自动下载
                'password_manager_leak_detection': False,  # 禁用密码泄露检测（prefs级别）
                'safebrowsing.enabled': False,  # 禁用安全浏览
                'safebrowsing.disable_download_protection': True,
                'intl.accept_languages': 'zh-CN,zh,en-US,en',  # 设置语言
                'profile.exit_type': 'Normal',  # 避免"Chrome未正常关闭"提示
            }
            options.add_experimental_option('prefs', prefs)

            # 禁用密码泄露检查和其他安全警告（更全面的设置）
            # 将所有 disable-features 合并为一个参数，避免覆盖
            disabled_features = [
                'PasswordLeakDetection',
                'PrivacySandboxSettings4',
                'TranslateUI',
                'SavePasswordBubble',
                'AutofillServerCommunication',
                'CreditCardSave',
                'HeaderUI',
                'AccountConsistency',
            ]
            options.add_argument(f'--disable-features={",".join(disabled_features)}')

            options.add_argument('--disable-infobars')  # 禁用信息栏
            options.add_argument('--disable-save-password-bubble')  # 禁用保存密码气泡
            options.add_argument('--disable-password-generation')  # 禁用密码生成
            options.add_argument('--disable-password-manager-reauthentication')  # 禁用密码管理器重新认证
            options.add_argument('--disable-popup-blocking')  # 禁用弹窗拦截
            options.add_argument('--disable-notifications')  # 禁用所有通知
            options.add_argument('--no-default-browser-check')  # 禁用默认浏览器检查
            options.add_argument('--no-first-run')  # 禁用首次运行界面

            # 针对密码弹窗的额外参数
            options.add_argument('--password-store=basic')
            options.add_argument('--use-mock-keychain')
            options.add_argument('--disable-background-timer-throttling')
            options.add_argument('--disable-renderer-backgrounding')
            options.add_argument('--disable-device-discovery-notifications')

            # 使用缓存优先策略
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        elif self.browser == 'firefox':
            options = FirefoxOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--width=1920')
            options.add_argument('--height=1080')

            # 性能优化：禁用不必要的功能加快启动速度
            options.set_preference('browser.cache.disk.enable', False)
            options.set_preference('browser.cache.memory.enable', True)
            options.set_preference('browser.cache.offline.enable', False)
            options.set_preference('network.http.use-cache', False)
            options.set_preference('browser.startup.homepage', 'about:blank')
            options.set_preference('startup.homepage_welcome_url', 'about:blank')
            options.set_preference('startup.homepage_welcome_url.additional', 'about:blank')
            # 禁用自动更新检查
            options.set_preference('app.update.auto', False)
            options.set_preference('app.update.enabled', False)
            # 禁用扩展和插件检查
            options.set_preference('extensions.update.enabled', False)
            options.set_preference('extensions.update.autoUpdateDefault', False)

            # 使用缓存优先策略
            service = FirefoxService(GeckoDriverManager().install())
            driver = webdriver.Firefox(service=service, options=options)
        elif self.browser == 'safari':
            # Safari 不支持 headless 模式
            # 需要先启用：sudo safaridriver --enable
            # 并在 Safari 设置 -> 开发菜单中启用"允许远程自动化"
            try:
                driver = webdriver.Safari()
                driver.set_window_size(1920, 1080)
            except Exception as e:
                error_msg = str(e)
                if 'Could not create a session' in error_msg or 'InvalidSessionIdException' in error_msg:
                    raise Exception(
                        "Safari 远程自动化未启用。\n\n"
                        "请按以下步骤配置：\n"
                        "1. 在终端执行: sudo safaridriver --enable\n"
                        "2. 打开 Safari → 设置 → 高级 → 勾选'在菜单栏中显示开发菜单'\n"
                        "3. Safari 菜单栏 → 开发 → 勾选'允许远程自动化'\n\n"
                        f"原始错误: {error_msg}"
                    )
                raise
        elif self.browser == 'edge':
            options = EdgeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')

            # 使用缓存优先策略
            service = EdgeService(EdgeChromiumDriverManager().install())
            driver = webdriver.Edge(service=service, options=options)
        else:
            # 默认使用Chrome
            options = ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')

            # 禁用自动化特征检测
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)

            # 禁用密码保存和泄露提醒（解决弹框遮挡元素的问题）
            prefs = {
                'credentials_enable_service': False,  # 禁用密码保存服务
                'profile.password_manager_enabled': False,  # 禁用密码管理器
                'profile.default_content_setting_values.notifications': 2,  # 禁用通知
                'autofill.profile_enabled': False,  # 禁用自动填充
                'profile.default_content_setting_values.automatic_downloads': 1,  # 允许自动下载
            }
            options.add_experimental_option('prefs', prefs)

            # 禁用密码泄露检查和其他安全警告
            options.add_argument('--disable-features=PasswordLeakDetection')  # 禁用密码泄露检测
            options.add_argument('--disable-features=PrivacySandboxSettings4')  # 禁用隐私沙盒
            options.add_argument('--disable-features=TranslateUI')  # 禁用翻译提示
            options.add_argument('--disable-infobars')  # 禁用信息栏

            # 使用缓存优先策略
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)

        return driver

    def execute_test_case_selenium_no_db(self, driver, case_data):
        """使用 Selenium 执行单个测试用例（不访问数据库）

        Args:
            driver: Selenium WebDriver对象
            case_data: 预先准备的用例数据字典，包含id, name, project_id, steps等
        """
        result = {
            'test_case_id': case_data['id'],
            'test_case_name': case_data['name'],
            'status': 'passed',
            'steps': [],
            'error': None,
            'start_time': datetime.now().isoformat(),
            'screenshots': []
        }

        try:
            # Selenium 与 Playwright 共用严格解析规则，渲染结果只用于当前步骤。
            # 遍历预先准备好的步骤数据
            for step_data in case_data['steps']:
                step_result = self.execute_step_selenium(driver, step_data)
                result['steps'].append(step_result)

                # 步骤执行完后添加短暂延迟，确保页面状态稳定
                # 特别是点击操作后，可能触发动画、下拉框展开等
                if step_result['success'] and step_data['action_type'] in ['click', 'fill', 'hover']:
                    # 点击操作后等待更长时间（下拉框展开动画）
                    if step_data['action_type'] == 'click':
                        time.sleep(0.8)  # 等待800ms，确保下拉框完全展开
                    else:
                        time.sleep(0.3)  # 其他操作等待300ms

                # 如果步骤失败,捕获失败截图
                if not step_result['success']:
                    result['status'] = 'failed'
                    # 使用step的error信息作为case的error
                    result['error'] = step_result.get('error', f"步骤 {step_data['step_number']} 执行失败")

                    # 捕获失败截图
                    try:
                        import base64
                        screenshot_bytes = driver.get_screenshot_as_png()
                        screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
                        result['screenshots'].append({
                            'url': f'data:image/png;base64,{screenshot_base64}',
                            'description': f'步骤 {step_data["step_number"]} 失败截图: {step_data.get("description", "")}',
                            'step_number': step_data['step_number'],
                            'timestamp': datetime.now().isoformat()
                        })
                    except Exception as screenshot_error:
                        print(f"捕获失败截图失败: {str(screenshot_error)}")

                    break

        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)

            # 捕获异常截图
            try:
                import base64
                screenshot_bytes = driver.get_screenshot_as_png()
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
                result['screenshots'].append({
                    'url': f'data:image/png;base64,{screenshot_base64}',
                    'description': f'异常截图: {str(e)}',
                    'step_number': None,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as screenshot_error:
                print(f"捕获异常截图失败: {str(screenshot_error)}")

        result['end_time'] = datetime.now().isoformat()
        self.variables.clear_temporary()
        return result

    def execute_test_case_selenium(self, driver, case_data):
        """使用 Selenium 执行单个测试用例 - 已弃用，保留用于���后兼容

        Args:
            driver: Selenium WebDriver对象
            case_data: 预先准备的用例数据字典，包含id, name, project_id, steps等
        """
        result = {
            'test_case_id': case_data['id'],
            'test_case_name': case_data['name'],
            'status': 'passed',
            'steps': [],
            'error': None,
            'start_time': datetime.now().isoformat()
        }

        case_execution = TestCaseExecution.objects.create(
            test_case_id=case_data['id'],
            project_id=case_data['project_id'],
            status='running',
            browser=self.browser,
            created_by=self.executed_by,
            started_at=timezone.now()
        )

        try:
            # 历史入口也使用同一上下文，避免绕过变量解析和脱敏逻辑。
            # 遍历预先准备好的步骤数据
            for step_data in case_data['steps']:
                step_result = self.execute_step_selenium(driver, step_data)
                result['steps'].append(step_result)

                if not step_result['success']:
                    result['status'] = 'failed'
                    # 使用step的error信息作为case的error
                    result['error'] = step_result.get('error', f"步骤 {step_data['step_number']} 执行失败")
                    break

            # 更新用例执行记录
            case_execution.status = result['status']
            case_execution.finished_at = timezone.now()
            case_execution.execution_time = (case_execution.finished_at - case_execution.started_at).total_seconds()
            case_execution.execution_logs = json.dumps(result['steps'], ensure_ascii=False)
            if result['error']:
                case_execution.error_message = result['error']
            case_execution.save()

        except Exception as e:
            result['status'] = 'failed'
            result['error'] = str(e)

            case_execution.status = 'error'
            case_execution.error_message = str(e)
            case_execution.finished_at = timezone.now()
            case_execution.save()

        result['end_time'] = datetime.now().isoformat()
        self.variables.clear_temporary()
        return result

    def execute_step_selenium(self, driver, step_data):
        """使用 Selenium 执行单个步骤"""
        result = self._execute_step_selenium(driver, step_data)
        if self.variables.strict:
            # 保存脱敏后的项目变量快照，不把运行时凭据写入执行日志。
            result['variables'] = self.variables.snapshot()
        for key in ('error', 'result', 'resolved_value'):
            if isinstance(result.get(key), str):
                result[key] = self.variables.redact(result[key])
        return result

    def _execute_step_selenium(self, driver, step_data):
        """使用 Selenium 执行单个步骤

        Args:
            driver: Selenium WebDriver对象
            step_data: 预先准备的步骤数据字典
        """
        from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
        start_time = time.time()

        step_result = {
            'step_number': step_data['step_number'],
            'action_type': step_data['action_type'],
            'description': step_data['description'],
            'success': False,
            'error': None
        }

        element_name = step_data.get('element', {}).get('name', '页面') if step_data.get('element') else '页面'
        locator_strategy = step_data.get('element', {}).get('locator_strategy', 'css') if step_data.get('element') else 'css'
        locator_value = step_data.get('element', {}).get('locator_value', '') if step_data.get('element') else ''

        try:
            # 解析输入、断言和定位器；更新副本，不改写数据库中的步骤定义。
            step_data = self.variables.render_step(step_data)
            variable_result = self.variables.apply_step(step_data)
            if variable_result is not None:
                step_result['success'], step_result['result'] = variable_result[0], variable_result[1]
                return step_result
            for key in ('input_value', 'assert_value'):
                if key in step_data:
                    step_data[key] = as_text(step_data[key])
            if step_data.get('element'):
                step_data['element']['locator_value'] = as_text(step_data['element']['locator_value'])
            if step_data['action_type'] == 'navigate':
                url = step_data.get('input_value', '')
                if not url:
                    step_result['error'] = '跳转URL失败: URL不能为空'
                else:
                    driver.get(url)
                    step_result['success'] = True
            elif step_data['action_type'] == 'assert' and step_data.get('assert_type') == 'urlContains':
                expected_url = step_data.get('assert_value', '')
                actual_url = driver.current_url
                step_result['success'] = bool(expected_url and expected_url in actual_url)
                if not step_result['success']:
                    step_result['error'] = f"✗ 断言失败: URL不包含 '{expected_url}'\n  - 实际URL: '{actual_url}'"
            elif step_data['element']:
                element = step_data['element']
                locator_value = element['locator_value']
                locator_strategy = element['locator_strategy'].lower()
                element_name = element.get('name', '未知元素')

                # 根据定位策略获取元素
                wait = WebDriverWait(driver, step_data['wait_time'] / 1000)

                # 自动修正定位策略：如果值以 // 开头，强制使用 XPath
                if locator_value.startswith('//') or locator_value.startswith('xpath='):
                    locator_strategy = 'xpath'
                    if locator_value.startswith('xpath='):
                        locator_value = locator_value[6:]

                # 根据定位策略构造 Playwright 选择器
                if locator_strategy in ['css', 'css selector']:
                    by = By.CSS_SELECTOR
                elif locator_strategy == 'xpath':
                    by = By.XPATH
                elif locator_strategy == 'id':
                    by = By.ID
                elif locator_strategy == 'name':
                    by = By.NAME
                elif locator_strategy in ['class', 'class name']:
                    by = By.CLASS_NAME
                elif locator_strategy in ['tag', 'tag name']:
                    by = By.TAG_NAME
                elif locator_strategy == 'link text':
                    by = By.LINK_TEXT
                elif locator_strategy == 'partial link text':
                    by = By.PARTIAL_LINK_TEXT
                else:
                    by = By.CSS_SELECTOR

                # 定义重试次数（用于所有操作类型）
                max_retries = 3

                # 根据操作类型选择合适的等待条件
                if step_data['action_type'] == 'click':
                    # 检测是否是原生HTML select的option元素（优先检测）
                    is_native_select_option = (
                            (
                                        'option[' in locator_value or ' > option' in locator_value or '//option' in locator_value) or
                            ('select' in locator_value.lower() and 'option' in locator_value.lower())
                    )

                    # 对于原生HTML select的option，使用Selenium的select类
                    if is_native_select_option:
                        from selenium.webdriver.support.ui import Select
                        print(f"[Selenium-调试] 检测到原生HTML select元素，使用Select类...")

                        # 提取option的value值
                        import re
                        option_value_match = re.search(r'option\[value=["\']([^"\']+)["\']\]', locator_value)
                        option_value_xpath_match = re.search(r'option\[@value=["\']([^"\']+)["\']\]', locator_value)

                        option_value = None
                        if option_value_match:
                            option_value = option_value_match.group(1)
                        elif option_value_xpath_match:
                            option_value = option_value_xpath_match.group(1)
                        else:
                            option_value = '1'  # 默认值

                        # 构造select元素的定位器（去掉option部分）
                        select_locator_value = re.sub(r'\s*>\s*option\[.*?\]', '', locator_value)
                        select_locator_value = re.sub(r'\s+option\[.*?\]', '', select_locator_value)
                        select_locator_value = re.sub(r'//option\[.*?\]', '', select_locator_value)

                        print(f"[Selenium-调试] Select定位器: {select_locator_value}, Option值: {option_value}")

                        try:
                            # 查找select元素
                            select_element = wait.until(EC.presence_of_element_located((by, select_locator_value)))

                            # 使用Select类选择选项
                            select_obj = Select(select_element)
                            select_obj.select_by_value(option_value)

                            step_result['success'] = True
                            print(f"✓ 选择下拉框选项成功 (Select.select_by_value)")
                            # 成功处理select，跳过后续逻辑
                            native_select_handled = True
                        except Exception as e:
                            print(f"✗ Select类失败: {e}")
                            # 如果失败，继续尝试普通点击
                            native_select_handled = False
                    else:
                        native_select_handled = False

                    # 只有当原生select处理失败或不是原生select时，才继续后续逻辑
                    if not native_select_handled:
                        # 点击操作：等待元素可点击（解决 stale element 问题）
                        # 通过定位器特征自动识别下拉框选项
                        is_dropdown_option = (
                                'dropdown' in locator_value.lower() or
                                'el-select' in locator_value.lower() or
                                'role="option"' in element_name.lower() or
                                '下拉' in element_name or
                                '选项' in element_name or
                                'el-select-dropdown__item' in locator_value.lower() or
                                ('//li' in locator_value and 'span=' in locator_value)  # XPath 下拉框模式
                        )

                        if is_dropdown_option:
                            # 下拉框选项：特殊处理，遍历所有匹配元素找到可见的那个
                            print(f"  检测到下拉框选项（定位器匹配），尝试查找可见元素...")

                            # 自定义等待逻辑：轮询查找可见元素
                            end_time = time.time() + (step_data['wait_time'] / 1000)
                            found_visible = False

                            while time.time() < end_time:
                                try:
                                    # 查找所有匹配元素
                                    elements = driver.find_elements(by, locator_value)
                                    for el in elements:
                                        if el.is_displayed():
                                            element_obj = el
                                            found_visible = True
                                            print(f"  ✓ 找到可见的下拉框选项")
                                            break

                                    if found_visible:
                                        break

                                    time.sleep(0.5)
                                except:
                                    time.sleep(0.5)

                            if not found_visible:
                                # 如果没找到可见元素，回退到默认行为（可能会抛出超时）
                                print(f"  ⚠️ 未找到可见的下拉框选项，尝试默认等待...")
                                element_obj = wait.until(EC.visibility_of_element_located((by, locator_value)))
                        else:
                            element_obj = wait.until(EC.element_to_be_clickable((by, locator_value)))
                    else:
                        # 其他操作：等待元素出现
                        element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))

                    # click操作的实际执行逻辑（使用 stale element 重试机制）
                    for attempt in range(max_retries):
                        try:
                            # 每次重试都重新查找元素（解决stale element问题）
                            if attempt > 0:
                                print(f"⚠️  重新查找元素（Stale Element 重试）... (尝试 {attempt + 1}/{max_retries})")
                                # 增加等待时间，让页面 DOM 稳定（对于 Vue/React 应用很重要）
                                wait_time = 1.0 if attempt == 1 else 1.5  # 第一次重试等1秒，第二次重试等1.5秒
                                print(f"等待 {wait_time}秒 让页面稳定...")
                                time.sleep(wait_time)
                                # 重新定位元素
                                if is_dropdown_option:
                                    element_obj = wait.until(EC.visibility_of_element_located((by, locator_value)))
                                else:
                                    element_obj = wait.until(EC.element_to_be_clickable((by, locator_value)))
                                # 等待元素状态稳定
                                time.sleep(0.3)
                                print(f"✓ 元素重新定位成功")

                            # 对于下拉框选项，先滚动到可视区域
                            if 'dropdown' in locator_value.lower() or 'el-select' in locator_value.lower() or '下拉' in element_name or '选项' in element_name:
                                try:
                                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});",
                                                          element_obj)
                                    time.sleep(0.3)  # 等待滚动完成
                                except:
                                    pass

                            # 如果是 el-select 容器，尝试点击内部的可点击区域
                            if 'el-select' in locator_value.lower() and 'ancestor::' in locator_value.lower():
                                # 这是点击 el-select 容器，需要找到真正的触发器
                                try:
                                    # 尝试找到并点击内部的 input 或 wrapper
                                    clickable = element_obj.find_element(By.CSS_SELECTOR, '.el-select__wrapper, input')
                                    clickable.click()
                                except:
                                    # 如果找不到，直接点击容器
                                    element_obj.click()
                            else:
                                element_obj.click()

                            step_result['success'] = True
                            break
                        except StaleElementReferenceException:
                            if attempt < max_retries - 1:
                                print(f"⚠️  元素过期，正在重试... ({attempt + 1}/{max_retries})")
                                # 继续下一次循环，会重新查找元素
                                continue
                            else:
                                raise
                        except Exception as click_error:
                            # 如果是下拉框选项且点击失败，尝试使用 JavaScript 点击
                            if attempt < max_retries - 1 and (
                                    'not visible' in str(click_error).lower() or 'not interactable' in str(
                                    click_error).lower()):
                                print(f"⚠️  元素不可交互，尝试使用 JavaScript 点击... ({attempt + 1}/{max_retries})")
                                try:
                                    driver.execute_script("arguments[0].click();", element_obj)
                                    step_result['success'] = True
                                    break
                                except:
                                    if attempt < max_retries - 1:
                                        time.sleep(0.5)
                                        # 重新定位
                                        if 'dropdown' in locator_value.lower() or 'el-select' in locator_value.lower():
                                            element_obj = wait.until(
                                                EC.visibility_of_element_located((by, locator_value)))
                                        else:
                                            element_obj = wait.until(EC.element_to_be_clickable((by, locator_value)))
                                    else:
                                        raise
                            else:
                                raise

                elif step_data['action_type'] == 'fill':
                    # 先定位元素
                    element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                    
                    # 解析输入值中的变量表达式
                    resolved_value = step_data['input_value']

                    for attempt in range(max_retries):
                        try:
                            element_obj.clear()
                            element_obj.send_keys(resolved_value)
                            step_result['success'] = True

                            # 记录解析后的值（用于调试）
                            if resolved_value != step_data['input_value']:
                                step_result['resolved_value'] = resolved_value
                                print(f"  ✓ 变量解析: {step_data['input_value']} -> {resolved_value}")

                            break
                        except StaleElementReferenceException:
                            if attempt < max_retries - 1:
                                print(f"⚠️  元素过期（Stale Element），正在重试... (尝试 {attempt + 2}/{max_retries})")
                                # 增加等待时间，让页面 DOM 稳定
                                wait_time = 1.0 if attempt == 0 else 1.5
                                print(f"等待 {wait_time}秒 让页面稳定...")
                                time.sleep(wait_time)
                                element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                                time.sleep(0.3)  # 确保元素状态稳定
                                print(f"✓ 元素重新定位成功")
                            else:
                                raise

                elif step_data['action_type'] in {'press', 'select', 'check', 'uncheck'}:
                    element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                    if step_data['action_type'] == 'press':
                        element_obj.send_keys(step_data['input_value'])
                    elif step_data['action_type'] == 'select':
                        from selenium.webdriver.support.ui import Select
                        Select(element_obj).select_by_value(step_data['input_value'])
                    else:
                        should_be_selected = step_data['action_type'] == 'check'
                        if bool(element_obj.is_selected()) != should_be_selected:
                            element_obj.click()
                    step_result['success'] = True

                elif step_data['action_type'] == 'getText':
                    # 先定位元素
                    element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                    
                    for attempt in range(max_retries):
                        try:
                            text = element_obj.text
                            step_result['result'] = text
                            save_name = str(step_data.get('assert_value') or '').strip()
                            if save_name:
                                self.variables.set_temporary(save_name, text)
                                step_result['saved_variable'] = save_name
                            step_result['success'] = True
                            break
                        except StaleElementReferenceException:
                            if attempt < max_retries - 1:
                                print(f"⚠️  元素过期（Stale Element），正在重试... (尝试 {attempt + 2}/{max_retries})")
                                # 增加等待时间，让页面 DOM 稳定
                                wait_time = 1.0 if attempt == 0 else 1.5
                                print(f"等待 {wait_time}秒 让页面稳定...")
                                time.sleep(wait_time)
                                element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                                time.sleep(0.3)  # 确保元素状态稳定
                                print(f"✓ 元素重新定位成功")
                            else:
                                raise

                elif step_data['action_type'] == 'hover':
                    # 先定位元素
                    element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                    
                    from selenium.webdriver.common.action_chains import ActionChains
                    for attempt in range(max_retries):
                        try:
                            ActionChains(driver).move_to_element(element_obj).perform()
                            step_result['success'] = True
                            break
                        except StaleElementReferenceException:
                            if attempt < max_retries - 1:
                                print(f"⚠️  元素过期（Stale Element），正在重试... (尝试 {attempt + 2}/{max_retries})")
                                # 增加等待时间，让页面 DOM 稳定
                                wait_time = 1.0 if attempt == 0 else 1.5
                                print(f"等待 {wait_time}秒 让页面稳定...")
                                time.sleep(wait_time)
                                element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                                time.sleep(0.3)  # 确保元素状态稳定
                                print(f"✓ 元素重新定位成功")
                            else:
                                raise

                elif step_data['action_type'] == 'screenshot':
                    screenshot_path = f'screenshots/step_{step_data["step_number"]}.png'
                    driver.save_screenshot(screenshot_path)
                    step_result['screenshot'] = screenshot_path
                    step_result['success'] = True

                elif step_data['action_type'] == 'assert':
                    # 先定位元素
                    element_obj = wait.until(EC.presence_of_element_located((by, locator_value)))
                    
                    # 解析断言值中的变量
                    resolved_assert_value = step_data['assert_value']
                    if resolved_assert_value != step_data['assert_value']:
                        print(f"  ✓ 断言变量解析: {step_data['assert_value']} -> {resolved_assert_value}")

                    if step_data['assert_type'] == 'textContains':
                        text = element_obj.text
                        if resolved_assert_value in text:
                            step_result['success'] = True
                        else:
                            # 格式化为详细的错误信息，与selenium_engine.py保持一致
                            log = f"✗ 断言失败: 文本不包含 '{resolved_assert_value}'\n"
                            log += f"  - 实际文本: '{text}'"
                            step_result['error'] = log
                    elif step_data['assert_type'] == 'textEquals':
                        text = element_obj.text
                        if text == resolved_assert_value:
                            step_result['success'] = True
                        else:
                            # 格式化为详细的错误信息
                            log = f"✗ 断言失败: 文本不等于 '{resolved_assert_value}'\n"
                            log += f"  - 期望: '{resolved_assert_value}'\n"
                            log += f"  - 实际: '{text}'"
                            step_result['error'] = log
                    elif step_data['assert_type'] == 'isVisible':
                        is_visible = element_obj.is_displayed()
                        step_result['success'] = is_visible
                        if not is_visible:
                            step_result['error'] = f"✗ 断言失败: 元素 '{element_name}' 不可见"
                    elif step_data['assert_type'] == 'exists':
                        # 元素已经找到，说明存在
                        step_result['success'] = True

            else:
                if step_data['action_type'] == 'wait':
                    time.sleep(step_data['wait_time'] / 1000)
                    step_result['success'] = True

                elif step_data['action_type'] == 'switchTab':
                    # Selenium 切换标签页逻辑
                    try:
                        # 获取当前所有窗口句柄
                        handles = driver.window_handles

                        # 简单的策略：切换到最后一个窗口（通常是新打开的）
                        # 如果指定了索引，则切换到指定索引
                        target_index = -1
                        if step_data.get('input_value') and str(step_data['input_value']).isdigit():
                            target_index = int(step_data['input_value'])

                        if target_index >= 0 and target_index < len(handles):
                            driver.switch_to.window(handles[target_index])
                        else:
                            driver.switch_to.window(handles[-1])

                        step_result['success'] = True
                        print(f"✓ Selenium 切换标签页成功 (Handle Count: {len(handles)})")
                    except Exception as e:
                        step_result['error'] = f"切换标签页失败: {str(e)}"
                        step_result['success'] = False

        except TimeoutException as e:
            # 格式化为详细的错误信息，与selenium_engine.py保持一致
            execution_time = round(time.time() - start_time, 2)
            element_name = step_data['element'].get('name', '未知元素') if step_data.get('element') else '页面'
            locator_info = f"{step_data['element']['locator_strategy']}={step_data['element']['locator_value']}" if step_data.get(
                'element') else '无'

            # 获取超时设置（从element或step）
            timeout_seconds = 10  # 默认值
            if step_data.get('element') and step_data['element'].get('wait_timeout'):
                timeout_seconds = step_data['element']['wait_timeout']
            elif step_data.get('wait_time'):
                timeout_seconds = step_data['wait_time'] / 1000

            # 提取TimeoutException的完整堆栈信息（类似Playwright的显示方式）
            error_parts = []

            # 1. 基本错误信息
            base_msg = str(e).strip()
            if base_msg and base_msg not in ['', 'Message:', 'Message: ', 'Message']:
                error_parts.append(base_msg)
            else:
                # 如果str(e)为空，说明是标准的超时异常
                error_parts.append(f"TimeoutException: 等待元素超时")

            # 2. 尝试从msg属性获取详细信息
            if hasattr(e, 'msg') and e.msg:
                msg_str = str(e.msg).strip()
                if msg_str and msg_str not in ['', 'Message:', 'Message: ', 'Message']:
                    if msg_str not in error_parts:
                        error_parts.append(msg_str)

            # 3. 从args获取
            if hasattr(e, 'args') and len(e.args) > 0 and e.args[0]:
                args_str = str(e.args[0]).strip()
                if args_str and args_str not in ['', 'Message:', 'Message: ', 'Message']:
                    if args_str not in error_parts:
                        error_parts.append(args_str)

            # 4. 如果有stacktrace，添加堆栈信息（类似Playwright的格式）
            if hasattr(e, 'stacktrace') and e.stacktrace:
                stacktrace_str = str(e.stacktrace).strip()
                if stacktrace_str:
                    error_parts.append(f"\nSelenium堆栈跟踪:\n{stacktrace_str}")

            # 4.5. 添加Python的traceback信息（这个总是可用的）
            try:
                import traceback
                tb_lines = traceback.format_tb(e.__traceback__)
                if tb_lines:
                    # 只取最后2层堆栈（最相关的部分）
                    relevant_tb = tb_lines[-2:] if len(tb_lines) >= 2 else tb_lines
                    tb_str = ''.join(relevant_tb).strip()
                    if tb_str:
                        # 提取等待条件信息（从堆栈中）
                        wait_condition = "未知条件"
                        if 'EC.visibility_of_element_located' in tb_str:
                            wait_condition = "等待元素可见 (visibility_of_element_located)"
                        elif 'EC.element_to_be_clickable' in tb_str:
                            wait_condition = "等待元素可点击 (element_to_be_clickable)"
                        elif 'EC.presence_of_element_located' in tb_str:
                            wait_condition = "等待元素存在 (presence_of_element_located)"

                        error_parts.append(f"\n等待条件: {wait_condition}")
                        error_parts.append(f"\n调用堆栈:\n{tb_str}")
            except:
                pass

            # 5. 如果仍然没有有用信息，提供操作类型相关的提示
            if len(error_parts) == 0 or (len(error_parts) == 1 and 'TimeoutException' in error_parts[0]):
                # 添加操作相关的上下文
                action_type_str = step_data.get('action_type', '') if isinstance(step_data, dict) else ''
                if action_type_str == 'click':
                    error_parts.append(f"等待元素可点击失败（超时{timeout_seconds}秒）")
                elif action_type_str == 'fill':
                    error_parts.append(f"等待输入框可用失败（超时{timeout_seconds}秒）")
                elif action_type_str == 'waitFor':
                    error_parts.append(f"等待元素出现失败（超时{timeout_seconds}秒）")

            # 合并所有错误信息
            error_msg = '\n'.join(error_parts)

            log = f"✗ 操作超时\n"
            log += f"  - 元素: '{element_name}'\n"
            log += f"  - 定位器: {locator_info}\n"
            log += f"  - 超时设置: {timeout_seconds}秒\n"
            log += f"  - 实际用时: {execution_time}秒\n"
            log += f"  - 错误详情: {error_msg}"
            step_result['error'] = log

        except Exception as e:
            # 格式化为详细的错误信息，与selenium_engine.py保持一致
            execution_time = round(time.time() - start_time, 2)
            element_name = step_data['element'].get('name', '未知元素') if step_data.get('element') else '页面'
            locator_info = f"{step_data['element']['locator_strategy']}={step_data['element']['locator_value']}" if step_data.get(
                'element') else '无'

            # 提取详细的错误信息（改进版 - 添加调试日志）
            error_type = type(e).__name__
            error_msg = ""

            # 🔍 调试：打印异常对象的所有信息
            print(f"=" * 60)
            print(f"🔍 Selenium 异常调试信息 (test_executor):")
            print(f"  异常类型: {error_type}")
            print(f"  str(e): {repr(str(e))}")
            print(f"  hasattr msg: {hasattr(e, 'msg')}")
            if hasattr(e, 'msg'):
                print(f"  e.msg 值: {repr(e.msg)}")
                print(f"  e.msg 类型: {type(e.msg)}")
            print(f"  hasattr args: {hasattr(e, 'args')}")
            if hasattr(e, 'args'):
                print(f"  e.args 长度: {len(e.args)}")
                print(f"  e.args 内容: {e.args}")
            print(f"  hasattr stacktrace: {hasattr(e, 'stacktrace')}")
            if hasattr(e, 'stacktrace'):
                print(f"  e.stacktrace 前200字符: {str(e.stacktrace)[:200]}")
            print(f"  dir(e): {[attr for attr in dir(e) if not attr.startswith('_')]}")
            print(f"=" * 60)

            # 定义无意义的错误信息列表
            meaningless_messages = ['', 'Message', 'Message:', 'Message: ', 'Message:\n']

            # 尝试提取更详细的 Selenium 异常信息（使用优先级策略）
            try:
                # 优先级1: 从 msg 属性获取（Selenium 异常的主要信息源）
                if hasattr(e, 'msg') and e.msg:
                    temp = str(e.msg).strip()
                    if temp not in meaningless_messages:
                        error_msg = temp
                        print(f"✓ 从 e.msg 提取到错误: {error_msg[:100]}")

                # 优先级2: 从 args 获取
                if not error_msg and hasattr(e, 'args') and len(e.args) > 0 and e.args[0]:
                    temp = str(e.args[0]).strip()
                    if temp not in meaningless_messages:
                        error_msg = temp
                        print(f"✓ 从 e.args[0] 提取到错误: {error_msg[:100]}")

                # 优先级3: 使用 str(e)，但排除无意义的值
                if not error_msg:
                    temp = str(e).strip()
                    if temp not in meaningless_messages:
                        error_msg = temp
                        print(f"✓ 从 str(e) 提取到错误: {error_msg[:100]}")

                # 优先级4: 从 stacktrace 提取
                if not error_msg and hasattr(e, 'stacktrace') and e.stacktrace:
                    error_msg = f"详细堆栈:\n{e.stacktrace[:300]}"
                    print(f"✓ 从 e.stacktrace 提取到错误")

                # 优先级5: 从 __dict__ 提取有用信息
                if not error_msg and hasattr(e, '__dict__'):
                    useful_attrs = {k: v for k, v in e.__dict__.items()
                                    if
                                    v is not None and not k.startswith('_') and k not in ['msg', 'args', 'stacktrace']}
                    if useful_attrs:
                        error_msg = f"异常属性: {useful_attrs}"
                        print(f"✓ 从 e.__dict__ 提取到错误")

                # 如果还是没有，使用默认信息
                if not error_msg:
                    error_msg = f"未知错误 (异常类型: {error_type})"
                    print(f"⚠️ 无法提取任何有用信息，使用默认错误消息")

            except Exception as extract_error:
                print(f"⚠️  提取错误信息时出错: {extract_error}")
                error_msg = f"无法提取详细错误信息 (异常类型: {error_type})"

            # 添加异常类型前缀（如果还没有）
            if error_type not in error_msg and error_type != 'Exception':
                error_msg = f"{error_type}: {error_msg}"

            log = f"✗ 执行失败\n"
            log += f"  - 元素: '{element_name}'\n"
            log += f"  - 定位器: {locator_info}\n"
            log += f"  - 执行时间: {execution_time}秒\n"
            log += f"  - 错误: {error_msg}"
            step_result['error'] = log

            # 打印详细日志便于调试
            print(f"❌ Selenium 步骤执行失败:")
            print(f"   异常类型: {error_type}")
            print(f"   错误信息: {error_msg[:500]}")  # 限制长度避免刷屏

        return step_result


# --------------------------------------------------------------------------- #
# 单用例执行入口：页面「运行用例」与 MCP 异步执行共用同一实现
# --------------------------------------------------------------------------- #

logger = logging.getLogger(__name__)

_PLAYWRIGHT_LAUNCH_ARGS = [
    '--disable-blink-features=AutomationControlled',  # 避免被检测
    '--ignore-certificate-errors',
    '--allow-insecure-localhost',
    '--disable-web-security',
]


def check_engine_environment(engine, browser):
    """检查执行引擎环境，返回 (是否就绪, 错误信息)。

    页面与 MCP 共用同一判定，调用方只负责把结果翻译成各自的响应格式。
    """
    if engine == 'selenium':
        from .selenium_engine import SeleniumTestEngine
        return SeleniumTestEngine.check_execution_environment(browser)
    from .playwright_engine import PlaywrightTestEngine
    return PlaywrightTestEngine.check_execution_environment_sync(browser)


def _launch_playwright_browser(playwright, browser, headless):
    """按浏览器类型启动 Playwright 浏览器（与套件执行同一套参数）。"""
    args = list(_PLAYWRIGHT_LAUNCH_ARGS)
    if browser == 'firefox':
        return playwright.firefox.launch(headless=headless, args=args)
    if browser == 'safari':
        return playwright.webkit.launch(headless=headless, args=args)
    return playwright.chromium.launch(headless=headless, args=args)


def _wait_after_navigation():
    """SPA 首屏在导航完成后仍需渲染，服务器（Linux）上留更长等待。"""
    import platform
    time.sleep(3 if platform.system() == 'Linux' else 2)


def _run_case_playwright(executor, case_data):
    """在独立浏览器上下文中执行单个用例（Playwright 同步 API）。"""
    from .models import StorageState

    with sync_playwright() as playwright:
        browser = _launch_playwright_browser(
            playwright, executor.browser, executor.headless)
        try:
            executor.browser_instance = browser
            executor._active_project_id = case_data['project_id']
            initial_state = None
            if executor.storage_state_id:
                initial_state = StorageState.objects.filter(
                    pk=executor.storage_state_id,
                    project_id=case_data['project_id'],
                ).first()
                if not initial_state:
                    raise ValueError('选择的登录态不存在或不属于当前项目')
            executor.context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/119.0.0.0 Safari/537.36'),
                **({'storage_state': initial_state.file.path} if initial_state else {}),
            )
            executor.current_page = executor.context.new_page()
            if executor.project.base_url:
                executor._goto_with_load_fallback(
                    executor.current_page, executor.project.base_url, timeout=30000)
                _wait_after_navigation()
            return executor.execute_test_case_playwright_no_db(case_data)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响执行结论
                logger.warning('[UiSingleCase] 浏览器关闭失败', exc_info=True)


def _run_case_selenium(executor, case_data):
    """在独立 WebDriver 中执行单个用例（Selenium）。"""
    import platform

    driver = executor.create_selenium_driver()
    try:
        if executor.project.base_url:
            driver.get(executor.project.base_url)
            try:
                WebDriverWait(
                    driver, 15 if platform.system() == 'Linux' else 10
                ).until(lambda d: d.execute_script('return document.readyState') == 'complete')
            except Exception:  # noqa: BLE001 - 首屏就绪超时也继续执行
                pass
            _wait_after_navigation()
        return executor.execute_test_case_selenium_no_db(driver, case_data)
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            logger.warning('[UiSingleCase] 浏览器关闭失败', exc_info=True)


def _persist_case_execution(execution, case_result, started):
    """把执行结果写入 TestCaseExecution（调用方预建的记录直接复用）。"""
    if execution is None:
        return
    try:
        execution.status = case_result.get('status') or 'failed'
        execution.error_message = case_result.get('error') or ''
        execution.execution_logs = json.dumps(
            case_result.get('steps') or [], ensure_ascii=False)
        execution.execution_time = round(time.time() - started, 2)
        execution.screenshots = case_result.get('screenshots') or []
        execution.finished_at = timezone.now()
        execution.save()
    except Exception:  # noqa: BLE001 - 落库失败不掩盖执行结论
        logger.exception('[UiSingleCase] 执行记录落库失败 execution_id=%s', execution.id)


def run_single_case(case, *, engine='playwright', browser='chrome', headless=False,
                    executed_by=None, execution=None, storage_state_id=None):
    """执行单个 UI 用例并把结果写入执行记录。

    与套件执行共用 ``execute_test_case_*_no_db``，因此 storageState、navigate、
    urlContains 轮询等待等步骤语义完全一致；页面「运行用例」与 MCP 异步执行
    都调用本函数，不再各自维护一份步骤循环。

    Args:
        case: TestCase 实例（调用方已完成权限与前置校验）
        execution: 可选的预建 TestCaseExecution（MCP 需要先返回执行 ID）

    Returns:
        dict: case_result（status / steps / error / screenshots / start_time / end_time）
    """
    executor = TestExecutor(
        test_suite=None, engine=engine, browser=browser, headless=headless,
        executed_by=executed_by, storage_state_id=storage_state_id,
        execution=execution, project=case.project,
    )
    executor.run_variables = {}
    executor.variables = UiVariableContext(
        case.project.variables, run=executor.run_variables)

    case_data = executor._build_case_data(case)
    started = time.time()
    try:
        if engine == 'selenium':
            case_result = _run_case_selenium(executor, case_data)
        else:
            case_result = _run_case_playwright(executor, case_data)
    except Exception as exc:  # noqa: BLE001 - 执行异常必须落库，不能留给调用方
        logger.exception('[UiSingleCase] 用例执行异常 case_id=%s', case.id)
        case_result = {
            'test_case_id': case.id,
            'test_case_name': case.name,
            'status': 'failed',
            'steps': [],
            'error': f'执行异常: {exc}',
            'screenshots': [],
            'start_time': datetime.now().isoformat(),
            'end_time': datetime.now().isoformat(),
        }
    finally:
        try:
            executor.variables.clear()
        except Exception:  # noqa: BLE001
            pass

    _persist_case_execution(execution, case_result, started)
    return case_result
