"""
Playwright自动化测试执行引擎
用于驱动真实浏览器执行UI自动化测试
"""
import asyncio
import base64
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeout
import logging
from .variable_context import UiVariableContext, as_text

logger = logging.getLogger(__name__)


def _locator_spec(strategy, value, options):
    options = dict(options or {})
    first = options.pop('first', False)
    nth = options.pop('nth', None)
    strategy = strategy.lower()
    method = {
        'role': 'get_by_role',
        'text': 'get_by_text',
        'label': 'get_by_label',
        'placeholder': 'get_by_placeholder',
        'title': 'get_by_title',
        'test-id': 'get_by_test_id',
        'alt-text': 'get_by_alt_text',
    }.get(strategy)
    if method:
        # 元素从 role 改为 placeholder 等策略时，库中可能仍保留旧的 name。
        # 只传当前 Playwright 工厂支持的参数，避免历史选项造成 TypeError。
        if strategy == 'role':
            supported = {'checked', 'disabled', 'expanded', 'include_hidden', 'level',
                         'name', 'pressed', 'selected', 'exact'}
        elif strategy == 'test-id':
            supported = set()
        else:
            supported = {'exact'}
        options = {key: value for key, value in options.items() if key in supported}
    else:
        method = 'locator'
        if strategy == 'id':
            value = f'#{value}'
        elif strategy == 'name':
            value = f'[name="{value}"]'
        elif strategy == 'xpath' and not value.startswith('xpath='):
            value = f'xpath={value}'
        supported = {'has_text', 'has_not_text', 'has', 'has_not'}
        options = {key: value for key, value in options.items() if key in supported}
    return method, value, options, first, nth


def build_playwright_locator(page, strategy, value, options=None):
    """Keep factory arguments separate from recorded positional selectors."""
    method, value, options, first, nth = _locator_spec(strategy, value, options)
    locator = getattr(page, method)(value, **options)
    if first:
        return locator.first
    if nth is not None:
        return locator.nth(nth)
    return locator


def playwright_locator_expression(strategy, value, options=None, *, page='page', language='python'):
    """Export locators with the same semantics as runtime execution."""
    method, value, options, first, nth = _locator_spec(strategy, value, options)
    if language == 'javascript':
        def camel_case(text):
            head, *tail = text.split('_')
            return head + ''.join(part.title() for part in tail)
        args = [json.dumps(value, ensure_ascii=False)]
        if options:
            args.append(json.dumps({camel_case(key): val for key, val in options.items()}, ensure_ascii=False))
        expression = f'{page}.{camel_case(method)}({", ".join(args)})'
        if first:
            expression += '.first()'
        elif nth is not None:
            expression += f'.nth({nth})'
    else:
        args = [repr(value), *(f'{key}={val!r}' for key, val in options.items())]
        expression = f'{page}.{method}({", ".join(args)})'
        if first:
            expression += '.first'
        elif nth is not None:
            expression += f'.nth({nth})'
    return expression


