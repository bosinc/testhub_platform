"""Isolated subprocess runner for imported Python Playwright scripts."""

import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from django.db import close_old_connections
from django.utils import timezone

from .models import TestExecution
from .script_security import ScriptSecurityError, analyze_script

_running: dict[int, subprocess.Popen] = {}
_aborted: set[int] = set()
_lock = threading.Lock()
MAX_LOG_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 300
BROWSER_PROBE = '''
from pathlib import Path as _testhub_path
from playwright.sync_api import BrowserType as _TestHubBrowserType
from playwright.sync_api import Browser as _TestHubBrowser
from playwright.sync_api import BrowserContext as _TestHubBrowserContext
from playwright.sync_api import Page as _TestHubPage
from playwright.sync_api import Locator as _TestHubLocator
from playwright.sync_api import Error as _TestHubPlaywrightError

_testhub_original_launch = _TestHubBrowserType.launch
_testhub_original_click = _TestHubLocator.click
_testhub_original_browser_new_page = _TestHubBrowser.new_page
_testhub_original_browser_new_context = _TestHubBrowser.new_context
_testhub_original_context_new_page = _TestHubBrowserContext.new_page
_testhub_original_goto = _TestHubPage.goto
_testhub_original_reload = _TestHubPage.reload

def _testhub_launch(self, *args, **kwargs):
    # 仅远程桌面会话强制使用有头，普通回放保留原脚本适配行为。
    if os.environ.get('REMOTE_DESKTOP') == '1':
        kwargs['headless'] = False
        kwargs['env'] = {**os.environ, **(kwargs.get('env') or {}),
                         'DISPLAY': os.environ['DISPLAY'], 'XAUTHORITY': os.environ['XAUTHORITY']}
    browser = _testhub_original_launch(self, *args, **kwargs)
    _testhub_path(os.environ['BROWSER_MARKER']).touch()
    return browser

def _testhub_click(locator, *args, **kwargs):
    try:
        return _testhub_original_click(locator, *args, **kwargs)
    except _TestHubPlaywrightError as error:
        message = str(error).lower()
        if 'strict mode violation' not in message and 'resolved to' not in message:
            raise
        count = locator.count()
        visible = []
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible(timeout=1000):
                    visible.append(candidate)
            except _TestHubPlaywrightError:
                continue
        if len(visible) != 1:
            print(f'[TestHub] 自动定位器修复未应用：共匹配{count}个元素，可见{len(visible)}个；无法安全确定目标。', flush=True)
            raise
        return _testhub_original_click(visible[0], *args, **kwargs)

def _testhub_toggle_click(locator, *args, **kwargs):
    """Open menu/accordion controls only when they are not already expanded."""
    try:
        expanded = locator.get_attribute('aria-expanded', timeout=1000)
        checked = locator.get_attribute('aria-checked', timeout=1000)
        if expanded == 'true' or checked == 'true':
            return None
    except _TestHubPlaywrightError as error:
        # A missing ARIA state is not enough to guess the desired action.
        print(f'[TestHub] 自动 toggle 处理：未读取到 aria-expanded/aria-checked，保留原点击：{error}', flush=True)
    return _testhub_click(locator, *args, **kwargs)

def _testhub_configure_page(page):
    page.set_default_navigation_timeout(30000)
    return page

def _testhub_browser_new_page(browser, *args, **kwargs):
    storage_state = os.environ.get('STORAGE_STATE_PATH')
    if storage_state and 'storage_state' not in kwargs:
        kwargs['storage_state'] = storage_state
    return _testhub_configure_page(_testhub_original_browser_new_page(browser, *args, **kwargs))

def _testhub_browser_new_context(browser, *args, **kwargs):
    storage_state = os.environ.get('STORAGE_STATE_PATH')
    if storage_state and 'storage_state' not in kwargs:
        kwargs['storage_state'] = storage_state
    return _testhub_original_browser_new_context(browser, *args, **kwargs)

def _testhub_context_new_page(context, *args, **kwargs):
    return _testhub_configure_page(_testhub_original_context_new_page(context, *args, **kwargs))

def _testhub_goto(page, *args, **kwargs):
    response = _testhub_original_goto(page, *args, **kwargs)
    try:
        page.wait_for_load_state('domcontentloaded', timeout=10000)
    except _TestHubPlaywrightError as error:
        print(f'[TestHub] 自动等待失败：导航后的 DOMContentLoaded 未在10秒内完成：{error}', flush=True)
    return response

def _testhub_reload(page, *args, **kwargs):
    response = _testhub_original_reload(page, *args, **kwargs)
    try:
        page.wait_for_load_state('domcontentloaded', timeout=10000)
    except _TestHubPlaywrightError as error:
        print(f'[TestHub] 自动等待失败：reload 后 DOMContentLoaded 未在10秒内完成：{error}', flush=True)
    return response

_TestHubLocator.click = _testhub_click
_TestHubBrowser.new_page = _testhub_browser_new_page
_TestHubBrowser.new_context = _testhub_browser_new_context
_TestHubBrowserContext.new_page = _testhub_context_new_page
_TestHubPage.goto = _testhub_goto
_TestHubPage.reload = _testhub_reload
_TestHubBrowserType.launch = _testhub_launch
'''


