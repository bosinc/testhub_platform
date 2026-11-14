"""有头执行的私有桌面资源；执行逻辑仍运行在应用线程中。"""
import logging
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..models import UiRemoteSession

logger = logging.getLogger(__name__)
ACTIVE = ('starting', 'ready', 'running')


def enabled(headless):
    """仅由配置开关和执行模式控制，不推断操作系统。"""
    return settings.UI_REMOTE_ENABLED and not headless


def create_session(execution, user):
    """通过数据库唯一槽位协调同一节点的多个应用 worker。"""
    if not enabled(execution.headless):
        return None
    if not 1 <= settings.UI_REMOTE_MAX_SESSIONS <= 100 or settings.UI_REMOTE_TIMEOUT < 1:
        raise ValidationError({'error': '远程调试并发数必须为 1–100，超时必须大于 0'})
    if not (0 < settings.UI_REMOTE_PORT_BASE < 65536 - settings.UI_REMOTE_MAX_SESSIONS):
        raise ValidationError({'error': '远程调试端口范围无效'})
    if not settings.UI_REMOTE_NODE:
        raise ValidationError({'error': '远程调试节点标识不能为空'})
    for command in ('Xvfb', 'xauth', 'xdpyinfo', 'x11vnc', 'fluxbox'):
        if not shutil.which(command):
            raise ValidationError({'error': f'远程调试组件未安装：{command}'})
    reap_expired_sessions()
    from ..models import TestCaseExecution
    relation = {'case_execution': execution} if isinstance(execution, TestCaseExecution) else {'execution': execution}
    if execution.browser == 'safari' and execution.engine == 'selenium':
        raise ValidationError({'error': '远程桌面不支持 Selenium Safari'})
    for index in range(settings.UI_REMOTE_MAX_SESSIONS):
        try:
            with transaction.atomic():
                return UiRemoteSession.objects.create(
                    owner=user, node=settings.UI_REMOTE_NODE,
                    slot=f'{settings.UI_REMOTE_NODE}:{index}',
                    display=f':{settings.UI_REMOTE_DISPLAY_BASE + index}',
                    port=settings.UI_REMOTE_PORT_BASE + index,
                    expires_at=timezone.now() + timedelta(seconds=settings.UI_REMOTE_TIMEOUT),
                    **relation,
                )
        except IntegrityError:
            continue
    raise ValidationError({'error': '远程调试并发数已达上限，请结束其他调试后重试'})


def payload(session):
    """响应只提供不透明会话 ID，不泄露内部端口或显示凭据。"""
    return {'remote_session_id': str(session.id)} if session else {}


def _terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass


def _stop_session_children(session_id):
    """通过每会话环境标记定向关闭浏览器/驱动，覆盖应用异常退出后的孤儿进程。"""
    marker = f'REMOTE_SESSION={session_id}'.encode()
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if marker not in (entry / 'environ').read_bytes().split(b'\0'):
                continue
            pid = int(entry.name)
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                if not entry.exists():
                    break
                time.sleep(0.05)
            else:
                if marker in (entry / 'environ').read_bytes().split(b'\0'):
                    os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            continue


def reap_expired_sessions():
    """回收过期/失联会话；进程命令必须包含本次工作目录才允许终止。"""
    if not settings.UI_REMOTE_ENABLED:
        return
    from django.db.models import Q
    now = timezone.now()
    stale = UiRemoteSession.objects.filter(node=settings.UI_REMOTE_NODE, status__in=(*ACTIVE, 'closing')).filter(
        Q(expires_at__lt=now) | Q(heartbeat_at__lt=now - timedelta(seconds=60)))
    for session in stale:
        # 先原子认领清理权；期间仍占用槽位，避免新会话复用尚未释放的端口。
        with transaction.atomic():
            current = UiRemoteSession.objects.select_for_update().get(pk=session.pk)
            if current.status not in (*ACTIVE, 'closing'):
                continue
            if current.status == 'closing' and current.heartbeat_at > now - timedelta(seconds=60):
                continue
            if current.expires_at > now and current.heartbeat_at > now - timedelta(seconds=60):
                continue
            current.status = 'closing'
            current.heartbeat_at = now
            current.save(update_fields=['status', 'heartbeat_at'])
        _stop_session_children(current.pk)
        resources = current.resources
        workdir = resources.get('workdir')
        for pid in reversed(resources.get('pids', [])):
            try:
                # /proc 校验防止 PID 被复用后误杀其他任务；不使用全局 pkill。
                command = Path(f'/proc/{int(pid)}/cmdline').read_bytes()
                if workdir and workdir.encode() in command and os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
                    for _ in range(20):
                        if not Path(f'/proc/{pid}').exists():
                            break
                        time.sleep(0.05)
                    else:
                        # 再次校验身份，避免等待期间 PID 被其他任务复用。
                        if workdir.encode() in Path(f'/proc/{pid}/cmdline').read_bytes():
                            os.killpg(pid, signal.SIGKILL)
            except (OSError, ValueError):
                continue
        if workdir and Path(workdir).parent == Path(tempfile.gettempdir()) and Path(workdir).name.startswith('testhub_desktop_'):
            shutil.rmtree(workdir, ignore_errors=True)
        UiRemoteSession.objects.filter(pk=session.pk, status='closing').update(
            status='failed', error='调试超时或执行进程失联', slot=None, resources={})
        _fail_execution(session, '调试超时或执行进程失联')