class PlaywrightTestEngine:
    """Playwright测试执行引擎"""

    def __init__(self, browser_type='chromium', headless=True):
        """
        初始化测试引擎

        Args:
            browser_type: 浏览器类型 (chromium, firefox, webkit)
            headless: 是否无头模式
        """
        self.browser_type = browser_type
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.variables = UiVariableContext()

    def initialize_variables(self, definitions, run=None):
        """初始化当前执行上下文；套件执行时由调用方传入共享运行副本。"""
        self.variables = UiVariableContext(definitions, run=run)

    @staticmethod
    def normalize_browser_type(browser_type='chromium'):
        """统一浏览器类型名称"""
        browser_map = {
            'chrome': 'chromium',
            'chromium': 'chromium',
            'edge': 'chromium',
            'firefox': 'firefox',
            'safari': 'webkit',
            'webkit': 'webkit',
        }
        return browser_map.get(browser_type, 'chromium')

    @staticmethod
    def get_browser_install_command(browser_type='chromium'):
        """返回 Playwright 浏览器安装命令"""
        normalized_browser = PlaywrightTestEngine.normalize_browser_type(browser_type)
        return f"python -m playwright install {normalized_browser}"

    @staticmethod
    def _format_environment_error(browser_type, error_msg):
        """格式化 Playwright 环境错误提示"""
        normalized_browser = PlaywrightTestEngine.normalize_browser_type(browser_type)
        browser_name_map = {
            'chromium': 'Chromium/Chrome',
            'firefox': 'Firefox',
            'webkit': 'WebKit/Safari',
        }
        browser_name = browser_name_map.get(normalized_browser, normalized_browser.capitalize())
        install_cmd = PlaywrightTestEngine.get_browser_install_command(normalized_browser)

        if "Executable doesn't exist" in error_msg or 'Please run the following command' in error_msg:
            return (
                f"{browser_name} 浏览器未安装或 Playwright 浏览器二进制缺失。\n"
                f"请先执行命令安装浏览器：{install_cmd}"
            )

        return (
            f"{browser_name} 浏览器启动失败：{error_msg}\n"
            f"如未安装 Playwright 浏览器，请先执行：{install_cmd}"
        )

    @staticmethod
    def check_execution_environment_sync(browser_type='chromium'):
        """同步检查 Playwright 执行环境"""
        normalized_browser = PlaywrightTestEngine.normalize_browser_type(browser_type)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            return False, (
                "Playwright 模块未安装。\n"
                "请先执行命令安装：pip install playwright\n"
                f"然后执行：{PlaywrightTestEngine.get_browser_install_command(normalized_browser)}\n"
                f"详细错误：{str(e)}"
            )

        playwright = None
        browser = None
        try:
            playwright = sync_playwright().start()
            browser_launcher = getattr(playwright, normalized_browser)
            browser = browser_launcher.launch(headless=True)
            return True, None
        except Exception as e:
            logger.error(f"同步检查 Playwright 执行环境失败: {str(e)}")
            return False, PlaywrightTestEngine._format_environment_error(normalized_browser, str(e))
        finally:
            try:
                if browser:
                    browser.close()
            finally:
                if playwright:
                    playwright.stop()

    @staticmethod
    async def check_execution_environment(browser_type='chromium'):
        """异步检查 Playwright 执行环境"""
        normalized_browser = PlaywrightTestEngine.normalize_browser_type(browser_type)

        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser_launcher = getattr(playwright, normalized_browser)
            browser = await browser_launcher.launch(headless=True)
            return True, None
        except Exception as e:
            logger.error(f"异步检查 Playwright 执行环境失败: {str(e)}")
            return False, PlaywrightTestEngine._format_environment_error(normalized_browser, str(e))
        finally:
            try:
                if browser:
                    await browser.close()
            finally:
                if playwright:
                    await playwright.stop()

    async def start(self, storage_state_path=None):
        """启动浏览器"""
        try:
            self.playwright = await async_playwright().start()

            # 根据浏览器类型选择启动方式
            normalized_browser = self.normalize_browser_type(self.browser_type)
            if normalized_browser == 'chromium':
                browser_launcher = self.playwright.chromium
            elif normalized_browser == 'firefox':
                browser_launcher = self.playwright.firefox
            elif normalized_browser == 'webkit':
                browser_launcher = self.playwright.webkit
            else:
                browser_launcher = self.playwright.chromium

            # 启动浏览器
            self.browser = await browser_launcher.launch(
                headless=self.headless,
                args=[
                    '--disable-blink-features=AutomationControlled',  # 避免被检测
                    '--ignore-certificate-errors',  # 忽略证书错误
                    '--allow-insecure-localhost',  # 允许不安全localhost
                    '--disable-web-security',  # 禁用web安全限制（跨域）
                ]
            )

            # 创建浏览器上下文；有初始登录态时由 Playwright 直接加载文件。
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                **({'storage_state': storage_state_path} if storage_state_path else {})
            )

            # 创建页面
            self.page = await self.context.new_page()

            logger.info(f"浏览器启动成功: {self.browser_type}, headless={self.headless}")

        except Exception as e:
            logger.error(f"启动浏览器失败: {str(e)}")
            raise

    async def apply_storage_state_action(self, params, *, project_id, user):
        """在低代码用例中保存、切换或清除登录态。"""
        import tempfile
        from pathlib import Path
        from asgiref.sync import sync_to_async
        from .models import StorageState

        operation = params.get('operation')
        # 保存动作把当前上下文导出为资源，覆盖和另存为共用同一持久化流程。
        if operation == 'save':
            name = str(params.get('name') or '').strip()
            state_id = params.get('storage_state_id')
            if params.get('mode') == 'overwrite':
                state = await sync_to_async(
                    lambda: StorageState.objects.filter(pk=state_id, project_id=project_id).first()
                )()
                if not state:
                    raise ValueError('要覆盖的登录态不存在或不属于当前项目')
            else:
                state = StorageState(project_id=project_id, name=name, created_by=user)
            with tempfile.NamedTemporaryFile(suffix='.json') as temp_file:
                await self.context.storage_state(path=temp_file.name)
                raw = Path(temp_file.name).read_bytes()
            state.name = name or state.name
            state.source_url = self.page.url
            state.browser = self.browser_type
            await sync_to_async(state.write_content)(raw)
            return f'登录态已保存：{state.name}'
        # 切换或清除必须重建 BrowserContext，确保旧 Cookie 和 localStorage 不会残留。
        if operation in {'switch', 'clear'}:
            url = self.page.url
            state_path = None
            if operation == 'switch':
                target_id = params.get('storage_state_id')
                state = await sync_to_async(
                    lambda: StorageState.objects.filter(pk=target_id, project_id=project_id).first()
                )()
                if not state:
                    raise ValueError('目标登录态不存在或不属于当前项目')
                state_path = state.file.path
            await self.page.close()
            await self.context.close()
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            }
            if state_path:
                context_options['storage_state'] = state_path
            self.context = await self.browser.new_context(**context_options)
            self.page = await self.context.new_page()
            # clear 后由后续 navigate 步骤进入公开页面，避免回访受保护 URL 触发重定向循环。
            if url and url != 'about:blank' and params.get('navigate_current', True):
                await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
            return '已切换到指定登录态' if state_path else '已清除登录态'
        raise ValueError('未知的登录态操作')

    async def stop(self):
        """关闭浏览器"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("浏览器已关闭")
        except Exception as e:
            logger.error(f"关闭浏览器失败: {str(e)}")
        finally:
            self.variables.clear()

    async def execute_step(self, step, element_data: Dict) -> Tuple[bool, str, Optional[str]]:
        """
        执行单个测试步骤

        Args:
            step: 测试步骤对象
            element_data: 元素数据字典 {locator_strategy, locator_value, name}

        Returns:
            (是否成功, 日志信息, 截图base64)
        """
        success, log, screenshot = await self._execute_step(step, element_data)
        if self.variables.strict:
            # 每步记录脱敏后的项目变量快照，便于排查本次运行状态。
            log += '\n  - 变量快照: ' + json.dumps(self.variables.snapshot(), ensure_ascii=False)
        return success, self.variables.redact(log), screenshot

    async def _execute_step(self, step, element_data: Dict) -> Tuple[bool, str, Optional[str]]:
        """
        执行单个测试步骤

        Args:
            step: 测试步骤对象
            element_data: 元素数据字典 {locator_strategy, locator_value, name}

        Returns:
            (是否成功, 日志信息, 截图base64)
        """
        action_type = step.action_type
        
        start_time = time.time()
        screenshot_base64 = None
        element_name = element_data.get('name', '页面')
        locator_strategy = element_data.get('locator_strategy', 'css')
        locator_value = element_data.get('locator_value', '')

        try:
            # 在动作开始时才解析参数，避免提前生成未执行步骤中的随机值。
            rendered = self.variables.render_step({
                'action_type': action_type,
                'input_value': step.input_value,
                'assert_value': step.assert_value,
                'element': element_data,
            })
            variable_result = self.variables.apply_step(rendered)
            if variable_result is not None:
                return variable_result
            resolved_input_value = as_text(rendered['input_value'])
            resolved_assert_value = as_text(rendered['assert_value'])
            element_data = rendered['element']
            if action_type == 'navigate':
                if not resolved_input_value:
                    return False, "✗ 跳转URL失败: URL不能为空", None
                success, log = await self.navigate(resolved_input_value)
                if not success:
                    try:
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                    except Exception:
                        pass
                return success, log, screenshot_base64

            # wait和screenshot操作不需要元素定位器
            if action_type == 'wait':
                wait_seconds = step.wait_time / 1000 if step.wait_time else 1
                await asyncio.sleep(wait_seconds)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 固定等待 {wait_seconds} 秒完成 - 耗时 {execution_time}秒"
                return True, log, None

            elif action_type == 'screenshot':
                screenshot = await self.page.screenshot()
                screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 截图成功\n"
                log += f"  - 截图范围: 整个页面\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, screenshot_base64

            elif action_type == 'switchTab':
                # 切换标签页
                # 获取超时时间
                if step.wait_time:
                    timeout = step.wait_time / 1000
                else:
                    timeout = 5.0
                
                start_wait = time.time()
                current_page = self.page
                target_index = -1
                
                while True:
                    pages = self.context.pages
                    target_index = -1  # 默认切换到最新标签页
                    should_switch = False
                    
                    if resolved_input_value and str(resolved_input_value).isdigit():
                        # 指定索引的情况
                        idx = int(resolved_input_value)
                        if 0 <= idx < len(pages):
                            target_index = idx
                            should_switch = True
                    else:
                        # 切换到最新的情况
                        target_index = -1
                        # 如果最新的页面不是当前页面，说明有新标签页，或者是切换到其他已存在的标签页
                        if pages[-1] != current_page:
                            should_switch = True
                        # 如果只有一个页面，且就是当前页，可能是在等待新标签页打开
                        elif len(pages) == 1 and pages[0] == current_page:
                            should_switch = False
                        # 如果有多个页面，但最新的就是当前页，可能是想留在当前页，也可能是等待更新的
                        else:
                            should_switch = False

                    if should_switch:
                        break
                    
                    if time.time() - start_wait > timeout:
                        # 超时了，就切换到当前能找到的那个（Best Effort）
                        break
                        
                    await asyncio.sleep(0.5)
                
                # 获取目标页面
                if target_index == -1:
                    target_page = pages[-1]
                    final_target_index = len(pages) - 1
                else:
                    target_page = pages[target_index]
                    final_target_index = target_index

                # 将目标页面设为当前活动页面
                await target_page.bring_to_front()
                # 更新引擎的当前页面引用
                self.page = target_page
                
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 切换标签页成功\n"
                log += f"  - 目标索引: {final_target_index}\n"
                log += f"  - 页面标题: {await self.page.title()}\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'assert' and step.assert_type == 'urlContains':
                timeout_seconds = (step.wait_time or 5000) / 1000
                deadline = time.monotonic() + timeout_seconds
                current_url = self.page.url
                while resolved_assert_value and resolved_assert_value not in current_url:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.1, remaining))
                    current_url = self.page.url
                if resolved_assert_value and resolved_assert_value in current_url:
                    return True, f"✓ 断言通过: URL包含 '{resolved_assert_value}'\n  - 实际URL: '{current_url}'", None
                log = f"✗ 断言失败: URL不包含 '{resolved_assert_value}'\n  - 实际URL: '{current_url}'"
                try:
                    screenshot = await self.page.screenshot()
                    screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                except Exception:
                    pass
                return False, log, screenshot_base64

            # 其他操作需要元素定位器
            # 获取元素定位器
            locator_strategy = element_data.get('locator_strategy', 'css')
            locator_value = element_data.get('locator_value', '')
            element_name = element_data.get('name', '未知元素')

            # 获取强制操作选项（用于visibility:hidden的元素）
            force_action = element_data.get('force_action', False)

            # 计算超时时间：优先使用元素的wait_timeout（秒），其次使用步骤的wait_time（毫秒）
            # 如果元素有wait_timeout，转换为毫秒；否则使用步骤的wait_time
            element_wait_timeout = element_data.get('wait_timeout')  # 秒
            if element_wait_timeout is not None and element_wait_timeout > 0:
                timeout_ms = element_wait_timeout * 1000  # 转换为毫秒
            elif step.wait_time:
                timeout_ms = step.wait_time
            else:
                timeout_ms = 5000  # 默认5秒

            # 保留原有 CSS/XPath 可见性处理；显式 first/nth 优先于默认首项。
            locator_options = dict(element_data.get('locator_options') or {})
            value = locator_value
            strategy = locator_strategy.lower()
            # 不可见及属性断言必须保留隐藏节点，不能套用下拉选项的可见性过滤。
            if strategy in {'css', 'css selector', 'xpath'} and not (
                action_type == 'assert' and step.assert_type in {'isNotVisible', 'hasAttribute'}
            ):
                dropdown = any(keyword in value.lower() for keyword in ['dropdown', 'el-select', ':has(', 'li'])
                if dropdown:
                    if 'visible=true' not in value:
                        value += ' >> visible=true'
                    if 'first' not in locator_options and 'nth' not in locator_options:
                        locator_options['first'] = True
                elif strategy == 'xpath' and not ('[' in value and ']' in value):
                    if 'first' not in locator_options and 'nth' not in locator_options:
                        locator_options['first'] = True
            locator = build_playwright_locator(self.page, strategy, value, locator_options)

            # 执行操作
            execution_time = 0

            if action_type == 'click':
                action_params = getattr(step, 'action_params', None) or {}
                if action_params.get('optional'):
                    match_count = await locator.count()
                    if not match_count or not await locator.is_visible():
                        return True, f"✓ 可选点击目标 '{element_name}' 不存在或不可见，已跳过", None

                # 保存类按钮在页面没有未提交变更时会保持 disabled；与同步
                # 执行器一致，遵循源用例的 skip_if_disabled 语义并安全跳过。
                if action_params.get('skip_if_disabled'):
                    match_count = await locator.count()
                    if match_count == 1 and not await locator.is_enabled():
                        return True, f"✓ 点击目标 '{element_name}' 已禁用，无待保存变更，已跳过", None

                if action_params.get('dispatch_click'):
                    # RTE decorators can swallow locator.click while still reporting success.
                    await locator.evaluate("""element => {
                        const events = [
                            new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }),
                            new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }),
                            new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
                        ];
                        events.forEach(event => element.dispatchEvent(event));
                    }""")
                    execution_time = round(time.time() - start_time, 2)
                    log = f"✓ 分发鼠标事件点击元素 '{element_name}' 成功\n"
                    log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                    log += f"  - 执行时间: {execution_time}秒"
                    return True, log, None

                # 检测是否是原生HTML select的option元素（优先检测，因为option元素特殊）
                is_native_select_option = (
                    ('option[' in locator_value or ' > option' in locator_value or '//option' in locator_value) or
                    ('select' in locator_value.lower() and 'option' in locator_value.lower())
                )

                # 对于原生HTML select的option，使用select_option方法
                if is_native_select_option and not element_data.get('locator_options'):
                    logger.info(f"检测到原生HTML select元素，使用select_option方法...")

                    # 提取select的定位器和option的value
                    # 例如：select[name="my-select"] option[value="1"]
                    # 需要提取：select[name="my-select"] 和 value="1"

                    import re

                    # 提取option的value值
                    option_value_match = re.search(r'option\[value=["\']([^"\']+)["\']\]', locator_value)
                    option_value_xpath_match = re.search(r'option\[@value=["\']([^"\']+)["\']\]', locator_value)

                    option_value = None
                    if option_value_match:
                        option_value = option_value_match.group(1)
                    elif option_value_xpath_match:
                        option_value = option_value_xpath_match.group(1)
                    else:
                        # 尝试其他格式
                        option_value = '1'  # 默认值

                    # 构造select元素的定位器（去掉option部分）
                    select_locator_value = re.sub(r'\s*>\s*option\[.*?\]', '', locator_value)
                    select_locator_value = re.sub(r'\s+option\[.*?\]', '', select_locator_value)
                    select_locator_value = re.sub(r'//option\[.*?\]', '', select_locator_value)

                    logger.info(f"Select定位器: {select_locator_value}, Option值: {option_value}")

                    try:
                        # 构造select元素的locator
                        if locator_strategy.lower() == 'xpath':
                            # XPath: //select[@name='my-select']/option[@value='1']
                            # 提取select部分
                            select_match = re.match(r'^(//.*?select)(?:/option)?', locator_value)
                            if select_match:
                                select_locator_value = select_match.group(1)
                            else:
                                select_locator_value = locator_value.split('/')[0]
                            select_locator = self.page.locator(f"xpath={select_locator_value}")
                        else:
                            # CSS: select[name="my-select"] option[value="1"]
                            select_locator = self.page.locator(select_locator_value)

                        # 使用select_option方法
                        await select_locator.select_option(value=option_value, timeout=timeout_ms)

                        execution_time = round(time.time() - start_time, 2)
                        log = f"✓ 选择下拉框选项 '{element_name}' 成功\n"
                        log += f"  - Select定位器: {select_locator_value}\n"
                        log += f"  - 选中值: {option_value}\n"
                        log += f"  - 方法: select_option() (原生HTML select)\n"
                        log += f"  - 执行时间: {execution_time}秒"
                        return True, log, None

                    except Exception as e:
                        logger.error(f"select_option失败: {e}")
                        # 如果失败，继续尝试普通点击
                        pass

                # 对于下拉框选项，需要特殊处理
                # 通过定位器特征自动识别下拉框选项
                is_dropdown_option = (
                    'dropdown' in locator_value.lower() or
                    'el-select' in locator_value.lower() or
                    '下拉' in element_name or
                    '选项' in element_name or
                    'role="option"' in locator_value.lower() or
                    'el-select-dropdown__item' in locator_value.lower() or  # Element Plus 下拉框
                    ('//li' in locator_value and 'span=' in locator_value)  # XPath 下拉框模式
                )
                
                # 检测是否是点击 el-select 容器（下拉框触发器）
                is_select_trigger = ('el-select' in locator_value.lower() and 
                                    'ancestor::' in locator_value.lower() and
                                    'el-select-dropdown' not in locator_value.lower())
                
                if is_select_trigger and not element_data.get('locator_options'):
                    # el-select 容器：点击内部的真正触发器，触发完整事件链
                    logger.info(f"检测到 el-select 容器，使用 Playwright 原生点击...")
                    try:
                        # 等待容器出现
                        await locator.wait_for(state='visible', timeout=timeout_ms)
                        # 点击内部的 wrapper 或 input（使用 Playwright 原生点击，会触发完整事件）
                        wrapper_locator = locator.locator('.el-select__wrapper, input').first
                        await wrapper_locator.click(timeout=timeout_ms, no_wait_after=False)
                        # 等待下拉框展开动画
                        await asyncio.sleep(0.5)
                        
                        execution_time = round(time.time() - start_time, 2)
                        log = f"✓ 点击下拉框触发器 '{element_name}' 成功\n"
                        log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                        log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                        log += f"  - 特殊处理: Playwright原生点击内部触发器 + 等待展开\n"
                        log += f"  - 执行时间: {execution_time}秒"
                        return True, log, None
                    except Exception as e:
                        logger.warning(f"Playwright 点击失败，尝试其他方法: {e}")
                        
                        # 备用方案：使用完整的事件链
                        try:
                            await locator.locator('.el-select__wrapper, input').first.evaluate("""
                                element => {
                                    const events = [
                                        new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }),
                                        new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }),
                                        new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
                                    ];
                                    events.forEach(event => element.dispatchEvent(event));
                                }
                            """)
                            await asyncio.sleep(0.5)
                            execution_time = round(time.time() - start_time, 2)
                            log = f"✓ 点击下拉框触发器 '{element_name}' 成功（事件链）\n"
                            log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                            log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                            log += f"  - 执行时间: {execution_time}秒"
                            return True, log, None
                        except Exception as e2:
                            logger.error(f"所有点击方法都失败: {e2}")
                            raise
                
                elif is_dropdown_option and not element_data.get('locator_options'):
                    # 下拉框选项：需要特殊处理，确保能正确触发 Vue/Element Plus 的 v-model 更新
                    logger.info(f"检测到下拉框选项，使用 Playwright + Vue 数据更新策略...")
                    
                    # 等待下拉框完全展开并渲染
                    await asyncio.sleep(0.8)
                    
                    try:
                        # 等待元素在 DOM 中（不要求可见）
                        await locator.wait_for(state='attached', timeout=timeout_ms)
                        logger.info(f"元素已在 DOM 中，执行 Vue 数据更新...")
                        
                        # 策略：直接通过 page.evaluate() 操作，绕过 locator 的 actionability 检查
                        # locator.evaluate() 会等待元素可见，但下拉框选项可能是隐藏的
                        # 所以我们使用 page.evaluate() 并传递定位器表达式
                        
                        # 根据定位策略构造 JavaScript 选择器
                        if locator_strategy.lower() == 'xpath':
                            js_selector_code = f"""
                                const xpath = {repr(locator_value)};
                                const result = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                                let element = null;
                                for (let i = 0; i < result.snapshotLength; i++) {{
                                    const node = result.snapshotItem(i);
                                    // 检查可见性: offsetParent 不为 null (且不是 fixed 定位) 或者 getComputedStyle display != none
                                    if (node.offsetParent !== null || window.getComputedStyle(node).display !== 'none') {{
                                        element = node;
                                        break;
                                    }}
                                }}
                            """
                        elif locator_strategy.lower() == 'css selector':
                            js_selector_code = f"""
                                const elements = document.querySelectorAll({repr(locator_value)});
                                let element = null;
                                for (const el of elements) {{
                                    if (el.offsetParent !== null || window.getComputedStyle(el).display !== 'none') {{
                                        element = el;
                                        break;
                                    }}
                                }}
                            """
                        else:
                            # 其他策略：尝试作为 CSS 选择器
                            js_selector_code = f"""
                                const elements = document.querySelectorAll({repr(locator_value)});
                                let element = null;
                                for (const el of elements) {{
                                    if (el.offsetParent !== null || window.getComputedStyle(el).display !== 'none') {{
                                        element = el;
                                        break;
                                    }}
                                }}
                            """
                        
                        # 构造基础定位器（不带 visible=true，因为我们要手动遍历）
                        base_locator_value = locator_value.replace(' >> visible=true', '')
                        
                        if locator_strategy.lower() == 'xpath':
                            if not base_locator_value.startswith('xpath='):
                                candidates = self.page.locator(f"xpath={base_locator_value}")
                            else:
                                candidates = self.page.locator(base_locator_value)
                        elif locator_strategy.lower() == 'css selector':
                            candidates = self.page.locator(base_locator_value)
                        else:
                            # 其他策略暂按 CSS 处理
                            candidates = self.page.locator(base_locator_value)
                        
                        # 获取匹配元素数量
                        count = await candidates.count()
                        logger.info(f"找到 {count} 个匹配元素，开始寻找可见元素...")
                        
                        found_visible = False
                        for i in range(count):
                            candidate = candidates.nth(i)
                            if await candidate.is_visible():
                                logger.info(f"找到第 {i+1} 个元素是可见的，执行点击...")
                                try:
                                    await candidate.click(timeout=timeout_ms)
                                    found_visible = True
                                    method_desc = f"iterative-click(index={i})"
                                    break
                                except Exception as e:
                                    logger.warning(f"点击第 {i+1} 个元素失败: {e}")
                        
                        if not found_visible:
                            logger.warning("未找到可见的下拉框选项元素，尝试点击第一个...")
                            try:
                                await candidates.first.click(force=True, timeout=timeout_ms)
                                method_desc = "fallback-force-click"
                            except Exception as e:
                                logger.error(f"强制点击失败: {e}")
                                raise e

                        # 检查并关闭多选下拉框
                        try:
                            await asyncio.sleep(0.5)
                            dropdown = self.page.locator('.el-select-dropdown').first
                            if await dropdown.is_visible():
                                logger.info(f"多选下拉框未自动关闭，点击空白处关闭...")
                                await self.page.click('body', position={'x': 10, 'y': 10}, timeout=3000)
                                auto_close_msg = " + 自动关闭"
                            else:
                                auto_close_msg = ""
                        except:
                            auto_close_msg = ""
                        
                        execution_time = round(time.time() - start_time, 2)
                        log = f"✓ 点击下拉框选项 '{element_name}' 成功（{method_desc}）\n"
                        log += f"  - 定位器: {locator_strategy}={base_locator_value}\n"
                        log += f"  - 匹配数量: {count}\n"
                        log += f"  - 执行方法: {method_desc}{auto_close_msg}\n"
                        log += f"  - 执行时间: {execution_time}秒"
                        return True, log, None
                        
                        logger.info(f"JS执行结果: {js_result}")
                        
                        # 等待 Vue 响应式更新完成
                        await asyncio.sleep(0.8)
                        
                        # 检查并关闭多选下拉框
                        try:
                            dropdown = self.page.locator('.el-select-dropdown').first
                            if await dropdown.is_visible():
                                logger.info(f"多选下拉框未自动关闭，点击空白处关闭...")
                                await self.page.click('body', position={'x': 10, 'y': 10}, timeout=3000)
                                await asyncio.sleep(0.5)
                                auto_close_msg = " + 自动关闭"
                            else:
                                auto_close_msg = ""
                        except:
                            auto_close_msg = ""
                        
                        execution_time = round(time.time() - start_time, 2)
                        method_desc = js_result.get('method', 'unknown') if isinstance(js_result, dict) else 'unknown'
                        log = f"✓ 点击下拉框选项 '{element_name}' 成功（{method_desc})\n"
                        log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                        log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                        log += f"  - 更新方法: {method_desc}{auto_close_msg}\n"
                        if isinstance(js_result, dict) and 'allValues' in js_result:
                            log += f"  - 当前选中值: {js_result['allValues']}\n"
                        log += f"  - 执行时间: {execution_time}秒"
                        return True, log, None
                    except Exception as e:
                        logger.error(f"下拉框选项点击失败: {e}")
                        execution_time = round(time.time() - start_time, 2)

                        # 构建详细的错误日志
                        error_log = f"✗ 点击下拉框选项 '{element_name}' 失败\n"
                        error_log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                        error_log += f"  - 执行时间: {execution_time}秒\n"
                        error_log += f"  - 错误: {str(e)}\n\n"
                        error_log += "建议解决方案:\n"
                        error_log += "1. 检查元素是否在下拉框展开后才出现在 DOM 中\n"
                        error_log += "2. 尝试在点击下拉框后增加等待时间（添加 wait 步骤，等待2000ms）\n"
                        error_log += "3. 使用 Playwright get_by_text 定位：文本=无忧行-鸿蒙APP\n"
                        error_log += "4. 检查 Vue DevTools 确认组件结构是否与代码匹配"

                        # 尝试捕获截图
                        screenshot_base64 = None
                        try:
                            screenshot = await self.page.screenshot()
                            screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        except:
                            pass

                        # 返回: (是否成功, 日志信息, 截图base64)
                        return False, error_log, screenshot_base64
                else:
                    # 普通元素：正常点击
                    # 如果启用了强制操作，先等待元素在 DOM 中，不要求可见
                    if force_action:
                        try:
                            await locator.wait_for(state='attached', timeout=timeout_ms)
                        except:
                            pass  # 如果已经在 DOM 中，继续
                    
                    await locator.click(timeout=timeout_ms, force=force_action)
                    execution_time = round(time.time() - start_time, 2)
                    log = f"✓ 点击元素 '{element_name}' 成功\n"
                    log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                    log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                    if force_action:
                        log += f"  - 强制操作: 是（跳过可见性检查，等待attached）\n"
                    log += f"  - 执行时间: {execution_time}秒"
                    return True, log, None

            elif action_type == 'fill':
                await locator.fill(resolved_input_value, timeout=timeout_ms, force=force_action)
                execution_time = round(time.time() - start_time, 2)

                # 输入成功后短暂等待，确保表单验证生效
                # 特别是在服务器环境下，需要给Vue/React等框架时间处理
                await asyncio.sleep(0.3)

                log = f"✓ 在元素 '{element_name}' 中输入文本成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                if resolved_input_value != step.input_value:
                    log += f"  - 变量解析: '{step.input_value}' => '{resolved_input_value}'\n"
                log += f"  - 输入内容: '{resolved_input_value}'\n"
                log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                if force_action:
                    log += f"  - 强制操作: 是（忽略可见性检查）\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'press':
                await locator.press(resolved_input_value, timeout=timeout_ms)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 在元素 '{element_name}' 上按下按键 '{resolved_input_value}' 成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms / 1000}秒\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'select':
                await locator.select_option(value=resolved_input_value, timeout=timeout_ms)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 在下拉框 '{element_name}' 中选择选项 '{resolved_input_value}' 成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms / 1000}秒\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type in {'check', 'uncheck'}:
                await getattr(locator, action_type)(timeout=timeout_ms, force=force_action)
                verb = '勾选' if action_type == 'check' else '取消勾选'
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ {verb}复选框 '{element_name}' 成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms / 1000}秒\n"
                if force_action:
                    log += "  - 强制操作: 是\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'getText':
                text = await locator.inner_text(timeout=timeout_ms)
                if resolved_assert_value.strip():
                    self.variables.set_temporary(resolved_assert_value, text)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 获取元素 '{element_name}' 的文本成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 文本内容: '{text}'\n"
                if resolved_assert_value.strip():
                    log += f"  - 已保存到临时变量: {resolved_assert_value}\n"
                log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'waitFor':
                await locator.wait_for(state='visible', timeout=timeout_ms)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 等待元素 '{element_name}' 出现成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                log += f"  - 等待时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'hover':
                await locator.hover(timeout=timeout_ms, force=force_action)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 在元素 '{element_name}' 上悬停成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                if force_action:
                    log += f"  - 强制操作: 是（忽略可见性检查）\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'scroll':
                await locator.scroll_into_view_if_needed(timeout=timeout_ms)
                execution_time = round(time.time() - start_time, 2)
                log = f"✓ 滚动到元素 '{element_name}' 成功\n"
                log += f"  - 定位器: {locator_strategy}={locator_value}\n"
                log += f"  - 超时设置: {timeout_ms/1000}秒\n"
                log += f"  - 执行时间: {execution_time}秒"
                return True, log, None

            elif action_type == 'assert':
                # 根据断言类型执行不同的断言
                if step.assert_type == 'textContains':
                    text = await locator.inner_text(timeout=timeout_ms)
                    if resolved_assert_value in text:
                        log = f"✓ 断言通过: 文本包含 '{resolved_assert_value}'\n"
                        if resolved_assert_value != step.assert_value:
                             log += f"  - 变量解析: '{step.assert_value}' => '{resolved_assert_value}'\n"
                        log += f"  - 实际文本: '{text}'\n"
                        log += f"  - 超时设置: {timeout_ms/1000}秒"
                        return True, log, None
                    else:
                        log = f"✗ 断言失败: 文本不包含 '{resolved_assert_value}'\n"
                        if resolved_assert_value != step.assert_value:
                             log += f"  - 变量解析: '{step.assert_value}' => '{resolved_assert_value}'\n"
                        log += f"  - 实际文本: '{text}'"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64

                elif step.assert_type == 'textEquals':
                    text = await locator.inner_text(timeout=timeout_ms)
                    if text == resolved_assert_value:
                        log = f"✓ 断言通过: 文本等于 '{resolved_assert_value}'\n"
                        if resolved_assert_value != step.assert_value:
                             log += f"  - 变量解析: '{step.assert_value}' => '{resolved_assert_value}'\n"
                        log += f"  - 超时设置: {timeout_ms/1000}秒"
                        return True, log, None
                    else:
                        log = f"✗ 断言失败: 文本不等于 '{resolved_assert_value}'\n"
                        if resolved_assert_value != step.assert_value:
                             log += f"  - 变量解析: '{step.assert_value}' => '{resolved_assert_value}'\n"
                        log += f"  - 期望: '{resolved_assert_value}'\n"
                        log += f"  - 实际: '{text}'"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64

                elif step.assert_type == 'isNotVisible':
                    # 等待隐藏或从 DOM 移除，允许异步关闭弹窗等场景完成后再判断。
                    try:
                        await locator.wait_for(state='hidden', timeout=timeout_ms)
                    except PlaywrightTimeout:
                        log = f"✗ 断言失败: 元素 '{element_name}' 仍然可见"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64
                    return True, f"✓ 断言通过: 元素 '{element_name}' 不可见", None

                elif step.assert_type == 'isVisible':
                    is_visible = await locator.is_visible()
                    if (getattr(step, 'action_params', None) or {}).get('negate'):
                        if not is_visible:
                            return True, f"✓ 断言通过: 元素 '{element_name}' 不可见", None
                        log = f"✗ 断言失败: 元素 '{element_name}' 仍然可见"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64
                    if is_visible:
                        log = f"✓ 断言通过: 元素 '{element_name}' 可见"
                        return True, log, None
                    else:
                        log = f"✗ 断言失败: 元素 '{element_name}' 不可见"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64

                elif step.assert_type == 'exists':
                    count = await locator.count()
                    if count > 0:
                        log = f"✓ 断言通过: 元素 '{element_name}' 存在"
                        return True, log, None
                    else:
                        log = f"✗ 断言失败: 元素 '{element_name}' 不存在"
                        screenshot = await self.page.screenshot()
                        screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                        return False, log, screenshot_base64

                elif step.assert_type == 'hasAttribute':
                    attr_spec = resolved_assert_value.strip()
                    if not attr_spec:
                        return False, "✗ hasAttribute 断言需要指定属性名", None
                    attr_name, separator, expected_value = attr_spec.partition('=')
                    attr_name = attr_name.strip()
                    if not attr_name:
                        return False, "✗ hasAttribute 断言需要指定属性名", None

                    actual_value = await locator.get_attribute(attr_name, timeout=timeout_ms)
                    passed = actual_value is not None and (
                        not separator or actual_value == expected_value
                    )
                    if passed:
                        expectation = f"'{attr_name}={expected_value}'" if separator else f"'{attr_name}'"
                        return True, f"✓ 断言通过: 元素 '{element_name}' 包含属性 {expectation}", None

                    if separator:
                        log = f"✗ 断言失败: 属性 '{attr_name}' 值不匹配\n"
                        log += f"  - 期望: '{expected_value}'\n  - 实际: '{actual_value}'"
                    else:
                        log = f"✗ 断言失败: 元素 '{element_name}' 不包含属性 '{attr_name}'"
                    screenshot = await self.page.screenshot()
                    screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
                    return False, log, screenshot_base64



            else:
                log = f"⚠ 未知的操作类型: {action_type}"
                return True, log, None

        except PlaywrightTimeout as e:
            execution_time = round(time.time() - start_time, 2)
            log = f"✗ 操作超时\n"
            log += f"  - 元素: '{element_name}'\n"
            log += f"  - 定位器: {locator_strategy}={locator_value}\n"
            log += f"  - 超时时间: {execution_time}秒\n"
            log += f"  - 错误: {str(e)}"

            # 捕获失败截图
            try:
                screenshot = await self.page.screenshot()
                screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
            except:
                pass

            return False, log, screenshot_base64

        except Exception as e:
            execution_time = round(time.time() - start_time, 2)
            log = f"✗ 执行失败\n"
            log += f"  - 元素: '{element_name}'\n"
            log += f"  - 定位器: {locator_strategy}={locator_value}\n"
            log += f"  - 执行时间: {execution_time}秒\n"
            log += f"  - 错误: {str(e)}"

            # 捕获失败截图
            try:
                screenshot = await self.page.screenshot()
                screenshot_base64 = f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
            except:
                pass

            return False, log, screenshot_base64

    async def navigate(self, url: str) -> Tuple[bool, str]:
        """
        导航到指定URL

        Args:
            url: 目标URL

        Returns:
            (是否成功, 日志信息)
        """
        try:
            # 检测是否在Linux服务器环境
            import platform
            is_linux = platform.system() == 'Linux'

            # 部分店铺页面持续发送分析/推送请求，networkidle 可能长期无法达到。
            await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
            try:
                await self.page.wait_for_load_state('networkidle', timeout=5000)
                load_state = 'networkidle'
            except PlaywrightTimeout:
                load_state = 'domcontentloaded（networkidle 未达成）'

            # 额外等待，确保动态内容加载（Vue/React等SPA应用）
            # 服务器无头模式需要更长的等待时间
            extra_wait = 3 if is_linux else 2
            await asyncio.sleep(extra_wait)

            log = f"✓ 成功导航到: {url}\n"
            log += f"  - 等待页面加载完成（{load_state} + 额外{extra_wait}秒）"
            return True, log
        except Exception as e:
            log = f"✗ 导航失败: {url}\n  - 错误: {str(e)}"
            return False, log

    async def capture_screenshot(self) -> str:
        """
        捕获当前页面截图

        Returns:
            截图的base64字符串
        """
        try:
            screenshot = await self.page.screenshot(full_page=True)
            return f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
        except Exception as e:
            logger.error(f"捕获截图失败: {str(e)}")
            return None