def _failure_guidance(log_text: str) -> str:
    text = (log_text or '').lower()
    # Browser startup failures happen before a locator or page action exists;
    # keep them actionable instead of returning a blank diagnostic section.
    if any(marker in text for marker in (
        'targetclosederror',
        'browsertype.launch',
        'mach_portrendezvous',
        'permission denied',
        'sigtrap',
    )):
        return '浏览器启动失败：检查 Playwright 浏览器缓存、系统进程权限和运行环境；在 macOS 受限环境中请改用 CI 或允许启动浏览器子进程的执行环境。'
    if 'strict mode violation' in text or re.search(r'resolved to \d+ elements', text):
        return '定位器可能匹配多个元素：改用带名称/属性的语义定位，必要时明确使用 .first 或 .nth(index)。'
    if 'waiting for locator' in text or 'timeout' in text and ('locator' in text or 'selector' in text):
        return '目标元素未出现或前置步骤未成功：核对上一条导航/操作，并在关键跳转后等待页面状态或目标元素。'
    if 'element is not visible' in text or 'not visible' in text:
        return '目标元素当前不可见：检查菜单/手风琴等 toggle 的初始状态，读取 aria-expanded 或可见性后再决定是否点击。'
    if any(marker in text for marker in ('filenotfounderror', 'no such file', 'enoent', 'cannot find the file')):
        return '执行依赖的文件或登录态路径不存在：移除本机绝对路径，改用项目相对路径并确认目标环境已准备文件。'
    return ''


def get_playwright_browsers_path() -> str:
    configured_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if configured_path:
        return configured_path
    if sys.platform == 'darwin':
        return str(Path.home() / 'Library' / 'Caches' / 'ms-playwright')
    if os.name == 'nt':
        local_app_data = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return str(Path(local_app_data) / 'ms-playwright')
    cache_home = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return str(Path(cache_home) / 'ms-playwright')


def start_script_execution(execution_id: int, script_content: str | None = None, remote_session=None):
    def worker():
        if remote_session is None:
            execute_script(execution_id, script_content)
            return
        from .remote.sessions import Desktop
        close_old_connections()
        try:
            with Desktop(remote_session) as desktop:
                execute_script(execution_id, script_content, desktop=desktop)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('远程回放桌面执行失败')
        finally:
            close_old_connections()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