def _fail_execution(session, message):
    """显示启动异常也要结束执行记录，不能留下永久运行中的任务。"""
    if session.case_execution_id:
        from ..models import TestCaseExecution
        TestCaseExecution.objects.filter(pk=session.case_execution_id, status__in=['pending', 'running']).update(
            status='failed', error_message=message, finished_at=timezone.now())
    elif session.execution_id:
        from ..models import TestExecution
        execution = TestExecution.objects.filter(pk=session.execution_id).first()
        if execution:
            TestExecution.objects.filter(pk=execution.pk, status__in=['PENDING', 'RUNNING']).update(
                status='FAILED', error_message=message, finished_at=timezone.now())
            if execution.test_suite_id:
                from ..models import TestCaseExecution, TestSuite
                TestCaseExecution.objects.filter(suite_execution=execution, status__in=['pending', 'running']).update(
                    status='failed', error_message=message, finished_at=timezone.now())
                TestSuite.objects.filter(pk=execution.test_suite_id).update(execution_status='failed')


class Desktop:
    """拥有显示进程的上下文；环境副本仅传给当前浏览器，不修改 os.environ。"""

    def __init__(self, session):
        self.session = session
        self.processes = []
        self.closed = threading.Event()
        self.cancelled = threading.Event()
        self.env = None
        self.log = None
        self.workdir = None
        self.process_lock = threading.Lock()

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeError('远程调试已停止或超时')

    def _stop_processes(self):
        with self.process_lock:
            _stop_session_children(self.session.pk)
            for process in reversed(self.processes):
                _terminate(process)

    def _assert_active(self):
        self.session.refresh_from_db(fields=['status', 'expires_at', 'stop_requested'])
        if (self.session.status not in ACTIVE or self.session.stop_requested
                or self.session.expires_at <= timezone.now()):
            self.cancelled.set()
        self.check()

    def _spawn(self, args):
        # 启动阶段也可能收到停止或超时，不能复活已被清理器关闭的会话。
        self._assert_active()
        process = subprocess.Popen(args, env=self.env, stdout=self.log, stderr=self.log, start_new_session=True)
        self.processes.append(process)
        UiRemoteSession.objects.filter(pk=self.session.pk).update(resources={
            'workdir': self.workdir, 'pids': [item.pid for item in self.processes]})
        return process

    def _monitor(self):
        close_old_connections()
        try:
            while not self.closed.wait(1):
                current = UiRemoteSession.objects.get(pk=self.session.pk)
                if current.stop_requested or current.expires_at <= timezone.now() or current.status not in ACTIVE:
                    self.cancelled.set()
                    # 关闭显示会打断正在等待的浏览器操作，实际浏览器由执行线程 finally 关闭。
                    self._stop_processes()
                    return
                UiRemoteSession.objects.filter(pk=current.pk).update(heartbeat_at=timezone.now())
                close_old_connections()
        except Exception:
            logger.exception('远程桌面心跳失败 session=%s', self.session.pk)
            self.cancelled.set()
            self._stop_processes()
        finally:
            close_old_connections()

    def __enter__(self):
        try:
            self._assert_active()
            self.workdir = tempfile.mkdtemp(prefix='testhub_desktop_')
            UiRemoteSession.objects.filter(pk=self.session.pk).update(resources={'workdir': self.workdir, 'pids': []})
            authority = str(Path(self.workdir) / 'Xauthority')
            Path(authority).touch(mode=0o600)
            subprocess.run(['xauth', '-f', authority, 'add', self.session.display,
                            'MIT-MAGIC-COOKIE-1', secrets.token_hex(16)], check=True, capture_output=True, timeout=5)
            # 每会话 HOME、临时文件和 Xauthority 独立，保留共享浏览器安装位置。
            self.env = {**os.environ, 'DISPLAY': self.session.display, 'XAUTHORITY': authority,
                        'HOME': self.workdir, 'TMPDIR': self.workdir,
                        'REMOTE_SESSION': str(self.session.pk)}
            from ..script_runner import get_playwright_browsers_path
            self.env['PLAYWRIGHT_BROWSERS_PATH'] = get_playwright_browsers_path()
            self.log = open(Path(self.workdir) / 'desktop.log', 'wb')
            self._spawn(['Xvfb', self.session.display, '-screen', '0', '1920x1080x24',
                         '-nolisten', 'tcp', '-auth', authority])
            # xdpyinfo 同时验证当前 Xauthority，避免误连接已有显示器。
            deadline = time.monotonic() + 10
            while True:
                if self.processes[0].poll() is not None:
                    raise RuntimeError('虚拟屏幕启动失败，显示号可能已被占用')
                result = subprocess.run(['xdpyinfo', '-display', self.session.display], env=self.env,
                                        capture_output=True, timeout=2)
                if result.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('虚拟屏幕启动超时')
                time.sleep(0.1)
            rc = str(Path(self.workdir) / 'fluxboxrc')
            Path(rc).touch()
            self._spawn(['fluxbox', '-rc', rc])
            try:
                with socket.create_connection(('127.0.0.1', self.session.port), timeout=0.3):
                    raise RuntimeError('VNC 内部端口已被占用，无法启动私有桌面')
            except OSError:
                pass
            self._spawn(['x11vnc', '-display', self.session.display, '-auth', authority,
                         '-rfbport', str(self.session.port), '-listen', '127.0.0.1', '-localhost',
                         '-forever', '-shared', '-nopw', '-noxdamage'])
            deadline = time.monotonic() + 10
            while True:
                if self.processes[-1].poll() is not None:
                    raise RuntimeError('VNC 启动失败，内部端口可能已被占用')
                try:
                    with socket.create_connection(('127.0.0.1', self.session.port), timeout=0.3):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('VNC 启动超时')
                    time.sleep(0.1)
            self._assert_active()
            UiRemoteSession.objects.filter(pk=self.session.pk, status__in=ACTIVE).update(status='ready', heartbeat_at=timezone.now())
            self.monitor = threading.Thread(target=self._monitor, daemon=True, name=f'desktop-{self.session.pk}')
            self.monitor.start()
            # 给前端首次连接留时间，避免短用例在画面显示前就已执行完。
            deadline = time.monotonic() + settings.UI_REMOTE_CONNECT_WAIT
            while time.monotonic() < deadline:
                self.check()
                self.session.refresh_from_db(fields=['connected_at'])
                if self.session.connected_at:
                    break
                time.sleep(0.2)
            self.check()
            self._assert_active()
            UiRemoteSession.objects.filter(pk=self.session.pk, status__in=ACTIVE).update(status='running')
            return self
        except Exception as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.closed.set()
        if hasattr(self, 'monitor'):
            self.monitor.join(timeout=12)
        self._stop_processes()
        if exc and self.workdir:
            logger.error('桌面启动/执行失败 session=%s: %s', self.session.pk, exc)
        if self.log:
            self.log.close()
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
        error = str(exc) if exc else ('远程调试已停止或超时' if self.cancelled.is_set() else '')
        UiRemoteSession.objects.filter(pk=self.session.pk, status__in=ACTIVE).update(
            status='failed' if error else 'finished', error=error, slot=None, resources={})
        if error:
            _fail_execution(self.session, error)


def launch_case(session, case, *, engine, browser, user, execution, storage_state_id=None):
    """仅远程有头入口异步化，保留普通入口的同步响应协议。"""
    def worker():
        close_old_connections()
        try:
            from ..test_executor import run_single_case
            with Desktop(session) as desktop:
                run_single_case(case, engine=engine, browser=browser, headless=False,
                                executed_by=user, execution=execution,
                                storage_state_id=storage_state_id, desktop=desktop)
        except Exception:
            logger.exception('远程用例执行失败 session=%s', session.pk)
        finally:
            close_old_connections()
    thread = threading.Thread(target=worker, daemon=True, name=f'ui-case-{session.pk}')
    thread.start()


def execution_payload(session):
    """将异步单用例结果转换为现有结果面板的协议。"""
    if not session.case_execution_id:
        return None
    execution = session.case_execution
    return {
        'success': execution.status == 'passed', 'logs': execution.execution_logs,
        'screenshots': execution.screenshots, 'execution_time': execution.execution_time,
        'errors': [{'message': execution.error_message}] if execution.error_message else [],
    }