def execute_script(execution_id: int, script_content: str | None = None, desktop=None):
    close_old_connections()
    execution = TestExecution.objects.select_related('test_script').get(pk=execution_id)
    started = time.monotonic()
    analysis = None
    watchdog_done = threading.Event()
    try:
        source = script_content if script_content is not None else execution.test_script.content
        analysis = analyze_script(source)
        script = analysis['normalized_content']
        with _lock:
            if execution_id in _aborted:
                execution.status = 'ABORTED'
                execution.error_message = '用户手动终止执行'
                execution.finished_at = timezone.now()
                return
        execution.status = 'RUNNING'
        execution.started_at = timezone.now()
        mode = '无头' if execution.headless else '有头'
        browser_name = (execution.browser or 'chromium').capitalize()
        execution.execution_logs = (
            '========== TestHub 脚本回放开始 ==========\n'
            '安全扫描通过\n'
            f'执行环境：执行引擎=Playwright，浏览器={browser_name}，模式={mode}（headless={execution.headless}）\n'
            f"脚本来源={execution.run_config.get('script_source', 'saved_snapshot')}，"
            f"SHA-256={execution.run_config.get('content_hash') or analysis['content_hash']}\n"
        )
        execution.save(update_fields=['status', 'started_at', 'execution_logs'])

        with tempfile.TemporaryDirectory(prefix=f'testhub_script_{execution.id}_') as workdir:
            script_path = Path(workdir) / 'script.py'
            browser_marker = Path(workdir) / 'browser_started.marker'
            # 将数据库中的登录态复制到本次脚本工作目录，避免脚本直接访问持久化目录。
            storage_state_path = None
            storage_state_id = execution.run_config.get('storage_state_id')
            if storage_state_id:
                from .models import StorageState
                storage_state = StorageState.objects.filter(
                    pk=storage_state_id, project_id=execution.project_id
                ).first()
                if not storage_state or not storage_state.file:
                    raise ValueError('选择的登录态不存在或不属于当前项目')
                storage_state_path = Path(workdir) / 'storage_state.json'
                shutil.copyfile(storage_state.file.path, storage_state_path)
            script_path.write_text(BROWSER_PROBE + '\n' + script, encoding='utf-8')
            env = {
                'PATH': os.environ.get('PATH', ''),
                'HOME': str(Path(workdir) / 'home'),
                'PYTHONUNBUFFERED': '1',
                'HEADLESS': '1' if execution.headless else '0',
                'BASE_URL': (
                    execution.run_config.get('base_url')
                    or execution.run_config.get('start_url')
                    or (execution.test_script.start_url if execution.test_script else '')
                ),
                'PLAYWRIGHT_BROWSERS_PATH': get_playwright_browsers_path(),
                'BROWSER_MARKER': str(browser_marker),
                'WORKDIR': workdir,
            }
            if desktop:
                desktop.check()
                # 保留脚本的临时 HOME，仅传入当前会话的显示凭据。
                env.update({key: desktop.env[key] for key in ('DISPLAY', 'XAUTHORITY')})
                env['REMOTE_DESKTOP'] = '1'
                env['REMOTE_SESSION'] = str(desktop.session.pk)
            # 通过环境变量把临时文件位置传给脚本运行时适配器。
            if storage_state_path:
                env['STORAGE_STATE_PATH'] = str(storage_state_path)
            Path(env['HOME']).mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=workdir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=(os.name != 'nt'),
            )
            with _lock:
                _running[execution.id] = process
            _append_log(execution.id, '回放子进程已启动\n')
            def watch_remote_stop():
                close_old_connections()
                try:
                    while not watchdog_done.wait(0.5):
                        if desktop and desktop.cancelled.is_set():
                            _terminate_process(process)
                            return
                finally:
                    close_old_connections()
            if desktop:
                threading.Thread(target=watch_remote_stop, daemon=True).start()
            output_bytes = 0
            log_truncated = False
            assert process.stdout is not None
            for line in process.stdout:
                line_bytes = len(line.encode('utf-8'))
                if output_bytes < MAX_LOG_BYTES:
                    _append_log(execution.id, line)
                    output_bytes += line_bytes
                elif not log_truncated:
                    _append_log(execution.id, '\n日志超过1MB，后续输出已截断。\n')
                    log_truncated = True
            try:
                process.wait(timeout=max(1, TIMEOUT_SECONDS - int(time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                _terminate_process(process)
                raise TimeoutError('脚本执行超过5分钟，已自动终止')
            watchdog_done.set()
            return_code = process.returncode
            browser_started = browser_marker.exists()

        with _lock:
            was_aborted = execution_id in _aborted
        if return_code == 0 and not browser_started and not was_aborted:
            execution.status = 'FAILED'
            execution.error_message = '脚本未实际启动Playwright浏览器，不能判定为成功'
            _append_log(execution.id, '执行失败：脚本未实际启动Playwright浏览器\n')
        else:
            execution.status = 'ABORTED' if was_aborted else ('SUCCESS' if return_code == 0 else 'FAILED')
        execution.passed_cases = 1 if execution.status == 'SUCCESS' else 0
        execution.failed_cases = 1 if execution.status == 'FAILED' else 0
        execution.total_cases = 1
        _append_log(execution.id, f'子进程退出码：{return_code}\n')
        if was_aborted:
            execution.error_message = '用户手动终止执行'
        elif return_code != 0:
            execution.error_message = f'脚本进程退出码：{return_code}'
            execution.refresh_from_db(fields=['execution_logs'])
            guidance = _failure_guidance(execution.execution_logs)
            if guidance:
                _append_log(execution.id, f'排查建议：{guidance}\n')
    except (ScriptSecurityError, TimeoutError, Exception) as exc:
        execution.status = 'FAILED' if not isinstance(exc, TimeoutError) else 'ABORTED'
        execution.total_cases = 1
        execution.failed_cases = 1
        execution.error_message = str(exc)
        _append_log(execution.id, f'执行失败：{exc}\n')
        if analysis and analysis.get('diagnostics'):
            _append_log(execution.id, '失败原因分类：回放前自动检查项未能通过运行时验证，详见上述检查与处理记录。\n')
        guidance = _failure_guidance(str(exc))
        if guidance:
            _append_log(execution.id, f'排查建议：{guidance}\n')
    finally:
        watchdog_done.set()
        with _lock:
            _running.pop(execution.id, None)
            _aborted.discard(execution.id)
        execution.duration = round(time.monotonic() - started, 2)
        execution.finished_at = timezone.now()
        execution.refresh_from_db(fields=['execution_logs'])
        result = {'SUCCESS': 'passed', 'FAILED': 'failed', 'ABORTED': 'aborted'}.get(execution.status, 'failed')
        execution.execution_logs = (execution.execution_logs or '') + (
            f'执行结果：{result}\n执行耗时：{execution.duration:.2f}秒\n'
            f'========== TestHub 脚本回放结束 ==========\n'
        )
        execution.save()
        close_old_connections()


def _append_log(execution_id: int, line: str):
    execution = TestExecution.objects.get(pk=execution_id)
    current = execution.execution_logs or ''
    if len(current.encode('utf-8')) < MAX_LOG_BYTES:
        execution.execution_logs = (current + line)[-MAX_LOG_BYTES:]
        execution.save(update_fields=['execution_logs'])


def stop_script_execution(execution_id: int) -> bool:
    with _lock:
        _aborted.add(execution_id)
        process = _running.get(execution_id)
    if not process:
        return False
    _terminate_process(process)
    return True


def _terminate_process(process: subprocess.Popen):
    if process.poll() is not None:
        return
    if os.name != 'nt':
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
